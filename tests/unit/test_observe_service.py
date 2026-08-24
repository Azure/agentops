"""Tests for Observe orchestration, normalization, and deterministic coverage.

Covers T038/T045/T046/T056/T059/T060: normalized ObservedAgent/ModelUsage
rows, source attribution, token-reporting semantics, two-minute identity/
scope/filter caching with refresh bypass, agent detail lookup, and
deterministic coverage classification (including protected_or_unavailable
zero-row behavior and safe, actionable failure reasons). All Azure access is
faked; nothing here imports azure.monitor.query or azure.mgmt.resourcegraph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

import pytest

from agentops.agent.observe.cache import ObserveCache, SensitiveValueError
from agentops.agent.observe.queries import MAX_ROWS_PER_QUERY, SourceResult
from agentops.agent.observe.service import (
    CACHE_TTL_SECONDS,
    ObserveResult,
    ObserveService,
    PartialFailure,
    TokenClassInventory,
    _result_bounds,
    _source_attribution_coverage,
    classify_runtime,
    classify_discovery_coverage,
    classify_protected_content_coverage,
    classify_query_coverage,
    normalize_agent_row,
    normalize_model_row,
    normalize_run_row,
    normalize_tool_row,
    safe_failure_reason,
    token_class_inventory,
    token_reporting_state,
)
from agentops.core.observe import (
    ModelUsage,
    ObserveFilterState,
    ObserveScope,
    ResourceInventory,
    TelemetrySource,
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
    calls: list[tuple[Any, ...]] = field(default_factory=list, init=False)

    async def query(
        self, sources: Sequence[TelemetrySource], filters: ObserveFilterState, *, view: str
    ) -> list[SourceResult]:
        self.calls.append((tuple(source.source_id for source in sources), view))
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
) -> tuple[ObserveService, FakeDiscoveryClient, FakeQueryClient]:
    discovery = FakeDiscoveryClient(inventory)
    query = FakeQueryClient(results)
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
        ("hosted-id", None, None, [{"id": "hosted-id", "kind": "hosted"}], "foundry_hosted"),
        ("prompt-id", None, None, [{"id": "prompt-id", "kind": "prompt"}], "foundry_prompt"),
        (None, "registered", None, [{"name": "registered", "kind": "custom"}], "external_registered"),
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


def test_classify_runtime_does_not_preserve_retired_foundry_label_without_evidence() -> None:
    assert classify_runtime(agent_id="previously-foundry", agent_name=None) == "unknown"


def test_token_reporting_state_distinguishes_absence_from_zero() -> None:
    assert token_reporting_state(input_tokens=0, output_tokens=0) == "reported"
    assert token_reporting_state(input_tokens=None, output_tokens=None) == "not_reported"
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


def test_normalize_model_row_caps_sorted_additional_classes_and_marks_truncation() -> None:
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
            "invocations": 4,
            "failures": 1,
            "p95_latency_ms": None,
            "last_seen": datetime(2024, 1, 1, 12, tzinfo=timezone.utc),
        },
        source=source,
    )

    assert tool.source_id == source.source_id
    assert tool.tool_name == "search"
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
        tables=[{"total_in_scope": 600}] * MAX_ROWS_PER_QUERY,
    )
    second = SourceResult(
        source_id="src-b",
        status="success",
        tables=[{"total_in_scope": 10}] * 10,
    )

    bounds = _result_bounds([first, second], rows_shown=MAX_ROWS_PER_QUERY)

    assert bounds.rows_total_in_scope == 610
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


def test_token_class_inventory_skips_tokenless_rows_and_reports_missing_classes() -> None:
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


def test_classify_query_coverage_partial_inventory_names_present_and_missing_classes() -> None:
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
async def test_query_view_tools_normalizes_rows_and_explains_unattributed_activity() -> None:
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
    service, _discovery, query = _service(inventory=inventory, results=[result], clock=clock)

    observed = await service.query_view(_scope(), _filters(), view="tools")

    assert query.calls[0][1] == "tools"
    assert [(tool.tool_name, tool.source_id) for tool in observed.data] == [("search", "src-1")]
    assert observed.bounds is not None
    assert observed.bounds.rows_shown == 1
    assert observed.bounds.rows_total_in_scope is None
    assert observed.bounds.truncated is False
    coverage = [item for item in observed.coverage if item.dimension == "tool_attribution"]
    assert any(item.state == "error" and item.reason and item.next_action for item in coverage)


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
    coverage = [item for item in observed.coverage if item.dimension == "tool_attribution"]
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
    run_coverage = [item for item in observed.coverage if item.dimension == "run_correlation"]
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
    service, _discovery, _query = _service(inventory=inventory, results=[result], clock=clock)

    first = await service.query_view(_scope(), _filters(), view="agents")
    second = await service.query_view(_scope(), _filters(), view="agents")

    assert first.bounds is not None
    assert second.bounds == first.bounds
    assert second.bounds.rows_total_in_scope == 1


@pytest.mark.asyncio
async def test_models_view_emits_token_usage_coverage_without_changing_agents_entry() -> None:
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
        item.dimension: item for item in models_result.coverage if item.source_id == "src-1"
    }
    assert models_coverage["token_usage"].state == "partial"

    agents_service, _, _ = _service(
        inventory=inventory, results=[_agent_rows_result()], clock=clock
    )
    agents_result = await agents_service.query_view(_scope(), _filters(), view="agents")
    agents_coverage = {
        item.dimension: item for item in agents_result.coverage if item.source_id == "src-1"
    }
    assert agents_coverage["token_usage"].state == "available"


@pytest.mark.asyncio
async def test_query_view_caches_for_two_minutes_and_serves_hits_without_requerying() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc), step=timedelta(seconds=1))
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
async def test_query_view_refresh_bypasses_cache_and_requeries() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc), step=timedelta(seconds=1))
    inventory = _inventory([_source()])
    service, discovery, query = _service(
        inventory=inventory, results=[_agent_rows_result()], clock=clock
    )

    await service.query_view(_scope(), _filters(), view="agents")
    refreshed = await service.query_view(_scope(), _filters(), view="agents", refresh=True)

    assert refreshed.cache_status == "bypass"
    assert len(query.calls) == 2
    assert discovery.calls == 2


@pytest.mark.asyncio
async def test_query_view_expires_after_ttl() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    cache = ObserveCache(ttl_seconds=CACHE_TTL_SECONDS, clock=lambda: cache_clock.now)
    cache_clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))  # placeholder
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
    await service.query_view(_scope(), _filters(), view="agents")

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
        [_source("src-available", state="available"), _source("src-blocked", state="inaccessible")]
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
    service, _discovery, _query = _service(inventory=inventory, results=[malformed], clock=clock)

    result = await service.query_view(_scope(), _filters(), view="agents")

    assert result.data == []
    error_entries = [c for c in result.coverage if c.state == "error"]
    assert any(c.dimension == "agent_attribution" for c in error_entries)


# ---------------------------------------------------------------------------
# T061: every query/agent_detail response carries safe, actionable
# partial_failures alongside diagnostics/source counts/coverage/refreshed_at.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_view_partial_failures_is_empty_when_every_source_succeeds() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory([_source()])
    service, _discovery, _query = _service(
        inventory=inventory, results=[_agent_rows_result()], clock=clock
    )

    result = await service.query_view(_scope(), _filters(), view="agents")

    assert result.partial_failures == []


@pytest.mark.asyncio
async def test_query_view_partial_failures_summarizes_non_success_sources_safely() -> None:
    clock = FakeDatetimeClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
    inventory = _inventory(
        [_source("src-timeout"), _source("src-throttled"), _source("src-error"), _source("src-ok")]
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
    service, _discovery, _query = _service(inventory=inventory, results=results, clock=clock)

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
    service, _discovery, query = _service(inventory=inventory, results=results, clock=clock)

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
                tables=[{"invocations": 5, "failures": 1}],
            )
        ],
        clock=clock,
    )
    overview = await overview_service.query_view(_scope(), _filters(), view="overview")
    assert overview.data == {"invocations": 5, "failures": 1}


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
    service, _discovery, _query = _service(inventory=inventory, results=results, clock=clock)

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

    result: ObserveResult = await service.query_view(_scope(), _filters(), view="agents")

    for agent in result.data:
        assert not hasattr(agent, "input_messages")
        assert not hasattr(agent, "tool_content")
