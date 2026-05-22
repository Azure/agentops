"""Reporter for AgentOps 1.0 - generates ``report.md`` from a ``RunResult``."""

from __future__ import annotations

from typing import List

from agentops.core.results import (
    ComparisonInfo,
    ComparisonMetric,
    RowResult,
    RunResult,
    ThresholdEvaluation,
)


def render(result: RunResult) -> str:
    """Render a RunResult into a Markdown report."""
    lines: List[str] = []
    lines.append("# AgentOps Evaluation Report")
    lines.append("")
    overall = "✅ PASS" if result.summary.overall_passed else "❌ FAIL"
    lines.append(f"**Result:** {overall}")
    lines.append("")
    lines.append(f"- **Target:** `{result.target.raw}` ({result.target.kind})")
    if result.target.protocol:
        lines.append(f"- **Protocol:** {result.target.protocol}")
    lines.append(f"- **Dataset:** `{result.dataset_path}`")
    lines.append(f"- **Started:** {result.started_at}")
    lines.append(f"- **Duration:** {result.duration_seconds:.2f}s")
    lines.append(f"- **Rows:** {result.summary.items_total}")
    lines.append("")

    if result.aggregate_metrics:
        lines.append("## Metrics")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("| --- | --- |")
        for name, value in sorted(result.aggregate_metrics.items()):
            lines.append(f"| {name} | {value:.3f} |")
        lines.append("")

    if result.thresholds:
        lines.append("## Thresholds")
        lines.append("")
        lines.append("| Metric | Expected | Actual | Status |")
        lines.append("| --- | --- | --- | --- |")
        for threshold in result.thresholds:
            lines.append(_threshold_row(threshold))
        lines.append("")

    if result.comparison is not None:
        lines.extend(_render_comparison(result.comparison))
        lines.append("")

    error_rows = [row for row in result.rows if row.error]
    if error_rows:
        lines.append("## Failed Invocations")
        lines.append("")
        lines.append("| Row | Error |")
        lines.append("| --- | --- |")
        for row in error_rows:
            lines.append(f"| {row.row_index} | {_short(row.error or '', 200)} |")
        lines.append("")

    lines.append("## Rows")
    lines.append("")
    lines.append("| # | Latency (s) | Metrics |")
    lines.append("| --- | --- | --- |")
    for row in result.rows:
        lines.append(_row_summary(row))
    lines.append("")

    if result.rows:
        lines.append("## Row Details")
        lines.append("")
        lines.append("| # | Input | Response | Expected |")
        lines.append("| --- | --- | --- | --- |")
        for row in result.rows:
            lines.append(_row_detail(row))
        lines.append("")

    cloud = result.config.get("cloud_evaluation")
    if isinstance(cloud, dict):
        lines.extend(_render_cloud_evaluation(cloud))
        lines.append("")
    return "\n".join(lines)


def _threshold_row(threshold: ThresholdEvaluation) -> str:
    status = "✅" if threshold.passed else "❌"
    return f"| {threshold.metric} | `{threshold.expected}` | `{threshold.actual}` | {status} |"


def _row_summary(row: RowResult) -> str:
    parts = []
    for metric in row.metrics:
        if metric.error:
            parts.append(f"{metric.name}=ERR")
        elif metric.value is not None:
            parts.append(f"{metric.name}={metric.value:.2f}")
    metrics_str = ", ".join(parts) if parts else " - "
    latency = f"{row.latency_seconds:.2f}" if row.latency_seconds is not None else " - "
    return f"| {row.row_index} | {latency} | {metrics_str} |"


def _row_detail(row: RowResult) -> str:
    response = row.response or (f"Error: {row.error}" if row.error else " - ")
    return (
        f"| {row.row_index} | {_cell(row.input, 220)} | "
        f"{_cell(response, 220)} | {_cell(row.expected or ' - ', 220)} |"
    )


def _cell(value: str, limit: int) -> str:
    return _short(str(value), limit).replace("\r", " ").replace("\n", "<br>")


def _short(text: str, limit: int) -> str:
    text = text.replace("\n", " ").replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _render_cloud_evaluation(cloud: dict) -> List[str]:
    lines = ["## Foundry Cloud Session", ""]
    status = str(cloud.get("status") or "unknown")
    lines.append(f"- **Status:** {status}")
    if cloud.get("evaluation_name"):
        lines.append(f"- **Evaluation:** `{cloud['evaluation_name']}`")
    if cloud.get("eval_id"):
        lines.append(f"- **Eval ID:** `{cloud['eval_id']}`")
    if cloud.get("run_id"):
        lines.append(f"- **Run ID:** `{cloud['run_id']}`")
    if cloud.get("report_url"):
        lines.append(f"- **Foundry URL:** {cloud['report_url']}")
    dataset = cloud.get("dataset")
    if isinstance(dataset, dict):
        lines.extend(_render_cloud_dataset(dataset))
    if cloud.get("error"):
        lines.append(f"- **Error:** {_cell(str(cloud['error']), 300)}")
    return lines


def _render_cloud_dataset(dataset: dict) -> List[str]:
    source_type = str(dataset.get("source_type") or "unknown")
    requested = str(dataset.get("requested_mode") or dataset.get("mode") or "unknown")
    if source_type == "file_content":
        label = "inline file_content"
        behavior = dataset.get("foundry_behavior")
    elif dataset.get("foundry_name"):
        label = (
            f"Foundry dataset `{dataset.get('foundry_name')}`"
            f"@`{dataset.get('foundry_version') or 'unknown'}`"
        )
        behavior = None
    else:
        label = source_type
        behavior = None

    lines = [f"- **Dataset:** {label} (requested `{requested}`)"]
    if dataset.get("local_path"):
        lines.append(f"  - Local source: `{dataset['local_path']}`")
    if dataset.get("sha256"):
        lines.append(f"  - SHA-256: `{str(dataset['sha256'])[:12]}...`")
    if behavior:
        lines.append(f"  - Note: {_cell(str(behavior), 300)}")
    return lines


def _render_comparison(comparison: ComparisonInfo) -> List[str]:
    lines = ["## Comparison vs Baseline", ""]
    lines.append(f"**Baseline:** `{comparison.baseline_path}`")
    if comparison.baseline_started_at:
        lines.append(f"**Baseline run:** {comparison.baseline_started_at}")
    lines.append("")

    lines.append("| Metric | Baseline | Current | Δ | Direction |")
    lines.append("| --- | --- | --- | --- | --- |")
    for metric in comparison.metrics:
        lines.append(_comparison_metric_row(metric))
    lines.append("")

    regressed = [r for r in comparison.rows if r.direction == "regressed"]
    improved = [r for r in comparison.rows if r.direction == "improved"]
    if regressed or improved:
        lines.append("**Per-row changes:**")
        if regressed:
            lines.append(
                "- ❌ Regressed rows: " + ", ".join(str(r.row_index) for r in regressed)
            )
        if improved:
            lines.append(
                "- ✅ Improved rows: " + ", ".join(str(r.row_index) for r in improved)
            )
    return lines


def _comparison_metric_row(metric: ComparisonMetric) -> str:
    arrow = {"improved": "🟢", "regressed": "🔴", "unchanged": "⚪"}[metric.direction]
    baseline = f"{metric.baseline:.3f}" if metric.baseline is not None else " - "
    current = f"{metric.current:.3f}" if metric.current is not None else " - "
    delta = f"{metric.delta:+.3f}" if metric.delta is not None else " - "
    return f"| {metric.metric} | {baseline} | {current} | {delta} | {arrow} {metric.direction} |"
