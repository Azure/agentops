from __future__ import annotations

import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.utils.yaml import save_yaml


def _fixture_backend_script() -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "fake_eval_runner.py"


def _write_project_files(tmp_path: Path, *, fail_thresholds: bool) -> Path:
    agentops_dir = tmp_path / ".agentops"
    bundles_dir = agentops_dir / "bundles"
    datasets_dir = agentops_dir / "datasets"
    data_dir = agentops_dir / "data"

    bundles_dir.mkdir(parents=True, exist_ok=True)
    datasets_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    threshold_value = 0.95 if fail_thresholds else 0.8

    save_yaml(
        bundles_dir / "rag_baseline.yaml",
        {
            "version": 1,
            "name": "rag_baseline",
            "description": "Integration test bundle",
            "evaluators": [
                {"name": "groundedness", "source": "local", "enabled": True},
                {"name": "relevance", "source": "local", "enabled": True},
                {"name": "coherence", "source": "local", "enabled": True},
                {"name": "fluency", "source": "local", "enabled": True},
            ],
            "thresholds": [
                {"evaluator": "groundedness", "criteria": ">=", "value": threshold_value},
                {"evaluator": "relevance", "criteria": ">=", "value": threshold_value},
                {"evaluator": "coherence", "criteria": ">=", "value": threshold_value},
                {"evaluator": "fluency", "criteria": ">=", "value": threshold_value},
            ],
            "metadata": {"category": "integration"},
        },
    )

    save_yaml(
        datasets_dir / "smoke-agent.yaml",
        {
            "version": 1,
            "name": "smoke",
            "description": "Integration dataset",
            "source": {"type": "file", "path": "../data/smoke.jsonl"},
            "format": {"type": "jsonl", "input_field": "input", "expected_field": "expected"},
            "metadata": {"owner": "tests"},
        },
    )

    (data_dir / "smoke.jsonl").write_text(
        '\n'.join(
            [
                '{"id":"1","input":"hello","expected":"hello"}',
                '{"id":"2","input":"world","expected":"world"}',
            ]
        )
        + '\n',
        encoding="utf-8",
    )

    run_payload = {
        "version": 1,
        "bundle": {"path": ".agentops/bundles/rag_baseline.yaml"},
        "dataset": {"path": ".agentops/datasets/smoke-agent.yaml"},
        "backend": {
            "type": "subprocess",
            "command": sys.executable,
            "args": [
                str(_fixture_backend_script()),
                "--bundle",
                "{bundle_path}",
                "--dataset",
                "{dataset_path}",
                "--output",
                "{backend_output_dir}",
            ],
            "timeout_seconds": 30,
            "env": {},
        },
        "output": {"write_report": True},
    }

    run_path = tmp_path / "run.fake.yaml"
    save_yaml(
        run_path,
        run_payload,
    )

    save_yaml(
        agentops_dir / "run.yaml",
        run_payload,
    )
    return run_path


def test_eval_run_integration_success(tmp_path: Path, monkeypatch) -> None:
    run_path = _write_project_files(tmp_path, fail_thresholds=False)
    output_dir = tmp_path / "out-pass"

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["eval", "run", "--config", str(run_path), "--output", str(output_dir)],
    )

    assert result.exit_code == 0
    assert (output_dir / "results.json").is_file()
    assert (output_dir / "report.md").is_file()
    assert (output_dir / "backend.stdout.log").is_file()
    assert (output_dir / "backend.stderr.log").is_file()

    payload = json.loads((output_dir / "results.json").read_text(encoding="utf-8"))
    assert payload["summary"]["overall_passed"] is True
    assert payload["execution"]["exit_code"] == 0
    assert len(payload["row_metrics"]) == 2
    assert len(payload["item_evaluations"]) == 2
    run_metrics = {item["name"]: item["value"] for item in payload["run_metrics"]}
    assert run_metrics["run_pass"] == 1.0
    assert run_metrics["threshold_pass_rate"] == 1.0
    assert run_metrics["items_total"] == 2.0
    assert run_metrics["items_passed_all"] == 2.0
    assert run_metrics["items_pass_rate"] == 1.0
    assert "groundedness_avg" in run_metrics
    assert "groundedness_stddev" in run_metrics


def test_eval_run_integration_threshold_fail(tmp_path: Path, monkeypatch) -> None:
    run_path = _write_project_files(tmp_path, fail_thresholds=True)
    output_dir = tmp_path / "out-fail"

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["eval", "run", "--config", str(run_path), "--output", str(output_dir)],
    )

    assert result.exit_code == 2
    assert (output_dir / "results.json").is_file()
    assert (output_dir / "report.md").is_file()

    payload = json.loads((output_dir / "results.json").read_text(encoding="utf-8"))
    assert payload["summary"]["overall_passed"] is False
    assert payload["summary"]["thresholds_failed"] > 0
    assert len(payload["row_metrics"]) == 2
    assert len(payload["item_evaluations"]) == 2
    run_metrics = {item["name"]: item["value"] for item in payload["run_metrics"]}
    assert run_metrics["run_pass"] == 0.0
    assert run_metrics["threshold_pass_rate"] < 1.0
    assert run_metrics["items_total"] == 2.0
    assert run_metrics["items_passed_all"] == 0.0
    assert run_metrics["items_pass_rate"] == 0.0


def test_eval_run_integration_uses_default_run_yaml_and_updates_latest(tmp_path: Path, monkeypatch) -> None:
    _write_project_files(tmp_path, fail_thresholds=False)

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["eval", "run"])

    assert result.exit_code == 0

    results_root = tmp_path / ".agentops" / "results"
    latest_dir = results_root / "latest"
    assert latest_dir.is_dir()
    assert (latest_dir / "results.json").is_file()
    assert (latest_dir / "report.md").is_file()

    timestamp_dirs = [
        path for path in results_root.iterdir() if path.is_dir() and path.name != "latest"
    ]
    assert len(timestamp_dirs) == 1
    assert (timestamp_dirs[0] / "results.json").is_file()
    assert (timestamp_dirs[0] / "report.md").is_file()
