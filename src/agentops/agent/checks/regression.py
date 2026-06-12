"""Regression check: detect metric drops vs a rolling baseline."""

from __future__ import annotations

from statistics import mean
from typing import List

from agentops.agent.config import RegressionCheckConfig
from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.sources.results_history import ResultsHistory


def run_regression_check(
    history: ResultsHistory, config: RegressionCheckConfig
) -> List[Finding]:
    runs = history.runs
    if len(runs) < config.min_runs:
        return []

    latest = runs[-1]
    # Only compare against runs that share the same evaluation methodology
    # (same agent target, dataset, and evaluator set). This avoids spurious
    # regressions when the dataset, evaluators, or runner changes between
    # runs (e.g. smoke → hardened conversation rubric, or local → cloud).
    fingerprint = latest.methodology_fingerprint
    if fingerprint is None:
        baseline_runs = runs[:-1]
    else:
        baseline_runs = [
            r for r in runs[:-1] if r.methodology_fingerprint == fingerprint
        ]
    if len(baseline_runs) + 1 < config.min_runs:
        return []
    if not baseline_runs:
        return []

    findings: List[Finding] = []
    for metric in config.metrics:
        baseline_values = [
            r.metrics[metric] for r in baseline_runs if metric in r.metrics
        ]
        if not baseline_values:
            continue
        if metric not in latest.metrics:
            continue

        baseline = mean(baseline_values)
        current = latest.metrics[metric]
        if baseline <= 0:
            continue

        drop = (baseline - current) / baseline
        if drop < config.threshold_drop:
            continue

        severity = (
            Severity.CRITICAL
            if drop >= max(config.threshold_drop * 2, 0.20)
            else Severity.WARNING
        )

        findings.append(
            Finding(
                id=f"regression.{metric}",
                severity=severity,
                category=Category.QUALITY,
                title=f"Regression detected on `{metric}`",
                summary=(
                    f"`{metric}` dropped {drop * 100:.1f}% in run "
                    f"`{latest.run_id}` (current={current:.4f}, "
                    f"baseline={baseline:.4f} over {len(baseline_values)} runs)."
                ),
                recommendation=(
                    "Compare the latest run against the baseline runs in "
                    "`.agentops/results/` or the Foundry Evaluations page, "
                    "inspect prompt/model/dataset changes, and re-run the "
                    "evaluation after the fix."
                ),
                source="results_history",
                evidence={
                    "metric": metric,
                    "current": current,
                    "baseline_avg": baseline,
                    "drop_ratio": drop,
                    "baseline_runs": len(baseline_values),
                    "latest_run_id": latest.run_id,
                },
            )
        )
    return findings
