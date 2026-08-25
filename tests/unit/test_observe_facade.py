"""Tests for the route-facing Observe facade/factory production composition.

Every Azure-touching collaborator is injected via the constructor/factory
seams ``facade.py`` exposes for exactly this purpose (``discovery_client``,
``query_client``, ``cache``, ``credential_factory``, ``obo_factory``), so
these tests never need real Azure SDK packages or ``sys.modules`` faking.
"""

from __future__ import annotations

import json
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
from agentops.agent.observe.service import ObserveResult, PartialFailure
from agentops.core.cost import (
    COST_ALLOCATION_DISCLAIMER,
    CostModelLoadResult,
    CostPeriodRef,
    CostViewData,
    load_cost_model,
)
from agentops.core.observe import (
    CoverageResult,
    ObserveScope,
    QueryDiagnostics,
    ResourceInventory,
    ResultBounds,
    TelemetrySource,
)
from fixtures.cost import mixed_currency_cost_model_payload, valid_cost_model_payload

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
    cost_model: CostModelLoadResult | None = None,
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
        cost_model_result=cost_model,
    )


def _valid_cost_model() -> CostModelLoadResult:
    return load_cost_model(json.dumps(valid_cost_model_payload()))


def _mixed_cost_model() -> CostModelLoadResult:
    return load_cost_model(json.dumps(mixed_currency_cost_model_payload()))


def _cost_view_data(*, breakdown: str = "agents", component_filter: str | None = None) -> CostViewData:
    return CostViewData(
        period=CostPeriodRef(
            id="2026-08",
            starts_at="2026-08-01T00:00:00Z",
            ends_at="2026-09-01T00:00:00Z",
        ),
        breakdown=breakdown,
        component_filter=component_filter,
        components=[],
        rows=[],
        currency_subtotals=[],
        calculated_at=_NOW,
    )


