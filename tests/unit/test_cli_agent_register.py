"""CLI tests for `agentops agent register`.

The command is exercised through Typer's CliRunner. Microsoft Graph is never
contacted: the happy path patches the service function, and every other path
either fails input resolution or is a dry run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.services import agent_identity as identity_service
from agentops.services.agent_identity import (
    AgentIdentityBlueprint,
    AgentIdentityError,
    identity_record_path,
    read_identity_record,
)

runner = CliRunner()


def _write_config(workspace: Path, body: str) -> None:
    (workspace / "agentops.yaml").write_text(body, encoding="utf-8")


def _invoke(workspace: Path, *args: str):
    return runner.invoke(app, ["agent", "register", "-w", str(workspace), *args])


def test_register_explain_renders_the_manual() -> None:
    result = runner.invoke(app, ["agent", "register", "explain"])
    assert result.exit_code == 0
    assert "register" in result.output.lower()


def test_register_fails_without_a_display_name(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "--sponsor", "paulo@contoso.com", "--dry-run")
    assert result.exit_code == 1
    assert "display name" in result.output.lower()


def test_register_fails_without_a_sponsor(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "--display-name", "support-agent", "--dry-run")
    assert result.exit_code == 1
    assert "sponsor" in result.output.lower()


def test_dry_run_reports_inputs_without_writing_a_record(tmp_path: Path) -> None:
    result = _invoke(
        tmp_path,
        "--display-name",
        "support-agent",
        "--sponsor",
        "paulo@contoso.com",
        "--dry-run",
    )
    assert result.exit_code == 0
    assert "support-agent" in result.output
    assert "paulo@contoso.com" in result.output
    assert "Dry run" in result.output
    assert not identity_record_path(tmp_path).exists()


def test_dry_run_resolves_inputs_from_config(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "identity:\n  display_name: config-agent\n  sponsor: owner@contoso.com\n",
    )
    result = _invoke(tmp_path, "--dry-run")
    assert result.exit_code == 0
    assert "config-agent" in result.output
    assert "owner@contoso.com" in result.output


def test_register_writes_the_identity_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        identity_service,
        "register_blueprint",
        lambda name, *, sponsor: (
            AgentIdentityBlueprint(
                app_id="app-1", object_id="object-1", display_name=name
            ),
            True,
        ),
    )
    result = _invoke(
        tmp_path,
        "--display-name",
        "support-agent",
        "--sponsor",
        "paulo@contoso.com",
    )
    assert result.exit_code == 0
    assert "Registered" in result.output
    assert "app-1" in result.output
    record = read_identity_record(tmp_path)
    assert record is not None
    assert record["app_id"] == "app-1"
    assert record["created"] is True


def test_register_reports_reuse_when_blueprint_already_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        identity_service,
        "register_blueprint",
        lambda name, *, sponsor: (
            AgentIdentityBlueprint(app_id="app-1", display_name=name),
            False,
        ),
    )
    result = _invoke(
        tmp_path,
        "--display-name",
        "support-agent",
        "--sponsor",
        "paulo@contoso.com",
    )
    assert result.exit_code == 0
    assert "Reused existing" in result.output
    assert read_identity_record(tmp_path)["created"] is False


def test_graph_failure_is_reported_without_a_stack_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(name: str, *, sponsor: str):
        raise AgentIdentityError("Admin consent is missing for AgentIdentityBlueprint.Create.")

    monkeypatch.setattr(identity_service, "register_blueprint", _raise)
    result = _invoke(
        tmp_path,
        "--display-name",
        "support-agent",
        "--sponsor",
        "paulo@contoso.com",
    )
    assert result.exit_code == 1
    assert "Admin consent is missing" in result.output
    assert "Traceback" not in result.output
