"""Versioned schema for AgentOps release evidence artifacts."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


ReadinessStatus = Literal["ready", "ready_with_warnings", "blocked"]
CheckStatus = Literal["ready", "warning", "blocked", "unknown"]


class ReleaseEvidenceLink(BaseModel):
    """A navigable reference related to the release decision."""

    label: str
    url: str

    model_config = ConfigDict(extra="forbid")


class ReleaseEvidenceCheck(BaseModel):
    """One production-readiness check in the evidence pack."""

    name: str
    status: CheckStatus
    summary: str
    evidence: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ReleaseEvidence(BaseModel):
    """Stable machine-readable production-readiness evidence contract."""

    version: int = 1
    generated_at: str
    workspace: str
    status: ReadinessStatus
    target: Optional[str] = None
    blockers: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    ready: List[str] = Field(default_factory=list)
    checks: List[ReleaseEvidenceCheck] = Field(default_factory=list)
    links: List[ReleaseEvidenceLink] = Field(default_factory=list)
    latest_eval: Dict[str, Any] = Field(default_factory=dict)
    official_eval: Dict[str, Any] = Field(default_factory=dict)
    doctor: Dict[str, Any] = Field(default_factory=dict)
    workflows: Dict[str, Any] = Field(default_factory=dict)
    foundry: Dict[str, Any] = Field(default_factory=dict)
    monitoring: Dict[str, Any] = Field(default_factory=dict)
    trace_dataset: Dict[str, Any] = Field(default_factory=dict)
    ailz: Dict[str, Any] = Field(default_factory=dict)
    governance: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")
