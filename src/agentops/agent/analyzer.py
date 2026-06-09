"""Analyzer orchestration for the watchdog agent."""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, TypeVar

from agentops.agent.checks.errors import run_errors_check
from agentops.agent.checks.foundry_config import run_foundry_config_check
from agentops.agent.checks.governance import run_governance_check
from agentops.agent.checks.latency import run_latency_check
from agentops.agent.checks.observability import run_observability_check
from agentops.agent.checks.opex_workspace import run_opex_workspace_check
from agentops.agent.checks.opex import run_opex_check
from agentops.agent.checks.posture import run_posture_check
from agentops.agent.checks.regression import run_regression_check
from agentops.agent.checks.release_readiness import run_release_readiness_check
from agentops.agent.checks.safety import run_safety_check
from agentops.agent.checks.spec_conformance import run_spec_conformance_check
from agentops.agent.llm_assist import run_llm_assist_check
from agentops.agent.llm_assist._spec_rules import (
    run_spec_implementation_gap_rule,
)
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

_T = TypeVar("_T")


@dataclass
class AnalysisResult:
    findings: List[Finding] = field(default_factory=list)
    history: Optional[ResultsHistory] = None
    monitor: Optional[AzureMonitorPayload] = None
    foundry: Optional[FoundryControlPayload] = None
    resources: Optional[AzureResourcesPayload] = None
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    workspace: Optional[Path] = None

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
    from agentops.agent._legacy_ids import canonical_category

    out: Set[Category] = set()
    for c in categories:
        if not c:
            continue
        normalized = canonical_category(c.strip().lower())
        try:
            out.add(Category(normalized))
        except ValueError:
            continue
    return out or None


def analyze(
    workspace: Path,
    config: AgentConfig,
    *,
    categories: Optional[Iterable[str]] = None,
    exclude_rules: Optional[Iterable[str]] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> AnalysisResult:
    """Run every configured source + check and return the merged result.

    ``categories`` (when provided) limits the findings to the listed
    :class:`Category` values. ``exclude_rules`` is forwarded to the
    posture check to skip individual WAF rule ids on top of any
    exclusions configured in ``agent.yaml``.
    """
    notify = progress or (lambda _msg: None)
    notify("doctor: collecting local history, Azure Monitor, and Foundry control plane")

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="agentops-doctor") as pool:
        monitor_future = pool.submit(
            _timed,
            lambda: collect_azure_monitor(config.sources.azure_monitor, config.lookback_days),
        )
        foundry_future = pool.submit(
            _timed,
            lambda: collect_foundry_control(config.sources.foundry_control),
        )
        history_future = pool.submit(
            _timed,
            lambda: collect_results_history(
                workspace,
                config.sources.results_history,
                foundry_config=config.sources.foundry_control,
            ),
        )

        monitor, monitor_seconds = _finish_source("Azure Monitor", monitor_future, notify)
        foundry, foundry_seconds = _finish_source("Foundry control plane", foundry_future, notify)
        history, history_seconds = _finish_source("results history", history_future, notify)

    resources_started = time.perf_counter()
    resources = collect_azure_resources(
        config.sources.azure_resources,
        workspace=workspace,
        project_endpoint=(foundry.diagnostics or {}).get("endpoint"),
    )
    resources_seconds = time.perf_counter() - resources_started
    notify(f"doctor: source Azure resources complete ({resources_seconds:.1f}s)")
    notify("doctor: running readiness checks")

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
    findings.extend(run_safety_check(history, config.checks.safety, monitor, foundry))
    findings.extend(run_posture_check(resources, posture_config))
    findings.extend(run_opex_workspace_check(workspace))
    findings.extend(run_governance_check(workspace))
    findings.extend(run_observability_check(workspace))
    findings.extend(run_opex_check(history, config.checks.opex))
    findings.extend(run_release_readiness_check(workspace, history, foundry))
    findings.extend(
        run_spec_conformance_check(
            workspace, config.checks.operational_excellence.spec_conformance
        )
    )
    findings.extend(run_foundry_config_check(foundry))
    findings.extend(
        run_llm_assist_check(workspace, config.checks.llm_assist, foundry)
    )
    findings.extend(
        run_spec_implementation_gap_rule(
            workspace,
            config.checks.llm_assist,
            config.checks.operational_excellence.spec_conformance,
        )
    )

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
            "source_timings_seconds": {
                "results_history": round(history_seconds, 3),
                "azure_monitor": round(monitor_seconds, 3),
                "foundry_control": round(foundry_seconds, 3),
                "azure_resources": round(resources_seconds, 3),
            },
        },
        workspace=workspace,
    )


def _timed(fn: Callable[[], _T]) -> tuple[_T, float]:
    started = time.perf_counter()
    value = fn()
    return value, time.perf_counter() - started


def _finish_source(
    label: str,
    future: Future[tuple[_T, float]],
    progress: Callable[[str], None],
) -> tuple[_T, float]:
    value, seconds = future.result()
    progress(f"doctor: source {label} complete ({seconds:.1f}s)")
    return value, seconds
