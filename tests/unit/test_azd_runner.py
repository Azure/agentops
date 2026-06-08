"""Tests for the azd eval runner and orchestrator dispatch."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest
from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.core.agentops_config import AgentOpsConfig
from agentops.core.azd_eval import load_eval_recipe
from agentops.pipeline import azd_runner, orchestrator


runner = CliRunner()


def _write_dataset(path: Path) -> None:
    path.write_text(json.dumps({"input": "hello", "expected": "hi"}) + "\n", encoding="utf-8")


def _write_recipe(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """
name: travel-agent-eval
agent:
  name: travel-agent
  kind: prompt-agent
  version: "1"
dataset_reference:
  name: smoke
  version: "1"
  local_uri: datasets/smoke.jsonl
evaluators:
  - name: builtin.coherence
  - name: booking_accuracy
""".lstrip(),
        encoding="utf-8",
    )


def test_run_azd_eval_reads_show_out_file_when_run_outputs_text(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recipe_path = tmp_path / "eval.yaml"
    _write_recipe(recipe_path)
    calls: list[list[str]] = []

    def fake_run(command, *, cwd, text, encoding, errors, capture_output, stdin, timeout, check):
        calls.append(command)
        assert encoding == "utf-8"
        assert errors == "replace"
        assert stdin is azd_runner.subprocess.DEVNULL
        if command[:6] == ["azd", "--no-prompt", "ai", "agent", "eval", "run"]:
            return mock.Mock(
                returncode=0,
                stdout=(
                    "Eval:       eval_123\n"
                    "Run:        evalrun_456\n"
                    "Status:     Completed\n"
                ),
                stderr="",
            )
        if command[:6] == ["azd", "--no-prompt", "ai", "agent", "eval", "show"]:
            out_file = Path(command[command.index("--out-file") + 1])
            out_file.write_text(
                json.dumps(
                    {
                        "id": "evalrun_456",
                        "eval_id": "eval_123",
                        "status": "completed",
                        "result_counts": {"total": 3},
                        "per_testing_criteria_results": [
                            {
                                "testing_criteria": "coherence",
                                "passed": 3,
                                "failed": 0,
                                "errored": 0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return mock.Mock(returncode=0, stdout="exported", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(azd_runner.subprocess, "run", fake_run)
    monkeypatch.setattr(azd_runner, "azd_available", lambda *, cwd=None: True)

    result = azd_runner.run_azd_eval(recipe_path, workspace=tmp_path)

    assert result.run_id == "evalrun_456"
    assert result.payload["eval_id"] == "eval_123"
    assert azd_runner._extract_numeric_metrics(result.payload) == {"coherence": 1.0}
    assert calls[0][:6] == ["azd", "--no-prompt", "ai", "agent", "eval", "run"]
    assert calls[1][:7] == ["azd", "--no-prompt", "ai", "agent", "eval", "show", "eval_123"]


def test_run_command_with_progress_emits_heartbeat(
    tmp_path: Path,
    monkeypatch,
) -> None:
    messages: list[str] = []
    ticks = iter([0.0, 31.0, 32.0])

    class FakeProcess:
        returncode = 0

        def __init__(self, *, stdout, stderr) -> None:
            self._polls = 0
            stdout.write("Eval: eval_123\n")
            stderr.write("diagnostic\n")

        def poll(self) -> int | None:
            self._polls += 1
            return None if self._polls == 1 else self.returncode

    def fake_popen(command, **kwargs):
        assert command == ["azd", "ai", "agent", "eval", "run"]
        return FakeProcess(stdout=kwargs["stdout"], stderr=kwargs["stderr"])

    monkeypatch.setattr(azd_runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(azd_runner.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(azd_runner.time, "sleep", lambda _seconds: None)

    completed = azd_runner._run_command(
        ["azd", "ai", "agent", "eval", "run"],
        cwd=tmp_path,
        timeout_seconds=120,
        progress=messages.append,
        progress_label="azd eval run",
    )

    assert completed.returncode == 0
    assert completed.stdout == "Eval: eval_123\n"
    assert completed.stderr == "diagnostic\n"
    assert messages == [
        "azd eval run: waiting for azd/Foundry to finish (timeout 2 min).",
        "azd eval run: still running (0.5 min elapsed).",
    ]


def test_normalize_to_results_binds_azd_metrics_and_thresholds(tmp_path: Path) -> None:
    recipe_path = tmp_path / "eval.yaml"
    _write_recipe(recipe_path)
    recipe = load_eval_recipe(recipe_path)
    config = AgentOpsConfig(
        version=1,
        agent="travel-agent:1",
        dataset="ignored.jsonl",
        execution="azd",
        thresholds={"coherence": ">=3", "booking_accuracy": ">=0.8"},
    )
    azd_run = azd_runner.AzdEvalRun(
        recipe_path=recipe_path,
        payload={
            "status": "completed",
            "items_total": 4,
            "metrics": [
                {"name": "builtin.coherence", "score": 4.2},
                {"name": "booking_accuracy", "score": 0.9},
            ],
        },
        run_id="run-1",
        status="completed",
        stdout="{}",
        stderr="",
        duration_seconds=12.0,
    )

    result = azd_runner.normalize_to_results(
        azd_run,
        config=config,
        recipe=recipe,
        started_at=datetime.now(timezone.utc),
    )

    assert result.summary.overall_passed is True
    assert result.config["backend_effective"] == "azd"
    assert result.config["result_granularity"] == "aggregate"
    assert result.aggregate_metrics == {
        "builtin.coherence": 4.2,
        "booking_accuracy": 0.9,
    }
    assert {item.metric: item.actual for item in result.thresholds} == {
        "coherence": "4.2",
        "booking_accuracy": "0.9",
    }


def test_normalize_to_results_fails_closed_for_unmatched_threshold(tmp_path: Path) -> None:
    recipe_path = tmp_path / "eval.yaml"
    _write_recipe(recipe_path)
    recipe = load_eval_recipe(recipe_path)
    config = AgentOpsConfig(
        version=1,
        agent="travel-agent:1",
        dataset="ignored.jsonl",
        execution="azd",
        thresholds={"groundedness": ">=3"},
    )
    azd_run = azd_runner.AzdEvalRun(
        recipe_path=recipe_path,
        payload={"status": "completed", "metrics": [{"name": "builtin.coherence", "score": 4.2}]},
        run_id="run-1",
        status="completed",
        stdout="{}",
        stderr="",
        duration_seconds=1.0,
    )

    with pytest.raises(azd_runner.AzdBackendError, match="threshold metric"):
        azd_runner.normalize_to_results(
            azd_run,
            config=config,
            recipe=recipe,
            started_at=datetime.now(timezone.utc),
        )


def test_normalize_to_results_binds_rubric_dimensions(tmp_path: Path) -> None:
    recipe_path = tmp_path / "eval.yaml"
    recipe_path.write_text(
        """
