"""Result dataclasses for the AgentOps 1.0 pipeline.

These shapes are written to ``results.json`` after every ``agentops eval`` run
and consumed by the reporter and comparison logic.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class RowMetric(BaseModel):
    """A single evaluator score for one dataset row."""

    name: str
    value: Optional[float] = None
    error: Optional[str] = None
    reason: Optional[str] = None


class RowResult(BaseModel):
    """One evaluated dataset row."""

    row_index: int
    input: str
    expected: Optional[str] = None
    response: str = ""
    context: Optional[str] = None
    latency_seconds: Optional[float] = None
    tool_calls: Optional[List[Any]] = None
    metrics: List[RowMetric] = Field(default_factory=list)
    error: Optional[str] = None


class ThresholdEvaluation(BaseModel):
    """A pass/fail check for a single metric on the run aggregate."""

    metric: str
    criteria: str
    expected: str
    actual: str
    passed: bool


class RunSummary(BaseModel):
    """Top-level pass/fail summary of an evaluation run."""

    items_total: int
    items_passed_all: int
    items_pass_rate: float
    thresholds_total: int
    thresholds_passed: int
    threshold_pass_rate: float
    overall_passed: bool


class TargetInfo(BaseModel):
    """Resolved target information (echoed into results.json)."""

    kind: str
    raw: str
    protocol: Optional[str] = None
    name: Optional[str] = None
    version: Optional[str] = None
    url: Optional[str] = None
    deployment: Optional[str] = None


class ComparisonMetric(BaseModel):
    """Per-metric delta between the current run and a baseline."""

    metric: str
    current: Optional[float] = None
    baseline: Optional[float] = None
    delta: Optional[float] = None
    direction: str  # "improved" | "regressed" | "unchanged"


class ComparisonRow(BaseModel):
    """Per-row regression / improvement against a baseline."""

    row_index: int
    current_passed: bool
    baseline_passed: Optional[bool] = None
    direction: str  # "improved" | "regressed" | "unchanged" | "new"


class CommitInfo(BaseModel):
    """The git commit a single evaluation run was produced from."""

    sha: str
    short_sha: str
    subject: str
    author: str
    authored_at: str
    source: Literal["ci", "local"]


class ChangedInput(BaseModel):
    """One concrete difference detected between two runs' recorded fields."""

    field: str
    description: str
    from_value: Optional[str] = None
    to_value: Optional[str] = None


class RegressionInsight(BaseModel):
    """Causal explanation for a detected regression between two runs."""

    from_run_id: str
    to_run_id: str
    from_commit: Optional[CommitInfo] = None
    to_commit: Optional[CommitInfo] = None
    metric: str
    from_value: float
    to_value: float
    changed_inputs: List[ChangedInput] = Field(default_factory=list)
    explanation: str
    suggested_action: Optional[str] = None
    used_git_diff: bool = False


class ComparisonInfo(BaseModel):
    """Comparison block included when ``--baseline`` was provided."""

    baseline_path: str
    baseline_started_at: Optional[str] = None
    baseline_overall_passed: Optional[bool] = None
    metrics: List[ComparisonMetric] = Field(default_factory=list)
    rows: List[ComparisonRow] = Field(default_factory=list)
    insight: Optional[RegressionInsight] = None


class RunResult(BaseModel):
    """Full ``results.json`` payload."""

    version: int = 1
    started_at: str
    finished_at: str
    duration_seconds: float
    target: TargetInfo
    dataset_path: str
    evaluators: List[str] = Field(default_factory=list)
    rows: List[RowResult] = Field(default_factory=list)
    aggregate_metrics: Dict[str, float] = Field(default_factory=dict)
    thresholds: List[ThresholdEvaluation] = Field(default_factory=list)
    summary: RunSummary
    comparison: Optional[ComparisonInfo] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    commit: Optional[CommitInfo] = None

    model_config = ConfigDict(extra="forbid")
