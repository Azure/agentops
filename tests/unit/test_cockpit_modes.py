"""Tests for explicit local and hosted Cockpit runtime modes."""

from __future__ import annotations

import json

import pytest

from agentops.agent.cockpit import load_cockpit_runtime_configuration


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
