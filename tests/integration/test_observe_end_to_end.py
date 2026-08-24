"""End-to-end hosted Observe API tests with deterministic fakes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.testclient import TestClient

from agentops.agent.cockpit import create_app
from agentops.agent.observe.ui import render_models_usage_table


class _Auth:
    def __call__(self, headers: Any) -> dict[str, Any]:
        if headers.get("x-ms-client-principal") != "allowed":
            raise PermissionError("authentication required")
        return {"tenant_id": "tenant", "user_id": "user", "groups": []}


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
