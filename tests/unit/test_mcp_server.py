"""Tests for the AgentOps MCP server.

These tests do not require the ``mcp`` extra to be installed: they
verify that ``cmd_mcp_serve`` is wired into the CLI and that the server
module raises a clear error when the optional dependency is missing.

When ``mcp`` is installed, we additionally smoke-test that the server
builds successfully and registers the expected tool set.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentops.cli.app import app


runner = CliRunner()

_HAS_MCP = importlib.util.find_spec("mcp") is not None


def test_cli_exposes_mcp_serve_command() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "mcp" in result.stdout

    sub = runner.invoke(app, ["mcp", "--help"])
    assert sub.exit_code == 0
    assert "serve" in sub.stdout


def test_mcp_serve_help_runs_without_mcp_extra() -> None:
    result = runner.invoke(app, ["mcp", "serve", "--help"])
    assert result.exit_code == 0
    assert "stdio" in result.stdout.lower() or "MCP" in result.stdout


@pytest.mark.skipif(_HAS_MCP, reason="mcp extra is installed")
def test_mcp_serve_errors_when_extra_missing() -> None:
    result = runner.invoke(app, ["mcp", "serve"])
    assert result.exit_code == 1
    assert "mcp" in result.stdout.lower() or "mcp" in (result.stderr or "").lower()


@pytest.mark.skipif(not _HAS_MCP, reason="mcp extra not installed")
def test_build_server_registers_expected_tools() -> None:
    from agentops.mcp.server import _build_server

    server = _build_server()
    # FastMCP exposes registered tools via _tool_manager._tools (private but
    # stable across 1.x); fall back to list_tools() when available.
    tools = set()
    tm = getattr(server, "_tool_manager", None)
    if tm is not None and hasattr(tm, "_tools"):
        tools = set(tm._tools.keys())
    expected = {
        "agentops_init",
        "agentops_eval_run",
        "agentops_report_show",
        "agentops_results_summary",
        "agentops_dataset_add",
        "agentops_list_runs",
        "agentops_workflow_init",
    }
    assert expected.issubset(tools), f"missing tools: {expected - tools}"


@pytest.mark.skipif(not _HAS_MCP, reason="mcp extra not installed")
def test_dataset_add_tool_appends_rows(tmp_path: Path) -> None:
    from agentops.mcp.server import _build_server

    server = _build_server()
    tm = server._tool_manager
    tool = tm._tools["agentops_dataset_add"]

    target = tmp_path / "rows.jsonl"
    rows = [{"input": "hello", "expected": "hi"}, {"input": "ping", "expected": "pong"}]

    fn = getattr(tool, "fn", None) or getattr(tool, "func", None)
    assert fn is not None, "could not locate underlying function on Tool"
    result = fn(dataset_path=str(target), rows=rows)

    assert result["ok"] is True
    assert result["appended"] == 2
    assert target.read_text(encoding="utf-8").count("\n") == 2


@pytest.mark.skipif(not _HAS_MCP, reason="mcp extra not installed")
def test_list_runs_tool_handles_missing_dir(tmp_path: Path) -> None:
    from agentops.mcp.server import _build_server

    server = _build_server()
    tool = server._tool_manager._tools["agentops_list_runs"]
    fn = getattr(tool, "fn", None) or getattr(tool, "func", None)
    result = fn(workspace_dir=str(tmp_path))
    assert result == {"ok": True, "runs": []}
