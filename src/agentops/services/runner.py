"""Evaluation run orchestration service."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from agentops.backends.base import BackendRunContext
from agentops.backends.foundry_backend import FoundryBackend
from agentops.backends.subprocess_backend import SubprocessBackend
from agentops.core.config_loader import (
    load_bundle_config,
    load_dataset_config,
    load_run_config,
)
from agentops.core.models import (
    Artifacts,
    ItemEvaluationResult,
    ItemThresholdEvaluationResult,
    MetricResult,
    RowMetricsResult,
    RunResult,
    Summary,
    ThresholdEvaluationResult,
    ThresholdRule,
)
from agentops.core.reporter import generate_report_markdown
from agentops.services.foundry_evals import publish_foundry_evaluation


@dataclass(frozen=True)
class EvalRunServiceResult:
    output_dir: Path
    results_path: Path
    report_path: Path
    exit_code: int


def _default_run_config_path() -> Path:
    return (Path.cwd() / ".agentops" / "run.yaml").resolve()


def _resolve_path(path_value: Path, base_dir: Path) -> Path:
    if path_value.is_absolute():
        return path_value
    candidate = (base_dir / path_value).resolve()
    if candidate.exists():
        return candidate

    fallback = (Path.cwd() / path_value).resolve()
    if fallback.exists():
        return fallback

    return candidate


def _default_output_dir(run_config_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return (run_config_path.parent / "results" / timestamp).resolve()


def _latest_output_dir(run_config_path: Path) -> Path:
    return (run_config_path.parent / "results" / "latest").resolve()


def _sync_latest_output(source_output_dir: Path, latest_output_dir: Path) -> None:
    if source_output_dir.resolve() == latest_output_dir.resolve():
        return
    if latest_output_dir.exists():
        shutil.rmtree(latest_output_dir)
    shutil.copytree(source_output_dir, latest_output_dir)


def _load_backend_metrics(
    metrics_path: Path,
) -> Tuple[List[MetricResult], List[RowMetricsResult]]:
    if not metrics_path.exists():
        raise FileNotFoundError(f"Backend metrics file not found: {metrics_path}")

    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid backend metrics payload: expected JSON object")

    raw_metrics = payload.get("metrics")
    if not isinstance(raw_metrics, list):
        raise ValueError("Invalid backend metrics payload: 'metrics' must be a list")

    metrics: List[MetricResult] = []
    for item in raw_metrics:
        if not isinstance(item, dict):
            raise ValueError(
                "Invalid backend metrics payload: metric entries must be objects"
            )
        metrics.append(MetricResult.model_validate(item))
    raw_row_metrics = payload.get("row_metrics", [])
    if not isinstance(raw_row_metrics, list):
        raise ValueError(
            "Invalid backend metrics payload: 'row_metrics' must be a list"
        )

    row_metrics: List[RowMetricsResult] = []
    for item in raw_row_metrics:
        if not isinstance(item, dict):
            raise ValueError(
                "Invalid backend metrics payload: row_metrics entries must be objects"
            )
        row_metrics.append(RowMetricsResult.model_validate(item))

    return metrics, row_metrics


def _load_cloud_evaluation_metadata(output_dir: Path) -> tuple[str | None, str | None]:
    cloud_meta_path = output_dir / "cloud_evaluation.json"
    if not cloud_meta_path.exists():
        return None, None

    payload = json.loads(cloud_meta_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None, None

    report_url = payload.get("report_url")
    evaluation_name = payload.get("evaluation_name") or payload.get("run_name")
    if not isinstance(report_url, str):
        report_url = None
    if not isinstance(evaluation_name, str):
        evaluation_name = None
    return report_url, evaluation_name


def _summary_from_thresholds(
    metrics: List[MetricResult], threshold_passes: List[bool]
) -> Summary:
    thresholds_count = len(threshold_passes)
    thresholds_passed = sum(1 for value in threshold_passes if value)
    thresholds_failed = thresholds_count - thresholds_passed
    overall_passed = thresholds_failed == 0
    return Summary(
        metrics_count=len(metrics),
        thresholds_count=thresholds_count,
        thresholds_passed=thresholds_passed,
        thresholds_failed=thresholds_failed,
        overall_passed=overall_passed,
    )


def _rule_expected_text(rule: ThresholdRule) -> str:
    if rule.criteria in {"true", "false"}:
        return rule.criteria
    if rule.value is None:
        return ""
    return f"{float(rule.value):.6f}"


def _evaluate_threshold_against_value(
    *,
    row_index: int,
    rule: ThresholdRule,
    actual_value: float,
) -> ItemThresholdEvaluationResult:
    if rule.criteria in {"true", "false"}:
        expected_bool = rule.criteria == "true"
        if actual_value in (0.0, 1.0):
            actual_bool = actual_value == 1.0
        else:
            raise ValueError(
                f"Evaluator '{rule.evaluator}' must produce 0/1 for boolean criteria"
            )

        return ItemThresholdEvaluationResult(
            row_index=row_index,
            evaluator=rule.evaluator,
            criteria=rule.criteria,
            expected="true" if expected_bool else "false",
            actual="true" if actual_bool else "false",
            passed=actual_bool is expected_bool,
        )

    if rule.value is None:
        raise ValueError(
            f"Threshold for evaluator '{rule.evaluator}' requires a numeric value"
        )

    target_value = float(rule.value)
    if rule.criteria == ">=":
        passed = actual_value >= target_value
    elif rule.criteria == ">":
        passed = actual_value > target_value
    elif rule.criteria == "<=":
        passed = actual_value <= target_value
    elif rule.criteria == "<":
        passed = actual_value < target_value
    elif rule.criteria == "==":
        passed = actual_value == target_value
    else:
        raise ValueError(f"Unsupported threshold criteria: {rule.criteria}")

    return ItemThresholdEvaluationResult(
        row_index=row_index,
        evaluator=rule.evaluator,
        criteria=rule.criteria,
        expected=f"{target_value:.6f}",
        actual=f"{actual_value:.6f}",
        passed=passed,
    )


def _evaluate_item_thresholds(
    threshold_rules: List[ThresholdRule],
    row_metrics: List[RowMetricsResult],
) -> List[ItemEvaluationResult]:
    if not row_metrics:
        return []

    results: List[ItemEvaluationResult] = []
    for row in sorted(row_metrics, key=lambda value: value.row_index):
        row_values = {metric.name: metric.value for metric in row.metrics}
        threshold_results: List[ItemThresholdEvaluationResult] = []
        for rule in threshold_rules:
            if rule.evaluator not in row_values:
                raise ValueError(
                    f"Missing evaluator score '{rule.evaluator}' for row {row.row_index}"
                )

            threshold_results.append(
                _evaluate_threshold_against_value(
                    row_index=row.row_index,
                    rule=rule,
                    actual_value=row_values[rule.evaluator],
                )
            )

        passed_all = (
            all(item.passed for item in threshold_results)
            if threshold_results
            else True
        )
        results.append(
            ItemEvaluationResult(
                row_index=row.row_index,
                passed_all=passed_all,
                thresholds=threshold_results,
            )
        )

    return results


def _validate_enabled_evaluators_scored(
    *,
    evaluator_names: List[str],
    row_metrics: List[RowMetricsResult],
) -> None:
    if not evaluator_names:
        return

    if not row_metrics:
        raise ValueError(
            "Enabled evaluators require backend 'row_metrics' with per-item scores"
        )

    scored_names: set[str] = set()
    for row in row_metrics:
        for metric in row.metrics:
            scored_names.add(metric.name)

    missing = [name for name in evaluator_names if name not in scored_names]
    if missing:
        raise ValueError(
            "Missing scores for enabled evaluators: " + ", ".join(sorted(missing))
        )


def _summarize_thresholds_from_items(
    threshold_rules: List[ThresholdRule],
    item_evaluations: List[ItemEvaluationResult],
) -> List[ThresholdEvaluationResult]:
    if not threshold_rules:
        return []

    summary: List[ThresholdEvaluationResult] = []
    total_items = len(item_evaluations)

    for rule in threshold_rules:
        rule_results: List[ItemThresholdEvaluationResult] = []
        for item in item_evaluations:
            for threshold_result in item.thresholds:
                if (
                    threshold_result.evaluator == rule.evaluator
                    and threshold_result.criteria == rule.criteria
                ):
                    rule_results.append(threshold_result)

        passed_items = sum(1 for result in rule_results if result.passed)
        passed = bool(rule_results) and passed_items == len(rule_results)

        summary.append(
            ThresholdEvaluationResult(
                evaluator=rule.evaluator,
                criteria=rule.criteria,
                expected=_rule_expected_text(rule),
                actual=f"{passed_items}/{total_items} items",
                passed=passed,
            )
        )

    return summary


def _derive_run_metrics(
    metrics_by_name: Dict[str, float],
    row_metrics: List[RowMetricsResult],
    item_evaluations: List[ItemEvaluationResult],
    summary: Summary,
) -> List[MetricResult]:
    run_metrics: List[MetricResult] = []
    seen_run_metric_names: set[str] = set()

    def _append_run_metric(name: str, value: float) -> None:
        if name in seen_run_metric_names:
            return
        run_metrics.append(MetricResult(name=name, value=value))
        seen_run_metric_names.add(name)

    _append_run_metric("run_pass", 1.0 if summary.overall_passed else 0.0)

    if summary.thresholds_count > 0:
        _append_run_metric(
            "threshold_pass_rate",
            summary.thresholds_passed / summary.thresholds_count,
        )

    if item_evaluations:
        passed_items = sum(1 for item in item_evaluations if item.passed_all)
        _append_run_metric("items_total", float(len(item_evaluations)))
        _append_run_metric("items_passed_all", float(passed_items))
        _append_run_metric(
            "items_failed_any", float(len(item_evaluations) - passed_items)
        )
        _append_run_metric("items_pass_rate", passed_items / len(item_evaluations))

    row_aggregates: Dict[str, List[float]] = {}
    for row in row_metrics:
        for metric in row.metrics:
            row_aggregates.setdefault(metric.name, []).append(metric.value)

    for metric_name in sorted(row_aggregates):
        values = row_aggregates[metric_name]
        if values:
            mean_value = sum(values) / len(values)
            variance = sum((value - mean_value) ** 2 for value in values) / len(values)
            stddev_value = variance**0.5

            _append_run_metric(f"{metric_name}_avg", mean_value)
            _append_run_metric(f"{metric_name}_stddev", stddev_value)

    if "exact_match" in row_aggregates:
        values = row_aggregates["exact_match"]
        _append_run_metric("accuracy", sum(values) / len(values))
    elif "exact_match" in metrics_by_name:
        _append_run_metric("accuracy", metrics_by_name["exact_match"])

    return run_metrics


def run_evaluation(
    config_path: Path | None = None, output_override: Path | None = None
) -> EvalRunServiceResult:
    run_config_path = (
        config_path.resolve() if config_path is not None else _default_run_config_path()
    )
    run_config = load_run_config(run_config_path)

    run_config_dir = run_config_path.parent
    bundle_path = _resolve_path(run_config.bundle.path, run_config_dir)
    dataset_path = _resolve_path(run_config.dataset.path, run_config_dir)

    bundle_config = load_bundle_config(bundle_path)
    dataset_config = load_dataset_config(dataset_path)

    output_dir = (
        output_override.resolve()
        if output_override is not None
        else _default_output_dir(run_config_path)
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    if run_config.backend.type == "subprocess":
        backend = SubprocessBackend()
    elif run_config.backend.type == "foundry":
        backend = FoundryBackend()
    else:
        raise ValueError(f"Unsupported backend type: {run_config.backend.type}")

    backend_result = backend.execute(
        BackendRunContext(
            backend_config=run_config.backend,
            bundle_path=bundle_path,
            dataset_path=dataset_path,
            backend_output_dir=output_dir,
        )
    )

    if backend_result.exit_code != 0:
        raise RuntimeError(
            f"Backend execution failed with exit code {backend_result.exit_code}"
        )

    backend_metrics_path = output_dir / "backend_metrics.json"
    metrics, row_metrics = _load_backend_metrics(backend_metrics_path)
    metrics_by_name: Dict[str, float] = {
        metric.name: metric.value for metric in metrics
    }

    enabled_evaluator_names = [
        evaluator.name for evaluator in bundle_config.evaluators if evaluator.enabled
    ]
    _validate_enabled_evaluators_scored(
        evaluator_names=enabled_evaluator_names,
        row_metrics=row_metrics,
    )

    item_evaluations = _evaluate_item_thresholds(bundle_config.thresholds, row_metrics)

    if bundle_config.thresholds and not row_metrics:
        raise ValueError(
            "Item-level threshold evaluation requires backend 'row_metrics'"
        )

    threshold_results = _summarize_thresholds_from_items(
        bundle_config.thresholds, item_evaluations
    )
    summary = _summary_from_thresholds(
        metrics, [item.passed for item in threshold_results]
    )
    run_metrics = _derive_run_metrics(
        metrics_by_name, row_metrics, item_evaluations, summary
    )

    foundry_eval_studio_url: str | None = None
    foundry_eval_name: str | None = None

    cloud_report_url, cloud_evaluation_name = _load_cloud_evaluation_metadata(
        output_dir
    )
    if cloud_report_url is not None:
        foundry_eval_studio_url = cloud_report_url
    if cloud_evaluation_name is not None:
        foundry_eval_name = cloud_evaluation_name

    if (
        run_config.output.publish_foundry_evaluation
        and run_config.backend.type == "foundry"
        and cloud_report_url is None
    ):
        try:
            foundry_publish = publish_foundry_evaluation(
                backend_config=run_config.backend,
                dataset_config_path=dataset_path,
                backend_stdout_path=backend_result.stdout_file,
            )
            foundry_eval_studio_url = foundry_publish.studio_url
            foundry_eval_name = foundry_publish.evaluation_name
        except Exception as exc:  # noqa: BLE001
            if run_config.output.fail_on_foundry_publish_error:
                raise RuntimeError(f"Foundry evaluation publish failed: {exc}") from exc
            publish_error_path = output_dir / "foundry_eval_publish_error.log"
            publish_error_path.write_text(str(exc), encoding="utf-8")

    normalized_result = RunResult(
        version=1,
        status="completed",
        bundle={"name": bundle_config.name, "path": bundle_path},
        dataset={"name": dataset_config.name, "path": dataset_path},
        execution={
            "backend": backend_result.backend,
            "command": backend_result.command,
            "started_at": backend_result.started_at,
            "finished_at": backend_result.finished_at,
            "duration_seconds": backend_result.duration_seconds,
            "exit_code": backend_result.exit_code,
        },
        metrics=metrics,
        row_metrics=row_metrics,
        item_evaluations=item_evaluations,
        run_metrics=run_metrics,
        thresholds=threshold_results,
        summary=summary,
        artifacts=Artifacts(
            backend_stdout=backend_result.stdout_file.name,
            backend_stderr=backend_result.stderr_file.name,
            foundry_eval_studio_url=foundry_eval_studio_url,
            foundry_eval_name=foundry_eval_name,
        ),
    )

    results_path = output_dir / "results.json"
    report_path = output_dir / "report.md"

    results_path.write_text(
        json.dumps(normalized_result.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    report_path.write_text(
        generate_report_markdown(normalized_result), encoding="utf-8"
    )

    latest_dir = _latest_output_dir(run_config_path)
    _sync_latest_output(output_dir, latest_dir)

    exit_code = 0 if summary.overall_passed else 2
    return EvalRunServiceResult(
        output_dir=output_dir,
        results_path=results_path,
        report_path=report_path,
        exit_code=exit_code,
    )
