"""Unit tests for the unified comparison service and models."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentops.core.models import (
    ComparisonItemRow,
    ComparisonMetricRow,
    ComparisonResult,
    ComparisonSummary,
    ComparisonThresholdRow,
    RunReference,
    RunResult,
)
from agentops.core.reporter import generate_comparison_markdown
from agentops.services.comparison import (
    _compute_metric_direction,
    _lower_is_better_metrics,
    _resolve_run_path,
    compare_runs,
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
    row2_groundedness: float = 0.78,
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
                {"row_index": 1, "metrics": [{"name": "groundedness", "value": row1_groundedness}]},
                {"row_index": 2, "metrics": [{"name": "groundedness", "value": row2_groundedness}]},
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


def _sample_result_with_latency(*, similarity: float = 5.0, latency: float = 5.0) -> RunResult:
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
                {"row_index": 1, "metrics": [{"name": "SimilarityEvaluator", "value": similarity}, {"name": "avg_latency_seconds", "value": latency}]},
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


def _write_result(path: Path, result: RunResult) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.model_dump(mode="json"), indent=2))
    return path


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestComparisonModels:
    def test_comparison_result_roundtrip(self) -> None:
        result = ComparisonResult(
            version=1,
            runs=[
                RunReference(run_id="run1", bundle_name="b", dataset_name="d", started_at="t1"),
                RunReference(run_id="run2", bundle_name="b", dataset_name="d", started_at="t2"),
            ],
            metric_rows=[],
            threshold_rows=[],
            item_rows=[],
            summary=ComparisonSummary(run_count=2, any_regressions=False, runs_with_regressions=[]),
        )
        payload = json.loads(result.model_dump_json())
        restored = ComparisonResult.model_validate(payload)
        assert restored.version == 1
        assert restored.summary.any_regressions is False
        assert len(restored.runs) == 2


# ---------------------------------------------------------------------------
# Direction helpers
# ---------------------------------------------------------------------------


class TestComputeMetricDirection:
    def test_higher_is_better_positive_delta(self) -> None:
        assert _compute_metric_direction(0.05, lower_is_better=False) == "improved"

    def test_higher_is_better_negative_delta(self) -> None:
        assert _compute_metric_direction(-0.05, lower_is_better=False) == "regressed"

    def test_lower_is_better_negative_delta_is_improved(self) -> None:
        assert _compute_metric_direction(-0.05, lower_is_better=True) == "improved"

    def test_lower_is_better_positive_delta_is_regressed(self) -> None:
        assert _compute_metric_direction(0.05, lower_is_better=True) == "regressed"

    def test_zero_is_unchanged(self) -> None:
        assert _compute_metric_direction(0.0, lower_is_better=False) == "unchanged"
        assert _compute_metric_direction(0.0, lower_is_better=True) == "unchanged"


# ---------------------------------------------------------------------------
# compare_runs (2 runs)
# ---------------------------------------------------------------------------


class TestCompareRunsTwoRuns:
    def test_regression_detected(self, tmp_path: Path) -> None:
        baseline = _sample_result(groundedness=0.90, relevance=0.90, overall_passed=True)
        current = _sample_result(groundedness=0.70, relevance=0.95, overall_passed=False)

        bp = _write_result(tmp_path / "baseline" / "results.json", baseline)
        cp = _write_result(tmp_path / "current" / "results.json", current)

        result = compare_runs([bp, cp], ["baseline", "current"])

        assert result.summary.any_regressions is True
        assert len(result.summary.runs_with_regressions) >= 1
        g_row = next(r for r in result.metric_rows if r.name == "groundedness")
        assert g_row.directions[1] == "regressed"
        r_row = next(r for r in result.metric_rows if r.name == "relevance")
        assert r_row.directions[1] == "improved"

    def test_no_regression(self, tmp_path: Path) -> None:
        baseline = _sample_result(groundedness=0.80, relevance=0.80, overall_passed=True)
        current = _sample_result(groundedness=0.90, relevance=0.90, overall_passed=True)

        bp = _write_result(tmp_path / "baseline" / "results.json", baseline)
        cp = _write_result(tmp_path / "current" / "results.json", current)

        result = compare_runs([bp, cp], ["baseline", "current"])

        assert result.summary.any_regressions is False

    def test_lower_is_better_latency(self, tmp_path: Path) -> None:
        baseline = _sample_result_with_latency(similarity=5.0, latency=6.0)
        current = _sample_result_with_latency(similarity=5.0, latency=4.0)

        bp = _write_result(tmp_path / "baseline" / "results.json", baseline)
        cp = _write_result(tmp_path / "current" / "results.json", current)

        result = compare_runs([bp, cp], ["baseline", "current"])

        lat = next(r for r in result.metric_rows if r.name == "avg_latency_seconds")
        assert lat.directions[1] == "improved"
        assert lat.deltas[1] == pytest.approx(-2.0, abs=1e-6)


# ---------------------------------------------------------------------------
# compare_runs (3+ runs)
# ---------------------------------------------------------------------------


class TestCompareRunsMultiple:
    def test_three_runs(self, tmp_path: Path) -> None:
        r1 = _sample_result(groundedness=0.80, relevance=0.80, overall_passed=True)
        r2 = _sample_result(groundedness=0.90, relevance=0.85, overall_passed=True)
        r3 = _sample_result(groundedness=0.70, relevance=0.95, overall_passed=False)

        p1 = _write_result(tmp_path / "run1" / "results.json", r1)
        p2 = _write_result(tmp_path / "run2" / "results.json", r2)
        p3 = _write_result(tmp_path / "run3" / "results.json", r3)

        result = compare_runs([p1, p2, p3], ["run1", "run2", "run3"])

        assert result.summary.run_count == 3
        assert len(result.runs) == 3

        # run2 improved groundedness, run3 regressed
        g_row = next(r for r in result.metric_rows if r.name == "groundedness")
        assert g_row.directions[1] == "improved"
        assert g_row.directions[2] == "regressed"

        # run3 should be in regressions list
        assert result.summary.any_regressions is True
        assert 2 in result.summary.runs_with_regressions
        # run2 should not have regressions
        assert 1 not in result.summary.runs_with_regressions

    def test_best_run_index(self, tmp_path: Path) -> None:
        r1 = _sample_result(groundedness=0.80, relevance=0.80)
        r2 = _sample_result(groundedness=0.95, relevance=0.70)
        r3 = _sample_result(groundedness=0.85, relevance=0.90)

        p1 = _write_result(tmp_path / "run1" / "results.json", r1)
        p2 = _write_result(tmp_path / "run2" / "results.json", r2)
        p3 = _write_result(tmp_path / "run3" / "results.json", r3)

        result = compare_runs([p1, p2, p3], ["run1", "run2", "run3"])

        g_row = next(r for r in result.metric_rows if r.name == "groundedness")
        assert g_row.best_run_index == 1  # run2 has 0.95

        r_row = next(r for r in result.metric_rows if r.name == "relevance")
        assert r_row.best_run_index == 2  # run3 has 0.90


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
            runs=[
                RunReference(run_id="run1", bundle_name="rag_baseline", dataset_name="smoke", started_at="t1"),
                RunReference(run_id="run2", bundle_name="rag_baseline", dataset_name="smoke", started_at="t2"),
            ],
            metric_rows=[
                ComparisonMetricRow(
                    name="groundedness",
                    values=[0.9, 0.7],
                    deltas=[None, -0.2],
                    delta_percents=[None, -22.22],
                    directions=["unchanged", "regressed"],
                    best_run_index=0,
                ),
            ],
            threshold_rows=[
                ComparisonThresholdRow(evaluator="groundedness", criteria=">=", passed=[True, False]),
            ],
            item_rows=[
                ComparisonItemRow(row_index=1, passed_all=[True, False]),
            ],
            summary=ComparisonSummary(run_count=2, any_regressions=True, runs_with_regressions=[1]),
        )

        md = generate_comparison_markdown(result)

        assert "# AgentOps Comparison Report" in md
        assert "REGRESSIONS DETECTED" in md
        assert "groundedness" in md
        assert "regressed" in md
        assert "FAIL" in md

    def test_report_no_regressions(self) -> None:
        result = ComparisonResult(
            version=1,
            runs=[
                RunReference(run_id="run1", bundle_name="b", dataset_name="d", started_at="t1"),
                RunReference(run_id="run2", bundle_name="b", dataset_name="d", started_at="t2"),
            ],
            metric_rows=[
                ComparisonMetricRow(
                    name="g",
                    values=[0.7, 0.9],
                    deltas=[None, 0.2],
                    directions=["unchanged", "improved"],
                ),
            ],
            threshold_rows=[],
            item_rows=[],
            summary=ComparisonSummary(run_count=2, any_regressions=False, runs_with_regressions=[]),
        )

        md = generate_comparison_markdown(result)
        assert "NO REGRESSIONS" in md
