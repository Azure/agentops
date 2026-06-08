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
import sys
import time
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from agentops.core.agentops_config import AgentOpsConfig, Threshold, classify_agent
from agentops.core.azd_eval import load_eval_recipe
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
from agentops.utils import telemetry
from agentops.utils.colors import style

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
    # Optional callback invoked with progress messages during a run. The
    # CLI wires this to ``typer.echo`` so users see per-row progress
    # ("invoking", "scored", ...) instead of long unexplained pauses.
    # Library callers can leave it as ``None`` to keep runs silent.
    progress: Optional[Callable[[str], None]] = field(default=None, repr=False)


def run_evaluation(
    config: AgentOpsConfig,
    *,
    options: RunOptions,
) -> RunResult:
    """Run a full evaluation and persist artifacts. Returns the RunResult."""
    telemetry.init_tracing()
    try:
        return _run_evaluation(config, options=options)
    finally:
        telemetry.shutdown()


def _run_evaluation(
    config: AgentOpsConfig,
    *,
    options: RunOptions,
) -> RunResult:
    """Run a full evaluation after optional telemetry has been initialized."""
    if options.baseline_path is not None and not options.baseline_path.exists():
        raise FileNotFoundError(
            f"baseline file not found: {options.baseline_path}. "
            "Run `agentops eval run` once without `--baseline` first, then copy "
            "`.agentops/results/latest/results.json` to the baseline path."
        )

    if config.execution == "cloud":
        return _run_evaluation_cloud(config, options=options)
    if _resolve_execution_backend(config) == "azd":
        return _run_evaluation_azd(config, options=options)
    return _run_evaluation_local(config, options=options)


def _resolve_execution_backend(config: AgentOpsConfig) -> str:
    """Resolve the effective backend without probing tool availability."""

    if config.execution != "auto":
        return config.execution
    target = classify_agent(config.agent, config.protocol)
    if target.kind in {"foundry_prompt", "foundry_hosted"}:
        return "azd"
    return "local"


def _run_evaluation_local(
    config: AgentOpsConfig,
    *,
    options: RunOptions,
) -> RunResult:
    """Local execution: AgentOps invokes the agent + evaluators row-by-row."""

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
    presets = select_evaluators(
        target,
        shape,
        overrides=overrides,
        threshold_metrics=config.thresholds.keys(),
    )
    user_thresholds = [
        Threshold.from_expression(metric, expr)
        for metric, expr in config.thresholds.items()
    ]
    threshold_rules = merge_thresholds(presets, user_thresholds)

    evaluator_runtimes = runtime.load_evaluators(presets)

    progress = options.progress or (lambda _msg: None)

    dataset_rows = list(_iter_dataset(dataset_path))
    total = len(dataset_rows)
    from agentops import __version__ as _agentops_version
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    progress(
        f"{style('agentops', 'bold', 'cyan')} {style(_agentops_version, 'cyan')} "
        f"{style('|', 'dim')} python {py} "
        f"{style('|', 'dim')} config: {style(options.config_path.name, 'cyan')}"
    )
    progress(
        f"Loaded {style(str(total), 'bold')} row(s) from "
        f"{style(dataset_path.name, 'cyan')}; running "
        f"{style(str(len(presets)), 'bold')} evaluator(s) against "
        f"{_friendly_target_kind(target.kind)}: {style(target.raw, 'bold')}."
    )

    with telemetry.eval_run_span(
        bundle_name=options.config_path.stem,
        dataset_name=dataset_path.name,
        backend_type=target.kind,
        target=target.raw,
        model=target.deployment,
        agent_id=target.raw if target.kind.startswith("foundry") else None,
    ) as run_span:
        rows: List[RowResult] = []
        rules_by_metric = {rule.metric: rule for rule in threshold_rules}
        for index, row in enumerate(dataset_rows):
            rows.append(
                _evaluate_row(
                    row=row,
                    index=index,
                    total=total,
                    target=target,
                    config=config,
                    evaluators=evaluator_runtimes,
                    timeout=options.timeout_seconds,
                    progress=progress,
                    rules_by_metric=rules_by_metric,
                )
            )

        aggregate = _aggregate_metrics(rows)
        threshold_results = thresholds.evaluate(threshold_rules, aggregate)
        summary = _summarize(rows, threshold_results)
        telemetry.set_eval_run_result(
            run_span,
            passed=summary.overall_passed,
            items_total=summary.items_total,
            items_passed=summary.items_passed_all,
        )

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

    # Local execution only ever publishes to Classic Foundry. Cloud
    # execution goes through _run_evaluation_cloud and never reaches here.
    if config.publish_target() == "foundry":
        _publish_to_foundry_safely(result, config, options.output_dir, progress=progress)

    return result


