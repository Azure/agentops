"""Comparison service for evaluating baseline vs current run results."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from agentops.core.models import (
    ComparisonConditions,
    ComparisonItemRow,
    ComparisonMetricRow,
    ComparisonResult,
    ComparisonSummary,
    ComparisonThresholdRow,
    RunReference,
    RunResult,
)


@dataclass(frozen=True)
class ComparisonServiceResult:
    comparison_json_path: Path
    comparison_md_path: Path | None
    comparison_html_path: Path | None
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


def _parse_command_field(command: str) -> dict[str, str]:
    """Extract key=value pairs from the execution command string."""
    parts = command.split()
    result: dict[str, str] = {}
    for part in parts:
        if "=" in part:
            key, _, value = part.partition("=")
            result[key] = value
    return result


def _run_reference(result: RunResult, run_id: str) -> RunReference:
    cmd = _parse_command_field(result.execution.command)
    # Infer target from command fields
    target = cmd.get("target")
    if not target:
        if cmd.get("agent_id"):
            target = "agent"
        elif cmd.get("model"):
            target = "model"
    return RunReference(
        run_id=run_id,
        bundle_name=result.bundle.name,
        dataset_name=result.dataset.name,
        started_at=result.execution.started_at,
        backend=result.execution.backend,
        target=target,
        model=cmd.get("model"),
        agent_id=cmd.get("agent_id"),
        project_endpoint=cmd.get("project_endpoint"),
        overall_passed=result.summary.overall_passed,
    )


def _lower_is_better_metrics(*results: RunResult) -> frozenset[str]:
    """Derive which metrics are lower-is-better from threshold criteria.

    If a threshold uses ``<=`` or ``<``, the metric is lower-is-better.
    """
    names: set[str] = set()
    for r in results:
        for t in r.thresholds:
            if t.criteria in {"<=", "<"}:
                names.add(t.evaluator)
    return frozenset(names)


def _compute_metric_direction(delta: float, lower_is_better: bool) -> str:
    if delta == 0:
        return "unchanged"
    if lower_is_better:
        return "improved" if delta < 0 else "regressed"
    return "improved" if delta > 0 else "regressed"


def _detect_conditions(refs: List[RunReference]) -> ComparisonConditions:
    """Detect what's fixed vs varying across runs to determine comparison type."""
    dimensions = {
        "dataset": [r.dataset_name for r in refs],
        "agent": [r.agent_id or "-" for r in refs],
        "model": [r.model or "-" for r in refs],
        "backend": [r.backend or "-" for r in refs],
        "target": [r.target or "-" for r in refs],
        "bundle": [r.bundle_name for r in refs],
        "project": [r.project_endpoint or "-" for r in refs],
    }

    fixed: Dict[str, str] = {}
    varying: List[str] = []
    # Fields always shown in Run Details — exclude from fixed list
    always_shown = {"target", "model", "agent"}
    for key, values in dimensions.items():
        unique = set(values)
        if len(unique) == 1:
            if key not in always_shown:
                fixed[key] = values[0]
        else:
            varying.append(key)

    # Determine comparison type
    if "dataset" not in varying and "agent" in varying:
        ctype = "agent"
    elif "dataset" not in varying and "model" in varying:
        ctype = "model"
    elif "dataset" in varying and "agent" not in varying and "model" not in varying:
        ctype = "dataset"
    else:
        ctype = "general"

    # Row-level comparison is only valid when all runs use the same dataset
    row_level_valid = "dataset" not in varying

    return ComparisonConditions(
        comparison_type=ctype,
        fixed=fixed,
        varying=varying,
        row_level_valid=row_level_valid,
    )