def _rich_cost_view_data() -> CostViewData:
    period_provenance = {
        "period_id": "2026-08",
        "starts_at": "2026-08-01T00:00:00Z",
        "ends_at": "2026-09-01T00:00:00Z",
        "breakdown": "agents",
        "currency": "USD",
        "currency_minor_units": 2,
    }
    commitment_provenance = {
        **period_provenance,
        "component_id": "gpt-ptu-prod",
        "component_type": "provisioned_throughput",
        "billing_boundary": {
            "kind": "resource",
            "value": _FOUNDRY_ID,
            "label": "Production Foundry",
        },
        "billed_source": "August provisioned throughput commitment",
        "allocation_model": "commitment",
        "preferred_key": "weighted_tokens",
        "applied_key": "total_tokens",
        "fallback_used": True,
    }
    metered_provenance = {
        **period_provenance,
        "component_id": "search-prod",
        "component_type": "search",
        "billing_boundary": {
            "kind": "resource",
            "value": "/subscriptions/00000000-0000-0000-0000-000000000000/"
            "resourceGroups/rg1/providers/Microsoft.Search/searchServices/search-prod",
            "label": None,
        },
        "billed_source": "August search billed total",
        "allocation_model": "metered",
        "preferred_key": "tool_invocations",
        "applied_key": "tool_invocations",
        "fallback_used": False,
    }
    zero_provenance = {
        **period_provenance,
        "component_id": "model-zero",
        "component_type": "standard_model",
        "billing_boundary": {
            "kind": "resource",
            "value": _FOUNDRY_ID,
            "label": None,
        },
        "billed_source": "Explicit zero metered total",
        "allocation_model": "metered",
        "preferred_key": "total_tokens",
        "applied_key": "total_tokens",
        "fallback_used": False,
    }
    return CostViewData.model_validate(
        {
            "period": {
                "id": "2026-08",
                "starts_at": "2026-08-01T00:00:00Z",
                "ends_at": "2026-09-01T00:00:00Z",
            },
            "breakdown": "agents",
            "component_filter": None,
            "components": [
                {
                    **commitment_provenance,
                    "declared_total": "120.00",
                    "attributed_amount": "90.00",
                    "unattributed_amount": "10.00",
                    "unallocated_amount": "20.00",
                    "omitted_allocated_amount": "5.00",
                    "rows_shown": 2,
                    "rows_total": 3,
                    "confidence": "medium",
                    "coverage_state": "partial",
                    "coverage_reason": "Fallback usage is readable but the period is partial.",
                    "next_action": "Review the missing preferred-key telemetry.",
                },
                {
                    **metered_provenance,
                    "declared_total": "30.00",
                    "attributed_amount": "0.00",
                    "unattributed_amount": "0.00",
                    "unallocated_amount": "30.00",
                    "omitted_allocated_amount": "0.00",
                    "rows_shown": 0,
                    "rows_total": 0,
                    "confidence": "unavailable",
                    "coverage_state": "not_configured",
                    "coverage_reason": "Tool invocation telemetry is not configured.",
                    "next_action": "Configure readable tool telemetry.",
                },
                {
                    **zero_provenance,
                    "declared_total": "0.00",
                    "attributed_amount": "0.00",
                    "unattributed_amount": "0.00",
                    "unallocated_amount": "0.00",
                    "omitted_allocated_amount": "0.00",
                    "rows_shown": 0,
                    "rows_total": 0,
                    "confidence": "high",
                    "coverage_state": "available",
                    "coverage_reason": "The configured billed total is explicitly zero.",
                    "next_action": None,
                },
            ],
            "rows": [
                {
                    **commitment_provenance,
                    "consumer_kind": "agent",
                    "consumer_key": "agent-1",
                    "source_resource_id": _WORKSPACE_RESOURCE_ID,
                    "project_resource_id": _PROJECT_ID,
                    "agent_key": "agent-1",
                    "tool_name": None,
                    "run_key": "run-1",
                    "amount": "80.00",
                    "usage_numerator": "8",
                    "usage_denominator": "10",
                    "usage_unit": "total_tokens",
                    "rounding_adjustment_minor_units": 0,
                    "confidence": "medium",
                    "coverage_state": "partial",
                    "coverage_reason": "Fallback usage covers part of the period.",
                    "calculated_at": "2026-09-01T01:00:00Z",
                    "latest_observed_at": "2026-08-30T12:00:00Z",
                },
                {
                    **commitment_provenance,
                    "consumer_kind": "unattributed",
                    "consumer_key": "unattributed",
                    "source_resource_id": _WORKSPACE_RESOURCE_ID,
                    "project_resource_id": None,
                    "agent_key": None,
                    "tool_name": None,
                    "run_key": None,
                    "amount": "10.00",
                    "usage_numerator": "2",
                    "usage_denominator": "10",
                    "usage_unit": "total_tokens",
                    "rounding_adjustment_minor_units": 1,
                    "confidence": "low",
                    "coverage_state": "partial",
                    "coverage_reason": "Usage could not be attributed to an agent.",
                    "calculated_at": "2026-09-01T01:00:00Z",
                    "latest_observed_at": "2026-08-29T12:00:00Z",
                },
            ],
            "currency_subtotals": [
                {
                    "currency": "USD",
                    "currency_minor_units": 2,
                    "declared_total": "150.00",
                    "attributed_amount": "90.00",
                    "unattributed_amount": "10.00",
                    "unallocated_amount": "50.00",
                }
            ],
            "calculated_at": "2026-09-01T01:00:00Z",
            "latest_observed_at": "2026-08-30T12:00:00Z",
            "disclaimer": COST_ALLOCATION_DISCLAIMER,
        }
    )


def _alternate_cost_view_data(breakdown: str) -> CostViewData:
    payload = _rich_cost_view_data().model_dump(mode="json")
    payload["breakdown"] = breakdown
    for component in payload["components"]:
        component["breakdown"] = breakdown
    for index, row in enumerate(payload["rows"]):
        row["breakdown"] = breakdown
        if index == 0 and breakdown == "tools":
            row.update(
                consumer_kind="tool",
                consumer_key="product_search",
                tool_name="product_search",
            )
        elif index == 0 and breakdown == "runs":
            row.update(
                consumer_kind="run",
                consumer_key="run-1",
                run_key="run-1",
            )
    return CostViewData.model_validate(payload)


