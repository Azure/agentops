"""Markdown report generation for AgentOps."""
from __future__ import annotations

from agentops.core.models import ComparisonResult, RunResult


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
        lines.append(f"- Items passed all thresholds: {passed_items}/{len(result.item_evaluations)}")
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
            lines.append(f"| {threshold.evaluator} | {threshold.criteria} | {threshold.expected} | {threshold.actual} | {mark} |")
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
            lines.append(f"- foundry_eval_studio_url: {result.artifacts.foundry_eval_studio_url}")
        if result.artifacts.foundry_eval_name is not None:
            lines.append(f"- foundry_eval_name: {result.artifacts.foundry_eval_name}")
    return "\n".join(lines).rstrip() + "\n"


def generate_comparison_markdown(comparison: ComparisonResult) -> str:
    verdict = "REGRESSIONS DETECTED" if comparison.summary.has_regressions else "NO REGRESSIONS"

    lines: list[str] = []
    lines.append("# AgentOps Comparison Report")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append(f"- Baseline run: **{comparison.baseline.run_id}** ({comparison.baseline.bundle_name})")
    lines.append(f"- Current run: **{comparison.current.run_id}** ({comparison.current.bundle_name})")
    lines.append(f"- Verdict: **{verdict}**")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Category | Count |")
    lines.append("|---|---:|")
    lines.append(f"| Metrics improved | {comparison.summary.metrics_improved} |")
    lines.append(f"| Metrics regressed | {comparison.summary.metrics_regressed} |")
    lines.append(f"| Metrics unchanged | {comparison.summary.metrics_unchanged} |")
    lines.append(f"| Thresholds flipped pass→fail | {comparison.summary.thresholds_flipped_pass_to_fail} |")
    lines.append(f"| Thresholds flipped fail→pass | {comparison.summary.thresholds_flipped_fail_to_pass} |")
    lines.append(f"| Items newly failing | {comparison.summary.items_newly_failing} |")
    lines.append(f"| Items newly passing | {comparison.summary.items_newly_passing} |")
    lines.append("")

    lines.append("## Metric Deltas")
    if comparison.metric_deltas:
        lines.append("")
        lines.append("| Metric | Baseline | Current | Delta | Delta % | Direction |")
        lines.append("|---|---:|---:|---:|---:|---|")
        for d in comparison.metric_deltas:
            pct = f"{d.delta_percent:+.2f}%" if d.delta_percent is not None else "N/A"
            lines.append(
                f"| {d.name} | {d.baseline_value:.6f} | {d.current_value:.6f} "
                f"| {d.delta:+.6f} | {pct} | {d.direction} |"
            )
    else:
        lines.append("- No comparable metrics found")
    lines.append("")

    lines.append("## Threshold Changes")
    flipped = [d for d in comparison.threshold_deltas if d.flipped]
    if flipped:
        lines.append("")
        lines.append("| Evaluator | Criteria | Baseline | Current | Change |")
        lines.append("|---|---|---|---|---|")
        for d in flipped:
            b_str = "PASS" if d.baseline_passed else "FAIL"
            c_str = "PASS" if d.current_passed else "FAIL"
            change = "REGRESSION" if d.baseline_passed and not d.current_passed else "IMPROVEMENT"
            lines.append(f"| {d.evaluator} | {d.criteria} | {b_str} | {c_str} | {change} |")
    else:
        lines.append("- No threshold changes detected")
    lines.append("")

    lines.append("## Item Changes")
    changed_items = [d for d in comparison.item_deltas if d.baseline_passed_all != d.current_passed_all]
    if changed_items:
        lines.append("")
        lines.append("| Row | Baseline | Current | Change |")
        lines.append("|---:|---|---|---|")
        for d in changed_items:
            b_str = "PASS" if d.baseline_passed_all else "FAIL"
            c_str = "PASS" if d.current_passed_all else "FAIL"
            change = "REGRESSION" if d.baseline_passed_all and not d.current_passed_all else "IMPROVEMENT"
            lines.append(f"| {d.row_index} | {b_str} | {c_str} | {change} |")
    else:
        lines.append("- No item-level changes detected")

    return "\n".join(lines).rstrip() + "\n"
