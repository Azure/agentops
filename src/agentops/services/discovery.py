"""Discovery services for listing Foundry models and agents."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class ModelDeploymentInfo:
    """Summary of a model deployment in the Foundry project."""

    name: str
    model_name: str
    model_version: str
    deployment_type: str


@dataclass(frozen=True)
class AgentInfo:
    """Summary of an agent in the Foundry project."""

    name: str
    agent_id: str
    model: str


def _resolve_endpoint(endpoint: Optional[str] = None) -> str:
    """Resolve the Foundry project endpoint from argument or env var."""
    resolved = endpoint or os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", "")
    if not resolved:
        raise ValueError(
            "Foundry project endpoint is required. Set it via:\n"
            "  --endpoint <url>, or\n"
            "  AZURE_AI_FOUNDRY_PROJECT_ENDPOINT environment variable"
        )
    return resolved.strip()


def _get_project_client(endpoint: str):
    """Create an AIProjectClient with lazy Azure imports."""
    try:
        from azure.ai.projects import AIProjectClient  # noqa: WPS433
        from azure.identity import DefaultAzureCredential  # noqa: WPS433
    except ImportError as exc:
        raise ImportError(
            "This command requires 'azure-ai-projects>=2.0.1' and 'azure-identity'.\n"
            "Install with: pip install 'azure-ai-projects>=2.0.1' azure-identity"
        ) from exc

    credential = DefaultAzureCredential(exclude_developer_cli_credential=True)
    return AIProjectClient(endpoint=endpoint, credential=credential)


def list_models(
    endpoint: Optional[str] = None,
) -> List[ModelDeploymentInfo]:
    """List model deployments in the Foundry project."""
    resolved = _resolve_endpoint(endpoint)
    client = _get_project_client(resolved)

    deployments: List[ModelDeploymentInfo] = []
    for d in client.deployments.list():
        deployments.append(
            ModelDeploymentInfo(
                name=d.name,
                model_name=getattr(d, "model_name", "") or "",
                model_version=getattr(d, "model_version", "") or "",
                deployment_type=getattr(d, "type", "") or "",
            )
        )
    return deployments


def list_agents(
    endpoint: Optional[str] = None,
) -> List[AgentInfo]:
    """List agents in the Foundry project."""
    resolved = _resolve_endpoint(endpoint)
    client = _get_project_client(resolved)

    agents: List[AgentInfo] = []
    for a in client.agents.list():
        agents.append(
            AgentInfo(
                name=getattr(a, "name", "") or "",
                agent_id=getattr(a, "id", "") or "",
                model=getattr(a, "model", "") or "",
            )
        )
    return agents
