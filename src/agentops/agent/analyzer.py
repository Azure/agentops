"""Analyzer orchestration for the watchdog agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from agentops.agent.checks.errors import run_errors_check
from agentops.agent.checks.latency import run_latency_check
from agentops.agent.checks.posture import run_posture_check
from agentops.agent.checks.regression import run_regression_check
from agentops.agent.checks.safety import run_safety_check
from agentops.agent.config import AgentConfig
from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.sources.azure_monitor import (
    AzureMonitorPayload,
    collect_azure_monitor,
)
from agentops.agent.sources.azure_resources import (
    AzureResourcesPayload,
    collect_azure_resources,
)
from agentops.agent.sources.foundry_control import (
    FoundryControlPayload,
    collect_foundry_control,
)
from agentops.agent.sources.results_history import (
    ResultsHistory,
    collect_results_history,
)


@dataclass
class AnalysisResult:
    findings: List[Finding] = field(default_factory=list)
    history: Optional[ResultsHistory] = None
    monitor: Optional[AzureMonitorPayload] = None
    foundry: Optional[FoundryControlPayload] = None
    resources: Optional[AzureResourcesPayload] = None
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    @property
    def max_severity(self) -> Optional[Severity]:
        if not self.findings:
            return None
        return max(f.severity for f in self.findings)


def _normalize_categories(
    categories: Optional[Iterable[str]],
) -> Optional[Set[Category]]:
    if categories is None:
        return None
    out: Set[Category] = set()
    for c in categories:
        if not c:
            continue
        try:
            out.add(Category(c.strip().lower()))
        except ValueError:
            continue
    return out or None


def analyze(
    workspace: Path,
    config: AgentConfig,
    *,
    categories: Optional[Iterable[str]] = None,
    exclude_rules: Optional[Iterable[str]] = None,
) -> AnalysisResult:
    """Run every configured source + check and return the merged result.

    ``categories`` (when provided) limits the findings to the listed
    :class:`Category` values. ``exclude_rules`` is forwarded to the
    posture check to skip individual WAF rule ids on top of any
    exclusions configured in ``agent.yaml``.
    """
    history = collect_results_history(workspace, config.sources.results_history)
    monitor = collect_azure_monitor(config.sources.azure_monitor, config.lookback_days)
    foundry = collect_foundry_control(config.sources.foundry_control)
    resources = collect_azure_resources(config.sources.azure_resources)

    posture_config = config.checks.posture
    if exclude_rules:
        merged = list(posture_config.exclude_rules) + [
            r.strip() for r in exclude_rules if r and r.strip()
        ]
        posture_config = posture_config.model_copy(update={"exclude_rules": merged})

    findings: List[Finding] = []
    findings.extend(run_regression_check(history, config.checks.regression))
    findings.extend(run_latency_check(history, monitor, config.checks.latency))
    findings.extend(run_errors_check(monitor, foundry, config.checks.errors))
    findings.extend(run_safety_check(history, config.checks.safety))
    findings.extend(run_posture_check(resources, posture_config))

    allowed = _normalize_categories(categories)
    if allowed is not None:
        findings = [f for f in findings if f.category in allowed]

    findings.sort(key=lambda f: (-f.severity.rank, f.category.value, f.id))

    return AnalysisResult(
        findings=findings,
        history=history,
        monitor=monitor,
        foundry=foundry,
        resources=resources,
        diagnostics={
            "results_history": history.diagnostics,
            "azure_monitor": monitor.diagnostics,
            "foundry_control": foundry.diagnostics,
            "azure_resources": resources.diagnostics,
        },
    )
