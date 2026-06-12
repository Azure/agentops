"""Operational excellence check.

Pipeline-hygiene findings that are time-based or stability-based rather
than file-based (which live in :mod:`agentops.agent.checks.mlops`).

Findings emitted:

* ``opex.stale_evaluation`` - Doctor warns when no fresh eval run has
  landed in the configured window.
* ``opex.flaky_metric`` - a metric's coefficient of variation across
  recent runs is high enough to suggest a flaky judge / non-deterministic
  prompt rather than real change.
"""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean, pstdev
from typing import List

from agentops.agent.config import OpexCheckConfig
from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.sources.results_history import ResultsHistory

SOURCE_NAME = "results_history"


def run_opex_check(
    history: ResultsHistory, config: OpexCheckConfig
) -> List[Finding]:
    """Detect operational-excellence regressions (stale runs + flaky metrics)."""
    if not config.enabled:
        return []

    findings: List[Finding] = []
    findings.extend(_check_stale_evaluation(history, config))
    findings.extend(_check_flaky_metric(history, config))
    return findings


def _check_stale_evaluation(
    history: ResultsHistory, config: OpexCheckConfig
) -> List[Finding]:
    if not history.runs:
        return []
    latest = history.runs[-1]
    if latest.timestamp is None:
        return []
    now = datetime.now(timezone.utc)
    age_days = (now - latest.timestamp).total_seconds() / 86400.0
    if age_days <= config.stale_after_days:
        return []

    severity = (
        Severity.CRITICAL
        if age_days >= config.stale_after_days * 2
        else Severity.WARNING
    )
    return [
        Finding(
            id="opex.stale_evaluation",
            severity=severity,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="No fresh evaluation run in the configured window",
            summary=(
                f"The most recent eval run (`{latest.run_id}`) is "
                f"{age_days:.1f} day(s) old, above the configured "
                f"threshold of {config.stale_after_days} day(s). The "
                "agent's measured quality is drifting away from its "
                "last validated baseline."
            ),
            recommendation=(
                "Run `agentops eval run` (locally or via CI) to "
                "produce a fresh local `results.json` or Foundry cloud "
                "evaluation, then re-run `agentops doctor`."
            ),
            source=SOURCE_NAME,
            evidence={
                "latest_run_id": latest.run_id,
                "latest_timestamp": latest.timestamp.isoformat(),
                "age_days": round(age_days, 2),
                "threshold_days": config.stale_after_days,
            },
        )
    ]


def _check_flaky_metric(
    history: ResultsHistory, config: OpexCheckConfig
) -> List[Finding]:
    """Flag metrics whose coefficient of variation is suspiciously high.

    A high CV (stddev / mean) across many runs without a corresponding
    agent change is the fingerprint of a non-deterministic judge or a
    prompt that's overly sensitive to phrasing. Real regressions show
    up as monotonic drops (caught by the ``regression`` check); flaky
    metrics oscillate.

    We only consider metrics with at least ``min_runs_for_flaky`` data
    points and a mean that's safely above zero to avoid amplifying noise
    on near-zero scores.
    """
    runs = history.runs
    if len(runs) < config.min_runs_for_flaky:
        return []

    # Only consider the recent window of runs that share the latest run's
    # evaluation methodology (same target/dataset/evaluators). Mixing
    # methodologies inflates the coefficient of variation and produces
    # false "flaky metric" warnings.
    latest_fingerprint = runs[-1].methodology_fingerprint
    if latest_fingerprint is None:
        comparable = runs
    else:
        comparable = [
            r for r in runs if r.methodology_fingerprint == latest_fingerprint
        ]
    if len(comparable) < config.min_runs_for_flaky:
        return []

    # Collect each metric's series across the recent window.
    series: dict[str, List[float]] = {}
    for run in comparable[-config.min_runs_for_flaky :]:
        for name, value in run.metrics.items():
            series.setdefault(name, []).append(value)

    findings: List[Finding] = []
    for metric, values in series.items():
        if len(values) < config.min_runs_for_flaky:
            continue
        avg = mean(values)
        if avg <= 0.05:
            # Near-zero metrics make CV explode without signal.
            continue
        cv = pstdev(values) / avg
        if cv < config.flaky_cv_threshold:
            continue
        findings.append(
            Finding(
                id=f"opex.flaky_metric.{metric}",
                severity=Severity.WARNING,
                category=Category.OPERATIONAL_EXCELLENCE,
                title=f"`{metric}` is unstable across recent runs",
                summary=(
                    f"`{metric}` shows a coefficient of variation of "
                    f"{cv * 100:.1f}% across the last {len(values)} "
                    "runs (threshold: "
                    f"{config.flaky_cv_threshold * 100:.0f}%). That "
                    "kind of oscillation usually points at a "
                    "non-deterministic judge model or a prompt that's "
                    "overly sensitive to phrasing - not at real "
                    "agent change."
                ),
                recommendation=(
                    "Pin the judge model's `temperature` / `seed` "
                    "(or switch to a deterministic evaluator), and "
                    "review the metric's prompt for ambiguity. If "
                    "the metric is intrinsically noisy, raise "
                    "`min_runs` on the regression check so signals "
                    "average out."
                ),
                source=SOURCE_NAME,
                evidence={
                    "metric": metric,
                    "cv": round(cv, 4),
                    "mean": round(avg, 4),
                    "samples": len(values),
                },
            )
        )
    return findings
