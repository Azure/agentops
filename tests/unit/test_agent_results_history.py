"""Tests for the results-history source."""

from __future__ import annotations

import json
from pathlib import Path

from agentops.agent.config import ResultsHistorySourceConfig
from agentops.agent.sources.results_history import collect_results_history


def _write_run(
    results_root: Path,
    run_id: str,
    timestamp: str,
    metrics: dict,
    *,
    items_total: int = 3,
    items_passed_all: int = 3,
    run_pass: bool = True,
) -> None:
    run_dir = results_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "timestamp": timestamp,
        "metrics": metrics,
        "summary": {
            "run_pass": run_pass,
            "items_total": items_total,
            "items_passed_all": items_passed_all,
        },
    }
    (run_dir / "results.json").write_text(json.dumps(payload), encoding="utf-8")


def test_collect_results_history_orders_oldest_to_newest(tmp_path: Path) -> None:
    workspace = tmp_path
    results = workspace / ".agentops" / "results"
    _write_run(results, "run-1", "2024-05-01T10:00:00Z", {"coherence": 4.5})
    _write_run(results, "run-2", "2024-05-02T10:00:00Z", {"coherence": 4.0})
    _write_run(results, "latest", "2024-06-01T10:00:00Z", {"coherence": 1.0})

    config = ResultsHistorySourceConfig(
        enabled=True, path=".agentops/results", lookback_runs=10
    )
    history = collect_results_history(workspace, config)

    assert [r.run_id for r in history.runs] == ["run-1", "run-2"]
    assert history.runs[-1].metrics["coherence"] == 4.0
    assert history.diagnostics["status"] == "ok"


def test_collect_results_history_handles_missing_dir(tmp_path: Path) -> None:
    config = ResultsHistorySourceConfig(
        enabled=True, path=".agentops/results", lookback_runs=10
    )
    history = collect_results_history(tmp_path, config)
    assert history.runs == []
    assert history.diagnostics["status"] == "missing"


def test_collect_results_history_disabled(tmp_path: Path) -> None:
    config = ResultsHistorySourceConfig(enabled=False)
    history = collect_results_history(tmp_path, config)
    assert history.runs == []
    assert history.diagnostics["status"] == "disabled"
