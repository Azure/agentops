"""Tests for the regression check."""

from __future__ import annotations

import json
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
    raw_path: Path | None = None,
    source: str = "local",
) -> RunSummary:
    return RunSummary(
        run_id=run_id,
        timestamp=datetime.now(timezone.utc) + timedelta(days=offset_days),
        metrics=metrics,
        run_pass=True,
        items_total=1,
        items_passed_all=1,
        raw_path=raw_path or Path("dummy"),
        methodology_fingerprint=fingerprint,
        source=source,
    )


def _write_result_json(
    path: Path,
    *,
    accuracy: float,
    version: str,
    deployment: str,
    commit_sha: str,
) -> None:
    payload = {
        "version": 1,
        "started_at": "2026-09-01T10:00:00+00:00",
        "finished_at": "2026-09-01T10:00:01+00:00",
        "duration_seconds": 1.0,
        "target": {
            "kind": "foundry_prompt",
            "raw": f"greeter:{version}",
            "name": "greeter",
            "version": version,
            "deployment": deployment,
        },
        "dataset_path": "data/smoke.jsonl",
        "evaluators": ["CoherenceEvaluator"],
        "rows": [],
        "aggregate_metrics": {"accuracy": accuracy},
        "thresholds": [],
        "summary": {
            "items_total": 1,
            "items_passed_all": 1,
            "items_pass_rate": 1.0,
            "thresholds_total": 0,
            "thresholds_passed": 0,
            "threshold_pass_rate": 1.0,
            "overall_passed": True,
        },
        "config": {},
        "commit": {
            "sha": commit_sha,
            "short_sha": commit_sha[:7],
            "subject": "A commit",
            "author": "Dev",
            "authored_at": "2026-09-01T10:00:00+00:00",
            "source": "ci",
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


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


def test_regression_check_attaches_insight_when_both_runs_have_commit_metadata(tmp_path) -> None:
    baseline_path = tmp_path / "baseline" / "results.json"
    latest_path = tmp_path / "latest" / "results.json"
    _write_result_json(
        baseline_path, accuracy=0.91, version="3", deployment="gpt-4o", commit_sha="a" * 40
    )
    _write_result_json(
        latest_path, accuracy=0.79, version="4", deployment="gpt-4o-mini", commit_sha="b" * 40
    )
    history = ResultsHistory(
        runs=[
            _run({"accuracy": 0.91}, run_id="b1", offset_days=-3, raw_path=baseline_path),
            _run({"accuracy": 0.91}, run_id="b2", offset_days=-2, raw_path=baseline_path),
            _run({"accuracy": 0.79}, run_id="latest", offset_days=0, raw_path=latest_path),
        ]
    )
    config = RegressionCheckConfig(metrics=["accuracy"], threshold_drop=0.10, min_runs=3)

    findings = run_regression_check(history, config)

    assert len(findings) == 1
    finding = findings[0]
    assert "insight" in finding.evidence
    assert finding.evidence["insight"]["metric"] == "accuracy"
    assert "gpt-4o" in finding.recommendation and "gpt-4o-mini" in finding.recommendation
    assert "prompt" in finding.recommendation


def test_regression_check_keeps_generic_recommendation_without_commit_metadata() -> None:
    """Today's behavior is unchanged when raw_path can't be reloaded (e.g. missing file)."""
    history = ResultsHistory(
        runs=[
            _run({"coherence": 4.5}, run_id="b1", offset_days=-3),
            _run({"coherence": 4.5}, run_id="b2", offset_days=-2),
            _run({"coherence": 3.0}, run_id="latest", offset_days=0),
        ]
    )
    config = RegressionCheckConfig(metrics=["coherence"], threshold_drop=0.10, min_runs=3)

    findings = run_regression_check(history, config)

    assert len(findings) == 1
    assert "insight" not in findings[0].evidence
    assert "inspect prompt/model/dataset changes" in findings[0].recommendation


def test_regression_check_skips_insight_for_cloud_sourced_runs(tmp_path) -> None:
    baseline_path = tmp_path / "baseline" / "results.json"
    latest_path = tmp_path / "latest" / "results.json"
    _write_result_json(
        baseline_path, accuracy=0.91, version="3", deployment="gpt-4o", commit_sha="a" * 40
    )
    _write_result_json(
        latest_path, accuracy=0.79, version="4", deployment="gpt-4o-mini", commit_sha="b" * 40
    )
    history = ResultsHistory(
        runs=[
            _run(
                {"accuracy": 0.91},
                run_id="b1",
                offset_days=-3,
                raw_path=baseline_path,
                source="foundry_cloud",
            ),
            _run(
                {"accuracy": 0.91},
                run_id="b2",
                offset_days=-2,
                raw_path=baseline_path,
                source="foundry_cloud",
            ),
            _run(
                {"accuracy": 0.79},
                run_id="latest",
                offset_days=0,
                raw_path=latest_path,
                source="foundry_cloud",
            ),
        ]
    )
    config = RegressionCheckConfig(metrics=["accuracy"], threshold_drop=0.10, min_runs=3)

    findings = run_regression_check(history, config)

    assert len(findings) == 1
    assert "insight" not in findings[0].evidence
