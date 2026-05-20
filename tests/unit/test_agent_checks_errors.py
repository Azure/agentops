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


# ---------------------------------------------------------------------------
# AI.154 rate-limit pressure (errors.rate_limit_pressure)
# ---------------------------------------------------------------------------


def _monitor_payload(**kwargs):
    from agentops.agent.sources.azure_monitor import AzureMonitorPayload
    return AzureMonitorPayload(**kwargs)


def test_rate_limit_pressure_fires_when_429_above_floor():
    from agentops.agent.checks.errors import run_errors_check
    from agentops.agent.config import ErrorsCheckConfig
    monitor = _monitor_payload(
        request_count=1000, error_count=0, rate_limit_429_count=80,
        diagnostics={"status": "ok"},
    )
    findings = run_errors_check(monitor, None, ErrorsCheckConfig(rate_threshold=0.05))
    rl = [f for f in findings if f.id == "errors.rate_limit_pressure"]
    assert len(rl) == 1
    assert rl[0].evidence["rate_limit_429_count"] == 80


def test_rate_limit_pressure_silent_when_under_floor():
    from agentops.agent.checks.errors import run_errors_check
    from agentops.agent.config import ErrorsCheckConfig
    monitor = _monitor_payload(
        request_count=1000, error_count=0, rate_limit_429_count=3,
        diagnostics={"status": "ok"},
    )
    findings = run_errors_check(monitor, None, ErrorsCheckConfig(rate_threshold=0.05))
    assert not any(f.id == "errors.rate_limit_pressure" for f in findings)


def test_rate_limit_pressure_escalates_to_critical_at_2x_threshold():
    from agentops.agent.checks.errors import run_errors_check
    from agentops.agent.config import ErrorsCheckConfig
    from agentops.agent.findings import Severity
    monitor = _monitor_payload(
        request_count=1000, error_count=0, rate_limit_429_count=200,
        diagnostics={"status": "ok"},
    )
    findings = run_errors_check(monitor, None, ErrorsCheckConfig(rate_threshold=0.05))
    rl = next(f for f in findings if f.id == "errors.rate_limit_pressure")
    assert rl.severity == Severity.CRITICAL


# ---------------------------------------------------------------------------
# AI.132 token telemetry (opex.no_token_telemetry)
# ---------------------------------------------------------------------------


def test_no_token_telemetry_fires_when_requests_present_but_zero_tokens():
    from agentops.agent.checks.errors import run_errors_check
    from agentops.agent.config import ErrorsCheckConfig
    monitor = _monitor_payload(
        request_count=120, error_count=0,
        input_token_count=0, output_token_count=0,
        diagnostics={"status": "ok", "token_status": "ok"},
    )
    findings = run_errors_check(monitor, None, ErrorsCheckConfig())
    assert any(f.id == "opex.no_token_telemetry" for f in findings)


def test_no_token_telemetry_silent_when_tokens_reported():
    from agentops.agent.checks.errors import run_errors_check
    from agentops.agent.config import ErrorsCheckConfig
    monitor = _monitor_payload(
        request_count=120, error_count=0,
        input_token_count=15000, output_token_count=4000,
        diagnostics={"status": "ok", "token_status": "ok"},
    )
    findings = run_errors_check(monitor, None, ErrorsCheckConfig())
    assert not any(f.id == "opex.no_token_telemetry" for f in findings)


def test_no_token_telemetry_silent_when_token_probe_errored():
    from agentops.agent.checks.errors import run_errors_check
    from agentops.agent.config import ErrorsCheckConfig
    monitor = _monitor_payload(
        request_count=120, error_count=0,
        input_token_count=0, output_token_count=0,
        diagnostics={"status": "ok", "token_status": "error"},
    )
    findings = run_errors_check(monitor, None, ErrorsCheckConfig())
    assert not any(f.id == "opex.no_token_telemetry" for f in findings)


def test_no_token_telemetry_silent_when_zero_requests():
    from agentops.agent.checks.errors import run_errors_check
    from agentops.agent.config import ErrorsCheckConfig
    monitor = _monitor_payload(
        request_count=0, error_count=0,
        diagnostics={"status": "ok", "token_status": "ok"},
    )
    findings = run_errors_check(monitor, None, ErrorsCheckConfig())
    assert not any(f.id == "opex.no_token_telemetry" for f in findings)
