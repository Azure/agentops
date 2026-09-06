"""Subprocess adapter for the current ``azd ai eval`` surface.

This module owns every command construction and every external JSON field name
for the ``azure.ai.evaluations`` extension, so an upstream change to that
preview surface stays a localized edit.

Key differences from the legacy ``azd ai agent eval`` adapter in
:mod:`agentops.pipeline.azd_runner`:

* The run object exposes only *counts*, never aggregate numeric metrics, so
  AgentOps computes each metric as the mean of its per-sample scores.
* Per-sample output is retrieved and normalized into ``RunResult.rows``, where
  the legacy adapter emits an aggregate-only result.
* The run is submitted with ``--no-wait`` and polled by AgentOps. The surface's
  blocking mode has an internal wait budget that, when it expires, exits zero
  with an unfinished run and a differently shaped payload.
* AgentOps never passes a failure-gating flag; the release gate stays here.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Dict, Iterable, Optional, Sequence

from agentops.core.agentops_config import AgentOpsConfig, Threshold, classify_agent
from agentops.core.azd_eval import (
    CurrentEval,
    CurrentEvalRecipe,
    MetricBinding,
    bind_threshold_metrics,
    current_recipe_dataset_path,
)
from agentops.core.results import (
    RowMetric,
    RowResult,
    RunResult,
    RunSummary,
    TargetInfo,
)
from agentops.pipeline import thresholds
from agentops.pipeline.azd_runner import (
    AZD_PROGRESS_INTERVAL_SECONDS,
    AzdBackendError,
    _format_command_failure,
    _parse_json_object,
    _persist_failure_logs,
    _run_command,
    probe_extension,
)


#: azd extension providing the current evaluation surface.
CURRENT_EXTENSION_NAME = "azure.ai.evaluations"

#: Minimum azd version the extension manifest requires.
CURRENT_MIN_AZD_VERSION = "1.27.1"

CURRENT_EVAL_TIMEOUT_SECONDS = 1800.0
CURRENT_POLL_INTERVAL_SECONDS = 10.0

#: Statuses that end a run. Both spellings of cancelled occur upstream, and
#: ``error`` is distinct from ``failed``.
TERMINAL_SUCCESS_STATUSES = frozenset({"completed"})
TERMINAL_FAILURE_STATUSES = frozenset({"failed", "error", "canceled", "cancelled"})
TERMINAL_STATUSES = TERMINAL_SUCCESS_STATUSES | TERMINAL_FAILURE_STATUSES

#: Per-sample outcome vocabulary.
SAMPLE_OUTCOMES = frozenset({"passed", "failed", "errored", "skipped"})

_INPUT_KEYS = ("input", "query", "question", "prompt", "user_input")
_EXPECTED_KEYS = (
    "expected",
    "expected_response",
    "expected_output",
    "ground_truth",
    "reference",
)
_RESPONSE_KEYS = (
    "response",
    "output",
    "answer",
    "completion",
    "output_text",
    "sample.output_text",
)


class CurrentSurfaceUnavailable(AzdBackendError):
    """Raised when azd or the evaluations extension cannot be used."""


@dataclass(frozen=True)
class RunCounts:
    """Sample tallies reported by the run object."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    skipped: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "errored": self.errored,
            "skipped": self.skipped,
        }


@dataclass(frozen=True)
class SampleScore:
    """One metric score for one sample.

    ``score`` is ``None`` when absent or undecodable; it is never coerced to
    zero. ``passed`` is three-valued: ``True`` pass, ``False`` judged failure,
    ``None`` not judged.
    """

    metric: str
    score: Optional[float] = None
    passed: Optional[bool] = None
    label: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class SampleResult:
    """One normalized per-sample output item."""

    index: int
    identifier: Optional[str]
    outcome: str
    scores: tuple[SampleScore, ...]
    input_text: str = ""
    expected_text: Optional[str] = None
    response_text: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CurrentEvalRun:
    """Everything captured from one delegated evaluation."""

    recipe_path: Path
    run_id: str
    status: str
    run_payload: Dict[str, Any]
    output_items: tuple[Dict[str, Any], ...]
    eval_id: Optional[str] = None
    eval_name: Optional[str] = None
    report_url: Optional[str] = None
    error_message: Optional[str] = None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


