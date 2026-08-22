"""Tests for the route-facing Observe facade/factory production composition.

Every Azure-touching collaborator is injected via the constructor/factory
seams ``facade.py`` exposes for exactly this purpose (``discovery_client``,
``query_client``, ``cache``, ``credential_factory``, ``obo_factory``), so
these tests never need real Azure SDK packages or ``sys.modules`` faking.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

import pytest

from agentops.agent.observe import facade as facade_module
from agentops.agent.observe.cache import ObserveCache
from agentops.agent.observe.facade import (
    MAX_TREND_POINTS,
    ObserveFacade,
    _build_trend_series,
    create_observe_facade,
)
from agentops.agent.observe.queries import SourceResult
from agentops.core.observe import ObserveScope, ResourceInventory, TelemetrySource

_PROJECT_ID = (
    "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg1"
    "/providers/Microsoft.CognitiveServices/accounts/acct1/projects/proj1"
)
_FOUNDRY_ID = (
    "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg1"
    "/providers/Microsoft.CognitiveServices/accounts/acct1"
)
_WORKSPACE_RESOURCE_ID = (
    "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg1"
    "/providers/Microsoft.OperationalInsights/workspaces/law1"
)
_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _scope() -> ObserveScope:
    return ObserveScope(mode="projects", project_resource_ids=[_PROJECT_ID])


def _source(**overrides: Any) -> TelemetrySource:
    defaults: dict[str, Any] = dict(
        source_id="source-1",
        resource_id=_WORKSPACE_RESOURCE_ID,
        workspace_id="workspace-guid-1",
        foundry_resource_id=_FOUNDRY_ID,
        project_resource_ids=[_PROJECT_ID],
        state="available",
    )
    defaults.update(overrides)
    return TelemetrySource(**defaults)


def _inventory(sources: Sequence[TelemetrySource]) -> ResourceInventory:
    return ResourceInventory(
        scope=_scope(),
        telemetry_sources=list(sources),
        discovered_at=_NOW,
        expires_at=_NOW + timedelta(hours=1),
    )


class FakeDiscoveryClient:
    def __init__(self, inventory: ResourceInventory) -> None:
        self.inventory = inventory
        self.calls = 0

    async def discover(self, scope: ObserveScope) -> ResourceInventory:
        self.calls += 1
        return self.inventory


class FakeQueryClient:
    def __init__(
        self,
        *,
        rows: Sequence[Mapping[str, Any]] = (),
        detail_results: Sequence[SourceResult] | None = None,
    ) -> None:
        self.rows = list(rows)
        self.detail_results = detail_results
        self.query_calls: list[str] = []
        self.detail_calls = 0
        self.closed = False

    async def query(self, sources, filters, *, view):
        self.query_calls.append(view)
        return [
            SourceResult(source_id=source.source_id, status="success", tables=self.rows, duration_ms=1)
            for source in sources
        ]

    async def query_agent_detail(self, sources, filters, *, agent_key):
        self.detail_calls += 1
        if self.detail_results is not None:
            return list(self.detail_results)
        return [
            SourceResult(source_id=source.source_id, status="success", tables=[], duration_ms=1)
            for source in sources
        ]

    async def aclose(self) -> None:
        self.closed = True


class FakeCredential:
    def get_token(self, *scopes: str, **kwargs: object) -> object:
        return object()


class FakeLogsQueryAdapter:
    """Fakes ``adapters._LogsQueryAdapter`` for the ``trace_content`` path."""

    instances: list["FakeLogsQueryAdapter"] = []

    def __init__(self, *, credential: Any) -> None:
        self.credential = credential
        self.closed = False
        self.batches: list[Sequence[Any]] = []
        FakeLogsQueryAdapter.instances.append(self)

    async def query_batch(self, requests):
        self.batches.append(requests)
        return [
            _FakeBatchItem(tables=[{"TraceId": r.query and "trace-1", "SpanId": None,
                                     "EventName": "gen_ai.user.message", "Content": "hello"}])
            for r in requests
        ]

    async def aclose(self) -> None:
        self.closed = True


class _FakeBatchItem:
    def __init__(self, *, tables):
        self.error = None
        self.partial_error = None
        self.tables = tables
        self.status = "Success"


def _agent_row(**overrides: Any) -> dict[str, Any]:
    row = dict(
        agent_key="agent-1",
        agent_id="agent-1",
        agent_name="Agent One",
        model="gpt-4o",
        last_seen=_NOW,
        invocations=10,
        failures=1,
        p95_latency_ms=120.0,
        input_tokens=100,
        output_tokens=50,
    )
    row.update(overrides)
    return row


def _make_facade(
    *,
    discovery_client: Any | None = None,
    query_client: Any | None = None,
    cache: ObserveCache | None = None,
    obo_factory: Any | None = None,
) -> ObserveFacade:
    return ObserveFacade(
        scope=_scope(),
        tenant_id="tenant-1",
        application_client_id="app-client-1",
        uami_client_id="uami-client-1",
        discovery_client=discovery_client or FakeDiscoveryClient(_inventory([_source()])),
        query_client=query_client or FakeQueryClient(rows=[_agent_row()]),
        cache=cache or ObserveCache(ttl_seconds=120.0),
        clock=lambda: _NOW,
        monotonic_clock=lambda: 0.0,
        credential_factory=lambda *, client_id: FakeCredential(),
        obo_factory=obo_factory or (lambda **kwargs: FakeCredential()),
    )


# ---------------------------------------------------------------------------
# create_observe_facade: env-var wiring + missing-config errors
# ---------------------------------------------------------------------------


def test_create_observe_facade_reads_canonical_env_vars() -> None:
    env = {
        "AGENTOPS_TENANT_ID": "tenant-env",
        "AGENTOPS_APPLICATION_CLIENT_ID": "app-env",
        "AGENTOPS_UAMI_CLIENT_ID": "uami-env",
    }
    fac = create_observe_facade(
        scope=_scope(),
        env=env,
        discovery_client=FakeDiscoveryClient(_inventory([_source()])),
        query_client=FakeQueryClient(),
        credential_factory=lambda *, client_id: FakeCredential(),
    )
    assert fac._tenant_id == "tenant-env"
    assert fac._application_client_id == "app-env"
    assert fac._uami_client_id == "uami-env"


def test_create_observe_facade_explicit_args_win_over_env() -> None:
    env = {
        "AGENTOPS_TENANT_ID": "tenant-env",
        "AGENTOPS_APPLICATION_CLIENT_ID": "app-env",
        "AGENTOPS_UAMI_CLIENT_ID": "uami-env",
    }
    fac = create_observe_facade(
        scope=_scope(),
        tenant_id="tenant-explicit",
        env=env,
        discovery_client=FakeDiscoveryClient(_inventory([_source()])),
        query_client=FakeQueryClient(),
        credential_factory=lambda *, client_id: FakeCredential(),
    )
    assert fac._tenant_id == "tenant-explicit"
    assert fac._application_client_id == "app-env"


def test_create_observe_facade_raises_actionable_error_when_missing() -> None:
    with pytest.raises(ValueError) as excinfo:
        create_observe_facade(scope=_scope(), env={})
    message = str(excinfo.value)
    assert "AGENTOPS_TENANT_ID" in message
    assert "AGENTOPS_APPLICATION_CLIENT_ID" in message
    assert "AGENTOPS_UAMI_CLIENT_ID" in message


def test_create_observe_facade_accepts_scope_mapping() -> None:
    fac = create_observe_facade(
        scope={"mode": "projects", "project_resource_ids": [_PROJECT_ID]},
        tenant_id="t",
        application_client_id="a",
        uami_client_id="u",
        discovery_client=FakeDiscoveryClient(_inventory([_source()])),
        query_client=FakeQueryClient(),
        credential_factory=lambda *, client_id: FakeCredential(),
    )
    assert isinstance(fac._scope, ObserveScope)
    assert fac._scope.project_resource_ids == [_PROJECT_ID.lower()]


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_returns_json_safe_inventory_dict() -> None:
    fac = _make_facade()
    result = await fac.discover(refresh=False, user_context={})
    assert result["telemetry_sources"][0]["source_id"] == "source-1"
    assert isinstance(result["discovered_at"], str)


# ---------------------------------------------------------------------------
# query: native views + coverage bridging + dict-filters conversion
# ---------------------------------------------------------------------------


def _filters() -> dict[str, Any]:
    return {"start": (_NOW - timedelta(hours=1)).isoformat(), "end": _NOW.isoformat()}


@pytest.mark.asyncio
async def test_query_overview_view_serializes_observe_result() -> None:
    fac = _make_facade()
    result = await fac.query(view="overview", filters=_filters(), refresh=False, user_context={})
    assert result["view"] == "overview"
    assert result["cache_status"] == "miss"
    assert "diagnostics" in result and "coverage" in result


@pytest.mark.asyncio
async def test_query_agents_view_normalizes_rows() -> None:
    fac = _make_facade(query_client=FakeQueryClient(rows=[_agent_row()]))
    result = await fac.query(view="agents", filters=_filters(), refresh=False, user_context={})
    assert result["data"][0]["key"] == "agent-1"


@pytest.mark.asyncio
async def test_query_rejects_unknown_view() -> None:
    fac = _make_facade()
    with pytest.raises(ValueError):
        await fac.query(view="bogus", filters=_filters(), user_context={})


@pytest.mark.asyncio
async def test_query_coverage_view_bridges_to_overview_and_clears_data() -> None:
    query_client = FakeQueryClient(rows=[_agent_row()])
    fac = _make_facade(query_client=query_client)
    result = await fac.query(view="coverage", filters=_filters(), refresh=False, user_context={})
    assert result["view"] == "coverage"
    assert result["data"] == []
    assert len(result["coverage"]) > 0
    # the coverage view is bridged through the native "overview" builder
    assert query_client.query_calls == ["overview"]


@pytest.mark.asyncio
async def test_query_accepts_plain_dict_filters() -> None:
    fac = _make_facade()
    # filters is a plain dict (as delivered by cockpit.py's _service_call),
    # not an ObserveFilterState instance -- must be re-validated internally.
    result = await fac.query(
        view="overview",
        filters={"start": _filters()["start"], "end": _filters()["end"], "agent_id": "agent-1"},
        user_context={},
    )
    assert result["view"] == "overview"


# ---------------------------------------------------------------------------
# T061: every serialized query()/agent_detail() response carries a safe,
# actionable "partial_failures" list alongside diagnostics/coverage/
# refreshed_at, even when every source succeeds (empty list).
# ---------------------------------------------------------------------------


class FakeMixedQueryClient:
    """Fakes ``query_client`` with one healthy source and one failing source."""

    def __init__(self, *, rows: Sequence[Mapping[str, Any]] = ()) -> None:
        self.rows = list(rows)

    async def query(self, sources, filters, *, view):
        results = []
        for index, source in enumerate(sources):
            if index == 0:
                results.append(
                    SourceResult(
                        source_id=source.source_id,
                        status="success",
                        tables=self.rows,
                        duration_ms=1,
                    )
                )
            else:
                results.append(
                    SourceResult(
                        source_id=source.source_id,
                        status="timeout",
                        reason="Deadline exceeded after 30000ms",
                        duration_ms=1,
                    )
                )
        return results

    async def query_agent_detail(self, sources, filters, *, agent_key):
        return await self.query(sources, filters, view="agent-detail")

    async def aclose(self) -> None:
        pass


def _two_source_inventory() -> ResourceInventory:
    return _inventory([_source(source_id="source-1"), _source(source_id="source-2")])


@pytest.mark.asyncio
async def test_query_response_includes_empty_partial_failures_when_all_sources_succeed() -> None:
    fac = _make_facade(query_client=FakeQueryClient(rows=[_agent_row()]))
    result = await fac.query(view="overview", filters=_filters(), refresh=False, user_context={})
    assert result["partial_failures"] == []


@pytest.mark.asyncio
async def test_query_response_includes_populated_partial_failures_for_failing_source() -> None:
    fac = _make_facade(
        discovery_client=FakeDiscoveryClient(_two_source_inventory()),
        query_client=FakeMixedQueryClient(rows=[_agent_row()]),
    )
    result = await fac.query(view="agents", filters=_filters(), refresh=False, user_context={})

    assert "partial_failures" in result
    failures = result["partial_failures"]
    assert isinstance(failures, list) and len(failures) == 1
    failure = failures[0]
    assert set(failure) == {"source_id", "status", "reason", "next_action"}
    assert failure["source_id"] == "source-2"
    assert failure["status"] == "timeout"
    assert failure["reason"]
    assert failure["next_action"]
    # every other required response key is still present alongside it
    assert {"diagnostics", "coverage", "refreshed_at", "cache_status"} <= set(result)


@pytest.mark.asyncio
async def test_agent_detail_response_includes_partial_failures() -> None:
    fac = _make_facade(
        discovery_client=FakeDiscoveryClient(_two_source_inventory()),
        query_client=FakeMixedQueryClient(rows=[_agent_row(agent_key="agent-1")]),
    )
    result = await fac.agent_detail(agent_key="agent-1", filters=_filters(), user_context={})

    assert result is not None
    assert result["partial_failures"] == [
        {
            "source_id": "source-2",
            "status": "timeout",
            "reason": "Deadline exceeded after 30000ms",
            "next_action": "Retry with a narrower time range or fewer sources.",
        }
    ]


# ---------------------------------------------------------------------------
# agent_detail: T053 trends + portal links
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_detail_returns_none_for_unseen_agent() -> None:
    fac = _make_facade(query_client=FakeQueryClient(rows=[_agent_row(agent_key="agent-1")]))
    result = await fac.agent_detail(agent_key="agent-does-not-exist", filters=_filters(), user_context={})
    assert result is None


@pytest.mark.asyncio
async def test_agent_detail_includes_bounded_trends_and_portal_links() -> None:
    detail_rows = [
        {
            "TimeGenerated": _NOW - timedelta(hours=i),
            "invocations": i + 1,
            "failures": 0,
            "p95_latency_ms": 100.0 + i,
        }
        for i in range(3)
    ]
    query_client = FakeQueryClient(
        rows=[_agent_row()],
        detail_results=[SourceResult(source_id="source-1", status="success", tables=detail_rows)],
    )
    fac = _make_facade(query_client=query_client)
    result = await fac.agent_detail(agent_key="agent-1", filters=_filters(), user_context={})
    assert result is not None
    assert query_client.detail_calls == 1
    assert result["trends"], "expected at least one bounded trend series"
    for trend in result["trends"]:
        assert len(trend["series"][0]["points"]) <= MAX_TREND_POINTS
    assert result["portal_links"]["foundry_resource"]
    assert result["portal_links"]["foundry_project"]
    assert result["portal_links"]["azure_monitor_resource"]


@pytest.mark.asyncio
async def test_agent_detail_no_matching_sources_falls_back_to_available_sources() -> None:
    # source has no foundry_resource_id/project overlap with the agent, so
    # the narrowing match is empty and the fallback-to-available-sources
    # path must still be exercised rather than silently returning nothing.
    unrelated_source = _source(
        source_id="source-unrelated", foundry_resource_id=None, project_resource_ids=[]
    )
    query_client = FakeQueryClient(rows=[_agent_row()])
    fac = _make_facade(
        discovery_client=FakeDiscoveryClient(_inventory([unrelated_source])),
        query_client=query_client,
    )
    result = await fac.agent_detail(agent_key="agent-1", filters=_filters(), user_context={})
    assert result is not None
    assert query_client.detail_calls == 1


def test_build_trend_series_ignores_failed_sources_and_bounds_points() -> None:
    many_rows = [
        {"TimeGenerated": _NOW - timedelta(hours=i), "invocations": 1, "failures": 0, "p95_latency_ms": 1.0}
        for i in range(MAX_TREND_POINTS + 50)
    ]
    results = [
        SourceResult(source_id="s1", status="success", tables=many_rows),
        SourceResult(source_id="s2", status="timeout", tables=None, reason="deadline exceeded"),
    ]
    trends = _build_trend_series(results)
    assert trends
    for trend in trends:
        assert len(trend["series"][0]["points"]) <= MAX_TREND_POINTS


# ---------------------------------------------------------------------------
# trace_content: OBO-only, cache-free, safe fallbacks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trace_content_missing_user_assertion_raises_value_error() -> None:
    fac = _make_facade()
    with pytest.raises(ValueError):
        await fac.trace_content(
            request={"source_resource_id": _WORKSPACE_RESOURCE_ID, "trace_id": "trace-1"},
            user_context={},
        )


@pytest.mark.asyncio
async def test_trace_content_unknown_source_returns_not_configured_without_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeLogsQueryAdapter.instances.clear()
    monkeypatch.setattr(facade_module.adapters, "_LogsQueryAdapter", FakeLogsQueryAdapter)
    fac = _make_facade(discovery_client=FakeDiscoveryClient(_inventory([_source()])))
    result = await fac.trace_content(
        request={"source_resource_id": "/subscriptions/x/resourceGroups/y/providers/Microsoft.Foo/bars/baz",
                  "trace_id": "trace-1"},
        user_context={"access_token": "token-1"},
    )
    assert result["protection_state"] == "not_configured"
    assert not FakeLogsQueryAdapter.instances, "unknown source must never be queried"


@pytest.mark.asyncio
async def test_trace_content_classifies_available_content_and_never_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeLogsQueryAdapter.instances.clear()
    monkeypatch.setattr(facade_module.adapters, "_LogsQueryAdapter", FakeLogsQueryAdapter)

    class SpyCache(ObserveCache):
        def __init__(self) -> None:
            super().__init__(ttl_seconds=120.0)
            self.set_calls = 0

        def set(self, key, value):  # type: ignore[override]
            self.set_calls += 1
            super().set(key, value)

    cache = SpyCache()
    fac = _make_facade(cache=cache)
    result = await fac.trace_content(
        request={"source_resource_id": _WORKSPACE_RESOURCE_ID, "trace_id": "trace-1"},
        user_context={"access_token": "token-1"},
    )
    assert result["protection_state"] == "available"
    assert result["input_messages"] == ["hello"]
    assert FakeLogsQueryAdapter.instances, "expected the fake logs adapter to be used"
    assert FakeLogsQueryAdapter.instances[0].closed is True
    # get_inventory (non-sensitive) may cache; the protected content itself
    # must never be written through ObserveCache.set.
    for call_args in cache.set_calls, cache._entries:  # pragma: no cover - sanity only
        pass
    assert cache.set_calls == 1  # only the inventory cache write from get_inventory


@pytest.mark.asyncio
async def test_trace_content_zero_rows_reports_protected_or_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EmptyLogsQueryAdapter(FakeLogsQueryAdapter):
        async def query_batch(self, requests):
            self.batches.append(requests)
            return [_FakeBatchItem(tables=[]) for _ in requests]

    monkeypatch.setattr(facade_module.adapters, "_LogsQueryAdapter", EmptyLogsQueryAdapter)
    fac = _make_facade()
    result = await fac.trace_content(
        request={"source_resource_id": _WORKSPACE_RESOURCE_ID, "trace_id": "trace-1"},
        user_context={"access_token": "token-1"},
    )
    assert result["protection_state"] == "protected_or_unavailable"


@pytest.mark.asyncio
async def test_aclose_delegates_to_query_client() -> None:
    query_client = FakeQueryClient()
    fac = _make_facade(query_client=query_client)
    await fac.aclose()
    assert query_client.closed is True
