"""Pydantic configuration model for the watchdog agent."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ResultsHistorySourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    path: str = ".agentops/results"
    lookback_runs: int = Field(10, ge=2)


class AzureMonitorSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    app_insights_resource_id: Optional[str] = None
    log_analytics_workspace_id: Optional[str] = None


class FoundryControlSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    project_endpoint: Optional[str] = None
    project_endpoint_env: str = "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT"
    agent_ids: List[str] = Field(default_factory=list)


class AzureResourcesSourceConfig(BaseModel):
    """Read-only management-plane source for Azure resource posture audits.

    Requires ``Reader`` (or stronger) RBAC on the resource group, and the
    ``[agent]`` extra (which pulls in ``azure-mgmt-cognitiveservices`` and
    ``azure-mgmt-monitor``).
    """

    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    subscription_id: Optional[str] = None
    subscription_id_env: str = "AZURE_SUBSCRIPTION_ID"
    resource_group: Optional[str] = None
    cognitive_services_account: Optional[str] = None


class SourcesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results_history: ResultsHistorySourceConfig = Field(
        default_factory=ResultsHistorySourceConfig
    )
    azure_monitor: AzureMonitorSourceConfig = Field(
        default_factory=AzureMonitorSourceConfig
    )
    foundry_control: FoundryControlSourceConfig = Field(
        default_factory=FoundryControlSourceConfig
    )
    azure_resources: AzureResourcesSourceConfig = Field(
        default_factory=AzureResourcesSourceConfig
    )


class RegressionCheckConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metrics: List[str] = Field(
        default_factory=lambda: [
            "coherence",
            "fluency",
            "similarity",
            "f1_score",
            "groundedness",
            "tool_call_accuracy",
        ]
    )
    threshold_drop: float = Field(0.10, ge=0.0, le=1.0)
    min_runs: int = Field(3, ge=2)


class LatencyCheckConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    p95_threshold_seconds: float = Field(5.0, gt=0)


class ErrorsCheckConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rate_threshold: float = Field(0.05, ge=0.0, le=1.0)


class SafetyCheckConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity_floor: str = "Medium"  # Low | Medium | High


class PostureCheckConfig(BaseModel):
    """WAF-AI posture audit configuration.

    The MVP rule set targets the **Security** pillar of the
    Microsoft Well-Architected Framework for AI workloads.

    The check is opt-in: ``enabled`` defaults to ``False`` because it
    requires the ``azure_resources`` source to be configured and an
    Azure Reader role on the target resource group.
    """

    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    pillar: str = "security"
    exclude_rules: List[str] = Field(default_factory=list)


class ChecksConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    regression: RegressionCheckConfig = Field(default_factory=RegressionCheckConfig)
    latency: LatencyCheckConfig = Field(default_factory=LatencyCheckConfig)
    errors: ErrorsCheckConfig = Field(default_factory=ErrorsCheckConfig)
    safety: SafetyCheckConfig = Field(default_factory=SafetyCheckConfig)
    posture: PostureCheckConfig = Field(default_factory=PostureCheckConfig)


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    github_app_client_id: Optional[str] = None


class AgentConfig(BaseModel):
    """Root config for ``.agentops/agent.yaml``."""

    model_config = ConfigDict(extra="forbid")
    version: int = 1
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    checks: ChecksConfig = Field(default_factory=ChecksConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    lookback_days: int = Field(7, ge=1)


def load_agent_config(path: Optional[Path]) -> AgentConfig:
    """Load an :class:`AgentConfig` from a YAML file (or return defaults)."""
    if path is None or not path.exists():
        return AgentConfig()

    from agentops.utils.yaml import load_yaml

    raw = load_yaml(path)
    return AgentConfig.model_validate(raw)
