"""Bounded Azure Monitor query construction and execution.

This module never imports ``azure.monitor.query`` (it is not a declared
dependency of AgentOps). Query execution is expressed purely in terms of a
duck-typed ``client.query_batch(requests)`` coroutine so unit tests can use
lightweight fakes and production code can inject the real async
``LogsQueryClient`` without this module needing to import the SDK.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Mapping, Sequence
from uuid import UUID

from agentops.core.attribution import AttributionConfiguration
from agentops.core.cost import CostComponent
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

_TELEMETRY_TABLES = "union withsource=TelemetryTable AppDependencies, AppRequests"
_APPGENAI_TABLE = "AppGenAIContent"
_PROJECT_RESOURCE_ID = (
    'tostring(coalesce(Properties["gen_ai.project.id"], '
    'Properties["gen_ai.azure_ai_project.id"], '
    'Properties["microsoft.foundry.project.id"]))'
)

TOKEN_CLASS_ALIASES: dict[str, tuple[str, ...]] = {
    "cache_read": (
        "gen_ai.usage.cache_read.input_tokens",
        "gen_ai.usage.cache_read_input_tokens",
        "gen_ai.usage.cached_tokens",
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


def _period_time_window_clause(filters: ObserveFilterState) -> str:
    """Return the exact inclusive-start, exclusive-end period predicate."""
    return (
        f"| where TimeGenerated >= datetime({_iso(filters.start)}) and "
        f"TimeGenerated < datetime({_iso(filters.end)})"
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
            f"'{_kql_escape(project_id)}'"
            for project_id in scope_source.project_resource_ids
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
        '| extend operation_name = tostring(Properties["gen_ai.operation.name"])',
        '| extend model = tostring(coalesce(Properties["gen_ai.request.model"], '
        'Properties["gen_ai.response.model"]))',
        '| extend input_tokens = toint(Properties["gen_ai.usage.input_tokens"])',
        '| extend output_tokens = toint(Properties["gen_ai.usage.output_tokens"])',
    ]


def _cost_component_filter_clauses(
    component: CostComponent | None,
    scope_source: TelemetrySource | None,
) -> list[str]:
    if component is None:
        return []

    match = component.usage_match
    clauses: list[str] = []
    source_resource_id = (
        scope_source.foundry_resource_id or scope_source.resource_id
        if scope_source is not None
        else None
    )
    if match.source_resource_ids and (
        source_resource_id is None
        or source_resource_id not in match.source_resource_ids
    ):
        return ["| where false"]

    dimension_matches = (
        (match.project_resource_ids, "tolower(project_resource_id)"),
        (match.agent_keys, "agent_key"),
        (match.deployments, "deployment"),
        (match.models, "model"),
        (match.tool_names, "tool_name"),
        (match.credit_event_operations, "operation_name"),
    )
    for allowed, expression in dimension_matches:
        if not allowed:
            continue
        values = ", ".join(f"'{_kql_escape(value)}'" for value in sorted(allowed))
        clauses.append(f"| where {expression} in ({values})")
    return clauses


_COST_DIMENSIONS = (
    "project_resource_id",
    "agent_key",
    "agent_id",
    "agent_name",
    "provider_name",
    "system",
    "deployment",
    "model",
    "tool_name",
    "operation_name",
)


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
    """Aggregate agent invocations without counting internal HTTP/model spans."""
    base_lines = [
        _TELEMETRY_TABLES,
        _time_window_clause(filters),
        *_dimension_filters(filters, scope_source),
        *_agent_extend_clauses(),
        '| where operation_name == "invoke_agent"',
        '| extend is_request_invocation = TelemetryTable endswith "AppRequests", '
        'is_dependency_invocation = TelemetryTable endswith "AppDependencies"',
        "| where is_request_invocation or is_dependency_invocation",
    ]
    return "\n".join(
        [
            f"let candidates = materialize({base_lines[0]}",
            *base_lines[1:],
            ");",
            "let preferences = candidates",
            "| summarize has_request = countif(is_request_invocation) > 0 "
            "by project_resource_id, agent_key;",
            "candidates",
            "| join kind=leftouter preferences on project_resource_id, agent_key",
            "| where (has_request and is_request_invocation) or "
            "(not(has_request) and is_dependency_invocation)",
            "| summarize invocations = count(), "
            "failures = countif(Success == false), "
            "avg_latency_ms = avg(DurationMs), "
            "p95_latency_ms = percentile(DurationMs, 95)",
            "| project invocations, failures, avg_latency_ms, p95_latency_ms",
        ]
    )


def build_agents_query(
    filters: ObserveFilterState, *, scope_source: TelemetrySource | None = None
) -> str:
    """Bounded per-agent summary query (agent id/name, model, tokens, last seen)."""
    aggregate_lines = [
        _TELEMETRY_TABLES,
        _time_window_clause(filters),
        *_dimension_filters(filters, scope_source),
        *_agent_extend_clauses(),
        '| extend is_request_invocation = TelemetryTable endswith "AppRequests" and '
        'operation_name == "invoke_agent", '
        'is_dependency_invocation = TelemetryTable endswith "AppDependencies" and '
        'operation_name == "invoke_agent"',
        "| summarize request_invocations = countif(is_request_invocation), "
        "dependency_invocations = countif(is_dependency_invocation), "
        "request_failures = countif(is_request_invocation and Success == false), "
        "dependency_failures = countif(is_dependency_invocation and Success == false), "
        "request_p95_latency_ms = percentile("
        "iff(is_request_invocation, DurationMs, real(null)), 95), "
        "dependency_p95_latency_ms = percentile("
        "iff(is_dependency_invocation, DurationMs, real(null)), 95), "
        "input_tokens = sum(input_tokens), "
        "output_tokens = sum(output_tokens), "
        "last_seen = max(TimeGenerated), "
        "agent_id = take_anyif(agent_id, isnotempty(agent_id)), "
        "agent_name = take_anyif(agent_name, isnotempty(agent_name)), "
        "provider_name = take_anyif(provider_name, isnotempty(provider_name)), "
        "system = take_anyif(system, isnotempty(system)), "
        "model = take_anyif(model, isnotempty(model)) "
        "by project_resource_id, agent_key",
        "| extend invocations = iff(request_invocations > 0, "
        "request_invocations, dependency_invocations), "
        "failures = iff(request_invocations > 0, request_failures, dependency_failures), "
        "p95_latency_ms = iff(request_invocations > 0, "
        "request_p95_latency_ms, dependency_p95_latency_ms)",
        "| project-away request_invocations, dependency_invocations, "
        "request_failures, dependency_failures, request_p95_latency_ms, "
        "dependency_p95_latency_ms",
        "| where invocations > 0",
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
        "| where isnotempty(model) or isnotempty(deployment)",
        '| where operation_name !in ("invoke_agent", "execute_tool")',
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
    aggregate_lines = [
        "base",
        "| where isnotempty(tool_name)",
        "| join kind=leftouter runtime_evidence "
        "on project_resource_id, agent_key, OperationId",
        "| extend agent_id = iff(isnotempty(agent_id), agent_id, runtime_agent_id), "
        "agent_name = iff(isnotempty(agent_name), agent_name, runtime_agent_name), "
        "provider_name = iff(isnotempty(provider_name), "
        "provider_name, runtime_provider_name), "
        "system = iff(isnotempty(system), system, runtime_system)",
        "| project-away project_resource_id1, agent_key1, OperationId1, "
        "runtime_agent_id, runtime_agent_name, runtime_provider_name, runtime_system",
    ]
    if filters.tool_name:
        aggregate_lines.append(
            f"| where tool_name == '{_kql_escape(filters.tool_name)}'"
        )
    aggregate_lines.append(
        "| summarize invocations = count(), "
        "failures = countif(Success == false), "
        "p95_latency_ms = percentile(DurationMs, 95), "
        "last_seen = max(TimeGenerated), "
        "agent_id = take_anyif(agent_id, isnotempty(agent_id)), "
        "agent_name = take_anyif(agent_name, isnotempty(agent_name)), "
        "provider_name = take_anyif(provider_name, isnotempty(provider_name)), "
        "system = take_anyif(system, isnotempty(system)) "
        "by project_resource_id, agent_key, tool_name"
    )
    unattributed_count = (
        "0"
        if filters.tool_name
        else 'toscalar(base | where isempty(tool_name) and operation_name == "execute_tool" | count)'
    )
    return "\n".join(
        [
            "let base = materialize(",
            *base_lines,
            ");",
            "let runtime_evidence = base",
            '| where operation_name == "invoke_agent"',
            "| summarize "
            "runtime_agent_id = take_anyif(agent_id, isnotempty(agent_id)), "
            "runtime_agent_name = take_anyif(agent_name, isnotempty(agent_name)), "
            "runtime_provider_name = take_anyif(provider_name, isnotempty(provider_name)), "
            "runtime_system = take_anyif(system, isnotempty(system)) "
            "by project_resource_id, agent_key, OperationId;",
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
        _period_time_window_clause(filters),
        *_dimension_filters(filters, scope_source),
        *_agent_extend_clauses(),
        *_token_class_extend_clauses(),
        '| extend tool_name = tostring(Properties["gen_ai.tool.name"])',
        '| extend operation_name = tostring(Properties["gen_ai.operation.name"])',
        "| extend credit_event = iff(isnotempty(operation_name), 1, long(null))",
        '| extend reported_credits = todecimal(Properties["gen_ai.usage.credits"])',
        "| extend credits = iff("
        "isnotnull(reported_credits) and reported_credits >= 0, "
        "reported_credits, decimal(null))",
        '| extend conversation_id = tostring(Properties["gen_ai.conversation.id"])',
        '| extend foundry_thread_id = tostring(Properties["gen_ai.thread.id"])',
        "| where isnotempty(conversation_id) or isnotempty(foundry_thread_id) "
        'or operation_name == "invoke_agent"',
        "| extend run_key = iff(isnotempty(conversation_id), conversation_id, "
        "iff(isnotempty(foundry_thread_id), foundry_thread_id, tostring(OperationId)))",
        "| extend run_key_kind = iff(isnotempty(conversation_id) or isnotempty(foundry_thread_id), "
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
            "cache_read_tokens = sum(cache_read_tokens), "
            "cache_write_tokens = sum(cache_write_tokens), "
            "reasoning_tokens = sum(reasoning_tokens), "
            "credits = sum(credits), "
            "credit_events = sum(credit_event), "
            "input_token_reports = countif(isnotnull(input_tokens)), "
            "output_token_reports = countif(isnotnull(output_tokens)), "
            "cache_read_token_reports = countif(isnotnull(cache_read_tokens)), "
            "cache_write_token_reports = countif(isnotnull(cache_write_tokens)), "
            "reasoning_token_reports = countif(isnotnull(reasoning_tokens)), "
            "credit_reports = countif(isnotnull(credits)), "
            "credit_event_reports = countif(isnotnull(credit_event)), "
            "agent_id = take_anyif(agent_id, isnotempty(agent_id)), "
            "agent_name = take_anyif(agent_name, isnotempty(agent_name)), "
            "provider_name = take_anyif(provider_name, isnotempty(provider_name)), "
            "system = take_anyif(system, isnotempty(system)) "
            "by project_resource_id, agent_key, run_key, run_key_kind",
            "| extend input_tokens = iff(input_token_reports == 0, long(null), input_tokens), "
            "output_tokens = iff(output_token_reports == 0, long(null), output_tokens), "
            "cache_read_tokens = iff(cache_read_token_reports == 0, long(null), "
            "cache_read_tokens), "
            "cache_write_tokens = iff(cache_write_token_reports == 0, long(null), "
            "cache_write_tokens), "
            "reasoning_tokens = iff(reasoning_token_reports == 0, long(null), "
            "reasoning_tokens), "
            "credits = iff(credit_reports == 0, decimal(null), credits), "
            "credit_events = iff(credit_event_reports == 0, long(null), credit_events)",
            "| project-away input_token_reports, output_token_reports, "
            "cache_read_token_reports, cache_write_token_reports, "
            "reasoning_token_reports, credit_reports, credit_event_reports",
            '| extend duration_ms = todouble(datetime_diff("millisecond", last_activity_at, started_at))',
        ]
    )
    return _bounded_aggregate(aggregate_lines, order_by="last_activity_at")


def build_drilldown_query(
    filters: ObserveFilterState,
    *,
    view: Literal["agents", "models", "tools", "runs"],
    selector: Mapping[str, str | None],
    scope_source: TelemetrySource | None = None,
    limit: int = 50,
) -> str:
    """Return bounded, metadata-only telemetry rows behind one aggregate."""
    if limit < 1 or limit > 100:
        raise ValueError("drill-through limit must be between 1 and 100")

    base_lines = [
        _TELEMETRY_TABLES,
        _time_window_clause(filters),
        *_dimension_filters(filters, scope_source),
        *_agent_extend_clauses(),
        '| extend deployment = tostring(Properties["gen_ai.request.deployment"]), '
        'tool_name = tostring(Properties["gen_ai.tool.name"]), '
        'conversation_id = tostring(Properties["gen_ai.conversation.id"]), '
        'foundry_thread_id = tostring(Properties["gen_ai.thread.id"])',
        "| extend run_key = iff(isnotempty(conversation_id), conversation_id, "
        "iff(isnotempty(foundry_thread_id), foundry_thread_id, tostring(OperationId)))",
    ]
    project_resource_id = selector.get("project_resource_id")
    if project_resource_id:
        base_lines.append(
            "| where tolower(project_resource_id) == "
            f"'{_kql_escape(project_resource_id.lower())}'"
        )
    else:
        base_lines.append("| where isempty(project_resource_id)")

    if view == "agents":
        agent_key = selector.get("agent_key")
        if not agent_key:
            raise ValueError("agent drill-through requires agent_key")
        base_lines.extend(
            [
                f"| where agent_key == '{_kql_escape(agent_key)}'",
                '| where operation_name == "invoke_agent"',
            ]
        )
    elif view == "models":
        model = selector.get("model")
        deployment = selector.get("deployment")
        if not model and not deployment:
            raise ValueError("model drill-through requires model or deployment")
        if model:
            base_lines.append(f"| where model == '{_kql_escape(model)}'")
        if deployment:
            base_lines.append(f"| where deployment == '{_kql_escape(deployment)}'")
        base_lines.append('| where operation_name !in ("invoke_agent", "execute_tool")')
    elif view == "tools":
        tool_name = selector.get("tool_name")
        if not tool_name:
            raise ValueError("tool drill-through requires tool_name")
        base_lines.append(f"| where tool_name == '{_kql_escape(tool_name)}'")
        if selector.get("agent_key"):
            base_lines.append(
                f"| where agent_key == '{_kql_escape(selector['agent_key'] or '')}'"
            )
    elif view == "runs":
        run_key = selector.get("run_key")
        if not run_key:
            raise ValueError("run drill-through requires run_key")
        base_lines.append(f"| where run_key == '{_kql_escape(run_key)}'")
        if selector.get("agent_key"):
            base_lines.append(
                f"| where agent_key == '{_kql_escape(selector['agent_key'] or '')}'"
            )
    else:
        raise ValueError(f"unsupported drill-through view: {view}")

    projection = [
        "| project timestamp = TimeGenerated, "
        'telemetry_type = iff(TelemetryTable endswith "AppRequests", '
        '"request", "dependency"), '
        "operation_name, trace_id = tostring(OperationId), "
        "span_id = tostring(Id), parent_span_id = tostring(ParentId), "
        "agent_id, agent_name, model, deployment, tool_name, "
        "success = Success, duration_ms = DurationMs",
        "| sort by timestamp desc",
        f"| take {limit + 1}",
    ]

    if view != "agents":
        return "\n".join([*base_lines, *projection])

    return "\n".join(
        [
            f"let selected = materialize({base_lines[0]}",
            *base_lines[1:],
            ");",
            "let has_request_rows = toscalar("
            'selected | where TelemetryTable endswith "AppRequests" | count) > 0;',
            "selected",
            "| where not(has_request_rows) or "
            'TelemetryTable endswith "AppRequests"',
            *projection,
        ]
    )


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


def _attribution_mapping_datatable(config: AttributionConfiguration) -> str:
    """Render the validated explicit-user mapping as one bounded KQL table."""
    rows = [
        (
            user_key,
            department.id,
            department.label,
        )
        for department in config.departments
        for user_key in department.user_keys
    ]
    values = ",\n    ".join(
        f"'{_kql_escape(user_key)}', "
        f"'{_kql_escape(department_id)}', "
        f"'{_kql_escape(department_label)}'"
        for user_key, department_id, department_label in rows
    )
    body = f"[\n    {values}\n]" if values else "[]"
    return (
        "datatable(user_key:string, department_id:string, "
        f"department_label:string) {body}"
    )


def build_department_usage_query(
    filters: ObserveFilterState,
    config: AttributionConfiguration,
    *,
    tenant_id: UUID | str,
    department_id: str | None = None,
    principal_user_keys: Sequence[str] = (),
    scope_source: TelemetrySource | None = None,
    cost_component: CostComponent | None = None,
) -> str:
    """Build a bounded privacy-safe department usage aggregate.

    Identity exists only in the ``classified`` intermediate relation. The final
    projection contains department aggregates and coverage counters, never an
    identity value or pseudonymous user key.
    """
    if (
        not config.enabled
        or config.deployment_namespace is None
        or config.generation is None
    ):
        raise ValueError("department attribution requires an enabled configuration")
    tenant = str(UUID(str(tenant_id)))
    namespace = str(config.deployment_namespace)
    generation = config.generation
    expected_prefix = f"usr1.g{generation}."
    normalized_principal_keys = tuple(sorted(set(principal_user_keys)))
    if any(
        not re.fullmatch(r"usr1\.g[1-9][0-9]*\.[0-9a-f]{64}", key)
        or not key.startswith(expected_prefix)
        for key in normalized_principal_keys
    ):
        raise ValueError("principal_user_keys contains an invalid active-generation key")
    principal_key_values = ", ".join(
        f"'{_kql_escape(key)}'" for key in normalized_principal_keys
    )

    base_lines = [
        _TELEMETRY_TABLES,
        _time_window_clause(filters),
        *_dimension_filters(filters, scope_source),
        *_agent_extend_clauses(),
        '| extend deployment = tostring(Properties["gen_ai.request.deployment"])',
        '| extend tool_name = tostring(Properties["gen_ai.tool.name"]), '
        'operation_name = tostring(Properties["gen_ai.operation.name"])',
        '| extend conversation_id = tostring(Properties["gen_ai.conversation.id"]), '
        'foundry_thread_id = tostring(Properties["gen_ai.thread.id"])',
        "| extend run_key = iff(isnotempty(conversation_id), conversation_id, "
        "iff(isnotempty(foundry_thread_id), foundry_thread_id, tostring(OperationId)))",
    ]
    if filters.tool_name:
        base_lines.append(f"| where tool_name == '{_kql_escape(filters.tool_name)}'")
    if filters.run_key:
        base_lines.append(f"| where run_key == '{_kql_escape(filters.run_key)}'")
    base_lines.extend(_cost_component_filter_clauses(cost_component, scope_source))

    canonical_prefix = _kql_escape(
        f"agentops-attribution-v1|{namespace}|{generation}|{tenant}|"
    )
    classified_lines = [
        "base",
        '| extend authenticated_id = trim(@"[ \\t\\r\\n]+", tostring(UserAuthenticatedId)), '
        'otel_enduser_id = trim(@"[ \\t\\r\\n]+", tostring(Properties["enduser.id"]))',
        "| extend identity_state = case("
        'isempty(authenticated_id) and isempty(otel_enduser_id), "not_reported", '
        "isempty(authenticated_id) or isempty(otel_enduser_id) or "
        'authenticated_id == otel_enduser_id, "identified", "ambiguous")',
        "| extend effective_identity = iff("
        'identity_state == "identified", '
        "iff(isnotempty(authenticated_id), authenticated_id, otel_enduser_id), "
        'tostring(""))',
        "| extend user_key = iff("
        'identity_state == "identified", '
        f'strcat("usr1.g{generation}.", hash_sha256('
        f'strcat("{canonical_prefix}", effective_identity))), tostring(""))',
        (
            f"| extend principal_member = user_key in ({principal_key_values})"
            if principal_key_values
            else "| extend principal_member = false"
        ),
        "| project-away authenticated_id, otel_enduser_id, effective_identity",
        "| join kind=leftouter mapping on user_key",
        "| project-away user_key1",
        "| extend mapping_state = case("
        'identity_state == "ambiguous", "ambiguous", '
        'identity_state == "identified" and isnotempty(department_id), "mapped", '
        '"unmapped")',
    ]
    if department_id is not None:
        if not isinstance(department_id, str) or not department_id.strip():
            raise ValueError("department_id must be a non-empty string")
        classified_lines.append(
            f"| where department_id == '{_kql_escape(department_id.strip())}'"
        )

    cost_group_fields = (
        ", identity_state, " + ", ".join(_COST_DIMENSIONS)
        if cost_component is not None
        else ""
    )
    cost_metadata_fields = (
        ", identity_state = 'not_reported', "
        + ", ".join(f"{field} = ''" for field in _COST_DIMENSIONS)
        if cost_component is not None
        else ""
    )
    cost_projection_fields = (
        ", identity_state, " + ", ".join(_COST_DIMENSIONS)
        if cost_component is not None
        else ""
    )

    return "\n".join(
        [
            f"let mapping = {_attribution_mapping_datatable(config)};",
            f"let base = materialize({base_lines[0]}",
            *base_lines[1:],
            ");",
            f"let classified = materialize({classified_lines[0]}",
            *classified_lines[1:],
            ");",
            "let eligible_records = toscalar(classified | count);",
            'let identified_records = toscalar(classified | where identity_state == "identified" | count);',
            'let mapped_records = toscalar(classified | where mapping_state == "mapped" | count);',
            'let unattributed_records = toscalar(classified | where mapping_state != "mapped" | count);',
            'let ambiguous_records = toscalar(classified | where mapping_state == "ambiguous" | count);',
            "let agg = classified",
            "| summarize invocations = count(), "
            "input_tokens = sum(input_tokens), "
            "input_token_reports = countif(isnotnull(input_tokens)), "
            "output_tokens = sum(output_tokens), "
            "output_token_reports = countif(isnotnull(output_tokens)), "
            "tool_invocations = countif(isnotempty(tool_name) or operation_name == "
            '"execute_tool"), '
            "tool_invocation_reports = countif(isnotempty(tool_name) or "
            "isnotempty(operation_name)), "
            "nonprincipal_member_count = dcountif("
            "user_key, isnotempty(user_key) and not(principal_member)), "
            "principal_member_present = max(toint(principal_member)) "
            "by department_id, department_label, mapping_state"
            f"{cost_group_fields}",
            "| extend member_count = nonprincipal_member_count + principal_member_present",
            "| extend input_tokens = iff(input_token_reports == 0, long(null), input_tokens), "
            "output_tokens = iff(output_token_reports == 0, long(null), output_tokens), "
            "tool_invocations = iff(tool_invocation_reports == 0, long(null), "
            "tool_invocations), active_session_seconds = decimal(null)",
            "| project-away input_token_reports, output_token_reports, "
            "tool_invocation_reports;",
            "let returned_records = toscalar(agg | count);",
            "union",
            "(",
            "    agg",
            "    | sort by invocations desc, department_id asc, mapping_state asc",
            f"    | take {MAX_ROWS_PER_QUERY}",
            "    | extend _metadata_only = false",
            "),",
            "(",
            "    print department_id = '', department_label = '', "
            'mapping_state = "unmapped", member_count = 0, '
            "nonprincipal_member_count = 0, principal_member_present = 0, "
            "invocations = 0, "
            "input_tokens = long(null), output_tokens = long(null), "
            "tool_invocations = long(null), active_session_seconds = decimal(null), "
            f"_metadata_only = true{cost_metadata_fields}",
            "    | where eligible_records == 0",
            ")",
            "| extend eligible_records = eligible_records, "
            "identified_records = identified_records, mapped_records = mapped_records, "
            "unattributed_records = unattributed_records, "
            "ambiguous_records = ambiguous_records, returned_records = returned_records",
            "| project department_id, department_label, mapping_state, member_count, "
            "nonprincipal_member_count, principal_member_present, "
            "invocations, input_tokens, output_tokens, tool_invocations, "
            "active_session_seconds, eligible_records, identified_records, "
            "mapped_records, unattributed_records, ambiguous_records, "
            f"returned_records, _metadata_only{cost_projection_fields}",
        ]
    )


# Backward-compatible descriptive alias for callers that spell out the boundary.
build_aggregate_department_usage_query = build_department_usage_query


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


def build_user_usage_query(
    filters: ObserveFilterState,
    config: AttributionConfiguration,
    *,
    tenant_id: UUID | str,
    department_id: str | None = None,
    selected_user_key: str | None = None,
    scope_source: TelemetrySource | None = None,
    cost_component: CostComponent | None = None,
) -> str:
    """Build the delegated-only exact per-source user attribution query.

    Every identity predicate is expressed in terms of a pseudonymous key that
    was computed by the caller. Raw telemetry identity is only projected after
    filtering and is never interpolated into KQL. Ranking and ``Other users``
    folding intentionally happen only after exact cross-source aggregation.
    """
    if (
        not config.enabled
        or config.deployment_namespace is None
        or config.generation is None
    ):
        raise ValueError("user attribution requires an enabled configuration")
    departments = {department.id: department for department in config.departments}
    if department_id is not None and department_id not in departments:
        raise ValueError("department_id is not allowlisted")
    expected_prefix = f"usr1.g{config.generation}."
    if selected_user_key is not None and (
        not re.fullmatch(r"usr1\.g[1-9][0-9]*\.[0-9a-f]{64}", selected_user_key)
        or not selected_user_key.startswith(expected_prefix)
    ):
        raise ValueError("selected_user_key is invalid for the active generation")

    allowed_user_keys = (
        tuple(sorted(departments[department_id].user_keys))
        if department_id is not None
        else None
    )
    tenant = str(UUID(str(tenant_id)))
    canonical_prefix = _kql_escape(
        f"agentops-attribution-v1|{config.deployment_namespace}|"
        f"{config.generation}|{tenant}|"
    )
    base = [
        _TELEMETRY_TABLES,
        _time_window_clause(filters),
        *_dimension_filters(filters, scope_source),
        *_agent_extend_clauses(),
        '| extend deployment = tostring(Properties["gen_ai.request.deployment"])',
        '| extend tool_name = tostring(Properties["gen_ai.tool.name"]), '
        'operation_name = tostring(Properties["gen_ai.operation.name"])',
        '| extend conversation_id = tostring(Properties["gen_ai.conversation.id"]), '
        'foundry_thread_id = tostring(Properties["gen_ai.thread.id"])',
        "| extend run_key = iff(isnotempty(conversation_id), conversation_id, "
        "iff(isnotempty(foundry_thread_id), foundry_thread_id, tostring(OperationId)))",
    ]
    if filters.tool_name:
        base.append(f"| where tool_name == '{_kql_escape(filters.tool_name)}'")
    if filters.run_key:
        base.append(f"| where run_key == '{_kql_escape(filters.run_key)}'")
    base.extend(_cost_component_filter_clauses(cost_component, scope_source))
    predicates: list[str] = []
    if selected_user_key is not None:
        predicates.append(f"user_key == '{_kql_escape(selected_user_key)}'")
    if allowed_user_keys is not None:
        if allowed_user_keys:
            values = ", ".join(f"'{_kql_escape(value)}'" for value in allowed_user_keys)
            predicates.append(f"user_key in ({values})")
        else:
            predicates.append("false")

    cost_group_fields = (
        ", " + ", ".join(_COST_DIMENSIONS) if cost_component is not None else ""
    )
    cost_projection_fields = (
        ", " + ", ".join(_COST_DIMENSIONS) if cost_component is not None else ""
    )

    return "\n".join(
        [
            "let base =",
            *base,
            ";",
            "let identity_rows = base",
            '| extend authenticated_id = trim(@"[ \\t\\r\\n]+", tostring(UserAuthenticatedId)), '
            'otel_enduser_id = trim(@"[ \\t\\r\\n]+", tostring(Properties["enduser.id"]))',
            "| extend raw_identity = case(",
            '    isempty(authenticated_id) and isempty(otel_enduser_id), "",',
            "    isempty(authenticated_id), otel_enduser_id,",
            "    isempty(otel_enduser_id), authenticated_id,",
            "    authenticated_id == otel_enduser_id, authenticated_id,",
            '    "")',
            "| extend user_key = iff(isnotempty(raw_identity), "
            f'strcat("usr1.g{config.generation}.", hash_sha256('
            f'strcat("{canonical_prefix}", raw_identity))), tostring(""))',
            "| project-away authenticated_id, otel_enduser_id",
            *(["| where " + " and ".join(predicates)] if predicates else []),
            ";",
            "let identified = identity_rows",
            "| where isnotempty(raw_identity) and isnotempty(user_key)",
            "| summarize invocations=count(),",
            "    input_tokens=sum(input_tokens), input_token_reports=countif(isnotnull(input_tokens)),",
            "    output_tokens=sum(output_tokens), output_token_reports=countif(isnotnull(output_tokens)),",
            "    tool_invocations=countif(isnotempty(tool_name) or operation_name == 'execute_tool'),",
            "    tool_invocation_reports=countif(isnotempty(tool_name) or isnotempty(operation_name))",
            f"  by user_key, raw_identity{cost_group_fields}",
            "| extend input_tokens=iff(input_token_reports == 0, long(null), input_tokens),",
            "    output_tokens=iff(output_token_reports == 0, long(null), output_tokens),",
            "    tool_invocations=iff(tool_invocation_reports == 0, long(null), tool_invocations),",
            "    active_session_seconds=decimal(null)",
            "| extend row_kind='user', distinct_users=1;",
            "let unattributed = identity_rows",
            "| where isempty(raw_identity) or isempty(user_key)",
            "| summarize invocations=count(), input_tokens=sum(input_tokens),",
            "    input_token_reports=countif(isnotnull(input_tokens)),",
            "    output_tokens=sum(output_tokens), output_token_reports=countif(isnotnull(output_tokens)),",
            "    tool_invocations=countif(isnotempty(tool_name) or operation_name == 'execute_tool'),",
            "    tool_invocation_reports=countif(isnotempty(tool_name) or isnotempty(operation_name))"
            f" by {', '.join(_COST_DIMENSIONS)}"
            if cost_component is not None
            else "    tool_invocation_reports=countif(isnotempty(tool_name) or isnotempty(operation_name))",
            "| extend input_tokens=iff(input_token_reports == 0, long(null), input_tokens),",
            "    output_tokens=iff(output_token_reports == 0, long(null), output_tokens),",
            "    tool_invocations=iff(tool_invocation_reports == 0, long(null), tool_invocations),",
            "    active_session_seconds=decimal(null)",
            "| extend row_kind='unattributed', user_key='', raw_identity='', distinct_users=0;",
            "union identified, unattributed",
            "| project row_kind, user_key, raw_identity,",
            "    invocations, input_tokens, output_tokens, tool_invocations,",
            f"    active_session_seconds, distinct_users{cost_projection_fields}",
        ]
    )


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
