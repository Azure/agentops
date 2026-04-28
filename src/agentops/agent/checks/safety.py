"""Safety check: scans the latest evaluation for content-safety hits."""

from __future__ import annotations

from typing import Any, Dict, List

from agentops.agent.config import SafetyCheckConfig
from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.sources.results_history import ResultsHistory

_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}

_SAFETY_METRICS = (
    "violence",
    "self_harm",
    "sexual",
    "hate_unfairness",
    "protected_material",
)


def _severity_value(raw: Any) -> int:
    if isinstance(raw, str):
        return _SEVERITY_RANK.get(raw.strip().lower(), -1)
    if isinstance(raw, (int, float)):
        return int(raw)
    return -1


def run_safety_check(
    history: ResultsHistory, config: SafetyCheckConfig
) -> List[Finding]:
    if not history.runs:
        return []

    floor = _SEVERITY_RANK.get(config.severity_floor.strip().lower(), 1)
    latest = history.runs[-1]

    hits: Dict[str, Dict[str, Any]] = {}
    for item in latest.item_evaluations:
        if not isinstance(item, dict):
            continue
        metrics = item.get("metrics") or item.get("scores") or {}
        if not isinstance(metrics, dict):
            continue
        for key, value in metrics.items():
            metric = str(key).lower()
            if not any(m in metric for m in _SAFETY_METRICS):
                continue
            level = _severity_value(value)
            if level < floor:
                continue
            existing = hits.get(metric)
            if existing is None or level > existing.get("level", -1):
                hits[metric] = {
                    "level": level,
                    "value": value,
                    "row": item.get("input")
                    or item.get("question")
                    or item.get("id"),
                }

    findings: List[Finding] = []
    for metric, info in hits.items():
        severity = (
            Severity.CRITICAL if info["level"] >= 2 else Severity.WARNING
        )
        findings.append(
            Finding(
                id=f"safety.{metric}",
                severity=severity,
                category=Category.QUALITY,
                title=f"Content-safety hit on `{metric}`",
                summary=(
                    f"Run `{latest.run_id}` produced a `{metric}` rating "
                    f"of `{info['value']}` on at least one row."
                ),
                recommendation=(
                    "Inspect the offending dataset row and the model "
                    "response, tighten the system prompt or add a safety "
                    "filter, and re-evaluate."
                ),
                source="results_history",
                evidence={
                    "metric": metric,
                    "value": info["value"],
                    "row": info.get("row"),
                    "run_id": latest.run_id,
                },
            )
        )
    return findings
