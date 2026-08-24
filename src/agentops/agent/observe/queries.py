"""Bounded Azure Monitor query construction and execution.

This module never imports ``azure.monitor.query`` (it is not a declared
dependency of AgentOps). Query execution is expressed purely in terms of a
duck-typed ``client.query_batch(requests)`` coroutine so unit tests can use
lightweight fakes and production code can inject the real async
``LogsQueryClient`` without this module needing to import the SDK.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Mapping, Sequence

from agentops.core.observe import (
    MAX_ROWS_PER_QUERY,
    GenerativeAIContent,
    ObserveFilterState,
    TelemetrySource,
)

# ---------------------------------------------------------------------------
# Bounds shared across builders and batch execution (T043/T044).
# ---------------------------------------------------------------------------

MAX_SOURCES_PER_BATCH = 10
SOURCE_TIMEOUT_SECONDS = 30
DEFAULT_REQUEST_DEADLINE_SECONDS = 10
DEFAULT_LOOKBACK_HOURS = 24

_TELEMETRY_TABLES = "union AppDependencies, AppRequests"
_APPGENAI_TABLE = "AppGenAIContent"
_PROJECT_RESOURCE_ID = (
    'tostring(coalesce(Properties["gen_ai.project.id"], '
    'Properties["gen_ai.azure_ai_project.id"]))'
)

TOKEN_CLASS_ALIASES: dict[str, tuple[str, ...]] = {
    "cache_read": (
        "gen_ai.usage.cache_read.input_tokens",
        "gen_ai.usage.cache_read_input_tokens",
    ),
    "cache_write": (
        "gen_ai.usage.cache_write.input_tokens",
        "gen_ai.usage.cache_creation.input_tokens",
        "gen_ai.usage.cache_creation_input_tokens",
    ),
    "reasoning": (
        "gen_ai.usage.reasoning.output_tokens",
        "gen_ai.usage.reasoning_tokens",
    ),
}
TOKEN_CLASS_ALIAS_NAMES = frozenset(
    alias for aliases in TOKEN_CLASS_ALIASES.values() for alias in aliases
)

_EVENT_NAME_TO_FIELD = {
    "gen_ai.system.message": "system_instructions",
    "gen_ai.user.message": "input_messages",
    "gen_ai.assistant.message": "output_messages",
    "gen_ai.choice": "output_messages",
    "gen_ai.tool.message": "tool_content",
    "gen_ai.evaluation.result": "evaluation_explanation",
}


class SupersededRequestError(RuntimeError):
    """Raised when an in-flight batch is discarded for a newer request (T057)."""


def _kql_escape(value: str) -> str:
    """Escape a string literal for safe interpolation into a KQL query."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def default_lookback_window(
    *, now: datetime | None = None, hours: int = DEFAULT_LOOKBACK_HOURS
) -> tuple[datetime, datetime]:
    """Return a bounded ``(start, end)`` window defaulting to 24 hours."""
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = end - timedelta(hours=hours)
    return start, end


def _time_window_clause(filters: ObserveFilterState) -> str:
    return (
        f"| where TimeGenerated between "
        f"(datetime({_iso(filters.start)}) .. datetime({_iso(filters.end)}))"
    )


def _dimension_filters(
    filters: ObserveFilterState, scope_source: TelemetrySource | None = None
) -> list[str]:
    """Build early, bounded per-dimension filter clauses (T043)."""
    clauses: list[str] = []
    if filters.foundry_resource_id:
        value = _kql_escape(filters.foundry_resource_id)
        clauses.append(
            '| where tolower(tostring(Properties["gen_ai.foundry.resource.id"])) '
            f"== '{value}' or tolower({_PROJECT_RESOURCE_ID}) startswith "
            f"'{value}/projects/'"
        )
    elif scope_source and scope_source.foundry_resource_id:
        value = _kql_escape(scope_source.foundry_resource_id)
        clauses.append(
            '| where tolower(tostring(Properties["gen_ai.foundry.resource.id"])) '
            f"== '{value}' or tolower({_PROJECT_RESOURCE_ID}) startswith "
            f"'{value}/projects/'"
        )
    if filters.project_resource_id:
        value = _kql_escape(filters.project_resource_id)
        clauses.append(f"| where tolower({_PROJECT_RESOURCE_ID}) == '{value}'")
    elif scope_source and scope_source.project_resource_ids:
        values = ", ".join(
            f"'{_kql_escape(project_id)}'" for project_id in scope_source.project_resource_ids
        )
        clauses.append(f"| where tolower({_PROJECT_RESOURCE_ID}) in ({values})")
    if filters.agent_id:
        value = _kql_escape(filters.agent_id)
        clauses.append(
            '| where tostring(coalesce(Properties["gen_ai.agent.id"], '
            'Properties["gen_ai.agent.name"])) '
            f"== '{value}'"
        )
    if filters.model:
        value = _kql_escape(filters.model)
        clauses.append(
            '| where tostring(coalesce(Properties["gen_ai.request.model"], '
            'Properties["gen_ai.response.model"])) '
            f"== '{value}'"
        )
    return clauses


