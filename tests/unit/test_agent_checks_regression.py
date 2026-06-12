"""Tests for the regression check."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentops.agent.checks.regression import run_regression_check
from agentops.agent.config import RegressionCheckConfig
from agentops.agent.findings import Severity
from agentops.agent.sources.results_history import ResultsHistory, RunSummary


def _run(
    metrics: dict,
    run_id: str = "r",
    offset_days: int = 0,
    fingerprint: str | None = None,
) -> RunSummary:
    return RunSummary(
        run_id=run_id,
        timestamp=datetime.now(timezone.utc) + timedelta(days=offset_days),
        metrics=metrics,
        run_pass=True,
        items_total=1,
        items_passed_all=1,
        raw_path=Path("dummy"),
        methodology_fingerprint=fingerprint,
    )


def test_regression_check_flags_drop_above_threshold() -> None:
    history = ResultsHistory(
        runs=[
            _run({"coherence": 4.5}, run_id="b1", offset_days=-3),
            _run({"coherence": 4.5}, run_id="b2", offset_days=-2),
            _run({"coherence": 3.0}, run_id="latest", offset_days=0),
        ]
    )
    config = RegressionCheckConfig(
        metrics=["coherence"], threshold_drop=0.10, min_runs=3
    )
    findings = run_regression_check(history, config)

    assert len(findings) == 1
    assert findings[0].id == "regression.coherence"
    # Drop is ~33% which is >= 2*threshold (20%) -> CRITICAL.
    assert findings[0].severity == Severity.CRITICAL
    assert findings[0].evidence["latest_run_id"] == "latest"


def test_regression_check_ignores_small_drops() -> None:
    history = ResultsHistory(
        runs=[
            _run({"coherence": 4.5}, run_id="b1", offset_days=-3),
            _run({"coherence": 4.5}, run_id="b2", offset_days=-2),
            _run({"coherence": 4.4}, run_id="latest", offset_days=0),
        ]
    )
    config = RegressionCheckConfig(
        metrics=["coherence"], threshold_drop=0.10, min_runs=3
    )
    findings = run_regression_check(history, config)
    assert findings == []


def test_regression_check_skips_when_baseline_too_small() -> None:
    history = ResultsHistory(runs=[_run({"coherence": 4.5}, run_id="only")])
    config = RegressionCheckConfig(metrics=["coherence"], min_runs=3)
    findings = run_regression_check(history, config)
    assert findings == []


def test_regression_check_ignores_baselines_with_mismatched_methodology() -> None:
    """Baselines from a different dataset/evaluator set must not count."""
    history = ResultsHistory(
        runs=[
            # These baselines used a different methodology (e.g. smoke dataset)
            # and must be excluded from the comparison.
            _run({"coherence": 4.5}, run_id="b1", offset_days=-3, fingerprint="A"),
            _run({"coherence": 4.5}, run_id="b2", offset_days=-2, fingerprint="A"),
            _run({"coherence": 3.0}, run_id="latest", offset_days=0, fingerprint="B"),
        ]
    )
    config = RegressionCheckConfig(
        metrics=["coherence"], threshold_drop=0.10, min_runs=3
    )
    findings = run_regression_check(history, config)
    assert findings == []


def test_regression_check_uses_matching_methodology_baselines() -> None:
    """Baselines with the same fingerprint as the latest run drive the check."""
    history = ResultsHistory(
        runs=[
            _run({"coherence": 4.5}, run_id="other", offset_days=-4, fingerprint="A"),
            _run({"coherence": 4.5}, run_id="b1", offset_days=-3, fingerprint="B"),
            _run({"coherence": 4.5}, run_id="b2", offset_days=-2, fingerprint="B"),
            _run({"coherence": 3.0}, run_id="latest", offset_days=0, fingerprint="B"),
        ]
    )
    config = RegressionCheckConfig(
        metrics=["coherence"], threshold_drop=0.10, min_runs=3
    )
    findings = run_regression_check(history, config)
    assert len(findings) == 1
    assert findings[0].evidence["baseline_runs"] == 2
