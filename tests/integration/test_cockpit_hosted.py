"""Hosted Cockpit authentication and runtime integration tests."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import json
from typing import Any

from fastapi.testclient import TestClient
import pytest

from agentops.agent.cockpit import create_app
from agentops.agent.observe.auth import MissingUserAssertionError
from agentops.agent.observe import facade as observe_facade
from agentops.agent.observe.attribution import SingletonAttributionError
from agentops.agent.observe.cache import ObserveCache
from agentops.agent.observe.facade import ObserveFacade
from agentops.agent.observe import principal as observe_principal
from agentops.core.attribution import (
    AttributionUsage,
    AttributionViewData,
    DepartmentAttributionRow,
    UsageAttributionSummary,
    load_attribution_config,
)
from agentops.core.observe import (
    AttributionResponse,
    ObserveScope,
    QueryDiagnostics,
    ResultBounds,
)
from fixtures.observe import make_attribution_config_payload


class _FakeAuth:
    def __call__(self, headers: Any) -> dict[str, Any]:
        if headers.get("x-ms-client-principal") != "allowed":
            raise PermissionError("Microsoft Entra authentication is required.")
        return {
            "tenant_id": "tenant",
            "user_id": "user",
            "display_name": "Allowed User",
            "groups": [],
        }


class _FakeObserveService:
    def discover(self, *, refresh: bool, user_context: dict[str, Any]) -> dict[str, Any]:
        return {
            "foundry_resources": [],
            "projects": [],
            "telemetry_sources": [],
            "partial_failures": [],
            "discovered_at": "2026-08-21T00:00:00Z",
            "expires_at": "2026-08-21T00:15:00Z",
            "refresh": refresh,
        }

    def query(
        self,
        *,
        view: str,
        filters: dict[str, Any],
        refresh: bool,
        user_context: dict[str, Any],
    ) -> dict[str, Any]:
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
        self,
        *,
        agent_key: str,
        filters: dict[str, Any],
        refresh: bool,
        user_context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "agent_key": agent_key,
            "filters": filters,
            "refresh": refresh,
            "trends": [],
        }

    def trace_content(
        self,
        *,
        request: dict[str, Any],
        user_context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "trace_id": request["trace_id"],
            "span_id": request.get("span_id"),
            "source_resource_id": request["source_resource_id"],
            "protection_state": "protected_or_unavailable",
        }


class _NeverAggregateAttributionService:
    def __init__(self, response: AttributionResponse | Exception) -> None:
        self.response = response
        self.calls = 0

    async def query_attribution(self, *_args: Any, **_kwargs: Any) -> AttributionResponse:
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _attribution_response(*, delegated: bool, member_count: int = 1) -> AttributionResponse:
    now = datetime(2026, 8, 25, tzinfo=timezone.utc)
    usage = AttributionUsage(
        invocations=1,
        input_tokens=10,
        output_tokens=5,
        tool_invocations=0,
        active_session_seconds=None,
    )
    zero = AttributionUsage(
        invocations=0,
        input_tokens=None,
        output_tokens=None,
        tool_invocations=None,
        active_session_seconds=None,
    )
    return AttributionResponse(
        data=AttributionViewData(
            metric="usage",
            group_by="department",
            access_boundary="delegated" if delegated else "aggregate",
            rows=[
                DepartmentAttributionRow(
                    kind="department",
                    department_id="engineering",
                    department_label="Engineering",
                    filter_token="opaque-department-token",
                    member_count=member_count,
                    usage=usage,
                    cost=None,
                    mapping_state="mapped",
                )
            ],
            summary=UsageAttributionSummary(
                metric="usage",
                total=usage,
                attributed=usage,
                unattributed=zero,
                distinct_users=member_count,
                omitted_users=0,
            ),
            primary_measure="invocations",
            calculated_at=now,
        ),
        coverage=[],
        partial_failures=[],
        diagnostics=QueryDiagnostics(
            started_at=now,
            completed_at=now,
            duration_ms=0,
            source_count=1,
            successful_sources=1,
            partial_sources=0,
            failed_sources=0,
            cache_status="bypass" if delegated else "miss",
        ),
        refreshed_at=now,
        cache_status="bypass" if delegated else "miss",
        bounds=ResultBounds(rows_shown=1, rows_total_in_scope=1),
    )


def _production_facade(
    service: _NeverAggregateAttributionService,
) -> ObserveFacade:
    scope = ObserveScope(
        mode="projects",
        project_resource_ids=[
            "/subscriptions/sub/resourceGroups/rg/providers/"
            "Microsoft.CognitiveServices/accounts/foundry/projects/project-a"
        ],
    )
    facade = ObserveFacade(
        scope=scope,
        tenant_id="22222222-2222-2222-2222-222222222222",
        application_client_id="synthetic-app",
        uami_client_id="synthetic-uami",
        discovery_client=object(),
        query_client=object(),
        aggregate_credential=object(),
        cache=ObserveCache(ttl_seconds=60),
        attribution_config_result=load_attribution_config(
            json.dumps(make_attribution_config_payload(singleton=True))
        ),
    )
    facade._service = service
    return facade


def _hosted_client(workspace: Path | None = None) -> TestClient:
    scope = {
        "version": 1,
        "mode": "projects",
        "project_resource_ids": [
            "/subscriptions/sub/resourcegroups/rg/providers/"
            "microsoft.cognitiveservices/accounts/foundry/projects/project-a"
        ],
    }
    app = create_app(
        workspace,
        mode="hosted",
        observe_scope=scope,
        observe_service=_FakeObserveService(),
        auth_context_resolver=_FakeAuth(),
    )
    return TestClient(app)


def test_healthz_is_the_only_anonymous_hosted_route() -> None:
    client = _hosted_client()

    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/api/runtime").status_code == 401
    assert client.get("/").status_code == 401
    assert client.get("/openapi.json").status_code == 404


def test_authenticated_hosted_root_renders_observe_shell() -> None:
    response = _hosted_client().get(
        "/",
        headers={"x-ms-client-principal": "allowed"},
    )

    assert response.status_code == 200
    assert "AgentOps Observe" in response.text
    assert "Apply filters" in response.text
    assert "Telemetry coverage" not in response.text


def test_hosted_runtime_has_no_local_history_dependency() -> None:
    client = _hosted_client()

    response = client.get(
        "/api/runtime",
        headers={"x-ms-client-principal": "allowed"},
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "hosted"
    assert response.json()["local_history_available"] is False
    assert (
        client.get(
            "/api/history",
            headers={"x-ms-client-principal": "allowed"},
        ).status_code
        == 404
    )


def test_hosted_auth_context_exposes_only_non_sensitive_status() -> None:
    client = _hosted_client()

    response = client.get(
        "/api/auth/context",
        headers={"x-ms-client-principal": "allowed"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "authenticated": True,
        "tenant_authorized": True,
    }
    assert "user" not in response.text.lower()
    assert "group" not in response.text.lower()
    assert "token" not in response.text.lower()
    assert "assertion" not in response.text.lower()


def test_trace_content_is_non_cacheable() -> None:
    client = _hosted_client()
    response = client.post(
        "/api/observe/trace-content",
        headers={"x-ms-client-principal": "allowed"},
        json={
            "source_resource_id": (
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.Insights/components/appi"
            ),
            "trace_id": "trace-1",
            "span_id": "span-1",
        },
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["protection_state"] == "protected_or_unavailable"


def test_hosted_user_attribution_missing_obo_never_falls_back_to_aggregate(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "AGENTOPS_ATTRIBUTION_CONFIG",
        json.dumps(make_attribution_config_payload(singleton=True)),
    )
    aggregate = _NeverAggregateAttributionService(
        _attribution_response(delegated=False, member_count=2)
    )
    facade = _production_facade(aggregate)
    client = TestClient(
        create_app(
            None,
            mode="hosted",
            observe_scope=facade._scope,
            observe_service=facade,
            auth_context_resolver=_FakeAuth(),
        )
    )
    response = client.post(
        "/api/observe/attribution",
        headers={"x-ms-client-principal": "allowed"},
        json={
            "metric": "usage",
            "group_by": "user",
            "filters": {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-09-01T00:00:00Z",
            },
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "attribution_delegated_access_unavailable"
    assert "delegated" in response.json()["message"].lower()
    assert response.json()["next_action"]
    assert aggregate.calls == 0
    assert facade._cache._entries == {}


def test_hosted_singleton_discards_aggregate_and_returns_private_delegated_result(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "AGENTOPS_ATTRIBUTION_CONFIG",
        json.dumps(make_attribution_config_payload(singleton=True)),
    )
    aggregate = _NeverAggregateAttributionService(
        SingletonAttributionError("delegated access required")
    )
    facade = _production_facade(aggregate)
    delegated_calls: list[object] = []

    async def delegated(request, **_kwargs):
        delegated_calls.append(request)
        return _attribution_response(delegated=True)

    monkeypatch.setattr(facade, "_query_attribution_delegated", delegated)
    client = TestClient(
        create_app(
            None,
            mode="hosted",
            observe_scope=facade._scope,
            observe_service=facade,
            auth_context_resolver=_FakeAuth(),
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
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["data"]["access_boundary"] == "delegated"
    assert aggregate.calls == 1
    assert len(delegated_calls) == 1
    assert facade._cache._entries == {}


def test_hosted_singleton_delegated_failure_is_private_and_stable(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "AGENTOPS_ATTRIBUTION_CONFIG",
        json.dumps(make_attribution_config_payload(singleton=True)),
    )
    aggregate = _NeverAggregateAttributionService(
        SingletonAttributionError("delegated access required")
    )
    facade = _production_facade(aggregate)

    async def delegated(*_args, **_kwargs):
        raise RuntimeError("tenant-specific provider failure")

    monkeypatch.setattr(facade, "_query_attribution_delegated", delegated)
    client = TestClient(
        create_app(
            None,
            mode="hosted",
            observe_scope=facade._scope,
            observe_service=facade,
            auth_context_resolver=_FakeAuth(),
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

    assert response.status_code == 503
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == {
        "code": "attribution_delegated_query_failed",
        "message": "Delegated attribution could not be completed.",
        "next_action": "Verify delegated access and retry the attribution query.",
    }
    assert "tenant-specific" not in response.text
    assert facade._cache._entries == {}


@pytest.mark.parametrize("fail_on", ["get", "set"])
def test_hosted_attribution_cache_failure_is_stable_and_fail_closed(
    monkeypatch,
    fail_on: str,
) -> None:
    monkeypatch.setenv(
        "AGENTOPS_ATTRIBUTION_CONFIG",
        json.dumps(make_attribution_config_payload(singleton=True)),
    )
    aggregate = _NeverAggregateAttributionService(
        _attribution_response(delegated=False, member_count=2)
    )
    facade = _production_facade(aggregate)

    class FailingCache:
        def get(self, _key, *, bypass=False):
            if fail_on == "get":
                raise RuntimeError("tenant-specific cache details")
            return None

        def set(self, _key, _value):
            if fail_on == "set":
                raise RuntimeError("tenant-specific cache details")

    facade._cache = FailingCache()
    client = TestClient(
        create_app(
            None,
            mode="hosted",
            observe_scope=facade._scope,
            observe_service=facade,
            auth_context_resolver=_FakeAuth(),
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

    assert response.status_code == 503
    assert response.json()["code"] == "attribution_cache_unavailable"
    assert "tenant-specific" not in response.text
    assert aggregate.calls == (0 if fail_on == "get" else 1)


def test_observe_routes_are_present_in_application_schema() -> None:
    client = _hosted_client()
    schema = client.app.openapi()

    for path in (
        "/api/observe/discovery",
        "/api/observe/query",
        "/api/observe/agent-detail",
        "/api/observe/trace-content",
    ):
        assert path in schema["paths"]


def test_hosted_mode_composes_default_auth_and_observe_factories(monkeypatch) -> None:
    service = _FakeObserveService()
    calls: dict[str, Any] = {}

    def build_resolver():
        calls["auth"] = True
        return _FakeAuth()

    def build_facade(*, scope, cost_model_result, attribution_config_result):
        calls["scope"] = scope
        calls["cost_model_state"] = cost_model_result.state
        calls["attribution_state"] = attribution_config_result.state
        return service

    monkeypatch.setattr(observe_principal, "build_easy_auth_resolver", build_resolver)
    monkeypatch.setattr(observe_facade, "create_observe_facade", build_facade)
    scope = {
        "version": 1,
        "mode": "projects",
        "project_resource_ids": [
            "/subscriptions/sub/resourceGroups/rg/providers/"
            "Microsoft.CognitiveServices/accounts/foundry/projects/project-a"
        ],
    }

    client = TestClient(create_app(None, mode="hosted", observe_scope=scope))
    response = client.get(
        "/api/observe/discovery",
        headers={"x-ms-client-principal": "allowed"},
    )

    assert response.status_code == 200
    assert calls["auth"] is True
    assert calls["scope"]["mode"] == "projects"
    assert calls["cost_model_state"] == "absent"
    assert calls["attribution_state"] in {"absent", "valid"}


def test_hosted_authorization_failure_maps_to_forbidden() -> None:
    class Forbidden(PermissionError):
        http_status = 403

    def resolver(_headers):
        raise Forbidden("The signed-in user is outside the configured boundary.")

    scope = {
        "version": 1,
        "mode": "projects",
        "project_resource_ids": [
            "/subscriptions/sub/resourceGroups/rg/providers/"
            "Microsoft.CognitiveServices/accounts/foundry/projects/project-a"
        ],
    }
    client = TestClient(
        create_app(
            None,
            mode="hosted",
            observe_scope=scope,
            observe_service=_FakeObserveService(),
            auth_context_resolver=resolver,
        )
    )

    response = client.get("/api/runtime", headers={"x-ms-client-principal": "present"})

    assert response.status_code == 403
    assert "outside the configured boundary" in response.json()["detail"]


def test_observe_request_models_reject_extra_fields() -> None:
    client = _hosted_client()

    response = client.post(
        "/api/observe/query",
        headers={"x-ms-client-principal": "allowed"},
        json={
            "view": "overview",
            "filters": {},
            "refresh": False,
            "unexpected": "rejected",
        },
    )

    assert response.status_code == 422


def test_missing_agent_detail_maps_to_not_found() -> None:
    class MissingAgentService(_FakeObserveService):
        def agent_detail(self, **_kwargs: Any) -> None:
            return None

    scope = {
        "version": 1,
        "mode": "projects",
        "project_resource_ids": [
            "/subscriptions/sub/resourceGroups/rg/providers/"
            "Microsoft.CognitiveServices/accounts/foundry/projects/project-a"
        ],
    }
    client = TestClient(
        create_app(
            None,
            mode="hosted",
            observe_scope=scope,
            observe_service=MissingAgentService(),
            auth_context_resolver=_FakeAuth(),
        )
    )

    response = client.post(
        "/api/observe/agent-detail",
        headers={"x-ms-client-principal": "allowed"},
        json={
            "agent_key": "missing",
            "filters": {
                "start": "2026-08-20T00:00:00Z",
                "end": "2026-08-21T00:00:00Z",
            },
        },
    )

    assert response.status_code == 404


def test_missing_delegated_assertion_maps_to_forbidden() -> None:
    class MissingAssertionService(_FakeObserveService):
        def trace_content(self, **_kwargs: Any) -> None:
            raise MissingUserAssertionError("A delegated user assertion is required.")

    scope = {
        "version": 1,
        "mode": "projects",
        "project_resource_ids": [
            "/subscriptions/sub/resourceGroups/rg/providers/"
            "Microsoft.CognitiveServices/accounts/foundry/projects/project-a"
        ],
    }
    client = TestClient(
        create_app(
            None,
            mode="hosted",
            observe_scope=scope,
            observe_service=MissingAssertionService(),
            auth_context_resolver=_FakeAuth(),
        )
    )

    response = client.post(
        "/api/observe/trace-content",
        headers={"x-ms-client-principal": "allowed"},
        json={
            "source_resource_id": (
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.Insights/components/appi"
            ),
            "trace_id": "trace-1",
        },
    )

    assert response.status_code == 403