def _agent_extend_clauses() -> list[str]:
    return [
        f"| extend project_resource_id = {_PROJECT_RESOURCE_ID}",
        '| extend agent_key = tostring(coalesce(Properties["gen_ai.agent.id"], '
        'Properties["gen_ai.agent.name"], "unknown"))',
        # Raw (uncoalesced) id, kept distinct from agent_key so the service
        # layer can tell a managed Foundry agent (gen_ai.agent.id present)
        # apart from an externally-instrumented one (id only, name set).
        '| extend agent_id = tostring(Properties["gen_ai.agent.id"])',
        '| extend agent_name = tostring(Properties["gen_ai.agent.name"])',
        '| extend provider_name = tostring(Properties["gen_ai.provider.name"])',
        '| extend system = tostring(Properties["gen_ai.system"])',
        '| extend model = tostring(coalesce(Properties["gen_ai.request.model"], '
        'Properties["gen_ai.response.model"]))',
        '| extend input_tokens = toint(Properties["gen_ai.usage.input_tokens"])',
        '| extend output_tokens = toint(Properties["gen_ai.usage.output_tokens"])',
    ]


def _bounded_aggregate(aggregate_lines: Sequence[str], *, order_by: str) -> str:
    """Return one aggregate query with a bounded result and in-scope total.

    Counting the already-aggregated rows keeps the total and bounded result in
    one source query, so callers retain the normal per-source batch limits.
    """
    if not aggregate_lines:
        raise ValueError("aggregate_lines must contain a query")
    return "\n".join(
        [
            f"let agg = {aggregate_lines[0]}",
            *aggregate_lines[1:-1],
            f"{aggregate_lines[-1]};",
            "let total_in_scope = toscalar(agg | count);",
            "agg",
            f"| sort by {order_by} desc",
            f"| take {MAX_ROWS_PER_QUERY}",
            "| extend total_in_scope = total_in_scope",
        ]
    )


def _token_class_extend_clauses() -> list[str]:
    clauses: list[str] = []
    for token_class, aliases in TOKEN_CLASS_ALIASES.items():
        arguments = ", ".join(f'Properties["{alias}"]' for alias in aliases)
        clauses.append(f"| extend {token_class}_tokens = toint(coalesce({arguments}))")
    return clauses


def build_overview_query(
    filters: ObserveFilterState, *, scope_source: TelemetrySource | None = None
) -> str:
    """Bounded aggregate invocation/failure/latency query for the overview view."""
    lines = [
        _TELEMETRY_TABLES,
        _time_window_clause(filters),
        *_dimension_filters(filters, scope_source),
        "| where isnotempty(Name)",
        "| summarize invocations = count(), "
        "failures = countif(Success == false), "
        "avg_latency_ms = avg(DurationMs), "
        "p95_latency_ms = percentile(DurationMs, 95)",
    ]
    return "\n".join(lines)


def build_agents_query(
    filters: ObserveFilterState, *, scope_source: TelemetrySource | None = None
) -> str:
    """Bounded per-agent summary query (agent id/name, model, tokens, last seen)."""
    aggregate_lines = [
        _TELEMETRY_TABLES,
        _time_window_clause(filters),
        *_dimension_filters(filters, scope_source),
        *_agent_extend_clauses(),
        "| summarize invocations = count(), "
        "failures = countif(Success == false), "
        "p95_latency_ms = percentile(DurationMs, 95), "
        "input_tokens = sum(input_tokens), "
        "output_tokens = sum(output_tokens), "
        "last_seen = max(TimeGenerated), "
        "agent_id = take_anyif(agent_id, isnotempty(agent_id)), "
        "agent_name = take_anyif(agent_name, isnotempty(agent_name)), "
        "provider_name = take_anyif(provider_name, isnotempty(provider_name)), "
        "system = take_anyif(system, isnotempty(system)), "
        "model = take_anyif(model, isnotempty(model)) "
        "by project_resource_id, agent_key",
    ]
    return _bounded_aggregate(aggregate_lines, order_by="invocations")


