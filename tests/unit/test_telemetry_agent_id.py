"""Tests for the Entra Agent ID stamped on OpenTelemetry resources."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentops.services.agent_identity import (
    AGENT_ID_ATTRIBUTE,
    AGENT_ID_ENV,
    AgentIdentityBlueprint,
    write_identity_record,
)
from agentops.utils.telemetry import _resource_attributes


@pytest.fixture(autouse=True)
def _clear_agent_id_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(AGENT_ID_ENV, raising=False)


def test_resource_always_carries_service_identity() -> None:
    attributes = _resource_attributes()
    assert attributes["service.name"] == "agentops"
    assert isinstance(attributes["service.version"], str)


def test_agent_id_is_omitted_when_not_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unregistered agent must not emit an empty attribute.

    Downstream queries filter on the attribute's presence, so emitting it
    blank would make unregistered agents indistinguishable from registered
    ones whose id failed to resolve.
    """

    monkeypatch.chdir(tmp_path)
    assert AGENT_ID_ATTRIBUTE not in _resource_attributes()


def test_agent_id_is_stamped_from_the_workspace_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_identity_record(tmp_path, AgentIdentityBlueprint(app_id="app-1"))
    monkeypatch.chdir(tmp_path)
    assert _resource_attributes()[AGENT_ID_ATTRIBUTE] == "app-1"


def test_agent_id_is_stamped_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(AGENT_ID_ENV, "app-from-ci")
    assert _resource_attributes()[AGENT_ID_ATTRIBUTE] == "app-from-ci"


def test_identity_failures_never_break_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tracing is mandatory, identity is optional; the former must survive."""

    import agentops.services.agent_identity as identity_service

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("identity subsystem exploded")

    monkeypatch.setattr(identity_service, "resolve_agent_id", _boom)
    attributes = _resource_attributes()
    assert attributes["service.name"] == "agentops"
    assert AGENT_ID_ATTRIBUTE not in attributes
