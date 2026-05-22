"""Tests for the flat pipeline Markdown reporter."""

from __future__ import annotations

from agentops.core.results import (
    RowMetric,
    RowResult,
    RunResult,
    RunSummary,
    TargetInfo,
)
from agentops.pipeline import reporter


def _result() -> RunResult:
    return RunResult(
        started_at="2026-05-11T18:00:00+00:00",
        finished_at="2026-05-11T18:00:01+00:00",
        duration_seconds=1.0,
        target=TargetInfo(kind="foundry_prompt", raw="my-agent:2"),
        dataset_path=".agentops/data/smoke.jsonl",
        evaluators=["CoherenceEvaluator"],
        rows=[
            RowResult(
                row_index=0,
                input="Question?",
                response="Actual answer.",
                expected="Expected answer.",
                latency_seconds=1.2,
                metrics=[RowMetric(name="coherence", value=5.0)],
            )
        ],
        aggregate_metrics={"coherence": 5.0},
        summary=RunSummary(
            items_total=1,
            items_passed_all=1,
            items_pass_rate=1.0,
            thresholds_total=0,
            thresholds_passed=0,
            threshold_pass_rate=1.0,
            overall_passed=True,
        ),
    )


def test_report_includes_row_details_with_input_response_expected():
    text = reporter.render(_result())

    assert "## Row Details" in text
    assert "| # | Input | Response | Expected |" in text
    assert "Question?" in text
    assert "Actual answer." in text
    assert "Expected answer." in text


def test_report_includes_foundry_cloud_session_from_config():
    result = _result()
    result.config["cloud_evaluation"] = {
        "evaluation_name": "agentops-cloud-abc",
        "eval_id": "eval-1",
        "run_id": "run-1",
        "status": "completed",
        "report_url": "https://ai.azure.com/foundry/runs/run-1",
        "dataset": {
            "mode": "foundry",
            "requested_mode": "auto",
            "source_type": "file_id",
            "local_path": ".agentops/data/smoke.jsonl",
            "sha256": "abc123def456",
            "foundry_name": "agentops-smoke",
            "foundry_version": "sha256-abc123",
            "foundry_id": "azureai://accounts/a/projects/p/data/agentops-smoke/versions/sha256-abc123",
        },
    }

    text = reporter.render(result)

    assert "## Foundry Cloud Session" in text
    assert "**Evaluation:** `agentops-cloud-abc`" in text
    assert "**Run ID:** `run-1`" in text
    assert "https://ai.azure.com/foundry/runs/run-1" in text
    assert "**Dataset:** Foundry dataset `agentops-smoke`@`sha256-abc123` (requested `auto`)" in text
    assert ".agentops/data/smoke.jsonl" in text
