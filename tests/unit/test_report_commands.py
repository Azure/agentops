"""Tests for report show and report export commands."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from typer.testing import CliRunner

from agentops.cli.app import app

runner = CliRunner()


def _create_results_dir(tmp_path: Path, *, with_html: bool = False) -> Path:
    """Create a results directory with results.json and report.md."""
    results_dir = tmp_path / ".agentops" / "results" / "latest"
    results_dir.mkdir(parents=True)

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
            {"name": "RelevanceEvaluator", "value": 5.0},
            {"name": "samples_evaluated", "value": 3.0},
        ],
        "row_metrics": [
            {
                "row_index": 1,
                "metrics": [
                    {"name": "CoherenceEvaluator", "value": 4.0},
                    {"name": "RelevanceEvaluator", "value": 5.0},
                ],
            },
            {
                "row_index": 2,
                "metrics": [
                    {"name": "CoherenceEvaluator", "value": 5.0},
                    {"name": "RelevanceEvaluator", "value": 5.0},
                ],
            },
        ],
        "item_evaluations": [],
        "thresholds": [],
        "summary": {
            "metrics_count": 3,
            "thresholds_count": 0,
            "thresholds_passed": 0,
            "thresholds_failed": 0,
            "overall_passed": True,
        },
    }
    (results_dir / "results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    (results_dir / "report.md").write_text(
        "# Test Report\n\nAll passed.\n", encoding="utf-8"
    )
    if with_html:
        (results_dir / "report.html").write_text(
            "<html><body>Report</body></html>", encoding="utf-8"
        )
    return results_dir


# ---------------------------------------------------------------------------
# report show
# ---------------------------------------------------------------------------


class TestReportShow:
    def test_show_md(self, tmp_path: Path) -> None:
        results_dir = _create_results_dir(tmp_path)
        result = runner.invoke(
            app,
            ["report", "show", "--in", str(results_dir / "results.json")],
        )
        assert result.exit_code == 0
        assert "# Test Report" in result.stdout
        assert "All passed." in result.stdout

    def test_show_md_default_format(self, tmp_path: Path) -> None:
        results_dir = _create_results_dir(tmp_path)
        result = runner.invoke(
            app,
            ["report", "show", "--in", str(results_dir / "results.json")],
        )
        assert result.exit_code == 0
        assert "# Test Report" in result.stdout

    def test_show_md_missing(self, tmp_path: Path) -> None:
        results_dir = _create_results_dir(tmp_path)
        (results_dir / "report.md").unlink()
        result = runner.invoke(
            app,
            ["report", "show", "--in", str(results_dir / "results.json")],
        )
        assert result.exit_code == 1

    def test_show_html_missing(self, tmp_path: Path) -> None:
        results_dir = _create_results_dir(tmp_path)
        result = runner.invoke(
            app,
            [
                "report",
                "show",
                "--in",
                str(results_dir / "results.json"),
                "--format",
                "html",
            ],
        )
        assert result.exit_code == 1
        assert "report.html not found" in (result.stdout + result.stderr)


# ---------------------------------------------------------------------------
# report export
# ---------------------------------------------------------------------------


class TestReportExport:
    def test_export_json_stdout(self, tmp_path: Path) -> None:
        results_dir = _create_results_dir(tmp_path)
        result = runner.invoke(
            app,
            [
                "report",
                "export",
                "--in",
                str(results_dir / "results.json"),
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["status"] == "completed"
        assert len(data["metrics"]) == 3

    def test_export_json_to_file(self, tmp_path: Path) -> None:
        results_dir = _create_results_dir(tmp_path)
        out_file = tmp_path / "export.json"
        result = runner.invoke(
            app,
            [
                "report",
                "export",
                "--in",
                str(results_dir / "results.json"),
                "--out",
                str(out_file),
            ],
        )
        assert result.exit_code == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert data["status"] == "completed"

    def test_export_csv_stdout(self, tmp_path: Path) -> None:
        results_dir = _create_results_dir(tmp_path)
        result = runner.invoke(
            app,
            [
                "report",
                "export",
                "--in",
                str(results_dir / "results.json"),
                "--format",
                "csv",
            ],
        )
        assert result.exit_code == 0
        reader = csv.reader(io.StringIO(result.stdout))
        rows = list(reader)
        # Header + 3 metrics + blank + row header + 2 row metrics
        assert rows[0] == ["metric", "value"]
        assert rows[1][0] == "CoherenceEvaluator"

    def test_export_csv_to_file(self, tmp_path: Path) -> None:
        results_dir = _create_results_dir(tmp_path)
        out_file = tmp_path / "export.csv"
        result = runner.invoke(
            app,
            [
                "report",
                "export",
                "--in",
                str(results_dir / "results.json"),
                "--format",
                "csv",
                "--out",
                str(out_file),
            ],
        )
        assert result.exit_code == 0
        assert out_file.exists()
        content = out_file.read_text(encoding="utf-8")
        assert "CoherenceEvaluator" in content

    def test_export_md_stdout(self, tmp_path: Path) -> None:
        results_dir = _create_results_dir(tmp_path)
        result = runner.invoke(
            app,
            [
                "report",
                "export",
                "--in",
                str(results_dir / "results.json"),
                "--format",
                "md",
            ],
        )
        assert result.exit_code == 0
        assert "# Test Report" in result.stdout

    def test_export_invalid_format(self, tmp_path: Path) -> None:
        results_dir = _create_results_dir(tmp_path)
        result = runner.invoke(
            app,
            [
                "report",
                "export",
                "--in",
                str(results_dir / "results.json"),
                "--format",
                "xml",
            ],
        )
        assert result.exit_code == 1

    def test_export_missing_results(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["report", "export", "--in", str(tmp_path / "nonexistent.json")],
        )
        assert result.exit_code == 1
