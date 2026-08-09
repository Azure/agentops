"""Tests for the Agent 365 registration posture check."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentops.agent.checks import agent_identity as check_module
from agentops.agent.checks.agent_identity import SOURCE_NAME, run_agent_identity_check
from agentops.agent.findings import Category, Severity
from agentops.services.agent_identity import (
    AGENT_ID_ENV,
    AgentIdentityBlueprint,
    AgentIdentityError,
    write_identity_record,
)


@pytest.fixture(autouse=True)
def _clear_agent_id_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(AGENT_ID_ENV, raising=False)


def _write_config(workspace: Path, body: str) -> None:
    (workspace / "agentops.yaml").write_text(body, encoding="utf-8")


def test_registered_workspace_produces_no_findings(tmp_path: Path) -> None:
    write_identity_record(tmp_path, AgentIdentityBlueprint(app_id="app-1"))
    assert run_agent_identity_check(tmp_path) == []


def test_environment_provided_identity_produces_no_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(AGENT_ID_ENV, "app-from-ci")
    assert run_agent_identity_check(tmp_path) == []


def test_unregistered_workspace_warns(tmp_path: Path) -> None:
    findings = run_agent_identity_check(tmp_path)
    assert [f.id for f in findings] == ["agent_identity.not_registered"]
    finding = findings[0]
    assert finding.severity is Severity.WARNING
    assert finding.category is Category.SECURITY
    assert finding.source == SOURCE_NAME
    assert "agentops agent register" in finding.recommendation


def test_graph_is_not_called_when_verify_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verification is opt-in: most tenants lack the Graph consent on day one."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("Graph must not be consulted when verify is off")

    monkeypatch.setattr(check_module, "lookup_blueprint", _boom)
    findings = run_agent_identity_check(tmp_path)
    assert [f.id for f in findings] == ["agent_identity.not_registered"]


def test_verify_reports_blueprint_that_is_not_recorded_locally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path, "identity:\n  verify: true\n  display_name: support-agent\n")
    monkeypatch.setattr(
        check_module,
        "lookup_blueprint",
        lambda name: AgentIdentityBlueprint(app_id="app-1", display_name=name),
    )
    findings = run_agent_identity_check(tmp_path)
    assert [f.id for f in findings] == ["agent_identity.not_recorded"]
    assert findings[0].severity is Severity.INFO
    assert findings[0].evidence["app_id"] == "app-1"


def test_verify_reports_not_registered_when_graph_finds_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path, "identity:\n  verify: true\n")
    monkeypatch.setattr(check_module, "lookup_blueprint", lambda name: None)
    findings = run_agent_identity_check(tmp_path)
    assert [f.id for f in findings] == ["agent_identity.not_registered"]


def test_graph_failure_becomes_a_readable_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing consent must never surface as a stack trace."""

    def _raise(name: str) -> None:
        raise AgentIdentityError("Admin consent is missing for AgentIdentityBlueprint.Read.All.")

    _write_config(tmp_path, "identity:\n  verify: true\n")
    monkeypatch.setattr(check_module, "lookup_blueprint", _raise)
    findings = run_agent_identity_check(tmp_path)
    assert [f.id for f in findings] == ["agent_identity.lookup_failed"]
    assert findings[0].severity is Severity.WARNING
    assert "Admin consent is missing" in findings[0].summary


@pytest.mark.parametrize("raw", ["true", "True", "yes", "on", "1"])
def test_verify_accepts_string_truthy_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    _write_config(tmp_path, f"identity:\n  verify: '{raw}'\n")
    monkeypatch.setattr(
        check_module,
        "lookup_blueprint",
        lambda name: AgentIdentityBlueprint(app_id="app-1"),
    )
    assert [f.id for f in run_agent_identity_check(tmp_path)] == [
        "agent_identity.not_recorded"
    ]


def test_check_falls_back_to_directory_name_for_display_name(tmp_path: Path) -> None:
    workspace = tmp_path / "my-agent"
    workspace.mkdir()
    findings = run_agent_identity_check(workspace)
    assert findings[0].evidence["display_name"] == "my-agent"