class FakeCostService:
    def __init__(
        self,
        data: CostViewData | None = None,
        *,
        coverage: Sequence[CoverageResult] = (),
        partial_failures: Sequence[PartialFailure] = (),
        bounds: ResultBounds | None = None,
    ) -> None:
        self.data = data or _cost_view_data()
        self.coverage = list(coverage)
        self.partial_failures = list(partial_failures)
        self.bounds = bounds
        self.calls: list[dict[str, Any]] = []

    async def query_cost(
        self,
        scope,
        filters,
        *,
        cost_model,
        cost_model_fingerprint,
        refresh=False,
    ):
        self.calls.append(
            {
                "scope": scope,
                "filters": filters,
                "cost_model": cost_model,
                "cost_model_fingerprint": cost_model_fingerprint,
                "refresh": refresh,
            }
        )
        diagnostics = QueryDiagnostics(
            started_at=_NOW,
            completed_at=_NOW,
            duration_ms=0,
            source_count=0,
            successful_sources=0,
            partial_sources=0,
            failed_sources=0,
            cache_status="bypass" if refresh else "miss",
        )
        return ObserveResult(
            view="cost",
            data=self.data,
            coverage=self.coverage,
            diagnostics=diagnostics,
            partial_failures=self.partial_failures,
            bounds=self.bounds,
            refreshed_at=_NOW,
            cache_status="bypass" if refresh else "miss",
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


def test_create_observe_facade_accepts_cost_model_load_result() -> None:
    cost_model = _valid_cost_model()
    fac = create_observe_facade(
        scope=_scope(),
        tenant_id="t",
        application_client_id="a",
        uami_client_id="u",
        cost_model_result=cost_model,
        discovery_client=FakeDiscoveryClient(_inventory([_source()])),
        query_client=FakeQueryClient(),
        credential_factory=lambda *, client_id: FakeCredential(),
    )
    assert fac._cost_model_result is cost_model


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
    assert result["bounds"] is None


@pytest.mark.asyncio
async def test_query_agents_view_normalizes_rows() -> None:
    fac = _make_facade(query_client=FakeQueryClient(rows=[_agent_row()]))
    result = await fac.query(view="agents", filters=_filters(), refresh=False, user_context={})
    assert result["data"][0]["key"] == "agent-1"
    assert result["data"][0]["source_id"] == "source-1"


@pytest.mark.asyncio
async def test_query_tools_view_normalizes_rows_and_serializes_bounds() -> None:
    row = {
        "tool_name": "search",
        "agent_key": "agent-1",
        "agent_id": "agent-1",
        "agent_name": "Agent One",
        "last_seen": _NOW,
        "invocations": 7,
        "failures": 1,
        "p95_latency_ms": None,
        "total_in_scope": 1,
    }
    fac = _make_facade(query_client=FakeQueryClient(rows=[row]))

    result = await fac.query(view="tools", filters=_filters(), refresh=False, user_context={})

    assert result["view"] == "tools"
    assert result["data"][0]["tool_name"] == "search"
    assert result["data"][0]["source_id"] == "source-1"
    assert result["data"][0]["p95_latency_ms"] is None
    assert result["bounds"] == {
        "rows_shown": 1,
        "rows_total_in_scope": 1,
        "truncated": False,
    }


@pytest.mark.asyncio
async def test_query_runs_view_preserves_mixed_correlation_kinds() -> None:
    rows = [
        {
            "run_key": "conversation-1",
            "run_key_kind": "conversation",
            "agent_key": "agent-1",
            "agent_id": "agent-1",
            "agent_name": "Agent One",
            "started_at": _NOW - timedelta(minutes=10),
            "last_activity_at": _NOW - timedelta(minutes=5),
            "duration_ms": 300_000.0,
            "turns": 2,
            "failed_turns": 0,
            "tool_invocations": 1,
            "tool_failures": 0,
            "input_tokens": 100,
            "output_tokens": 50,
            "total_in_scope": 2,
        },
        {
            "run_key": "trace-1",
            "run_key_kind": "trace",
            "agent_key": "agent-1",
            "agent_id": "agent-1",
            "agent_name": "Agent One",
            "started_at": _NOW - timedelta(minutes=8),
            "last_activity_at": _NOW - timedelta(minutes=4),
            "duration_ms": 240_000.0,
            "turns": 1,
            "failed_turns": 1,
            "tool_invocations": 1,
            "tool_failures": 1,
            "input_tokens": None,
            "output_tokens": None,
            "total_in_scope": 2,
        },
    ]
    fac = _make_facade(query_client=FakeQueryClient(rows=rows))

    result = await fac.query(view="runs", filters=_filters(), refresh=False, user_context={})

    assert {item["run_key_kind"] for item in result["data"]} == {"conversation", "trace"}
    assert all(item["source_id"] == "source-1" for item in result["data"])
    assert result["bounds"]["rows_total_in_scope"] == 2


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
# T012/T019: cost view dispatch, selector validation, and serialization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_cost_uses_authoritative_period_and_only_cost_selectors() -> None:
    loaded = _valid_cost_model()
    facade = _make_facade(cost_model=loaded)
    cost_service = FakeCostService(_cost_view_data(component_filter="gpt-ptu-prod"))
    facade._service = cost_service

    result = await facade.query(
        view="cost",
        filters={
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-01-02T00:00:00Z",
            "foundry_resource_id": _FOUNDRY_ID,
            "project_resource_id": _PROJECT_ID,
            "agent_id": "shared-agent-must-be-ignored",
            "model": "shared-model-must-be-ignored",
            "tool_name": "shared-tool-must-be-ignored",
            "run_key": "shared-run-must-be-ignored",
            "cost_period_id": "2026-08",
            "cost_breakdown": "tools",
            "cost_component_id": "gpt-ptu-prod",
            "cost_agent_key": "agent-1",
        },
        refresh=True,
        user_context={},
    )

    assert len(cost_service.calls) == 1
    call = cost_service.calls[0]
    calculation_filters = call["filters"]
    assert calculation_filters.start.isoformat() == "2026-08-01T00:00:00+00:00"
    assert calculation_filters.end.isoformat() == "2026-09-01T00:00:00+00:00"
    assert calculation_filters.cost_period_id == "2026-08"
    assert calculation_filters.cost_breakdown == "tools"
    assert calculation_filters.cost_component_id == "gpt-ptu-prod"
    assert calculation_filters.cost_agent_key == "agent-1"
    assert calculation_filters.foundry_resource_id is None
    assert calculation_filters.project_resource_id is None
    assert calculation_filters.agent_id is None
    assert calculation_filters.model is None
    assert calculation_filters.tool_name is None
    assert calculation_filters.run_key is None
    assert call["cost_model"] is loaded.model
    assert call["cost_model_fingerprint"] == loaded.fingerprint
    assert call["refresh"] is True
    assert result["view"] == "cost"
    assert result["data"]["period"]["id"] == "2026-08"
    assert result["data"]["component_filter"] == "gpt-ptu-prod"
    assert result["data"]["calculated_at"] == "2024-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_query_cost_defaults_breakdown_to_agents() -> None:
    facade = _make_facade(cost_model=_valid_cost_model())
    cost_service = FakeCostService()
    facade._service = cost_service

    await facade.query(
        view="cost",
        filters={**_filters(), "cost_period_id": "2026-08"},
        user_context={},
    )

    assert cost_service.calls[0]["filters"].cost_breakdown == "agents"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("breakdown", "component_id", "consumer_kind", "consumer_key"),
    [
        ("tools", "search-prod", "tool", "product_search"),
        ("runs", "gpt-ptu-prod", "run", "run-1"),
    ],
)
async def test_query_cost_dispatches_alternate_breakdowns_without_reallocation(
    breakdown: str,
    component_id: str,
    consumer_kind: str,
    consumer_key: str,
) -> None:
    facade = _make_facade(cost_model=_mixed_cost_model())
    cost_service = FakeCostService(_alternate_cost_view_data(breakdown))
    facade._service = cost_service

    result = await facade.query(
        view="cost",
        filters={
            **_filters(),
            "cost_period_id": "2026-08",
            "cost_breakdown": breakdown,
            "cost_component_id": component_id,
            "cost_agent_key": "agent-1",
        },
        user_context={},
    )

    calculation_filters = cost_service.calls[0]["filters"]
    assert calculation_filters.cost_breakdown == breakdown
    assert calculation_filters.cost_component_id == component_id
    assert calculation_filters.cost_agent_key == "agent-1"
    assert result["data"]["breakdown"] == breakdown
    assert result["data"]["rows"][0]["consumer_kind"] == consumer_kind
    assert result["data"]["rows"][0]["consumer_key"] == consumer_key
    assert result["data"]["components"][0]["omitted_allocated_amount"] == "5.00"
    assert result["data"]["currency_subtotals"] == [
        {
            "currency": "USD",
            "currency_minor_units": 2,
            "declared_total": "150.00",
            "attributed_amount": "90.00",
            "unattributed_amount": "10.00",
            "unallocated_amount": "50.00",
        }
    ]


