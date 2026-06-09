"""Subprocess adapter for ``azd ai agent eval`` runs."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from agentops.core.agentops_config import AgentOpsConfig, Threshold, classify_agent
from agentops.core.azd_eval import (
    EvalRecipe,
    bind_threshold_metrics,
    find_eval_yaml,
)
from agentops.core.results import RunResult, RunSummary, TargetInfo
from agentops.pipeline import thresholds


AZD_EXTENSION_NAME = "azure.ai.agents"
AZD_AVAILABILITY_TIMEOUT_SECONDS = 10.0
AZD_EVAL_TIMEOUT_SECONDS = 1800.0
AZD_PROGRESS_INTERVAL_SECONDS = 30.0


class AzdBackendError(RuntimeError):
    """User-actionable azd backend failure."""


@dataclass(frozen=True)
class AzdEvalRun:
    """Native azd evaluation run payload plus raw command output."""

    recipe_path: Path
    payload: Dict[str, Any]
    run_id: Optional[str]
    status: str
    stdout: str
    stderr: str
    duration_seconds: float


def azd_available(*, cwd: Optional[Path] = None) -> bool:
    """Return whether azd and the AI agents extension are available."""

    try:
        subprocess.run(
            ["azd", "version"],
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            timeout=AZD_AVAILABILITY_TIMEOUT_SECONDS,
            check=True,
        )
        extensions = subprocess.run(
            ["azd", "extension", "list"],
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            timeout=AZD_AVAILABILITY_TIMEOUT_SECONDS,
            check=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return AZD_EXTENSION_NAME in (extensions.stdout + extensions.stderr)


def resolve_eval_recipe(workspace: Path, config: AgentOpsConfig) -> Path:
    """Resolve the azd recipe path for an azd-backed run."""

    recipe = find_eval_yaml(workspace, config.eval_recipe)
    if recipe is None:
        raise AzdBackendError(
            "azd eval recipe not found. Run `azd ai agent eval init` first, "
            "commit the generated eval.yaml, or set `eval_recipe:` in agentops.yaml. "
            "If you want the AgentOps local engine instead, set `execution: local`."
        )
    if not recipe.exists():
        raise AzdBackendError(
            f"azd eval recipe not found at {recipe}. Run `azd ai agent eval init` "
            "or update `eval_recipe:` in agentops.yaml."
        )
    return recipe


def run_azd_eval(
    recipe_path: Path,
    *,
    workspace: Path,
    progress: Optional[Callable[[str], None]] = None,
    timeout_seconds: float = AZD_EVAL_TIMEOUT_SECONDS,
) -> AzdEvalRun:
    """Run ``azd ai agent eval`` and return its normalized native payload."""

    notify = progress or (lambda _msg: None)
    if not azd_available(cwd=workspace):
        raise AzdBackendError(
            "azd AI agent evaluation is not available. Install azd and the "
            f"`{AZD_EXTENSION_NAME}` extension (`azd extension install "
            f"{AZD_EXTENSION_NAME}`), then rerun `agentops eval run`."
        )

    command = [
        "azd",
        "--no-prompt",
        "ai",
        "agent",
        "eval",
        "run",
        "--config",
        str(recipe_path),
        "--output",
        "json",
    ]
    notify(f"Running azd backend: {' '.join(command)}")

    started = time.perf_counter()
    completed = _run_command(
        command,
        cwd=workspace,
        timeout_seconds=timeout_seconds,
        progress=progress,
        progress_label="azd eval run",
    )
    duration = time.perf_counter() - started
    if completed.returncode != 0:
        raise AzdBackendError(_format_command_failure("azd ai agent eval run", completed))

    run_payload = _parse_json_object(completed.stdout)
    run_id = _extract_run_id(run_payload) or _extract_labeled_id(completed.stdout, "Run")
    eval_id = _extract_eval_id(run_payload) or _extract_labeled_id(completed.stdout, "Eval")
    show_payload: Dict[str, Any] = {}
    show_stdout = ""
    show_stderr = ""
    if run_id and eval_id:
        with tempfile.TemporaryDirectory() as temp_dir:
            details_path = Path(temp_dir) / "azd-eval-run.json"
            show = _run_command(
                [
                    "azd",
                    "--no-prompt",
                    "ai",
                    "agent",
                    "eval",
                    "show",
                    eval_id,
                    "--eval-run-id",
                    run_id,
                    "--out-file",
                    str(details_path),
                ],
                cwd=workspace,
                timeout_seconds=timeout_seconds,
            )
            show_stdout = show.stdout
            show_stderr = show.stderr
            if show.returncode != 0:
                raise AzdBackendError(_format_command_failure("azd ai agent eval show", show))
            if details_path.exists():
                show_stdout = "\n".join(
                    part for part in (show_stdout, details_path.read_text(encoding="utf-8")) if part
                )
                show_payload = _parse_json_object(details_path.read_text(encoding="utf-8"))

    payload = show_payload or run_payload
    if not payload:
        raise AzdBackendError(
            "azd completed but did not return readable JSON metrics. "
            "The raw stdout/stderr were captured by the command runner; rerun "
            "`azd ai agent eval show --output json` to inspect the native result."
        )

    return AzdEvalRun(
        recipe_path=recipe_path,
        payload=payload,
        run_id=run_id or _extract_run_id(payload),
        status=_extract_status(payload),
        stdout="\n".join(part for part in (completed.stdout, show_stdout) if part),
        stderr="\n".join(part for part in (completed.stderr, show_stderr) if part),
        duration_seconds=duration,
    )


def normalize_to_results(
    azd_run: AzdEvalRun,
    *,
    config: AgentOpsConfig,
    recipe: EvalRecipe,
    started_at: datetime,
) -> RunResult:
    """Normalize an azd run to the stable AgentOps ``results.json`` schema."""

    target = classify_agent(config.agent, config.protocol)
    aggregate_metrics = _extract_numeric_metrics(azd_run.payload)
    if not aggregate_metrics:
        raise AzdBackendError(
            "azd eval run returned no numeric metrics, so AgentOps cannot apply "
            "thresholds or claim the gate passed."
        )
    _validate_rubric_evidence(config=config, recipe=recipe, metrics=aggregate_metrics)

    metric_binding = bind_threshold_metrics(config.thresholds.keys(), aggregate_metrics.keys())
    if metric_binding.unmatched:
        names = ", ".join(metric_binding.unmatched)
        available = ", ".join(sorted(aggregate_metrics))
        raise AzdBackendError(
            f"threshold metric(s) not found in azd results: {names}. "
            f"Available azd metrics: {available}."
        )
    if metric_binding.ambiguous:
        details = "; ".join(
            f"{name} -> {', '.join(matches)}"
            for name, matches in sorted(metric_binding.ambiguous.items())
        )
        raise AzdBackendError(f"ambiguous azd threshold metric binding: {details}")

    threshold_rules = [
        Threshold.from_expression(metric, expression)
        for metric, expression in config.thresholds.items()
    ]
    threshold_metrics = {
        threshold_name: aggregate_metrics[actual_name]
        for threshold_name, actual_name in metric_binding.bound.items()
    }
    threshold_results = thresholds.evaluate(threshold_rules, threshold_metrics)
    thresholds_total = len(threshold_results)
    thresholds_passed = sum(1 for item in threshold_results if item.passed)
    threshold_pass_rate = (
        thresholds_passed / thresholds_total if thresholds_total else 1.0
    )
    status_ok = azd_run.status.lower() in {"completed", "succeeded", "success", "passed"}
    overall_passed = status_ok and threshold_pass_rate == 1.0
    items_total = _extract_item_count(azd_run.payload)
    items_passed = items_total if overall_passed else 0

    finished_at = datetime.now(timezone.utc)
    dataset_path = _recipe_dataset_path(recipe, azd_run.recipe_path)
    return RunResult(
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
        duration_seconds=azd_run.duration_seconds,
        target=TargetInfo(
            kind=target.kind,
            raw=target.raw,
            protocol=target.protocol,
            name=target.name,
            version=target.version,
            url=target.url,
            deployment=target.deployment,
        ),
        dataset_path=dataset_path,
        evaluators=[evaluator.name for evaluator in recipe.evaluators],
        rows=[],
        aggregate_metrics=aggregate_metrics,
        thresholds=threshold_results,
        summary=RunSummary(
            items_total=items_total,
            items_passed_all=items_passed,
            items_pass_rate=(items_passed / items_total if items_total else 0.0),
            thresholds_total=thresholds_total,
            thresholds_passed=thresholds_passed,
            threshold_pass_rate=threshold_pass_rate,
            overall_passed=overall_passed,
        ),
        config={
            "version": config.version,
            "agent": config.agent,
            "thresholds": dict(config.thresholds),
            "dataset_kind": config.dataset_kind,
            "rubrics": [rubric.model_dump(mode="json") for rubric in config.rubrics],
            "execution": "azd",
            "backend_requested": "azd",
            "backend_effective": "azd",
            "degraded": False,
            "result_granularity": "aggregate",
            "azd_evaluation": {
                "recipe_path": str(azd_run.recipe_path),
                "run_id": azd_run.run_id,
                "status": azd_run.status,
                "dataset": (
                    recipe.dataset_reference.model_dump(mode="json")
                    if recipe.dataset_reference
                    else None
                ),
                "metric_binding": metric_binding.bound,
                "unused_metrics": list(metric_binding.unused_metrics),
            },
        },
    )


def _validate_rubric_evidence(
    *,
    config: AgentOpsConfig,
    recipe: EvalRecipe,
    metrics: Dict[str, float],
) -> None:
    if not config.rubrics:
        return

    recipe_evaluators = {evaluator.name for evaluator in recipe.evaluators}
    threshold_names = set(config.thresholds)
    metric_names = set(metrics)
    missing: list[str] = []

    for rubric in config.rubrics:
        evaluator_name = (rubric.evaluator or rubric.name).strip()
        if evaluator_name not in recipe_evaluators:
            missing.append(f"rubric evaluator `{evaluator_name}` in eval.yaml")
        dimension_names = [dimension.name for dimension in rubric.dimensions]
        thresholded_dimensions = [
            name for name in dimension_names if name in threshold_names
        ]
        if not thresholded_dimensions:
            missing.append(
                f"threshold for at least one dimension of rubric `{rubric.name}`"
            )
            continue
        for dimension_name in thresholded_dimensions:
            if dimension_name not in metric_names:
                missing.append(f"azd metric for rubric dimension `{dimension_name}`")

    if missing:
        raise AzdBackendError(
            "rubric evidence is incomplete; "
            + "; ".join(missing)
            + ". Run `agentops eval init --force` after configuring rubrics and "
            "bind rubric dimension thresholds in agentops.yaml."
        )


def write_raw_artifacts(azd_run: AzdEvalRun, output_dir: Path) -> None:
    """Write native azd payload and command streams for debugging/evidence."""

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "azd_evaluation.json").write_text(
        json.dumps(azd_run.payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    if azd_run.stdout:
        (output_dir / "azd_stdout.log").write_text(azd_run.stdout, encoding="utf-8")
    if azd_run.stderr:
        (output_dir / "azd_stderr.log").write_text(azd_run.stderr, encoding="utf-8")


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    progress: Optional[Callable[[str], None]] = None,
    progress_label: str = "command",
) -> subprocess.CompletedProcess[str]:
    if progress is not None:
        return _run_command_with_progress(
            command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            progress=progress,
            progress_label=progress_label,
        )
    try:
        return subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AzdBackendError(
            "azd was not found on PATH. Install the Azure Developer CLI and the "
            f"`{AZD_EXTENSION_NAME}` extension, then rerun `agentops eval run`."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise AzdBackendError(
            f"{' '.join(command)} timed out after {timeout_seconds:g}s."
        ) from exc


def _run_command_with_progress(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    progress: Callable[[str], None],
    progress_label: str,
) -> subprocess.CompletedProcess[str]:
    started = time.monotonic()
    next_update = started + AZD_PROGRESS_INTERVAL_SECONDS
    progress(
        f"{progress_label}: waiting for azd/Foundry to finish "
        f"(timeout {timeout_seconds / 60:.0f} min)."
    )
    try:
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stdout_file:
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stderr_file:
                process = subprocess.Popen(
                    command,
                    cwd=str(cwd),
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stdout=stdout_file,
                    stderr=stderr_file,
                    stdin=subprocess.DEVNULL,
                )
                while True:
                    returncode = process.poll()
                    now = time.monotonic()
                    if returncode is not None:
                        break
                    elapsed = now - started
                    if elapsed > timeout_seconds:
                        process.kill()
                        process.wait()
                        raise AzdBackendError(
                            f"{' '.join(command)} timed out after {timeout_seconds:g}s."
                        )
                    if now >= next_update:
                        progress(
                            f"{progress_label}: still running "
                            f"({elapsed / 60:.1f} min elapsed)."
                        )
                        next_update = now + AZD_PROGRESS_INTERVAL_SECONDS
                    time.sleep(1.0)

                stdout_file.seek(0)
                stderr_file.seek(0)
                stdout = stdout_file.read()
                stderr = stderr_file.read()
                return subprocess.CompletedProcess(
                    command,
                    returncode,
                    stdout=stdout,
                    stderr=stderr,
                )
    except FileNotFoundError as exc:
        raise AzdBackendError(
            "azd was not found on PATH. Install the Azure Developer CLI and the "
            f"`{AZD_EXTENSION_NAME}` extension, then rerun `agentops eval run`."
        ) from exc


def _parse_json_object(text: str) -> Dict[str, Any]:
    text = text.strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return {}
        value = json.loads(text[start : end + 1])
    return value if isinstance(value, dict) else {}


def _extract_run_id(payload: Dict[str, Any]) -> Optional[str]:
    for key in ("run_id", "runId", "id", "eval_id", "evalId"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    run = payload.get("run")
    if isinstance(run, dict):
        return _extract_run_id(run)
    return None


def _extract_eval_id(payload: Dict[str, Any]) -> Optional[str]:
    for key in ("eval_id", "evalId"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_labeled_id(text: str, label: str) -> Optional[str]:
    match = re.search(rf"(?m)^\s*{re.escape(label)}:\s*(\S+)\s*$", text)
    return match.group(1).strip() if match else None


def _extract_status(payload: Dict[str, Any]) -> str:
    for key in ("status", "state", "result"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def _extract_item_count(payload: Dict[str, Any]) -> int:
    for key in ("items_total", "item_count", "samples", "max_samples", "row_count"):
        value = payload.get(key)
        if isinstance(value, int) and value >= 0:
            return value
    for key in ("items", "rows", "samples"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    return 1


def _extract_numeric_metrics(payload: Dict[str, Any]) -> Dict[str, float]:
    metrics: Dict[str, float] = {}
    criteria = payload.get("per_testing_criteria_results")
    if isinstance(criteria, list):
        for item in criteria:
            if not isinstance(item, dict):
                continue
            name = item.get("testing_criteria")
            passed = item.get("passed")
            failed = item.get("failed")
            errored = item.get("errored")
            if (
                isinstance(name, str)
                and isinstance(passed, int)
                and isinstance(failed, int)
                and isinstance(errored, int)
            ):
                total = passed + failed + errored
                if total:
                    metrics[name] = passed / total
    for key in ("metrics", "aggregate_metrics", "scores", "results", "evaluators", "dimensions"):
        value = payload.get(key)
        _collect_metrics(value, metrics)
    if not metrics:
        _collect_metrics(payload, metrics)
    return metrics


def _collect_metrics(value: Any, metrics: Dict[str, float]) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_metrics(item, metrics)
        return
    if not isinstance(value, dict):
        return

    name = _metric_name_from(value)
    score = _metric_score_from(value)
    if name and score is not None:
        metrics[name] = score

    for key, child in value.items():
        if key in {"metrics", "aggregate_metrics", "scores", "results", "evaluators", "dimensions"}:
            _collect_metrics(child, metrics)
        elif isinstance(child, dict):
            score = _metric_score_from(child)
            if score is not None and _looks_like_metric_name(key):
                metrics[key] = score
        elif isinstance(child, (int, float)) and _looks_like_metric_name(key):
            metrics[key] = float(child)


def _metric_name_from(value: Dict[str, Any]) -> Optional[str]:
    for key in ("name", "metric", "evaluator", "id"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return None


def _metric_score_from(value: Dict[str, Any]) -> Optional[float]:
    for key in ("score", "value", "mean", "average", "avg", "pass_rate"):
        item = value.get(key)
        if isinstance(item, (int, float)):
            return float(item)
    return None


def _looks_like_metric_name(name: str) -> bool:
    lowered = name.lower()
    non_metrics = {
        "status",
        "state",
        "version",
        "duration",
        "duration_seconds",
        "score",
        "value",
        "mean",
        "average",
        "avg",
        "pass_rate",
        "items_total",
        "items_passed",
        "items_passed_all",
        "item_count",
        "row_count",
        "samples",
        "max_samples",
    }
    if lowered in non_metrics:
        return False
    return not lowered.endswith("_id") and not lowered.endswith("id")


def _recipe_dataset_path(recipe: EvalRecipe, recipe_path: Path) -> str:
    ref = recipe.dataset_reference
    if ref and ref.local_uri:
        dataset = Path(ref.local_uri)
        if not dataset.is_absolute():
            dataset = recipe_path.parent / dataset
        return str(dataset)
    if ref and ref.name:
        return ref.name
    return ""


def _format_command_failure(label: str, completed: subprocess.CompletedProcess[str]) -> str:
    stderr = completed.stderr.strip()
    stdout = completed.stdout.strip()
    detail = stderr or stdout or f"exit code {completed.returncode}"
    return f"{label} failed: {detail}"
