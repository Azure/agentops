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


def load_baseline(path: Path) -> RunResult:
    """Load a previous ``results.json`` for comparison."""
    if not path.exists():
        raise FileNotFoundError(f"baseline file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return RunResult.model_validate(payload)


def _direction(current: Optional[float], baseline: Optional[float]) -> str:
    if current is None or baseline is None:
        return "unchanged"
    if current > baseline:
        return "improved"
    if current < baseline:
        return "regressed"
    return "unchanged"


def _row_passed(row_metrics: List[Dict[str, float | None]]) -> bool:
    """Best-effort proxy: a row is "passing" when no metric reports an error."""
    return all("error" not in metric or not metric["error"] for metric in row_metrics)


def build_comparison(
    *,
    current: RunResult,
    baseline: RunResult,
    baseline_path: Path,
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
                direction=_direction(current_value, baseline_value),
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

    return ComparisonInfo(
        baseline_path=str(baseline_path),
        baseline_started_at=baseline.started_at,
        baseline_overall_passed=baseline.summary.overall_passed,
        metrics=metrics,
        rows=rows,
    )