def build_models_query(
    filters: ObserveFilterState, *, scope_source: TelemetrySource | None = None
) -> str:
    """Bounded per-model/deployment usage summary query."""
    event_lines = [
        _TELEMETRY_TABLES,
        _time_window_clause(filters),
        *_dimension_filters(filters, scope_source),
        *_agent_extend_clauses(),
        '| extend deployment = tostring(Properties["gen_ai.request.deployment"])',
        *_token_class_extend_clauses(),
    ]
    summary_lines = [
        "| summarize requests = count(), "
        "failures = countif(Success == false), "
        "p95_latency_ms = percentile(DurationMs, 95), "
        "input_tokens = sum(input_tokens), "
        "output_tokens = sum(output_tokens), "
        "token_reporting_records = countif("
        "isnotnull(input_tokens) or isnotnull(output_tokens) or "
        "isnotnull(cache_read_tokens) or isnotnull(cache_write_tokens) or "
        "isnotnull(reasoning_tokens)), "
        "cache_read_tokens = sum(cache_read_tokens), "
        "cache_read_reporting_records = countif(isnotnull(cache_read_tokens)), "
        "cache_write_tokens = sum(cache_write_tokens), "
        "cache_write_reporting_records = countif(isnotnull(cache_write_tokens)), "
        "reasoning_tokens = sum(reasoning_tokens), "
        "reasoning_reporting_records = countif(isnotnull(reasoning_tokens)), "
        "last_seen = max(TimeGenerated) "
        "by project_resource_id, model, deployment",
        "| extend "
        "cache_read_tokens = iff(cache_read_reporting_records > 0, "
        "cache_read_tokens, long(null)), "
        "cache_write_tokens = iff(cache_write_reporting_records > 0, "
        "cache_write_tokens, long(null)), "
        "reasoning_tokens = iff(reasoning_reporting_records > 0, "
        "reasoning_tokens, long(null))",
        "| extend "
        "cache_read_tokens_partial = cache_read_reporting_records > 0 and "
        "cache_read_reporting_records < token_reporting_records, "
        "cache_write_tokens_partial = cache_write_reporting_records > 0 and "
        "cache_write_reporting_records < token_reporting_records, "
        "reasoning_tokens_partial = reasoning_reporting_records > 0 and "
        "reasoning_reporting_records < token_reporting_records",
        "| project-away token_reporting_records, cache_read_reporting_records, "
        "cache_write_reporting_records, reasoning_reporting_records",
    ]
    excluded_names = (
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        *sorted(TOKEN_CLASS_ALIAS_NAMES),
    )
    excluded = ", ".join(f'"{name}"' for name in excluded_names)
    return "\n".join(
        [
        "let model_events = materialize(",
        *event_lines,
        ");",
        "let model_summary = model_events",
        *summary_lines,
        ";",
        "let extra_class_summary = model_events",
        "| mv-expand token_class_name = bag_keys(Properties)",
        "| extend token_class_name = tostring(token_class_name)",
        '| where token_class_name startswith "gen_ai.usage."',
        f"| where token_class_name !in ({excluded})",
        "| extend token_class_value = todouble(Properties[token_class_name])",
        "| where isnotnull(token_class_value) and token_class_value >= 0",
        "| summarize token_class_value = sum(token_class_value) "
        "by project_resource_id, model, deployment, token_class_name",
        "| summarize extra_token_classes = "
        "make_bag(pack(token_class_name, token_class_value)) "
        "by project_resource_id, model, deployment;",
        "let agg = model_summary",
        "| join kind=leftouter extra_class_summary "
        "on project_resource_id, model, deployment",
        "| project-away project_resource_id1, model1, deployment1",
        ";",
        "let total_in_scope = toscalar(agg | count);",
        "agg",
        "| sort by requests desc",
        f"| take {MAX_ROWS_PER_QUERY}",
        "| extend total_in_scope = total_in_scope",
        ]
    )


