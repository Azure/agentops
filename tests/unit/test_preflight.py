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
    cockpit_doctor_guidance,
    format_report,
    run_preflight,
    workspace_is_initialized,
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


def test_foundry_project_reachability_is_independent_from_app_insights(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://x.services.ai.azure.com/api/projects/p",
    )
    with mock.patch(
        "agentops.utils.foundry_discovery."
        "check_foundry_project_reachable_with_reason",
        return_value=(True, None),
    ), mock.patch(
        "agentops.utils.foundry_discovery."
        "resolve_appinsights_connection_with_reason",
        side_effect=AssertionError("Foundry reachability must not request telemetry"),
    ):
        c = _check_foundry_project()

    assert c.status == "ok"
    assert c.message == "Project reachable."


def test_application_insights_ok_when_env_var_set(monkeypatch) -> None:
    monkeypatch.setenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "InstrumentationKey=11111111-2222-3333-4444-555555555555",
    )
    c = _check_application_insights_env()
    assert c.status == "ok"


def test_application_insights_explicit_connection_skips_foundry_discovery(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "InstrumentationKey=11111111-2222-3333-4444-555555555555",
    )
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://x.services.ai.azure.com/api/projects/p",
    )
    with mock.patch(
        "agentops.utils.foundry_discovery."
        "resolve_appinsights_connection_with_reason",
        side_effect=AssertionError("Explicit configuration must win"),
    ):
        c = _check_application_insights_env()

    assert c.status == "ok"
    assert c.message == "APPLICATIONINSIGHTS_CONNECTION_STRING is set."


def test_application_insights_accepts_project_managed_identity(
    monkeypatch,
) -> None:
    from agentops.utils.foundry_discovery import (
        PROJECT_MANAGED_IDENTITY_APPINSIGHTS_REASON,
    )

    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://x.services.ai.azure.com/api/projects/p",
    )
    with mock.patch(
        "agentops.utils.foundry_discovery."
        "resolve_appinsights_connection_with_reason",
        return_value=(None, PROJECT_MANAGED_IDENTITY_APPINSIGHTS_REASON),
    ):
        c = _check_application_insights_env()

    assert c.status == "ok"
    assert c.message == PROJECT_MANAGED_IDENTITY_APPINSIGHTS_REASON


def test_application_insights_warns_when_env_var_is_invalid(monkeypatch) -> None:
    monkeypatch.setenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "InstrumentationKey=not-a-guid;IngestionEndpoint=garbage",
    )
    c = _check_application_insights_env()
    assert c.status == "warn"
    assert "not a valid App Insights connection string" in c.message


def test_application_insights_skips_when_unconfigured(monkeypatch) -> None:
    """Issue #452 AC1/AC7: with neither an explicit connection string nor a
    Foundry endpoint, App Insights is not applicable — it reports ``skip``,
    not ``warn``, so a fresh directory produces no App Insights warning."""
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    c = _check_application_insights_env()
    assert c.status == "skip"
    assert "telemetry" in c.message.lower()
    assert "App Insights" in c.remediation


def test_application_insights_warns_when_foundry_auth_blocks_discovery(monkeypatch) -> None:
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://x.services.ai.azure.com/api/projects/p",
    )
    with mock.patch(
        "agentops.utils.foundry_discovery."
        "resolve_appinsights_connection_with_reason",
        return_value=(
            None,
            "Foundry authentication failed while reading telemetry metadata.",
        ),
    ):
        c = _check_application_insights_env()
    assert c.status == "warn"
    assert "connection-string discovery failed" in c.message.lower()
    assert "no connection string available" not in c.message.lower()
    assert "APPLICATIONINSIGHTS_CONNECTION_STRING" in c.remediation


def test_application_insights_warns_when_configured_foundry_lacks_appinsights(
    monkeypatch,
) -> None:
    """Issue #452 AC6: a Foundry project *is* configured but discovery finds
    no App Insights connection — this is an actionable gap, so ``warn`` (not
    ``skip``)."""
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://x.services.ai.azure.com/api/projects/p",
    )
    with mock.patch(
        "agentops.utils.foundry_discovery."
        "resolve_appinsights_connection_with_reason",
        return_value=(
            None,
            "Foundry project returned no application insights connection.",
        ),
    ):
        c = _check_application_insights_env()
    assert c.status == "warn"
    assert "APPLICATIONINSIGHTS_CONNECTION_STRING" in c.remediation


def test_cockpit_doctor_guidance_suppressed_before_init(tmp_path: Path) -> None:
    """Issue #452 AC2: before ``agentops init`` there is no ``.agentops/``
    directory, so no Doctor guidance is printed."""
    assert not workspace_is_initialized(tmp_path)
    assert cockpit_doctor_guidance(tmp_path) is None


def test_cockpit_doctor_guidance_when_initialized_without_findings(
    tmp_path: Path,
) -> None:
    """Issue #452 AC3: initialized workspace with no Doctor history prints the
    exact state-aware guidance line."""
    (tmp_path / ".agentops").mkdir()
    assert workspace_is_initialized(tmp_path)
    assert cockpit_doctor_guidance(tmp_path) == (
        "Run agentops doctor to generate readiness findings, "
        "then refresh Cockpit."
    )


def test_cockpit_doctor_guidance_suppressed_when_findings_exist(
    tmp_path: Path,
) -> None:
    """Issue #452 AC4: once Doctor findings exist, no guidance is printed."""
    history = tmp_path / ".agentops" / "agent" / "history.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text('{"run": 1}\n', encoding="utf-8")
    assert cockpit_doctor_guidance(tmp_path) is None


def test_cockpit_doctor_guidance_prints_when_history_is_empty(
    tmp_path: Path,
) -> None:
    """An empty history file is not a finding — guidance still prints."""
    history = tmp_path / ".agentops" / "agent" / "history.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text("", encoding="utf-8")
    assert cockpit_doctor_guidance(tmp_path) is not None


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
    # With no Foundry endpoint and no connection string, foundry_project and
    # app_insights are both skip — the run has no warnings.
    assert not report.has_warnings
    statuses = {c.name: c.status for c in report.checks}
    assert statuses["foundry_project"] == "skip"
    assert statuses["app_insights"] == "skip"


def test_format_report_counts_app_insights_skip(monkeypatch, tmp_path: Path) -> None:
    """Issue #452 AC7: an unconfigured App Insights check is counted as
    ``skipped`` (not ``warning``) in the pre-flight summary, and no App
    Insights warning is emitted."""
    (tmp_path / ".agentops").mkdir()
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
        report = run_preflight(tmp_path, scope="cockpit")
    text = format_report(report, color=False)
    assert "skipped" in text
    assert "0 warning" not in text  # warning piece is omitted when count is 0
    assert "warning" not in text


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
