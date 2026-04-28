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


@dataclass
class FoundryControlPayload:
    agents: List[FoundryAgentSummary] = field(default_factory=list)
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
            "(install agentops-toolkit[foundry])"
        )
        log.info("azure-ai-projects unavailable: %s", exc)
        return FoundryControlPayload(diagnostics=diagnostics)

    payload = FoundryControlPayload(diagnostics=diagnostics)

    try:
        credential = DefaultAzureCredential(exclude_developer_cli_credential=True)
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
                        )
                    )
    except Exception as exc:  # pragma: no cover
        log.warning("Foundry agents listing failed: %s", exc)
        diagnostics["agents_error"] = str(exc)

    diagnostics["status"] = "ok"
    diagnostics["agents_count"] = len(payload.agents)
    return payload
