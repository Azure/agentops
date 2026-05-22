"""Latency check based on Azure Monitor and AgentOps results history."""

from __future__ import annotations

from typing import List, Optional

from agentops.agent.config import LatencyCheckConfig
from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.sources.azure_monitor import AzureMonitorPayload
from agentops.agent.sources.results_history import ResultsHistory


def run_latency_check(
    history: ResultsHistory,
    monitor: Optional[AzureMonitorPayload],
    config: LatencyCheckConfig,
) -> List[Finding]:
    findings: List[Finding] = []
    threshold = config.p95_threshold_seconds

    if monitor and monitor.p95_duration_seconds is not None:
        p95 = monitor.p95_duration_seconds
        if p95 > threshold:
            severity = (
                Severity.CRITICAL if p95 >= threshold * 2 else Severity.WARNING
            )
            findings.append(
                Finding(
                    id="latency.p95_production",
                    severity=severity,
                    category=Category.PERFORMANCE,
                    title="Production p95 latency exceeds threshold",
                    summary=(
                        f"Application Insights reports p95 latency of "
                        f"{p95:.2f}s, above the configured threshold of "
                        f"{threshold:.2f}s."
                    ),
                    recommendation=(
                        "Review recent deployments for tool-call loops or "
                        "long-running RAG retrievals, and consider scaling "
                        "out the agent runtime."
                    ),
                    source="azure_monitor",
                    evidence={
                        "p95_seconds": p95,
                        "threshold_seconds": threshold,
                        "request_count": monitor.request_count,
                    },
                )
            )

    if history.runs:
        latest = history.runs[-1]
        avg_latency = latest.metrics.get("avg_latency_seconds")
        if avg_latency is not None and avg_latency > threshold:
            severity = (
                Severity.CRITICAL
                if avg_latency >= threshold * 2
                else Severity.WARNING
            )
            findings.append(
                Finding(
                    id="latency.eval_avg",
                    severity=severity,
                    category=Category.PERFORMANCE,
                    title="Evaluation average latency above threshold",
                    summary=(
                        f"Run `{latest.run_id}` averaged "
                        f"{avg_latency:.2f}s per item, above the "
                        f"{threshold:.2f}s threshold."
                    ),
                    recommendation=(
                        "Profile the slowest dataset rows and inspect tool "
                        "calls; re-run evals after addressing the regression."
                    ),
                    source="results_history",
                    evidence={
                        "run_id": latest.run_id,
                        "avg_latency_seconds": avg_latency,
                        "threshold_seconds": threshold,
                    },
                )
            )
    return findings
