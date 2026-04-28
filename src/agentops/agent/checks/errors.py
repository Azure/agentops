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

    return findings
