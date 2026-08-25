"""Tests for explicit local and hosted Cockpit runtime modes."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentops.agent import cockpit as cockpit_module
from agentops.agent.cockpit import create_app, load_cockpit_runtime_configuration
from agentops.agent.observe import facade as facade_module
from fixtures.cost import (
    overlapping_cost_model_payload,
    secret_shaped_cost_model_payload,
    valid_cost_model_payload,
    valid_multi_period_cost_model_payload,
)


_SCOPE = {
    "version": 1,
    "mode": "projects",
    "project_resource_ids": [
        "/subscriptions/sub/resourceGroups/rg/providers/"
        "Microsoft.CognitiveServices/accounts/foundry/projects/project-a"
    ],
}
_FILTERS = {
    "start": "2026-08-01T00:00:00Z",
    "end": "2026-09-01T00:00:00Z",
}


class _Auth:
    def __call__(self, headers: Any) -> dict[str, Any]:
        if headers.get("x-ms-client-principal") != "allowed":
            raise PermissionError("authentication required")
        return {"tenant_id": "tenant", "user_id": "user", "groups": []}


class _ObserveService:
    def query(self, *, view: str, **_: Any) -> dict[str, Any]:
        return {
            "view": view,
            "data": [],
            "coverage": [],
            "diagnostics": {"source_count": 0},
            "refreshed_at": "2026-08-24T00:00:00Z",
        }


def _incompatible_cost_model() -> dict[str, Any]:
    payload = deepcopy(valid_cost_model_payload())
    component = payload["periods"][0]["components"][0]
    component["type"] = "search"
    return payload


_COST_MODEL_CASES = [
    pytest.param(None, "absent", "not configured", None, id="absent"),
    pytest.param(
        json.dumps(valid_cost_model_payload()),
        "valid",
        None,
        None,
        id="valid",
    ),
    pytest.param(
        '{"version":1,"sensitive-fragment":',
        "invalid",
        "valid JSON",
        "sensitive-fragment",
        id="malformed",
    ),
    pytest.param(
        ("x" * (32 * 1024 + 1)) + "oversized-sensitive-tail",
        "invalid",
        "32 KiB",
        "oversized-sensitive-tail",
        id="oversized",
    ),
    pytest.param(
        json.dumps(overlapping_cost_model_payload()),
        "invalid",
        "overlap",
        None,
        id="overlapping",
    ),
    pytest.param(
        json.dumps(_incompatible_cost_model()),
        "invalid",
        "compatible combination",
        None,
        id="incompatible",
    ),
    pytest.param(
        json.dumps(secret_shaped_cost_model_payload()),
        "invalid",
        "secret-shaped",
        "do-not-echo",
        id="secret-shaped",
    ),
]


def test_local_mode_is_the_behavior_safe_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENTOPS_COCKPIT_MODE", raising=False)
    monkeypatch.delenv("AGENTOPS_OBSERVE_SCOPE", raising=False)

    config = load_cockpit_runtime_configuration()

    assert config.mode == "local"
    assert config.observe_scope is None


def test_hosted_mode_requires_observe_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTOPS_COCKPIT_MODE", "hosted")
    monkeypatch.delenv("AGENTOPS_OBSERVE_SCOPE", raising=False)

    with pytest.raises(ValueError, match="AGENTOPS_OBSERVE_SCOPE"):
        load_cockpit_runtime_configuration()


def test_hosted_mode_loads_versioned_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = {
        "version": 1,
        "mode": "projects",
        "project_resource_ids": [
            "/subscriptions/sub/resourceGroups/rg/providers/"
            "Microsoft.CognitiveServices/accounts/foundry/projects/project-a"
        ],
    }
    monkeypatch.setenv("AGENTOPS_COCKPIT_MODE", "hosted")
    monkeypatch.setenv("AGENTOPS_OBSERVE_SCOPE", json.dumps(scope))

    config = load_cockpit_runtime_configuration()

    assert config.mode == "hosted"
    assert config.observe_scope is not None
    assert config.observe_scope["version"] == 1
    assert config.observe_scope["mode"] == "projects"
    assert config.observe_scope["project_resource_ids"] == [
        scope["project_resource_ids"][0].lower()
    ]


def test_unknown_mode_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTOPS_COCKPIT_MODE", "remote")

    with pytest.raises(ValueError, match="local or hosted"):
        load_cockpit_runtime_configuration()


@pytest.mark.parametrize("mode", ["local", "hosted"])
@pytest.mark.parametrize(
    ("raw_model", "expected_state", "expected_error", "sensitive_marker"),
    _COST_MODEL_CASES,
)
def test_cost_model_startup_states_have_local_hosted_parity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
    raw_model: str | None,
    expected_state: str,
    expected_error: str | None,
    sensitive_marker: str | None,
) -> None:
    injected_states: list[str] = []
    rendered_states: list[bool] = []

    def _factory(*, scope: Any, cost_model_result: Any) -> _ObserveService:
        assert scope["mode"] == "projects"
        injected_states.append(cost_model_result.state)
        return _ObserveService()

    def _render(
        *,
        scope_label: str | None,
        cost_enabled: bool = False,
        cost_periods: Any = (),
        cost_components: Any = (),
    ) -> str:
        rendered_states.append(cost_enabled)
        if cost_enabled:
            assert [period["id"] for period in cost_periods] == ["2026-08"]
            assert cost_periods[0]["component_ids"] == ("gpt-ptu-prod",)
            assert [component["id"] for component in cost_components] == [
                "gpt-ptu-prod"
            ]
        else:
            assert cost_periods == ()
            assert cost_components == ()
        return f"<html><body>{scope_label}</body></html>"

    if raw_model is None:
        monkeypatch.delenv("AGENTOPS_COST_MODEL", raising=False)
    elif len(raw_model.encode("utf-8")) > 32 * 1024:
        original_getenv = cockpit_module.os.getenv

        def _getenv(name: str, default: str | None = None) -> str | None:
            if name == "AGENTOPS_COST_MODEL":
                return raw_model
            return original_getenv(name, default)

        monkeypatch.setattr(cockpit_module.os, "getenv", _getenv)
    else:
        monkeypatch.setenv("AGENTOPS_COST_MODEL", raw_model)
    monkeypatch.setattr(facade_module, "create_observe_facade", _factory)
    monkeypatch.setattr(
        "agentops.agent.observe.ui.render_observe_page",
        _render,
    )

    app = create_app(
        tmp_path if mode == "local" else None,
        mode=mode,  # type: ignore[arg-type]
        observe_scope=_SCOPE,
        auth_context_resolver=_Auth() if mode == "hosted" else None,
    )
    client = TestClient(app)
    headers = {"x-ms-client-principal": "allowed"} if mode == "hosted" else {}

    assert client.get("/observe", headers=headers).status_code == 200
    overview = client.post(
        "/api/observe/query",
        headers=headers,
        json={"view": "overview", "filters": _FILTERS},
    )
    cost = client.post(
        "/api/observe/query",
        headers=headers,
        json={
            "view": "cost",
            "filters": {**_FILTERS, "cost_period_id": "2026-08"},
        },
    )

    assert overview.status_code == 200
    assert injected_states == [expected_state]
    assert rendered_states == [expected_state == "valid"]
    assert cost.status_code == (200 if expected_state == "valid" else 422)
    if expected_error is not None:
        detail = cost.json()["detail"]
        assert expected_error.lower() in detail.lower()
        assert "AGENTOPS_COST_MODEL" in detail
        assert "restart Cockpit" in detail
        if sensitive_marker is not None:
            assert sensitive_marker not in detail


def test_valid_multi_period_startup_exposes_components_for_each_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered_periods: list[Any] = []

    def _render(
        *,
        scope_label: str | None,
        cost_enabled: bool = False,
        cost_periods: Any = (),
        cost_components: Any = (),
    ) -> str:
        assert scope_label == "Projects (1)"
        assert cost_enabled is True
        assert [component["id"] for component in cost_components] == ["gpt-ptu-prod"]
        rendered_periods.extend(cost_periods)
        return "<html><body>Cost</body></html>"

    monkeypatch.setenv(
        "AGENTOPS_COST_MODEL",
        json.dumps(valid_multi_period_cost_model_payload()),
    )
    monkeypatch.setattr(
        "agentops.agent.observe.ui.render_observe_page",
        _render,
    )

    client = TestClient(
        create_app(
            None,
            mode="hosted",
            observe_scope=_SCOPE,
            observe_service=_ObserveService(),
            auth_context_resolver=_Auth(),
        )
    )

    assert client.get(
        "/observe",
        headers={"x-ms-client-principal": "allowed"},
    ).status_code == 200
    assert rendered_periods == [
        {
            "id": "2026-08",
            "label": "2026-08",
            "component_ids": ("gpt-ptu-prod",),
        },
        {
            "id": "2026-09",
            "label": "2026-09",
            "component_ids": ("gpt-ptu-september",),
        },
    ]


@pytest.mark.parametrize("mode", ["local", "hosted"])
def test_cost_model_changes_require_restart_and_removal_disables_new_apps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
) -> None:
    services: list[_ObserveService] = []

    def _factory(*, scope: Any, cost_model_result: Any) -> _ObserveService:
        service = _ObserveService()
        service.cost_model_state = cost_model_result.state
        services.append(service)
        return service

    monkeypatch.setattr(facade_module, "create_observe_facade", _factory)
    monkeypatch.setenv("AGENTOPS_COST_MODEL", json.dumps(valid_cost_model_payload()))

    def _start() -> TestClient:
        return TestClient(
            create_app(
                tmp_path if mode == "local" else None,
                mode=mode,  # type: ignore[arg-type]
                observe_scope=_SCOPE,
                auth_context_resolver=_Auth() if mode == "hosted" else None,
            )
        )

    headers = {"x-ms-client-principal": "allowed"} if mode == "hosted" else {}
    request = {
        "view": "cost",
        "filters": {**_FILTERS, "cost_period_id": "2026-08"},
    }
    valid_app = _start()
    monkeypatch.delenv("AGENTOPS_COST_MODEL")

    assert valid_app.post(
        "/api/observe/query", headers=headers, json=request
    ).status_code == 200
    removed_app = _start()
    assert removed_app.post(
        "/api/observe/query", headers=headers, json=request
    ).status_code == 422

    monkeypatch.setenv("AGENTOPS_COST_MODEL", json.dumps(valid_cost_model_payload()))
    assert removed_app.post(
        "/api/observe/query", headers=headers, json=request
    ).status_code == 422
    restarted_app = _start()
    assert restarted_app.post(
        "/api/observe/query", headers=headers, json=request
    ).status_code == 200
    assert [service.cost_model_state for service in services] == [
        "valid",
        "absent",
        "valid",
    ]
