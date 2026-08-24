"""Tests for bounded KQL builders and batched query execution (T037/T039/T044/T048/T057)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from agentops.agent.observe.queries import (
    DEFAULT_LOOKBACK_HOURS,
    MAX_ROWS_PER_QUERY,
    MAX_SOURCES_PER_BATCH,
    SOURCE_TIMEOUT_SECONDS,
    TOKEN_CLASS_ALIASES,
    SourceQuery,
    SupersededRequestError,
    build_agent_detail_query,
    build_agents_query,
    build_appgenai_content_query,
    build_models_query,
    build_overview_query,
    build_runs_query,
    build_trends_query,
    build_tools_query,
    build_usage_query,
    classify_appgenai_content_result,
    default_lookback_window,
    execute_source_batch,
)
from agentops.core.observe import GenerativeAIContent, ObserveFilterState, TelemetrySource


def _filters(**overrides: Any) -> ObserveFilterState:
    start = overrides.pop("start", datetime(2024, 1, 1, tzinfo=timezone.utc))
    end = overrides.pop("end", datetime(2024, 1, 2, tzinfo=timezone.utc))
    return ObserveFilterState(start=start, end=end, **overrides)


# ---------------------------------------------------------------------------
# T037: KQL builders - bounds, early filters, 24h defaults.
# ---------------------------------------------------------------------------


def test_default_lookback_window_is_24_hours() -> None:
    now = datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc)
    start, end = default_lookback_window(now=now)
    assert end == now
    assert end - start == timedelta(hours=DEFAULT_LOOKBACK_HOURS)


def test_overview_query_is_bounded_to_time_window_and_tables() -> None:
    query = build_overview_query(_filters())
    assert "AppDependencies" in query
    assert "AppRequests" in query
    assert "TimeGenerated between" in query
    assert "2024-01-01" in query
    assert "2024-01-02" in query


def test_agents_query_applies_dimension_filters_and_bounds_rows() -> None:
    filters = _filters(
        project_resource_id="/subscriptions/11111111-1111-1111-1111-111111111111"
        "/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/acct"
        "/projects/proj",
        agent_id="agent-1",
        model="gpt-4o",
    )
    query = build_agents_query(filters)
    assert "gen_ai.project.id" in query
    assert "gen_ai.azure_ai_project.id" in query
    assert "agent-1" in query
    assert "gpt-4o" in query
    assert f"| take {MAX_ROWS_PER_QUERY}" in query
    assert "let total_in_scope = toscalar(agg | count);" in query
    assert "| extend total_in_scope = total_in_scope" in query
    assert 'Properties["gen_ai.provider.name"]' in query
    assert 'Properties["gen_ai.system"]' in query
    # Dimension filters must appear before the summarize (early filters).
    assert query.index("gen_ai.agent.id") < query.index("summarize")


def test_agent_and_model_queries_preserve_project_for_shared_workspace() -> None:
    project_a = (
        "/subscriptions/11111111-1111-1111-1111-111111111111"
        "/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/acct"
        "/projects/project-a"
    )
    project_b = project_a.removesuffix("project-a") + "project-b"
    source = TelemetrySource(
        source_id="shared-workspace",
        resource_id=(
            "/subscriptions/11111111-1111-1111-1111-111111111111"
            "/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/logs"
        ),
        workspace_id="workspace-guid",
        project_resource_ids=[project_a, project_b],
        state="available",
    )

    agents_query = build_agents_query(_filters(), scope_source=source)
    models_query = build_models_query(_filters(), scope_source=source)

    assert "project_resource_id = tostring(coalesce(" in agents_query
    assert 'Properties["gen_ai.azure_ai_project.id"]' in agents_query
    assert "by project_resource_id, agent_key" in agents_query
    assert "agent_id = take_anyif(agent_id, isnotempty(agent_id))" in agents_query
    assert "model = take_anyif(model, isnotempty(model))" in agents_query
    assert (
        "by project_resource_id, agent_key, agent_id, agent_name"
        not in agents_query
    )
    assert "by project_resource_id, model, deployment" in models_query


def test_models_query_summarizes_by_model_and_deployment() -> None:
    query = build_models_query(_filters())
    assert "by project_resource_id, model, deployment" in query
    assert "| sort by requests desc" in query
    assert f"| take {MAX_ROWS_PER_QUERY}" in query
    assert "total_in_scope" in query


@pytest.mark.parametrize("builder", [build_agents_query, build_models_query])
def test_bounded_aggregate_queries_count_scope_then_take_max_rows(builder) -> None:
    query = builder(_filters())

    assert "let agg =" in query
    assert "let total_in_scope = toscalar(agg | count);" in query
    assert query.index("let total_in_scope") < query.index("\nagg\n")
    assert f"| take {MAX_ROWS_PER_QUERY}" in query
    assert "| extend total_in_scope = total_in_scope" in query


def test_token_class_aliases_are_unique_and_limited_to_usage_attributes() -> None:
    assert set(TOKEN_CLASS_ALIASES) == {"cache_read", "cache_write", "reasoning"}
    aliases = [alias for values in TOKEN_CLASS_ALIASES.values() for alias in values]
    assert all(alias.startswith("gen_ai.usage.") for alias in aliases)
    assert len(aliases) == len(set(aliases))


def test_models_query_projects_and_sums_normalized_token_classes() -> None:
    query = build_models_query(_filters())
    for token_class, aliases in TOKEN_CLASS_ALIASES.items():
        assert f"| extend {token_class}_tokens = toint(coalesce(" in query
        for alias in aliases:
            assert f'Properties["{alias}"]' in query
        assert f"{token_class}_tokens = sum({token_class}_tokens)" in query
    assert "by project_resource_id, model, deployment" in query


def test_models_query_preserves_missing_and_intermittent_class_reporting() -> None:
    query = build_models_query(_filters())
    assert "token_reporting_records = countif(" in query
    for token_class in TOKEN_CLASS_ALIASES:
        reporting_records = f"{token_class}_reporting_records"
        assert f"{reporting_records} = countif(isnotnull({token_class}_tokens))" in query
        assert (
            f"{token_class}_tokens = iff({reporting_records} > 0, "
            f"{token_class}_tokens, long(null))"
        ) in query
        assert (
            f"{token_class}_tokens_partial = {reporting_records} > 0 and "
            f"{reporting_records} < token_reporting_records"
        ) in query


def test_models_query_projects_unmapped_usage_attributes_in_one_bounded_query() -> None:
    query = build_models_query(_filters())
    assert "materialize(" in query
    assert "bag_keys(Properties)" in query
    assert 'token_class_name startswith "gen_ai.usage."' in query
    assert "TOKEN_CLASS_ALIAS_NAMES" not in query
    for aliases in TOKEN_CLASS_ALIASES.values():
        for alias in aliases:
            assert f'"{alias}"' in query
    assert "token_class_value >= 0" in query
    assert "make_bag(pack(token_class_name, token_class_value))" in query
    assert "extra_token_classes" in query
    assert "join kind=leftouter" in query
    assert query.count("union AppDependencies, AppRequests") == 1
    assert query.count("by project_resource_id, model, deployment") >= 2
    assert "| sort by requests desc" in query
    assert f"| take {MAX_ROWS_PER_QUERY}" in query
    assert "| extend total_in_scope = total_in_scope" in query


def test_granular_classes_do_not_change_agents_or_combined_usage_queries() -> None:
    for query in (build_agents_query(_filters()), build_usage_query(_filters())):
        assert "cache_read_tokens" not in query
        assert "cache_write_tokens" not in query
        assert "reasoning_tokens" not in query
        assert "extra_token_classes" not in query
        assert "bag_keys(Properties)" not in query


def test_models_query_does_not_filter_by_runtime_or_model_family() -> None:
    query = build_models_query(_filters())
    for forbidden in (
        "runtime_type",
        "agent_type",
        "vendor",
        "Microsoft",
        "OpenAI",
        "Anthropic",
    ):
        assert forbidden not in query
    assert "| where provider_name" not in query


def test_usage_query_summarizes_tokens_by_agent_and_model() -> None:
    query = build_usage_query(_filters())
    assert "input_tokens = sum" in query
    assert "by agent_key, model" in query


def test_trends_query_is_bucketed_and_ordered() -> None:
    query = build_trends_query(_filters(), bucket=timedelta(minutes=30))
    assert "bin(TimeGenerated, 1800s)" in query
    assert "order by TimeGenerated asc" in query
    assert "| take 500" in query


def test_agent_detail_query_filters_to_single_agent_key() -> None:
    query = build_agent_detail_query(_filters(), agent_key="agent-42")
    assert "agent_key == 'agent-42'" in query
    assert "order by TimeGenerated asc" in query


def test_agent_detail_query_requires_agent_key() -> None:
    with pytest.raises(ValueError):
        build_agent_detail_query(_filters(), agent_key="")


def test_kql_string_filters_are_escaped_against_injection() -> None:
    filters = _filters(agent_id="o\\'brien")
    query = build_agents_query(filters)
    assert "o\\\\\\'brien" in query


def test_tool_filter_kql_metacharacters_remain_inside_the_string_literal() -> None:
    query = build_tools_query(_filters(tool_name="a' | take 1 //"))

    assert "tool_name == 'a\\' | take 1 //'" in query


def test_tools_query_uses_tool_metadata_without_reading_tool_content() -> None:
    query = build_tools_query(_filters(tool_name="lookup'o''ticket"))

    assert query.startswith("let base = union AppDependencies, AppRequests")
    assert 'Properties["gen_ai.tool.name"]' in query
    assert 'Properties["gen_ai.operation.name"]' in query
    assert "| where isnotempty(tool_name)" in query
    assert "unattributed_count" in query
    assert "_metadata_only = true" in query
    assert "lookup\\'o\\'\\'ticket" in query
    assert "AppGenAIContent" not in query
    assert "gen_ai.tool.message" not in query
    assert "provider_name, system, tool_name" in query
    assert "| sort by invocations desc" in query
    assert f"| take {MAX_ROWS_PER_QUERY}" in query


def test_runs_query_prefers_conversation_then_foundry_thread_before_trace() -> None:
    query = build_runs_query(_filters(run_key="run'o''key"))

    assert 'Properties["gen_ai.conversation.id"]' in query
    assert 'Properties["gen_ai.thread.id"]' in query
    assert query.index("conversation_id") < query.index("foundry_thread_id") < query.index(
        "tostring(OperationId)"
    )
    assert '"conversation", "trace"' in query
    assert "run\\'o\\'\\'key" in query
    assert "| sort by last_activity_at desc" in query
    assert "turns = dcount(OperationId)" in query
    assert "provider_name, system, run_key, run_key_kind" in query
    assert "input_token_reports = countif(isnotnull(input_tokens))" in query
    assert "iff(input_token_reports == 0, long(null), input_tokens)" in query


# ---------------------------------------------------------------------------
# T048/T039: AppGenAIContent correlation-key queries, no legacy fallback.
# ---------------------------------------------------------------------------


def test_appgenai_content_query_uses_correlation_keys_only() -> None:
    query = build_appgenai_content_query(trace_id="trace-1", span_id="span-1")
    assert query.startswith("AppGenAIContent")
    assert "TraceId == 'trace-1'" in query
    assert "SpanId == 'span-1'" in query
    assert "customDimensions" not in query
    assert "AppTraces" not in query
    assert "AppDependencies" not in query


def test_appgenai_content_query_requires_trace_id() -> None:
    with pytest.raises(ValueError):
        build_appgenai_content_query(trace_id="")


def test_classify_appgenai_content_zero_rows_is_protected_or_unavailable() -> None:
    content = classify_appgenai_content_result(
        [], source_resource_id="/subscriptions/s/resourceGroups/rg/providers/x/y", trace_id="trace-1"
    )
    assert isinstance(content, GenerativeAIContent)
    assert content.protection_state == "protected_or_unavailable"
    assert content.input_messages is None
    assert content.output_messages is None


def test_classify_appgenai_content_maps_event_rows_to_fields() -> None:
    rows = [
        {"TraceId": "trace-1", "SpanId": "span-1", "EventName": "gen_ai.system.message", "Content": "sys"},
        {"TraceId": "trace-1", "SpanId": "span-1", "EventName": "gen_ai.user.message", "Content": "hi"},
        {"TraceId": "trace-1", "SpanId": "span-1", "EventName": "gen_ai.assistant.message", "Content": "hello"},
        {"TraceId": "trace-1", "SpanId": "span-1", "EventName": "gen_ai.tool.message", "Content": "tool"},
        {"TraceId": "trace-1", "SpanId": "span-1", "EventName": "gen_ai.evaluation.result", "Content": "eval"},
    ]
    content = classify_appgenai_content_result(
        rows,
        source_resource_id="/subscriptions/s/resourceGroups/rg/providers/x/y",
        trace_id="trace-1",
    )
    assert content.protection_state == "available"
    assert content.system_instructions == ["sys"]
    assert content.input_messages == ["hi"]
    assert content.output_messages == ["hello"]
    assert content.tool_content == ["tool"]
    assert content.evaluation_explanation == ["eval"]


def test_classify_appgenai_content_ignores_unknown_event_names_missing_dimension() -> None:
    rows = [{"TraceId": "trace-1", "SpanId": "span-1", "EventName": "gen_ai.unknown.event", "Content": "x"}]
    content = classify_appgenai_content_result(
        rows,
        source_resource_id="/subscriptions/s/resourceGroups/rg/providers/x/y",
        trace_id="trace-1",
    )
    assert content.protection_state == "available"
    assert content.input_messages is None
    assert content.output_messages is None
    assert content.system_instructions is None
    assert content.tool_content is None
    assert content.evaluation_explanation is None


# ---------------------------------------------------------------------------
# T044/T057: batched execution, classification, timeout/deadline, supersede.
# ---------------------------------------------------------------------------


@dataclass
class _FakeError:
    code: str
    message: str


@dataclass
class _FakeBatchItem:
    tables: Any = None
    error: _FakeError | None = None
    partial_error: _FakeError | None = None
    partial_data: Any = None
    status: str = "Success"


@dataclass
class FakeBatchClient:
    responses: list[Any] = field(default_factory=list)
    delay_seconds: float = 0.0
    received_requests: list[Any] = field(default_factory=list)

    async def query_batch(self, requests: list[Any]) -> list[Any]:
        self.received_requests = list(requests)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return self.responses


def _queries(n: int) -> list[SourceQuery]:
    return [
        SourceQuery(source_id=f"source-{i}", workspace_id=f"ws-{i}", query="table")
        for i in range(n)
    ]


def test_execute_source_batch_rejects_more_than_ten_sources() -> None:
    client = FakeBatchClient(responses=[])
    with pytest.raises(ValueError):
        asyncio.run(
            execute_source_batch(_queries(MAX_SOURCES_PER_BATCH + 1), client=client)
        )


def test_execute_source_batch_returns_empty_for_no_sources() -> None:
    client = FakeBatchClient(responses=[])
    result = asyncio.run(execute_source_batch([], client=client))
    assert result == []


def test_execute_source_batch_classifies_success_partial_throttled_and_error() -> None:
    queries = _queries(4)
    client = FakeBatchClient(
        responses=[
            _FakeBatchItem(tables=["ok"], status="Success"),
            _FakeBatchItem(partial_error=_FakeError(code="PartialError", message="slow table")),
            _FakeBatchItem(error=_FakeError(code="TooManyRequests", message="throttled, retry later")),
            _FakeBatchItem(error=_FakeError(code="BadGatewayTimeout", message="query timeout")),
        ]
    )
    results = asyncio.run(execute_source_batch(queries, client=client))
    statuses = {result.source_id: result.status for result in results}
    assert statuses == {
        "source-0": "success",
        "source-1": "partial",
        "source-2": "throttled",
        "source-3": "timeout",
    }
    assert results[2].reason is not None


def test_execute_source_batch_maps_generic_errors_to_error_status() -> None:
    queries = _queries(1)
    client = FakeBatchClient(
        responses=[_FakeBatchItem(error=_FakeError(code="BadRequest", message="malformed query"))]
    )
    results = asyncio.run(execute_source_batch(queries, client=client))
    assert results[0].status == "error"
    assert "malformed query" in results[0].reason


def test_execute_source_batch_enforces_request_deadline_as_timeout() -> None:
    queries = _queries(2)
    client = FakeBatchClient(responses=[_FakeBatchItem(), _FakeBatchItem()], delay_seconds=0.2)
    results = asyncio.run(
        execute_source_batch(queries, client=client, request_deadline_seconds=0.01)
    )
    assert all(result.status == "timeout" for result in results)


def test_execute_source_batch_forwards_source_timeout_to_requests() -> None:
    queries = _queries(1)
    client = FakeBatchClient(responses=[_FakeBatchItem()])
    asyncio.run(
        execute_source_batch(queries, client=client, source_timeout_seconds=SOURCE_TIMEOUT_SECONDS)
    )
    assert client.received_requests[0].server_timeout_seconds == SOURCE_TIMEOUT_SECONDS


def test_execute_source_batch_raises_when_superseded() -> None:
    queries = _queries(1)
    client = FakeBatchClient(responses=[_FakeBatchItem()])
    with pytest.raises(SupersededRequestError):
        asyncio.run(
            execute_source_batch(queries, client=client, is_superseded=lambda: True)
        )


def test_execute_source_batch_rejects_mismatched_response_count() -> None:
    queries = _queries(2)
    client = FakeBatchClient(responses=[_FakeBatchItem()])
    with pytest.raises(ValueError):
        asyncio.run(execute_source_batch(queries, client=client))
