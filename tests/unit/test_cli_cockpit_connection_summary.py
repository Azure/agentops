"""Tests for the cockpit startup connection summary helper.

The cockpit prints a small "where is this pointed?" block above the
doctor hint so the operator can confirm the Foundry project + agent
before opening the browser. These tests pin the resolution chain:
agentops.yaml > .agentops/run.yaml > AZURE_AI_FOUNDRY_PROJECT_ENDPOINT.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentops.cli.app import _summarize_cockpit_connection


@pytest.fixture(autouse=True)
def _clear_endpoint_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test owns the env var; never inherit from the shell."""
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)


def _row(rows: list[tuple[str, str]], label: str) -> str:
    for entry_label, entry_value in rows:
        if entry_label == label:
            return entry_value
    raise AssertionError(f"row {label!r} not found in {rows!r}")


def test_empty_workspace_returns_not_configured_hints(tmp_path: Path) -> None:
    rows = _summarize_cockpit_connection(tmp_path)
    project = _row(rows, "Foundry project")
    agent = _row(rows, "agent")
    assert "not configured" in project
    assert "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT" in project
    assert "not configured" in agent
    assert "agentops.yaml" in agent


def test_agentops_yaml_wins(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\n"
        "agent: my-rag:3\n"
        "dataset: ./qa.jsonl\n"
        "project_endpoint: https://acct.services.ai.azure.com/api/projects/myproj\n",
        encoding="utf-8",
    )

    rows = _summarize_cockpit_connection(tmp_path)

    assert (
        _row(rows, "Foundry project")
        == "https://acct.services.ai.azure.com/api/projects/myproj"
    )
    assert _row(rows, "agent") == "my-rag:3"


def test_env_var_used_when_agentops_yaml_has_no_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: my-agent:2\ndataset: ./data.jsonl\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://acct.services.ai.azure.com/api/projects/from-env",
    )

    rows = _summarize_cockpit_connection(tmp_path)

    project = _row(rows, "Foundry project")
    assert project == "https://acct.services.ai.azure.com/api/projects/from-env"
    assert "(from env)" not in project
    assert _row(rows, "agent") == "my-agent:2"


def test_legacy_run_yaml_endpoint(tmp_path: Path) -> None:
    workspace_dir = tmp_path / ".agentops"
    workspace_dir.mkdir()
    (workspace_dir / "run.yaml").write_text(
        "target:\n"
        "  endpoint:\n"
        "    agent_id: legacy-agent:2\n"
        "    project_endpoint: https://legacy.services.ai.azure.com/api/projects/lp\n",
        encoding="utf-8",
    )

    rows = _summarize_cockpit_connection(tmp_path)

    project = _row(rows, "Foundry project")
    assert project == "https://legacy.services.ai.azure.com/api/projects/lp"
    assert "(from run.yaml)" not in project
    assert _row(rows, "agent") == "legacy-agent:2"


def test_protocol_prefix_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://x.services.ai.azure.com/api/projects/p",
    )

    rows = _summarize_cockpit_connection(tmp_path)

    project = _row(rows, "Foundry project")
    assert project == "https://x.services.ai.azure.com/api/projects/p"


def test_malformed_yaml_does_not_raise(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(": not yaml :", encoding="utf-8")

    rows = _summarize_cockpit_connection(tmp_path)

    # The helper falls back to "not configured" rather than crashing
    assert "not configured" in _row(rows, "Foundry project")
    assert "not configured" in _row(rows, "agent")
