"""CLI behaviour when graders *execute* but a subset errors out.

A grader execution error (auth/RBAC/timeout) is not a quality regression, but
because ``items_passed_all`` requires every grader on a row to succeed, a single
errored grader flips ``overall_passed`` to ``False`` and the run reports
``Threshold status: FAILED`` even though every computable threshold passed.

The CLI must surface that distinction loudly so users (the most common trigger
is data-plane RBAC that is still propagating) do not chase a phantom quality
failure or start lowering thresholds.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentops.cli.app import _grader_error_summary, app
from agentops.core.results import (
    RowMetric,
    RowResult,
    RunResult,
    RunSummary,
    TargetInfo,
    ThresholdEvaluation,
)

runner = CliRunner()

_AUTH_ERROR = (
    "FAILED_EXECUTION: (UserError) OpenAI API hits AuthenticationError: "
    "Principal does not have access to API/Operation."
)


def _result_with_partial_grader_errors() -> RunResult:
    """One row where coherence scored but similarity errored on auth."""
    row = RowResult(
        row_index=0,
        input="plan a trip",
        expected="an itinerary",
        response="here is an itinerary",
        metrics=[
            RowMetric(name="coherence", value=5.0),
            RowMetric(name="similarity", value=None, error=_AUTH_ERROR),
        ],
    )
    summary = RunSummary(
        items_total=1,
        items_passed_all=0,  # the errored grader means no row passed all
        items_pass_rate=0.0,
        thresholds_total=1,
        thresholds_passed=1,  # every computable threshold passed
        threshold_pass_rate=1.0,
        overall_passed=False,
    )
    return RunResult(
        started_at="2026-06-01T00:00:00+00:00",
        finished_at="2026-06-01T00:01:00+00:00",
        duration_seconds=60.0,
        target=TargetInfo(kind="foundry_prompt", raw="travel-agent:2"),
        dataset_path="dataset.jsonl",
        evaluators=["CoherenceEvaluator", "SimilarityEvaluator"],
        rows=[row],
        aggregate_metrics={"coherence": 5.0},
        thresholds=[
            ThresholdEvaluation(
                metric="coherence",
                criteria=">=",
                expected=">=3",
                actual="5",
                passed=True,
            )
        ],
        summary=summary,
    )


def test_grader_error_summary_counts_and_lifts_first_error() -> None:
    errored, total, first_error = _grader_error_summary(
        _result_with_partial_grader_errors()
    )
    assert (errored, total) == (1, 2)
    assert first_error is not None
    assert "AuthenticationError" in first_error


def _write_minimal_config(tmp_path: Path) -> Path:
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"input": "hi", "expected": "hi"}), encoding="utf-8")
    config = tmp_path / "agentops.yaml"
    config.write_text(
        json.dumps(
            {"version": 1, "agent": "model:gpt-4o", "dataset": str(dataset)}
        ),
        encoding="utf-8",
    )
    return config


def test_eval_run_warns_on_partial_grader_errors(tmp_path, monkeypatch) -> None:
    config = _write_minimal_config(tmp_path)
    output = tmp_path / "out"
    output.mkdir()

    crafted = _result_with_partial_grader_errors()
    import agentops.pipeline.orchestrator as orch

    monkeypatch.setattr(orch, "run_evaluation", lambda *a, **k: crafted)

    result = runner.invoke(
        app,
        ["eval", "run", "--config", str(config), "--output", str(output)],
    )

    # A grader-execution failure keeps the gate-failed exit code...
    assert result.exit_code == 2, result.output
    # ...but the user is told it is an execution error, not a quality failure.
    assert "grader execution(s) errored" in result.output
    assert "propagating" in result.output
    assert "AuthenticationError" in result.output
    assert "FAILED" in result.output


def test_eval_run_no_warning_when_no_grader_errors(tmp_path, monkeypatch) -> None:
    config = _write_minimal_config(tmp_path)
    output = tmp_path / "out"
    output.mkdir()

    clean = _result_with_partial_grader_errors()
    # Drop the errored grader so the row is clean and the gate genuinely passes.
    clean.rows[0].metrics = [RowMetric(name="coherence", value=5.0)]
    clean.summary.items_passed_all = 1
    clean.summary.items_pass_rate = 1.0
    clean.summary.overall_passed = True

    import agentops.pipeline.orchestrator as orch

    monkeypatch.setattr(orch, "run_evaluation", lambda *a, **k: clean)

    result = runner.invoke(
        app,
        ["eval", "run", "--config", str(config), "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert "PASSED" in result.output
    assert "grader execution(s) errored" not in result.output
