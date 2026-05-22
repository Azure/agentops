"""Safety check: scans local evaluations, production telemetry, and
Foundry control-plane configuration for Responsible-AI gaps.

The check emits findings under :class:`Category.RESPONSIBLE_AI` from
three independent layers:

* **Eval** - content-safety metric hits in the latest eval run.
  Backwards-compatible id ``safety.<metric>``; evidence carries
  ``layer=eval``.
* **Runtime** - content-filter triggers observed in App Insights /
  Log Analytics within the lookback window. Id ``safety.runtime.content_filter``.
* **Config** - Foundry continuous-evaluation rules missing or disabled.
  Id ``safety.config.continuous_eval_missing`` /
  ``safety.config.continuous_eval_disabled``.

Each layer fails open: if its source did not produce a payload, that
layer simply emits nothing.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agentops.agent.config import SafetyCheckConfig
from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.sources.azure_monitor import AzureMonitorPayload
from agentops.agent.sources.foundry_control import FoundryControlPayload
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
    history: ResultsHistory,
    config: SafetyCheckConfig,
    monitor: Optional[AzureMonitorPayload] = None,
    foundry: Optional[FoundryControlPayload] = None,
) -> List[Finding]:
    """Run all three safety layers and return the merged findings."""
    findings: List[Finding] = []
    findings.extend(_find_eval_safety(history, config))
    if monitor is not None:
        findings.extend(_find_runtime_safety(monitor, config))
    if foundry is not None:
        findings.extend(_find_config_safety(foundry))
    return findings


def _find_eval_safety(
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
                category=Category.RESPONSIBLE_AI,
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
                    "layer": "eval",
                    "metric": metric,
                    "value": info["value"],
                    "row": info.get("row"),
                    "run_id": latest.run_id,
                },
            )
        )
    return findings


def _find_runtime_safety(
    monitor: AzureMonitorPayload, config: SafetyCheckConfig
) -> List[Finding]:
    findings: List[Finding] = []
    for violation in monitor.safety_violations:
        if not isinstance(violation, dict):
            continue
        hits = int(violation.get("hits", 0) or 0)
        if hits < config.min_runtime_hits:
            continue
        signal = str(violation.get("signal") or "content_filter")
        severity = (
            Severity.CRITICAL
            if hits >= config.runtime_critical_hits
            else Severity.WARNING
        )
        findings.append(
            Finding(
                id=f"safety.runtime.{signal}",
                severity=severity,
                category=Category.RESPONSIBLE_AI,
                title=f"Content-filter triggers detected in production (`{signal}`)",
                summary=(
                    f"App Insights observed {hits} `{signal}` event(s) "
                    "over the lookback window. Each one is a response "
                    "the model refused to complete or a request blocked "
                    "by Azure AI Content Safety."
                ),
                recommendation=(
                    "Inspect the underlying traces in Application "
                    "Insights, identify whether the spike originates "
                    "from a single client, a regression in the system "
                    "prompt, or actual adversarial input, and adjust "
                    "guardrails accordingly."
                ),
                source="azure_monitor",
                evidence={
                    "layer": "runtime",
                    "signal": signal,
                    "hits": hits,
                },
            )
        )
    return findings


def _find_config_safety(foundry: FoundryControlPayload) -> List[Finding]:
    if not foundry.agents:
        return []

    rules = foundry.evaluation_rules
    diag = foundry.diagnostics or {}

    # We only emit config findings if we were actually able to *probe*
    # for rules (avoid false positives when the SDK lacks the surface).
    if (
        "evaluation_rules_count" not in diag
        and "evaluation_rules_warning" not in diag
    ):
        return []

    findings: List[Finding] = []

    if not rules:
        findings.append(
            Finding(
                id="safety.config.continuous_eval_missing",
                severity=Severity.WARNING,
                category=Category.RESPONSIBLE_AI,
                title="No continuous evaluation rules configured",
                summary=(
                    f"Foundry project lists {len(foundry.agents)} agent(s) "
                    "but no continuous-evaluation rules. Production "
                    "responses are not being scored on quality / safety "
                    "after deployment."
                ),
                recommendation=(
                    "Attach continuous evaluation rules to your agents "
                    "in Foundry (Operate -> Evaluations) so deployed "
                    "responses are scored against safety and quality "
                    "metrics in production."
                ),
                source="foundry_control",
                evidence={
                    "layer": "config",
                    "agents": [a.agent_id for a in foundry.agents],
                },
            )
        )
        return findings

    disabled = [r for r in rules if r.enabled is False]
    if disabled:
        findings.append(
            Finding(
                id="safety.config.continuous_eval_disabled",
                severity=Severity.WARNING,
                category=Category.RESPONSIBLE_AI,
                title="One or more continuous evaluation rules are disabled",
                summary=(
                    f"{len(disabled)} of {len(rules)} continuous "
                    "evaluation rule(s) are disabled. Production safety "
                    "scoring is partially or fully turned off."
                ),
                recommendation=(
                    "Re-enable the disabled rules in Foundry "
                    "(Operate -> Evaluations) or remove them if they "
                    "are intentionally retired."
                ),
                source="foundry_control",
                evidence={
                    "layer": "config",
                    "disabled_rules": [r.rule_id for r in disabled],
                },
            )
        )
    return findings