def _command(*args: str) -> list[str]:
    """Build a non-interactive azd invocation requesting structured output."""

    return ["azd", "--no-prompt", *args, "-o", "json"]


def _require_success(
    label: str,
    completed: Any,
    command: list[str],
    debug_dir: Optional[Path],
    step: str,
) -> None:
    if completed.returncode == 0:
        return
    debug_paths = _persist_failure_logs(
        debug_dir, step=step, completed=completed, command=command
    )
    raise AzdBackendError(
        _format_command_failure(label, completed, command=command, debug_paths=debug_paths)
    )


def _unavailable_error() -> CurrentSurfaceUnavailable:
    return CurrentSurfaceUnavailable(
        "The current azd evaluation surface is not available. It needs the Azure "
        f"Developer CLI {CURRENT_MIN_AZD_VERSION} or newer plus the "
        f"`{CURRENT_EXTENSION_NAME}` extension:\n"
        f"  azd extension install {CURRENT_EXTENSION_NAME}\n"
        f"`{CURRENT_EXTENSION_NAME}` is in preview and is not yet published to the "
        "default azd extension registry, so the install may not resolve until it "
        "ships. Until then, use a legacy `eval.yaml` recipe, or set "
        "`execution: local` to run the AgentOps engine instead."
    )


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def run_current_eval(
    recipe_path: Path,
    evaluation: CurrentEval,
    *,
    workspace: Path,
    progress: Optional[Callable[[str], None]] = None,
    timeout_seconds: float = CURRENT_EVAL_TIMEOUT_SECONDS,
    poll_interval_seconds: float = CURRENT_POLL_INTERVAL_SECONDS,
    debug_dir: Optional[Path] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> CurrentEvalRun:
    """Reconcile, start, poll, and read back one current-surface evaluation."""

    notify = progress or (lambda _message: None)
    if not probe_extension(CURRENT_EXTENSION_NAME, cwd=workspace):
        raise _unavailable_error()

    recipe_dir = str(recipe_path.parent)
    started = time.perf_counter()
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []

    def _record(completed: Any) -> None:
        if completed.stdout:
            stdout_parts.append(completed.stdout)
        if completed.stderr:
            stderr_parts.append(completed.stderr)

    # 1. Reconcile the declared datasets, evaluators, and evaluation.
    notify("azd ai eval create: reconciling the evaluation definition.")
    create_command = _command("ai", "eval", "create", "--path", recipe_dir)
    created = _run_command(create_command, cwd=workspace, timeout_seconds=timeout_seconds)
    _record(created)
    _require_success("azd ai eval create", created, create_command, debug_dir, "azd_eval_create")

    # 2. Submit without waiting, so identifiers arrive in one predictable shape.
    notify(f"azd ai eval run start: submitting '{evaluation.name}'.")
    start_command = _command(
        "ai",
        "eval",
        "run",
        "start",
        "--eval",
        evaluation.name,
        "--path",
        recipe_dir,
        "--no-wait",
    )
    started_run = _run_command(start_command, cwd=workspace, timeout_seconds=timeout_seconds)
    _record(started_run)
    _require_success(
        "azd ai eval run start", started_run, start_command, debug_dir, "azd_eval_run_start"
    )

    handoff = _parse_json_object(started_run.stdout)
    run_id = _first_str(handoff, ("run_id", "runId", "id"))
    eval_id = _first_str(handoff, ("eval_id", "evalId"))
    eval_name = _first_str(handoff, ("eval_name", "evalName")) or evaluation.name
    if not run_id:
        raise AzdBackendError(
            "azd ai eval run start did not return a run identifier, so AgentOps "
            "cannot retrieve results. Inspect the run with `azd ai eval run list`."
        )

    # 3. Poll to a terminal state under the AgentOps timeout.
    def _partial_run(status: str, payload: Dict[str, Any]) -> CurrentEvalRun:
        return CurrentEvalRun(
            recipe_path=recipe_path,
            run_id=run_id,
            status=status,
            run_payload=payload,
            output_items=(),
            eval_id=eval_id or _first_str(payload, ("eval_id", "evalId")),
            eval_name=eval_name,
            report_url=_first_str(payload, ("report_url", "reportUrl"))
            or _first_str(payload, ("portal_url", "portalUrl")),
            error_message=_run_error_message(payload),
            stdout="\n".join(stdout_parts),
            stderr="\n".join(stderr_parts),
            duration_seconds=time.perf_counter() - started,
        )

    poll_state: Dict[str, Any] = {"status": "", "payload": {}}
    try:
        run_payload, status = _poll_until_terminal(
            run_id,
            recipe_dir=recipe_dir,
            workspace=workspace,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            notify=notify,
            record=_record,
            debug_dir=debug_dir,
            sleep=sleep,
            eval_id=eval_id,
            state=poll_state,
        )
    except AzdBackendError:
        # The evaluation exists in Foundry even though AgentOps could not finish
        # reading it. Persist what we have so the failure stays diagnosable
        # without re-running the evaluation.
        if debug_dir is not None:
            write_raw_artifacts(
                _partial_run(poll_state["status"], poll_state["payload"]), debug_dir
            )
        raise

    eval_id = eval_id or _first_str(run_payload, ("eval_id", "evalId"))
    report_url = _first_str(run_payload, ("report_url", "reportUrl")) or _first_str(
        run_payload, ("portal_url", "portalUrl")
    )
    error_message = _run_error_message(run_payload)

    # 4. Read every per-sample item. `--all` plus `--output-file` defeats the
    #    default page bound; the bare-array JSON form drops paging metadata.
    output_items: tuple[Dict[str, Any], ...] = ()
    try:
        with TemporaryDirectory() as temp_dir:
            items_path = Path(temp_dir) / "azd-eval-output-items.json"
            list_command = _command(
                "ai",
                "eval",
                "run",
                "output",
                "list",
                run_id,
                "--all",
                "--output-file",
                str(items_path),
                "--path",
                recipe_dir,
            )
            listed = _run_command(list_command, cwd=workspace, timeout_seconds=timeout_seconds)
            _record(listed)
            _require_success(
                "azd ai eval run output list",
                listed,
                list_command,
                debug_dir,
                "azd_eval_run_output_list",
            )
            output_items = _load_output_items(items_path, listed.stdout)
    except AzdBackendError:
        if debug_dir is not None:
            write_raw_artifacts(_partial_run(status, run_payload), debug_dir)
        raise


    return CurrentEvalRun(
        recipe_path=recipe_path,
        run_id=run_id,
        status=status,
        run_payload=run_payload,
        output_items=output_items,
        eval_id=eval_id,
        eval_name=eval_name,
        report_url=report_url,
        error_message=error_message,
        stdout="\n".join(stdout_parts),
        stderr="\n".join(stderr_parts),
        duration_seconds=time.perf_counter() - started,
    )


def _poll_until_terminal(
    run_id: str,
    *,
    recipe_dir: str,
    workspace: Path,
    timeout_seconds: float,
    poll_interval_seconds: float,
    notify: Callable[[str], None],
    record: Callable[[Any], None],
    debug_dir: Optional[Path],
    sleep: Callable[[float], None],
    eval_id: Optional[str],
    state: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, Any], str]:
    """Poll ``run show`` until the run ends, or the AgentOps timeout expires.

    ``state`` is updated in place with the last observed status and payload so
    the caller can persist diagnostics if this raises.
    """

    show_command = _command(
        "ai", "eval", "run", "show", run_id, "--path", recipe_dir
    )
    deadline = time.monotonic() + timeout_seconds
    next_heartbeat = time.monotonic() + AZD_PROGRESS_INTERVAL_SECONDS
    status = ""
    payload: Dict[str, Any] = {}

    while True:
        completed = _run_command(
            show_command, cwd=workspace, timeout_seconds=timeout_seconds
        )
        record(completed)
        _require_success(
            "azd ai eval run show", completed, show_command, debug_dir, "azd_eval_run_show"
        )
        payload = _parse_json_object(completed.stdout)
        status = _status_of(payload)
        if state is not None:
            state["status"] = status
            state["payload"] = payload
        if status in TERMINAL_STATUSES:
            break

        now = time.monotonic()
        if now >= deadline:
            raise AzdBackendError(
                "azd evaluation run did not reach a terminal state within "
                f"{timeout_seconds:g}s. The run is still recorded in Foundry:\n"
                f"  eval id: {eval_id or '(unknown)'}\n"
                f"  run id:  {run_id}\n"
                f"  last observed status: {status or '(none reported)'}\n"
                f"Reattach with `azd ai eval run show {run_id} --wait`."
            )
        if now >= next_heartbeat:
            elapsed = timeout_seconds - (deadline - now)
            notify(
                f"azd ai eval run: still running ({elapsed / 60:.1f} min elapsed, "
                f"status {status or 'pending'})."
            )
            next_heartbeat = now + AZD_PROGRESS_INTERVAL_SECONDS
        sleep(poll_interval_seconds)

    if status in TERMINAL_FAILURE_STATUSES:
        detail = _run_error_message(payload)
        raise AzdBackendError(
            f"azd evaluation run ended with status '{status}'.\n"
            f"  eval id: {eval_id or '(unknown)'}\n"
            f"  run id:  {run_id}\n"
            + (f"  error:   {detail}\n" if detail else "")
            + "AgentOps did not evaluate thresholds because the run did not complete."
        )
    return payload, status


