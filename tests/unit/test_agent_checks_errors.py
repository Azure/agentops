"""Tests for the no_runtime_telemetry rule in the errors check."""

from __future__ import annotations

from agentops.agent.checks.errors import run_errors_check
from agentops.agent.config import ErrorsCheckConfig
from agentops.agent.findings import Category, Severity
from agentops.agent.sources.azure_monitor import AzureMonitorPayload


def test_no_runtime_telemetry_emitted_when_monitor_ok_but_zero_requests() -> None:
    monitor = AzureMonitorPayload(
        request_count=0,
        error_count=0,
        diagnostics={"status": "ok"},
    )
    findings = run_errors_check(monitor, None, ErrorsCheckConfig())
    assert any(f.id == "errors.no_runtime_telemetry" for f in findings)
    finding = next(f for f in findings if f.id == "errors.no_runtime_telemetry")
    assert finding.severity == Severity.WARNING
    assert finding.category == Category.RELIABILITY


def test_no_runtime_telemetry_silent_when_requests_present() -> None:
    monitor = AzureMonitorPayload(
        request_count=42,
        error_count=0,
        diagnostics={"status": "ok"},
    )
    findings = run_errors_check(monitor, None, ErrorsCheckConfig())
    assert all(f.id != "errors.no_runtime_telemetry" for f in findings)


def test_no_runtime_telemetry_silent_when_monitor_skipped() -> None:
    monitor = AzureMonitorPayload(
        request_count=0,
        error_count=0,
        diagnostics={"status": "disabled"},
    )
    findings = run_errors_check(monitor, None, ErrorsCheckConfig())
    assert all(f.id != "errors.no_runtime_telemetry" for f in findings)


def test_no_runtime_telemetry_fires_when_source_not_configured() -> None:
    monitor = AzureMonitorPayload(
        request_count=0,
        error_count=0,
        diagnostics={
            "status": "skipped",
            "reason": "neither app_insights_resource_id nor log_analytics_workspace_id is configured",
        },
    )
    findings = run_errors_check(monitor, None, ErrorsCheckConfig())
    finding = next(
        (f for f in findings if f.id == "errors.no_runtime_telemetry"), None
    )
    assert finding is not None
    assert finding.evidence["mode"] == "not_configured"


def test_no_runtime_telemetry_silent_when_monitor_is_none() -> None:
    findings = run_errors_check(None, None, ErrorsCheckConfig())
    assert findings == []
