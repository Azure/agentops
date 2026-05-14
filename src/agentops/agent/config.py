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
    min_runtime_hits: int = Field(1, ge=1)
    runtime_critical_hits: int = Field(10, ge=1)


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


class OpexCheckConfig(BaseModel):
    """Operational-excellence (time-based) check configuration."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    stale_after_days: int = Field(14, ge=1)
    min_runs_for_flaky: int = Field(5, ge=3)
    flaky_cv_threshold: float = Field(0.30, gt=0.0, le=1.0)


class LLMAssistCheckConfig(BaseModel):
    """LLM-judged advisory checks.

    Enabled by default - the Doctor auto-discovers a judge model from
    the Foundry project on first use and reuses it on subsequent runs.
    Set ``enabled: false`` to skip the suite entirely (e.g. in
    ephemeral CI sandboxes that have no Foundry access).

    The judge model is invoked via the Foundry project's OpenAI client.
    No new credential flow.
    """

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    deployment_name: Optional[str] = None
    deployment_name_env: str = "AZURE_AI_MODEL_DEPLOYMENT_NAME"
    project_endpoint_env: str = "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT"
    project_endpoint: Optional[str] = None
    rules: List[str] = Field(default_factory=list)
    max_dataset_rows: int = Field(50, ge=1, le=500)
    min_confidence: float = Field(0.6, ge=0.0, le=1.0)
    cache_ttl_days: int = Field(30, ge=0)


class LLMSpecConformanceConfig(BaseModel):
    """LLM gap-analysis sub-config for spec-conformance."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    severity_floor: float = Field(0.6, ge=0.0, le=1.0)
    max_input_chars: int = Field(30_000, ge=1_000, le=200_000)
    max_workspace_paths: int = Field(200, ge=10, le=2_000)


class SpecConformanceCheckConfig(BaseModel):
    """Spec-conformance sub-check under Operational Excellence.

    The check inspects the workspace for spec-driven-development
    artifacts (spec-kit ``.specify/``, ``AGENTS.md``, Copilot
    instructions) and flags drift between the spec and the
    implementation.
    """

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    detectors: List[str] = Field(
        default_factory=lambda: ["spec-kit", "agents-md"]
    )
    stale_after_days: int = Field(30, ge=1)
    skip: List[str] = Field(default_factory=list)
    llm_assist: LLMSpecConformanceConfig = Field(
        default_factory=LLMSpecConformanceConfig
    )


class OperationalExcellenceCheckConfig(BaseModel):
    """Container for Operational Excellence sub-checks."""

    model_config = ConfigDict(extra="forbid")
    spec_conformance: SpecConformanceCheckConfig = Field(
        default_factory=SpecConformanceCheckConfig
    )


class ChecksConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    regression: RegressionCheckConfig = Field(default_factory=RegressionCheckConfig)
    latency: LatencyCheckConfig = Field(default_factory=LatencyCheckConfig)
    errors: ErrorsCheckConfig = Field(default_factory=ErrorsCheckConfig)
    safety: SafetyCheckConfig = Field(default_factory=SafetyCheckConfig)
    posture: PostureCheckConfig = Field(default_factory=PostureCheckConfig)
    opex: OpexCheckConfig = Field(default_factory=OpexCheckConfig)
    operational_excellence: OperationalExcellenceCheckConfig = Field(
        default_factory=OperationalExcellenceCheckConfig
    )
    llm_assist: LLMAssistCheckConfig = Field(default_factory=LLMAssistCheckConfig)


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
    """Load an :class:`AgentConfig` from a YAML file (or return defaults).

    Legacy ``genaiops.*`` rule ids in ``checks.llm_assist.rules`` are
    rewritten to their canonical ``opex.*`` equivalents with a one-shot
    deprecation warning. See ``_legacy_ids.py`` for details.
    """
    if path is None or not path.exists():
        return AgentConfig()

    from agentops.utils.yaml import load_yaml

    from agentops.agent._legacy_ids import canonicalize_id_list

    raw = load_yaml(path)
    if isinstance(raw, dict):
        checks = raw.get("checks")
        if isinstance(checks, dict):
            llm = checks.get("llm_assist")
            if isinstance(llm, dict) and isinstance(llm.get("rules"), list):
                llm["rules"] = canonicalize_id_list(llm["rules"])
    return AgentConfig.model_validate(raw)
