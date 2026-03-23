"""Markdown report generation for AgentOps."""

from __future__ import annotations

from agentops.core.models import RunResult


def generate_report_markdown(result: RunResult) -> str:
    overall_status = "PASS" if result.summary.overall_passed else "FAIL"

    lines: list[str] = []
    lines.append("# AgentOps Evaluation Report")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append(f"- Bundle: {result.bundle.name}")
    lines.append(f"- Dataset: {result.dataset.name}")
    lines.append(f"- Overall status: **{overall_status}**")
    lines.append("")
    lines.append("## Execution Summary")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| Backend | {result.execution.backend} |")
    lines.append(f"| Duration (s) | {result.execution.duration_seconds:.3f} |")
    lines.append(f"| Started at | {result.execution.started_at} |")
    lines.append(f"| Finished at | {result.execution.finished_at} |")
    lines.append(f"| Exit code | {result.execution.exit_code} |")
    lines.append("")
    lines.append("## Metrics")

    if result.metrics:
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|---|---:|")
        for metric in result.metrics:
            lines.append(f"| {metric.name} | {metric.value:.6f} |")
    else:
        lines.append("- No metrics found")

    lines.append("")
    lines.append("## Run Metrics")
    if result.run_metrics:
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|---|---:|")
        for metric in result.run_metrics:
            lines.append(f"| {metric.name} | {metric.value:.6f} |")
    else:
        lines.append("- No run metrics derived")

    lines.append("")
    lines.append("## Item Verdicts")
    if result.item_evaluations:
        passed_items = sum(1 for item in result.item_evaluations if item.passed_all)
        lines.append(
            f"- Items passed all thresholds: {passed_items}/{len(result.item_evaluations)}"
        )
        lines.append("")
        lines.append("| Row | Passed All | Passed Rules | Total Rules |")
        lines.append("|---:|---|---:|---:|")
        for item in result.item_evaluations:
            passed_rules = sum(1 for threshold in item.thresholds if threshold.passed)
            lines.append(
                f"| {item.row_index} | {'PASS' if item.passed_all else 'FAIL'} | {passed_rules} | {len(item.thresholds)} |"
            )
    else:
        lines.append("- No item-level evaluations found")

    lines.append("")
    lines.append("## Threshold Checks")
    if result.thresholds:
        lines.append("")
        lines.append("| Evaluator | Criteria | Expected | Actual | Status |")
        lines.append("|---|---|---:|---:|---|")
        for threshold in result.thresholds:
            mark = "PASS" if threshold.passed else "FAIL"
            lines.append(
                f"| {threshold.evaluator} | {threshold.criteria} | {threshold.expected} | {threshold.actual} | {mark} |"
            )
    else:
        lines.append("- No thresholds configured")

    lines.append("")
    lines.append("## Artifacts")
    if result.artifacts is not None:
        if result.artifacts.backend_stdout is not None:
            lines.append(f"- backend_stdout: {result.artifacts.backend_stdout}")
        if result.artifacts.backend_stderr is not None:
            lines.append(f"- backend_stderr: {result.artifacts.backend_stderr}")
        if result.artifacts.foundry_eval_studio_url is not None:
            lines.append(
                f"- foundry_eval_studio_url: {result.artifacts.foundry_eval_studio_url}"
            )
        if result.artifacts.foundry_eval_name is not None:
            lines.append(f"- foundry_eval_name: {result.artifacts.foundry_eval_name}")
    return "\n".join(lines).rstrip() + "\n"