def build_tools_query(
    filters: ObserveFilterState, *, scope_source: TelemetrySource | None = None
) -> str:
    """Build a bounded, metadata-only tool invocation aggregate."""
    base_lines = [
        _TELEMETRY_TABLES,
        _time_window_clause(filters),
        *_dimension_filters(filters, scope_source),
        *_agent_extend_clauses(),
        '| extend tool_name = tostring(Properties["gen_ai.tool.name"])',
        '| extend operation_name = tostring(Properties["gen_ai.operation.name"])',
    ]
    aggregate_lines = ["base", "| where isnotempty(tool_name)"]
    if filters.tool_name:
        aggregate_lines.append(f"| where tool_name == '{_kql_escape(filters.tool_name)}'")
    aggregate_lines.append(
        "| summarize invocations = count(), "
        "failures = countif(Success == false), "
        "p95_latency_ms = percentile(DurationMs, 95), "
        "last_seen = max(TimeGenerated) "
        "by project_resource_id, agent_key, agent_id, agent_name, provider_name, system, tool_name"
    )
    unattributed_count = (
        "0"
        if filters.tool_name
        else 'toscalar(base | where isempty(tool_name) and operation_name == "execute_tool" | count)'
    )
    return "\n".join(
        [
            f"let base = {base_lines[0]}",
            *base_lines[1:-1],
            f"{base_lines[-1]};",
            f"let unattributed_count = {unattributed_count};",
            f"let agg = {aggregate_lines[0]}",
            *aggregate_lines[1:-1],
            f"{aggregate_lines[-1]};",
            "let total_in_scope = toscalar(agg | count);",
            "union",
            "(",
            "    agg",
            "    | sort by invocations desc",
            f"    | take {MAX_ROWS_PER_QUERY}",
            "    | extend total_in_scope = total_in_scope,",
            "        unattributed_count = unattributed_count, _metadata_only = false",
            "),",
            "(",
            "    print total_in_scope = total_in_scope,",
            "        unattributed_count = unattributed_count, _metadata_only = true",
            "    | where total_in_scope == 0 and unattributed_count > 0",
            ")",
        ]
    )


def build_runs_query(
    filters: ObserveFilterState, *, scope_source: TelemetrySource | None = None
) -> str:
    """Build a bounded aggregate of conversation- or trace-correlated runs."""
    aggregate_lines = [
        _TELEMETRY_TABLES,
        _time_window_clause(filters),
        *_dimension_filters(filters, scope_source),
        *_agent_extend_clauses(),
        '| extend tool_name = tostring(Properties["gen_ai.tool.name"])',
        '| extend conversation_id = tostring(Properties["gen_ai.conversation.id"])',
        '| extend foundry_thread_id = tostring(Properties["gen_ai.thread.id"])',
        "| extend run_key = iff(isnotempty(conversation_id), conversation_id, "
        "iff(isnotempty(foundry_thread_id), foundry_thread_id, tostring(OperationId)))",
        '| extend run_key_kind = iff(isnotempty(conversation_id) or isnotempty(foundry_thread_id), '
        '"conversation", "trace")',
    ]
    if filters.run_key:
        aggregate_lines.append(f"| where run_key == '{_kql_escape(filters.run_key)}'")
    aggregate_lines.extend(
        [
            "| summarize started_at = min(TimeGenerated), "
            "last_activity_at = max(TimeGenerated), "
            "turns = dcount(OperationId), "
            "failed_turns = dcountif(OperationId, Success == false), "
            "tool_invocations = countif(isnotempty(tool_name)), "
            "tool_failures = countif(isnotempty(tool_name) and Success == false), "
            "input_tokens = sum(input_tokens), "
            "output_tokens = sum(output_tokens), "
            "input_token_reports = countif(isnotnull(input_tokens)), "
            "output_token_reports = countif(isnotnull(output_tokens)) "
            "by project_resource_id, agent_key, agent_id, agent_name, provider_name, system, "
            "run_key, run_key_kind",
            "| extend input_tokens = iff(input_token_reports == 0, long(null), input_tokens), "
            "output_tokens = iff(output_token_reports == 0, long(null), output_tokens)",
            '| extend duration_ms = todouble(datetime_diff("millisecond", last_activity_at, started_at))',
        ]
    )
    return _bounded_aggregate(aggregate_lines, order_by="last_activity_at")


