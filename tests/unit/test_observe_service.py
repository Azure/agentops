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
from agentops.agent.observe.queries import SourceResult
from agentops.agent.observe.service import (
    CACHE_TTL_SECONDS,
    ObserveResult,
    ObserveService,
    PartialFailure,
    agent_source_kind,
    classify_discovery_coverage,
    classify_protected_content_coverage,
    classify_query_coverage,
    normalize_agent_row,
    normalize_model_row,
    safe_failure_reason,
    token_reporting_state,
)
from agentops.core.observe import (
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


def _inventory(sources: Sequence[TelemetrySource]) -> ResourceInventory:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return ResourceInventory(
        scope=_scope(),
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


def test_agent_source_kind_prefers_foundry_id_over_name() -> None:
    assert agent_source_kind(agent_id="agent-1", agent_name="ignored") == "foundry"
    assert agent_source_kind(agent_id=None, agent_name="custom-bot") == "external"
    assert agent_source_kind(agent_id=None, agent_name=None) == "unknown"


def test_token_reporting_state_distinguishes_absence_from_zero() -> None:
    assert token_reporting_state(input_tokens=0, output_tokens=0) == "reported"
    assert token_reporting_state(input_tokens=None, output_tokens=None) == "not_reported"
    assert token_reporting_state(input_tokens=None, output_tokens=5) == "reported"


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
    assert agent.source_kind == "foundry"
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
    assert agent.source_kind == "external"


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
    assert agent.source_kind == "foundry"
    assert agent.project_resource_id == _PROJECT_ID
    assert query.calls[0][1] == "agents"
    dimensions = {c.dimension for c in result.coverage}
    assert "resource_access" in dimensions
    assert "telemetry_connection" in dimensions
    assert "agent_attribution" in dimensions
    assert "token_usage" in dimensions


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
