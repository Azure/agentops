"""Pydantic models for AgentOps schemas."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

ComparisonCriteria = Literal[">=", ">", "<=", "<", "=="]
Criteria = Literal[">=", ">", "<=", "<", "==", "true", "false"]
EvaluatorSource = Literal["local", "foundry"]


class WorkspacePaths(BaseModel):
    bundles_dir: Path
    datasets_dir: Path
    data_dir: Path
    results_dir: Path


class WorkspaceDefaults(BaseModel):
    backend: str
    timeout_seconds: int


class WorkspaceReport(BaseModel):
    generate_markdown: bool = True


class WorkspaceConfig(BaseModel):
    version: int
    paths: WorkspacePaths
    defaults: WorkspaceDefaults
    report: WorkspaceReport


class EvaluatorConfig(BaseModel):
    name: str
    source: EvaluatorSource = "local"
    enabled: bool = True
    config: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must be non-empty")
        return value


class ThresholdRule(BaseModel):
    evaluator: str
    criteria: Criteria
    value: Optional[float] = Field(None, description="Numeric threshold target")

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)

        if "evaluator" not in normalized and "metric" in normalized:
            normalized["evaluator"] = normalized["metric"]

        if "criteria" not in normalized and "operator" in normalized:
            normalized["criteria"] = normalized["operator"]

        if isinstance(normalized.get("criteria"), bool):
            normalized["criteria"] = "true" if normalized["criteria"] else "false"

        criteria_value = normalized.get("criteria")
        if isinstance(criteria_value, str):
            normalized["criteria"] = criteria_value.strip().lower()

        return normalized

    @field_validator("evaluator")
    @classmethod
    def _evaluator_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evaluator must be non-empty")
        return value

    @field_validator("value", mode="before")
    @classmethod
    def _value_is_number(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("value must be numeric")
        return value

    @model_validator(mode="after")
    def _validate_criteria(self) -> "ThresholdRule":
        if self.criteria in {"true", "false"}:
            if self.value is not None:
                raise ValueError("value must be omitted for boolean criteria")
            return self

        if self.value is None:
            raise ValueError("value is required for comparison criteria")
        return self


class BundleConfig(BaseModel):
    version: int
    name: str
    description: Optional[str] = None
    evaluators: List[EvaluatorConfig] = Field(default_factory=list)
    thresholds: List[ThresholdRule] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must be non-empty")
        return value


class DatasetSource(BaseModel):
    type: str
    path: Path

    @field_validator("path", mode="before")
    @classmethod
    def _path_non_empty(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            raise ValueError("path must be non-empty")
        return value


class DatasetFormat(BaseModel):
    type: str
    input_field: str
    expected_field: str
    context_field: Optional[str] = None


class DatasetConfig(BaseModel):
    version: int
    name: str
    description: Optional[str] = None
    source: DatasetSource
    format: DatasetFormat
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must be non-empty")
        return value


class BundleRef(BaseModel):
    path: Path


class DatasetRef(BaseModel):
    path: Path


class BackendConfig(BaseModel):
    type: str
    command: Optional[str] = None
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    timeout_seconds: Optional[int] = None
    target: Optional[str] = None
    agent_id: Optional[str] = None
    project_endpoint: Optional[str] = None
    project_endpoint_env: Optional[str] = None
    api_version: Optional[str] = None
    poll_interval_seconds: Optional[float] = None
    max_poll_attempts: Optional[int] = None
    model: Optional[str] = None

    @field_validator("model")
    @classmethod
    def _reject_placeholder_model(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value

        normalized = value.strip()
        looks_like_placeholder = (
            normalized.startswith("<") and normalized.endswith(">")
        ) or "replace-with" in normalized.lower()
        if looks_like_placeholder:
            raise ValueError(
                "backend.model must be replaced with a real Foundry model deployment name"
            )
        return normalized

    @model_validator(mode="after")
    def _validate_subprocess_requirements(self) -> "BackendConfig":
        if self.type == "subprocess":
            if not self.command or not self.command.strip():
                raise ValueError("backend.command is required for subprocess")
            if not self.args:
                raise ValueError("backend.args is required for subprocess")
        elif self.type == "foundry":
            target = (self.target or "agent").strip().lower()
            if target not in {"agent", "model"}:
                raise ValueError(
                    "backend.target must be 'agent' or 'model' for foundry"
                )

            self.target = target
            if target == "agent":
                if not self.agent_id or not self.agent_id.strip():
                    raise ValueError(
                        "backend.agent_id is required for foundry target=agent"
                    )
            # target=model does not require agent_id

            if self.max_poll_attempts is not None and self.max_poll_attempts <= 0:
                raise ValueError("backend.max_poll_attempts must be > 0")
            if (
                self.poll_interval_seconds is not None
                and self.poll_interval_seconds <= 0
            ):
                raise ValueError("backend.poll_interval_seconds must be > 0")
        else:
            raise ValueError(f"Unsupported backend type: {self.type}")
        return self


class OutputConfig(BaseModel):
    write_report: bool = True
    publish_foundry_evaluation: bool = False
    fail_on_foundry_publish_error: bool = False


class RunConfig(BaseModel):
    version: int
    bundle: BundleRef
    dataset: DatasetRef
    backend: BackendConfig
    output: OutputConfig


class BundleInfo(BaseModel):
    name: str
    path: Path


class DatasetInfo(BaseModel):
    name: str
    path: Path


class ExecutionInfo(BaseModel):
    backend: str
    command: str
    started_at: str
    finished_at: str
    duration_seconds: float
    exit_code: int


class MetricResult(BaseModel):
    name: str
    value: float

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must be non-empty")
        return value

    @field_validator("value", mode="before")
    @classmethod
    def _value_is_number(cls, value: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("value must be numeric")
        return value


class RowMetricsResult(BaseModel):
    row_index: int
    metrics: List[MetricResult] = Field(default_factory=list)

    @field_validator("row_index")
    @classmethod
    def _row_index_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("row_index must be >= 1")
        return value


class ThresholdEvaluationResult(BaseModel):
    evaluator: str
    criteria: Criteria
    expected: str
    actual: str
    passed: bool

    @field_validator("evaluator")
    @classmethod
    def _evaluator_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evaluator must be non-empty")
        return value


class ItemThresholdEvaluationResult(BaseModel):
    row_index: int
    evaluator: str
    criteria: Criteria
    expected: str
    actual: str
    passed: bool

    @field_validator("row_index")
    @classmethod
    def _row_index_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("row_index must be >= 1")
        return value

    @field_validator("evaluator")
    @classmethod
    def _evaluator_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evaluator must be non-empty")
        return value


class ItemEvaluationResult(BaseModel):
    row_index: int
    passed_all: bool
    thresholds: List[ItemThresholdEvaluationResult] = Field(default_factory=list)

    @field_validator("row_index")
    @classmethod
    def _row_index_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("row_index must be >= 1")
        return value


class Summary(BaseModel):
    metrics_count: int
    thresholds_count: int
    thresholds_passed: int
    thresholds_failed: int
    overall_passed: bool


class Artifacts(BaseModel):
    backend_stdout: Optional[str] = None
    backend_stderr: Optional[str] = None
    foundry_eval_studio_url: Optional[str] = None
    foundry_eval_name: Optional[str] = None


class RunResult(BaseModel):
    version: int
    status: str
    bundle: BundleInfo
    dataset: DatasetInfo
    execution: ExecutionInfo
    metrics: List[MetricResult] = Field(default_factory=list)
    row_metrics: List[RowMetricsResult] = Field(default_factory=list)
    item_evaluations: List[ItemEvaluationResult] = Field(default_factory=list)
    run_metrics: List[MetricResult] = Field(default_factory=list)
    thresholds: List[ThresholdEvaluationResult] = Field(default_factory=list)
    summary: Summary
    artifacts: Optional[Artifacts] = None


# ---------------------------------------------------------------------------
# Comparison models
# ---------------------------------------------------------------------------

Direction = Literal["improved", "regressed", "unchanged"]


class RunReference(BaseModel):
    run_id: str
    bundle_name: str
    dataset_name: str
    started_at: str
    backend: Optional[str] = None
    target: Optional[str] = None
    model: Optional[str] = None
    agent_id: Optional[str] = None
    project_endpoint: Optional[str] = None
    overall_passed: Optional[bool] = None


class ComparisonMetricRow(BaseModel):
    """One metric across all compared runs."""

    name: str
    values: List[float] = Field(default_factory=list)
    deltas: List[Optional[float]] = Field(default_factory=list)
    delta_percents: List[Optional[float]] = Field(default_factory=list)
    directions: List[Direction] = Field(default_factory=list)
    best_run_index: Optional[int] = None


class ComparisonThresholdRow(BaseModel):
    """One threshold across all compared runs."""

    evaluator: str
    criteria: Criteria
    target: Optional[str] = None
    passed: List[bool] = Field(default_factory=list)


class ComparisonItemRow(BaseModel):
    """One dataset item across all compared runs."""

    row_index: int
    passed_all: List[bool] = Field(default_factory=list)
    scores: Dict[str, List[Optional[float]]] = Field(default_factory=dict)


ComparisonType = Literal[
    "agent",  # Same dataset, different agent/agent version
    "model",  # Same dataset, different model
    "dataset",  # Same agent/model, different datasets
    "general",  # Multiple things differ
]


class ComparisonConditions(BaseModel):
    """What's fixed vs varying across compared runs."""

    comparison_type: ComparisonType
    fixed: Dict[str, str] = Field(default_factory=dict)
    varying: List[str] = Field(default_factory=list)
    row_level_valid: bool = True


class ComparisonSummary(BaseModel):
    run_count: int
    any_regressions: bool
    runs_with_regressions: List[int] = Field(default_factory=list)


class ComparisonResult(BaseModel):
    """Unified comparison of 2 or more evaluation runs.

    The first entry in ``runs`` is always the baseline.
    """

    version: int = 1
    runs: List[RunReference] = Field(default_factory=list)
    baseline_index: int = 0
    conditions: Optional[ComparisonConditions] = None
    metric_rows: List[ComparisonMetricRow] = Field(default_factory=list)
    threshold_rows: List[ComparisonThresholdRow] = Field(default_factory=list)
    item_rows: List[ComparisonItemRow] = Field(default_factory=list)
    summary: ComparisonSummary