def build_usage_query(
    filters: ObserveFilterState, *, scope_source: TelemetrySource | None = None
) -> str:
    """Bounded token-usage query broken down by agent and model together."""
    lines = [
        _TELEMETRY_TABLES,
        _time_window_clause(filters),
        *_dimension_filters(filters, scope_source),
        *_agent_extend_clauses(),
        "| summarize input_tokens = sum(input_tokens), "
        "output_tokens = sum(output_tokens), "
        "requests = count() "
        "by agent_key, model",
        f"| top {MAX_ROWS_PER_QUERY} by requests desc",
    ]
    return "\n".join(lines)


def build_trends_query(
    filters: ObserveFilterState,
    *,
    bucket: timedelta = timedelta(hours=1),
    scope_source: TelemetrySource | None = None,
) -> str:
    """Bounded time-bucketed invocation/latency trend query."""
    bucket_expr = f"{max(int(bucket.total_seconds()), 1)}s"
    lines = [
        _TELEMETRY_TABLES,
        _time_window_clause(filters),
        *_dimension_filters(filters, scope_source),
        "| summarize invocations = count(), "
        "failures = countif(Success == false), "
        f"p95_latency_ms = percentile(DurationMs, 95) by bin(TimeGenerated, {bucket_expr})",
        "| order by TimeGenerated asc",
        f"| take {MAX_ROWS_PER_QUERY}",
    ]
    return "\n".join(lines)


def build_agent_detail_query(
    filters: ObserveFilterState,
    *,
    agent_key: str,
    bucket: timedelta = timedelta(hours=1),
    scope_source: TelemetrySource | None = None,
) -> str:
    """Bounded single-agent trend query used by the agent detail view."""
    if not agent_key:
        raise ValueError("agent_key is required for agent detail queries")
    bucket_expr = f"{max(int(bucket.total_seconds()), 1)}s"
    lines = [
        _TELEMETRY_TABLES,
        _time_window_clause(filters),
        *_dimension_filters(filters, scope_source),
        '| extend agent_key = tostring(coalesce(Properties["gen_ai.agent.id"], '
        'Properties["gen_ai.agent.name"], "unknown"))',
        f"| where agent_key == '{_kql_escape(agent_key)}'",
        "| summarize invocations = count(), "
        "failures = countif(Success == false), "
        f"p95_latency_ms = percentile(DurationMs, 95) by bin(TimeGenerated, {bucket_expr})",
        "| order by TimeGenerated asc",
        f"| take {MAX_ROWS_PER_QUERY}",
    ]
    return "\n".join(lines)


def build_appgenai_content_query(*, trace_id: str, span_id: str | None = None) -> str:
    """Explicit, correlation-keyed ``AppGenAIContent`` query with no legacy fallback.

    Only ``AppGenAIContent`` is ever queried here (T048): there is no union
    with ``AppTraces``/``AppDependencies`` and no ``customDimensions``
    fallback, so a protected, zero-row result can never be silently backfilled
    from unprotected legacy telemetry.
    """
    if not trace_id:
        raise ValueError("trace_id is required for AppGenAIContent queries")
    lines = [
        _APPGENAI_TABLE,
        f"| where TraceId == '{_kql_escape(trace_id)}'",
    ]
    if span_id:
        lines.append(f"| where SpanId == '{_kql_escape(span_id)}'")
    lines.extend(
        [
            "| project TraceId, SpanId, EventName, Content",
            f"| take {MAX_ROWS_PER_QUERY}",
        ]
    )
    return "\n".join(lines)


def classify_appgenai_content_result(
    rows: Sequence[Mapping[str, Any]],
    *,
    source_resource_id: str,
    trace_id: str,
    span_id: str | None = None,
) -> GenerativeAIContent:
    """Classify ``AppGenAIContent`` rows honoring zero-row ambiguity (T048).

    A delegated query that returns zero rows is reported as
    ``protected_or_unavailable`` rather than an inferred "no data" state,
    because Azure Monitor cannot distinguish an unauthorized protected-table
    read from a table that genuinely has no matching rows.
    """
    if not rows:
        return GenerativeAIContent(
            trace_id=trace_id,
            span_id=span_id,
            source_resource_id=source_resource_id,
            protection_state="protected_or_unavailable",
        )

    fields: dict[str, list[Any]] = {}
    resolved_span_id = span_id
    for row in rows:
        event_name = row.get("EventName") or row.get("event_name")
        field_name = _EVENT_NAME_TO_FIELD.get(str(event_name))
        content = row.get("Content", row.get("content"))
        if field_name is not None and content is not None:
            fields.setdefault(field_name, []).append(content)
        row_span_id = row.get("SpanId") or row.get("span_id")
        if row_span_id and not resolved_span_id:
            resolved_span_id = row_span_id

    return GenerativeAIContent(
        trace_id=trace_id,
        span_id=resolved_span_id,
        source_resource_id=source_resource_id,
        protection_state="available",
        input_messages=fields.get("input_messages"),
        output_messages=fields.get("output_messages"),
        system_instructions=fields.get("system_instructions"),
        tool_content=fields.get("tool_content"),
        evaluation_explanation=fields.get("evaluation_explanation"),
    )