def compare_runs(
    run_paths: List[Path],
    run_ids: List[str],
) -> ComparisonResult:
    """Compare N evaluation runs. The first run is the baseline."""
    results = [_load_run_result(p) for p in run_paths]
    refs = [_run_reference(r, rid) for r, rid in zip(results, run_ids)]

    lib_metrics = _lower_is_better_metrics(*results)

    # Collect all metric names preserving order
    all_metric_names: List[str] = []
    seen_names: set[str] = set()
    for r in results:
        for m in r.metrics:
            if m.name not in seen_names:
                all_metric_names.append(m.name)
                seen_names.add(m.name)

    # Build metric rows
    metric_rows: List[ComparisonMetricRow] = []
    for name in all_metric_names:
        values: List[float] = []
        deltas: List[Optional[float]] = []
        delta_percents: List[Optional[float]] = []
        directions: List[str] = []
        baseline_val: Optional[float] = None

        for i, r in enumerate(results):
            val_map = {m.name: m.value for m in r.metrics}
            val = val_map.get(name)
            if val is None:
                values.append(0.0)
                deltas.append(None)
                delta_percents.append(None)
                directions.append("unchanged")
                continue

            values.append(val)
            if i == 0:
                baseline_val = val
                deltas.append(None)
                delta_percents.append(None)
                directions.append("unchanged")
            else:
                if baseline_val is not None:
                    d = val - baseline_val
                    dp = (d / abs(baseline_val) * 100) if baseline_val != 0 else None
                    deltas.append(d)
                    delta_percents.append(dp)
                    directions.append(_compute_metric_direction(d, name in lib_metrics))
                else:
                    deltas.append(None)
                    delta_percents.append(None)
                    directions.append("unchanged")

        # Best run: for lower-is-better pick min, otherwise pick max
        valid_vals = [
            (i, v) for i, v in enumerate(values)
            if any(m.name == name for m in results[i].metrics)
        ]
        best_idx: Optional[int] = None
        if valid_vals:
            if name in lib_metrics:
                best_idx = min(valid_vals, key=lambda x: x[1])[0]
            else:
                best_idx = max(valid_vals, key=lambda x: x[1])[0]

        metric_rows.append(ComparisonMetricRow(
            name=name,
            values=values,
            deltas=deltas,
            delta_percents=delta_percents,
            directions=directions,
            best_run_index=best_idx,
        ))

    # Build threshold rows
    all_thresholds: List[tuple[str, str]] = []
    seen_thresholds: set[tuple[str, str]] = set()
    for r in results:
        for t in r.thresholds:
            key = (t.evaluator, t.criteria)
            if key not in seen_thresholds:
                all_thresholds.append(key)
                seen_thresholds.add(key)

    threshold_rows: List[ComparisonThresholdRow] = []
    for evaluator, criteria in all_thresholds:
        passed_list: List[bool] = []
        target_val: str | None = None
        for r in results:
            t_map = {(t.evaluator, t.criteria): t for t in r.thresholds}
            t = t_map.get((evaluator, criteria))
            passed_list.append(t.passed if t else False)
            if t and target_val is None:
                target_val = t.expected
        threshold_rows.append(ComparisonThresholdRow(
            evaluator=evaluator,
            criteria=criteria,
            target=target_val,
            passed=passed_list,
        ))

    # Build item rows
    all_row_indices: set[int] = set()
    for r in results:
        for item in r.item_evaluations:
            all_row_indices.add(item.row_index)

    # Collect evaluator names that have thresholds (for row-level display)
    threshold_evaluator_names = [tr.evaluator for tr in threshold_rows]

    item_rows: List[ComparisonItemRow] = []
    for idx in sorted(all_row_indices):
        passed_list = []
        # Per-evaluator scores for this row across all runs
        scores: Dict[str, List[Optional[float]]] = {name: [] for name in threshold_evaluator_names}
        for r in results:
            item_map = {item.row_index: item for item in r.item_evaluations}
            item = item_map.get(idx)
            passed_list.append(item.passed_all if item else False)
            # Extract row-level metric scores
            row_metrics_map = {row.row_index: row for row in r.row_metrics}
            row_m = row_metrics_map.get(idx)
            for name in threshold_evaluator_names:
                if row_m:
                    val_map = {m.name: m.value for m in row_m.metrics}
                    scores[name].append(val_map.get(name))
                else:
                    scores[name].append(None)
        item_rows.append(ComparisonItemRow(row_index=idx, passed_all=passed_list, scores=scores))

    # Summary: regression = a run whose status flipped from PASS to FAIL,
    # or a threshold that was met by baseline but missed by this run.
    # Minor numeric shifts within passing thresholds are NOT regressions.
    runs_with_regressions: List[int] = []
    for i in range(1, len(results)):
        has_reg = False
        # Check if overall run status flipped PASS→FAIL
        if results[0].summary.overall_passed and not results[i].summary.overall_passed:
            has_reg = True
        # Check if any row flipped from passing to failing
        if not has_reg:
            for ir in item_rows:
                if ir.passed_all[0] and not ir.passed_all[i]:
                    has_reg = True
                    break
        if has_reg:
            runs_with_regressions.append(i)

    summary = ComparisonSummary(
        run_count=len(results),
        any_regressions=len(runs_with_regressions) > 0,
        runs_with_regressions=runs_with_regressions,
    )

    return ComparisonResult(
        version=1,
        runs=refs,
        baseline_index=0,
        conditions=_detect_conditions(refs),
        metric_rows=metric_rows,
        threshold_rows=threshold_rows,
        item_rows=item_rows,
        summary=summary,
    )


def run_comparison(
    run_ids: List[str],
    output_dir: Path | None = None,
    report_format: str = "md",
) -> ComparisonServiceResult:
    """Resolve run IDs, compare, and write comparison outputs."""
    from agentops.core.reporter import generate_comparison_html, generate_comparison_markdown

    paths = [_resolve_run_path(rid) for rid in run_ids]
    result = compare_runs(run_paths=paths, run_ids=run_ids)

    resolved_output = output_dir.resolve() if output_dir else paths[-1].parent
    resolved_output.mkdir(parents=True, exist_ok=True)

    comparison_json_path = resolved_output / "comparison.json"
    comparison_md_path: Path | None = None
    comparison_html_path: Path | None = None

    comparison_json_path.write_text(
        json.dumps(result.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    if report_format in ("md", "all"):
        comparison_md_path = resolved_output / "comparison.md"
        comparison_md_path.write_text(
            generate_comparison_markdown(result),
            encoding="utf-8",
        )
    if report_format in ("html", "all"):
        comparison_html_path = resolved_output / "comparison.html"
        comparison_html_path.write_text(
            generate_comparison_html(result),
            encoding="utf-8",
        )

    return ComparisonServiceResult(
        comparison_json_path=comparison_json_path,
        comparison_md_path=comparison_md_path,
        comparison_html_path=comparison_html_path,
        has_regressions=result.summary.any_regressions,
    )
