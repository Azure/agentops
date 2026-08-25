"""Hosted Cockpit authentication and runtime integration tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agentops.agent.cockpit import create_app
from agentops.agent.observe.auth import MissingUserAssertionError
from agentops.agent.observe import facade as observe_facade
from agentops.agent.observe import principal as observe_principal


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
    assert "Telemetry coverage" in response.text


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


def test_hosted_auth_context_omits_assertions_and_tokens() -> None:
    client = _hosted_client()

    response = client.get(
        "/api/auth/context",
        headers={"x-ms-client-principal": "allowed"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "tenant_id": "tenant",
        "user_id": "user",
        "display_name": "Allowed User",
        "groups": [],
    }
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

    def build_facade(*, scope, cost_model_result):
        calls["scope"] = scope
        calls["cost_model_state"] = cost_model_result.state
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