def _load_output_items(items_path: Path, fallback_stdout: str) -> tuple[Dict[str, Any], ...]:
    """Read the per-sample items azd wrote, falling back to stdout."""

    raw: Any = None
    if items_path.exists():
        try:
            raw = json.loads(items_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = None
    if raw is None:
        text = (fallback_stdout or "").strip()
        if text:
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                raw = None
    if isinstance(raw, dict):
        for key in ("items", "data", "output_items"):
            nested = raw.get(key)
            if isinstance(nested, list):
                raw = nested
                break
    if not isinstance(raw, list):
        return ()
    return tuple(entry for entry in raw if isinstance(entry, dict))


# ---------------------------------------------------------------------------
# Payload readers
# ---------------------------------------------------------------------------


def _first_str(payload: Dict[str, Any], keys: Sequence[str]) -> Optional[str]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _status_of(payload: Dict[str, Any]) -> str:
    value = payload.get("status")
    if isinstance(value, str):
        return value.strip().lower()
    return ""


def _run_error_message(payload: Dict[str, Any]) -> Optional[str]:
    """Return a run-level error message, if one is actually present.

    The error object is always present with null members on success, so its
    existence says nothing. Only a non-empty message is a failure signal.
    """

    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    message = error.get("message")
    if isinstance(message, str) and message.strip():
        code = error.get("code")
        if isinstance(code, str) and code.strip():
            return f"{code.strip()}: {message.strip()}"
        return message.strip()
    return None


def _coerce_score(value: Any) -> Optional[float]:
    """Decode a score tolerantly; return ``None`` rather than a false zero."""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None
    return None


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _first_text(payload: Dict[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        if key in payload:
            text = _coerce_text(payload.get(key))
            if text:
                return text
    return ""


def _parse_scores(item: Dict[str, Any]) -> tuple[SampleScore, ...]:
    raw = item.get("results")
    if not isinstance(raw, list):
        return ()
    scores: list[SampleScore] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        metric = _first_str(entry, ("metric", "name"))
        if not metric:
            continue
        passed = entry.get("passed")
        scores.append(
            SampleScore(
                metric=metric,
                score=_coerce_score(entry.get("score")),
                passed=passed if isinstance(passed, bool) else None,
                label=_first_str(entry, ("label",)),
                reason=_first_str(entry, ("reason",)),
            )
        )
    return tuple(scores)


def _derive_outcome(scores: Sequence[SampleScore]) -> str:
    """Classify a sample when the payload did not state an outcome.

    A sample with no results is a failure, and any verdict that is not an
    explicit pass counts as a failure.
    """

    if not scores:
        return "failed"
    if all(score.passed is True for score in scores):
        return "passed"
    return "failed"


def parse_output_item(index: int, item: Dict[str, Any]) -> SampleResult:
    """Normalize one raw per-sample output item."""

    scores = _parse_scores(item)
    status = str(item.get("status") or "").strip().lower()
    outcome = status if status in SAMPLE_OUTCOMES else _derive_outcome(scores)

    data_item = item.get("datasource_item")
    if not isinstance(data_item, dict):
        data_item = {}

    return SampleResult(
        index=index,
        identifier=_first_str(item, ("id",)),
        outcome=outcome,
        scores=scores,
        input_text=_first_text(data_item, _INPUT_KEYS),
        expected_text=_first_text(data_item, _EXPECTED_KEYS) or None,
        response_text=_first_text(data_item, _RESPONSE_KEYS),
        raw=item,
    )


def _run_counts(payload: Dict[str, Any], samples: Sequence[SampleResult]) -> RunCounts:
    raw = payload.get("result_counts")
    if isinstance(raw, dict):
        def _count(key: str) -> int:
            value = raw.get(key)
            if isinstance(value, bool):
                return 0
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str):
                try:
                    return int(value.strip())
                except (TypeError, ValueError):
                    return 0
            return 0

        counts = RunCounts(
            total=_count("total"),
            passed=_count("passed"),
            failed=_count("failed"),
            errored=_count("errored"),
            skipped=_count("skipped"),
        )
        if counts.total or counts.passed or counts.failed or counts.errored or counts.skipped:
            return counts

    return RunCounts(
        total=len(samples),
        passed=sum(1 for sample in samples if sample.outcome == "passed"),
        failed=sum(1 for sample in samples if sample.outcome == "failed"),
        errored=sum(1 for sample in samples if sample.outcome == "errored"),
        skipped=sum(1 for sample in samples if sample.outcome == "skipped"),
    )


def aggregate_scores(samples: Iterable[SampleResult]) -> Dict[str, float]:
    """Compute each metric as the mean of its non-absent per-sample scores.

    The run object carries only counts, so this is the only source of numeric
    metrics for this surface. A metric with no decodable score anywhere is
    omitted, which makes any threshold bound to it fail closed.
    """

    totals: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    for sample in samples:
        for score in sample.scores:
            if score.score is None:
                continue
            totals[score.metric] = totals.get(score.metric, 0.0) + score.score
            counts[score.metric] = counts.get(score.metric, 0) + 1
    return {metric: totals[metric] / counts[metric] for metric in sorted(totals)}


# ---------------------------------------------------------------------------
# Threshold binding
# ---------------------------------------------------------------------------


def preflight_bind_thresholds(
    threshold_names: Iterable[str],
    declared_metrics: Iterable[str],
) -> MetricBinding:
    """Bind thresholds against what the recipe declares, before running anything.

    A threshold naming a metric no evaluator in the recipe can produce is a
    configuration error, not a gate outcome, and catching it here means a typo
    never bills a cloud run.
    """

    names = list(threshold_names)
    metrics = sorted({metric for metric in declared_metrics if metric})
    binding = bind_threshold_metrics(names, metrics)

    if binding.unmatched:
        unmatched = ", ".join(sorted(binding.unmatched))
        available = ", ".join(metrics) if metrics else "(none declared)"
        raise AzdBackendError(
            f"threshold metric(s) not declared by the azd eval recipe: {unmatched}.\n"
            f"Metrics this recipe can produce: {available}.\n"
            "Fix the threshold names in agentops.yaml, or add the evaluator to the "
            "recipe. AgentOps checked this before starting the evaluation, so no "
            "cloud run was consumed."
        )
    if binding.ambiguous:
        details = "; ".join(
            f"{name} -> {', '.join(matches)}"
            for name, matches in sorted(binding.ambiguous.items())
        )
        raise AzdBackendError(
            f"ambiguous threshold metric binding: {details}.\n"
            "Use the fully qualified metric name in agentops.yaml so the gate is "
            "unambiguous. No cloud run was consumed."
        )
    return binding


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_to_results(
    azd_run: CurrentEvalRun,
    *,
    config: AgentOpsConfig,
    recipe: CurrentEvalRecipe,
    evaluation: CurrentEval,
    metric_binding: MetricBinding,
    started_at: datetime,
    resolution: Optional[Any] = None,
) -> RunResult:
    """Normalize a current-surface run to the stable ``results.json`` schema."""

    target = classify_agent(config.agent, config.protocol)
    samples = [
        parse_output_item(index, item) for index, item in enumerate(azd_run.output_items)
    ]
    aggregate_metrics = aggregate_scores(samples)
    counts = _run_counts(azd_run.run_payload, samples)

    retrieval_warnings: list[str] = []
    retrieved_passed = sum(1 for sample in samples if sample.outcome == "passed")
    if counts.total and len(samples) != counts.total:
        retrieval_warnings.append(
            f"retrieved {len(samples)} of {counts.total} reported samples; "
            "aggregate metrics are computed from the retrieved subset"
        )
        items_passed = min(counts.passed, retrieved_passed)
    else:
        items_passed = counts.passed

    items_total = counts.total or len(samples)

    # Bound thresholds whose metric the run actually produced. Anything else is
    # left out so `thresholds.evaluate` records it as missing and fails closed.
    threshold_metrics = {
        threshold_name: aggregate_metrics[actual_name]
        for threshold_name, actual_name in metric_binding.bound.items()
        if actual_name in aggregate_metrics
    }
    missing_metrics = sorted(
        actual_name
        for actual_name in metric_binding.bound.values()
        if actual_name not in aggregate_metrics
    )

    threshold_rules = [
        Threshold.from_expression(metric, expression)
        for metric, expression in config.thresholds.items()
    ]
    threshold_results = thresholds.evaluate(threshold_rules, threshold_metrics)
    thresholds_total = len(threshold_results)
    thresholds_passed = sum(1 for item in threshold_results if item.passed)
    threshold_pass_rate = thresholds_passed / thresholds_total if thresholds_total else 1.0

    status_ok = azd_run.status in TERMINAL_SUCCESS_STATUSES and not azd_run.error_message
    # A run with no samples or no decodable metrics produced no evidence, so it
    # cannot pass regardless of how few thresholds were configured.
    evidence_ok = items_total > 0 and bool(aggregate_metrics)
    overall_passed = status_ok and evidence_ok and threshold_pass_rate == 1.0

    rows = [_to_row(sample, azd_run.error_message) for sample in samples]
    finished_at = datetime.now(timezone.utc)

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
        dataset_path=current_recipe_dataset_path(recipe, evaluation, azd_run.recipe_path),
        evaluators=[reference.metric_name for reference in evaluation.evaluators],
        rows=rows,
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
            "result_granularity": "row",
            "azd_evaluation": {
                "recipe_path": str(azd_run.recipe_path),
                "surface": "current",
                "extension": CURRENT_EXTENSION_NAME,
                "eval_name": azd_run.eval_name,
                "run_id": azd_run.run_id,
                "eval_id": azd_run.eval_id,
                "status": azd_run.status,
                "report_url": azd_run.report_url,
                "error_message": azd_run.error_message,
                "dataset": evaluation.dataset,
                "metric_binding": dict(metric_binding.bound),
                "unused_metrics": list(metric_binding.unused_metrics),
                "missing_metrics": missing_metrics,
                "result_counts": counts.as_dict(),
                "retrieval_warnings": retrieval_warnings,
                "skipped_recipes": [
                    str(path) for path in getattr(resolution, "skipped", ()) or ()
                ],
            },
        },
    )


