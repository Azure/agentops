from __future__ import annotations

import re

from typer.testing import CliRunner

from agentops.cli.app import app


runner = CliRunner()


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_root_explain_renders_cli_manual() -> None:
    result = runner.invoke(app, ["explain", "--no-pager"])

    assert result.exit_code == 0, result.stdout
    stripped = _strip_ansi(result.stdout)
    normalized = " ".join(stripped.split())
    assert "AgentOps CLI" in stripped
    assert "AGENTOPS EXPLAIN / detailed command guide" in stripped
    assert "AgentOps Toolkit" in stripped
    assert "can we ship it, and where is the proof" in normalized
    assert "Foundry runs the agent. AgentOps proves the release is ready" in normalized
    assert "COMMAND" in stripped
    assert "WHAT IT DOES" in stripped
    assert "COMMANDS" in stripped
    assert "agentops explain eval run --open" in stripped


def test_explain_accepts_command_path() -> None:
    result = runner.invoke(app, ["explain", "cockpit", "--no-pager"])

    assert result.exit_code == 0, result.stdout
    stripped = _strip_ansi(result.stdout)
    assert "AgentOps Cockpit" in stripped
    assert "agentops cockpit [--host HOST]" in stripped
    assert "HOW IT WORKS" in stripped


def test_group_explain_alias_renders_group_docs() -> None:
    result = runner.invoke(app, ["eval", "explain", "--no-pager"])

    assert result.exit_code == 0, result.stdout
    stripped = _strip_ansi(result.stdout)
    assert "Evaluation commands" in stripped
    assert "agentops eval analyze" in stripped
    assert "agentops eval run" in stripped


def test_eval_analyze_explain_renders_manual() -> None:
    result = runner.invoke(app, ["eval", "analyze", "explain"])

    assert result.exit_code == 0, result.stdout
    stripped = _strip_ansi(result.stdout)
    assert "Analyze evaluation setup" in stripped
    assert "agentops eval analyze" in stripped
    assert "agentops-config" in stripped


def test_leaf_explain_alias_renders_without_running_command() -> None:
    result = runner.invoke(app, ["cockpit", "explain"])

    assert result.exit_code == 0, result.stdout
    stripped = _strip_ansi(result.stdout)
    assert "AgentOps Cockpit" in stripped
    assert "The local browser Cockpit" in stripped


def test_workflow_analyze_explain_renders_manual() -> None:
    result = runner.invoke(app, ["workflow", "analyze", "explain"])

    assert result.exit_code == 0, result.stdout
    stripped = _strip_ansi(result.stdout)
    assert "Analyze CI/CD workflow shape" in stripped
    assert "agentops workflow analyze" in stripped
    assert "azure.yaml" in stripped


def test_explain_can_write_markdown_for_any_command(tmp_path) -> None:
    out = tmp_path / "eval-run.md"

    result = runner.invoke(
        app,
        ["explain", "eval", "run", "--format", "markdown", "--out", str(out)],
    )

    assert result.exit_code == 0, result.stdout
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Run evaluation")
    assert "## HOW IT WORKS" in text
    assert "`results.json`" in text


def test_explain_open_creates_browser_copy(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", opened.append)

    result = runner.invoke(app, ["explain", "cockpit", "--open"])

    assert result.exit_code == 0, result.stdout
    assert "Opened browser copy:" in result.stdout
    assert len(opened) == 1
    assert opened[0].startswith("file:")


def test_unknown_explain_path_errors_cleanly() -> None:
    result = runner.invoke(app, ["explain", "missing"])

    assert result.exit_code == 1
    assert "unknown command path" in result.output
