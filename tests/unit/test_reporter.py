from agentops.core.models import RunResult
from agentops.core.reporter import (
    generate_report_markdown,
    generate_report_html,
    _format_metric_name,
    _get_evaluator_description,
    _fmt_threshold_value,
)


def _sample_result(overall_passed: bool = True, with_row_details: bool = False, with_context: bool = False) -> RunResult:
    row_metrics = []
    item_evaluations = []
    if with_row_details:
        row_metrics = [
            {
                "row_index": 1,
                "input": "What is the refund policy?",
                "response": "Refunds are available within 30 days.",
                "context": "Our company offers a 30-day refund policy for all purchases." if with_context else None,
                "metrics": [{"name": "groundedness", "value": 4.0}],
            },
            {
                "row_index": 2,
                "input": "How do I reset my password?",
                "response": "Go to Settings > Security > Reset.",
                "metrics": [{"name": "groundedness", "value": 2.0}],
            },
        ]
        item_evaluations = [
            {
                "row_index": 1,
                "passed_all": True,
                "thresholds": [
                    {"row_index": 1, "evaluator": "groundedness", "criteria": ">=", "expected": "3.0", "actual": "4.0", "passed": True},
                ],
            },
            {
                "row_index": 2,
                "passed_all": False,
                "thresholds": [
                    {"row_index": 2, "evaluator": "groundedness", "criteria": ">=", "expected": "3.0", "actual": "2.0", "passed": False},
                ],
            },
        ]
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
            "row_metrics": row_metrics,
            "item_evaluations": item_evaluations,
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
    assert "❌ FAIL" in markdown

    assert "## How Pass/Fail Is Determined" in markdown

    assert "## Execution Summary" in markdown
    assert "| Field | Value |" in markdown
    assert "| Backend | subprocess |" in markdown
    assert "| Duration (s) | 5.000 |" in markdown

    assert "## Metrics" in markdown
    assert "| Metric | Value | What It Measures |" in markdown
    assert "| Groundedness | 0.84 |" in markdown
    assert "Are claims supported by the retrieved context?" in markdown

    assert "## Run Metrics" in markdown
    assert "| Run Pass | 0 |" in markdown

    assert "## Threshold Checks" in markdown
    assert "| Evaluator | Threshold | Actual | Status |" in markdown
    assert "| Relevance | >= 0.95 | 0.83 | ❌ Missed |" in markdown
    assert "| Groundedness | >= 0.80 | 0.84 | ✅ Met |" in markdown


def test_report_markdown_pass_status() -> None:
    markdown = generate_report_markdown(_sample_result(overall_passed=True))
    assert "✅ PASS" in markdown


def test_report_markdown_row_details_with_input_response() -> None:
    markdown = generate_report_markdown(_sample_result(overall_passed=False, with_row_details=True))
    assert "## Row Details" in markdown
    assert "### Row 1" in markdown
    assert "**Input:** What is the refund policy?" in markdown
    assert "**Response:** Refunds are available within 30 days." in markdown
    assert "### Row 2" in markdown
    assert "**Input:** How do I reset my password?" in markdown
    assert "**Response:** Go to Settings > Security > Reset." in markdown
    # Per-row score tables
    assert "| Evaluator | Score | Threshold | Status |" in markdown
    assert "| Groundedness | 4 | >= 3 | ✅ Met |" in markdown
    assert "| Groundedness | 2 | >= 3 | ❌ Missed |" in markdown
    # Row status icons
    assert "✅ Pass" in markdown
    assert "❌ Fail" in markdown


def test_report_markdown_context_display() -> None:
    markdown = generate_report_markdown(_sample_result(overall_passed=False, with_row_details=True, with_context=True))
    assert "**Retrieved Context:**" in markdown
    assert "30-day refund policy" in markdown


def test_report_markdown_context_not_shown_when_absent() -> None:
    markdown = generate_report_markdown(_sample_result(overall_passed=False, with_row_details=True, with_context=False))
    assert "**Retrieved Context:**" not in markdown


def test_format_metric_name() -> None:
    assert _format_metric_name("groundedness") == "Groundedness"
    assert _format_metric_name("avg_latency_seconds") == "Avg. Latency Seconds"
    assert _format_metric_name("SimilarityEvaluator") == "Similarity"
    assert _format_metric_name("GroundednessEvaluator_avg") == "Groundedness Avg."
    assert _format_metric_name("f1_score") == "F1 Score"
    assert _format_metric_name("run_pass") == "Run Pass"


def test_get_evaluator_description() -> None:
    assert _get_evaluator_description("groundedness") != ""
    assert _get_evaluator_description("relevance") != ""
    assert _get_evaluator_description("unknown_metric_xyz") == ""


def test_fmt_threshold_value() -> None:
    assert _fmt_threshold_value("0.800000") == "0.80"
    assert _fmt_threshold_value("3.0") == "3"
    assert _fmt_threshold_value("0.950000") == "0.95"
    assert _fmt_threshold_value("invalid") == "invalid"


def test_report_markdown_row_details_without_data() -> None:
    markdown = generate_report_markdown(_sample_result(overall_passed=True))
    assert "## Row Details" in markdown
    assert "No input/response data captured" in markdown


def test_report_html_row_details_with_input_response() -> None:
    html = generate_report_html(_sample_result(overall_passed=False, with_row_details=True))
    assert "<h2>Row Details</h2>" in html
    assert "What is the refund policy?" in html
    assert "Refunds are available within 30 days." in html
    assert "How do I reset my password?" in html
    assert "Go to Settings &gt; Security &gt; Reset." in html
    # Per-row score tables in HTML
    assert "Groundedness" in html
    assert "How Pass/Fail Is Determined" in html
    assert "What It Measures" in html
