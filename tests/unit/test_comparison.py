"""Unit tests for baseline comparison service and models."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentops.core.models import (
    ComparisonResult,
    ComparisonSummary,
    ItemDelta,
    MetricDelta,
    RunReference,
    RunResult,
    ThresholdDelta,
)
from agentops.core.reporter import generate_comparison_markdown
from agentops.services.comparison import (
    _compute_direction,
    _compute_metric_direction,
    _compute_item_deltas,
    _compute_metric_deltas,
    _compute_threshold_deltas,
    _build_summary,
    _lower_is_better_metrics,
    compare_runs,
    _resolve_run_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample_result(
    *,
    groundedness: float = 0.84,
    relevance: float = 0.83,
    overall_passed: bool = True,
    row1_groundedness: float = 0.90,
    row1_relevance: float = 0.85,
    row2_groundedness: float = 0.78,
    row2_relevance: float = 0.81,
) -> RunResult:
    return RunResult.model_validate(
        {
            "version": 1,
            "status": "completed",
            "bundle": {"name": "rag_baseline", "path": ".agentops/bundles/rag_baseline.yaml"},
            "dataset": {"name": "smoke", "path": ".agentops/datasets/smoke.yaml"},
            "execution": {
                "backend": "subprocess",
                "command": "python -m fake_eval_runner",
                "started_at": "2026-03-01T10:00:00Z",
                "finished_at": "2026-03-01T10:00:05Z",
                "duration_seconds": 5.0,
                "exit_code": 0,
            },
            "metrics": [
                {"name": "groundedness", "value": groundedness},
                {"name": "relevance", "value": relevance},
            ],
            "row_metrics": [
                {
                    "row_index": 1,
                    "metrics": [
                        {"name": "groundedness", "value": row1_groundedness},
                        {"name": "relevance", "value": row1_relevance},
                    ],
                },
                {
                    "row_index": 2,
                    "metrics": [
                        {"name": "groundedness", "value": row2_groundedness},
                        {"name": "relevance", "value": row2_relevance},
                    ],
                },
            ],
            "item_evaluations": [
                {
                    "row_index": 1,
                    "passed_all": True,
                    "thresholds": [
                        {"row_index": 1, "evaluator": "groundedness", "criteria": ">=", "expected": "0.800000", "actual": str(row1_groundedness), "passed": row1_groundedness >= 0.8},
                    ],
                },
                {
                    "row_index": 2,
                    "passed_all": overall_passed,
                    "thresholds": [
                        {"row_index": 2, "evaluator": "groundedness", "criteria": ">=", "expected": "0.800000", "actual": str(row2_groundedness), "passed": row2_groundedness >= 0.8},
                    ],
                },
            ],
            "thresholds": [
                {"evaluator": "groundedness", "criteria": ">=", "expected": "0.800000", "actual": f"{groundedness:.6f}", "passed": groundedness >= 0.8},
                {"evaluator": "relevance", "criteria": ">=", "expected": "0.800000", "actual": f"{relevance:.6f}", "passed": relevance >= 0.8},
            ],
            "summary": {
                "metrics_count": 2,
                "thresholds_count": 2,
                "thresholds_passed": 2 if overall_passed else 1,
                "thresholds_failed": 0 if overall_passed else 1,
                "overall_passed": overall_passed,
            },
        }
    )


def _sample_result_with_latency(
    *,
    similarity: float = 5.0,
    latency: float = 5.0,
) -> RunResult:
    """Sample result with both a higher-is-better (similarity) and lower-is-better (latency) metric."""
    return RunResult.model_validate(
        {
            "version": 1,
            "status": "completed",
            "bundle": {"name": "model_direct", "path": ".agentops/bundles/model_direct.yaml"},
            "dataset": {"name": "smoke", "path": ".agentops/datasets/smoke.yaml"},
            "execution": {
                "backend": "foundry",
                "command": "foundry.cloud_evaluation",
                "started_at": "2026-03-01T10:00:00Z",
                "finished_at": "2026-03-01T10:00:05Z",
                "duration_seconds": 5.0,
                "exit_code": 0,
            },
            "metrics": [
                {"name": "SimilarityEvaluator", "value": similarity},
                {"name": "avg_latency_seconds", "value": latency},
            ],
            "row_metrics": [
                {
                    "row_index": 1,
                    "metrics": [
                        {"name": "SimilarityEvaluator", "value": similarity},
                        {"name": "avg_latency_seconds", "value": latency},
                    ],
                },
            ],
            "item_evaluations": [
                {
                    "row_index": 1,
                    "passed_all": True,
                    "thresholds": [
                        {"row_index": 1, "evaluator": "SimilarityEvaluator", "criteria": ">=", "expected": "3.000000", "actual": str(similarity), "passed": similarity >= 3},
                        {"row_index": 1, "evaluator": "avg_latency_seconds", "criteria": "<=", "expected": "10.000000", "actual": str(latency), "passed": latency <= 10},
                    ],
                },
            ],
            "thresholds": [
                {"evaluator": "SimilarityEvaluator", "criteria": ">=", "expected": "3.000000", "actual": f"{similarity:.6f}", "passed": similarity >= 3},
                {"evaluator": "avg_latency_seconds", "criteria": "<=", "expected": "10.000000", "actual": f"{latency:.6f}", "passed": latency <= 10},
            ],
            "summary": {
                "metrics_count": 2,
                "thresholds_count": 2,
                "thresholds_passed": 2,
                "thresholds_failed": 0,
                "overall_passed": True,
            },
        }
    )


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestComparisonModels:
    def test_metric_delta_roundtrip(self) -> None:
        d = MetricDelta(
            name="groundedness",
            baseline_value=0.84,
            current_value=0.90,
            delta=0.06,
            delta_percent=7.14,
            direction="improved",
        )
        data = d.model_dump()
        restored = MetricDelta.model_validate(data)
        assert restored.name == "groundedness"
        assert restored.direction == "improved"

    def test_threshold_delta_roundtrip(self) -> None:
        d = ThresholdDelta(
            evaluator="groundedness",
            criteria=">=",
            baseline_passed=True,
            current_passed=False,
            flipped=True,
        )
        data = d.model_dump()
        restored = ThresholdDelta.model_validate(data)
        assert restored.flipped is True

    def test_comparison_result_roundtrip(self) -> None:
        result = ComparisonResult(
            version=1,
            baseline=RunReference(run_id="run1", bundle_name="b", dataset_name="d", started_at="t1"),
            current=RunReference(run_id="run2", bundle_name="b", dataset_name="d", started_at="t2"),
            metric_deltas=[],
            threshold_deltas=[],
            item_deltas=[],
            summary=ComparisonSummary(
                metrics_improved=0,
                metrics_regressed=0,
                metrics_unchanged=0,
                thresholds_flipped_pass_to_fail=0,
                thresholds_flipped_fail_to_pass=0,
                items_newly_failing=0,
                items_newly_passing=0,
                has_regressions=False,
            ),
        )
        payload = json.loads(result.model_dump_json())
        restored = ComparisonResult.model_validate(payload)
        assert restored.version == 1
        assert restored.summary.has_regressions is False


# ---------------------------------------------------------------------------
# Direction helper
# ---------------------------------------------------------------------------


class TestComputeDirection:
    def test_positive_delta_is_improved(self) -> None:
        assert _compute_direction(0.05) == "improved"

    def test_negative_delta_is_regressed(self) -> None:
        assert _compute_direction(-0.05) == "regressed"

    def test_zero_delta_is_unchanged(self) -> None:
        assert _compute_direction(0.0) == "unchanged"


class TestComputeMetricDirection:
    def test_higher_is_better_positive_delta(self) -> None:
        assert _compute_metric_direction(0.05, lower_is_better=False) == "improved"

    def test_higher_is_better_negative_delta(self) -> None:
        assert _compute_metric_direction(-0.05, lower_is_better=False) == "regressed"

    def test_lower_is_better_negative_delta_is_improved(self) -> None:
        assert _compute_metric_direction(-0.05, lower_is_better=True) == "improved"

    def test_lower_is_better_positive_delta_is_regressed(self) -> None:
        assert _compute_metric_direction(0.05, lower_is_better=True) == "regressed"

    def test_zero_is_unchanged_regardless_of_polarity(self) -> None:
        assert _compute_metric_direction(0.0, lower_is_better=False) == "unchanged"
        assert _compute_metric_direction(0.0, lower_is_better=True) == "unchanged"


# ---------------------------------------------------------------------------
# Metric delta computation
# ---------------------------------------------------------------------------


class TestComputeMetricDeltas:
    def test_basic_delta(self) -> None:
        baseline = _sample_result(groundedness=0.80, relevance=0.80)
        current = _sample_result(groundedness=0.90, relevance=0.75)
        deltas = _compute_metric_deltas(baseline, current)

        assert len(deltas) == 2
        g_delta = next(d for d in deltas if d.name == "groundedness")
        assert g_delta.direction == "improved"
        assert g_delta.delta == pytest.approx(0.10, abs=1e-6)

        r_delta = next(d for d in deltas if d.name == "relevance")
        assert r_delta.direction == "regressed"
        assert r_delta.delta == pytest.approx(-0.05, abs=1e-6)

    def test_unchanged_metric(self) -> None:
        baseline = _sample_result(groundedness=0.80)
        current = _sample_result(groundedness=0.80)
        deltas = _compute_metric_deltas(baseline, current)
        g_delta = next(d for d in deltas if d.name == "groundedness")
        assert g_delta.direction == "unchanged"
        assert g_delta.delta == 0.0

    def test_delta_percent_when_baseline_nonzero(self) -> None:
        baseline = _sample_result(groundedness=0.50)
        current = _sample_result(groundedness=0.75)
        deltas = _compute_metric_deltas(baseline, current)
        g_delta = next(d for d in deltas if d.name == "groundedness")
        assert g_delta.delta_percent == pytest.approx(50.0, abs=1e-6)

    def test_delta_percent_none_when_baseline_zero(self) -> None:
        baseline = _sample_result(groundedness=0.0)
        current = _sample_result(groundedness=0.5)
        deltas = _compute_metric_deltas(baseline, current)
        g_delta = next(d for d in deltas if d.name == "groundedness")
        assert g_delta.delta_percent is None

    def test_lower_is_better_latency_decrease_is_improved(self) -> None:
        baseline = _sample_result_with_latency(similarity=5.0, latency=6.0)
        current = _sample_result_with_latency(similarity=5.0, latency=4.0)
        deltas = _compute_metric_deltas(baseline, current)

        lat = next(d for d in deltas if d.name == "avg_latency_seconds")
        assert lat.delta == pytest.approx(-2.0, abs=1e-6)
        assert lat.direction == "improved"

    def test_lower_is_better_latency_increase_is_regressed(self) -> None:
        baseline = _sample_result_with_latency(similarity=5.0, latency=4.0)
        current = _sample_result_with_latency(similarity=5.0, latency=8.0)
        deltas = _compute_metric_deltas(baseline, current)

        lat = next(d for d in deltas if d.name == "avg_latency_seconds")
        assert lat.delta == pytest.approx(4.0, abs=1e-6)
        assert lat.direction == "regressed"

    def test_higher_is_better_still_works_alongside_lower(self) -> None:
        baseline = _sample_result_with_latency(similarity=5.0, latency=6.0)
        current = _sample_result_with_latency(similarity=3.0, latency=4.0)
        deltas = _compute_metric_deltas(baseline, current)

        sim = next(d for d in deltas if d.name == "SimilarityEvaluator")
        assert sim.direction == "regressed"

        lat = next(d for d in deltas if d.name == "avg_latency_seconds")
        assert lat.direction == "improved"


# ---------------------------------------------------------------------------
# Threshold delta computation
# ---------------------------------------------------------------------------


class TestComputeThresholdDeltas:
    def test_flipped_threshold(self) -> None:
        baseline = _sample_result(groundedness=0.90, overall_passed=True)
        current = _sample_result(groundedness=0.70, overall_passed=False)
        deltas = _compute_threshold_deltas(baseline, current)

        g_delta = next(d for d in deltas if d.evaluator == "groundedness")
        assert g_delta.flipped is True
        assert g_delta.baseline_passed is True
        assert g_delta.current_passed is False

    def test_stable_threshold(self) -> None:
        baseline = _sample_result(groundedness=0.90)
        current = _sample_result(groundedness=0.85)
        deltas = _compute_threshold_deltas(baseline, current)

        g_delta = next(d for d in deltas if d.evaluator == "groundedness")
        assert g_delta.flipped is False


# ---------------------------------------------------------------------------
# Item delta computation
# ---------------------------------------------------------------------------


class TestComputeItemDeltas:
    def test_item_regression(self) -> None:
        baseline = _sample_result(row2_groundedness=0.85, overall_passed=True)
        current = _sample_result(row2_groundedness=0.70, overall_passed=False)
        deltas = _compute_item_deltas(baseline, current, ["groundedness", "relevance"])

        row2 = next(d for d in deltas if d.row_index == 2)
        assert row2.baseline_passed_all is True
        assert row2.current_passed_all is False

    def test_item_metric_deltas_computed(self) -> None:
        baseline = _sample_result(row1_groundedness=0.80)
        current = _sample_result(row1_groundedness=0.90)
        deltas = _compute_item_deltas(baseline, current, ["groundedness"])

        row1 = next(d for d in deltas if d.row_index == 1)
        assert len(row1.metric_deltas) >= 1
        g = next(d for d in row1.metric_deltas if d.name == "groundedness")
        assert g.delta == pytest.approx(0.10, abs=1e-6)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


class TestBuildSummary:
    def test_regression_detected(self) -> None:
        metric_deltas = [
            MetricDelta(name="g", baseline_value=0.9, current_value=0.7, delta=-0.2, direction="regressed"),
        ]
        threshold_deltas = [
            ThresholdDelta(evaluator="g", criteria=">=", baseline_passed=True, current_passed=False, flipped=True),
        ]
        item_deltas = [
            ItemDelta(row_index=1, baseline_passed_all=True, current_passed_all=False),
        ]

        summary = _build_summary(metric_deltas, threshold_deltas, item_deltas)
        assert summary.has_regressions is True
        assert summary.metrics_regressed == 1
        assert summary.thresholds_flipped_pass_to_fail == 1
        assert summary.items_newly_failing == 1

    def test_no_regression(self) -> None:
        metric_deltas = [
            MetricDelta(name="g", baseline_value=0.7, current_value=0.9, delta=0.2, direction="improved"),
        ]
        threshold_deltas = [
            ThresholdDelta(evaluator="g", criteria=">=", baseline_passed=False, current_passed=True, flipped=True),
        ]
        item_deltas = [
            ItemDelta(row_index=1, baseline_passed_all=False, current_passed_all=True),
        ]

        summary = _build_summary(metric_deltas, threshold_deltas, item_deltas)
        assert summary.has_regressions is False
        assert summary.metrics_improved == 1
        assert summary.thresholds_flipped_fail_to_pass == 1
        assert summary.items_newly_passing == 1


# ---------------------------------------------------------------------------
# End-to-end compare_runs
# ---------------------------------------------------------------------------


class TestCompareRuns:
    def test_comparison_with_regression(self, tmp_path: Path) -> None:
        baseline = _sample_result(groundedness=0.90, relevance=0.90, overall_passed=True)
        current = _sample_result(groundedness=0.70, relevance=0.95, overall_passed=False)

        baseline_path = tmp_path / "baseline" / "results.json"
        current_path = tmp_path / "current" / "results.json"
        baseline_path.parent.mkdir()
        current_path.parent.mkdir()
        baseline_path.write_text(json.dumps(baseline.model_dump(mode="json"), indent=2))
        current_path.write_text(json.dumps(current.model_dump(mode="json"), indent=2))

        result = compare_runs(baseline_path, current_path, "baseline", "current")

        assert result.summary.has_regressions is True
        assert result.summary.metrics_regressed >= 1
        g_delta = next(d for d in result.metric_deltas if d.name == "groundedness")
        assert g_delta.direction == "regressed"
        r_delta = next(d for d in result.metric_deltas if d.name == "relevance")
        assert r_delta.direction == "improved"

    def test_comparison_no_regression(self, tmp_path: Path) -> None:
        baseline = _sample_result(groundedness=0.80, relevance=0.80, overall_passed=True)
        current = _sample_result(groundedness=0.90, relevance=0.90, overall_passed=True)

        baseline_path = tmp_path / "baseline" / "results.json"
        current_path = tmp_path / "current" / "results.json"
        baseline_path.parent.mkdir()
        current_path.parent.mkdir()
        baseline_path.write_text(json.dumps(baseline.model_dump(mode="json"), indent=2))
        current_path.write_text(json.dumps(current.model_dump(mode="json"), indent=2))

        result = compare_runs(baseline_path, current_path, "baseline", "current")

        assert result.summary.has_regressions is False
        assert result.summary.metrics_improved == 2


# ---------------------------------------------------------------------------
# Run path resolution
# ---------------------------------------------------------------------------


class TestResolveRunPath:
    def test_resolve_absolute_file(self, tmp_path: Path) -> None:
        f = tmp_path / "results.json"
        f.write_text("{}")
        assert _resolve_run_path(str(f)) == f

    def test_resolve_absolute_dir(self, tmp_path: Path) -> None:
        d = tmp_path / "run1"
        d.mkdir()
        f = d / "results.json"
        f.write_text("{}")
        assert _resolve_run_path(str(d)) == f

    def test_resolve_by_run_id(self, tmp_path: Path) -> None:
        results_dir = tmp_path / "results" / "2026-03-01_100000"
        results_dir.mkdir(parents=True)
        f = results_dir / "results.json"
        f.write_text("{}")
        resolved = _resolve_run_path("2026-03-01_100000", workspace_dir=tmp_path)
        assert resolved == f.resolve()

    def test_resolve_latest(self, tmp_path: Path) -> None:
        latest_dir = tmp_path / "results" / "latest"
        latest_dir.mkdir(parents=True)
        f = latest_dir / "results.json"
        f.write_text("{}")
        resolved = _resolve_run_path("latest", workspace_dir=tmp_path)
        assert resolved == f.resolve()

    def test_resolve_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            _resolve_run_path("nonexistent_run", workspace_dir=tmp_path)


# ---------------------------------------------------------------------------
# Comparison report markdown
# ---------------------------------------------------------------------------


class TestComparisonReport:
    def test_report_contains_required_sections(self) -> None:
        result = ComparisonResult(
            version=1,
            baseline=RunReference(run_id="run1", bundle_name="rag_baseline", dataset_name="smoke", started_at="t1"),
            current=RunReference(run_id="run2", bundle_name="rag_baseline", dataset_name="smoke", started_at="t2"),
            metric_deltas=[
                MetricDelta(name="groundedness", baseline_value=0.9, current_value=0.7, delta=-0.2, delta_percent=-22.22, direction="regressed"),
            ],
            threshold_deltas=[
                ThresholdDelta(evaluator="groundedness", criteria=">=", baseline_passed=True, current_passed=False, flipped=True),
            ],
            item_deltas=[
                ItemDelta(row_index=1, baseline_passed_all=True, current_passed_all=False),
            ],
            summary=ComparisonSummary(
                metrics_improved=0,
                metrics_regressed=1,
                metrics_unchanged=0,
                thresholds_flipped_pass_to_fail=1,
                thresholds_flipped_fail_to_pass=0,
                items_newly_failing=1,
                items_newly_passing=0,
                has_regressions=True,
            ),
        )

        md = generate_comparison_markdown(result)

        assert "# AgentOps Comparison Report" in md
        assert "## Overview" in md
        assert "REGRESSIONS DETECTED" in md
        assert "## Metric Deltas" in md
        assert "groundedness" in md
        assert "regressed" in md
        assert "## Threshold Changes" in md
        assert "REGRESSION" in md
        assert "## Item Changes" in md

    def test_report_no_regressions(self) -> None:
        result = ComparisonResult(
            version=1,
            baseline=RunReference(run_id="run1", bundle_name="b", dataset_name="d", started_at="t1"),
            current=RunReference(run_id="run2", bundle_name="b", dataset_name="d", started_at="t2"),
            metric_deltas=[
                MetricDelta(name="g", baseline_value=0.7, current_value=0.9, delta=0.2, direction="improved"),
            ],
            threshold_deltas=[],
            item_deltas=[],
            summary=ComparisonSummary(
                metrics_improved=1,
                metrics_regressed=0,
                metrics_unchanged=0,
                thresholds_flipped_pass_to_fail=0,
                thresholds_flipped_fail_to_pass=0,
                items_newly_failing=0,
                items_newly_passing=0,
                has_regressions=False,
            ),
        )

        md = generate_comparison_markdown(result)
        assert "NO REGRESSIONS" in md
        assert "No threshold changes detected" in md
        assert "No item-level changes detected" in md
