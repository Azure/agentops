"""Errors / failure rate check."""

from __future__ import annotations

from typing import List, Optional

from agentops.agent.config import ErrorsCheckConfig
from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.sources.azure_monitor import AzureMonitorPayload
from agentops.agent.sources.foundry_control import FoundryControlPayload


def run_errors_check(
    monitor: Optional[AzureMonitorPayload],
    foundry: Optional[FoundryControlPayload],
    config: ErrorsCheckConfig,
) -> List[Finding]:
    findings: List[Finding] = []

    if (
        monitor
        and monitor.error_rate is not None
        and monitor.error_rate > config.rate_threshold
    ):
        severity = (
            Severity.CRITICAL
            if monitor.error_rate >= config.rate_threshold * 2
            else Severity.WARNING
        )
        findings.append(
            Finding(
                id="errors.production_rate",
                severity=severity,
                category=Category.RELIABILITY,
                title="Production error rate above threshold",
                summary=(
                    f"App Insights reports {monitor.error_count} failed "
                    f"requests over {monitor.request_count} total "
                    f"({monitor.error_rate * 100:.2f}%), above the "
                    f"{config.rate_threshold * 100:.2f}% threshold."
                ),
                recommendation=(
                    "Open the App Insights resource, group failures by "
                    "operation, and inspect the most common exception "
                    "type."
                ),
                source="azure_monitor",
                evidence={
                    "error_count": monitor.error_count,
                    "request_count": monitor.request_count,
                    "error_rate": monitor.error_rate,
                    "threshold": config.rate_threshold,
                },
            )
        )

    if (
        foundry
        and foundry.failure_rate is not None
        and foundry.failure_rate > config.rate_threshold
    ):
        findings.append(
            Finding(
                id="errors.foundry_runs",
                severity=Severity.WARNING,
                category=Category.RELIABILITY,
                title="Foundry agent run failure rate elevated",
                summary=(
                    f"Foundry control plane reports "
                    f"{foundry.failed_runs}/{foundry.total_runs} failed "
                    f"runs ({foundry.failure_rate * 100:.2f}%)."
                ),
                recommendation=(
                    "Review recent Foundry runs, paying attention to "
                    "tool-call errors and rate limits."
                ),
                source="foundry_control",
                evidence={
                    "failed_runs": foundry.failed_runs,
                    "total_runs": foundry.total_runs,
                    "failure_rate": foundry.failure_rate,
                },
            )
        )

    findings.extend(_check_no_runtime_telemetry(monitor))

    return findings


def _check_no_runtime_telemetry(
    monitor: Optional[AzureMonitorPayload],
) -> List[Finding]:
    """Warn when Azure Monitor is not wired, or wired but silent.

    Two failure modes count, both blockers for L3 in the GenAIOps
    Maturity Model:

    * **Not configured.** The ``azure_monitor`` source is enabled but
      has no ``app_insights_resource_id`` / ``log_analytics_workspace_id``,
      so it reports ``status: skipped``. Doctor has no production
      observability at all.
    * **Configured but empty.** The source reports ``status: ok`` but
      ``request_count == 0`` over the lookback, so the App Insights
      workspace exists but the agent runtime is not emitting
      telemetry to it.

    The two cases share one finding because the user-facing
    remediation is identical: wire the OpenTelemetry exporter on the
    agent runtime side, and configure the resource id on the
    ``azure_monitor`` source in ``agent.yaml``. If the source is
    explicitly ``enabled: false`` we treat that as an opt-out and
    stay quiet.
    """
    if monitor is None:
        return []
    diag = monitor.diagnostics or {}
    status = diag.get("status")

    if status == "disabled":
        return []

    if status == "ok" and monitor.request_count <= 0:
        summary = (
            "Application Insights / Log Analytics is reachable but "
            "reports 0 requests over the lookback window. The "
            "agent runtime is not emitting telemetry, so the "
            "dashboard, latency, errors, and runtime-safety "
            "checks have nothing to grade."
        )
        evidence = {
            "request_count": monitor.request_count,
            "monitor_status": status,
            "mode": "configured_but_empty",
        }
    elif status == "skipped":
        summary = (
            "The `azure_monitor` source is not configured "
            f"({diag.get('reason') or 'unknown reason'}). Without "
            "App Insights wired up, Doctor has no production "
            "observability, so latency, errors, runtime safety, and "
            "telemetry-based maturity all stay grey."
        )
        evidence = {
            "monitor_status": status,
            "reason": diag.get("reason"),
            "mode": "not_configured",
        }
    else:
        return []

    return [
        Finding(
            id="errors.no_runtime_telemetry",
            severity=Severity.WARNING,
            category=Category.RELIABILITY,
            title="Production telemetry is not wired to the agent",
            summary=summary,
            recommendation=(
                "Configure `sources.azure_monitor.app_insights_resource_id` "
                "in `.agentops/agent.yaml`, install the `[agent]` extra, "
                "and connect Azure Monitor OpenTelemetry on the agent "
                "runtime (set `APPLICATIONINSIGHTS_CONNECTION_STRING` "
                "and call `configure_azure_monitor()` on startup). "
                "See `docs/tutorial-basic-foundry-agent.md` -> "
                "'Connect Application Insights'."
            ),
            source="azure_monitor",
            evidence=evidence,
        )
    ]