@pytest.mark.asyncio
async def test_query_cost_keeps_alternate_breakdown_totals_non_additive() -> None:
    facade = _make_facade(cost_model=_valid_cost_model())
    totals: list[str] = []

    for breakdown in ("agents", "tools", "runs"):
        facade._service = FakeCostService(_alternate_cost_view_data(breakdown))
        result = await facade.query(
            view="cost",
            filters={
                **_filters(),
                "cost_period_id": "2026-08",
                "cost_breakdown": breakdown,
            },
            user_context={},
        )
        totals.append(result["data"]["components"][0]["declared_total"])

    assert totals == ["120.00", "120.00", "120.00"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        ({"cost_period_id": "missing-period"}, "cost period"),
        (
            {
                "cost_period_id": "2026-08",
                "cost_component_id": "missing-component",
            },
            "cost component",
        ),
    ],
)
async def test_query_cost_rejects_unknown_configured_selectors(
    selector: dict[str, str], expected: str
) -> None:
    facade = _make_facade(cost_model=_valid_cost_model())
    facade._service = FakeCostService()

    with pytest.raises(ValueError, match=expected):
        await facade.query(
            view="cost",
            filters={**_filters(), **selector},
            user_context={},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        ({"cost_period_id": "2026-08", "cost_breakdown": "services"}, "cost_breakdown"),
        ({"cost_period_id": "2026-08", "cost_agent_key": "   "}, "cost_agent_key"),
    ],
)
async def test_query_cost_rejects_invalid_breakdown_and_agent_selectors(
    selector: dict[str, str], expected: str
) -> None:
    facade = _make_facade(cost_model=_valid_cost_model())
    facade._service = FakeCostService()

    with pytest.raises(ValueError, match=expected):
        await facade.query(
            view="cost",
            filters={**_filters(), **selector},
            user_context={},
        )


