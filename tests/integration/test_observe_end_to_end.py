"""End-to-end hosted Observe API tests with deterministic fakes."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentops.agent import cockpit as cockpit_module
from agentops.agent.cockpit import create_app
from agentops.agent.observe.cost_allocation import allocate_cost_period
from agentops.agent.observe import facade as facade_module
from agentops.agent.observe.cache import ObserveCache
from agentops.agent.observe.queries import SourceResult
from agentops.agent.observe.service import ObserveService
from agentops.agent.observe.ui import render_models_usage_table
from agentops.core.attribution import (
    AttributionTokenValidationError,
    load_attribution_config,
)
from agentops.core.cost import (
    CostUsageObservation,
    load_cost_model as load_cost_model_contract,
)
from agentops.core.observe import (
    AttributionQueryRequest,
    ObserveScope,
    ResourceInventory,
    TelemetrySource,
)
from fixtures.cost import (
    FOUNDRY_RESOURCE_ID,
    mixed_currency_cost_model_payload,
    valid_cost_model_payload,
    valid_multi_period_cost_model_payload,
)
from fixtures.observe import (
    ATTRIBUTION_FIXTURE_GROUPS,
    ATTRIBUTION_FIXTURE_PRINCIPAL,
    make_attribution_config_payload,
    make_attribution_user_key,
)


class _Auth:
    def __call__(self, headers: Any) -> dict[str, Any]:
        if headers.get("x-ms-client-principal") != "allowed":
            raise PermissionError("authentication required")
        return {"tenant_id": "tenant", "user_id": "user", "groups": []}


class _AttributionAuth:
    def __call__(self, headers: Any) -> dict[str, Any]:
        if headers.get("x-ms-client-principal") != "allowed":
            raise PermissionError("authentication required")
        return {
            "tenant_id": "22222222-2222-2222-2222-222222222222",
            "user_id": ATTRIBUTION_FIXTURE_PRINCIPAL,
            "user_name": ATTRIBUTION_FIXTURE_PRINCIPAL,
            "groups": [ATTRIBUTION_FIXTURE_GROUPS[0]],
            "groups_overage": False,
            "access_token": "redacted-user-assertion",
        }


class _AttributionDiscovery:
    def __init__(self, scope: ObserveScope) -> None:
        self.inventory = ResourceInventory(
            scope=scope,
            telemetry_sources=[
                TelemetrySource(
                    source_id="synthetic-source",
                    resource_id=(
                        "/subscriptions/sub/resourceGroups/rg/providers/"
                        "Microsoft.OperationalInsights/workspaces/law"
                    ),
                    workspace_id="synthetic-workspace",
                    foundry_resource_id=FOUNDRY_RESOURCE_ID,
                    project_resource_ids=list(scope.project_resource_ids),
                    state="available",
                )
            ],
            discovered_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
            expires_at=datetime(2026, 8, 25, 1, tzinfo=timezone.utc),
        )

    async def discover(self, _scope: ObserveScope) -> ResourceInventory:
        return self.inventory


class _AttributionQuery:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    async def query_user_usage(self, sources, filters, **kwargs):
        self.calls.append({"sources": sources, "filters": filters, **kwargs})
        return [
            SourceResult(
                source_id=source.source_id,
                status="success",
                tables=deepcopy(self.rows),
                duration_ms=1,
            )
            for source in sources
        ]


def _production_attribution_service(
    rows: list[dict[str, Any]],
) -> tuple[ObserveService, ObserveScope, _AttributionQuery]:
    scope = ObserveScope(
        mode="projects",
        project_resource_ids=[FOUNDRY_RESOURCE_ID + "/projects/project-a"],
    )
    query = _AttributionQuery(rows)
    service = ObserveService(
        discovery_client=_AttributionDiscovery(scope),
        query_client=query,
        runtime=type(
            "Runtime",
            (),
            {"mode": "hosted", "credential_identity": "synthetic-delegated"},
        )(),
        clock=lambda: datetime(2026, 8, 25, tzinfo=timezone.utc),
        cache=ObserveCache(ttl_seconds=60),
    )
    return service, scope, query


class _Service:
    def __init__(self) -> None:
        self.discovery_calls = 0
        self.query_calls = 0

    def discover(self, **_: Any) -> dict[str, Any]:
        self.discovery_calls += 1
        return {
            "foundry_resources": [],
            "projects": [],
            "telemetry_sources": [],
            "partial_failures": [],
            "discovered_at": "2026-08-21T00:00:00Z",
            "expires_at": "2026-08-21T00:15:00Z",
        }

    def query(self, *, view: str, filters: dict[str, Any], **_: Any) -> dict[str, Any]:
        self.query_calls += 1
        return {
            "view": view,
            "data": {"filters": filters},
            "coverage": [],
            "diagnostics": {
                "started_at": "2026-08-21T00:00:00Z",
                "completed_at": "2026-08-21T00:00:00Z",
                "duration_ms": 0,
                "source_count": 0,
                "successful_sources": 0,
                "partial_sources": 0,
                "failed_sources": 0,
                "cache_status": "miss",
            },
            "refreshed_at": "2026-08-21T00:00:00Z",
        }

    def agent_detail(
        self, *, agent_key: str, filters: dict[str, Any], **_: Any
    ) -> dict[str, Any]:
        return {"agent_key": agent_key, "filters": filters, "trends": []}


class _CostFacade(_Service):
    """Agreed cost-facade signature backed by deterministic fake telemetry."""

    def __init__(self, cost_model_result: Any) -> None:
        super().__init__()
        self.cost_model_result = cost_model_result
        self.telemetry = [
            {"agent_key": "agent-a", "weighted_tokens": Decimal("2")},
            {"agent_key": "agent-b", "weighted_tokens": Decimal("1")},
        ]

    def query(self, *, view: str, filters: dict[str, Any], **_: Any) -> dict[str, Any]:
        if view != "cost":
            return super().query(view=view, filters=filters)
        self.query_calls += 1
        assert self.cost_model_result.state == "valid"
        component = self.cost_model_result.model.periods[0].components[0]
        total_usage = sum(row["weighted_tokens"] for row in self.telemetry)
        rows = []
        for telemetry in self.telemetry:
            share = telemetry["weighted_tokens"] / total_usage
            amount = (component.billed_total * share).quantize(Decimal("0.01"))
            rows.append(
                {
                    "consumer_key": telemetry["agent_key"],
                    "allocated_amount": str(amount),
                    "observed_usage": str(telemetry["weighted_tokens"]),
                    "usage_share": str(share),
                }
            )
        return {
            "view": "cost",
            "data": {
                "period_id": self.cost_model_result.model.periods[0].id,
                "breakdown": "agents",
                "rows": rows,
            },
            "coverage": [],
            "diagnostics": {
                "started_at": "2026-08-21T00:00:00Z",
                "completed_at": "2026-08-21T00:00:00Z",
                "duration_ms": 0,
                "source_count": 1,
                "successful_sources": 1,
                "partial_sources": 0,
                "failed_sources": 0,
                "cache_status": "miss",
            },
            "refreshed_at": "2026-08-21T00:00:00Z",
        }


class _AttributionFacade(_Service):
    def __init__(
        self,
        *,
        coverage: list[dict[str, Any]] | None = None,
        partial_failures: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__()
        self.attribution_requests: list[dict[str, Any]] = []
        self.user_contexts: list[dict[str, Any]] = []
        self.coverage = coverage
        self.partial_failures = partial_failures

    def attribution(
        self,
        *,
        request: dict[str, Any],
        user_context: dict[str, Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        self.attribution_requests.append(deepcopy(request))
        self.user_contexts.append(deepcopy(user_context or {}))
        metric = request["metric"]
        group_by = request["group_by"]
        department_token = request["filters"].get("department_filter_token")
        user_token = request["filters"].get("user_filter_token")
        usage = {
            "invocations": 10,
            "input_tokens": 100,
            "output_tokens": 50,
            "tool_invocations": 2,
            "active_session_seconds": None,
        }
        rows = [
            {
                "kind": "department",
                "department_id": "engineering",
                "department_label": "Engineering",
                "filter_token": "at1~d~g1~config~scope~engineering",
                "member_count": 2,
                "usage": {**usage, "invocations": 7},
                "cost": (
                    {
                        "period_id": "2026-08",
                        "component_id": "gpt-ptu-prod",
                        "amount": "70.00",
                        "currency": "USD",
                        "currency_minor_units": 2,
                        "usage_numerator": "7",
                        "usage_denominator": "10",
                        "allocation_key": "weighted_tokens",
                        "confidence": "high",
                    }
                    if metric == "cost"
                    else None
                ),
                "mapping_state": "mapped",
            },
            {
                "kind": "department",
                "department_id": "finance",
                "department_label": "Finance",
                "filter_token": "at1~d~g1~config~scope~finance",
                "member_count": 2,
                "usage": {**usage, "invocations": 3},
                "cost": (
                    {
                        "period_id": "2026-08",
                        "component_id": "gpt-ptu-prod",
                        "amount": "20.00",
                        "currency": "USD",
                        "currency_minor_units": 2,
                        "usage_numerator": "3",
                        "usage_denominator": "10",
                        "allocation_key": "weighted_tokens",
                        "confidence": "high",
                    }
                    if metric == "cost"
                    else None
                ),
                "mapping_state": "mapped",
            },
        ]
        if group_by == "user":
            rows = [
                {
                    "kind": "user",
                    "user_key": make_attribution_user_key(0),
                    "raw_identity": ATTRIBUTION_FIXTURE_PRINCIPAL,
                    "filter_token": "opaque-user-token-a",
                    "department_id": "engineering",
                    "department_label": "Engineering",
                    "usage": {**usage, "invocations": 5},
                    "cost": (
                        {
                            "period_id": "2026-08",
                            "component_id": "gpt-ptu-prod",
                            "amount": "50.00",
                            "currency": "USD",
                            "currency_minor_units": 2,
                            "usage_numerator": "5",
                            "usage_denominator": "10",
                            "allocation_key": "weighted_tokens",
                            "confidence": "high",
                        }
                        if metric == "cost"
                        else None
                    ),
                    "mapping_state": "mapped",
                },
                {
                    "kind": "user",
                    "user_key": make_attribution_user_key(1),
                    "raw_identity": "synthetic-user-2@example.test",
                    "filter_token": "opaque-user-token-b",
                    "department_id": "engineering",
                    "department_label": "Engineering",
                    "usage": {**usage, "invocations": 5},
                    "cost": (
                        {
                            "period_id": "2026-08",
                            "component_id": "gpt-ptu-prod",
                            "amount": "40.00",
                            "currency": "USD",
                            "currency_minor_units": 2,
                            "usage_numerator": "5",
                            "usage_denominator": "10",
                            "allocation_key": "weighted_tokens",
                            "confidence": "high",
                        }
                        if metric == "cost"
                        else None
                    ),
                    "mapping_state": "mapped",
                },
            ]
            if user_token:
                rows = [row for row in rows if row["filter_token"] == user_token]
        if department_token:
            rows = [row for row in rows if row.get("department_id") == "engineering"]
        summary = (
            {
                "metric": "cost",
                "declared_total": "100.00",
                "attributed_amount": "90.00",
                "unattributed_amount": "10.00",
                "unallocated_amount": "0.00",
            }
            if metric == "cost"
            else {
                "metric": "usage",
                "total": {**usage, "invocations": 12},
                "attributed": usage,
                "unattributed": {**usage, "invocations": 2},
            }
        )
        return {
            "data": {
                "metric": metric,
                "group_by": group_by,
                "access_boundary": "delegated" if group_by == "user" else "aggregate",
                "rows": rows,
                "summary": summary,
            },
            "coverage": self.coverage
            if self.coverage is not None
            else [{"source_id": "source-a", "state": "partial"}],
            "partial_failures": self.partial_failures
            if self.partial_failures is not None
            else [{"source_id": "source-b", "status": "timeout"}],
            "cache_status": "miss",
        }


class _AllocationFacade(_Service):
    """HTTP-facing fake telemetry facade using the real allocation engine."""

    def __init__(
        self,
        cost_model_result: Any,
        observations: list[CostUsageObservation],
        *,
        partial_sources: bool = False,
    ) -> None:
        super().__init__()
        self.cost_model_result = cost_model_result
        self.observations = observations
        self.partial_sources = partial_sources

    def query(self, *, view: str, filters: dict[str, Any], **_: Any) -> dict[str, Any]:
        if view != "cost":
            return super().query(view=view, filters=filters)
        self.query_calls += 1
        assert self.cost_model_result.state == "valid"
        period_id = filters.get("cost_period_id")
        period = next(
            period
            for period in self.cost_model_result.model.periods
            if period.id == period_id
        )
        allocation = allocate_cost_period(
            period,
            self.observations,
            breakdown=filters.get("cost_breakdown") or "agents",
            calculated_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            component_id=filters.get("cost_component_id"),
            cost_agent_key=filters.get("cost_agent_key"),
        )
        return {
            "view": "cost",
            "data": allocation.model_dump(mode="json"),
            "coverage": (
                [
                    {
                        "source_id": "workspace-partial",
                        "dimension": "cost_attribution",
                        "state": "partial",
                        "reason": "One telemetry source was unavailable.",
                        "next_action": "Restore access to the unavailable telemetry source.",
                        "refreshed_at": "2026-09-01T00:00:00Z",
                    }
                ]
                if self.partial_sources
                else []
            ),
            "diagnostics": {
                "started_at": "2026-09-01T00:00:00Z",
                "completed_at": "2026-09-01T00:00:00Z",
                "duration_ms": 0,
                "source_count": 2 if self.partial_sources else 1,
                "successful_sources": 1,
                "partial_sources": 1 if self.partial_sources else 0,
                "failed_sources": 0,
                "cache_status": "miss",
            },
            "refreshed_at": "2026-09-01T00:00:00Z",
        }


def _hosted_client() -> TestClient:
    app = create_app(
        None,
        mode="hosted",
        observe_scope={
            "version": 1,
            "mode": "projects",
            "project_resource_ids": [
                "/subscriptions/sub/resourcegroups/rg/providers/"
                "microsoft.cognitiveservices/accounts/foundry/projects/project-a"
            ],
        },
        observe_service=_Service(),
        auth_context_resolver=_Auth(),
    )
    return TestClient(app)


def _cost_client(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
    observations: list[CostUsageObservation],
    *,
    partial_sources: bool = False,
) -> TestClient:
    raw_model = json.dumps(payload)
    result = load_cost_model_contract(raw_model)
    assert result.state == "valid"
    monkeypatch.setenv("AGENTOPS_COST_MODEL", raw_model)
    return TestClient(
        create_app(
            None,
            mode="hosted",
            observe_scope={
                "version": 1,
                "mode": "projects",
                "project_resource_ids": [
                    "/subscriptions/sub/resourcegroups/rg/providers/"
                    "microsoft.cognitiveservices/accounts/foundry/projects/project-a"
                ],
            },
            observe_service=_AllocationFacade(
                result,
                observations,
                partial_sources=partial_sources,
            ),
            auth_context_resolver=_Auth(),
        )
    )


def _usage(**overrides: Any) -> CostUsageObservation:
    values: dict[str, Any] = {
        "source_resource_id": FOUNDRY_RESOURCE_ID,
        "project_resource_id": (
            f"{FOUNDRY_RESOURCE_ID}/projects/project-a"
        ),
        "agent_key": "agent-a",
        "tool_name": "search",
        "run_key": "run-a",
        "runtime_kind": "foundry_hosted",
        "deployment": "gpt-prod",
        "input_tokens": 1,
        "output_tokens": 1,
        "cache_read_tokens": 0,
        "tool_invocations": 1,
        "latest_observed_at": "2026-08-31T23:00:00Z",
        "coverage_complete": True,
    }
    values.update(overrides)
    return CostUsageObservation.model_validate(values)


def test_observe_discovery_and_all_views() -> None:
    client: TestClient = _hosted_client()
    headers = {"x-ms-client-principal": "allowed"}
    end = datetime(2026, 8, 21, tzinfo=timezone.utc)
    filters = {
        "start": (end - timedelta(hours=24)).isoformat(),
        "end": end.isoformat(),
    }

    discovery = client.get(
        "/api/observe/discovery",
        headers=headers,
    )
    assert discovery.status_code == 200

    for view in ("overview", "agents", "models", "coverage", "tools", "runs"):
        response = client.post(
            "/api/observe/query",
            headers=headers,
            json={"view": view, "filters": filters},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["view"] == view
        assert payload["diagnostics"]["source_count"] == 0


def test_granular_models_payload_renders_end_to_end() -> None:
    class _GranularService(_Service):
        def query(self, *, view: str, filters: dict[str, Any], **_: Any) -> dict[str, Any]:
            payload = super().query(view=view, filters=filters)
            if view != "models":
                return payload
            payload["data"] = [
                {
                    "project_resource_id": (
                        "/subscriptions/sub/resourcegroups/rg/providers/"
                        "microsoft.cognitiveservices/accounts/foundry/projects/project-a"
                    ),
                    "deployment": "gpt-5-nano",
                    "model": "gpt-5-nano",
                    "requests": 3,
                    "failures": 0,
                    "input_tokens": 30,
                    "output_tokens": 12,
                    "cache_read_tokens": 8,
                    "cache_write_tokens": None,
                    "reasoning_tokens": 4,
                    "additional_token_classes": {
                        "gen_ai.usage.audio.input_tokens": 2,
                        "gen_ai.usage.audio.output_tokens": 1,
                        "gen_ai.usage.image.input_tokens": 3,
                        "gen_ai.usage.image.output_tokens": 2,
                        "gen_ai.usage.vendor_tokens": 5,
                    },
                    "additional_token_classes_truncated": True,
                    "partially_reported_token_classes": ["cache-read"],
                    "token_classes_partial": True,
                    "last_seen": "2026-08-21T00:00:00Z",
                }
            ]
            payload["coverage"] = [
                {
                    "source_id": "src-1",
                    "dimension": "token_usage",
                    "state": "partial",
                    "reason": "cache-read is intermittently reported; cache-write is not reported.",
                    "next_action": (
                        "Configure instrumentation to emit cache-read consistently "
                        "and cache-write under gen_ai.usage.*."
                    ),
                    "refreshed_at": "2026-08-21T00:00:00Z",
                }
            ]
            return payload

    app = create_app(
        None,
        mode="hosted",
        observe_scope={
            "version": 1,
            "mode": "projects",
            "project_resource_ids": [
                "/subscriptions/sub/resourcegroups/rg/providers/"
                "microsoft.cognitiveservices/accounts/foundry/projects/project-a"
            ],
        },
        observe_service=_GranularService(),
        auth_context_resolver=_Auth(),
    )
    client = TestClient(app)
    end = datetime(2026, 8, 21, tzinfo=timezone.utc)
    response = client.post(
        "/api/observe/query",
        headers={"x-ms-client-principal": "allowed"},
        json={
            "view": "models",
            "filters": {
                "start": (end - timedelta(hours=24)).isoformat(),
                "end": end.isoformat(),
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    row = payload["data"][0]
    assert row["cache_read_tokens"] == 8
    assert row["cache_write_tokens"] is None
    assert row["reasoning_tokens"] == 4
    assert len(row["additional_token_classes"]) == 5
    assert row["additional_token_classes_truncated"] is True
    assert payload["coverage"][0]["state"] == "partial"

    html = render_models_usage_table(payload["data"])
    assert "Cache read" in html
    assert "Not reported" in html
    assert "Partial class coverage" in html
    assert "Additional classes truncated" in html


def test_agent_detail_is_authenticated_and_bounded() -> None:
    client = _hosted_client()
    end = datetime(2026, 8, 21, tzinfo=timezone.utc)
    response = client.post(
        "/api/observe/agent-detail",
        headers={"x-ms-client-principal": "allowed"},
        json={
            "agent_key": "agent-a",
            "filters": {
                "start": (end - timedelta(hours=24)).isoformat(),
                "end": end.isoformat(),
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["agent_key"] == "agent-a"


def test_local_and_hosted_modes_share_observe_results_without_startup_queries(
    tmp_path,
) -> None:
    scope = {
        "version": 1,
        "mode": "projects",
        "project_resource_ids": [
            "/subscriptions/sub/resourcegroups/rg/providers/"
            "microsoft.cognitiveservices/accounts/foundry/projects/project-a"
        ],
    }
    local_service = _Service()
    hosted_service = _Service()
    local = TestClient(
        create_app(
            tmp_path,
            mode="local",
            observe_scope=scope,
            observe_service=local_service,
        )
    )
    hosted = TestClient(
        create_app(
            None,
            mode="hosted",
            observe_scope=scope,
            observe_service=hosted_service,
            auth_context_resolver=_Auth(),
        )
    )

    assert local.get("/").status_code == 200
    local_observe = local.get("/observe")
    assert local_observe.status_code == 200
    assert "AgentOps Observe" in local_observe.text
    assert hosted.get("/", headers={"x-ms-client-principal": "allowed"}).status_code == 200
    assert local_service.discovery_calls == hosted_service.discovery_calls == 0
    assert local_service.query_calls == hosted_service.query_calls == 0

    end = datetime(2026, 8, 21, tzinfo=timezone.utc)
    request = {
        "view": "overview",
        "filters": {
            "start": (end - timedelta(hours=24)).isoformat(),
            "end": end.isoformat(),
        },
    }
    local_payload = local.post("/api/observe/query", json=request).json()
    hosted_payload = hosted.post(
        "/api/observe/query",
        headers={"x-ms-client-principal": "allowed"},
        json=request,
    ).json()

    assert local_payload == hosted_payload
    assert local_service.query_calls == hosted_service.query_calls == 1


def test_valid_cost_model_startup_opens_agent_allocation_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_calls: list[str | None] = []
    injected_results: list[Any] = []
    render_states: list[bool] = []

    def _load_once(raw: str | None) -> Any:
        load_calls.append(raw)
        return load_cost_model_contract(raw)

    def _create_facade(
        *,
        scope: Any,
        cost_model_result: Any,
        attribution_config_result: Any = None,
    ) -> _CostFacade:
        assert scope["mode"] == "projects"
        assert attribution_config_result.state == "absent"
        injected_results.append(cost_model_result)
        return _CostFacade(cost_model_result)

    def _render_page(
        *,
        scope_label: str | None,
        cost_enabled: bool = False,
        cost_periods: Any = (),
        cost_components: Any = (),
        **_kwargs: Any,
    ) -> str:
        render_states.append(cost_enabled)
        assert [period["id"] for period in cost_periods] == ["2026-08"]
        assert cost_periods[0]["component_ids"] == ("gpt-ptu-prod",)
        assert [component["id"] for component in cost_components] == [
            "gpt-ptu-prod"
        ]
        return f"<html><body>{scope_label}: Cost</body></html>"

    monkeypatch.setenv("AGENTOPS_COST_MODEL", json.dumps(valid_cost_model_payload()))
    monkeypatch.setattr(cockpit_module, "load_cost_model", _load_once)
    monkeypatch.setattr(facade_module, "create_observe_facade", _create_facade)
    monkeypatch.setattr(
        "agentops.agent.observe.ui.render_observe_page",
        _render_page,
    )

    client = TestClient(
        cockpit_module.create_app(
            None,
            mode="hosted",
            observe_scope={
                "version": 1,
                "mode": "projects",
                "project_resource_ids": [
                    "/subscriptions/sub/resourcegroups/rg/providers/"
                    "microsoft.cognitiveservices/accounts/foundry/projects/project-a"
                ],
            },
            auth_context_resolver=_Auth(),
        )
    )
    headers = {"x-ms-client-principal": "allowed"}

    shell = client.get("/observe", headers=headers)
    assert shell.status_code == 200
    assert "Cost" in shell.text
    response = client.post(
        "/api/observe/query",
        headers=headers,
        json={
            "view": "cost",
            "filters": {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-09-01T00:00:00Z",
                "cost_period_id": "2026-08",
                "cost_breakdown": "agents",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["rows"] == [
        {
            "consumer_key": "agent-a",
            "allocated_amount": "8000.00",
            "observed_usage": "2",
            "usage_share": "0.6666666666666666666666666667",
        },
        {
            "consumer_key": "agent-b",
            "allocated_amount": "4000.00",
            "observed_usage": "1",
            "usage_share": "0.3333333333333333333333333333",
        },
    ]
    assert len(load_calls) == 1
    assert len(injected_results) == 1
    assert injected_results[0].state == "valid"
    assert render_states == [True]


def test_multi_period_shell_and_second_period_component_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = valid_multi_period_cost_model_payload()
    client = _cost_client(monkeypatch, payload, [_usage()])
    headers = {"x-ms-client-principal": "allowed"}

    shell = client.get("/observe", headers=headers)
    assert shell.status_code == 200
    assert 'value="2026-08" data-cost-component-ids="gpt-ptu-prod"' in shell.text
    assert (
        'value="2026-09" data-cost-component-ids="gpt-ptu-september"'
        in shell.text
    )

    response = client.post(
        "/api/observe/query",
        headers=headers,
        json={
            "view": "cost",
            "filters": {
                "start": "2026-09-01T00:00:00Z",
                "end": "2026-10-01T00:00:00Z",
                "cost_period_id": "2026-09",
                "cost_component_id": "gpt-ptu-september",
                "cost_breakdown": "agents",
            },
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["period"]["id"] == "2026-09"
    assert data["component_filter"] == "gpt-ptu-september"


def test_clean_cost_url_uses_typed_default_period_without_model_leakage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = valid_cost_model_payload()
    client = _cost_client(monkeypatch, payload, [_usage()])
    headers = {"x-ms-client-principal": "allowed"}

    shell = client.get("/observe", headers=headers)

    assert shell.status_code == 200
    assert (
        '<option value="2026-08" data-cost-component-ids="gpt-ptu-prod" '
        "selected>2026-08</option>"
        in shell.text
    )
    assert '<option value="gpt-ptu-prod">gpt-ptu-prod</option>' in shell.text
    assert payload["periods"][0]["components"][0]["billed_source"] not in shell.text
    assert payload["periods"][0]["components"][0]["billed_total"] not in shell.text
    assert FOUNDRY_RESOURCE_ID not in shell.text
    assert "gpt-prod" not in shell.text

    default_period = "2026-08"
    response = client.post(
        "/api/observe/query",
        headers=headers,
        json={
            "view": "cost",
            "filters": {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-09-01T00:00:00Z",
                "cost_period_id": default_period,
                "cost_breakdown": "agents",
            },
        },
    )

    assert response.status_code == 200
    allocation = response.json()["data"]
    assert allocation["period"]["id"] == default_period
    assert allocation["components"][0]["declared_total"] == "12000.00"
    assert allocation["rows"][0]["consumer_key"] == "agent-a"


def test_tool_and_run_allocations_include_correlated_and_uncorrelated_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations = [
        _usage(
            agent_key="agent-a",
            tool_name="search",
            run_key="run-a",
            input_tokens=1,
            output_tokens=0,
        ),
        _usage(
            agent_key="agent-b",
            tool_name="grounding",
            run_key="run-b",
            input_tokens=3,
            output_tokens=0,
        ),
        _usage(
            agent_key="agent-a",
            tool_name=None,
            run_key=None,
            input_tokens=2,
            output_tokens=0,
        ),
    ]
    client = _cost_client(
        monkeypatch,
        valid_cost_model_payload(),
        observations,
    )
    headers = {"x-ms-client-principal": "allowed"}
    filters = {
        "start": "2026-08-01T00:00:00Z",
        "end": "2026-09-01T00:00:00Z",
        "cost_period_id": "2026-08",
    }

    responses = {}
    for breakdown in ("tools", "runs"):
        response = client.post(
            "/api/observe/query",
            headers=headers,
            json={
                "view": "cost",
                "filters": {**filters, "cost_breakdown": breakdown},
            },
        )
        assert response.status_code == 200
        responses[breakdown] = response.json()["data"]

    assert {row["consumer_key"] for row in responses["tools"]["rows"]} == {
        "search",
        "grounding",
        "__unattributed_tool__",
    }
    assert {row["consumer_key"] for row in responses["runs"]["rows"]} == {
        "run-a",
        "run-b",
        "__unattributed_run__",
    }
    for breakdown, data in responses.items():
        assert data["breakdown"] == breakdown
        assert sum(Decimal(row["amount"]) for row in data["rows"]) == Decimal(
            "12000.00"
        )
        assert data["components"][0]["declared_total"] == "12000.00"
        assert data["components"][0]["unattributed_amount"] == "4000.00"


def _confidence_cost_model_payload() -> dict[str, Any]:
    payload = mixed_currency_cost_model_payload()
    template = deepcopy(payload["periods"][0]["components"][0])
    template["billed_total"] = "100.00"
    components = []
    for component_id, deployment in (
        ("high", "high-deployment"),
        ("medium", "medium-deployment"),
        ("low", "low-deployment"),
        ("unavailable", "unavailable-deployment"),
    ):
        component = deepcopy(template)
        component["id"] = component_id
        component["usage_match"] = {"deployments": [deployment]}
        if component_id in {"low", "unavailable"}:
            component["allocation_key"] = "total_tokens"
            component["fallback_key"] = None
            component["token_weights"] = None
        components.append(component)
    search = deepcopy(payload["periods"][0]["components"][1])
    components.append(search)
    payload["periods"][0]["components"] = components
    return payload


def test_cost_missing_data_states_mixed_currencies_and_partial_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations = [
        _usage(
            deployment="high-deployment",
            agent_key="agent-high",
            input_tokens=1,
            output_tokens=1,
            cache_read_tokens=1,
        ),
        _usage(
            deployment="medium-deployment",
            agent_key="agent-medium",
            input_tokens=1,
            output_tokens=1,
            cache_read_tokens=None,
        ),
        _usage(
            deployment="low-deployment",
            agent_key="agent-low",
            input_tokens=3,
            output_tokens=0,
            coverage_complete=False,
        ),
        _usage(
            deployment="low-deployment",
            agent_key=None,
            tool_name=None,
            run_key=None,
            input_tokens=1,
            output_tokens=0,
            coverage_complete=False,
        ),
        _usage(
            source_resource_id=(
                "/subscriptions/11111111-1111-1111-1111-111111111111/"
                "resourceGroups/AI-Prod/providers/Microsoft.Search/"
                "searchServices/Search-Prod"
            ),
            deployment=None,
            agent_key="agent-search",
            tool_name="product_search",
            input_tokens=None,
            output_tokens=None,
            cache_read_tokens=None,
            tool_invocations=2,
        ),
    ]
    client = _cost_client(
        monkeypatch,
        _confidence_cost_model_payload(),
        observations,
        partial_sources=True,
    )
    response = client.post(
        "/api/observe/query",
        headers={"x-ms-client-principal": "allowed"},
        json={
            "view": "cost",
            "filters": {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-09-01T00:00:00Z",
                "cost_period_id": "2026-08",
                "cost_breakdown": "agents",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    data = payload["data"]
    summaries = {
        summary["component_id"]: summary for summary in data["components"]
    }
    assert {
        component_id: summaries[component_id]["confidence"]
        for component_id in ("high", "medium", "low", "unavailable")
    } == {
        "high": "high",
        "medium": "medium",
        "low": "low",
        "unavailable": "unavailable",
    }
    assert summaries["low"]["unattributed_amount"] == "25.00"
    assert summaries["unavailable"]["unallocated_amount"] == "100.00"
    assert summaries["unavailable"]["rows_total"] == 0
    assert summaries["unavailable"]["next_action"]
    assert {subtotal["currency"] for subtotal in data["currency_subtotals"]} == {
        "USD",
        "EUR",
    }
    assert payload["diagnostics"]["partial_sources"] == 1
    assert payload["diagnostics"]["successful_sources"] == 1
    assert payload["coverage"] == [
        {
            "source_id": "workspace-partial",
            "dimension": "cost_attribution",
            "state": "partial",
            "reason": "One telemetry source was unavailable.",
            "next_action": "Restore access to the unavailable telemetry source.",
            "refreshed_at": "2026-09-01T00:00:00Z",
        }
    ]


@pytest.mark.parametrize(
    ("raw_model", "expected_detail"),
    [
        (None, "not configured"),
        ('{"client_secret":"do-not-echo"}', "invalid"),
    ],
)
def test_unavailable_cost_isolated_from_other_observe_routes(
    monkeypatch: pytest.MonkeyPatch,
    raw_model: str | None,
    expected_detail: str,
) -> None:
    load_calls: list[str | None] = []
    render_states: list[bool] = []
    service = _Service()

    def _load_once(raw: str | None) -> Any:
        load_calls.append(raw)
        return load_cost_model_contract(raw)

    def _render_page(
        *,
        scope_label: str | None,
        cost_enabled: bool = False,
        cost_periods: Any = (),
        cost_components: Any = (),
        **_kwargs: Any,
    ) -> str:
        render_states.append(cost_enabled)
        assert cost_periods == ()
        assert cost_components == ()
        return f"<html><body>{scope_label}</body></html>"

    if raw_model is None:
        monkeypatch.delenv("AGENTOPS_COST_MODEL", raising=False)
    else:
        monkeypatch.setenv("AGENTOPS_COST_MODEL", raw_model)
    monkeypatch.setattr(cockpit_module, "load_cost_model", _load_once)
    monkeypatch.setattr(
        "agentops.agent.observe.ui.render_observe_page",
        _render_page,
    )

    client = TestClient(
        cockpit_module.create_app(
            None,
            mode="hosted",
            observe_scope={
                "version": 1,
                "mode": "projects",
                "project_resource_ids": [
                    "/subscriptions/sub/resourcegroups/rg/providers/"
                    "microsoft.cognitiveservices/accounts/foundry/projects/project-a"
                ],
            },
            observe_service=service,
            auth_context_resolver=_Auth(),
        )
    )
    headers = {"x-ms-client-principal": "allowed"}
    filters = {
        "start": "2026-08-01T00:00:00Z",
        "end": "2026-09-01T00:00:00Z",
    }

    assert client.get("/observe", headers=headers).status_code == 200
    non_cost = [
        client.post(
            "/api/observe/query",
            headers=headers,
            json={"view": view, "filters": filters},
        )
        for view in ("overview", "agents", "models", "tools", "runs", "coverage")
    ]
    cost = client.post(
        "/api/observe/query",
        headers=headers,
        json={"view": "cost", "filters": filters},
    )

    assert [response.status_code for response in non_cost] == [200] * 6
    assert [response.json()["view"] for response in non_cost] == [
        "overview",
        "agents",
        "models",
        "tools",
        "runs",
        "coverage",
    ]
    assert cost.status_code == 422
    detail = cost.json()["detail"]
    assert expected_detail in detail.lower()
    assert "AGENTOPS_COST_MODEL" in detail
    assert "do-not-echo" not in detail
    assert service.query_calls == 6
    assert load_calls == [raw_model]
    assert render_states == [False]


def test_department_attribution_end_to_end_preserves_disabled_parity_and_totals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    scope = {
        "version": 1,
        "mode": "projects",
        "project_resource_ids": [FOUNDRY_RESOURCE_ID + "/projects/project-a"],
    }
    service = _AttributionFacade()
    filters = {
        "start": "2026-08-01T00:00:00Z",
        "end": "2026-09-01T00:00:00Z",
    }
    monkeypatch.delenv("AGENTOPS_ATTRIBUTION_CONFIG", raising=False)
    disabled = TestClient(
        create_app(
            tmp_path,
            mode="local",
            observe_scope=scope,
            observe_service=service,
        )
    )
    baseline = disabled.post(
        "/api/observe/query",
        json={"view": "overview", "filters": filters},
    )
    assert baseline.status_code == 200
    assert disabled.post(
        "/api/observe/attribution",
        json={
            "metric": "usage",
            "group_by": "department",
            "filters": filters,
        },
    ).status_code == 409

    monkeypatch.setenv(
        "AGENTOPS_ATTRIBUTION_CONFIG",
        json.dumps(make_attribution_config_payload()),
    )
    enabled = TestClient(
        create_app(
            tmp_path,
            mode="local",
            observe_scope=scope,
            observe_service=service,
        )
    )
    assert enabled.post(
        "/api/observe/query",
        json={"view": "overview", "filters": filters},
    ).json() == baseline.json()

    usage = enabled.post(
        "/api/observe/attribution",
        json={
            "metric": "usage",
            "group_by": "department",
            "filters": filters,
        },
    )
    assert usage.status_code == 200
    usage_payload = usage.json()
    summary = usage_payload["data"]["summary"]
    assert (
        summary["attributed"]["invocations"]
        + summary["unattributed"]["invocations"]
        == summary["total"]["invocations"]
    )
    assert usage_payload["coverage"][0]["state"] == "partial"
    assert usage_payload["partial_failures"][0]["status"] == "timeout"

    token = usage_payload["data"]["rows"][0]["filter_token"]
    filtered = enabled.post(
        "/api/observe/attribution",
        json={
            "metric": "usage",
            "group_by": "department",
            "filters": {**filters, "department_filter_token": token},
        },
    ).json()
    assert [row["department_id"] for row in filtered["data"]["rows"]] == [
        "engineering"
    ]
    assert filtered["data"]["summary"] == summary

    cost = enabled.post(
        "/api/observe/attribution",
        json={
            "metric": "cost",
            "group_by": "department",
            "filters": {
                **filters,
                "cost_period_id": "2026-08",
                "cost_component_id": "gpt-ptu-prod",
            },
        },
    )
    assert cost.status_code == 200
    cost_summary = cost.json()["data"]["summary"]
    assert (
        Decimal(cost_summary["attributed_amount"])
        + Decimal(cost_summary["unattributed_amount"])
        + Decimal(cost_summary["unallocated_amount"])
        == Decimal(cost_summary["declared_total"])
    )


def test_user_attribution_fixture_conserves_totals_maps_exact_principal_and_is_private(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv(
        "AGENTOPS_ATTRIBUTION_CONFIG",
        json.dumps(make_attribution_config_payload()),
    )
    facade = _AttributionFacade()
    client = TestClient(
        create_app(
            tmp_path,
            mode="hosted",
            observe_scope={
                "version": 1,
                "mode": "projects",
                "project_resource_ids": [FOUNDRY_RESOURCE_ID + "/projects/project-a"],
            },
            observe_service=facade,
            auth_context_resolver=_AttributionAuth(),
        )
    )
    headers = {"x-ms-client-principal": "allowed"}
    filters = {
        "start": "2026-08-01T00:00:00Z",
        "end": "2026-09-01T00:00:00Z",
        "department_filter_token": "opaque-department-token",
    }

    usage = client.post(
        "/api/observe/attribution",
        headers=headers,
        json={"metric": "usage", "group_by": "user", "filters": filters},
    )
    assert usage.status_code == 200
    assert usage.headers["cache-control"] == "private, no-store"
    payload = usage.json()
    rows = payload["data"]["rows"]
    assert [row["user_key"] for row in rows] == sorted(
        row["user_key"] for row in rows
    )
    assert sum(row["usage"]["invocations"] for row in rows) == 10
    summary = payload["data"]["summary"]
    assert (
        summary["attributed"]["invocations"]
        + summary["unattributed"]["invocations"]
        == summary["total"]["invocations"]
    )
    assert rows[0]["raw_identity"] == ATTRIBUTION_FIXTURE_PRINCIPAL
    assert rows[0]["department_id"] == "engineering"
    assert facade.user_contexts[-1]["groups"] == [ATTRIBUTION_FIXTURE_GROUPS[0]]

    selector = rows[0]["filter_token"]
    selected = client.post(
        "/api/observe/attribution",
        headers=headers,
        json={
            "metric": "usage",
            "group_by": "user",
            "filters": {**filters, "user_filter_token": selector},
        },
    )
    assert selected.status_code == 200
    assert [row["user_key"] for row in selected.json()["data"]["rows"]] == [
        make_attribution_user_key(0)
    ]
    serialized_request = json.dumps(facade.attribution_requests[-1])
    assert selector in serialized_request
    assert ATTRIBUTION_FIXTURE_PRINCIPAL not in serialized_request
    assert ATTRIBUTION_FIXTURE_GROUPS[0] not in serialized_request

    cost = client.post(
        "/api/observe/attribution",
        headers=headers,
        json={
            "metric": "cost",
            "group_by": "user",
            "filters": {
                **filters,
                "cost_period_id": "2026-08",
                "cost_component_id": "gpt-ptu-prod",
            },
        },
    )
    assert cost.status_code == 200
    cost_payload = cost.json()
    assert sum(Decimal(row["cost"]["amount"]) for row in cost_payload["data"]["rows"]) == Decimal(
        cost_payload["data"]["summary"]["attributed_amount"]
    )
    cost_summary = cost_payload["data"]["summary"]
    assert (
        Decimal(cost_summary["attributed_amount"])
        + Decimal(cost_summary["unattributed_amount"])
        + Decimal(cost_summary["unallocated_amount"])
        == Decimal(cost_summary["declared_total"])
    )


def test_attribution_coverage_preserves_multi_source_states_without_identity_leakage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    states = [
        "available",
        "partial",
        "not_reported",
        "ambiguous",
        "inaccessible",
        "protected_or_unavailable",
        "error",
    ]
    coverage = [
        {
            "source_id": f"source-{state}",
            "dimension": "user_attribution",
            "state": state,
            "reason": f"Safe reason for {state}.",
            "next_action": f"Safe action for {state}.",
            "refreshed_at": "2026-08-25T12:00:00Z",
            "metric": "usage",
            "attribution_level": "department",
            "component_id": None,
            "eligible_records": None if state == "inaccessible" else 10,
            "identified_records": None if state == "inaccessible" else 8,
            "mapped_records": None if state == "inaccessible" else 7,
            "unattributed_records": None if state == "inaccessible" else 2,
            "ambiguous_records": None if state == "inaccessible" else 1,
            "returned_records": None if state == "inaccessible" else 8,
        }
        for state in states
    ]
    coverage.append(
        {
            **coverage[1],
            "source_id": "source-cost-partial",
            "metric": "cost",
            "component_id": "gpt-ptu-prod",
        }
    )
    partial_failures = [
        {
            "source_id": "source-timeout",
            "status": "timeout",
            "reason": "Safe timeout reason.",
            "next_action": "Retry the source.",
        }
    ]
    monkeypatch.setenv(
        "AGENTOPS_ATTRIBUTION_CONFIG",
        json.dumps(make_attribution_config_payload()),
    )
    client = TestClient(
        create_app(
            tmp_path,
            mode="hosted",
            observe_scope={
                "version": 1,
                "mode": "projects",
                "project_resource_ids": [FOUNDRY_RESOURCE_ID + "/projects/project-a"],
            },
            observe_service=_AttributionFacade(
                coverage=coverage, partial_failures=partial_failures
            ),
            auth_context_resolver=_AttributionAuth(),
        )
    )
    response = client.post(
        "/api/observe/attribution",
        headers={"x-ms-client-principal": "allowed"},
        json={
            "metric": "usage",
            "group_by": "department",
            "filters": {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-09-01T00:00:00Z",
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert [entry["state"] for entry in payload["coverage"][:7]] == states
    assert payload["coverage"][-1]["metric"] == "cost"
    assert payload["coverage"][-1]["component_id"] == "gpt-ptu-prod"
    assert payload["coverage"][4]["eligible_records"] is None
    assert payload["data"]["rows"]
    assert payload["partial_failures"] == partial_failures
    coverage_json = json.dumps(
        {"coverage": payload["coverage"], "partial_failures": payload["partial_failures"]}
    )
    assert ATTRIBUTION_FIXTURE_PRINCIPAL not in coverage_json
    assert ATTRIBUTION_FIXTURE_GROUPS[0] not in coverage_json
    assert "opaque-user-token" not in coverage_json


@pytest.mark.asyncio
async def test_production_attribution_tokens_survive_restart_but_reject_copy_and_rotation() -> None:
    rows = [
        {
            "row_kind": "user",
            "user_key": make_attribution_user_key(0),
            "raw_identity": ATTRIBUTION_FIXTURE_PRINCIPAL,
            "rank": 1,
            "distinct_users": 1,
            "invocations": 4,
            "input_tokens": 40,
            "output_tokens": 20,
            "tool_invocations": 1,
            "active_session_seconds": None,
        }
    ]
    config = load_attribution_config(json.dumps(make_attribution_config_payload())).config
    assert config is not None
    service, scope, _query = _production_attribution_service(rows)
    principal = {
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "user_id": ATTRIBUTION_FIXTURE_PRINCIPAL,
        "user_name": ATTRIBUTION_FIXTURE_PRINCIPAL,
        "groups": [ATTRIBUTION_FIXTURE_GROUPS[0]],
    }
    request = AttributionQueryRequest(
        metric="usage",
        group_by="user",
        filters={
            "start": "2026-08-01T00:00:00Z",
            "end": "2026-09-01T00:00:00Z",
        },
    )
    initial = await service.query_attribution(
        scope,
        request,
        config=config,
        principal_context=principal,
        access_boundary="delegated",
    )
    token = initial.data.rows[0].filter_token
    assert token

    restarted, restarted_scope, restarted_query = _production_attribution_service(rows)
    selected = await restarted.query_attribution(
        restarted_scope,
        request.model_copy(
            update={
                "filters": request.filters.model_copy(
                    update={"user_filter_token": token}
                )
            }
        ),
        config=config,
        principal_context=principal,
        access_boundary="delegated",
    )
    assert len(selected.data.rows) == 1
    assert restarted_query.calls[-1]["selected_user_key"] == make_attribution_user_key(0)

    with pytest.raises(AttributionTokenValidationError) as copied:
        await restarted.query_attribution(
            restarted_scope,
            request.model_copy(
                update={
                    "filters": request.filters.model_copy(
                        update={"user_filter_token": token}
                    )
                }
            ),
            config=config,
            principal_context={**principal, "user_id": "copied-token-user@example.test"},
            access_boundary="delegated",
        )
    assert copied.value.code == "attribution_token_principal_changed"
    assert "authorized account" in copied.value.next_action

    rotated = load_attribution_config(
        json.dumps(make_attribution_config_payload(generation=2))
    ).config
    assert rotated is not None
    with pytest.raises(AttributionTokenValidationError) as stale:
        await restarted.query_attribution(
            restarted_scope,
            request.model_copy(
                update={
                    "filters": request.filters.model_copy(
                        update={"user_filter_token": token}
                    )
                }
            ),
            config=rotated,
            principal_context=principal,
            access_boundary="delegated",
        )
    assert stale.value.code == "attribution_token_generation_changed"
    assert "rotation" in stale.value.next_action


@pytest.mark.asyncio
async def test_production_user_attribution_bounds_502_users_to_499_plus_other() -> None:
    rows = [
        {
            "row_kind": "user",
            "user_key": make_attribution_user_key(index),
            "raw_identity": f"synthetic-user-{index:03d}@example.test",
            "rank": index + 1,
            "distinct_users": 1,
            "invocations": 10 if index < 2 else 1,
            "input_tokens": index + 1,
            "output_tokens": index + 1,
            "tool_invocations": 0,
            "active_session_seconds": None,
        }
        for index in range(502)
    ]
    config = load_attribution_config(json.dumps(make_attribution_config_payload())).config
    assert config is not None
    service, scope, _query = _production_attribution_service(rows)
    result = await service.query_attribution(
        scope,
        AttributionQueryRequest(
            metric="usage",
            group_by="user",
            filters={
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-09-01T00:00:00Z",
            },
        ),
        config=config,
        principal_context={
            "tenant_id": "22222222-2222-2222-2222-222222222222",
            "user_id": ATTRIBUTION_FIXTURE_PRINCIPAL,
            "groups": [],
        },
        access_boundary="delegated",
    )
    assert len(result.data.rows) == 500
    assert sum(row.kind == "user" for row in result.data.rows) == 499
    other = result.data.rows[-1]
    assert other.kind == "other_users"
    assert other.member_count == 3
    assert result.data.summary.distinct_users == 502
    assert result.data.summary.omitted_users == 3
    assert result.data.summary.attributed.invocations == sum(
        row["invocations"] for row in rows
    )
    tied = [
        row.user_key
        for row in result.data.rows
        if row.kind == "user" and row.usage.invocations == 10
    ]
    assert tied == sorted(tied)
