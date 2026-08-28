"""Tests for Observe orchestration, normalization, and deterministic coverage.

Covers T038/T045/T046/T056/T059/T060: normalized ObservedAgent/ModelUsage
rows, source attribution, token-reporting semantics, two-minute identity/
scope/filter caching with refresh bypass, agent detail lookup, and
deterministic coverage classification (including protected_or_unavailable
zero-row behavior and safe, actionable failure reasons). All Azure access is
faked; nothing here imports azure.monitor.query or azure.mgmt.resourcegraph.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Sequence

import pytest

import agentops.agent.observe.service as observe_service_module
from agentops.agent.observe.cache import ObserveCache, SensitiveValueError
from agentops.agent.observe.queries import MAX_ROWS_PER_QUERY, SourceResult
from agentops.agent.observe.service import (
    CACHE_TTL_SECONDS,
    ObserveResult,
    ObserveService,
    PartialFailure,
    TokenClassInventory,
    _merge_user_attribution_coverage,
    _result_bounds,
    _source_attribution_coverage,
    classify_runtime,
    classify_discovery_coverage,
    classify_protected_content_coverage,
    normalize_cost_run_observation,
    classify_query_coverage,
    normalize_agent_row,
    normalize_model_row,
    normalize_run_row,
    normalize_tool_row,
    safe_failure_reason,
    token_class_inventory,
    token_reporting_state,
)
from agentops.agent.observe.attribution import (
    SingletonAttributionError,
    classify_department_cardinality,
    resolve_department,
)
from agentops.agent.observe.adapters import AggregateDepartmentUsageRow
from agentops.core.attribution import (
    AttributionConfiguration,
    AttributionUsage,
    derive_pseudonymous_user_key,
    issue_user_filter_token,
)
from agentops.core.cost import (
    MAX_COST_ROWS,
    CostComponent,
    CostModel,
    CostPeriod,
    CostUsageObservation,
)
from agentops.core.observe import (
    AttributionQueryRequest,
    ModelUsage,
    ObserveFilterState,
    ObserveQueryRequest,
    ObserveScope,
    ResourceInventory,
    TelemetrySource,
    UserAttributionCoverage,
)

_PROJECT_ID = (
    "/subscriptions/11111111-1111-1111-1111-111111111111"
    "/resourcegroups/rg/providers/microsoft.cognitiveservices/accounts/acct"
    "/projects/proj"
)
_PROJECT_ID_TWO = _PROJECT_ID.removesuffix("/proj") + "/proj-two"


def _scope() -> ObserveScope:
    return ObserveScope(mode="projects", project_resource_ids=[_PROJECT_ID])


def _filters(**overrides: Any) -> ObserveFilterState:
    start = overrides.pop("start", datetime(2024, 1, 1, tzinfo=timezone.utc))
    end = overrides.pop("end", datetime(2024, 1, 2, tzinfo=timezone.utc))
    return ObserveFilterState(start=start, end=end, **overrides)


def _source(
    source_id: str = "src-1",
    *,
    state: str = "available",
    reason: str | None = None,
) -> TelemetrySource:
    return TelemetrySource(
        source_id=source_id,
        resource_id=(
            "/subscriptions/11111111-1111-1111-1111-111111111111"
            "/resourceGroups/rg/providers/Microsoft.OperationalInsights"
            "/workspaces/logs"
        ),
        workspace_id="workspace-guid",
        foundry_resource_id=(
            "/subscriptions/11111111-1111-1111-1111-111111111111"
            "/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/acct"
        ),
        project_resource_ids=[_PROJECT_ID],
        state=state,
        reason=reason,
    )


def _shared_source() -> TelemetrySource:
    return _source().model_copy(
        update={"project_resource_ids": [_PROJECT_ID, _PROJECT_ID_TWO]}
    )


def _inventory(
    sources: Sequence[TelemetrySource],
    *,
    projects: list[dict[str, Any]] | None = None,
) -> ResourceInventory:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return ResourceInventory(
        scope=_scope(),
        projects=projects or [],
        telemetry_sources=list(sources),
        discovered_at=now,
        expires_at=now + timedelta(minutes=5),
    )


@dataclass
class FakeDatetimeClock:
    """Advances a fixed step every call so ``started_at`` != ``completed_at``."""

    now: datetime
    step: timedelta = timedelta(0)

    def __call__(self) -> datetime:
        current = self.now
        self.now = self.now + self.step
        return current


@dataclass
class FakeDiscoveryClient:
    inventory: ResourceInventory
    calls: int = field(default=0, init=False)

    async def discover(self, scope: ObserveScope) -> ResourceInventory:
        self.calls += 1
        return self.inventory


@dataclass
class FakeQueryClient:
    results: list[SourceResult]
    results_by_view: dict[str, list[SourceResult]] | None = None
    calls: list[tuple[Any, ...]] = field(default_factory=list, init=False)
    attribution_configs: list[AttributionConfiguration] = field(
        default_factory=list, init=False
    )

    async def query(
        self,
        sources: Sequence[TelemetrySource],
        filters: ObserveFilterState,
        *,
        view: str,
    ) -> list[SourceResult]:
        self.calls.append(
            (
                tuple(source.source_id for source in sources),
                view,
                filters.start,
                filters.end,
            )
        )
        if self.results_by_view is not None:
            return self.results_by_view.get(view, [])
        return self.results

    async def query_department_usage(
        self,
        sources: Sequence[TelemetrySource],
        filters: ObserveFilterState,
        *,
        config: AttributionConfiguration,
        tenant_id: str,
        department_id: str | None = None,
        principal_user_keys: Sequence[str] = (),
        cost_component: CostComponent | None = None,
    ) -> list[SourceResult]:
        self.attribution_configs.append(config)
        self.calls.append(
            (
                tuple(source.source_id for source in sources),
                "attribution",
                department_id,
                tenant_id,
                filters.start,
                filters.end,
                tuple(principal_user_keys),
            )
        )
        return self.results

    async def query_user_usage(
        self,
        sources: Sequence[TelemetrySource],
        filters: ObserveFilterState,
        *,
        config: AttributionConfiguration,
        tenant_id: str,
        department_id: str | None = None,
        selected_user_key: str | None = None,
        cost_component: CostComponent | None = None,
    ) -> list[SourceResult]:
        self.attribution_configs.append(config)
        self.calls.append(
            (
                tuple(source.source_id for source in sources),
                "user_attribution",
                department_id,
                selected_user_key,
                tenant_id,
                filters.start,
                filters.end,
            )
        )
        return self.results


@dataclass
class FakeRuntime:
    mode: str = "local"
    credential_identity: str = "user:alice"


def _service(
    *,
    inventory: ResourceInventory,
    results: list[SourceResult],
    clock: FakeDatetimeClock,
    cache: ObserveCache | None = None,
    runtime: FakeRuntime | None = None,
    results_by_view: dict[str, list[SourceResult]] | None = None,
) -> tuple[ObserveService, FakeDiscoveryClient, FakeQueryClient]:
    discovery = FakeDiscoveryClient(inventory)
    query = FakeQueryClient(results, results_by_view=results_by_view)
    service = ObserveService(
        discovery_client=discovery,
        query_client=query,
        runtime=runtime or FakeRuntime(),
        clock=clock,
        cache=cache or ObserveCache(ttl_seconds=CACHE_TTL_SECONDS),
    )
    return service, discovery, query


# ---------------------------------------------------------------------------
# T045: normalization, source attribution, token-reporting semantics.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agent_id", "agent_name", "provider_name", "agents", "expected"),
    [
        (
            "hosted-id",
            None,
            None,
            [{"id": "hosted-id", "kind": "hosted"}],
            "foundry_hosted",
        ),
        (
            "prompt-id",
            None,
            None,
            [{"id": "prompt-id", "kind": "prompt"}],
            "foundry_prompt",
        ),
        (
            None,
            "registered",
            None,
            [{"name": "registered", "kind": "custom"}],
            "external_registered",
        ),
        (None, "external", None, [], "external_unregistered"),
        (None, None, "copilot_studio", [], "copilot_studio"),
        ("unknown-id", None, None, [], "unknown"),
        (None, None, None, [], "unknown"),
    ],
)
def test_classify_runtime_uses_only_explicit_telemetry_and_inventory_evidence(
    agent_id: str | None,
    agent_name: str | None,
    provider_name: str | None,
    agents: list[dict[str, str]],
    expected: str,
) -> None:
    inventory = _inventory([_source()], projects=[{"properties": {"agents": agents}}])
    assert (
        classify_runtime(
            agent_id=agent_id,
            agent_name=agent_name,
            provider_name=provider_name,
            inventory=inventory,
        )
        == expected
    )


def test_classify_runtime_does_not_preserve_retired_foundry_label_without_evidence() -> (
    None
):
    assert classify_runtime(agent_id="previously-foundry", agent_name=None) == "unknown"


def test_token_reporting_state_distinguishes_absence_from_zero() -> None:
    assert token_reporting_state(input_tokens=0, output_tokens=0) == "reported"
    assert (
        token_reporting_state(input_tokens=None, output_tokens=None) == "not_reported"
    )
    assert token_reporting_state(input_tokens=None, output_tokens=5) == "reported"


def test_model_usage_new_token_class_fields_have_backward_compatible_defaults() -> None:
    usage = ModelUsage(requests=1, failures=0)
    assert usage.cache_read_tokens is None
    assert usage.cache_write_tokens is None
    assert usage.reasoning_tokens is None
    assert usage.additional_token_classes == {}
    assert usage.additional_token_classes_truncated is False
    assert usage.partially_reported_token_classes == ()
    assert usage.token_classes_partial is False


def test_normalize_agent_row_attributes_project_and_foundry_resource() -> None:
    source = _source()
    row = {
        "agent_key": "agent-1",
        "agent_id": "agent-1",
        "agent_name": None,
        "model": "gpt-4o",
        "invocations": 10,
        "failures": 2,
        "p95_latency_ms": 120.0,
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 25,
        "cache_write_tokens": 5,
        "reasoning_tokens": 10,
        "last_seen": datetime(2024, 1, 1, 12, tzinfo=timezone.utc),
    }
    agent = normalize_agent_row(row, source=source)
    assert agent.key == "agent-1"
    assert agent.source_id == source.source_id
    assert agent.source_kind == "unknown"
    assert agent.project_resource_id == _PROJECT_ID
    assert agent.foundry_resource_id == source.foundry_resource_id
    assert agent.invocations == 10
    assert agent.failures == 2
    assert agent.cache_read_tokens == 25
    assert agent.cache_write_tokens == 5
    assert agent.reasoning_tokens == 10


def test_normalize_agent_row_classifies_external_agent_by_name_only() -> None:
    source = _source()
    row = {
        "agent_key": "custom-bot",
        "agent_id": None,
        "agent_name": "custom-bot",
        "model": None,
        "invocations": 1,
        "failures": 0,
        "last_seen": datetime(2024, 1, 1, tzinfo=timezone.utc),
    }
    agent = normalize_agent_row(row, source=source)
    assert agent.source_kind == "external_unregistered"


def test_normalize_agent_row_requires_last_seen_timestamp() -> None:
    source = _source()
    row = {"agent_key": "agent-1", "invocations": 1, "failures": 0}
    with pytest.raises(ValueError, match="last_seen"):
        normalize_agent_row(row, source=source)


def test_normalize_model_row_reads_deployment_and_tokens() -> None:
    source = _source()
    row = {
        "model": "gpt-4o",
        "deployment": "gpt-4o-prod",
        "requests": 5,
        "failures": 1,
        "input_tokens": 10,
        "output_tokens": 20,
        "last_seen": datetime(2024, 1, 1, tzinfo=timezone.utc),
    }
    usage = normalize_model_row(row, source=source)
    assert usage.deployment == "gpt-4o-prod"
    assert usage.source_id == source.source_id
    assert usage.project_resource_id == _PROJECT_ID
    assert usage.requests == 5
    assert usage.failures == 1


@pytest.mark.parametrize(
    ("row", "expected", "partial"),
    [
        (
            {
                "cache_read_tokens": 0,
                "cache_write_tokens": 12,
                "reasoning_tokens": 3,
            },
            (0, 12, 3),
            False,
        ),
        (
            {"cache_read_tokens": 9, "reasoning_tokens": 4},
            (9, None, 4),
            True,
        ),
        ({}, (None, None, None), False),
    ],
)
def test_normalize_model_row_preserves_token_class_absence_and_zero(
    row: dict[str, Any], expected: tuple[int | None, ...], partial: bool
) -> None:
    usage = normalize_model_row({"requests": 1, "failures": 0, **row}, source=_source())
    assert (
        usage.cache_read_tokens,
        usage.cache_write_tokens,
        usage.reasoning_tokens,
    ) == expected
    assert usage.token_classes_partial is partial


def test_normalize_model_row_preserves_intermittently_reported_classes() -> None:
    usage = normalize_model_row(
        {
            "requests": 3,
            "failures": 0,
            "input_tokens": 30,
            "output_tokens": 12,
            "cache_read_tokens": 7,
            "cache_write_tokens": 5,
            "reasoning_tokens": 9,
            "cache_read_tokens_partial": True,
            "cache_write_tokens_partial": False,
            "reasoning_tokens_partial": "true",
        },
        source=_source(),
    )

    assert usage.partially_reported_token_classes == ("cache-read", "reasoning")
    assert usage.token_classes_partial is True


@pytest.mark.parametrize(
    ("attribute", "expected_field"),
    [
        ("gen_ai.usage.cache_write.input_tokens", "cache_write_tokens"),
        ("gen_ai.usage.cache_creation.input_tokens", "cache_write_tokens"),
        ("gen_ai.usage.reasoning.output_tokens", "reasoning_tokens"),
        ("gen_ai.usage.reasoning_tokens", "reasoning_tokens"),
    ],
)
def test_normalize_model_row_maps_distinct_aliases_to_one_class(
    attribute: str, expected_field: str
) -> None:
    usage = normalize_model_row(
        {
            "requests": 1,
            "failures": 0,
            "extra_token_classes": {attribute: 17},
        },
        source=_source(),
    )
    assert getattr(usage, expected_field) == 17
    assert usage.additional_token_classes == {}


def test_normalize_model_row_uses_first_present_alias_without_double_counting() -> None:
    usage = normalize_model_row(
        {
            "requests": 1,
            "failures": 0,
            "extra_token_classes": {
                "gen_ai.usage.cache_write.input_tokens": 100,
                "gen_ai.usage.cache_creation.input_tokens": 100,
            },
        },
        source=_source(),
    )
    assert usage.cache_write_tokens == 100
    assert usage.additional_token_classes == {}


def test_normalize_model_row_parses_log_analytics_dynamic_json() -> None:
    usage = normalize_model_row(
        {
            "requests": 2,
            "failures": 0,
            "extra_token_classes": (
                '{"gen_ai.usage.audio.input_tokens":2,'
                '"gen_ai.usage.audio.output_tokens":3}'
            ),
        },
        source=_source(),
    )
    assert usage.additional_token_classes == {
        "gen_ai.usage.audio.input_tokens": 2,
        "gen_ai.usage.audio.output_tokens": 3,
    }


def test_normalize_model_row_retains_only_eligible_additional_classes() -> None:
    usage = normalize_model_row(
        {
            "requests": 1,
            "failures": 0,
            "extra_token_classes": {
                "gen_ai.usage.audio_tokens": 8,
                "gen_ai.usage.negative_tokens": -1,
                "gen_ai.usage.text_tokens": "invalid",
                "other.usage.image_tokens": 9,
            },
        },
        source=_source(),
    )
    assert usage.additional_token_classes == {"gen_ai.usage.audio_tokens": 8}
    assert usage.cache_read_tokens is None
    assert usage.cache_write_tokens is None
    assert usage.reasoning_tokens is None


def test_normalize_model_row_caps_sorted_additional_classes_and_marks_truncation() -> (
    None
):
    attributes = {
        f"gen_ai.usage.custom_{suffix}_tokens": value
        for value, suffix in enumerate(("g", "f", "e", "d", "c", "b", "a"), start=1)
    }
    usage = normalize_model_row(
        {"requests": 1, "failures": 0, "extra_token_classes": attributes},
        source=_source(),
    )
    assert list(usage.additional_token_classes) == [
        "gen_ai.usage.custom_a_tokens",
        "gen_ai.usage.custom_b_tokens",
        "gen_ai.usage.custom_c_tokens",
        "gen_ai.usage.custom_d_tokens",
        "gen_ai.usage.custom_e_tokens",
    ]
    assert usage.additional_token_classes_truncated is True


def test_shared_workspace_rows_preserve_their_canonical_project_attribution() -> None:
    source = _shared_source()
    last_seen = datetime(2024, 1, 1, 12, tzinfo=timezone.utc)
    agent_a = normalize_agent_row(
        {
            "project_resource_id": _PROJECT_ID.upper() + "/",
            "agent_key": "agent-a",
            "agent_id": "agent-a",
            "invocations": 2,
            "failures": 0,
            "last_seen": last_seen,
        },
        source=source,
    )
    agent_b = normalize_agent_row(
        {
            "project_resource_id": _PROJECT_ID_TWO,
            "agent_key": "agent-b",
            "agent_id": "agent-b",
            "invocations": 3,
            "failures": 0,
            "last_seen": last_seen,
        },
        source=source,
    )
    model_a = normalize_model_row(
        {
            "project_resource_id": _PROJECT_ID,
            "model": "gpt-4o",
            "requests": 2,
            "failures": 0,
            "last_seen": last_seen,
        },
        source=source,
    )
    model_b = normalize_model_row(
        {
            "project_resource_id": _PROJECT_ID_TWO.upper(),
            "model": "gpt-4o",
            "requests": 3,
            "failures": 0,
            "last_seen": last_seen,
        },
        source=source,
    )

    assert [agent_a.project_resource_id, agent_b.project_resource_id] == [
        _PROJECT_ID,
        _PROJECT_ID_TWO,
    ]
    assert [model_a.project_resource_id, model_b.project_resource_id] == [
        _PROJECT_ID,
        _PROJECT_ID_TWO,
    ]


@pytest.mark.parametrize("normalizer", [normalize_agent_row, normalize_model_row])
def test_shared_workspace_rejects_project_outside_source_boundary(normalizer) -> None:
    row = {
        "project_resource_id": _PROJECT_ID.removesuffix("/proj") + "/unrelated",
        "agent_key": "agent-1",
        "invocations": 1,
        "requests": 1,
        "failures": 0,
        "last_seen": datetime(2024, 1, 1, tzinfo=timezone.utc),
    }
    with pytest.raises(ValueError, match="outside its source boundary"):
        normalizer(row, source=_shared_source())


def test_shared_workspace_missing_project_is_not_assigned_to_first_project() -> None:
    row = {
        "agent_key": "agent-1",
        "invocations": 1,
        "failures": 0,
        "last_seen": datetime(2024, 1, 1, tzinfo=timezone.utc),
    }
    agent = normalize_agent_row(row, source=_shared_source())
    assert agent.project_resource_id is None


def test_normalize_agent_rows_from_distinct_sources_remain_distinguishable() -> None:
    row = {
        "agent_key": "agent-1",
        "agent_id": "agent-1",
        "invocations": 1,
        "failures": 0,
        "last_seen": datetime(2024, 1, 1, tzinfo=timezone.utc),
    }
    first = normalize_agent_row(row, source=_source("src-a"))
    second = normalize_agent_row(row, source=_source("src-b"))

    assert (first.key, first.source_id) == ("agent-1", "src-a")
    assert (second.key, second.source_id) == ("agent-1", "src-b")


def test_normalize_tool_row_preserves_source_and_omits_token_attribution() -> None:
    source = _source()
    tool = normalize_tool_row(
        {
            "tool_name": "search",
            "agent_key": "agent-1",
            "agent_id": "agent-1",
            "provider_name": "microsoft.agent_framework",
            "invocations": 4,
            "failures": 1,
            "p95_latency_ms": None,
            "last_seen": datetime(2024, 1, 1, 12, tzinfo=timezone.utc),
        },
        source=source,
    )

    assert tool.source_id == source.source_id
    assert tool.tool_name == "search"
    assert tool.source_kind == "foundry_hosted"
    assert tool.p95_latency_ms is None
    assert not hasattr(tool, "input_tokens")
    assert not hasattr(tool, "output_tokens")


def test_normalize_tool_row_rejects_missing_tool_name_instead_of_placeholder() -> None:
    with pytest.raises(ValueError, match="tool_name"):
        normalize_tool_row(
            {
                "agent_key": "agent-1",
                "last_seen": datetime(2024, 1, 1, tzinfo=timezone.utc),
            },
            source=_source(),
        )


def test_normalize_run_row_uses_settling_margin_and_sticky_failure() -> None:
    window_end = datetime(2024, 1, 2, tzinfo=timezone.utc)
    base_row = {
        "run_key": "conversation-1",
        "run_key_kind": "conversation",
        "agent_key": "agent-1",
        "started_at": datetime(2024, 1, 1, 23, tzinfo=timezone.utc),
        "last_activity_at": window_end - timedelta(seconds=60),
        "duration_ms": 1_000.0,
        "turns": 2,
        "failed_turns": 0,
        "tool_invocations": 1,
        "tool_failures": 0,
        "input_tokens": None,
        "output_tokens": 0,
    }

    in_progress = normalize_run_row(base_row, source=_source(), window_end=window_end)
    failed = normalize_run_row(
        {
            **base_row,
            "last_activity_at": window_end - timedelta(minutes=10),
            "failed_turns": 1,
        },
        source=_source(),
        window_end=window_end,
    )

    assert in_progress.status == "in_progress"
    assert in_progress.input_tokens is None
    assert in_progress.output_tokens == 0
    assert failed.status == "failed"


def test_normalize_cost_run_observation_preserves_null_zero_and_safe_fields() -> None:
    observed = normalize_run_row(
        {
            "run_key": "trace-1",
            "run_key_kind": "trace",
            "agent_key": "agent-1",
            "started_at": datetime(2024, 1, 1, 8, tzinfo=timezone.utc),
            "last_activity_at": datetime(2024, 1, 1, 8, 0, 1, tzinfo=timezone.utc),
            "duration_ms": 0,
            "turns": 1,
            "failed_turns": 0,
            "tool_invocations": 0,
            "tool_failures": 0,
            "input_tokens": None,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": None,
            "reasoning_tokens": 0,
            "credits": "0",
            "credit_events": 0,
            "input_messages": ["must not be copied"],
            "tool_content": {"secret": "must not be copied"},
        },
        source=_source(),
        window_end=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )

    usage = normalize_cost_run_observation(
        observed,
        source_resource_id=_source().foundry_resource_id or _source().resource_id,
        coverage_complete=False,
    )

    assert usage == CostUsageObservation(
        source_resource_id=_source().foundry_resource_id or _source().resource_id,
        project_resource_id=_PROJECT_ID,
        agent_key="agent-1",
        run_key="trace-1",
        runtime_kind="unknown",
        input_tokens=None,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=None,
        reasoning_tokens=0,
        tool_invocations=0,
        active_session_seconds=Decimal("0"),
        credits=Decimal("0"),
        credit_events=0,
        latest_observed_at=datetime(2024, 1, 1, 8, 0, 1, tzinfo=timezone.utc),
        coverage_complete=False,
    )
    assert "input_messages" not in CostUsageObservation.model_fields
    assert "tool_content" not in CostUsageObservation.model_fields


def test_result_bounds_only_uses_complete_per_source_totals() -> None:
    shown = [
        SourceResult(
            source_id="src-a",
            status="success",
            tables=[{"total_in_scope": MAX_ROWS_PER_QUERY + 1}] * MAX_ROWS_PER_QUERY,
        )
    ]
    bounded = _result_bounds(shown, rows_shown=MAX_ROWS_PER_QUERY)
    unknown = _result_bounds(
        [SourceResult(source_id="src-a", status="success", tables=[{}])],
        rows_shown=1,
    )

    assert bounded.rows_total_in_scope == MAX_ROWS_PER_QUERY + 1
    assert bounded.truncated is True
    assert unknown.rows_total_in_scope is None
    assert unknown.truncated is False


def test_result_bounds_combines_truncated_multi_source_totals() -> None:
    first = SourceResult(
        source_id="src-a",
        status="success",
        tables=[{"total_in_scope": MAX_ROWS_PER_QUERY + 100}]
        * MAX_ROWS_PER_QUERY,
    )
    second = SourceResult(
        source_id="src-b",
        status="success",
        tables=[{"total_in_scope": 10}] * 10,
    )

    bounds = _result_bounds([first, second], rows_shown=MAX_ROWS_PER_QUERY)

    assert bounds.rows_total_in_scope == MAX_ROWS_PER_QUERY + 110
    assert bounds.rows_shown == MAX_ROWS_PER_QUERY
    assert bounds.truncated is True


# ---------------------------------------------------------------------------
# T056/T059/T060: deterministic coverage classification, safe reasons.
# ---------------------------------------------------------------------------


def test_safe_failure_reason_falls_back_to_default_and_truncates() -> None:
    assert safe_failure_reason(None, default="fallback") == "fallback"
    assert safe_failure_reason("   ", default="fallback") == "fallback"
    long_reason = "x" * 500
    result = safe_failure_reason(long_reason, default="fallback")
    assert len(result) <= 200
    assert result.endswith("...")


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("available", "available"),
        ("inaccessible", "inaccessible"),
        ("not_configured", "not_configured"),
        ("not_found", "error"),
        ("error", "error"),
    ],
)
def test_classify_discovery_coverage_is_deterministic_per_source_state(
    state: str, expected: str
) -> None:
    source = _source(state=state)
    refreshed_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    result = classify_discovery_coverage(
        source, dimension="resource_access", refreshed_at=refreshed_at
    )
    assert result.state == expected
    assert result.dimension == "resource_access"
    assert result.reason
    assert result.next_action


def test_classify_query_coverage_success_zero_rows_is_no_data_not_error() -> None:
    refreshed_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    result = classify_query_coverage(
        source_id="src-1",
        dimension="recent_traces",
        status="success",
        row_count=0,
        refreshed_at=refreshed_at,
    )
    assert result.state == "no_data"


def test_classify_query_coverage_success_with_rows_is_available() -> None:
    refreshed_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    result = classify_query_coverage(
        source_id="src-1",
        dimension="recent_traces",
        status="success",
        row_count=3,
        refreshed_at=refreshed_at,
    )
    assert result.state == "available"


def test_classify_query_coverage_unreported_dimension_is_not_reported() -> None:
    refreshed_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    result = classify_query_coverage(
        source_id="src-1",
        dimension="token_usage",
        status="success",
        row_count=3,
        reported=False,
        refreshed_at=refreshed_at,
    )
    assert result.state == "not_reported"


def test_token_class_inventory_skips_tokenless_rows_and_reports_missing_classes() -> (
    None
):
    tokenless = ModelUsage(requests=1, failures=0)
    cache_only = ModelUsage(
        requests=1,
        failures=0,
        input_tokens=10,
        output_tokens=2,
        cache_read_tokens=4,
        token_classes_partial=True,
    )
    inventory = token_class_inventory([tokenless, cache_only])
    assert inventory.state == "partial"
    assert inventory.reported_classes == ("cache-read",)
    assert inventory.missing_classes == ("cache-write", "reasoning")


def test_token_class_inventory_preserves_intermittent_reporting_across_rows() -> None:
    inventory = token_class_inventory(
        [
            ModelUsage(
                requests=3,
                failures=0,
                input_tokens=30,
                cache_read_tokens=7,
                cache_write_tokens=5,
                reasoning_tokens=9,
                partially_reported_token_classes=("cache-read",),
                token_classes_partial=True,
            ),
            ModelUsage(
                requests=1,
                failures=0,
                input_tokens=10,
                cache_read_tokens=2,
                cache_write_tokens=1,
                reasoning_tokens=3,
            ),
        ]
    )

    assert inventory.state == "partial"
    assert inventory.reported_classes == ("cache-read", "cache-write", "reasoning")
    assert inventory.missing_classes == ()
    assert inventory.partially_reported_classes == ("cache-read",)


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([], "not_reported"),
        ([ModelUsage(requests=1, failures=0, input_tokens=1)], "not_reported"),
        (
            [
                ModelUsage(
                    requests=1,
                    failures=0,
                    input_tokens=1,
                    cache_read_tokens=1,
                )
            ],
            "partial",
        ),
        (
            [
                ModelUsage(
                    requests=1,
                    failures=0,
                    input_tokens=1,
                    cache_read_tokens=1,
                    cache_write_tokens=2,
                    reasoning_tokens=3,
                )
            ],
            "reported",
        ),
    ],
)
def test_token_class_inventory_has_three_deterministic_states(
    rows: list[ModelUsage], expected: str
) -> None:
    assert token_class_inventory(rows).state == expected


def test_classify_query_coverage_partial_inventory_names_present_and_missing_classes() -> (
    None
):
    result = classify_query_coverage(
        source_id="src-1",
        dimension="token_usage",
        status="success",
        row_count=1,
        reported=TokenClassInventory(
            state="partial",
            reported_classes=("cache-read",),
            missing_classes=("cache-write", "reasoning"),
        ),
        refreshed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    assert result.state == "partial"
    assert "cache-read" in result.reason
    assert "cache-write" in result.reason
    assert "reasoning" in result.reason
    assert "cache-write" in result.next_action
    text = f"{result.reason} {result.next_action}".lower()
    for forbidden in ("cost", "price", "rate", "spend", "charge", "billing"):
        assert forbidden not in text.split()
    assert "src-1" not in text
    assert "union appdependencies" not in text


def test_classify_query_coverage_names_intermittently_reported_classes() -> None:
    result = classify_query_coverage(
        source_id="src-1",
        dimension="token_usage",
        status="success",
        row_count=1,
        reported=TokenClassInventory(
            state="partial",
            reported_classes=("cache-read", "cache-write", "reasoning"),
            missing_classes=(),
            partially_reported_classes=("cache-read",),
        ),
        refreshed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )

    assert result.state == "partial"
    assert "cache-read" in result.reason
    assert "cache-read" in result.next_action
    assert "consistently" in result.next_action


def test_query_failure_precedes_partial_token_inventory() -> None:
    result = classify_query_coverage(
        source_id="src-1",
        dimension="token_usage",
        status="timeout",
        row_count=1,
        reported=TokenClassInventory(
            state="partial",
            reported_classes=("cache-read",),
            missing_classes=("cache-write", "reasoning"),
        ),
        refreshed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    assert result.state == "error"
    assert "time budget" in result.reason


@pytest.mark.parametrize(
    ("source_state", "dimension", "expected_state"),
    [
        ("inaccessible", "tool_attribution", "inaccessible"),
        ("not_configured", "tool_attribution", "not_configured"),
        ("inaccessible", "run_correlation", "inaccessible"),
        ("not_configured", "run_correlation", "not_configured"),
    ],
)
def test_new_attribution_coverage_explains_unavailable_sources(
    source_state: str, dimension: str, expected_state: str
) -> None:
    result = _source_attribution_coverage(
        _source(state=source_state),
        dimension=dimension,  # type: ignore[arg-type]
        refreshed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    assert result.state == expected_state
    assert result.reason
    assert result.next_action


@pytest.mark.parametrize("dimension", ["tool_attribution", "run_correlation"])
@pytest.mark.parametrize(
    ("status", "row_count", "reported", "expected_state"),
    [
        ("success", 0, True, "no_data"),
        ("success", 1, False, "not_reported"),
        ("partial", 1, True, "partial"),
    ],
)
def test_new_attribution_coverage_distinguishes_data_gaps(
    dimension: str, status: str, row_count: int, reported: bool, expected_state: str
) -> None:
    result = classify_query_coverage(
        source_id="src-1",
        dimension=dimension,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        row_count=row_count,
        reported=reported,
        refreshed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    assert result.state == expected_state
    if expected_state != "available":
        assert result.reason
        assert result.next_action


@pytest.mark.parametrize(
    ("status", "expected_state"),
    [
        ("partial", "partial"),
        ("timeout", "error"),
        ("throttled", "error"),
        ("error", "error"),
    ],
)
def test_classify_query_coverage_never_infers_available_on_failure_paths(
    status: str, expected_state: str
) -> None:
    refreshed_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    result = classify_query_coverage(
        source_id="src-1",
        dimension="recent_traces",
        status=status,
        row_count=0,
        refreshed_at=refreshed_at,
    )
    assert result.state == expected_state
    assert result.reason
    assert result.next_action


@pytest.mark.parametrize(
    ("protection_state", "expected"),
    [
        ("available", "available"),
        ("not_configured", "not_configured"),
        ("protected_or_unavailable", "protected_or_unavailable"),
    ],
)
def test_classify_protected_content_coverage_zero_row_semantics(
    protection_state: str, expected: str
) -> None:
    refreshed_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    result = classify_protected_content_coverage(
        source_id="src-1", protection_state=protection_state, refreshed_at=refreshed_at
    )
    assert result.state == expected
    assert result.dimension == "protected_content"
    assert result.reason
    assert result.next_action


# ---------------------------------------------------------------------------
# T046/T038: orchestration, caching, refresh bypass, agent detail.
# ---------------------------------------------------------------------------


def _agent_rows_result(source_id: str = "src-1") -> SourceResult:
    return SourceResult(
        source_id=source_id,
        status="success",
        tables=[
            {
                "agent_key": "agent-1",
                "agent_id": "agent-1",
                "agent_name": None,
                "model": "gpt-4o",
                "invocations": 4,
                "failures": 0,
                "p95_latency_ms": 88.0,
                "input_tokens": 40,
                "output_tokens": 20,
                "last_seen": datetime(2024, 1, 1, 8, tzinfo=timezone.utc),
            }
        ],
    )


def _cost_period(*, components: list[dict[str, Any]] | None = None) -> CostPeriod:
    source_resource_id = _source().foundry_resource_id or _source().resource_id
    default_components: list[dict[str, Any]] = [
        {
            "id": "model-total",
            "type": "standard_model",
            "billing_boundary": {"kind": "resource", "value": source_resource_id},
            "billed_source": "Declared model total",
            "billed_total": "10.00",
            "currency": "USD",
            "currency_minor_units": 2,
            "allocation_model": "metered",
            "allocation_key": "total_tokens",
            "usage_match": {"deployments": ["gpt-prod"]},
        },
        {
            "id": "model-commitment",
            "type": "provisioned_throughput",
            "billing_boundary": {"kind": "resource", "value": source_resource_id},
            "billed_source": "Declared throughput commitment",
            "billed_total": "20.00",
            "currency": "USD",
            "currency_minor_units": 2,
            "allocation_model": "commitment",
            "allocation_key": "weighted_tokens",
            "fallback_key": "total_tokens",
            "token_weights": {"input_tokens": "1", "output_tokens": "1"},
            "usage_match": {"source_resource_ids": [source_resource_id]},
        },
        {
            "id": "search",
            "type": "search",
            "billing_boundary": {"kind": "resource", "value": source_resource_id},
            "billed_source": "Declared search total",
            "billed_total": "30.00",
            "currency": "USD",
            "currency_minor_units": 2,
            "allocation_model": "metered",
            "allocation_key": "tool_invocations",
            "usage_match": {"tool_names": ["search"]},
        },
        {
            "id": "compute",
            "type": "hosted_compute",
            "billing_boundary": {"kind": "resource", "value": source_resource_id},
            "billed_source": "Declared compute total",
            "billed_total": "40.00",
            "currency": "USD",
            "currency_minor_units": 2,
            "allocation_model": "metered",
            "allocation_key": "active_session_seconds",
            "usage_match": {"source_resource_ids": [source_resource_id]},
        },
    ]
    model = CostModel.model_validate(
        {
            "version": 1,
            "periods": [
                {
                    "id": "period-1",
                    "starts_at": "2024-01-10T00:00:00Z",
                    "ends_at": "2024-02-10T00:00:00Z",
                    "components": components or default_components,
                }
            ],
        }
    )
    return model.periods[0]


def _cost_model(period: CostPeriod) -> CostModel:
    return CostModel(version=1, periods=[period])


def _cost_results_by_view() -> dict[str, list[SourceResult]]:
    last_seen = datetime(2024, 2, 1, tzinfo=timezone.utc)
    return {
        "models": [
            SourceResult(
                source_id="src-1",
                status="success",
                tables=[
                    {
                        "agent_id": "agent-a",
                        "deployment": "gpt-prod",
                        "model": "gpt-4o",
                        "requests": 1,
                        "failures": 0,
                        "input_tokens": 10,
                        "output_tokens": 10,
                        "last_seen": last_seen,
                        "total_in_scope": 2,
                    },
                    {
                        "agent_id": "agent-b",
                        "deployment": "gpt-prod",
                        "model": "gpt-4o",
                        "requests": 1,
                        "failures": 0,
                        "input_tokens": 5,
                        "output_tokens": 5,
                        "last_seen": last_seen,
                        "total_in_scope": 2,
                    },
                ],
            )
        ],
        "tools": [
            SourceResult(
                source_id="src-1",
                status="success",
                tables=[
                    {
                        "tool_name": "search",
                        "agent_key": "agent-a",
                        "invocations": 1,
                        "failures": 0,
                        "last_seen": last_seen,
                        "total_in_scope": 2,
                    },
                    {
                        "tool_name": "search",
                        "agent_key": "agent-b",
                        "invocations": 3,
                        "failures": 0,
                        "last_seen": last_seen,
                        "total_in_scope": 2,
                    },
                ],
            )
        ],
        "runs": [
            SourceResult(
                source_id="src-1",
                status="success",
                tables=[
                    {
                        "run_key": "run-a-1",
                        "run_key_kind": "trace",
                        "agent_key": "agent-a",
                        "started_at": datetime(2024, 1, 20, tzinfo=timezone.utc),
                        "last_activity_at": datetime(
                            2024, 1, 20, 0, 0, 1, tzinfo=timezone.utc
                        ),
                        "duration_ms": 1_000,
                        "turns": 1,
                        "failed_turns": 0,
                        "tool_invocations": 0,
                        "tool_failures": 0,
                        "input_tokens": 1,
                        "output_tokens": 2,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": None,
                        "reasoning_tokens": None,
                        "credits": None,
                        "credit_events": 0,
                        "total_in_scope": 3,
                    },
                    {
                        "run_key": "run-a-2",
                        "run_key_kind": "trace",
                        "agent_key": "agent-a",
                        "started_at": datetime(2024, 1, 21, tzinfo=timezone.utc),
                        "last_activity_at": datetime(
                            2024, 1, 21, 0, 0, 3, tzinfo=timezone.utc
                        ),
                        "duration_ms": 3_000,
                        "turns": 1,
                        "failed_turns": 0,
                        "tool_invocations": 0,
                        "tool_failures": 0,
                        "input_tokens": 3,
                        "output_tokens": 4,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": None,
                        "reasoning_tokens": None,
                        "credits": "0",
                        "credit_events": 0,
                        "total_in_scope": 3,
                    },
                    {
                        "run_key": "run-b",
                        "run_key_kind": "trace",
                        "agent_key": "agent-b",
                        "started_at": datetime(2024, 1, 22, tzinfo=timezone.utc),
                        "last_activity_at": datetime(
                            2024, 1, 22, 0, 0, 4, tzinfo=timezone.utc
                        ),
                        "duration_ms": 4_000,
                        "turns": 1,
                        "failed_turns": 0,
                        "tool_invocations": 0,
                        "tool_failures": 0,
                        "input_tokens": 5,
                        "output_tokens": 5,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": None,
                        "reasoning_tokens": None,
                        "credits": None,
                        "credit_events": 0,
                        "total_in_scope": 3,
                    },
                ],
            )
        ],
    }


@pytest.mark.asyncio
async def test_query_cost_collects_each_required_view_once_and_uses_period_boundaries() -> (
    None
):
    clock = FakeDatetimeClock(
        datetime(2024, 2, 11, tzinfo=timezone.utc), step=timedelta(milliseconds=10)
    )
    period = _cost_period()
    service, _discovery, query = _service(
        inventory=_inventory([_source()]),
        results=[],
        results_by_view=_cost_results_by_view(),
        clock=clock,
    )

    result = await service.query_cost(
        _scope(),
        _filters(
            start=datetime(2020, 1, 1, tzinfo=timezone.utc),
            end=datetime(2020, 1, 2, tzinfo=timezone.utc),
            model="ignored-shared-filter",
            cost_period_id=period.id,
        ),
        cost_model=_cost_model(period),
        cost_model_fingerprint="a" * 64,
    )

    assert [call[1] for call in query.calls] == ["models", "runs", "tools"]
    assert all(call[2:] == (period.starts_at, period.ends_at) for call in query.calls)
    assert result.view == "cost"
    assert result.data.breakdown == "agents"
    assert {summary.component_id for summary in result.data.components} == {
        component.id for component in period.components
    }
    assert all(
        summary.declared_total
        == summary.attributed_amount
        + summary.unattributed_amount
        + summary.unallocated_amount
        for summary in result.data.components
    )
    weighted_rows = [
        row for row in result.data.rows if row.component_id == "model-commitment"
    ]
    assert {row.agent_key for row in weighted_rows} == {"agent-a", "agent-b"}
    assert len(weighted_rows) == 2


@pytest.mark.asyncio
async def test_query_cost_cache_identity_includes_model_fingerprint() -> None:
    period = _cost_period()
    service, _discovery, query = _service(
        inventory=_inventory([_source()]),
        results=[],
        results_by_view=_cost_results_by_view(),
        clock=FakeDatetimeClock(datetime(2024, 2, 11, tzinfo=timezone.utc)),
    )

    first = await service.query_cost(
        _scope(),
        _filters(cost_period_id=period.id),
        cost_model=_cost_model(period),
        cost_model_fingerprint="a" * 64,
    )
    hit = await service.query_cost(
        _scope(),
        _filters(model="ignored", cost_period_id=period.id),
        cost_model=_cost_model(period),
        cost_model_fingerprint="a" * 64,
    )
    changed = await service.query_cost(
        _scope(),
        _filters(cost_period_id=period.id),
        cost_model=_cost_model(period),
        cost_model_fingerprint="b" * 64,
    )

    assert first.cache_status == "miss"
    assert hit.cache_status == "hit"
    assert changed.cache_status == "miss"
    assert len(query.calls) == 6


@pytest.mark.asyncio
async def test_query_cost_propagates_partial_failures_diagnostics_and_cost_coverage() -> (
    None
):
    period = _cost_period(
        components=[_cost_period().components[2].model_dump(mode="json")]
    )
    partial = _cost_results_by_view()["tools"][0]
    partial = SourceResult(
        source_id=partial.source_id,
        status="partial",
        tables=partial.tables,
        reason="One shard timed out.",
    )
    service, _discovery, _query = _service(
        inventory=_inventory([_source()]),
        results=[],
        results_by_view={"tools": [partial]},
        clock=FakeDatetimeClock(datetime(2024, 2, 11, tzinfo=timezone.utc)),
    )

    result = await service.query_cost(
        _scope(),
        _filters(cost_period_id=period.id),
        cost_model=_cost_model(period),
        cost_model_fingerprint="a" * 64,
    )

    assert [failure.source_id for failure in result.partial_failures] == ["src-1"]
    assert result.diagnostics.partial_sources == 1
    cost_coverage = [
        item for item in result.coverage if item.dimension == "cost_attribution"
    ]
    assert len(cost_coverage) == 1
    assert cost_coverage[0].component_id == "search"
    assert cost_coverage[0].state == "partial"


@pytest.mark.asyncio
async def test_query_cost_keeps_omitted_amounts_when_agent_rows_are_bounded() -> None:
    first_component = _cost_period().components[0].model_dump(mode="json")
    second_component = {
        **first_component,
        "id": "model-total-secondary",
        "billed_source": "Second declared model total",
    }
    period = _cost_period(components=[first_component, second_component])
    first_rows = [
        {
            "agent_id": f"agent-{index:03d}",
            "deployment": "gpt-prod",
            "model": "gpt-4o",
            "requests": 1,
            "failures": 0,
            "input_tokens": 1,
            "output_tokens": 0,
            "last_seen": datetime(2024, 2, 1, tzinfo=timezone.utc),
            "total_in_scope": 300,
        }
        for index in range(300)
    ]
    second_rows = [
        {
            "agent_id": f"agent-{index:03d}",
            "deployment": "gpt-prod",
            "model": "gpt-4o",
            "requests": 1,
            "failures": 0,
            "input_tokens": 1,
            "output_tokens": 0,
            "last_seen": datetime(2024, 2, 1, tzinfo=timezone.utc),
            "total_in_scope": 201,
        }
        for index in range(300, 501)
    ]
    service, _discovery, _query = _service(
        inventory=_inventory([_source("src-1"), _source("src-2")]),
        results=[],
        results_by_view={
            "models": [
                SourceResult(source_id="src-1", status="success", tables=first_rows),
                SourceResult(source_id="src-2", status="success", tables=second_rows),
            ]
        },
        clock=FakeDatetimeClock(datetime(2024, 2, 11, tzinfo=timezone.utc)),
    )

    result = await service.query_cost(
        _scope(),
        _filters(cost_period_id=period.id),
        cost_model=_cost_model(period),
        cost_model_fingerprint="a" * 64,
    )

    assert {summary.component_id for summary in result.data.components} == {
        "model-total",
        "model-total-secondary",
    }
    assert all(summary.rows_total == 501 for summary in result.data.components)
    assert (
        sum(summary.rows_shown for summary in result.data.components)
        == MAX_COST_ROWS
    )
    assert all(
        summary.omitted_allocated_amount > 0 for summary in result.data.components
    )
    assert len(result.data.rows) == MAX_COST_ROWS


@pytest.mark.asyncio
async def test_query_cost_alternate_breakdowns_clip_runs_and_query_each_view_once() -> (
    None
):
    source_id = _source().foundry_resource_id or _source().resource_id
    period = _cost_period(
        components=[
            {
                "id": "compute",
                "type": "hosted_compute",
                "billing_boundary": {"kind": "resource", "value": source_id},
                "billed_source": "Declared compute total",
                "billed_total": "12.00",
                "currency": "USD",
                "currency_minor_units": 2,
                "allocation_model": "metered",
                "allocation_key": "active_session_seconds",
                "usage_match": {
                    "source_resource_ids": [source_id],
                    "runtime_kinds": ["external_unregistered"],
                },
            },
            {
                "id": "credits",
                "type": "credit_payg",
                "billing_boundary": {"kind": "resource", "value": source_id},
                "billed_source": "Declared credit total",
                "billed_total": "8.00",
                "currency": "USD",
                "currency_minor_units": 2,
                "allocation_model": "metered",
                "allocation_key": "credits",
                "usage_match": {"source_resource_ids": [source_id]},
            },
        ]
    )
    crossing = {
        "run_key": "crossing",
        "run_key_kind": "trace",
        "agent_key": "agent-a",
        "agent_name": "external-a",
        "project_resource_id": _PROJECT_ID,
        "started_at": period.starts_at - timedelta(days=1),
        "last_activity_at": period.starts_at + timedelta(days=1),
        "duration_ms": 2 * 24 * 60 * 60 * 1000,
        "turns": 1,
        "failed_turns": 0,
        "tool_invocations": 0,
        "tool_failures": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "reasoning_tokens": None,
        "credits": "2",
        "credit_events": None,
        "total_in_scope": 2,
    }
    outside = {
        **crossing,
        "run_key": "outside",
        "started_at": period.starts_at - timedelta(days=3),
        "last_activity_at": period.starts_at - timedelta(days=2),
    }
    service, _discovery, query = _service(
        inventory=_inventory([_source()]),
        results=[],
        results_by_view={
            "models": [SourceResult(source_id="src-1", status="success", tables=[])],
            "tools": [SourceResult(source_id="src-1", status="success", tables=[])],
            "runs": [
                SourceResult(
                    source_id="src-1",
                    status="success",
                    tables=[crossing, outside],
                )
            ],
        },
        clock=FakeDatetimeClock(datetime(2024, 2, 11, tzinfo=timezone.utc)),
    )

    result = await service.query_cost(
        _scope(),
        _filters(
            cost_period_id=period.id,
            cost_breakdown="runs",
        ),
        cost_model=_cost_model(period),
        cost_model_fingerprint="a" * 64,
    )

    assert {
        view: [call[1] for call in query.calls].count(view) for view in {"runs"}
    } == {"runs": 1}
    assert {row.run_key for row in result.data.rows} == {"crossing"}
    compute = next(row for row in result.data.rows if row.component_id == "compute")
    assert compute.usage_numerator == Decimal(24 * 60 * 60)
    assert compute.project_resource_id == _PROJECT_ID.lower()


@pytest.mark.asyncio
async def test_query_cost_tool_breakdown_uses_tool_identity_without_duplicate_query() -> (
    None
):
    source_id = _source().foundry_resource_id or _source().resource_id
    period = _cost_period(
        components=[
            {
                "id": "search-a",
                "type": "search",
                "billing_boundary": {"kind": "resource", "value": source_id},
                "billed_source": "Search A",
                "billed_total": "3.00",
                "currency": "USD",
                "currency_minor_units": 2,
                "allocation_model": "metered",
                "allocation_key": "tool_invocations",
                "usage_match": {"source_resource_ids": [source_id]},
            },
            {
                "id": "search-b",
                "type": "grounding",
                "billing_boundary": {"kind": "resource", "value": source_id},
                "billed_source": "Search B",
                "billed_total": "7.00",
                "currency": "USD",
                "currency_minor_units": 2,
                "allocation_model": "metered",
                "allocation_key": "tool_invocations",
                "usage_match": {"source_resource_ids": [source_id]},
            },
        ]
    )
    service, _discovery, query = _service(
        inventory=_inventory([_source()]),
        results=[],
        results_by_view={
            "tools": [
                SourceResult(
                    source_id="src-1",
                    status="success",
                    tables=[
                        {
                            "tool_name": "search",
                            "agent_key": "agent-a",
                            "project_resource_id": _PROJECT_ID,
                            "invocations": 4,
                            "failures": 0,
                            "last_seen": period.starts_at + timedelta(hours=1),
                            "total_in_scope": 1,
                        },
                        {
                            "_metadata_only": True,
                            "unattributed_count": 2,
                            "total_in_scope": 1,
                        },
                    ],
                )
            ]
        },
        clock=FakeDatetimeClock(datetime(2024, 2, 11, tzinfo=timezone.utc)),
    )

    result = await service.query_cost(
        _scope(),
        _filters(cost_period_id=period.id, cost_breakdown="tools"),
        cost_model=_cost_model(period),
        cost_model_fingerprint="a" * 64,
    )

    assert [call[1] for call in query.calls].count("tools") == 1
    assert {row.tool_name for row in result.data.rows} == {"search", None}
    assert any(row.consumer_kind == "unattributed" for row in result.data.rows)
    assert {summary.component_id for summary in result.data.components} == {
        "search-a",
        "search-b",
    }


@pytest.mark.asyncio
async def test_query_cost_merges_partial_period_and_complete_row_provenance() -> None:
    source = _source("src-readable")
    unreadable = _source(
        "src-inaccessible",
        state="inaccessible",
        reason="Workspace access was denied.",
    )
    source_id = source.foundry_resource_id or source.resource_id
    period = _cost_period(
        components=[
            {
                "id": "compute",
                "type": "hosted_compute",
                "billing_boundary": {"kind": "resource", "value": source_id},
                "billed_source": "Compute statement",
                "billed_total": "5.00",
                "currency": "USD",
                "currency_minor_units": 2,
                "allocation_model": "metered",
                "allocation_key": "active_session_seconds",
                "usage_match": {
                    "source_resource_ids": [source_id],
                    "runtime_kinds": ["external_unregistered"],
                },
            }
        ]
    )
    service, _discovery, _query = _service(
        inventory=_inventory([source, unreadable]),
        results=[],
        results_by_view={
            "runs": [
                SourceResult(
                    source_id=source.source_id,
                    status="success",
                    tables=[
                        {
                            "run_key": "run-readable",
                            "run_key_kind": "trace",
                            "agent_key": "agent-a",
                            "agent_name": "external-a",
                            "project_resource_id": _PROJECT_ID,
                            "started_at": period.starts_at + timedelta(hours=1),
                            "last_activity_at": period.starts_at + timedelta(hours=2),
                            "duration_ms": 3_600_000,
                            "turns": 1,
                            "failed_turns": 0,
                            "tool_invocations": 0,
                            "tool_failures": 0,
                            "input_tokens": None,
                            "output_tokens": None,
                            "cache_read_tokens": None,
                            "cache_write_tokens": None,
                            "reasoning_tokens": None,
                            "credits": None,
                            "credit_events": None,
                            "total_in_scope": 1,
                        }
                    ],
                )
            ]
        },
        clock=FakeDatetimeClock(datetime(2024, 2, 11, tzinfo=timezone.utc)),
    )

    result = await service.query_cost(
        _scope(),
        _filters(cost_period_id=period.id),
        cost_model=_cost_model(period),
        cost_model_fingerprint="b" * 64,
    )

    row = result.data.rows[0]
    assert row.source_resource_id == source_id.lower()
    assert row.project_resource_id == _PROJECT_ID.lower()
    assert row.starts_at == period.starts_at
    assert row.ends_at == period.ends_at
    assert row.calculated_at == result.data.calculated_at
    assert row.latest_observed_at == period.starts_at + timedelta(hours=2)
    summary = result.data.components[0]
    assert summary.confidence == "low"
    assert summary.coverage_state == "partial"
    component_coverage = next(
        item
        for item in result.coverage
        if item.dimension == "cost_attribution" and item.component_id == "compute"
    )
    assert component_coverage.state == "partial"
    assert component_coverage.next_action


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "tables", "expected_state"),
    [
        (_source(state="not_configured", reason="No workspace."), [], "not_configured"),
        (_source(state="inaccessible", reason="Access denied."), [], "inaccessible"),
        (_source(), [], "no_data"),
        (_source(), [{"malformed": "run"}], "error"),
    ],
)
async def test_query_cost_classifies_unavailable_period_states(
    source: TelemetrySource,
    tables: list[dict[str, Any]],
    expected_state: str,
) -> None:
    source_id = source.foundry_resource_id or source.resource_id
    period = _cost_period(
        components=[
            {
                "id": "compute",
                "type": "hosted_compute",
                "billing_boundary": {"kind": "resource", "value": source_id},
                "billed_source": "Compute statement",
                "billed_total": "9.00",
                "currency": "USD",
                "currency_minor_units": 2,
                "allocation_model": "metered",
                "allocation_key": "active_session_seconds",
                "usage_match": {"source_resource_ids": [source_id]},
            }
        ]
    )
    results = (
        {
            "runs": [
                SourceResult(
                    source_id=source.source_id, status="success", tables=tables
                )
            ]
        }
        if source.state == "available"
        else {}
    )
    service, _discovery, _query = _service(
        inventory=_inventory([source]),
        results=[],
        results_by_view=results,
        clock=FakeDatetimeClock(datetime(2024, 2, 11, tzinfo=timezone.utc)),
    )

    result = await service.query_cost(
        _scope(),
        _filters(cost_period_id=period.id),
        cost_model=_cost_model(period),
        cost_model_fingerprint="c" * 64,
    )

    summary = result.data.components[0]
    assert summary.unallocated_amount == summary.declared_total
    coverage = next(
        item
        for item in result.coverage
        if item.dimension == "cost_attribution" and item.component_id == "compute"
    )
    assert coverage.state == expected_state
    assert coverage.reason
    assert coverage.next_action


@pytest.mark.asyncio
async def test_query_cost_classifies_missing_partial_and_unattributed_keys() -> None:
    source_id = _source().foundry_resource_id or _source().resource_id
    components = [
        {
            "id": "duration",
            "type": "hosted_compute",
            "billing_boundary": {"kind": "resource", "value": source_id},
            "billed_source": "Duration",
            "billed_total": "4.00",
            "currency": "USD",
            "currency_minor_units": 2,
            "allocation_model": "metered",
            "allocation_key": "active_session_seconds",
            "usage_match": {"source_resource_ids": [source_id]},
        },
        {
            "id": "weighted",
            "type": "standard_model",
            "billing_boundary": {"kind": "resource", "value": source_id},
            "billed_source": "Weighted",
            "billed_total": "6.00",
            "currency": "USD",
            "currency_minor_units": 2,
            "allocation_model": "metered",
            "allocation_key": "weighted_tokens",
            "token_weights": {"cache_read_tokens": "1"},
            "usage_match": {"source_resource_ids": [source_id]},
        },
        {
            "id": "direct-credit",
            "type": "credit_payg",
            "billing_boundary": {"kind": "resource", "value": source_id},
            "billed_source": "Credits",
            "billed_total": "2.00",
            "currency": "USD",
            "currency_minor_units": 2,
            "allocation_model": "metered",
            "allocation_key": "credits",
            "usage_match": {"source_resource_ids": [source_id]},
        },
    ]
    period = _cost_period(components=components)
    base = {
        "run_key_kind": "trace",
        "agent_key": "unknown",
        "started_at": period.starts_at + timedelta(hours=1),
        "last_activity_at": period.starts_at + timedelta(hours=2),
        "turns": 1,
        "failed_turns": 0,
        "tool_invocations": 0,
        "tool_failures": 0,
        "input_tokens": 1,
        "output_tokens": 1,
        "cache_write_tokens": None,
        "reasoning_tokens": None,
        "credits": None,
        "credit_events": None,
        "total_in_scope": 2,
    }
    service, _discovery, _query = _service(
        inventory=_inventory([_source()]),
        results=[],
        results_by_view={
            "runs": [
                SourceResult(
                    source_id="src-1",
                    status="success",
                    tables=[
                        {
                            **base,
                            "run_key": "missing-duration",
                            "duration_ms": 1_800_000,
                            "cache_read_tokens": 2,
                        },
                        {
                            **base,
                            "run_key": "partial-weight",
                            "agent_key": "agent-b",
                            "duration_ms": 3_600_000,
                            "cache_read_tokens": None,
                        },
                    ],
                )
            ]
        },
        clock=FakeDatetimeClock(datetime(2024, 2, 11, tzinfo=timezone.utc)),
    )

    result = await service.query_cost(
        _scope(),
        _filters(cost_period_id=period.id),
        cost_model=_cost_model(period),
        cost_model_fingerprint="d" * 64,
    )

    by_component = {item.component_id: item for item in result.data.components}
    assert by_component["duration"].coverage_state == "partial"
    assert by_component["duration"].unattributed_amount > 0
    assert by_component["weighted"].coverage_state == "partial"
    assert by_component["direct-credit"].coverage_state == "not_reported"
    cost_coverage = {
        item.component_id: item
        for item in result.coverage
        if item.dimension == "cost_attribution" and item.component_id
    }
    assert cost_coverage["duration"].state == "partial"
    assert "identity" in cost_coverage["duration"].reason.lower()
    assert cost_coverage["weighted"].state == "partial"
    assert cost_coverage["direct-credit"].state == "not_reported"


@pytest.mark.asyncio
async def test_query_cost_reports_unmatched_capability_without_inferred_billing_type() -> (
    None
):
    source_id = _source().foundry_resource_id or _source().resource_id
    period = _cost_period(
        components=[
            {
                "id": "search",
                "type": "search",
                "billing_boundary": {"kind": "resource", "value": source_id},
                "billed_source": "Search",
                "billed_total": "3.00",
                "currency": "USD",
                "currency_minor_units": 2,
                "allocation_model": "metered",
                "allocation_key": "tool_invocations",
                "usage_match": {"tool_names": ["search"]},
            }
        ]
    )
    service, _discovery, query = _service(
        inventory=_inventory([_source()]),
        results=[],
        results_by_view={
            "models": [SourceResult(source_id="src-1", status="success", tables=[])],
            "tools": [SourceResult(source_id="src-1", status="success", tables=[])],
            "runs": [
                SourceResult(
                    source_id="src-1",
                    status="success",
                    tables=[
                        {
                            "run_key": "duration-only",
                            "run_key_kind": "trace",
                            "agent_key": "agent-a",
                            "started_at": period.starts_at + timedelta(hours=1),
                            "last_activity_at": period.starts_at + timedelta(hours=2),
                            "duration_ms": 3_600_000,
                            "turns": 1,
                            "failed_turns": 0,
                            "tool_invocations": 0,
                            "tool_failures": 0,
                            "input_tokens": None,
                            "output_tokens": None,
                            "cache_read_tokens": None,
                            "cache_write_tokens": None,
                            "reasoning_tokens": None,
                            "credits": None,
                            "credit_events": None,
                            "total_in_scope": 1,
                        }
                    ],
                )
            ],
        },
        clock=FakeDatetimeClock(datetime(2024, 2, 11, tzinfo=timezone.utc)),
    )

    result = await service.query_cost(
        _scope(),
        _filters(cost_period_id=period.id),
        cost_model=_cost_model(period),
        cost_model_fingerprint="e" * 64,
    )

    assert sorted(call[1] for call in query.calls) == ["models", "runs", "tools"]
    unmatched = [
        item
        for item in result.coverage
        if item.dimension == "cost_attribution" and item.component_id is None
    ]
    duration = next(
        item for item in unmatched if item.allocation_key == "active_session_seconds"
    )
    assert duration.state == "not_configured"
    assert "billing" not in duration.reason.lower()
    assert "compute" not in duration.reason.lower()


@pytest.mark.asyncio
async def test_query_cost_retains_success_after_partial_source_failure() -> None:
    first = _source("src-1")
    second = _source("src-2")
    source_id = first.foundry_resource_id or first.resource_id
    period = _cost_period(
        components=[
            {
                "id": "compute",
                "type": "hosted_compute",
                "billing_boundary": {"kind": "resource", "value": source_id},
                "billed_source": "Compute",
                "billed_total": "4.00",
                "currency": "USD",
                "currency_minor_units": 2,
                "allocation_model": "metered",
                "allocation_key": "active_session_seconds",
                "usage_match": {"source_resource_ids": [source_id]},
            }
        ]
    )
    service, _discovery, _query = _service(
        inventory=_inventory([first, second]),
        results=[],
        results_by_view={
            "runs": [
                SourceResult(
                    source_id=first.source_id,
                    status="success",
                    tables=[
                        {
                            "run_key": "readable",
                            "run_key_kind": "trace",
                            "agent_key": "agent-a",
                            "started_at": period.starts_at + timedelta(hours=1),
                            "last_activity_at": period.starts_at + timedelta(hours=2),
                            "duration_ms": 3_600_000,
                            "turns": 1,
                            "failed_turns": 0,
                            "tool_invocations": 0,
                            "tool_failures": 0,
                            "input_tokens": None,
                            "output_tokens": None,
                            "cache_read_tokens": None,
                            "cache_write_tokens": None,
                            "reasoning_tokens": None,
                            "credits": None,
                            "credit_events": None,
                            "total_in_scope": 1,
                        }
                    ],
                ),
                SourceResult(
                    source_id=second.source_id,
                    status="partial",
                    tables=[],
                    reason="One shard timed out.",
                ),
            ]
        },
        clock=FakeDatetimeClock(datetime(2024, 2, 11, tzinfo=timezone.utc)),
    )

    result = await service.query_cost(
        _scope(),
        _filters(cost_period_id=period.id),
        cost_model=_cost_model(period),
        cost_model_fingerprint="f" * 64,
    )

    assert result.data.rows
    assert result.data.components[0].confidence == "low"
    assert result.data.components[0].coverage_state == "partial"
    assert result.partial_failures[0].source_id == second.source_id
    assert result.diagnostics.partial_sources == 1


@pytest.mark.asyncio
async def test_query_view_normalizes_rows_and_reports_source_attribution() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory([_source()])
    service, _discovery, query = _service(
        inventory=inventory, results=[_agent_rows_result()], clock=clock
    )

    result = await service.query_view(_scope(), _filters(), view="agents")

    assert result.cache_status == "miss"
    assert len(result.data) == 1
    agent = result.data[0]
    assert agent.key == "agent-1"
    assert agent.source_kind == "unknown"
    assert agent.project_resource_id == _PROJECT_ID
    assert query.calls[0][1] == "agents"
    dimensions = {c.dimension for c in result.coverage}
    assert "resource_access" in dimensions
    assert "telemetry_connection" in dimensions
    assert "agent_attribution" in dimensions
    assert "token_usage" in dimensions


@pytest.mark.asyncio
async def test_query_view_tools_normalizes_rows_and_explains_unattributed_activity() -> (
    None
):
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory([_source()])
    result = SourceResult(
        source_id="src-1",
        status="success",
        tables=[
            {
                "tool_name": "search",
                "agent_key": "agent-1",
                "agent_id": "agent-1",
                "last_seen": datetime(2024, 1, 1, 8, tzinfo=timezone.utc),
                "invocations": 4,
                "failures": 0,
                "total_in_scope": 2,
            },
            {
                "agent_key": "agent-1",
                "last_seen": datetime(2024, 1, 1, 8, tzinfo=timezone.utc),
                "total_in_scope": 2,
            },
        ],
    )
    service, _discovery, query = _service(
        inventory=inventory, results=[result], clock=clock
    )

    observed = await service.query_view(_scope(), _filters(), view="tools")

    assert query.calls[0][1] == "tools"
    assert [(tool.tool_name, tool.source_id) for tool in observed.data] == [
        ("search", "src-1")
    ]
    assert observed.bounds is not None
    assert observed.bounds.rows_shown == 1
    assert observed.bounds.rows_total_in_scope is None
    assert observed.bounds.truncated is False
    coverage = [
        item for item in observed.coverage if item.dimension == "tool_attribution"
    ]
    assert any(
        item.state == "error" and item.reason and item.next_action for item in coverage
    )


@pytest.mark.asyncio
async def test_query_view_tools_reports_execute_tool_rows_without_names() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory([_source()])
    result = SourceResult(
        source_id="src-1",
        status="success",
        tables=[
            {
                "total_in_scope": 0,
                "unattributed_count": 3,
                "_metadata_only": True,
            }
        ],
    )
    service, _discovery, _query = _service(
        inventory=inventory, results=[result], clock=clock
    )

    observed = await service.query_view(_scope(), _filters(), view="tools")

    assert observed.data == []
    assert observed.bounds is not None
    assert observed.bounds.rows_shown == 0
    assert observed.bounds.rows_total_in_scope == 0
    coverage = [
        item for item in observed.coverage if item.dimension == "tool_attribution"
    ]
    assert coverage[-1].state == "not_reported"
    assert coverage[-1].reason
    assert coverage[-1].next_action


@pytest.mark.asyncio
async def test_query_view_runs_normalizes_window_scoped_status_and_coverage() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory([_source()])
    run = {
        "run_key": "trace-1",
        "run_key_kind": "trace",
        "agent_key": "agent-1",
        "started_at": datetime(2024, 1, 1, 8, tzinfo=timezone.utc),
        "last_activity_at": datetime(2024, 1, 1, 12, tzinfo=timezone.utc),
        "turns": 1,
        "failed_turns": 0,
        "tool_invocations": 0,
        "tool_failures": 0,
        "input_tokens": None,
        "output_tokens": None,
        "total_in_scope": 1,
    }
    service, _discovery, query = _service(
        inventory=inventory,
        results=[SourceResult(source_id="src-1", status="success", tables=[run])],
        clock=clock,
    )

    observed = await service.query_view(_scope(), _filters(), view="runs")

    assert query.calls[0][1] == "runs"
    assert observed.data[0].status == "succeeded"
    assert observed.data[0].run_key_kind == "trace"
    assert observed.data[0].input_tokens is None
    assert observed.bounds is not None
    assert observed.bounds.rows_total_in_scope == 1
    run_coverage = [
        item for item in observed.coverage if item.dimension == "run_correlation"
    ]
    assert run_coverage[-1].state == "available"
    assert run_coverage[-1].reason
    assert run_coverage[-1].next_action


@pytest.mark.asyncio
async def test_query_view_caches_bounds_and_keeps_them_stable_on_cache_hits() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory([_source()])
    result = _agent_rows_result()
    result = SourceResult(
        source_id=result.source_id,
        status=result.status,
        tables=[{**result.tables[0], "total_in_scope": 1}],
    )
    service, _discovery, _query = _service(
        inventory=inventory, results=[result], clock=clock
    )

    first = await service.query_view(_scope(), _filters(), view="agents")
    second = await service.query_view(_scope(), _filters(), view="agents")

    assert first.bounds is not None
    assert second.bounds == first.bounds
    assert second.bounds.rows_total_in_scope == 1


@pytest.mark.asyncio
async def test_models_view_emits_token_usage_coverage_without_changing_agents_entry() -> (
    None
):
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory([_source()])
    model_result = SourceResult(
        source_id="src-1",
        status="success",
        tables=[
            {
                "model": "gpt-4o",
                "requests": 1,
                "failures": 0,
                "input_tokens": 10,
                "output_tokens": 2,
                "cache_read_tokens": 4,
            }
        ],
    )
    models_service, _, _ = _service(
        inventory=inventory, results=[model_result], clock=clock
    )
    models_result = await models_service.query_view(_scope(), _filters(), view="models")
    models_coverage = {
        item.dimension: item
        for item in models_result.coverage
        if item.source_id == "src-1"
    }
    assert models_coverage["token_usage"].state == "partial"

    agents_service, _, _ = _service(
        inventory=inventory, results=[_agent_rows_result()], clock=clock
    )
    agents_result = await agents_service.query_view(_scope(), _filters(), view="agents")
    agents_coverage = {
        item.dimension: item
        for item in agents_result.coverage
        if item.source_id == "src-1"
    }
    assert agents_coverage["token_usage"].state == "available"


@pytest.mark.asyncio
async def test_query_view_caches_for_two_minutes_and_serves_hits_without_requerying() -> (
    None
):
    clock = FakeDatetimeClock(
        datetime(2024, 1, 1, tzinfo=timezone.utc), step=timedelta(seconds=1)
    )
    inventory = _inventory([_source()])
    service, discovery, query = _service(
        inventory=inventory, results=[_agent_rows_result()], clock=clock
    )

    first = await service.query_view(_scope(), _filters(), view="agents")
    second = await service.query_view(_scope(), _filters(), view="agents")

    assert first.cache_status == "miss"
    assert second.cache_status == "hit"
    assert len(query.calls) == 1
    assert discovery.calls == 1
    # Underlying data freshness is stable across hits within the TTL window.
    assert second.refreshed_at == first.refreshed_at


@pytest.mark.asyncio
async def test_query_view_pages_searches_and_sorts_one_cached_aggregate() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory([_source()])
    rows = [
        {
            "agent_key": f"agent-{index:03d}",
            "agent_id": f"agent-{index:03d}",
            "agent_name": f"Agent {index:03d}",
            "model": "gpt-4o",
            "invocations": index,
            "failures": 0,
            "last_seen": datetime(2024, 1, 1, 8, tzinfo=timezone.utc),
            "total_in_scope": 120,
        }
        for index in range(120)
    ]
    service, _discovery, query = _service(
        inventory=inventory,
        results=[SourceResult(source_id="src-1", status="success", tables=rows)],
        clock=clock,
    )

    second_page = await service.query_view(
        _scope(),
        _filters(),
        view="agents",
        page=2,
        page_size=50,
    )
    filtered = await service.query_view(
        _scope(),
        _filters(),
        view="agents",
        page_size=25,
        search="Agent 11",
        sort_by="agent_name",
        sort_direction="asc",
    )

    assert len(query.calls) == 1
    assert [row.agent_id for row in second_page.data[:2]] == [
        "agent-069",
        "agent-068",
    ]
    assert second_page.bounds is not None
    assert second_page.bounds.model_dump() == {
        "rows_shown": 50,
        "rows_total_in_scope": 120,
        "truncated": False,
        "page": 2,
        "page_size": 50,
        "has_previous_page": True,
        "has_next_page": True,
    }
    assert [row.agent_id for row in filtered.data] == [
        f"agent-{index:03d}" for index in range(110, 120)
    ]
    assert filtered.bounds is not None
    assert filtered.bounds.rows_total_in_scope == 10


def test_every_allowlisted_view_sort_field_satisfies_request_contract() -> None:
    for view, fields in observe_service_module._VIEW_SORT_FIELDS.items():
        for sort_field in fields:
            request = ObserveQueryRequest(
                view=view,
                filters=_filters(),
                sort_by=sort_field,
            )
            assert request.sort_by == sort_field


@pytest.mark.asyncio
async def test_truncated_paging_stops_at_last_materialized_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(observe_service_module, "MAX_ROWS_PER_QUERY", 3)
    rows = [
        {
            "agent_key": f"agent-{index}",
            "agent_id": f"agent-{index}",
            "agent_name": f"Agent {index}",
            "model": "gpt-4o",
            "invocations": index,
            "failures": 0,
            "last_seen": datetime(2024, 1, 1, 8, tzinfo=timezone.utc),
            "total_in_scope": 5,
        }
        for index in range(5)
    ]
    service, _discovery, query = _service(
        inventory=_inventory([_source()]),
        results=[SourceResult(source_id="src-1", status="success", tables=rows)],
        clock=FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc)),
    )

    first_page = await service.query_view(
        _scope(),
        _filters(),
        view="agents",
        page=1,
        page_size=2,
    )
    last_page = await service.query_view(
        _scope(),
        _filters(),
        view="agents",
        page=2,
        page_size=2,
    )
    filtered_last_page = await service.query_view(
        _scope(),
        _filters(),
        view="agents",
        page=2,
        page_size=2,
        search="Agent",
    )

    assert len(query.calls) == 1
    assert first_page.bounds is not None
    assert first_page.bounds.has_next_page is True
    assert last_page.bounds is not None
    assert last_page.bounds.rows_shown == 1
    assert last_page.bounds.rows_total_in_scope == 5
    assert last_page.bounds.truncated is True
    assert last_page.bounds.has_next_page is False
    assert filtered_last_page.bounds is not None
    assert filtered_last_page.bounds.rows_total_in_scope is None
    assert filtered_last_page.bounds.has_next_page is False


@pytest.mark.asyncio
async def test_query_view_coalesces_concurrent_identical_misses() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingQueryClient(FakeQueryClient):
        async def query(self, sources, filters, *, view):
            self.calls.append((tuple(source.source_id for source in sources), view))
            entered.set()
            await release.wait()
            return self.results

    inventory = _inventory([_source()])
    discovery = FakeDiscoveryClient(inventory)
    query = BlockingQueryClient([_agent_rows_result()])
    service = ObserveService(
        discovery_client=discovery,
        query_client=query,
        runtime=FakeRuntime(),
        clock=FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc)),
        cache=ObserveCache(ttl_seconds=CACHE_TTL_SECONDS),
    )

    first_task = asyncio.create_task(
        service.query_view(_scope(), _filters(), view="agents")
    )
    await entered.wait()
    second_task = asyncio.create_task(
        service.query_view(_scope(), _filters(), view="agents")
    )
    await asyncio.sleep(0)
    assert len(query.calls) == 1
    release.set()
    first, second = await asyncio.gather(first_task, second_task)

    assert {first.cache_status, second.cache_status} == {"miss", "hit"}
    assert first.data == second.data


@pytest.mark.asyncio
async def test_query_view_refresh_bypasses_cache_and_requeries() -> None:
    clock = FakeDatetimeClock(
        datetime(2024, 1, 1, tzinfo=timezone.utc), step=timedelta(seconds=1)
    )
    inventory = _inventory([_source()])
    service, discovery, query = _service(
        inventory=inventory, results=[_agent_rows_result()], clock=clock
    )

    await service.query_view(_scope(), _filters(), view="agents")
    refreshed = await service.query_view(
        _scope(), _filters(), view="agents", refresh=True
    )

    assert refreshed.cache_status == "bypass"
    assert len(query.calls) == 2
    assert discovery.calls == 1


@pytest.mark.asyncio
async def test_query_view_serves_stale_after_ttl_and_refreshes_in_background() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    cache = ObserveCache(ttl_seconds=CACHE_TTL_SECONDS, clock=lambda: cache_clock.now)
    cache_clock = FakeDatetimeClock(
        datetime(2024, 1, 1, tzinfo=timezone.utc)
    )  # placeholder
    inventory = _inventory([_source()])

    # Use a simple monotonic float clock for the cache's own TTL bookkeeping.
    class _FloatClock:
        def __init__(self) -> None:
            self.value = 0.0

        def __call__(self) -> float:
            return self.value

    float_clock = _FloatClock()
    cache = ObserveCache(ttl_seconds=CACHE_TTL_SECONDS, clock=float_clock)
    service, discovery, query = _service(
        inventory=inventory, results=[_agent_rows_result()], clock=clock, cache=cache
    )

    await service.query_view(_scope(), _filters(), view="agents")
    float_clock.value += CACHE_TTL_SECONDS + 1
    stale = await service.query_view(_scope(), _filters(), view="agents")
    await asyncio.gather(*service._background_tasks)

    assert stale.cache_status == "stale"
    assert len(query.calls) == 2


@pytest.mark.asyncio
async def test_query_view_cache_keys_are_scoped_by_identity_and_filters() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory([_source()])
    cache = ObserveCache(ttl_seconds=CACHE_TTL_SECONDS)
    service, _discovery, query = _service(
        inventory=inventory,
        results=[_agent_rows_result()],
        clock=clock,
        cache=cache,
        runtime=FakeRuntime(credential_identity="user:alice"),
    )
    other_identity_service, _discovery2, query2 = _service(
        inventory=inventory,
        results=[_agent_rows_result()],
        clock=clock,
        cache=cache,
        runtime=FakeRuntime(credential_identity="user:bob"),
    )

    await service.query_view(_scope(), _filters(), view="agents")
    await other_identity_service.query_view(_scope(), _filters(), view="agents")
    await service.query_view(_scope(), _filters(model="gpt-4o"), view="agents")
    # Re-run the original (identity, scope, filters) combination: still cached.
    await service.query_view(_scope(), _filters(), view="agents")

    # Different identity and different filters each produced their own
    # cache-miss query; the repeated original combination did not.
    assert len(query.calls) == 2
    assert len(query2.calls) == 1


@pytest.mark.asyncio
async def test_query_view_rejects_filters_outside_scope() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory([_source()])
    service, _discovery, _query = _service(
        inventory=inventory, results=[_agent_rows_result()], clock=clock
    )
    outside_project = (
        "/subscriptions/22222222-2222-2222-2222-222222222222"
        "/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/other"
        "/projects/other-proj"
    )
    bad_filters = _filters(project_resource_id=outside_project)

    with pytest.raises(ValueError, match="outside Observe scope"):
        await service.query_view(_scope(), bad_filters, view="agents")


@pytest.mark.asyncio
async def test_query_view_only_queries_available_sources() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory(
        [
            _source("src-available", state="available"),
            _source("src-blocked", state="inaccessible"),
        ]
    )
    service, _discovery, query = _service(
        inventory=inventory, results=[_agent_rows_result("src-available")], clock=clock
    )

    result = await service.query_view(_scope(), _filters(), view="agents")

    assert query.calls[0][0] == ("src-available",)
    coverage_by_source = {(c.source_id, c.dimension): c.state for c in result.coverage}
    assert coverage_by_source[("src-blocked", "resource_access")] == "inaccessible"


@pytest.mark.asyncio
async def test_query_view_malformed_row_is_reported_as_error_not_a_crash() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory([_source()])
    malformed = SourceResult(
        source_id="src-1",
        status="success",
        tables=[{"agent_key": "agent-1", "invocations": 1, "failures": 0}],
    )
    service, _discovery, _query = _service(
        inventory=inventory, results=[malformed], clock=clock
    )

    result = await service.query_view(_scope(), _filters(), view="agents")

    assert result.data == []
    error_entries = [c for c in result.coverage if c.state == "error"]
    assert any(c.dimension == "agent_attribution" for c in error_entries)


# ---------------------------------------------------------------------------
# T061: every query/agent_detail response carries safe, actionable
# partial_failures alongside diagnostics/source counts/coverage/refreshed_at.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_view_partial_failures_is_empty_when_every_source_succeeds() -> (
    None
):
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory([_source()])
    service, _discovery, _query = _service(
        inventory=inventory, results=[_agent_rows_result()], clock=clock
    )

    result = await service.query_view(_scope(), _filters(), view="agents")

    assert result.partial_failures == []


@pytest.mark.asyncio
async def test_query_view_partial_failures_summarizes_non_success_sources_safely() -> (
    None
):
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory(
        [
            _source("src-timeout"),
            _source("src-throttled"),
            _source("src-error"),
            _source("src-ok"),
        ]
    )
    results = [
        SourceResult(
            source_id="src-timeout",
            status="timeout",
            reason="Deadline exceeded after 30000ms\nwith secret trace payload leaked here",
        ),
        SourceResult(source_id="src-throttled", status="throttled", reason=None),
        SourceResult(
            source_id="src-error",
            status="error",
            reason="Traceback (most recent call last):\n  raise ValueError('boom')",
        ),
        _agent_rows_result("src-ok"),
    ]
    service, _discovery, _query = _service(
        inventory=inventory, results=results, clock=clock
    )

    result = await service.query_view(_scope(), _filters(), view="agents")

    failures_by_source = {f.source_id: f for f in result.partial_failures}
    assert set(failures_by_source) == {"src-timeout", "src-throttled", "src-error"}

    timeout_failure = failures_by_source["src-timeout"]
    assert isinstance(timeout_failure, PartialFailure)
    assert timeout_failure.status == "timeout"
    assert "\n" not in timeout_failure.reason
    assert "secret trace payload" not in timeout_failure.reason
    assert timeout_failure.next_action

    throttled_failure = failures_by_source["src-throttled"]
    assert throttled_failure.status == "throttled"
    assert throttled_failure.reason == "The query was throttled by Azure Monitor."

    error_failure = failures_by_source["src-error"]
    assert error_failure.status == "error"
    # Only the first line of the raw reason is kept; the stack trace body
    # (second line, containing the exception detail) never leaks through.
    assert "\n" not in error_failure.reason
    assert "ValueError" not in error_failure.reason


@pytest.mark.asyncio
async def test_query_view_partial_failures_propagate_through_cache_hit() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory([_source("src-1"), _source("src-2")])
    results = [
        _agent_rows_result("src-1"),
        SourceResult(source_id="src-2", status="error", reason="boom"),
    ]
    service, _discovery, query = _service(
        inventory=inventory, results=results, clock=clock
    )

    first = await service.query_view(_scope(), _filters(), view="agents")
    second = await service.query_view(_scope(), _filters(), view="agents")

    assert len(query.calls) == 1  # second call was served from cache
    assert second.cache_status == "hit"
    assert second.partial_failures == first.partial_failures
    assert [f.source_id for f in second.partial_failures] == ["src-2"]


@pytest.mark.asyncio
async def test_query_view_models_and_overview_normalize_correctly() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory([_source()])
    model_result = SourceResult(
        source_id="src-1",
        status="success",
        tables=[
            {
                "model": "gpt-4o",
                "deployment": "gpt-4o-prod",
                "requests": 3,
                "failures": 1,
                "input_tokens": 30,
                "output_tokens": 10,
                "last_seen": datetime(2024, 1, 1, tzinfo=timezone.utc),
            }
        ],
    )
    service, _discovery, _query = _service(
        inventory=inventory, results=[model_result], clock=clock
    )
    models = await service.query_view(_scope(), _filters(), view="models")
    assert models.data[0].deployment == "gpt-4o-prod"

    overview_service, _discovery2, _query2 = _service(
        inventory=inventory,
        results=[
            SourceResult(
                source_id="src-1",
                status="success",
                tables=[
                    {
                        "invocations": 5,
                        "failures": 1,
                        "avg_latency_ms": 100.0,
                        "p95_latency_ms": 180.0,
                    }
                ],
            )
        ],
        clock=clock,
    )
    overview = await overview_service.query_view(_scope(), _filters(), view="overview")
    assert overview.data == {
        "invocations": 5,
        "failures": 1,
        "avg_latency_ms": 100.0,
        "p95_latency_ms": 180.0,
    }


@pytest.mark.asyncio
async def test_agent_detail_returns_none_when_agent_not_seen() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory([_source()])
    service, _discovery, _query = _service(
        inventory=inventory, results=[_agent_rows_result()], clock=clock
    )

    detail = await service.agent_detail(_scope(), _filters(), agent_key="not-seen")

    assert detail is None


@pytest.mark.asyncio
async def test_agent_detail_narrows_to_matching_agent() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory([_source()])
    service, _discovery, _query = _service(
        inventory=inventory, results=[_agent_rows_result()], clock=clock
    )

    detail = await service.agent_detail(_scope(), _filters(), agent_key="agent-1")

    assert detail is not None
    assert len(detail.data) == 1
    assert detail.data[0].key == "agent-1"


@pytest.mark.asyncio
async def test_agent_detail_propagates_partial_failures_from_underlying_query() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory([_source("src-1"), _source("src-2")])
    results = [
        _agent_rows_result("src-1"),
        SourceResult(source_id="src-2", status="timeout", reason="deadline exceeded"),
    ]
    service, _discovery, _query = _service(
        inventory=inventory, results=results, clock=clock
    )

    detail = await service.agent_detail(_scope(), _filters(), agent_key="agent-1")

    assert detail is not None
    assert [f.source_id for f in detail.partial_failures] == ["src-2"]
    assert detail.partial_failures[0].status == "timeout"


# ---------------------------------------------------------------------------
# Raw protected content must never enter the shared cache.
# ---------------------------------------------------------------------------


def test_shared_cache_still_rejects_raw_protected_content() -> None:
    cache = ObserveCache(ttl_seconds=CACHE_TTL_SECONDS)
    with pytest.raises(SensitiveValueError):
        cache.set("trace-key", {"input_messages": ["do not cache me"]})


@pytest.mark.asyncio
async def test_query_view_result_never_carries_raw_content_fields() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory([_source()])
    service, _discovery, _query = _service(
        inventory=inventory, results=[_agent_rows_result()], clock=clock
    )

    result: ObserveResult = await service.query_view(
        _scope(), _filters(), view="agents"
    )

    for agent in result.data:
        assert not hasattr(agent, "input_messages")
        assert not hasattr(agent, "tool_content")


# ---------------------------------------------------------------------------
# Issue #444 / T014: department attribution service composition.
# ---------------------------------------------------------------------------


def _attribution_config() -> AttributionConfiguration:
    return AttributionConfiguration.model_validate(
        {
            "version": 1,
            "enabled": True,
            "deployment_namespace": "11111111-2222-4333-8444-555555555555",
            "generation": 1,
            "departments": [
                {
                    "id": "engineering",
                    "label": "Engineering",
                    "user_keys": [f"usr1.g1.{1:064x}"],
                    "group_ids": ["aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"],
                },
                {
                    "id": "finance",
                    "label": "Finance",
                    "user_keys": [f"usr1.g1.{2:064x}"],
                    "group_ids": ["bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"],
                },
            ],
        }
    )


def _principal_context() -> dict[str, Any]:
    return {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "user_id": "alice@example.test",
        "groups": [],
    }


def test_department_resolution_uses_explicit_mapping_before_principal_groups() -> None:
    resolution = resolve_department(
        user_key=f"usr1.g1.{1:064x}",
        raw_identity="alice@example.test",
        config=_attribution_config(),
        principal_user_id="alice@example.test",
        principal_user_name=None,
        principal_group_ids=["bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"],
    )

    assert (resolution.source, resolution.department_id) == (
        "explicit_user",
        "engineering",
    )


def test_department_resolution_applies_groups_only_to_exact_principal_identity() -> (
    None
):
    config = _attribution_config()
    key = f"usr1.g1.{3:064x}"

    exact = resolve_department(
        user_key=key,
        raw_identity="Alice@Example.test",
        config=config,
        principal_user_id=None,
        principal_user_name="Alice@Example.test",
        principal_group_ids=["aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"],
    )
    different_case = resolve_department(
        user_key=key,
        raw_identity="alice@example.test",
        config=config,
        principal_user_id=None,
        principal_user_name="Alice@Example.test",
        principal_group_ids=["aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"],
    )
    ambiguous = resolve_department(
        user_key=key,
        raw_identity="Alice@Example.test",
        config=config,
        principal_user_id=None,
        principal_user_name="Alice@Example.test",
        principal_group_ids=[
            "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
        ],
    )

    assert (exact.source, exact.department_id) == ("principal_group", "engineering")
    assert different_case.source == "unmapped"
    assert ambiguous.source == "ambiguous"


@pytest.mark.asyncio
async def test_query_attribution_passes_only_exact_principal_group_keys_to_query() -> (
    None
):
    config = _attribution_config()
    clock = FakeDatetimeClock(datetime(2024, 1, 2, tzinfo=timezone.utc))
    service, _discovery, query = _service(
        inventory=_inventory([_source()]),
        results=[SourceResult(source_id="src-1", status="success", tables=[])],
        clock=clock,
    )
    principal = {
        **_principal_context(),
        "user_id": "principal-object-id",
        "user_name": "Alice@Example.test",
        "groups": ["aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"],
    }

    await service.query_attribution(
        _scope(),
        AttributionQueryRequest(
            metric="usage", group_by="department", filters=_filters()
        ),
        config=config,
        principal_context=principal,
    )

    passed_config = query.attribution_configs[0]
    engineering = next(
        item for item in passed_config.departments if item.id == "engineering"
    )
    exact_key = derive_pseudonymous_user_key(
        deployment_namespace=config.deployment_namespace,
        generation=config.generation,
        tenant_id=principal["tenant_id"],
        raw_identity="Alice@Example.test",
    )
    different_case_key = derive_pseudonymous_user_key(
        deployment_namespace=config.deployment_namespace,
        generation=config.generation,
        tenant_id=principal["tenant_id"],
        raw_identity="alice@example.test",
    )
    assert exact_key in engineering.user_keys
    assert different_case_key not in engineering.user_keys


def test_department_cardinality_is_conservative_for_singletons() -> None:
    assert classify_department_cardinality(
        [{"department_id": "engineering", "member_count": 2}]
    )
    assert not classify_department_cardinality(
        [{"department_id": "engineering", "member_count": 1}]
    )
    assert not classify_department_cardinality(
        [{"department_id": None, "member_count": 1}]
    )
    assert not classify_department_cardinality(
        [{"department_id": "engineering", "member_count": -1}]
    )


def test_department_cardinality_does_not_sum_overlapping_source_counts() -> None:
    duplicate_without_identities = [
        {"department_id": "engineering", "member_count": 1},
        {"department_id": "engineering", "member_count": 1},
    ]
    assert not classify_department_cardinality(duplicate_without_identities)
    assert classify_department_cardinality(
        [{"department_id": "engineering", "member_count": 2}]
    )


def test_department_cardinality_merges_global_identities_and_principal_aliases() -> None:
    alice_object = f"usr1.g1.{'a' * 64}"
    alice_upn = f"usr1.g1.{'b' * 64}"
    bob = f"usr1.g1.{'c' * 64}"
    assert not classify_department_cardinality(
        [
            {
                "department_id": "engineering",
                "member_count": 1,
                "member_user_keys": [alice_object],
            },
            {
                "department_id": "engineering",
                "member_count": 1,
                "member_user_keys": [alice_upn],
            },
        ],
        principal_aliases=[alice_object, alice_upn],
    )
    assert classify_department_cardinality(
        [
            {
                "department_id": "engineering",
                "member_count": 1,
                "member_user_keys": [alice_object],
            },
            {
                "department_id": "engineering",
                "member_count": 1,
                "member_user_keys": [bob],
            },
        ],
        principal_aliases=[alice_object, alice_upn],
    )


@pytest.mark.asyncio
async def test_user_attribution_ranks_ties_by_key_and_folds_only_above_500() -> None:
    config = _attribution_config()
    tenant = _principal_context()["tenant_id"]
    source_rows = []
    for index in range(501):
        identity = f"user-{index:03d}@example.test"
        source_rows.append(
            {
                "row_kind": "user",
                "user_key": derive_pseudonymous_user_key(
                    deployment_namespace=config.deployment_namespace,
                    generation=config.generation,
                    tenant_id=tenant,
                    raw_identity=identity,
                ),
                "raw_identity": identity,
                "invocations": 1,
                "input_tokens": None,
                "output_tokens": None,
                "tool_invocations": None,
                "active_session_seconds": None,
            }
        )
    service, _discovery, _query = _service(
        inventory=_inventory([_source()]),
        results=[SourceResult(source_id="src-1", status="success", tables=source_rows)],
        clock=FakeDatetimeClock(datetime(2024, 1, 2, tzinfo=timezone.utc)),
    )

    result = await service.query_attribution(
        _scope(),
        AttributionQueryRequest(metric="usage", group_by="user", filters=_filters()),
        config=config,
        principal_context=_principal_context(),
        access_boundary="delegated",
    )

    assert len(result.data.rows) == 500
    assert result.data.rows[-1].kind == "other_users"
    assert result.data.rows[-1].member_count == 2
    assert result.data.summary.distinct_users == 501
    assert result.data.summary.omitted_users == 2
    assert result.data.summary.total.invocations == 501
    keys = [row.user_key for row in result.data.rows[:-1]]
    assert keys == sorted(keys)


@pytest.mark.asyncio
async def test_user_attribution_merges_overlapping_sources_before_global_limit() -> None:
    config = _attribution_config()
    tenant = _principal_context()["tenant_id"]

    def row(index: int) -> dict[str, Any]:
        identity = f"overlap-{index:03d}@example.test"
        return {
            "row_kind": "user",
            "user_key": derive_pseudonymous_user_key(
                deployment_namespace=config.deployment_namespace,
                generation=config.generation,
                tenant_id=tenant,
                raw_identity=identity,
            ),
            "raw_identity": identity,
            "invocations": 1,
        }

    first = [row(index) for index in range(250)]
    second = [row(0), *(row(index) for index in range(250, 500))]
    service, _discovery, _query = _service(
        inventory=_inventory([_source("src-1"), _source("src-2")]),
        results=[
            SourceResult(source_id="src-1", status="success", tables=first),
            SourceResult(source_id="src-2", status="success", tables=second),
        ],
        clock=FakeDatetimeClock(datetime(2024, 1, 2, tzinfo=timezone.utc)),
    )

    result = await service.query_attribution(
        _scope(),
        AttributionQueryRequest(metric="usage", group_by="user", filters=_filters()),
        config=config,
        principal_context=_principal_context(),
        access_boundary="delegated",
    )

    assert len(result.data.rows) == 500
    assert all(row.kind == "user" for row in result.data.rows)
    assert result.data.summary.distinct_users == 500
    assert result.data.summary.omitted_users == 0
    assert result.data.summary.total.invocations == 501


@pytest.mark.asyncio
async def test_selected_user_isolated_with_principal_bound_token() -> None:
    config = _attribution_config()
    principal = _principal_context()
    identities = ("alice@example.test", "bob@example.test")
    rows = []
    for identity in identities:
        key = derive_pseudonymous_user_key(
            deployment_namespace=config.deployment_namespace,
            generation=config.generation,
            tenant_id=principal["tenant_id"],
            raw_identity=identity,
        )
        rows.append(
            {
                "row_kind": "user",
                "user_key": key,
                "raw_identity": identity,
                "invocations": 3,
            }
        )
    selected = rows[1]["user_key"]
    token = issue_user_filter_token(
        selected,
        config=config,
        scope=_scope(),
        tenant_id=principal["tenant_id"],
        principal_id=principal["user_id"],
    )
    filters = _filters().model_copy(update={"user_filter_token": token})
    service, _discovery, query = _service(
        inventory=_inventory([_source()]),
        results=[SourceResult(source_id="src-1", status="success", tables=rows)],
        clock=FakeDatetimeClock(datetime(2024, 1, 2, tzinfo=timezone.utc)),
    )

    result = await service.query_attribution(
        _scope(),
        AttributionQueryRequest(metric="usage", group_by="user", filters=filters),
        config=config,
        principal_context=principal,
        access_boundary="delegated",
    )

    assert [row.user_key for row in result.data.rows] == [selected]
    assert result.data.summary.total.invocations == 3
    assert query.calls[-1][3] == selected


@pytest.mark.asyncio
async def test_query_attribution_merges_sources_reconciles_and_preserves_failures() -> (
    None
):
    clock = FakeDatetimeClock(
        datetime(2024, 1, 2, tzinfo=timezone.utc), timedelta(milliseconds=5)
    )
    inventory = _inventory(
        [
            _source("src-1"),
            _source("src-2"),
            _source("src-3", state="inaccessible", reason="denied"),
        ]
    )
    results = [
        SourceResult(
            source_id="src-1",
            status="success",
            tables=[
                AggregateDepartmentUsageRow(
                    source_id="src-1",
                    source_resource_id=_source("src-1").resource_id,
                    department_id="engineering",
                    department_label="Engineering",
                    mapping_state="mapped",
                    member_count=2,
                    usage=AttributionUsage(
                        invocations=4,
                        input_tokens=40,
                        output_tokens=None,
                        tool_invocations=1,
                        active_session_seconds=Decimal("2.5"),
                    ),
                    eligible_records=5,
                    identified_records=4,
                    mapped_records=4,
                    unattributed_records=1,
                    ambiguous_records=0,
                    returned_records=2,
                ),
                AggregateDepartmentUsageRow(
                    source_id="src-1",
                    source_resource_id=_source("src-1").resource_id,
                    department_id=None,
                    department_label=None,
                    mapping_state="unmapped",
                    member_count=0,
                    usage=AttributionUsage(
                        invocations=1,
                        input_tokens=None,
                        output_tokens=3,
                        tool_invocations=None,
                        active_session_seconds=None,
                    ),
                    eligible_records=5,
                    identified_records=4,
                    mapped_records=4,
                    unattributed_records=1,
                    ambiguous_records=0,
                    returned_records=2,
                ),
            ],
        ),
        SourceResult(
            source_id="src-2",
            status="partial",
            reason="source timed out after returning partial rows",
            tables=[
                AggregateDepartmentUsageRow(
                    source_id="src-2",
                    source_resource_id=_source("src-2").resource_id,
                    department_id="engineering",
                    department_label="Engineering",
                    mapping_state="mapped",
                    member_count=3,
                    usage=AttributionUsage(
                        invocations=2,
                        input_tokens=20,
                        output_tokens=7,
                        tool_invocations=None,
                        active_session_seconds=Decimal("1.5"),
                    ),
                    eligible_records=2,
                    identified_records=2,
                    mapped_records=2,
                    unattributed_records=0,
                    ambiguous_records=0,
                    returned_records=1,
                )
            ],
        ),
    ]
    service, _discovery, query = _service(
        inventory=inventory, results=results, clock=clock
    )

    response = await service.query_attribution(
        _scope(),
        AttributionQueryRequest(
            metric="usage", group_by="department", filters=_filters()
        ),
        config=_attribution_config(),
        principal_context=_principal_context(),
    )

    assert len(query.calls) == 1
    assert query.calls[0][1:6] == (
        "attribution",
        None,
        "11111111-1111-1111-1111-111111111111",
        _filters().start,
        _filters().end,
    )
    assert len(query.calls[0][6]) == 1
    assert len(response.data.rows) == 1
    row = response.data.rows[0]
    assert row.department_id == "engineering"
    assert row.usage.invocations == 6
    assert row.usage.input_tokens == 60
    assert row.usage.output_tokens == 7
    assert row.usage.active_session_seconds == Decimal("4.0")
    assert response.data.summary.total.invocations == 7
    assert response.data.summary.attributed.invocations == 6
    assert response.data.summary.unattributed.invocations == 1
    assert response.data.summary.total.tool_invocations == 1
    src_one_coverage = next(
        item for item in response.coverage if item.source_id == "src-1"
    )
    assert src_one_coverage.dimension == "user_attribution"
    assert src_one_coverage.state == "partial"
    assert (
        src_one_coverage.eligible_records,
        src_one_coverage.identified_records,
        src_one_coverage.mapped_records,
        src_one_coverage.unattributed_records,
        src_one_coverage.ambiguous_records,
        src_one_coverage.returned_records,
    ) == (5, 4, 4, 1, 0, 2)
    assert {failure.source_id for failure in response.partial_failures} == {
        "src-2",
        "src-3",
    }
    assert all(
        "source timed out" not in failure.reason
        and "denied" not in failure.reason
        for failure in response.partial_failures
    )
    assert {item.source_id for item in response.coverage} == {
        "src-1",
        "src-2",
        "src-3",
    }
    inaccessible = next(item for item in response.coverage if item.source_id == "src-3")
    assert inaccessible.state == "inaccessible"
    assert inaccessible.eligible_records is None


def test_merge_user_attribution_coverage_keeps_usage_and_cost_independent() -> None:
    refreshed_at = datetime(2024, 1, 2, tzinfo=timezone.utc)

    def coverage(metric: str, *, component_id: str | None = None):
        return UserAttributionCoverage(
            source_id="src-1",
            dimension="user_attribution",
            state="available",
            reason="covered",
            next_action="none",
            refreshed_at=refreshed_at,
            metric=metric,
            attribution_level="department",
            component_id=component_id,
            eligible_records=1,
            identified_records=1,
            mapped_records=1,
            unattributed_records=0,
            ambiguous_records=0,
            returned_records=1,
        )

    merged = _merge_user_attribution_coverage(
        [coverage("usage")],
        [coverage("cost", component_id="model.ptu")],
    )

    assert [(item.source_id, item.metric, item.component_id) for item in merged] == [
        ("src-1", "usage", None),
        ("src-1", "cost", "model.ptu"),
    ]


@pytest.mark.asyncio
async def test_query_attribution_missing_counters_is_error_not_success_zero_grouping() -> (
    None
):
    clock = FakeDatetimeClock(datetime(2024, 1, 2, tzinfo=timezone.utc))
    service, _discovery, _query = _service(
        inventory=_inventory([_source()]),
        results=[SourceResult(source_id="src-1", status="success", tables=[])],
        clock=clock,
    )

    response = await service.query_attribution(
        _scope(),
        AttributionQueryRequest(
            metric="usage", group_by="department", filters=_filters()
        ),
        config=_attribution_config(),
        principal_context=_principal_context(),
    )

    assert response.data.rows == []
    assert response.coverage[0].state == "error"
    assert response.coverage[0].eligible_records is None
    assert "did not return attribution counters" in response.coverage[0].reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("counters", "expected_state"),
    [
        ((0, 0, 0, 0, 0, 0), "no_data"),
        ((4, 0, 0, 4, 0, 0), "not_reported"),
        ((4, 4, 0, 4, 4, 1), "ambiguous"),
        ((4, 3, 2, 2, 0, 2), "partial"),
        ((4, 4, 4, 0, 0, 1), "available"),
    ],
)
async def test_query_attribution_classifies_reported_coverage_counters(
    counters: tuple[int, int, int, int, int, int],
    expected_state: str,
) -> None:
    fields = (
        "eligible_records",
        "identified_records",
        "mapped_records",
        "unattributed_records",
        "ambiguous_records",
        "returned_records",
    )
    metadata = dict(zip(fields, counters, strict=True))
    metadata.update(
        {
            "department_id": None,
            "department_label": None,
            "mapping_state": "unmapped",
            "member_count": 0,
            "invocations": 0,
            "metadata_only": True,
        }
    )
    clock = FakeDatetimeClock(datetime(2024, 1, 2, tzinfo=timezone.utc))
    service, _discovery, _query = _service(
        inventory=_inventory([_source()]),
        results=[SourceResult(source_id="src-1", status="success", tables=[metadata])],
        clock=clock,
    )

    response = await service.query_attribution(
        _scope(),
        AttributionQueryRequest(
            metric="usage", group_by="department", filters=_filters()
        ),
        config=_attribution_config(),
        principal_context=_principal_context(),
    )

    assert response.coverage[0].state == expected_state
    assert tuple(getattr(response.coverage[0], field) for field in fields) == counters


@pytest.mark.asyncio
async def test_query_attribution_resolves_department_filter_before_one_bounded_query() -> (
    None
):
    from agentops.core.attribution import issue_department_filter_token

    config = _attribution_config()
    token = issue_department_filter_token("finance", config=config, scope=_scope())
    clock = FakeDatetimeClock(datetime(2024, 1, 2, tzinfo=timezone.utc))
    service, _discovery, query = _service(
        inventory=_inventory([_source()]),
        results=[SourceResult(source_id="src-1", status="success", tables=[])],
        clock=clock,
    )

    await service.query_attribution(
        _scope(),
        AttributionQueryRequest(
            metric="usage",
            group_by="department",
            filters=_filters(department_filter_token=token),
        ),
        config=config,
        principal_context=_principal_context(),
    )

    assert len(query.calls) == 1
    assert query.calls[0][2] == "finance"


@pytest.mark.asyncio
async def test_query_attribution_rejects_aggregate_singleton_result() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 2, tzinfo=timezone.utc))
    service, _discovery, _query = _service(
        inventory=_inventory([_source()]),
        results=[
            SourceResult(
                source_id="src-1",
                status="success",
                tables=[
                    {
                        "department_id": "engineering",
                        "department_label": "Engineering",
                        "mapping_state": "mapped",
                        "member_count": 1,
                        "invocations": 1,
                    }
                ],
            )
        ],
        clock=clock,
    )

    with pytest.raises(SingletonAttributionError):
        await service.query_attribution(
            _scope(),
            AttributionQueryRequest(
                metric="usage", group_by="department", filters=_filters()
            ),
            config=_attribution_config(),
            principal_context=_principal_context(),
        )


@pytest.mark.asyncio
async def test_query_attribution_counts_principal_alias_once_across_sources() -> None:
    rows = []
    for source_id in ("src-1", "src-2"):
        rows.append(
            SourceResult(
                source_id=source_id,
                status="success",
                tables=[
                    {
                        "department_id": "engineering",
                        "department_label": "Engineering",
                        "mapping_state": "mapped",
                        "member_count": 1,
                        "principal_member_present": 1,
                        "invocations": 1,
                    }
                ],
            )
        )
    service, _discovery, _query = _service(
        inventory=_inventory([_source("src-1"), _source("src-2")]),
        results=rows,
        clock=FakeDatetimeClock(datetime(2024, 1, 2, tzinfo=timezone.utc)),
    )

    with pytest.raises(SingletonAttributionError):
        await service.query_attribution(
            _scope(),
            AttributionQueryRequest(
                metric="usage", group_by="department", filters=_filters()
            ),
            config=_attribution_config(),
            principal_context=_principal_context(),
        )


@pytest.mark.asyncio
async def test_group_claim_overage_reports_fixed_partial_coverage() -> None:
    service, _discovery, query = _service(
        inventory=_inventory([_source()]),
        results=[
            SourceResult(
                source_id="src-1",
                status="success",
                tables=[
                    {
                        "department_id": "engineering",
                        "department_label": "Engineering",
                        "mapping_state": "mapped",
                        "member_count": 2,
                        "invocations": 2,
                        "eligible_records": 2,
                        "identified_records": 2,
                        "mapped_records": 2,
                        "unattributed_records": 0,
                        "ambiguous_records": 0,
                        "returned_records": 1,
                    }
                ],
            )
        ],
        clock=FakeDatetimeClock(datetime(2024, 1, 2, tzinfo=timezone.utc)),
    )
    principal = {**_principal_context(), "groups_overage": True}

    result = await service.query_attribution(
        _scope(),
        AttributionQueryRequest(
            metric="usage", group_by="department", filters=_filters()
        ),
        config=_attribution_config(),
        principal_context=principal,
    )

    assert result.coverage[0].state == "partial"
    assert "group overage" in result.coverage[0].reason
    assert "explicit user mappings" in result.coverage[0].next_action
    assert query.calls[0][6]


@pytest.mark.asyncio
async def test_query_attribution_allows_delegated_singleton_and_bypasses_cache() -> (
    None
):
    clock = FakeDatetimeClock(datetime(2024, 1, 2, tzinfo=timezone.utc))
    service, _discovery, query = _service(
        inventory=_inventory([_source()]),
        results=[
            SourceResult(
                source_id="src-1",
                status="success",
                tables=[
                    {
                        "department_id": "engineering",
                        "department_label": "Engineering",
                        "mapping_state": "mapped",
                        "member_count": 1,
                        "invocations": 1,
                    }
                ],
            )
        ],
        clock=clock,
    )

    response = await service.query_attribution(
        _scope(),
        AttributionQueryRequest(
            metric="usage", group_by="department", filters=_filters()
        ),
        config=_attribution_config(),
        principal_context={"tenant_id": _principal_context()["tenant_id"]},
        access_boundary="delegated",
    )

    assert len(query.calls) == 1
    assert response.data.access_boundary == "delegated"
    assert response.data.rows[0].member_count == 1
    assert response.cache_status == "bypass"
    assert response.diagnostics.cache_status == "bypass"


def _attribution_cost_period(*, billed_total: str = "100.00") -> CostPeriod:
    source_resource_id = _source().foundry_resource_id or _source().resource_id
    return _cost_period(
        components=[
            {
                "id": "attributed-model",
                "type": "standard_model",
                "billing_boundary": {
                    "kind": "resource",
                    "value": source_resource_id,
                },
                "billed_source": "Declared attributed model total",
                "billed_total": billed_total,
                "currency": "USD",
                "currency_minor_units": 2,
                "allocation_model": "metered",
                "allocation_key": "total_tokens",
                "usage_match": {
                    "source_resource_ids": [source_resource_id],
                    "deployments": ["gpt-prod"],
                },
            }
        ]
    )


@pytest.mark.asyncio
async def test_cost_attribution_matches_actual_telemetry_dimensions() -> None:
    config = _attribution_config()
    tenant = _principal_context()["tenant_id"]

    def row(identity: str, deployment: str, tokens: int) -> dict[str, Any]:
        return {
            "row_kind": "user",
            "user_key": derive_pseudonymous_user_key(
                deployment_namespace=config.deployment_namespace,
                generation=config.generation,
                tenant_id=tenant,
                raw_identity=identity,
            ),
            "raw_identity": identity,
            "source_resource_id": _source().foundry_resource_id
            or _source().resource_id,
            "deployment": deployment,
            "invocations": 1,
            "input_tokens": tokens,
            "output_tokens": 0,
        }

    period = _attribution_cost_period()
    service, _discovery, _query = _service(
        inventory=_inventory([_source()]),
        results=[
            SourceResult(
                source_id="src-1",
                status="success",
                tables=[
                    row("matched@example.test", "gpt-prod", 10),
                    row("different@example.test", "gpt-dev", 90),
                ],
            )
        ],
        clock=FakeDatetimeClock(datetime(2024, 1, 2, tzinfo=timezone.utc)),
    )

    result = await service.query_attribution(
        _scope(),
        AttributionQueryRequest(
            metric="cost",
            group_by="user",
            filters=_filters(
                cost_period_id=period.id,
                cost_component_id="attributed-model",
            ),
        ),
        config=config,
        cost_model=_cost_model(period),
        principal_context=_principal_context(),
        access_boundary="delegated",
    )

    assert [row.raw_identity for row in result.data.rows] == ["matched@example.test"]
    assert result.data.rows[0].cost.amount == Decimal("100.00")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_count", "expected_rows", "omitted_users"),
    [(499, 499, 0), (500, 500, 0), (501, 500, 2)],
)
async def test_user_cost_allocates_full_population_before_ranking_and_folding(
    user_count: int,
    expected_rows: int,
    omitted_users: int,
) -> None:
    config = _attribution_config()
    tenant = _principal_context()["tenant_id"]
    source_resource_id = _source().foundry_resource_id or _source().resource_id
    source_rows = []
    keys_by_identity: dict[str, str] = {}
    for index in range(user_count):
        identity = f"cost-user-{index:03d}@example.test"
        key = derive_pseudonymous_user_key(
            deployment_namespace=config.deployment_namespace,
            generation=config.generation,
            tenant_id=tenant,
            raw_identity=identity,
        )
        keys_by_identity[identity] = key
        source_rows.append(
            {
                "row_kind": "user",
                "user_key": key,
                "raw_identity": identity,
                "source_resource_id": source_resource_id,
                "deployment": "gpt-prod",
                "invocations": 10_000 if index == 0 else 1,
                "input_tokens": index + 1,
                "output_tokens": 0,
            }
        )
    period = _attribution_cost_period(billed_total="1000000.00")
    service, _discovery, _query = _service(
        inventory=_inventory([_source()]),
        results=[
            SourceResult(source_id="src-1", status="success", tables=source_rows)
        ],
        clock=FakeDatetimeClock(datetime(2024, 1, 2, tzinfo=timezone.utc)),
    )

    result = await service.query_attribution(
        _scope(),
        AttributionQueryRequest(
            metric="cost",
            group_by="user",
            filters=_filters(
                cost_period_id=period.id,
                cost_component_id="attributed-model",
            ),
        ),
        config=config,
        cost_model=_cost_model(period),
        principal_context=_principal_context(),
        access_boundary="delegated",
    )

    assert len(result.data.rows) == expected_rows
    if omitted_users:
        assert result.data.rows[-1].kind == "other_users"
        assert result.data.rows[-1].member_count == omitted_users
    else:
        assert all(row.kind == "user" for row in result.data.rows)
    visible_keys = {
        row.user_key for row in result.data.rows if row.kind == "user"
    }
    if user_count == 501:
        assert keys_by_identity["cost-user-500@example.test"] in visible_keys
        assert keys_by_identity["cost-user-000@example.test"] not in visible_keys
    assert result.data.summary.distinct_users == user_count
    assert result.data.summary.omitted_users == omitted_users
    assert (
        sum((row.cost.amount for row in result.data.rows), Decimal(0))
        == result.data.summary.attributed_amount
    )


@pytest.mark.asyncio
async def test_user_cost_filter_is_applied_after_full_population_allocation() -> None:
    config = _attribution_config()
    principal = _principal_context()
    tenant = principal["tenant_id"]
    source_resource_id = _source().foundry_resource_id or _source().resource_id
    rows = []
    selected_key = ""
    for index in range(501):
        identity = f"filtered-cost-user-{index:03d}@example.test"
        key = derive_pseudonymous_user_key(
            deployment_namespace=config.deployment_namespace,
            generation=config.generation,
            tenant_id=tenant,
            raw_identity=identity,
        )
        if index == 0:
            selected_key = key
        rows.append(
            {
                "row_kind": "user",
                "user_key": key,
                "raw_identity": identity,
                "source_resource_id": source_resource_id,
                "deployment": "gpt-prod",
                "invocations": 1,
                "input_tokens": index + 1,
                "output_tokens": 0,
            }
        )
    token = issue_user_filter_token(
        selected_key,
        config=config,
        scope=_scope(),
        tenant_id=tenant,
        principal_id=principal["user_id"],
    )
    period = _attribution_cost_period(billed_total="1000000.00")
    service, _discovery, _query = _service(
        inventory=_inventory([_source()]),
        results=[SourceResult(source_id="src-1", status="success", tables=rows)],
        clock=FakeDatetimeClock(datetime(2024, 1, 2, tzinfo=timezone.utc)),
    )

    result = await service.query_attribution(
        _scope(),
        AttributionQueryRequest(
            metric="cost",
            group_by="user",
            filters=_filters(
                cost_period_id=period.id,
                cost_component_id="attributed-model",
                user_filter_token=token,
            ),
        ),
        config=config,
        cost_model=_cost_model(period),
        principal_context=principal,
        access_boundary="delegated",
    )

    assert [row.user_key for row in result.data.rows] == [selected_key]
    assert result.data.rows[0].cost.usage_denominator == Decimal("125751")
