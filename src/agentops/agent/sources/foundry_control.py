"""Foundry control-plane source.

Lazy-imports ``azure.ai.projects`` to read agent metadata and recent
runs. Fails open: missing config or SDK is reported via diagnostics.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agentops.agent.config import FoundryControlSourceConfig

log = logging.getLogger(__name__)


@dataclass
class FoundryAgentSummary:
    agent_id: str
    name: Optional[str] = None
    model: Optional[str] = None
    updated_at: Optional[str] = None
    instructions: Optional[str] = None


@dataclass
class EvaluationRuleSummary:
    """Continuous evaluation rule attached to a Foundry agent.

    The Foundry SDK exposes ``evaluation_rules`` (online evaluation
    policies) but does **not** today expose a dedicated "Guardrails"
    config API. Content-filter posture is therefore observed via the
    ``azure_resources`` source instead.
    """

    rule_id: str
    name: Optional[str] = None
    agent: Optional[str] = None
    enabled: Optional[bool] = None


@dataclass
class FoundryControlPayload:
    agents: List[FoundryAgentSummary] = field(default_factory=list)
    evaluation_rules: List[EvaluationRuleSummary] = field(default_factory=list)
    failed_runs: int = 0
    total_runs: int = 0
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    @property
    def failure_rate(self) -> Optional[float]:
        if self.total_runs <= 0:
            return None
        return self.failed_runs / self.total_runs


def _resolve_endpoint(config: FoundryControlSourceConfig) -> Optional[str]:
    if config.project_endpoint:
        return config.project_endpoint
    if config.project_endpoint_env:
        return os.environ.get(config.project_endpoint_env)
    return None


def collect_foundry_control(
    config: FoundryControlSourceConfig,
) -> FoundryControlPayload:
    diagnostics: Dict[str, Any] = {"enabled": config.enabled}

    if not config.enabled:
        diagnostics["status"] = "disabled"
        return FoundryControlPayload(diagnostics=diagnostics)

    endpoint = _resolve_endpoint(config)
    if not endpoint:
        diagnostics["status"] = "skipped"
        diagnostics["reason"] = (
            "no project_endpoint configured "
            f"(env var: {config.project_endpoint_env})"
        )
        return FoundryControlPayload(diagnostics=diagnostics)

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
        log.info("azure-ai-projects unavailable: %s", exc)
        return FoundryControlPayload(diagnostics=diagnostics)

    payload = FoundryControlPayload(diagnostics=diagnostics)

    try:
        credential = DefaultAzureCredential(exclude_developer_cli_credential=True, process_timeout=30)
        client = AIProjectClient(endpoint=endpoint, credential=credential)
    except Exception as exc:  # pragma: no cover
        diagnostics["status"] = "error"
        diagnostics["reason"] = f"client init failed: {exc}"
        return payload

    try:
        agents_iter = getattr(client, "agents", None)
        if agents_iter is not None:
            list_agents = getattr(agents_iter, "list_agents", None) or getattr(
                agents_iter, "list", None
            )
            if list_agents:
                for raw in list_agents():
                    aid = str(getattr(raw, "id", "") or getattr(raw, "name", ""))
                    if config.agent_ids and aid not in config.agent_ids:
                        continue
                    payload.agents.append(
                        FoundryAgentSummary(
                            agent_id=aid,
                            name=getattr(raw, "name", None),
                            model=getattr(raw, "model", None),
                            updated_at=str(getattr(raw, "updated_at", "") or "")
                            or None,
                            instructions=getattr(raw, "instructions", None),
                        )
                    )
    except Exception as exc:  # pragma: no cover
        log.warning("Foundry agents listing failed: %s", exc)
        diagnostics["agents_error"] = str(exc)

    # Best-effort: continuous evaluation rules attached to agents.
    # The exact accessor varies by SDK version; we try a few attribute
    # paths and silently skip if none are present.
    try:
        rules_accessor = (
            getattr(client, "evaluation_rules", None)
            or getattr(client, "evaluations", None)
        )
        list_rules = None
        if rules_accessor is not None:
            list_rules = (
                getattr(rules_accessor, "list", None)
                or getattr(rules_accessor, "list_evaluation_rules", None)
            )
        if list_rules is not None:
            for raw in list_rules():
                rid = str(getattr(raw, "id", "") or getattr(raw, "name", "") or "")
                if not rid:
                    continue
                enabled = getattr(raw, "enabled", None)
                if enabled is None:
                    enabled = getattr(raw, "is_enabled", None)
                payload.evaluation_rules.append(
                    EvaluationRuleSummary(
                        rule_id=rid,
                        name=getattr(raw, "name", None),
                        agent=getattr(raw, "agent_name", None)
                        or getattr(raw, "agent_id", None),
                        enabled=bool(enabled) if enabled is not None else None,
                    )
                )
            diagnostics["evaluation_rules_count"] = len(payload.evaluation_rules)
        else:
            diagnostics["evaluation_rules_status"] = "unavailable"
    except Exception as exc:  # pragma: no cover - SDK shape varies
        log.info("Foundry evaluation_rules listing skipped: %s", exc)
        diagnostics["evaluation_rules_warning"] = str(exc)

    diagnostics["status"] = "ok"
    diagnostics["agents_count"] = len(payload.agents)
    return payload
