"""CLI tests for `agentops doctor --help` and `agentops doctor explain`."""

from __future__ import annotations

import re

from typer.testing import CliRunner

from agentops.cli.app import app


runner = CliRunner()


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_doctor_help_is_terse_and_exposes_options() -> None:
    result = runner.invoke(app, ["doctor", "--help"])

    assert result.exit_code == 0, result.stdout
    stripped = _strip_ansi(result.stdout)
    normalized = re.sub(r"\s+", " ", stripped)

    assert "Diagnose MLOps / security / responsible-AI gaps in this workspace." in normalized
    assert "Use `agentops doctor explain` for the long-form manual." in normalized
    for flag in (
        "--workspace",
        "--config",
        "--out",
        "--lookback-days",
        "--severity-fail",
        "--categories",
        "--exclude-rules",
        "--no-preflight",
        "--strict-preflight",
        "--evidence-pack",
        "--evidence-out",
    ):
        assert flag in stripped, f"missing analyzer flag {flag} in doctor --help"

    assert "explain" in stripped
    assert "agentops doctor list" not in stripped
    assert "DATA SOURCES" not in stripped
    assert "CHECK CATALOG" not in stripped


def test_doctor_list_subcommand_is_not_public() -> None:
    result = runner.invoke(app, ["doctor", "list"])
    assert result.exit_code != 0


def test_doctor_explain_renders_manual_sections_without_pager() -> None:
    result = runner.invoke(app, ["doctor", "explain", "--no-pager"])

    assert result.exit_code == 0, result.stdout
    stripped = _strip_ansi(result.stdout)
    assert "AGENTOPS EXPLAIN / detailed command guide" in stripped
    assert "Diagnose AgentOps workspaces" in stripped
    for section in (
        "NAME",
        "SYNOPSIS",
        "DESCRIPTION",
        "DATA SOURCES",
        "AZD AND DEPLOYED ENVIRONMENTS",
        "HOW IT WORKS",
        "CHECK CATEGORIES",
        "CHECK CATALOG",
        "EXIT CODES",
        "EXAMPLES",
        "SEE ALSO",
    ):
        assert section in stripped

    assert "azure_resources (Azure resources (ARM))" in stripped
    assert "Doctor first looks for the active AZD environment" in stripped
    assert "regression.<metric>" in stripped
    assert "responsible_ai.llm.prompt_transparency" in stripped
    assert "[Source-based] regression.<metric>" in stripped
    assert "[LLM Judge] responsible_ai.llm.prompt_transparency" in stripped
    assert "mode: Source-based (no judge model call)" in stripped
    assert "mode: LLM Judge (opt-in; uses configured judge model)" in stripped
    assert "learn more: https://learn.microsoft.com/azure/well-architected/ai/responsible-ai" in stripped
    assert "agentops doctor list" not in stripped


def test_doctor_explain_wraps_catalog_text_with_indent() -> None:
    result = runner.invoke(app, ["doctor", "explain", "--no-pager"])

    assert result.exit_code == 0, result.stdout
    stripped = _strip_ansi(result.stdout)
    assert (
        "        A judge model reviewed the agent's system prompt and flagged missing user-facing\n"
        "        AI disclosure or transparency language."
    ) in stripped


def test_doctor_explain_can_write_markdown(tmp_path) -> None:
    out = tmp_path / "doctor.md"

    result = runner.invoke(
        app,
        ["doctor", "explain", "--format", "markdown", "--out", str(out)],
    )

    assert result.exit_code == 0, result.stdout
    assert "Wrote" in result.stdout
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# AgentOps Doctor manual")
    assert "## DATA SOURCES" in text
    assert "`azure_resources`" in text
    assert "**Legend:** `LLM Judge` calls the configured judge model" in text
    assert "#### [LLM Judge] `responsible_ai.llm.prompt_transparency`" in text
    assert "**Mode:** Source-based (no judge model call)" in text
    assert "agentops doctor explain --open" in text


def test_doctor_explain_can_write_html(tmp_path) -> None:
    out = tmp_path / "doctor.html"

    result = runner.invoke(
        app,
        ["doctor", "explain", "--format", "html", "--out", str(out)],
    )

    assert result.exit_code == 0, result.stdout
    html = out.read_text(encoding="utf-8")
    assert "<title>AgentOps Doctor manual</title>" in html
    assert '<section class="hero">' in html
    assert "AgentOps explain" in html
    assert "<h2>DATA SOURCES</h2>" in html
    assert "<table>" in html
    assert '<article class="check-card">' in html
    assert '<h4>[Source-based] <code>errors.production_rate</code></h4>' in html
    assert '<h4>[LLM Judge] <code>responsible_ai.llm.prompt_transparency</code></h4>' in html
    assert "background: #eef6ff" in html
    assert "border-left: 3px solid #8cbeff" in html


def test_doctor_explain_open_creates_browser_copy(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", opened.append)

    result = runner.invoke(app, ["doctor", "explain", "--open"])

    assert result.exit_code == 0, result.stdout
    assert "Opened browser copy:" in result.stdout
    assert len(opened) == 1
    assert opened[0].startswith("file:")
