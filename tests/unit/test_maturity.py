"""Tests for the GenAIOps Maturity Model computation."""

from __future__ import annotations

from pathlib import Path

from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.maturity import compute_level, compute_level_from_ids
from agentops.agent.sources.results_history import ResultsHistory, RunSummary


def _f(fid: str) -> Finding:
    return Finding(
        id=fid,
        severity=Severity.WARNING,
        title="t",
        summary="s",
        recommendation="r",
        source="x",
        category=Category.OPERATIONAL_EXCELLENCE,
    )


def _history(n: int = 1) -> ResultsHistory:
    return ResultsHistory(
        runs=[
            RunSummary(
                run_id=f"r{i}",
                timestamp=None,
                metrics={},
                run_pass=True,
                items_total=0,
                items_passed_all=0,
                raw_path=Path("."),
            )
            for i in range(n)
        ]
    )


def test_l0_when_no_history() -> None:
    assessment = compute_level([], None)
    assert assessment.level == 0
    assert assessment.label == "Ad-hoc"
    assert assessment.next_gap == "no_history"


def test_l0_when_empty_history() -> None:
    assert compute_level([], ResultsHistory(runs=[])).level == 0


def test_l1_when_no_pr_gate() -> None:
    assessment = compute_level([_f("opex.no_pr_gate")], _history())
    assert assessment.level == 1
    assert assessment.next_gap == "opex.no_pr_gate"


def test_l2_when_pr_gate_but_no_deploy() -> None:
    assessment = compute_level([_f("opex.no_deploy_workflow")], _history())
    assert assessment.level == 2
    assert assessment.next_gap == "opex.no_deploy_workflow"


def test_l2_when_pr_gate_but_no_telemetry() -> None:
    assessment = compute_level([_f("errors.no_runtime_telemetry")], _history())
    assert assessment.level == 2


def test_l2_when_foundry_control_not_configured() -> None:
    assessment = compute_level(
        [_f("opex.no_foundry_control_configured")], _history()
    )
    assert assessment.level == 2
    assert assessment.next_gap == "opex.no_foundry_control_configured"


def test_l2_when_foundry_has_no_agents() -> None:
    assessment = compute_level([_f("opex.no_foundry_agents")], _history())
    assert assessment.level == 2


def test_l3_when_continuous_eval_missing() -> None:
    assessment = compute_level(
        [_f("safety.config.continuous_eval_missing")], _history()
    )
    assert assessment.level == 3


def test_l3_when_flaky_metric() -> None:
    assessment = compute_level([_f("opex.flaky_metric.coherence")], _history())
    assert assessment.level == 3
    assert assessment.next_gap == "opex.flaky_metric.coherence"


def test_l4_when_all_clear() -> None:
    assessment = compute_level([], _history())
    assert assessment.level == 4
    assert assessment.next_gap is None


def test_l4_does_not_regress_on_non_blocking_findings() -> None:
    # A high-severity finding that's NOT in any of the gating lists
    # must not pull the maturity level down.
    assessment = compute_level(
        [_f("regression.coherence"), _f("latency.eval_avg")], _history()
    )
    assert assessment.level == 4


def test_compute_level_from_ids_matches_full_version() -> None:
    findings = [_f("opex.no_pr_gate")]
    assert compute_level_from_ids(
        ["opex.no_pr_gate"], has_history=True
    ).level == compute_level(findings, _history()).level
