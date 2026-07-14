"""CLI tests for the ``agentops telemetry dashboard`` sub-app.

The Azure logic lives in :mod:`agentops.services.dashboard`; these tests keep
Azure out of the loop by exercising ``deploy --dry-run`` (which never touches
Azure), ``open --print-url`` and ``export`` (both pure), plus the ``--help``
and ``explain`` surfaces.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from agentops.cli.app import app


runner = CliRunner()


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


# ---------------------------------------------------------------------------
# help / discoverability
# ---------------------------------------------------------------------------
def test_dashboard_help_lists_commands() -> None:
    result = runner.invoke(app, ["telemetry", "dashboard", "--help"])
    assert result.exit_code == 0
    stripped = _strip_ansi(result.stdout)
    assert "deploy" in stripped
    assert "open" in stripped
    assert "export" in stripped
    assert "agent behavior" in stripped.lower()


def test_dashboard_deploy_help() -> None:
    result = runner.invoke(app, ["telemetry", "dashboard", "deploy", "--help"])
    assert result.exit_code == 0
    stripped = _strip_ansi(result.stdout)
    assert "--dry-run" in stripped
    assert "--subscription" in stripped
    assert "--resource-group" in stripped
    assert "--workspace-id" in stripped
    assert "--name" in stripped


def test_dashboard_open_help() -> None:
    result = runner.invoke(app, ["telemetry", "dashboard", "open", "--help"])
    assert result.exit_code == 0
    assert "--print-url" in _strip_ansi(result.stdout)


def test_dashboard_export_help() -> None:
    result = runner.invoke(app, ["telemetry", "dashboard", "export", "--help"])
    assert result.exit_code == 0
    assert "--out" in _strip_ansi(result.stdout)


# ---------------------------------------------------------------------------
# explain pages
# ---------------------------------------------------------------------------
def test_dashboard_explain_pages_exit_zero() -> None:
    for path in (
        ["telemetry", "dashboard", "explain"],
        ["telemetry", "dashboard", "deploy", "explain"],
        ["telemetry", "dashboard", "open", "explain"],
        ["telemetry", "dashboard", "export", "explain"],
    ):
        result = runner.invoke(app, path)
        assert result.exit_code == 0, f"{path} -> {result.stdout}"


# ---------------------------------------------------------------------------
# deploy --dry-run (no Azure)
# ---------------------------------------------------------------------------
def test_dashboard_deploy_dry_run_emits_arm_template(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "telemetry",
            "dashboard",
            "deploy",
            "--dry-run",
            "--subscription",
            "sub",
            "--resource-group",
            "rg",
            "--dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    # The dry-run prints the ARM template to stdout and an advisory note to
    # stderr (mixed into stdout by CliRunner); parse just the JSON object.
    obj, _end = json.JSONDecoder().raw_decode(result.stdout[result.stdout.index("{") :])
    template = obj
    assert template["resources"][0]["type"] == "Microsoft.Insights/workbooks"
    assert template["resources"][0]["properties"]["serializedData"]


# ---------------------------------------------------------------------------
# open --print-url (no browser, no Azure)
# ---------------------------------------------------------------------------
def test_dashboard_open_print_url(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "telemetry",
            "dashboard",
            "open",
            "--print-url",
            "--subscription",
            "sub",
            "--resource-group",
            "rg",
            "--dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "https://portal.azure.com/" in result.stdout


def test_dashboard_open_falls_back_to_gallery_without_target(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["telemetry", "dashboard", "open", "--print-url", "--dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.stdout
    # No subscription/rg discoverable -> gallery deep link.
    assert "WorkbookMenuBlade" in result.stdout


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
def test_dashboard_export_writes_file(tmp_path: Path) -> None:
    out = tmp_path / "foundry-ops.workbook.json"
    result = runner.invoke(app, ["telemetry", "dashboard", "export", "--out", str(out)])
    assert result.exit_code == 0, result.stdout
    assert out.is_file()
    # The exported file is valid JSON identical to the packaged template.
    from agentops.services import dashboard as dash

    assert json.loads(out.read_text(encoding="utf-8")) == dash.load_workbook_content()


def test_dashboard_export_creates_parent_dirs(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "dir" / "wb.json"
    result = runner.invoke(app, ["telemetry", "dashboard", "export", "--out", str(out)])
    assert result.exit_code == 0, result.stdout
    assert out.is_file()
