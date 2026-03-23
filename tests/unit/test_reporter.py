from agentops.core.models import RunResult
from agentops.core.reporter import generate_report_markdown


def _sample_result(overall_passed: bool = True) -> RunResult:
    return RunResult.model_validate(
        {
            "version": 1,
            "status": "completed",
            "bundle": {
                "name": "rag_baseline",
                "path": ".agentops/bundles/rag_baseline.yaml",
            },
            "dataset": {"name": "smoke", "path": ".agentops/datasets/smoke-agent.yaml"},
            "execution": {
                "backend": "subprocess",
                "command": "python -m fake_eval_runner",
                "started_at": "2026-02-23T10:00:00Z",
                "finished_at": "2026-02-23T10:00:05Z",
                "duration_seconds": 5.0,
                "exit_code": 0,
            },
            "metrics": [
                {"name": "groundedness", "value": 0.84},
                {"name": "relevance", "value": 0.83},
            ],
            "run_metrics": [
                {"name": "run_pass", "value": 0.0 if not overall_passed else 1.0},
                {
                    "name": "threshold_pass_rate",
                    "value": 0.5 if not overall_passed else 1.0,
                },
                {"name": "accuracy", "value": 0.84},
            ],
            "thresholds": [
                {
                    "evaluator": "groundedness",
                    "criteria": ">=",
                    "expected": "0.800000",
                    "actual": "0.840000",
                    "passed": True,
                },
                {
                    "evaluator": "relevance",
                    "criteria": ">=",
                    "expected": "0.950000",
                    "actual": "0.830000",
                    "passed": False,
                },
            ],
            "summary": {
                "metrics_count": 2,
                "thresholds_count": 2,
                "thresholds_passed": 2 if overall_passed else 1,
                "thresholds_failed": 0 if overall_passed else 1,
                "overall_passed": overall_passed,
            },
            "artifacts": {
                "backend_stdout": "backend.stdout.log",
                "backend_stderr": "backend.stderr.log",
            },
        }
    )


def test_report_markdown_contains_required_sections_and_tables() -> None:
    markdown = generate_report_markdown(_sample_result(overall_passed=False))

    assert "# AgentOps Evaluation Report" in markdown
    assert "## Overview" in markdown
    assert "- Bundle: rag_baseline" in markdown
    assert "- Dataset: smoke" in markdown
    assert "- Overall status: **FAIL**" in markdown

    assert "## Execution Summary" in markdown
    assert "| Field | Value |" in markdown
    assert "| Backend | subprocess |" in markdown
    assert "| Duration (s) | 5.000 |" in markdown

    assert "## Metrics" in markdown
    assert "| Metric | Value |" in markdown
    assert "| groundedness | 0.84 |" in markdown

    assert "## Run Metrics" in markdown
    assert "| run_pass | 0 |" in markdown

    assert "## Threshold Checks" in markdown
    assert "| Evaluator | Criteria | Expected | Actual | Status |" in markdown
    assert "| relevance | >= | 0.950000 | 0.830000 | Missed |" in markdown


def test_report_markdown_pass_status() -> None:
    markdown = generate_report_markdown(_sample_result(overall_passed=True))
    assert "- Overall status: **PASS**" in markdown
