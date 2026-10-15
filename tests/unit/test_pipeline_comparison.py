"""Tests for ``--baseline`` comparison, including regression-insight wiring."""

from __future__ import annotations

from pathlib import Path

from agentops.core.results import (
    CommitInfo,
    RunResult,
    RunSummary,
    TargetInfo,
    ThresholdEvaluation,
)
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
    avg_latency_seconds: float | None = None,
    commit: CommitInfo | None,
) -> RunResult:
    metrics = {"accuracy": accuracy}
    if coherence is not None:
        metrics["coherence"] = coherence
    if avg_latency_seconds is not None:
        metrics["avg_latency_seconds"] = avg_latency_seconds
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
    assert len(info.insight.regressed_metrics) == 1
    assert info.insight.regressed_metrics[0].metric == "accuracy"
    assert info.insight.regressed_metrics[0].from_value == 0.91
    assert info.insight.regressed_metrics[0].to_value == 0.79


def test_build_comparison_no_insight_without_commit_metadata():
    baseline = _run(version="3", deployment="gpt-4o", accuracy=0.91, commit=None)
    current = _run(version="4", deployment="gpt-4o-mini", accuracy=0.79, commit=_commit("b" * 40))

    info = comparison.build_comparison(
        current=current, baseline=baseline, baseline_path=Path(".agentops/baseline/results.json")
    )

    assert info.insight is None


def test_build_comparison_lists_every_regressed_metric_ordered_worst_first():
    """`accuracy` and `coherence` both regress at once (13% and 56%
    respectively) - both must appear in the insight, worst first, not just
    whichever metric name sorts first alphabetically or a single "worst"
    one with the rest silently dropped.
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
    assert [m.metric for m in info.insight.regressed_metrics] == ["coherence", "accuracy"]


def test_build_comparison_no_insight_when_nothing_regressed():
    baseline = _run(version="3", deployment="gpt-4o", accuracy=0.79, commit=_commit("a" * 40))
    current = _run(version="4", deployment="gpt-4o-mini", accuracy=0.91, commit=_commit("b" * 40))

    info = comparison.build_comparison(
        current=current, baseline=baseline, baseline_path=Path(".agentops/baseline/results.json")
    )

    assert info.insight is None


def test_latency_drop_is_improved_not_regressed():
    """``avg_latency_seconds`` is lower-is-better - a drop from 8s to 3s is
    an improvement, not a regression, even though the raw value went down."""
    baseline = _run(accuracy=0.91, avg_latency_seconds=8.0, commit=_commit("a" * 40))
    current = _run(accuracy=0.91, avg_latency_seconds=3.0, commit=_commit("b" * 40))

    info = comparison.build_comparison(
        current=current, baseline=baseline, baseline_path=Path(".agentops/baseline/results.json")
    )

    latency_metric = next(m for m in info.metrics if m.metric == "avg_latency_seconds")
    assert latency_metric.direction == "improved"
    assert info.insight is None


def test_latency_increase_is_regressed_and_explained():
    baseline = _run(accuracy=0.91, avg_latency_seconds=3.0, commit=_commit("a" * 40))
    current = _run(accuracy=0.91, avg_latency_seconds=8.0, commit=_commit("b" * 40))

    info = comparison.build_comparison(
        current=current, baseline=baseline, baseline_path=Path(".agentops/baseline/results.json")
    )

    latency_metric = next(m for m in info.metrics if m.metric == "avg_latency_seconds")
    assert latency_metric.direction == "regressed"
    assert info.insight is not None
    assert info.insight.regressed_metrics[0].metric == "avg_latency_seconds"


def _with_threshold(run: RunResult, *, metric: str, criteria: str) -> RunResult:
    run.thresholds = [
        ThresholdEvaluation(metric=metric, criteria=criteria, expected="", actual="", passed=True)
    ]
    return run


def test_custom_lower_is_better_metric_drop_is_improved_not_regressed():
    """``LOWER_IS_BETTER_METRICS`` only covers the one built-in metric known
    to commonly be lower-is-better (latency) - any other metric name can
    still be configured as lower-is-better via an explicit `<=`/`<`
    threshold in agentops.yaml (or a custom metric execution: azd imports).
    A drop in such a metric must be read from its own recorded threshold
    criteria, not assumed higher-is-better by default."""
    baseline = _with_threshold(
        _run(accuracy=0.91, commit=_commit("a" * 40)),
        metric="error_rate",
        criteria="<=",
    )
    baseline.aggregate_metrics["error_rate"] = 0.10
    current = _with_threshold(
        _run(accuracy=0.91, commit=_commit("b" * 40)), metric="error_rate", criteria="<="
    )
    current.aggregate_metrics["error_rate"] = 0.04

    info = comparison.build_comparison(
        current=current, baseline=baseline, baseline_path=Path(".agentops/baseline/results.json")
    )

    error_rate_metric = next(m for m in info.metrics if m.metric == "error_rate")
    assert error_rate_metric.direction == "improved"
    assert info.insight is None


def test_boolean_false_threshold_metric_drop_is_improved():
    """A `thresholds: { has_error: false }` metric (values 0/1) passes
    when it's 0 - moving from 1 to 0 is an improvement, same direction as
    a `<=` threshold, not the higher-is-better default."""
    baseline = _with_threshold(
        _run(accuracy=0.91, commit=_commit("a" * 40)), metric="has_error", criteria="false"
    )
    baseline.aggregate_metrics["has_error"] = 1.0
    current = _with_threshold(
        _run(accuracy=0.91, commit=_commit("b" * 40)), metric="has_error", criteria="false"
    )
    current.aggregate_metrics["has_error"] = 0.0

    info = comparison.build_comparison(
        current=current, baseline=baseline, baseline_path=Path(".agentops/baseline/results.json")
    )

    has_error_metric = next(m for m in info.metrics if m.metric == "has_error")
    assert has_error_metric.direction == "improved"


def test_insight_carries_baseline_report_url_from_sidecar_file(tmp_path: Path):
    """``baseline`` is a prior, fully-published run - a sidecar
    ``cloud_evaluation.json`` next to its ``results.json`` (as a completed
    local ``publish: true`` run would have) must surface as
    ``from_report_url``. ``current`` is still mid-orchestration (no file on
    disk yet), so ``to_report_url`` stays ``None`` unless its own in-memory
    config already has it (``execution: cloud``)."""
    baseline_dir = tmp_path / "baseline"
    baseline_dir.mkdir()
    baseline_path = baseline_dir / "results.json"
    (baseline_dir / "cloud_evaluation.json").write_text(
        '{"report_url": "https://ai.azure.com/foundry/baseline"}', encoding="utf-8"
    )

    baseline = _run(version="3", deployment="gpt-4o", accuracy=0.91, commit=_commit("a" * 40))
    current = _run(version="4", deployment="gpt-4o-mini", accuracy=0.79, commit=_commit("b" * 40))

    info = comparison.build_comparison(current=current, baseline=baseline, baseline_path=baseline_path)

    assert info.insight is not None
    assert info.insight.from_report_url == "https://ai.azure.com/foundry/baseline"
    assert info.insight.to_report_url is None
