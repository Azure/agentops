"""Tests for the operational-excellence check (stale_evaluation)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentops.agent.checks.opex import run_opex_check
from agentops.agent.config import OpexCheckConfig
from agentops.agent.findings import Category, Severity
from agentops.agent.sources.results_history import ResultsHistory, RunSummary


def _history(age_days: float) -> ResultsHistory:
    ts = datetime.now(timezone.utc) - timedelta(days=age_days)
    run = RunSummary(
        run_id="run-1",
        timestamp=ts,
        metrics={},
        run_pass=True,
        items_total=1,
        items_passed_all=1,
        raw_path=Path("."),
    )
    return ResultsHistory(runs=[run])


def test_emits_warning_when_run_is_older_than_threshold() -> None:
    findings = run_opex_check(_history(20), OpexCheckConfig(stale_after_days=14))
    assert len(findings) == 1
    f = findings[0]
    assert f.id == "opex.stale_evaluation"
    assert f.severity == Severity.WARNING
    assert f.category == Category.OPERATIONAL_EXCELLENCE


def test_promotes_to_critical_when_far_past_threshold() -> None:
    findings = run_opex_check(_history(40), OpexCheckConfig(stale_after_days=14))
    assert findings[0].severity == Severity.CRITICAL


def test_silent_when_run_is_fresh() -> None:
    assert run_opex_check(_history(2), OpexCheckConfig(stale_after_days=14)) == []


def test_silent_when_disabled() -> None:
    assert (
        run_opex_check(
            _history(40), OpexCheckConfig(enabled=False, stale_after_days=14)
        )
        == []
    )


def test_silent_when_no_history() -> None:
    assert run_opex_check(ResultsHistory(runs=[]), OpexCheckConfig()) == []


def test_silent_when_latest_has_no_timestamp() -> None:
    run = RunSummary(
        run_id="x",
        timestamp=None,
        metrics={},
        run_pass=True,
        items_total=0,
        items_passed_all=0,
        raw_path=Path("."),
    )
    assert run_opex_check(ResultsHistory(runs=[run]), OpexCheckConfig()) == []


# ---------------------------------------------------------------------------
# flaky_metric
# ---------------------------------------------------------------------------


def _runs_with_metric_series(values: list) -> ResultsHistory:
    runs = []
    base = datetime.now(timezone.utc) - timedelta(days=len(values))
    for i, v in enumerate(values):
        runs.append(
            RunSummary(
                run_id=f"r{i}",
                timestamp=base + timedelta(days=i),
                metrics={"coherence": v},
                run_pass=True,
                items_total=1,
                items_passed_all=1,
                raw_path=Path("."),
            )
        )
    return ResultsHistory(runs=runs)


def test_flaky_metric_emitted_for_high_cv() -> None:
    # Coherence oscillates between 1.0 and 4.0 → CV well above 30%.
    history = _runs_with_metric_series([1.0, 4.0, 1.5, 3.5, 2.0])
    findings = run_opex_check(history, OpexCheckConfig())
    flaky = [f for f in findings if f.id.startswith("opex.flaky_metric")]
    assert len(flaky) == 1
    assert flaky[0].id == "opex.flaky_metric.coherence"
    assert flaky[0].evidence["samples"] == 5


def test_flaky_metric_silent_for_stable_series() -> None:
    history = _runs_with_metric_series([3.0, 3.05, 3.02, 2.98, 3.01])
    flaky = [
        f
        for f in run_opex_check(history, OpexCheckConfig())
        if f.id.startswith("opex.flaky_metric")
    ]
    assert flaky == []


def test_flaky_metric_silent_with_too_few_runs() -> None:
    history = _runs_with_metric_series([1.0, 4.0])
    flaky = [
        f
        for f in run_opex_check(history, OpexCheckConfig())
        if f.id.startswith("opex.flaky_metric")
    ]
    assert flaky == []


def test_flaky_metric_silent_for_near_zero_mean() -> None:
    # Tiny values blow up CV; the rule explicitly skips them.
    history = _runs_with_metric_series([0.01, 0.04, 0.02, 0.03, 0.05])
    flaky = [
        f
        for f in run_opex_check(history, OpexCheckConfig())
        if f.id.startswith("opex.flaky_metric")
    ]
    assert flaky == []


def test_flaky_metric_silent_when_methodologies_differ() -> None:
    """Runs with mismatched methodologies should not blend into the CV."""
    base = datetime.now(timezone.utc) - timedelta(days=5)
    # Latest run uses fingerprint B; only the latest matches itself, so there
    # are not enough comparable runs to compute a CV.
    runs = []
    for i, (value, fp) in enumerate(
        [(1.0, "A"), (4.0, "A"), (1.5, "A"), (3.5, "A"), (3.0, "B")]
    ):
        runs.append(
            RunSummary(
                run_id=f"r{i}",
                timestamp=base + timedelta(days=i),
                metrics={"coherence": value},
                run_pass=True,
                items_total=1,
                items_passed_all=1,
                raw_path=Path("."),
                methodology_fingerprint=fp,
            )
        )
    history = ResultsHistory(runs=runs)
    flaky = [
        f
        for f in run_opex_check(history, OpexCheckConfig())
        if f.id.startswith("opex.flaky_metric")
    ]
    assert flaky == []
