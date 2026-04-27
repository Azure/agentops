"""End-to-end evaluation orchestrator for AgentOps 1.0.

This is the single entry point exercised by ``agentops eval``. It loads the
flat config, classifies the target, infers evaluators from the dataset shape,
invokes the target row-by-row, runs each evaluator, applies thresholds, and
writes ``results.json`` and ``report.md``.
"""

from __future__ import annotations

import json
import logging
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from agentops.core.agentops_config import AgentOpsConfig, Threshold, classify_agent
from agentops.core.evaluators import (
    detect_dataset_shape,
    merge_thresholds,
    select_evaluators,
)
from agentops.core.results import (
    RowMetric,
    RowResult,
    RunResult,
    RunSummary,
    TargetInfo,
)
from agentops.pipeline import comparison as comparison_module
from agentops.pipeline import invocations, publisher, reporter, runtime, thresholds

logger = logging.getLogger("agentops.pipeline")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


@dataclass
class RunOptions:
    config_path: Path
    output_dir: Path
    baseline_path: Optional[Path] = None
    timeout_seconds: float = 120.0
    dataset_override: Optional[Path] = None
    agent_override: Optional[str] = None


def run_evaluation(
    config: AgentOpsConfig,
    *,
    options: RunOptions,
) -> RunResult:
    """Run a full evaluation and persist artifacts. Returns the RunResult."""
    started_at = datetime.now(timezone.utc)
    started_perf = time.perf_counter()

    target = classify_agent(
        options.agent_override or config.agent,
        config.protocol,
    )

    dataset_path = options.dataset_override or _resolve_dataset_path(config, options)
    shape = detect_dataset_shape(dataset_path)

    overrides = (
        [override.name for override in config.evaluators] if config.evaluators else None
    )
    presets = select_evaluators(target, shape, overrides=overrides)
    user_thresholds = [
        Threshold.from_expression(metric, expr)
        for metric, expr in config.thresholds.items()
    ]
    threshold_rules = merge_thresholds(presets, user_thresholds)

    evaluator_runtimes = runtime.load_evaluators(presets)

    rows: List[RowResult] = []
    for index, row in enumerate(_iter_dataset(dataset_path)):
        rows.append(
            _evaluate_row(
                row=row,
                index=index,
                target=target,
                config=config,
                evaluators=evaluator_runtimes,
                timeout=options.timeout_seconds,
            )
        )

    aggregate = _aggregate_metrics(rows)
    threshold_results = thresholds.evaluate(threshold_rules, aggregate)
    summary = _summarize(rows, threshold_results)

    finished_at = datetime.now(timezone.utc)
    duration = time.perf_counter() - started_perf

    result = RunResult(
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
        duration_seconds=duration,
        target=TargetInfo(
            kind=target.kind,
            raw=target.raw,
            protocol=target.protocol,
            name=target.name,
            version=target.version,
            url=target.url,
            deployment=target.deployment,
        ),
        dataset_path=str(dataset_path),
        evaluators=[preset.name for preset in presets],
        rows=rows,
        aggregate_metrics=aggregate,
        thresholds=threshold_results,
        summary=summary,
        config={
            "version": config.version,
            "agent": config.agent,
            "thresholds": dict(config.thresholds),
        },
    )

    if options.baseline_path is not None:
        baseline = comparison_module.load_baseline(options.baseline_path)
        result.comparison = comparison_module.build_comparison(
            current=result,
            baseline=baseline,
            baseline_path=options.baseline_path,
        )

    _persist(result, options.output_dir)

    if config.publish == "foundry":
        _publish_to_foundry_safely(result, config, options.output_dir)

    return result


