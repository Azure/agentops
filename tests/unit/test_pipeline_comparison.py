"""Tests for ``--baseline`` comparison, including regression-insight wiring."""

from __future__ import annotations

from pathlib import Path

from agentops.core.results import CommitInfo, RunResult, RunSummary, TargetInfo
from agentops.pipeline import comparison


def _commit(sha: str) -> CommitInfo:
    return CommitInfo(
        sha=sha,
        short_sha=sha[:7],
        subject="A commit",
        author="Dev",
        authored_at="2026-09-01T10:00:00+00:00",
        source="ci",
    )


def _run(
    *,
    version: str = "3",
    deployment: str | None = "gpt-4o",
    accuracy: float,
    coherence: float | None = None,
    commit: CommitInfo | None,
) -> RunResult:
    metrics = {"accuracy": accuracy}
    if coherence is not None:
        metrics["coherence"] = coherence
    return RunResult(
        started_at="2026-09-01T10:00:00+00:00",
        finished_at="2026-09-01T10:00:01+00:00",
        duration_seconds=1.0,
        target=TargetInfo(
            kind="foundry_prompt",
            raw=f"greeter:{version}",
            name="greeter",
            version=version,
            deployment=deployment,
        ),
        dataset_path="data/smoke.jsonl",
        evaluators=["CoherenceEvaluator"],
        aggregate_metrics=metrics,
        summary=RunSummary(
            items_total=1,
            items_passed_all=1,
            items_pass_rate=1.0,
            thresholds_total=0,
            thresholds_passed=0,
            threshold_pass_rate=1.0,
            overall_passed=True,
        ),
        commit=commit,
    )


def test_build_comparison_attaches_insight_when_regressed_and_commits_known():
    baseline = _run(version="3", deployment="gpt-4o", accuracy=0.91, commit=_commit("a" * 40))
    current = _run(version="4", deployment="gpt-4o-mini", accuracy=0.79, commit=_commit("b" * 40))

    info = comparison.build_comparison(
        current=current, baseline=baseline, baseline_path=Path(".agentops/baseline/results.json")
    )

    assert info.insight is not None
    assert info.insight.metric == "accuracy"
    assert info.insight.from_value == 0.91
    assert info.insight.to_value == 0.79


def test_build_comparison_no_insight_without_commit_metadata():
    baseline = _run(version="3", deployment="gpt-4o", accuracy=0.91, commit=None)
    current = _run(version="4", deployment="gpt-4o-mini", accuracy=0.79, commit=_commit("b" * 40))

    info = comparison.build_comparison(
        current=current, baseline=baseline, baseline_path=Path(".agentops/baseline/results.json")
    )

    assert info.insight is None


def test_build_comparison_picks_worst_relative_drop_not_alphabetical_first():
    """`accuracy` sorts before `coherence` alphabetically, but `coherence`
    dropped much more in relative terms (56% vs 13%) - the insight must be
    for `coherence`, not whichever metric name comes first.
    """
    baseline = _run(
        version="3", deployment="gpt-4o", accuracy=0.91, coherence=4.5, commit=_commit("a" * 40)
    )
    current = _run(
        version="4", deployment="gpt-4o-mini", accuracy=0.79, coherence=2.0, commit=_commit("b" * 40)
    )

    info = comparison.build_comparison(
        current=current, baseline=baseline, baseline_path=Path(".agentops/baseline/results.json")
    )

    assert info.insight is not None
    assert info.insight.metric == "coherence"


def test_build_comparison_no_insight_when_nothing_regressed():
    baseline = _run(version="3", deployment="gpt-4o", accuracy=0.79, commit=_commit("a" * 40))
    current = _run(version="4", deployment="gpt-4o-mini", accuracy=0.91, commit=_commit("b" * 40))

    info = comparison.build_comparison(
        current=current, baseline=baseline, baseline_path=Path(".agentops/baseline/results.json")
    )

    assert info.insight is None