name: rubric-eval
agent:
  name: travel-agent
  kind: prompt-agent
evaluators:
  - name: travel_quality_rubric
    kind: rubric
    dimensions:
      - id: booking_accuracy
        weight: 0.7
      - id: policy_enforcement
        weight: 0.3
""".lstrip(),
        encoding="utf-8",
    )
    recipe = load_eval_recipe(recipe_path)
    config = AgentOpsConfig(
        version=1,
        agent="travel-agent:1",
        dataset="ignored.jsonl",
        execution="azd",
        thresholds={
            "travel_quality_rubric": ">=0.8",
            "booking_accuracy": ">=0.8",
            "policy_enforcement": ">=0.7",
        },
    )
    azd_run = azd_runner.AzdEvalRun(
        recipe_path=recipe_path,
        payload={
            "status": "completed",
            "evaluators": [
                {
                    "name": "travel_quality_rubric",
                    "score": 0.86,
                    "dimensions": [
                        {"id": "booking_accuracy", "score": 0.92},
                        {"id": "policy_enforcement", "score": 0.78},
                    ],
                }
            ],
        },
        run_id="run-1",
        status="completed",
        stdout="{}",
        stderr="",
        duration_seconds=3.0,
    )

    result = azd_runner.normalize_to_results(
        azd_run,
        config=config,
        recipe=recipe,
        started_at=datetime.now(timezone.utc),
    )

    assert result.summary.overall_passed is True
    assert result.aggregate_metrics == {
        "travel_quality_rubric": 0.86,
        "booking_accuracy": 0.92,
        "policy_enforcement": 0.78,
    }
    assert result.config["azd_evaluation"]["metric_binding"] == {
        "travel_quality_rubric": "travel_quality_rubric",
        "booking_accuracy": "booking_accuracy",
        "policy_enforcement": "policy_enforcement",
    }


def test_orchestrator_azd_dispatch_never_invokes_local_runtime(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    _write_dataset(dataset)
    recipe_path = tmp_path / "eval.yaml"
    _write_recipe(recipe_path)
    config = AgentOpsConfig(
        version=1,
        agent="travel-agent:1",
        dataset=dataset,
        execution="azd",
        eval_recipe=recipe_path.name,
        thresholds={"coherence": ">=3"},
    )
    azd_run = azd_runner.AzdEvalRun(
        recipe_path=recipe_path,
        payload={"status": "completed", "metrics": [{"name": "builtin.coherence", "score": 4.0}]},
        run_id="run-1",
        status="completed",
        stdout='{"run_id":"run-1"}',
        stderr="",
        duration_seconds=2.0,
    )

    with mock.patch.object(orchestrator, "_evaluate_row") as evaluate_row_mock, mock.patch.object(
        azd_runner, "azd_available", return_value=True
    ), mock.patch.object(azd_runner, "run_azd_eval", return_value=azd_run) as run_azd_mock:
        result = orchestrator.run_evaluation(
            config,
            options=orchestrator.RunOptions(
                config_path=tmp_path / "agentops.yaml",
                output_dir=tmp_path / "out",
            ),
        )

    evaluate_row_mock.assert_not_called()
    run_azd_mock.assert_called_once()
    assert result.summary.overall_passed is True
    assert (tmp_path / "out" / "results.json").exists()
    assert (tmp_path / "out" / "azd_evaluation.json").exists()


def test_cli_azd_missing_recipe_fails_gracefully(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    _write_dataset(dataset)
    config_path = tmp_path / "agentops.yaml"
    config_path.write_text(
        """
version: 1
agent: travel-agent:1
dataset: dataset.jsonl
execution: azd
""".lstrip(),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["eval", "run", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "azd eval recipe not found" in result.output
    assert "Traceback" not in result.output
