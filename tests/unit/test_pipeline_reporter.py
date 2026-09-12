"""Tests for the flat pipeline Markdown reporter."""

from __future__ import annotations

from agentops.core.results import (
    ChangedInput,
    CommitInfo,
    ComparisonInfo,
    ComparisonMetric,
    RegressionInsight,
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


def test_report_renders_remote_provenance_without_temporary_path():
    source_uri = (
        "https://examplestorage.blob.core.windows.net/evals/golden.jsonl"
    )
    result = _result()
    result.dataset_path = source_uri
    result.config["cloud_evaluation"] = {
        "status": "completed",
        "dataset": {
            "mode": "inline",
            "requested_mode": "inline",
            "source_type": "file_content",
            "source_uri": source_uri,
            "sha256": "abc123def456",
        },
    }

    text = reporter.render(result)

    assert source_uri in text
    assert "agentops-dataset-" not in text
    assert "Local source:" not in text


def _commit(sha: str) -> CommitInfo:
    return CommitInfo(
        sha=sha,
        short_sha=sha[:7],
        subject="A commit",
        author="Dev",
        authored_at="2026-09-01T10:00:00+00:00",
        source="ci",
    )


def test_report_renders_regression_insight_section_when_present():
    result = _result()
    result.comparison = ComparisonInfo(
        baseline_path=".agentops/baseline/results.json",
        metrics=[
            ComparisonMetric(
                metric="accuracy", current=0.79, baseline=0.91, delta=-0.12, direction="regressed"
            )
        ],
        insight=RegressionInsight(
            from_run_id="2026-09-01T10:00:00+00:00",
            to_run_id="2026-09-10T14:03:00+00:00",
            from_commit=_commit("a" * 40),
            to_commit=_commit("b" * 40),
            metric="accuracy",
            from_value=0.91,
            to_value=0.79,
            changed_inputs=[
                ChangedInput(field="model", description="the model changed from gpt-4o to gpt-4o-mini")
            ],
            explanation=(
                "Run aaaaaaa → bbbbbbb: accuracy dropped from 0.91 to 0.79. "
                "Likely cause: the model changed from gpt-4o to gpt-4o-mini."
            ),
            suggested_action="Review the model change; consider reverting it.",
            used_git_diff=False,
        ),
    )

    text = reporter.render(result)

    assert "## Regression Insight" in text
    assert "accuracy dropped from 0.91 to 0.79" in text
    assert "the model changed from gpt-4o to gpt-4o-mini" in text
    assert "Review the model change" in text
    # The section must come after the existing comparison table.
    assert text.index("## Comparison vs Baseline") < text.index("## Regression Insight")


def test_report_has_no_regression_insight_section_when_absent():
    result = _result()
    result.comparison = ComparisonInfo(
        baseline_path=".agentops/baseline/results.json",
        metrics=[
            ComparisonMetric(
                metric="accuracy", current=0.95, baseline=0.91, delta=0.04, direction="improved"
            )
        ],
    )

    text = reporter.render(result)

    assert "## Regression Insight" not in text


def test_report_unchanged_without_comparison_at_all():
    text = reporter.render(_result())

    assert "## Regression Insight" not in text
    assert "## Comparison vs Baseline" not in text
