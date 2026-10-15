"""``--baseline`` comparison helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from agentops.core.results import (
    ComparisonInfo,
    ComparisonMetric,
    ComparisonRow,
    RunResult,
)
from agentops.pipeline.regression_insight import (
    LOWER_IS_BETTER_METRICS,
    build_regression_insight,
    metric_improved,
    metric_threshold_criteria,
    resolve_report_url,
)

# Re-exported for callers that only need the metric-direction concept
# without pulling in regression-insight building - the single source of
# truth lives in `regression_insight` (see its module comment for why).
__all__ = ["LOWER_IS_BETTER_METRICS", "metric_improved", "load_baseline", "build_comparison"]


def load_baseline(path: Path) -> RunResult:
    """Load a previous ``results.json`` for comparison."""
    if not path.exists():
        raise FileNotFoundError(f"baseline file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return RunResult.model_validate(payload)


def _direction(
    metric: str,
    current: Optional[float],
    baseline: Optional[float],
    *,
    criteria: Optional[str] = None,
) -> str:
    improved = metric_improved(metric, current, baseline, criteria=criteria)
    if improved is None:
        return "unchanged"
    return "improved" if improved else "regressed"


def _row_passed(row_metrics: List[Dict[str, float | None]]) -> bool:
    """Best-effort proxy: a row is "passing" when no metric reports an error."""
    return all("error" not in metric or not metric["error"] for metric in row_metrics)


def build_comparison(
    *,
    current: RunResult,
    baseline: RunResult,
    baseline_path: Path,
    workspace: Optional[Path] = None,
) -> ComparisonInfo:
    metrics: List[ComparisonMetric] = []
    metric_names = sorted(set(current.aggregate_metrics) | set(baseline.aggregate_metrics))
    for name in metric_names:
        current_value = current.aggregate_metrics.get(name)
        baseline_value = baseline.aggregate_metrics.get(name)
        delta = (
            current_value - baseline_value
            if current_value is not None and baseline_value is not None
            else None
        )
        metrics.append(
            ComparisonMetric(
                metric=name,
                current=current_value,
                baseline=baseline_value,
                delta=delta,
                direction=_direction(
                    name,
                    current_value,
                    baseline_value,
                    criteria=metric_threshold_criteria(name, current, baseline),
                ),
            )
        )

    rows: List[ComparisonRow] = []
    baseline_by_index = {row.row_index: row for row in baseline.rows}
    for row in current.rows:
        baseline_row = baseline_by_index.get(row.row_index)
        current_pass = row.error is None and all(
            m.value is not None or m.error is None for m in row.metrics
        )
        if baseline_row is None:
            rows.append(
                ComparisonRow(
                    row_index=row.row_index,
                    current_passed=current_pass,
                    baseline_passed=None,
                    direction="new",
                )
            )
            continue
        baseline_pass = baseline_row.error is None and all(
            m.value is not None or m.error is None for m in baseline_row.metrics
        )
        if current_pass and not baseline_pass:
            direction = "improved"
        elif baseline_pass and not current_pass:
            direction = "regressed"
        else:
            direction = "unchanged"
        rows.append(
            ComparisonRow(
                row_index=row.row_index,
                current_passed=current_pass,
                baseline_passed=baseline_pass,
                direction=direction,
            )
        )

    insight = None
    if current.commit is not None and baseline.commit is not None:
        regressed = [m for m in metrics if m.direction == "regressed"]
        if regressed:
            insight = build_regression_insight(
                baseline,
                current,
                metrics=[m.metric for m in regressed],
                workspace=workspace,
                # `baseline` is a prior, fully-published run - a sidecar
                # `cloud_evaluation.json` next to it, if any, is complete.
                # `current` is still mid-orchestration here (persisted
                # after this call, Classic Foundry `publish: true` later
                # still) - only its own in-memory config can be checked.
                # For `execution: local` + `publish: true`,
                # orchestrator._publish_to_foundry_safely patches this
                # value in and re-persists once that later publish step
                # actually completes, so it isn't permanently `None`.
                from_report_url=resolve_report_url(baseline, results_path=baseline_path),
                to_report_url=resolve_report_url(current),
            )

    return ComparisonInfo(
        baseline_path=str(baseline_path),
        baseline_started_at=baseline.started_at,
        baseline_overall_passed=baseline.summary.overall_passed,
        metrics=metrics,
        rows=rows,
        insight=insight,
    )