def _publish_to_foundry_safely(
    result: RunResult,
    config: AgentOpsConfig,
    output_dir: Path,
) -> None:
    """Best-effort Foundry publish. Failures are logged, never fatal."""
    if config.publish != "foundry":
        return

    try:
        published = publisher.publish_to_foundry(
            result,
            project_endpoint=config.project_endpoint,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("foundry publish failed: %s", exc)
        return

    cloud_meta_path = output_dir / "cloud_evaluation.json"
    cloud_meta_path.write_text(
        json.dumps(
            {
                "evaluation_name": published.evaluation_name,
                "report_url": published.studio_url,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("Foundry Evaluations URL: %s", published.studio_url)


def exit_code_from(result: RunResult) -> int:
    """Translate a run's outcome into the ``agentops`` CLI contract.

    * ``0`` — success, all thresholds passed.
    * ``2`` — invocations succeeded but a threshold failed.
    * ``1`` — runtime errors are raised as exceptions before this is called.
    """
    return 0 if result.summary.overall_passed else 2


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


def _resolve_dataset_path(config: AgentOpsConfig, options: RunOptions) -> Path:
    candidate = config.dataset
    if candidate.is_absolute() and candidate.exists():
        return candidate
    base = options.config_path.parent
    resolved = (base / candidate).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"dataset not found: {resolved}")
    return resolved


def _iter_dataset(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}: invalid JSON on line {line_number}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(
                    f"{path}: line {line_number} is not a JSON object"
                )
            yield row


# ---------------------------------------------------------------------------
# Per-row execution
# ---------------------------------------------------------------------------


def _evaluate_row(
    *,
    row: Dict[str, Any],
    index: int,
    target,
    config: AgentOpsConfig,
    evaluators: List[runtime.EvaluatorRuntime],
    timeout: float,
) -> RowResult:
    try:
        invocation = invocations.invoke(target, config, row, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.warning("row %d invocation failed: %s", index, exc)
        return RowResult(
            row_index=index,
            input=str(row.get("input", "")),
            expected=row.get("expected"),
            response="",
            context=row.get("context"),
            error=str(exc),
        )

    metrics: List[RowMetric] = []
    for evaluator in evaluators:
        metric = runtime.run_evaluator(
            evaluator,
            row=row,
            response=invocation.response,
            latency_seconds=invocation.latency_seconds,
        )
        metrics.append(metric)

    return RowResult(
        row_index=index,
        input=str(row.get("input", "")),
        expected=row.get("expected"),
        response=invocation.response,
        context=row.get("context"),
        latency_seconds=invocation.latency_seconds,
        tool_calls=invocation.tool_calls,
        metrics=metrics,
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _aggregate_metrics(rows: List[RowResult]) -> Dict[str, float]:
    by_metric: Dict[str, List[float]] = {}
    for row in rows:
        for metric in row.metrics:
            if metric.value is None:
                continue
            by_metric.setdefault(metric.name, []).append(metric.value)
    aggregate: Dict[str, float] = {}
    for name, values in by_metric.items():
        if values:
            aggregate[name] = statistics.fmean(values)
    return aggregate


def _summarize(
    rows: List[RowResult],
    threshold_results,
) -> RunSummary:
    items_total = len(rows)
    items_passed_all = sum(
        1
        for row in rows
        if row.error is None and all(m.error is None for m in row.metrics)
    )
    items_pass_rate = items_passed_all / items_total if items_total else 0.0
    thresholds_total = len(threshold_results)
    thresholds_passed = sum(1 for t in threshold_results if t.passed)
    threshold_pass_rate = (
        thresholds_passed / thresholds_total if thresholds_total else 1.0
    )
    overall = items_total > 0 and threshold_pass_rate == 1.0 and items_passed_all > 0
    return RunSummary(
        items_total=items_total,
        items_passed_all=items_passed_all,
        items_pass_rate=items_pass_rate,
        thresholds_total=thresholds_total,
        thresholds_passed=thresholds_passed,
        threshold_pass_rate=threshold_pass_rate,
        overall_passed=overall,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _persist(result: RunResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "results.json"
    report_path = output_dir / "report.md"

    payload = result.model_dump(mode="json")
    results_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    report_path.write_text(reporter.render(result), encoding="utf-8")
