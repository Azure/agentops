"""Tests for browse services (bundle list/show, run list/show)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.services.browse import (
    list_bundles,
    list_runs,
    show_bundle,
    show_run,
    view_run,
)
from agentops.utils.yaml import save_yaml

runner = CliRunner()


def _create_workspace(tmp_path: Path) -> Path:
    """Create a minimal .agentops workspace."""
    ws = tmp_path / ".agentops"
    ws.mkdir()
    (ws / "bundles").mkdir()
    (ws / "results").mkdir()
    return ws


def _write_bundle(ws: Path, name: str, evaluators: list, thresholds: list) -> Path:
    bundle_path = ws / "bundles" / f"{name}.yaml"
    save_yaml(
        bundle_path,
        {
            "version": 1,
            "name": name,
            "description": f"Test bundle {name}",
            "evaluators": evaluators,
            "thresholds": thresholds,
            "metadata": {"category": "test"},
        },
    )
    return bundle_path


def _write_run(ws: Path, run_id: str, *, passed: bool = True) -> Path:
    run_dir = ws / "results" / run_id
    run_dir.mkdir(parents=True)
    results = {
        "version": 1,
        "status": "completed",
        "bundle": {"name": "test_bundle", "path": "bundles/test.yaml"},
        "dataset": {"name": "test_dataset", "path": "datasets/test.yaml"},
        "execution": {
            "backend": "foundry",
            "command": "test",
            "started_at": "2026-04-07T10:00:00Z",
            "finished_at": "2026-04-07T10:01:00Z",
            "duration_seconds": 60.0,
            "exit_code": 0,
        },
        "metrics": [
            {"name": "CoherenceEvaluator", "value": 4.5},
            {"name": "samples_evaluated", "value": 3.0},
        ],
        "row_metrics": [
            {
                "row_index": 1,
                "metrics": [
                    {"name": "CoherenceEvaluator", "value": 5.0},
                    {"name": "RelevanceEvaluator", "value": 4.0},
                ],
            },
            {
                "row_index": 2,
                "metrics": [
                    {"name": "CoherenceEvaluator", "value": 4.0},
                    {"name": "RelevanceEvaluator", "value": 5.0},
                ],
            },
        ],
        "item_evaluations": [
            {
                "row_index": 1,
                "passed_all": True,
                "thresholds": [
                    {
                        "row_index": 1,
                        "evaluator": "CoherenceEvaluator",
                        "criteria": ">=",
                        "expected": "3.000000",
                        "actual": "5.000000",
                        "passed": True,
                    }
                ],
            },
            {
                "row_index": 2,
                "passed_all": passed,
                "thresholds": [
                    {
                        "row_index": 2,
                        "evaluator": "CoherenceEvaluator",
                        "criteria": ">=",
                        "expected": "3.000000",
                        "actual": "4.000000",
                        "passed": passed,
                    }
                ],
            },
        ],
        "thresholds": [
            {
                "evaluator": "CoherenceEvaluator",
                "criteria": ">=",
                "expected": "3.000000",
                "actual": "2/2 items",
                "passed": passed,
            }
        ],
        "summary": {
            "metrics_count": 2,
            "thresholds_count": 1,
            "thresholds_passed": 1 if passed else 0,
            "thresholds_failed": 0 if passed else 1,
            "overall_passed": passed,
        },
    }
    (run_dir / "results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    (run_dir / "report.md").write_text("# Report", encoding="utf-8")
    return run_dir


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------


class TestListBundles:
    def test_empty_workspace(self, tmp_path: Path) -> None:
        _create_workspace(tmp_path)
        result = list_bundles(directory=tmp_path)
        assert result.bundles == []

    def test_lists_bundles(self, tmp_path: Path) -> None:
        ws = _create_workspace(tmp_path)
        _write_bundle(
            ws,
            "baseline",
            [{"name": "CoherenceEvaluator", "source": "foundry", "enabled": True}],
            [{"evaluator": "CoherenceEvaluator", "criteria": ">=", "value": 3}],
        )
        result = list_bundles(directory=tmp_path)
        assert len(result.bundles) == 1
        assert result.bundles[0].name == "baseline"
        assert result.bundles[0].evaluators == ["CoherenceEvaluator"]
        assert result.bundles[0].thresholds == 1

    def test_no_workspace_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="No .agentops workspace"):
            list_bundles(directory=tmp_path)


class TestShowBundle:
    def test_by_name(self, tmp_path: Path) -> None:
        ws = _create_workspace(tmp_path)
        _write_bundle(
            ws,
            "my_bundle",
            [{"name": "FluencyEvaluator", "source": "foundry", "enabled": True}],
            [{"evaluator": "FluencyEvaluator", "criteria": ">=", "value": 4}],
        )
        detail = show_bundle("my_bundle", directory=tmp_path)
        assert detail.name == "my_bundle"
        assert len(detail.evaluators) == 1
        assert detail.evaluators[0]["name"] == "FluencyEvaluator"

    def test_not_found(self, tmp_path: Path) -> None:
        _create_workspace(tmp_path)
        with pytest.raises(FileNotFoundError, match="not found"):
            show_bundle("nonexistent", directory=tmp_path)


class TestListRuns:
    def test_empty(self, tmp_path: Path) -> None:
        _create_workspace(tmp_path)
        result = list_runs(directory=tmp_path)
        assert result.runs == []

    def test_lists_runs(self, tmp_path: Path) -> None:
        ws = _create_workspace(tmp_path)
        _write_run(ws, "2026-04-07_100000", passed=True)
        _write_run(ws, "2026-04-07_110000", passed=False)
        result = list_runs(directory=tmp_path)
        assert len(result.runs) == 2
        # Sorted reverse (newest first)
        assert result.runs[0].run_id == "2026-04-07_110000"
        assert result.runs[0].overall_passed is False
        assert result.runs[1].run_id == "2026-04-07_100000"
        assert result.runs[1].overall_passed is True

    def test_skips_latest_dir(self, tmp_path: Path) -> None:
        ws = _create_workspace(tmp_path)
        _write_run(ws, "2026-04-07_100000")
        (ws / "results" / "latest").mkdir()
        result = list_runs(directory=tmp_path)
        assert len(result.runs) == 1


class TestShowRun:
    def test_shows_run(self, tmp_path: Path) -> None:
        ws = _create_workspace(tmp_path)
        _write_run(ws, "2026-04-07_100000", passed=True)
        detail = show_run("2026-04-07_100000", directory=tmp_path)
        assert detail.run_id == "2026-04-07_100000"
        assert detail.bundle_name == "test_bundle"
        assert detail.overall_passed is True
        assert detail.items_total == 2
        assert detail.items_passed == 2

    def test_not_found(self, tmp_path: Path) -> None:
        _create_workspace(tmp_path)
        with pytest.raises(FileNotFoundError, match="not found"):
            show_run("nonexistent", directory=tmp_path)


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestBundleListCLI:
    def test_lists_bundles(self, tmp_path: Path) -> None:
        ws = _create_workspace(tmp_path)
        _write_bundle(
            ws,
            "baseline",
            [{"name": "CoherenceEvaluator", "source": "foundry", "enabled": True}],
            [{"evaluator": "CoherenceEvaluator", "criteria": ">=", "value": 3}],
        )
        result = runner.invoke(app, ["bundle", "list", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "baseline" in result.stdout
        assert "CoherenceEvaluator" in result.stdout

    def test_no_workspace(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["bundle", "list", "--dir", str(tmp_path)])
        assert result.exit_code == 1
        assert "No .agentops workspace" in (result.stdout + result.stderr)


class TestBundleShowCLI:
    def test_shows_bundle(self, tmp_path: Path) -> None:
        ws = _create_workspace(tmp_path)
        _write_bundle(
            ws,
            "my_bundle",
            [{"name": "FluencyEvaluator", "source": "foundry", "enabled": True}],
            [{"evaluator": "FluencyEvaluator", "criteria": ">=", "value": 4}],
        )
        result = runner.invoke(
            app, ["bundle", "show", "my_bundle", "--dir", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "my_bundle" in result.stdout
        assert "FluencyEvaluator" in result.stdout


class TestRunListCLI:
    def test_lists_runs(self, tmp_path: Path) -> None:
        ws = _create_workspace(tmp_path)
        _write_run(ws, "2026-04-07_100000", passed=True)
        result = runner.invoke(app, ["run", "list", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "2026-04-07_100000" in result.stdout
        assert "PASS" in result.stdout


class TestRunShowCLI:
    def test_shows_run(self, tmp_path: Path) -> None:
        ws = _create_workspace(tmp_path)
        _write_run(ws, "2026-04-07_100000")
        result = runner.invoke(
            app, ["run", "show", "2026-04-07_100000", "--dir", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "test_bundle" in result.stdout
        assert "CoherenceEvaluator" in result.stdout

    def test_not_found(self, tmp_path: Path) -> None:
        _create_workspace(tmp_path)
        result = runner.invoke(
            app, ["run", "show", "nonexistent", "--dir", str(tmp_path)]
        )
        assert result.exit_code == 1
        assert "not found" in (result.stdout + result.stderr)


# ---------------------------------------------------------------------------
# view_run service tests
# ---------------------------------------------------------------------------


class TestViewRun:
    def test_table_view(self, tmp_path: Path) -> None:
        ws = _create_workspace(tmp_path)
        _write_run(ws, "2026-04-07_100000", passed=True)
        result = view_run("2026-04-07_100000", directory=tmp_path)
        assert result.run_id == "2026-04-07_100000"
        assert len(result.rows) == 2
        assert result.rows[0].row_index == 1
        assert result.rows[0].scores["CoherenceEvaluator"] == 5.0
        assert result.rows[1].scores["RelevanceEvaluator"] == 5.0

    def test_entry_filter(self, tmp_path: Path) -> None:
        ws = _create_workspace(tmp_path)
        _write_run(ws, "2026-04-07_100000")
        result = view_run("2026-04-07_100000", directory=tmp_path, entry=2)
        assert len(result.rows) == 1
        assert result.rows[0].row_index == 2

    def test_entry_not_found(self, tmp_path: Path) -> None:
        ws = _create_workspace(tmp_path)
        _write_run(ws, "2026-04-07_100000")
        with pytest.raises(ValueError, match="Entry 99 not found"):
            view_run("2026-04-07_100000", directory=tmp_path, entry=99)

    def test_entry_has_thresholds(self, tmp_path: Path) -> None:
        ws = _create_workspace(tmp_path)
        _write_run(ws, "2026-04-07_100000")
        result = view_run("2026-04-07_100000", directory=tmp_path, entry=1)
        row = result.rows[0]
        assert len(row.threshold_results) == 1
        assert row.threshold_results[0]["evaluator"] == "CoherenceEvaluator"
        assert row.threshold_results[0]["passed"] is True


# ---------------------------------------------------------------------------
# run view CLI tests
# ---------------------------------------------------------------------------


class TestRunViewCLI:
    def test_table_view(self, tmp_path: Path) -> None:
        ws = _create_workspace(tmp_path)
        _write_run(ws, "2026-04-07_100000")
        result = runner.invoke(
            app, ["run", "view", "2026-04-07_100000", "--dir", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "Coherence" in result.stdout
        assert "Relevance" in result.stdout

    def test_entry_view(self, tmp_path: Path) -> None:
        ws = _create_workspace(tmp_path)
        _write_run(ws, "2026-04-07_100000")
        result = runner.invoke(
            app,
            [
                "run",
                "view",
                "2026-04-07_100000",
                "--entry",
                "1",
                "--dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        assert "Row 1: PASS" in result.stdout
        assert "CoherenceEvaluator" in result.stdout

    def test_entry_not_found(self, tmp_path: Path) -> None:
        ws = _create_workspace(tmp_path)
        _write_run(ws, "2026-04-07_100000")
        result = runner.invoke(
            app,
            [
                "run",
                "view",
                "2026-04-07_100000",
                "--entry",
                "99",
                "--dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 1
