"""Comparison service for evaluating baseline vs current run results."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from agentops.core.models import (
    ComparisonResult,
    ComparisonSummary,
    ItemDelta,
    MetricDelta,
    RunReference,
    RunResult,
    ThresholdDelta,
)


@dataclass(frozen=True)
class ComparisonServiceResult:
    comparison_json_path: Path
    comparison_md_path: Path
    has_regressions: bool


def _resolve_run_path(run_id: str, workspace_dir: Path | None = None) -> Path:
    """Resolve a run identifier to a results.json path.

    Supports:
    - Absolute or relative path to a results.json file
    - Absolute or relative path to a run directory containing results.json
    - Timestamped run ID (e.g. '2026-03-03_143022') resolved under workspace results
    - The keyword 'latest'
    """
    candidate = Path(run_id)

    if candidate.is_absolute():
        if candidate.is_file():
            return candidate
        results_in_dir = candidate / "results.json"
        if results_in_dir.is_file():
            return results_in_dir
        raise FileNotFoundError(f"Cannot find results.json at: {candidate}")

    if candidate.is_file():
        return candidate.resolve()
    if candidate.is_dir():
        results_in_dir = candidate / "results.json"
        if results_in_dir.is_file():
            return results_in_dir.resolve()

    results_base = workspace_dir or (Path.cwd() / ".agentops")
    results_dir = results_base / "results" if results_base.name != "results" else results_base
    run_dir = results_dir / run_id
    results_file = run_dir / "results.json"
    if results_file.is_file():
        return results_file.resolve()

    raise FileNotFoundError(
        f"Cannot resolve run '{run_id}' to a results.json file. "
        f"Searched: {results_file}"
    )


def _load_run_result(path: Path) -> RunResult:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return RunResult.model_validate(payload)


def _run_reference(result: RunResult, run_id: str) -> RunReference:
    return RunReference(
        run_id=run_id,
        bundle_name=result.bundle.name,
        dataset_name=result.dataset.name,
        started_at=result.execution.started_at,
    )


def _compute_direction(delta: float) -> str:
    if delta > 0:
        return "improved"
    elif delta < 0:
        return "regressed"
    return "unchanged"


def _lower_is_better_metrics(result: RunResult) -> frozenset[str]:
    """Derive which metrics are lower-is-better from threshold criteria.

    If a threshold uses ``<=`` or ``<``, the metric is lower-is-better.
    """
    return frozenset(
        t.evaluator
        for t in result.thresholds
        if t.criteria in {"<=", "<"}
    )


def _compute_metric_direction(delta: float, lower_is_better: bool) -> str:
    if delta == 0:
        return "unchanged"
    if lower_is_better:
        return "improved" if delta < 0 else "regressed"
    return "improved" if delta > 0 else "regressed"


def _compute_metric_deltas(
    baseline: RunResult, current: RunResult
) -> List[MetricDelta]:
    baseline_map: Dict[str, float] = {m.name: m.value for m in baseline.metrics}
    current_map: Dict[str, float] = {m.name: m.value for m in current.metrics}

    lib_metrics = _lower_is_better_metrics(baseline) | _lower_is_better_metrics(current)

    all_names = list(dict.fromkeys(
        [m.name for m in baseline.metrics] + [m.name for m in current.metrics]
    ))

    deltas: List[MetricDelta] = []
    for name in all_names:
        if name not in baseline_map or name not in current_map:
            continue

        baseline_val = baseline_map[name]
        current_val = current_map[name]
        delta = current_val - baseline_val
        delta_pct: Optional[float] = None
        if baseline_val != 0:
            delta_pct = (delta / abs(baseline_val)) * 100

        deltas.append(
            MetricDelta(
                name=name,
                baseline_value=baseline_val,
                current_value=current_val,
                delta=delta,
                delta_percent=delta_pct,
                direction=_compute_metric_direction(delta, name in lib_metrics),
            )
        )

    return deltas


def _compute_threshold_deltas(
    baseline: RunResult, current: RunResult
) -> List[ThresholdDelta]:
    baseline_map = {
        (t.evaluator, t.criteria): t.passed for t in baseline.thresholds
    }
    current_map = {
        (t.evaluator, t.criteria): t.passed for t in current.thresholds
    }

    all_keys = list(dict.fromkeys(
        list(baseline_map.keys()) + list(current_map.keys())
    ))

    deltas: List[ThresholdDelta] = []
    for evaluator, criteria in all_keys:
        b_passed = baseline_map.get((evaluator, criteria))
        c_passed = current_map.get((evaluator, criteria))

        if b_passed is None or c_passed is None:
            continue

        deltas.append(
            ThresholdDelta(
                evaluator=evaluator,
                criteria=criteria,
                baseline_passed=b_passed,
                current_passed=c_passed,
                flipped=b_passed != c_passed,
            )
        )

    return deltas


def _compute_item_deltas(
    baseline: RunResult,
    current: RunResult,
    metric_names: List[str],
) -> List[ItemDelta]:
    baseline_items = {item.row_index: item for item in baseline.item_evaluations}
    current_items = {item.row_index: item for item in current.item_evaluations}

    baseline_row_metrics = {row.row_index: row for row in baseline.row_metrics}
    current_row_metrics = {row.row_index: row for row in current.row_metrics}

    lib_metrics = _lower_is_better_metrics(baseline) | _lower_is_better_metrics(current)

    all_indices = sorted(set(baseline_items.keys()) | set(current_items.keys()))

    deltas: List[ItemDelta] = []
    for idx in all_indices:
        b_item = baseline_items.get(idx)
        c_item = current_items.get(idx)

        if b_item is None or c_item is None:
            continue

        b_row = baseline_row_metrics.get(idx)
        c_row = current_row_metrics.get(idx)

        row_metric_deltas: List[MetricDelta] = []
        if b_row and c_row:
            b_vals = {m.name: m.value for m in b_row.metrics}
            c_vals = {m.name: m.value for m in c_row.metrics}

            for name in metric_names:
                if name in b_vals and name in c_vals:
                    delta = c_vals[name] - b_vals[name]
                    delta_pct: Optional[float] = None
                    if b_vals[name] != 0:
                        delta_pct = (delta / abs(b_vals[name])) * 100
                    row_metric_deltas.append(
                        MetricDelta(
                            name=name,
                            baseline_value=b_vals[name],
                            current_value=c_vals[name],
                            delta=delta,
                            delta_percent=delta_pct,
                            direction=_compute_metric_direction(delta, name in lib_metrics),
                        )
                    )

        deltas.append(
            ItemDelta(
                row_index=idx,
                baseline_passed_all=b_item.passed_all,
                current_passed_all=c_item.passed_all,
                metric_deltas=row_metric_deltas,
            )
        )

    return deltas


def _build_summary(
    metric_deltas: List[MetricDelta],
    threshold_deltas: List[ThresholdDelta],
    item_deltas: List[ItemDelta],
) -> ComparisonSummary:
    metrics_improved = sum(1 for d in metric_deltas if d.direction == "improved")
    metrics_regressed = sum(1 for d in metric_deltas if d.direction == "regressed")
    metrics_unchanged = sum(1 for d in metric_deltas if d.direction == "unchanged")

    pass_to_fail = sum(
        1 for d in threshold_deltas if d.flipped and d.baseline_passed and not d.current_passed
    )
    fail_to_pass = sum(
        1 for d in threshold_deltas if d.flipped and not d.baseline_passed and d.current_passed
    )

    items_newly_failing = sum(
        1 for d in item_deltas if d.baseline_passed_all and not d.current_passed_all
    )
    items_newly_passing = sum(
        1 for d in item_deltas if not d.baseline_passed_all and d.current_passed_all
    )

    has_regressions = pass_to_fail > 0 or metrics_regressed > 0 or items_newly_failing > 0

    return ComparisonSummary(
        metrics_improved=metrics_improved,
        metrics_regressed=metrics_regressed,
        metrics_unchanged=metrics_unchanged,
        thresholds_flipped_pass_to_fail=pass_to_fail,
        thresholds_flipped_fail_to_pass=fail_to_pass,
        items_newly_failing=items_newly_failing,
        items_newly_passing=items_newly_passing,
        has_regressions=has_regressions,
    )


def compare_runs(
    baseline_path: Path,
    current_path: Path,
    baseline_id: str = "",
    current_id: str = "",
) -> ComparisonResult:
    """Compare two evaluation runs and return a structured comparison."""
    baseline = _load_run_result(baseline_path)
    current = _load_run_result(current_path)

    b_ref = _run_reference(baseline, baseline_id or baseline_path.parent.name)
    c_ref = _run_reference(current, current_id or current_path.parent.name)

    metric_names = list(dict.fromkeys(
        [m.name for m in baseline.metrics] + [m.name for m in current.metrics]
    ))

    metric_deltas = _compute_metric_deltas(baseline, current)
    threshold_deltas = _compute_threshold_deltas(baseline, current)
    item_deltas = _compute_item_deltas(baseline, current, metric_names)
    summary = _build_summary(metric_deltas, threshold_deltas, item_deltas)

    return ComparisonResult(
        version=1,
        baseline=b_ref,
        current=c_ref,
        metric_deltas=metric_deltas,
        threshold_deltas=threshold_deltas,
        item_deltas=item_deltas,
        summary=summary,
    )


def run_comparison(
    baseline_id: str,
    current_id: str,
    output_dir: Path | None = None,
) -> ComparisonServiceResult:
    """Resolve run IDs, compare, and write comparison outputs."""
    from agentops.core.reporter import generate_comparison_markdown

    baseline_path = _resolve_run_path(baseline_id)
    current_path = _resolve_run_path(current_id)

    result = compare_runs(
        baseline_path=baseline_path,
        current_path=current_path,
        baseline_id=baseline_id,
        current_id=current_id,
    )

    resolved_output = output_dir.resolve() if output_dir else current_path.parent
    resolved_output.mkdir(parents=True, exist_ok=True)

    comparison_json_path = resolved_output / "comparison.json"
    comparison_md_path = resolved_output / "comparison.md"

    comparison_json_path.write_text(
        json.dumps(result.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    comparison_md_path.write_text(
        generate_comparison_markdown(result),
        encoding="utf-8",
    )

    return ComparisonServiceResult(
        comparison_json_path=comparison_json_path,
        comparison_md_path=comparison_md_path,
        has_regressions=result.summary.has_regressions,
    )