def _run_evaluation_cloud(
    config: AgentOpsConfig,
    *,
    options: RunOptions,
) -> RunResult:
    """Cloud execution: Foundry invokes the agent + evaluators server-side.

    The agent is invoked exactly once - on Foundry's side. AgentOps does
    not run the row-by-row local loop. After the cloud run completes we
    download the per-row ``output_items`` and reshape them into the same
    :class:`RunResult` schema that local execution produces, so
    ``report.md`` and ``--baseline`` work identically.
    """
    started_at = datetime.now(timezone.utc)
    started_perf = time.perf_counter()

    target = classify_agent(
        options.agent_override or config.agent,
        config.protocol,
    )
    if target.kind != "foundry_prompt":
        raise ValueError(
            "execution: cloud only supports Foundry prompt agents "
            f"('name:version'); got target.kind={target.kind!r}."
        )

    dataset_path = options.dataset_override or _resolve_dataset_path(config, options)
    shape = detect_dataset_shape(dataset_path)
    overrides = (
        [override.name for override in config.evaluators] if config.evaluators else None
    )
    all_presets = select_evaluators(
        target,
        shape,
        overrides=overrides,
        threshold_metrics=config.thresholds.keys(),
    )

    # Cloud execution runs server-side, so client-side runtime evaluators
    # (e.g. avg_latency_seconds) cannot be measured. Excluding them is the
    # right choice - otherwise their default thresholds would mark the run
    # FAILED for a metric we never had a chance to observe.
    presets = [p for p in all_presets if "runtime" not in p.categories]
    skipped_runtime = [p.name for p in all_presets if "runtime" in p.categories]

    user_thresholds = [
        Threshold.from_expression(metric, expr)
        for metric, expr in config.thresholds.items()
        # Drop user-specified thresholds for runtime metrics too - they
        # would otherwise fail with actual="missing".
        if metric not in {p.score_key for p in all_presets if "runtime" in p.categories}
    ]
    threshold_rules = merge_thresholds(presets, user_thresholds)

    # Build a "shell" result that carries just enough metadata for the
    # cloud publisher to map evaluator class names onto Azure AI evaluator
    # testing criteria.
    shell_target = TargetInfo(
        kind=target.kind,
        raw=target.raw,
        protocol=target.protocol,
        name=target.name,
        version=target.version,
        url=target.url,
        deployment=target.deployment,
    )

    progress = options.progress or (lambda _msg: None)
    from agentops import __version__ as _agentops_version
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    progress(
        f"{style('agentops', 'bold', 'cyan')} {style(_agentops_version, 'cyan')} "
        f"{style('|', 'dim')} python {py} "
        f"{style('|', 'dim')} config: {style(options.config_path.name, 'cyan')}"
    )
    progress(
        f"execution: {style('cloud', 'bold')} - Foundry will run the agent "
        f"and {style(str(len(presets)), 'bold')} evaluator(s) server-side. "
        f"Agent: {style(target.raw, 'bold')}."
    )
    if skipped_runtime:
        progress(
            f"  (skipped client-side runtime evaluators: "
            f"{', '.join(skipped_runtime)} - not measurable in cloud mode)"
        )

    shell_result = RunResult(
        started_at=started_at.isoformat(),
        finished_at=started_at.isoformat(),
        duration_seconds=0.0,
        target=shell_target,
        dataset_path=str(dataset_path),
        evaluators=[preset.name for preset in presets],
        rows=[],
        aggregate_metrics={},
        thresholds=[],
        summary=_summarize([], []),
        config={
            "version": config.version,
            "agent": config.agent,
            "thresholds": dict(config.thresholds),
        },
    )

    endpoint = config.project_endpoint or os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        raise ValueError(
            "execution: cloud requires either 'project_endpoint' in "
            "agentops.yaml or the AZURE_AI_FOUNDRY_PROJECT_ENDPOINT env var."
        )

    from agentops.pipeline import cloud_runner
    from agentops.pipeline import cloud_results

    with telemetry.eval_run_span(
        bundle_name=options.config_path.stem,
        dataset_name=dataset_path.name,
        backend_type="foundry_cloud",
        target=target.raw,
        model=target.deployment,
        agent_id=target.raw,
    ) as run_span:
        published = cloud_runner.run_on_foundry_cloud(
            shell_result,
            dataset_path=dataset_path,
            project_endpoint=endpoint,
            dataset_sync=config.dataset_sync,
            progress=progress,
        )

        if run_span is not None:
            run_span.set_attribute("agentops.eval.execution", "cloud")
            run_span.set_attribute("agentops.eval.cloud.eval_id", published.eval_id)
            run_span.set_attribute("agentops.eval.cloud.run_id", published.run_id)
            run_span.set_attribute("agentops.eval.cloud.status", published.status)
            if published.report_url:
                run_span.set_attribute(
                    "agentops.eval.cloud.report_url",
                    published.report_url,
                )
            if published.dataset:
                run_span.set_attribute(
                    "agentops.eval.cloud.dataset.mode",
                    str(published.dataset.get("mode") or ""),
                )
                dataset_id = published.dataset.get("id")
                if dataset_id:
                    run_span.set_attribute(
                        "agentops.eval.cloud.dataset.id",
                        str(dataset_id),
                    )

        rows = cloud_results.rows_from_cloud_output_items(published.output_items)
        aggregate = _aggregate_metrics(rows)
        threshold_results = thresholds.evaluate(threshold_rules, aggregate)
        summary = _summarize(rows, threshold_results)
        telemetry.set_eval_run_result(
            run_span,
            passed=summary.overall_passed,
            items_total=summary.items_total,
            items_passed=summary.items_passed_all,
        )

    finished_at = datetime.now(timezone.utc)
    duration = time.perf_counter() - started_perf

    # Always persist the raw Foundry output_items next to results.json /
    # report.md so the run is debuggable from the artifact bundle alone.
    # This is the only place the per-row grader payloads survive in their
    # native shape; without it a parser regression looks the same in CI
    # as a real eval failure.
    try:
        options.output_dir.mkdir(parents=True, exist_ok=True)
        raw_items_path = options.output_dir / "cloud_output_items.json"
        raw_items_path.write_text(
            json.dumps(list(published.output_items), indent=2, default=str),
            encoding="utf-8",
        )
    except (OSError, TypeError) as exc:
        progress(f"warning: failed to write cloud_output_items.json: {exc}")
        raw_items_path = None

    # If the cloud run yielded zero usable metric values despite running
    # graders, surface that loudly so the user does not chase a phantom
    # "threshold failed" gate. The artifact dumped above is the triage
    # entry point, but we also lift the first per-metric error string
    # into the warning itself so CI logs carry the actionable cause
    # (most often: the Foundry evaluator service principal lacks
    # `Cognitive Services OpenAI User` on the model deployment).
    if presets and not aggregate and rows:
        suffix = (
            f" Inspect {raw_items_path}." if raw_items_path is not None else ""
        )
        first_error = _first_metric_error(rows)
        cause_suffix = f" First grader error: {first_error}" if first_error else ""
        progress(
            "warning: cloud eval returned 0 usable metric scores across "
            f"{len(rows)} row(s).{cause_suffix}{suffix}"
        )

    result = RunResult(
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
        duration_seconds=duration,
        target=shell_target,
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
            "execution": "cloud",
            "cloud_evaluation": {
                "mode": "cloud",
                "evaluation_name": published.evaluation_name,
                "eval_id": published.eval_id,
                "run_id": published.run_id,
                "status": published.status,
                "report_url": published.report_url,
                "dataset": published.dataset,
            },
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

    # Write cloud_evaluation.json next to the other artifacts for parity
    # with the (now-removed) post-run cloud publish path.
    cloud_meta_path = options.output_dir / "cloud_evaluation.json"
    cloud_meta_path.write_text(
        json.dumps(result.config["cloud_evaluation"], indent=2),
        encoding="utf-8",
    )

    progress(
        f"Submitted to {style('New Foundry Evaluations', 'bold')}: "
        f"{style(published.report_url or '(no portal URL)', 'cyan')}"
    )
    progress(
        f"  eval_id={published.eval_id} run_id={published.run_id} "
        f"status={style(published.status, 'green' if published.status == 'completed' else 'yellow')} "
        f"rows={len(rows)}"
    )

    if not rows:
        progress(
            f"{style('WARNING', 'yellow')}: no per-row results were "
            f"downloaded from Foundry; report.md will be minimal. The "
            f"canonical view is the Foundry portal."
        )

    return result


def _run_evaluation_azd(
    config: AgentOpsConfig,
    *,
    options: RunOptions,
) -> RunResult:
    """azd execution: delegate to ``azd ai agent eval`` with no hidden fallback."""

    started_at = datetime.now(timezone.utc)
    progress = options.progress or (lambda _msg: None)
    workspace = options.config_path.parent

    target = classify_agent(
        options.agent_override or config.agent,
        config.protocol,
    )
    if target.kind not in {"foundry_prompt", "foundry_hosted"}:
        raise ValueError(
            "execution: azd supports Foundry prompt or hosted agents only. "
            "Use execution: local for HTTP/JSON, model:, or custom REST targets."
        )

    from agentops.pipeline import azd_runner

    recipe_path = azd_runner.resolve_eval_recipe(workspace, config)
    recipe = load_eval_recipe(recipe_path)
    progress(
        f"execution: {style('azd', 'bold')} - delegating to "
        f"{style('azd ai agent eval', 'cyan')} with recipe "
        f"{style(str(recipe_path), 'cyan')}."
    )

    azd_run = azd_runner.run_azd_eval(
        recipe_path,
        workspace=workspace,
        progress=progress,
        timeout_seconds=max(options.timeout_seconds, azd_runner.AZD_EVAL_TIMEOUT_SECONDS),
    )
    result = azd_runner.normalize_to_results(
        azd_run,
        config=config,
        recipe=recipe,
        started_at=started_at,
    )

    if options.baseline_path is not None:
        baseline = comparison_module.load_baseline(options.baseline_path)
        result.comparison = comparison_module.build_comparison(
            current=result,
            baseline=baseline,
            baseline_path=options.baseline_path,
        )

    _persist(result, options.output_dir)
    azd_runner.write_raw_artifacts(azd_run, options.output_dir)
    return result


def _publish_to_foundry_safely(
    result: RunResult,
    config: AgentOpsConfig,
    output_dir: Path,
    *,
    progress: Optional[Callable[[str], None]] = None,
) -> None:
    """Best-effort Classic Foundry publish. Failures are logged, never fatal."""
    if config.publish_target() != "foundry":
        return

    notify = progress or (lambda _msg: None)

    try:
        published = publisher.publish_to_foundry(
            result,
            project_endpoint=config.project_endpoint,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("foundry publish failed: %s", exc)
        notify(
            f"{style('publish foundry FAILED', 'red')}: {exc}. "
            f"Local results.json is the source of truth."
        )
        return

    cloud_meta_path = output_dir / "cloud_evaluation.json"
    cloud_meta_path.write_text(
        json.dumps(
            {
                "mode": "classic",
                "evaluation_name": published.evaluation_name,
                "report_url": published.studio_url,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    notify(
        f"Published to {style('Classic Foundry Evaluations', 'bold')}: "
        f"{style(published.studio_url, 'cyan')}"
    )
    notify(
        f"Tip: to run server-side in the {style('New Foundry', 'bold')} "
        f"experience, set 'execution: cloud' + 'publish: true' (preview)."
    )


def exit_code_from(result: RunResult) -> int:
    """Translate a run's outcome into the ``agentops`` CLI contract.

    * ``0`` - success, all thresholds passed.
    * ``2`` - invocations succeeded but a threshold failed.
    * ``1`` - runtime errors are raised as exceptions before this is called.
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


_FRIENDLY_KIND = {
    "foundry_prompt": "foundry agent",
    "foundry_hosted": "foundry agent (hosted)",
    "http_json": "http endpoint",
    "model_direct": "model deployment",
}


def _friendly_target_kind(kind: str) -> str:
    return _FRIENDLY_KIND.get(kind, kind)


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


def _metric_passes(rule: Threshold, value: float) -> bool:
    if rule.value is None or rule.criteria in {"true", "false"}:
        return True
    target_v = float(rule.value)
    c = rule.criteria
    if c == ">=":
        return value >= target_v
    if c == ">":
        return value > target_v
    if c == "<=":
        return value <= target_v
    if c == "<":
        return value < target_v
    if c == "==":
        return value == target_v
    return True


def _evaluate_row(
    *,
    row: Dict[str, Any],
    index: int,
    total: int,
    target,
    config: AgentOpsConfig,
    evaluators: List[runtime.EvaluatorRuntime],
    timeout: float,
    progress: Callable[[str], None],
    rules_by_metric: Optional[Dict[str, Threshold]] = None,
) -> RowResult:
    label = style(f"[{index + 1}/{total}]", "dim")
    preview = str(row.get("input", "")).strip().replace("\n", " ")
    if len(preview) > 80:
        preview = preview[:77] + "..."
    progress(f"{label} invoking target: {preview!r}")
    expected = row.get("expected")
    expected_text = str(expected) if expected is not None else None

    with telemetry.eval_item_span(
        row_index=index,
        input_text=str(row.get("input", "")),
        expected_text=expected_text,
    ) as item_span:
        try:
            with telemetry.agent_invoke_span(
                target="agent" if target.kind.startswith("foundry") else "model",
                model=target.deployment,
                agent_id=target.raw if target.kind.startswith("foundry") else None,
                agent_name=target.name,
                agent_version=target.version,
            ) as invoke_span:
                invocation = invocations.invoke(target, config, row, timeout=timeout)
                telemetry.set_agent_invoke_result(
                    invoke_span,
                    response_model=target.deployment,
                )
        except Exception as exc:  # noqa: BLE001
            telemetry.set_eval_item_result(item_span, passed=False)
            logger.debug("row %d invocation failed: %s", index, exc)
            progress(f"{label} {style('invocation FAILED', 'bold', 'red')}: {exc}")
            return RowResult(
                row_index=index,
                input=str(row.get("input", "")),
                expected=row.get("expected"),
                response="",
                context=row.get("context"),
                error=str(exc),
            )

        tool_count = len(invocation.tool_calls) if invocation.tool_calls else 0
        progress(
            f"{label} replied in {style(f'{invocation.latency_seconds:.2f}s', 'cyan')} "
            f"({tool_count} tool call(s)); scoring..."
        )

        metrics: List[RowMetric] = []
        for evaluator in evaluators:
            metric = runtime.run_evaluator(
                evaluator,
                row=row,
                response=invocation.response,
                latency_seconds=invocation.latency_seconds,
                actual_tool_calls=invocation.tool_calls,
            )
            metrics.append(metric)

            rule = (rules_by_metric or {}).get(metric.name)
            metric_passed = (
                None
                if metric.value is None or rule is None
                else _metric_passes(rule, float(metric.value))
            )
            telemetry.record_evaluator_span(
                evaluator_name=evaluator.preset.name,
                builtin_name=metric.name,
                source=(
                    "local"
                    if evaluator.preset.class_name == "_latency"
                    else "azure-ai-evaluation"
                ),
                score=float(metric.value) if metric.value is not None else 0.0,
                threshold=rule.value if rule is not None else None,
                criteria=rule.criteria if rule is not None else None,
                passed=metric_passed,
            )

        telemetry.set_eval_item_result(
            item_span,
            passed=all(metric.error is None for metric in metrics),
        )

    rules = rules_by_metric or {}

    def _format_metric(m: RowMetric) -> str:
        if isinstance(m.value, (int, float)):
            rule = rules.get(m.name)
            text = f"{m.value:.2f}"
            if rule is None:
                # No user threshold for this metric: keep value neutral
                # so the line stays readable.
                return f"{m.name}={text}"
            color = "green" if _metric_passes(rule, float(m.value)) else "red"
            return f"{m.name}={style(text, color)}"
        if m.error:
            return f"{m.name}={style('ERR', 'red')}"
        return f"{m.name}={style('n/a', 'dim')}"

    scored = ", ".join(_format_metric(m) for m in metrics)
    progress(f"{label} scored: {scored}")

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


def _first_metric_error(rows: List[RowResult]) -> Optional[str]:
    """Return the first non-empty per-metric error string across all rows.

    Used by the cloud-eval orchestrator to lift the actionable cause of
    an all-null aggregate (e.g., RBAC failure on the evaluator's model
    deployment) into the user-facing warning so CI logs carry the signal
    without operators having to download the raw artifact.
    """
    for row in rows:
        for metric in row.metrics:
            if isinstance(metric.error, str) and metric.error.strip():
                return metric.error.strip()
    return None


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
