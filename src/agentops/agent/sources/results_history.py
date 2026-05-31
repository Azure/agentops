"""AgentOps evaluation history source.

Reads ``.agentops/results/*/results.json`` and produces a normalized
list of run summaries ordered oldest-to-newest. When local history is
missing or too short, it can also fail-open to Foundry cloud evaluation
runs so Doctor still has signal in freshly cloned workspaces.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from agentops.agent.config import FoundryControlSourceConfig, ResultsHistorySourceConfig

log = logging.getLogger(__name__)


@dataclass
class RunSummary:
    """One historical AgentOps run."""

    run_id: str
    timestamp: Optional[datetime]
    metrics: Dict[str, float]
    run_pass: Optional[bool]
    items_total: int
    items_passed_all: int
    raw_path: Path
    item_evaluations: List[Dict[str, Any]] = field(default_factory=list)
    source: str = "local"
    portal_url: Optional[str] = None


@dataclass
class ResultsHistory:
    """Aggregated results-history payload."""

    runs: List[RunSummary]
    diagnostics: Dict[str, Any] = field(default_factory=dict)


def _coerce_timestamp(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(raw, str):
        candidate = raw.replace("Z", "+00:00")
        try:
            ts = datetime.fromisoformat(candidate)
        except ValueError:
            return None
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return None


def _summarize(path: Path) -> Optional[RunSummary]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Skipping unreadable results.json at %s: %s", path, exc)
        return None

    if not isinstance(data, dict):
        return None

    metrics_raw = (
        data.get("aggregate_metrics")
        or data.get("metrics")
        or data.get("run_metrics")
        or {}
    )
    metrics: Dict[str, float] = {}
    if isinstance(metrics_raw, dict):
        for key, value in metrics_raw.items():
            try:
                metrics[str(key)] = float(value)
            except (TypeError, ValueError):
                continue

    summary = data.get("summary") or {}
    run_pass: Optional[bool] = None
    if isinstance(summary, dict) and "overall_passed" in summary:
        run_pass = bool(summary["overall_passed"])
    elif isinstance(summary, dict) and "run_pass" in summary:
        run_pass = bool(summary["run_pass"])
    elif isinstance(metrics_raw, dict) and "run_pass" in metrics_raw:
        try:
            run_pass = bool(float(metrics_raw["run_pass"]))
        except (TypeError, ValueError):
            run_pass = None

    items_total = 0
    items_passed_all = 0
    if isinstance(summary, dict):
        items_total = int(summary.get("items_total", 0) or 0)
        items_passed_all = int(summary.get("items_passed_all", 0) or 0)

    item_evaluations = data.get("item_evaluations") or []
    if not isinstance(item_evaluations, list):
        item_evaluations = []

    timestamp_raw = (
        data.get("timestamp")
        or data.get("finished_at")
        or data.get("started_at")
        or data.get("created_at")
        or (summary.get("timestamp") if isinstance(summary, dict) else None)
    )
    timestamp = _coerce_timestamp(timestamp_raw)
    if timestamp is None:
        # Fall back to file mtime so ordering still works.
        try:
            timestamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            timestamp = None

    run_id = str(data.get("run_id") or path.parent.name)

    return RunSummary(
        run_id=run_id,
        timestamp=timestamp,
        metrics=metrics,
        run_pass=run_pass,
        items_total=items_total,
        items_passed_all=items_passed_all,
        raw_path=path,
        item_evaluations=item_evaluations,
    )


def collect_results_history(
    workspace: Path,
    config: ResultsHistorySourceConfig,
    *,
    foundry_config: Optional[FoundryControlSourceConfig] = None,
) -> ResultsHistory:
    """Walk local and, when needed, cloud results to build an ordered history."""
    diagnostics: Dict[str, Any] = {
        "enabled": config.enabled,
        "path": str(config.path),
    }
    if not config.enabled:
        diagnostics["status"] = "disabled"
        return ResultsHistory(runs=[], diagnostics=diagnostics)

    summaries = _collect_local_runs(workspace, config, diagnostics)
    diagnostics["local_runs_loaded"] = len(summaries)

    if len(summaries) < config.lookback_runs and foundry_config is not None:
        cloud_runs, cloud_diag = _collect_foundry_eval_runs(
            foundry_config,
            limit=max(config.lookback_runs, 10),
        )
        diagnostics["cloud"] = cloud_diag
        if cloud_runs:
            summaries = _merge_runs(summaries, cloud_runs)
            diagnostics["cloud_runs_loaded"] = len(cloud_runs)

    summaries.sort(
        key=lambda s: s.timestamp or datetime.fromtimestamp(0, tz=timezone.utc)
    )
    if config.lookback_runs > 0:
        summaries = summaries[-config.lookback_runs :]

    diagnostics["status"] = "ok" if summaries else diagnostics.get("status", "missing")
    diagnostics["runs_loaded"] = len(summaries)
    diagnostics["sources_used"] = sorted({run.source for run in summaries})
    return ResultsHistory(runs=summaries, diagnostics=diagnostics)


def _collect_local_runs(
    workspace: Path,
    config: ResultsHistorySourceConfig,
    diagnostics: Dict[str, Any],
) -> List[RunSummary]:
    base = (workspace / config.path).resolve()
    diagnostics["resolved_path"] = str(base)

    if not base.exists():
        diagnostics["local_status"] = "missing"
        diagnostics["local_reason"] = f"results directory not found at {base}"
        return []

    candidates: List[Path] = []
    latest_target: Optional[Path] = None
    for child in base.iterdir():
        if not child.is_dir():
            continue
        target = child / "results.json"
        if not target.is_file():
            continue
        if child.name == "latest":
            latest_target = target
            continue
        candidates.append(target)
    # CI workflows write directly to `.agentops/results/latest/` with no
    # timestamped sibling. Include the `latest/` entry only when it is the
    # sole local result so dev-mode runs (which already have a timestamped
    # sibling) are not double-counted.
    if not candidates and latest_target is not None:
        candidates.append(latest_target)

    summaries: List[RunSummary] = []
    for path in candidates:
        summary = _summarize(path)
        if summary is not None:
            summaries.append(summary)

    summaries.sort(
        key=lambda s: s.timestamp or datetime.fromtimestamp(0, tz=timezone.utc)
    )

    diagnostics["local_status"] = "ok"
    return summaries


def _merge_runs(local_runs: List[RunSummary], cloud_runs: List[RunSummary]) -> List[RunSummary]:
    merged: Dict[str, RunSummary] = {}
    for run in cloud_runs:
        merged[run.run_id] = run
    for run in local_runs:
        merged[run.run_id] = run
    return list(merged.values())


def _collect_foundry_eval_runs(
    config: FoundryControlSourceConfig,
    *,
    limit: int,
) -> tuple[List[RunSummary], Dict[str, Any]]:
    diagnostics: Dict[str, Any] = {"enabled": config.enabled}
    if not config.enabled:
        diagnostics["status"] = "disabled"
        return [], diagnostics

    endpoint = _resolve_foundry_endpoint(config)
    if not endpoint:
        diagnostics["status"] = "skipped"
        diagnostics["reason"] = (
            "no Foundry project endpoint configured; set "
            f"`{config.project_endpoint_env}` or "
            "`sources.foundry_control.project_endpoint`"
        )
        return [], diagnostics
    diagnostics["endpoint"] = endpoint

    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        diagnostics["status"] = "skipped"
        diagnostics["reason"] = (
            "azure-ai-projects / azure-identity not installed "
            "(install agentops-accelerator[foundry])"
        )
        log.info("Foundry cloud eval history unavailable: %s", exc)
        return [], diagnostics

    try:
        credential = DefaultAzureCredential(
            exclude_developer_cli_credential=True,
            process_timeout=30,
        )
        project_client = AIProjectClient(endpoint=endpoint, credential=credential)
        openai_client = project_client.get_openai_client()
    except Exception as exc:  # pragma: no cover - SDK/auth shape varies
        diagnostics["status"] = "skipped"
        diagnostics["reason"] = f"could not create Foundry OpenAI client: {exc}"
        return [], diagnostics

    try:
        runs = _list_cloud_eval_runs(openai_client, limit=limit)
    except Exception as exc:  # pragma: no cover - SDK shape varies
        diagnostics["status"] = "skipped"
        diagnostics["reason"] = f"could not list cloud evaluation runs: {exc}"
        return [], diagnostics

    diagnostics["status"] = "ok"
    diagnostics["runs_loaded"] = len(runs)
    return runs, diagnostics


def _resolve_foundry_endpoint(config: FoundryControlSourceConfig) -> Optional[str]:
    if config.project_endpoint:
        return config.project_endpoint
    if config.project_endpoint_env:
        import os

        return os.environ.get(config.project_endpoint_env)
    return None


def _list_cloud_eval_runs(openai_client: Any, *, limit: int) -> List[RunSummary]:
    evals_api = getattr(openai_client, "evals", None)
    if evals_api is None:
        return []
    evals_page = _call_list(getattr(evals_api, "list", None), limit=limit)
    summaries: List[RunSummary] = []
    for eval_obj in _iter_page(evals_page):
        eval_id = str(getattr(eval_obj, "id", "") or _get_dict(eval_obj, "id") or "")
        if not eval_id:
            continue
        runs_api = getattr(evals_api, "runs", None)
        list_runs = getattr(runs_api, "list", None) if runs_api is not None else None
        runs_page = _call_list(list_runs, eval_id=eval_id, limit=limit)
        for run_obj in _iter_page(runs_page):
            summary = _summarize_cloud_run(openai_client, eval_id, run_obj)
            if summary is not None:
                summaries.append(summary)
    summaries.sort(
        key=lambda s: s.timestamp or datetime.fromtimestamp(0, tz=timezone.utc)
    )
    return summaries[-limit:] if limit > 0 else summaries


def _call_list(fn: Any, **kwargs: Any) -> Any:
    if not callable(fn):
        return []
    try:
        return fn(**kwargs)
    except TypeError:
        safe_kwargs = {k: v for k, v in kwargs.items() if k != "limit"}
        return fn(**safe_kwargs)


def _iter_page(page: Any) -> List[Any]:
    if page is None:
        return []
    data = getattr(page, "data", None)
    if data is not None:
        return list(data)
    if isinstance(page, list):
        return page
    try:
        return list(page)
    except TypeError:
        return []


def _summarize_cloud_run(
    openai_client: Any,
    eval_id: str,
    run_obj: Any,
) -> Optional[RunSummary]:
    run_id = str(getattr(run_obj, "id", "") or _get_dict(run_obj, "id") or "")
    if not run_id:
        return None
    timestamp = _coerce_timestamp(
        getattr(run_obj, "created_at", None)
        or _get_dict(run_obj, "created_at")
        or getattr(run_obj, "createdAt", None)
        or _get_dict(run_obj, "createdAt")
    )
    output_items = _list_cloud_output_items(openai_client, eval_id=eval_id, run_id=run_id)
    metrics = _metrics_from_cloud_run(run_obj, output_items)
    items_total = len(output_items)
    items_passed = _count_cloud_passed_items(output_items)
    status = str(getattr(run_obj, "status", "") or _get_dict(run_obj, "status") or "")
    run_pass = None
    if status:
        run_pass = status.lower() in {"succeeded", "completed", "passed"}
    return RunSummary(
        run_id=run_id,
        timestamp=timestamp,
        metrics=metrics,
        run_pass=run_pass,
        items_total=items_total,
        items_passed_all=items_passed,
        raw_path=Path("foundry") / eval_id / run_id,
        item_evaluations=output_items,
        source="foundry_cloud",
        portal_url=_extract_report_url(run_obj),
    )


def _list_cloud_output_items(
    openai_client: Any,
    *,
    eval_id: str,
    run_id: str,
) -> List[Dict[str, Any]]:
    try:
        output_items_api = openai_client.evals.runs.output_items
        page = output_items_api.list(eval_id=eval_id, run_id=run_id)
    except Exception:
        return []
    items: List[Dict[str, Any]] = []
    for raw in _iter_page(page):
        item = _coerce_output_item_to_dict(raw)
        if item is not None:
            items.append(item)
    return items


def _coerce_output_item_to_dict(raw: Any) -> Optional[Dict[str, Any]]:
    if isinstance(raw, dict):
        return raw
    for method in ("model_dump", "to_dict", "dict"):
        fn = getattr(raw, method, None)
        if callable(fn):
            try:
                value = fn()
                if isinstance(value, dict):
                    return value
            except Exception:
                continue
    keys = ("id", "status", "datasource_item", "sample", "results")
    if any(hasattr(raw, key) for key in keys):
        return {key: getattr(raw, key, None) for key in keys}
    return None


def _metrics_from_cloud_run(run_obj: Any, output_items: List[Dict[str, Any]]) -> Dict[str, float]:
    metrics = _extract_metric_dict(run_obj)
    if metrics:
        return metrics
    series: Dict[str, List[float]] = {}
    for item in output_items:
        for name, value in _extract_item_scores(item).items():
            series.setdefault(name, []).append(value)
    return {
        name: sum(values) / len(values)
        for name, values in series.items()
        if values
    }


def _extract_metric_dict(raw: Any) -> Dict[str, float]:
    metrics: Dict[str, float] = {}
    for container_name in ("metrics", "run_metrics", "results", "scores"):
        container = getattr(raw, container_name, None) or _get_dict(raw, container_name)
        if not isinstance(container, dict):
            continue
        for key, value in container.items():
            score = _coerce_score(value)
            if score is not None:
                metrics[str(key)] = score
    return metrics


def _extract_item_scores(item: Dict[str, Any]) -> Dict[str, float]:
    scores: Dict[str, float] = {}
    results = item.get("results")
    if isinstance(results, dict):
        iterator: Iterable[tuple[Any, Any]] = results.items()
    elif isinstance(results, list):
        iterator = ((_get_dict(entry, "name") or _get_dict(entry, "key") or "", entry) for entry in results)
    else:
        iterator = ()
    for raw_name, raw_value in iterator:
        name = str(raw_name or "").strip()
        if not name:
            continue
        score = _coerce_score(raw_value)
        if score is not None:
            scores[name] = score
    return scores


def _coerce_score(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("score", "value", "result", "grade"):
            if key in value:
                score = _coerce_score(value[key])
                if score is not None:
                    return score
    for attr in ("score", "value", "result", "grade"):
        if hasattr(value, attr):
            score = _coerce_score(getattr(value, attr))
            if score is not None:
                return score
    return None


def _count_cloud_passed_items(output_items: List[Dict[str, Any]]) -> int:
    passed = 0
    for item in output_items:
        status = str(item.get("status") or "").lower()
        if status in {"pass", "passed", "succeeded", "completed"}:
            passed += 1
    return passed


def _extract_report_url(run: Any) -> Optional[str]:
    for attr in ("report_url", "reportUrl"):
        value = getattr(run, attr, None) or _get_dict(run, attr)
        if isinstance(value, str) and value:
            return value
    metadata = getattr(run, "metadata", None) or _get_dict(run, "metadata")
    if isinstance(metadata, dict):
        for key in ("report_url", "reportUrl"):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _get_dict(raw: Any, key: str) -> Any:
    return raw.get(key) if isinstance(raw, dict) else None