def _to_row(sample: SampleResult, run_error: Optional[str]) -> RowResult:
    metrics = [
        RowMetric(name=score.metric, value=score.score, reason=score.reason)
        for score in sample.scores
    ]
    error: Optional[str] = None
    if sample.outcome == "errored":
        error = run_error or "azd reported this sample as errored"
    elif sample.outcome == "skipped":
        error = "azd skipped this sample"
    elif not sample.scores:
        error = "azd returned no evaluator results for this sample"
    return RowResult(
        row_index=sample.index,
        input=sample.input_text,
        expected=sample.expected_text,
        response=sample.response_text,
        metrics=metrics,
        error=error,
    )


# ---------------------------------------------------------------------------
# Raw artifacts
# ---------------------------------------------------------------------------


def write_raw_artifacts(azd_run: CurrentEvalRun, output_dir: Path) -> None:
    """Persist the native azd payloads and command streams for diagnostics.

    Per-sample items carry prompts and model responses. They stay inside the
    run's results directory, which the generated workspace ``.gitignore``
    already excludes from version control.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "azd_evaluation.json").write_text(
        json.dumps(azd_run.run_payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    (output_dir / "azd_eval_output_items.json").write_text(
        json.dumps(list(azd_run.output_items), indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    if azd_run.stdout:
        (output_dir / "azd_stdout.log").write_text(azd_run.stdout, encoding="utf-8")
    if azd_run.stderr:
        (output_dir / "azd_stderr.log").write_text(azd_run.stderr, encoding="utf-8")
