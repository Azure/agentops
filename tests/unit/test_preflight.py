"""Tests for :mod:`agentops.services.preflight`."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from agentops.services.preflight import (
    PreflightCheck,
    PreflightReport,
    _check_application_insights_env,
    _check_azure_cli,
    _check_foundry_project,
    _check_workspace,
    format_report,
    run_preflight,
)
from agentops.utils.foundry_discovery import _summarize_discovery_exception


def test_workspace_check_passes_for_init_workspace(tmp_path: Path) -> None:
    (tmp_path / ".agentops").mkdir()
    c = _check_workspace(tmp_path)
    assert c.status == "ok"


def test_workspace_check_warns_when_not_initialized(tmp_path: Path) -> None:
    c = _check_workspace(tmp_path)
    assert c.status == "warn"
    assert "agentops init" in c.remediation


def test_workspace_check_fails_for_missing_dir(tmp_path: Path) -> None:
    c = _check_workspace(tmp_path / "does-not-exist")
    assert c.status == "fail"


def test_azure_cli_check_humanizes_az_login_failure() -> None:
    """When DefaultAzureCredential reports `AzureCliCredential: Failed
    to invoke the Azure CLI`, the pre-flight tile must offer the
    `az login` remediation, not the wall of text."""

    class _FakeCred:
        def __init__(self, **_kw):
            pass
        def get_token(self, _scope):
            raise RuntimeError(
                "DefaultAzureCredential failed to retrieve a token "
                "from the included credentials. AzureCliCredential: "
                "Failed to invoke the Azure CLI. ...lots of text..."
            )

    with mock.patch("azure.identity.DefaultAzureCredential", _FakeCred):
        c = _check_azure_cli()
    assert c.status == "fail"
    assert c.message == "Not signed in to Azure."
    assert "az login" in c.remediation
    assert "DefaultAzureCredential" not in c.message


def test_foundry_project_skip_when_env_missing(monkeypatch) -> None:
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    c = _check_foundry_project()
    assert c.status == "skip"


def test_application_insights_ok_when_env_var_set(monkeypatch) -> None:
    monkeypatch.setenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "InstrumentationKey=11111111-2222-3333-4444-555555555555",
    )
    c = _check_application_insights_env()
    assert c.status == "ok"


def test_application_insights_warns_when_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    c = _check_application_insights_env()
    assert c.status == "warn"
    assert "production telemetry" in c.message.lower()
    assert "App Insights" in c.remediation


def test_application_insights_warns_when_foundry_auth_blocks_discovery(monkeypatch) -> None:
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    c = _check_application_insights_env(
        PreflightCheck(
            name="foundry_project",
            display_name="Foundry project",
            status="warn",
            message=(
                "Discovery failed - Foundry authentication failed while reading "
                "telemetry metadata."
            ),
        )
    )
    assert c.status == "warn"
    assert "could not verify" in c.message.lower()
    assert "no connection string available" not in c.message.lower()
    assert "APPLICATIONINSIGHTS_CONNECTION_STRING" in c.remediation


def test_foundry_discovery_auth_error_is_summarized() -> None:
    reason = _summarize_discovery_exception(
        RuntimeError(
            "DefaultAzureCredential failed to retrieve a token from the included "
            "credentials.\nAttempted credentials:\nEnvironmentCredential: "
            "EnvironmentCredential authentication unavailable.\n"
            "AzureCliCredential: Failed to invoke the Azure CLI."
        ),
        context="Foundry telemetry discovery",
    )
    assert "Foundry authentication failed" in reason
    assert "DefaultAzureCredential" not in reason
    assert "Attempted credentials" not in reason
    assert "EnvironmentCredential" not in reason


def test_run_preflight_collects_all_checks(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".agentops").mkdir()
    # Force every Azure-dependent check into a deterministic state.
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)

    class _FakeCred:
        def __init__(self, **_kw):
            pass
        def get_token(self, _scope):
            class _T:
                token = "fake"
                expires_on = 9999999999
            return _T()

    with mock.patch("azure.identity.DefaultAzureCredential", _FakeCred):
        report = run_preflight(tmp_path, scope="doctor")
    names = [c.name for c in report.checks]
    assert names == ["workspace", "azure_auth", "foundry_project", "app_insights"]
    assert not report.has_failures
    assert report.has_warnings  # app_insights and foundry_project are skip/warn


def test_format_report_renders_status_glyphs() -> None:
    report = PreflightReport(checks=[
        PreflightCheck(name="workspace", display_name="Workspace",
                       status="ok", message="/tmp/x"),
        PreflightCheck(name="azure_auth", display_name="Azure authentication",
                       status="fail", message="Not signed in to Azure.",
                       remediation="Run `az login` in this terminal."),
        PreflightCheck(name="app_insights", display_name="Application Insights",
                       status="warn", message="No connection string available.",
                       remediation="Wire App Insights in Foundry."),
        PreflightCheck(name="foundry_project", display_name="Foundry project",
                       status="skip", message="env var missing"),
    ])
    text = format_report(report, color=False)
    # Headline counts.
    assert "AgentOps pre-flight" in text
    assert "1 ok" in text and "1 warning" in text and "1 failed" in text
    # Display names render instead of internal ids.
    assert "Workspace" in text and "Azure authentication" in text
    # Remediation lines appear indented for warn / fail.
    assert "Run `az login` in this terminal." in text
    assert "Wire App Insights in Foundry." in text
    # The arrow glyph leads each remediation row.
    assert "\u2192" in text


def test_format_report_collapses_to_one_line_when_all_ok() -> None:
    report = PreflightReport(checks=[
        PreflightCheck(name="workspace", display_name="Workspace",
                       status="ok", message="/tmp/x"),
        PreflightCheck(name="azure_auth", display_name="Azure authentication",
                       status="ok", message="ARM token acquired"),
    ])
    text = format_report(report, color=False)
    # Single line summary, no per-check rows.
    assert "\n" not in text
    assert "2 ok" in text


def test_format_report_can_show_ok_details() -> None:
    report = PreflightReport(checks=[
        PreflightCheck(name="workspace", display_name="Workspace",
                       status="ok", message="/tmp/x"),
        PreflightCheck(name="azure_auth", display_name="Azure authentication",
                       status="ok", message="ARM token acquired"),
    ])
    text = format_report(report, color=False, show_ok_details=True)
    assert "2 ok" in text
    assert "Workspace" in text
    assert "Azure authentication" in text
