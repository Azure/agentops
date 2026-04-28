"""AgentOps results-history source.

Reads ``.agentops/results/*/results.json`` and produces a normalized
list of run summaries ordered oldest-to-newest. This source is offline
and always available — it is the foundation of the regression and
safety checks.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentops.agent.config import ResultsHistorySourceConfig

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

    metrics_raw = data.get("metrics") or data.get("run_metrics") or {}
    metrics: Dict[str, float] = {}
    if isinstance(metrics_raw, dict):
        for key, value in metrics_raw.items():
            try:
                metrics[str(key)] = float(value)
            except (TypeError, ValueError):
                continue

    summary = data.get("summary") or {}
    run_pass: Optional[bool] = None
    if isinstance(summary, dict) and "run_pass" in summary:
        run_pass = bool(summary["run_pass"])
    elif "run_pass" in metrics_raw:
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
) -> ResultsHistory:
    """Walk the configured results directory and build an ordered history."""
    diagnostics: Dict[str, Any] = {
        "enabled": config.enabled,
        "path": str(config.path),
    }
    if not config.enabled:
        diagnostics["status"] = "disabled"
        return ResultsHistory(runs=[], diagnostics=diagnostics)

    base = (workspace / config.path).resolve()
    diagnostics["resolved_path"] = str(base)

    if not base.exists():
        diagnostics["status"] = "missing"
        diagnostics["reason"] = f"results directory not found at {base}"
        return ResultsHistory(runs=[], diagnostics=diagnostics)

    candidates: List[Path] = []
    for child in base.iterdir():
        if not child.is_dir():
            continue
        if child.name == "latest":
            continue
        target = child / "results.json"
        if target.is_file():
            candidates.append(target)

    summaries: List[RunSummary] = []
    for path in candidates:
        summary = _summarize(path)
        if summary is not None:
            summaries.append(summary)

    summaries.sort(
        key=lambda s: s.timestamp or datetime.fromtimestamp(0, tz=timezone.utc)
    )

    if config.lookback_runs > 0:
        summaries = summaries[-config.lookback_runs :]

    diagnostics["status"] = "ok"
    diagnostics["runs_loaded"] = len(summaries)
    return ResultsHistory(runs=summaries, diagnostics=diagnostics)