# ---------------------------------------------------------------------------
# Batched execution, per-source classification, and deadline handling (T044).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceQuery:
    """One bounded KQL query targeting a single telemetry source."""

    source_id: str
    workspace_id: str
    query: str
    timespan: tuple[datetime, datetime] | None = None


SourceStatus = Literal["success", "partial", "timeout", "throttled", "error"]


@dataclass(frozen=True)
class SourceResult:
    """Per-source outcome of a batched Azure Monitor query."""

    source_id: str
    status: SourceStatus
    tables: Any = None
    reason: str | None = None
    duration_ms: int = 0


@dataclass(frozen=True)
class _BatchRequestSpec:
    id: str
    workspace_id: str
    query: str
    timespan: tuple[datetime, datetime] | None
    server_timeout_seconds: int


def _classify_batch_item(item: Any) -> tuple[SourceStatus, Any, str | None]:
    error = getattr(item, "error", None)
    if error is not None:
        code = str(getattr(error, "code", "") or "").lower()
        message = str(getattr(error, "message", "") or error).lower()
        if "timeout" in code or "timeout" in message:
            return "timeout", None, str(getattr(error, "message", error))
        if "toomanyrequests" in code or "throttl" in message or " 429" in f" {message}":
            return "throttled", None, str(getattr(error, "message", error))
        return "error", None, str(getattr(error, "message", error))

    partial_error = getattr(item, "partial_error", None)
    tables = getattr(item, "tables", None)
    if partial_error is not None:
        reason = str(getattr(partial_error, "message", partial_error))
        return "partial", getattr(item, "partial_data", tables), reason

    status = str(getattr(item, "status", "") or "").lower()
    if "partial" in status:
        return "partial", tables, None
    return "success", tables, None


async def execute_source_batch(
    queries: Sequence[SourceQuery],
    *,
    client: Any,
    source_timeout_seconds: int = SOURCE_TIMEOUT_SECONDS,
    request_deadline_seconds: int = DEFAULT_REQUEST_DEADLINE_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    is_superseded: Callable[[], bool] | None = None,
) -> list[SourceResult]:
    """Execute up to 10 bounded source queries as one async batch (T044).

    ``client`` is any object exposing an ``async def query_batch(requests)``
    coroutine (the real ``azure.monitor.query.aio.LogsQueryClient`` or a test
    fake) so this module never needs to import ``azure.monitor.query``.
    """
    if len(queries) > MAX_SOURCES_PER_BATCH:
        raise ValueError(
            f"cannot batch more than {MAX_SOURCES_PER_BATCH} telemetry sources "
            "per Observe request"
        )
    if not queries:
        return []

    requests = [
        _BatchRequestSpec(
            id=query.source_id,
            workspace_id=query.workspace_id,
            query=query.query,
            timespan=query.timespan,
            server_timeout_seconds=source_timeout_seconds,
        )
        for query in queries
    ]

    start = clock()
    try:
        responses = await asyncio.wait_for(
            client.query_batch(requests), timeout=request_deadline_seconds
        )
    except asyncio.TimeoutError:
        duration_ms = int((clock() - start) * 1000)
        return [
            SourceResult(
                source_id=query.source_id,
                status="timeout",
                reason="Observe request deadline exceeded before this source responded",
                duration_ms=duration_ms,
            )
            for query in queries
        ]

    duration_ms = int((clock() - start) * 1000)

    if is_superseded is not None and is_superseded():
        raise SupersededRequestError(
            "a newer Observe query request superseded this in-flight batch"
        )

    if len(responses) != len(queries):
        raise ValueError(
            "query_batch response count did not match the number of requested sources"
        )

    results: list[SourceResult] = []
    for query, item in zip(queries, responses):
        status, tables, reason = _classify_batch_item(item)
        results.append(
            SourceResult(
                source_id=query.source_id,
                status=status,
                tables=tables,
                reason=reason,
                duration_ms=duration_ms,
            )
        )
    return results