@pytest.mark.asyncio
async def test_query_cost_requires_period_selector() -> None:
    facade = _make_facade(cost_model=_valid_cost_model())
    facade._service = FakeCostService()

    with pytest.raises(ValueError, match="cost_period_id"):
        await facade.query(view="cost", filters=_filters(), user_context={})


@pytest.mark.asyncio
async def test_query_cost_absent_and_invalid_configuration_fail_closed() -> None:
    absent = _make_facade()
    invalid = _make_facade(
        cost_model=CostModelLoadResult(
            state="invalid",
            error_code="cost_model_validation_error",
            message="Invalid cost model field 'periods[0]'. Correct the field and restart Cockpit.",
        )
    )

    with pytest.raises(ValueError, match="AGENTOPS_COST_MODEL"):
        await absent.query(
            view="cost",
            filters={**_filters(), "cost_period_id": "2026-08"},
            user_context={},
        )
    with pytest.raises(ValueError, match="Correct the field"):
        await invalid.query(
            view="cost",
            filters={**_filters(), "cost_period_id": "2026-08"},
            user_context={},
        )


@pytest.mark.asyncio
async def test_invalid_cost_configuration_does_not_change_non_cost_requests() -> None:
    facade = _make_facade(
        cost_model=CostModelLoadResult(
            state="invalid",
            error_code="cost_model_invalid_json",
            message="AGENTOPS_COST_MODEL must be a complete valid JSON object.",
        )
    )

    result = await facade.query(
        view="agents",
        filters={**_filters(), "cost_period_id": "ignored-by-agents"},
        user_context={},
    )

    assert result["view"] == "agents"
    assert result["data"][0]["key"] == "agent-1"


