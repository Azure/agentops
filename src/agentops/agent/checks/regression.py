"""Regression check: detect metric drops vs a rolling baseline."""

from __future__ import annotations

import json
from statistics import mean
from typing import List, Optional

from agentops.agent.config import RegressionCheckConfig
from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.sources.results_history import ResultsHistory, RunSummary
from agentops.core.results import RegressionInsight, RunResult
from agentops.pipeline.regression_insight import build_regression_insight


def _load_run_result(summary: RunSummary) -> Optional[RunResult]:
    """Best-effort reload of the full stored result behind a history entry.

    ``RunSummary`` is a thin projection with no commit/config fields; the
    causal explanation needs the full ``RunResult``. Only local runs have a
    real file to reload from (cloud-sourced summaries use a synthetic
    ``raw_path``); any read/parse failure is treated the same as "unknown"
    rather than raised, since this attribution is always best-effort.
    """
    if summary.source != "local":
        return None
    try:
        data = json.loads(summary.raw_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        return RunResult.model_validate(data)
    except ValueError:
        return None


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

    # The immediately preceding comparable run - the natural "what changed
    # since last time" pair, distinct from the rolling mean used for the
    # drop-percentage math below.
    previous_run = baseline_runs[-1]
    latest_result = _load_run_result(latest)
    previous_result = _load_run_result(previous_run)

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

        recommendation = (
            "Compare the latest run against the baseline runs in "
            "`.agentops/results/` or the Foundry Evaluations page, "
            "inspect prompt/model/dataset changes, and re-run the "
            "evaluation after the fix."
        )
        evidence = {
            "metric": metric,
            "current": current,
            "baseline_avg": baseline,
            "drop_ratio": drop,
            "baseline_runs": len(baseline_values),
            "latest_run_id": latest.run_id,
        }

        insight: Optional[RegressionInsight] = None
        if latest_result is not None and previous_result is not None:
            insight = build_regression_insight(previous_result, latest_result, metric=metric)
        if insight is not None:
            evidence["insight"] = insight.model_dump(mode="json")
            recommendation = insight.explanation
            if insight.suggested_action:
                recommendation = f"{recommendation} {insight.suggested_action}"

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
                recommendation=recommendation,
                source="results_history",
                evidence=evidence,
            )
        )
    return findings