@pytest.mark.asyncio
async def test_query_cost_preserves_complete_provenance_evidence_and_freshness() -> None:
    facade = _make_facade(cost_model=_valid_cost_model())
    facade._service = FakeCostService(_rich_cost_view_data())

    result = await facade.query(
        view="cost",
        filters={**_filters(), "cost_period_id": "2026-08"},
        user_context={},
    )

    data = result["data"]
    assert data["period"] == {
        "id": "2026-08",
        "starts_at": "2026-08-01T00:00:00Z",
        "ends_at": "2026-09-01T00:00:00Z",
    }
    assert data["calculated_at"] == "2026-09-01T01:00:00Z"
    assert data["latest_observed_at"] == "2026-08-30T12:00:00Z"
    assert data["disclaimer"] == COST_ALLOCATION_DISCLAIMER

    commitment, metered, explicit_zero = data["components"]
    assert commitment["billing_boundary"] == {
        "kind": "resource",
        "value": _FOUNDRY_ID.lower(),
        "label": "Production Foundry",
    }
    assert commitment["billed_source"] == "August provisioned throughput commitment"
    assert commitment["allocation_model"] == "commitment"
    assert commitment["preferred_key"] == "weighted_tokens"
    assert commitment["applied_key"] == "total_tokens"
    assert commitment["fallback_used"] is True
    assert commitment["confidence"] == "medium"
    assert commitment["coverage_state"] == "partial"
    assert commitment["coverage_reason"]
    assert commitment["next_action"]
    assert (commitment["rows_shown"], commitment["rows_total"]) == (2, 3)
    assert commitment["omitted_allocated_amount"] == "5.00"

    assert metered["allocation_model"] == "metered"
    assert metered["billed_source"] == "August search billed total"
    assert explicit_zero["billed_source"] == "Explicit zero metered total"
    assert [component["declared_total"] for component in data["components"]] == [
        "120.00",
        "30.00",
        "0.00",
    ]

    row = data["rows"][0]
    assert row["starts_at"] == "2026-08-01T00:00:00Z"
    assert row["ends_at"] == "2026-09-01T00:00:00Z"
    assert row["source_resource_id"] == _WORKSPACE_RESOURCE_ID.lower()
    assert row["project_resource_id"] == _PROJECT_ID.lower()
    assert row["usage_numerator"] == "8"
    assert row["usage_denominator"] == "10"
    assert row["usage_unit"] == "total_tokens"
    assert row["rounding_adjustment_minor_units"] == 0
    assert row["confidence"] == "medium"
    assert row["coverage_state"] == "partial"
    assert row["calculated_at"] == "2026-09-01T01:00:00Z"
    assert row["latest_observed_at"] == "2026-08-30T12:00:00Z"


@pytest.mark.asyncio
async def test_query_cost_preserves_explained_partial_and_missing_amount_states() -> None:
    coverage = CoverageResult(
        source_id="source-2",
        dimension="cost_attribution",
        state="partial",
        reason="One telemetry source was unavailable; readable usage was retained.",
        next_action="Restore source access and refresh the Cost view.",
        refreshed_at=_NOW,
        component_id="gpt-ptu-prod",
        cost_breakdown="agents",
        allocation_key="total_tokens",
    )
    partial_failure = PartialFailure(
        source_id="source-2",
        status="timeout",
        reason="Telemetry query timed out.",
        next_action="Retry with a narrower time range or fewer sources.",
    )
    facade = _make_facade(cost_model=_valid_cost_model())
    cost_service = FakeCostService(
        _rich_cost_view_data(),
        coverage=[coverage],
        partial_failures=[partial_failure],
        bounds=ResultBounds(
            rows_shown=2,
            rows_total_in_scope=3,
            truncated=False,
        ),
    )
    facade._service = cost_service

    result = await facade.query(
        view="cost",
        filters={
            **_filters(),
            "cost_period_id": "2026-08",
            "cost_component_id": "gpt-ptu-prod",
            "cost_agent_key": "agent-1",
        },
        user_context={},
    )

    components = {item["component_id"]: item for item in result["data"]["components"]}
    missing = components["search-prod"]
    explicit_zero = components["model-zero"]
    assert missing["coverage_state"] == "not_configured"
    assert missing["declared_total"] == "30.00"
    assert missing["unallocated_amount"] == "30.00"
    assert missing["coverage_reason"] and missing["next_action"]
    assert explicit_zero["coverage_state"] == "available"
    assert explicit_zero["declared_total"] == "0.00"
    assert missing != explicit_zero

    unattributed = next(
        row
        for row in result["data"]["rows"]
        if row["consumer_kind"] == "unattributed"
    )
    assert unattributed["consumer_key"] == "unattributed"
    assert unattributed["amount"] == "10.00"
    assert unattributed["rounding_adjustment_minor_units"] == 1
    assert components["gpt-ptu-prod"]["omitted_allocated_amount"] == "5.00"
    assert len(result["data"]["rows"]) <= 500
    assert result["bounds"] == {
        "rows_shown": 2,
        "rows_total_in_scope": 3,
        "truncated": False,
    }
    assert result["coverage"][0]["state"] == "partial"
    assert result["coverage"][0]["component_id"] == "gpt-ptu-prod"
    assert result["partial_failures"] == [
        {
            "source_id": "source-2",
            "status": "timeout",
            "reason": "Telemetry query timed out.",
            "next_action": "Retry with a narrower time range or fewer sources.",
        }
    ]
    assert cost_service.calls[0]["filters"].cost_component_id == "gpt-ptu-prod"
    assert cost_service.calls[0]["filters"].cost_agent_key == "agent-1"


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
