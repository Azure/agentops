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


# ---------------------------------------------------------------------------
# Run configuration — orthogonal target / hosting / execution_mode model
# ---------------------------------------------------------------------------

TargetType = Literal["agent", "model"]
Hosting = Literal["local", "foundry", "aks", "containerapps"]
ExecutionMode = Literal["local", "remote"]
AgentMode = Literal["prompt", "hosted"]
Framework = Literal["agent_framework", "langgraph", "custom"]
EndpointKind = Literal["foundry_agent", "http"]


class TargetEndpointConfig(BaseModel):
    """Remote endpoint configuration for the evaluation target."""

    kind: EndpointKind

    # Foundry agent fields
    agent_id: Optional[str] = None
    project_endpoint: Optional[str] = None
    project_endpoint_env: Optional[str] = None
    api_version: Optional[str] = None
    poll_interval_seconds: Optional[float] = None
    max_poll_attempts: Optional[int] = None
    model: Optional[str] = None

    # HTTP fields
    url: Optional[str] = None
    url_env: Optional[str] = None
    request_field: Optional[str] = None
    response_field: Optional[str] = None
    headers: Dict[str, str] = Field(default_factory=dict)
    auth_header_env: Optional[str] = None
    tool_calls_field: Optional[str] = None
    extra_fields: Optional[List[str]] = None

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
                "endpoint.model must be replaced with a real Foundry model deployment name"
            )
        return normalized

    @model_validator(mode="after")
    def _validate_endpoint_fields(self) -> "TargetEndpointConfig":
        if self.kind == "foundry_agent":
            if self.max_poll_attempts is not None and self.max_poll_attempts <= 0:
                raise ValueError("endpoint.max_poll_attempts must be > 0")
            if (
                self.poll_interval_seconds is not None
                and self.poll_interval_seconds <= 0
            ):
                raise ValueError("endpoint.poll_interval_seconds must be > 0")
        elif self.kind == "http":
            if not self.url and not self.url_env:
                raise ValueError(
                    "HTTP endpoint requires 'endpoint.url' or 'endpoint.url_env'"
                )
        return self


class LocalAdapterConfig(BaseModel):
    """Configuration for local adapter execution.

    Exactly one of ``adapter`` (subprocess command) or ``callable``
    (``module:function`` path) must be provided.
    """

    adapter: Optional[str] = None
    callable: Optional[str] = None

    @field_validator("adapter")
    @classmethod
    def _adapter_non_empty(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("local.adapter must be non-empty")
        return value

    @field_validator("callable")
    @classmethod
    def _callable_format(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if not value.strip():
            raise ValueError("local.callable must be non-empty")
        if ":" not in value:
            raise ValueError(
                "local.callable must use 'module:function' format "
                "(e.g. 'my_workflow:run_evaluation')"
            )
        module_part, _, func_part = value.partition(":")
        if not module_part.strip() or not func_part.strip():
            raise ValueError(
                "local.callable must use 'module:function' format "
                "(e.g. 'my_workflow:run_evaluation')"
            )
        return value

    @model_validator(mode="after")
    def _require_adapter_xor_callable(self) -> "LocalAdapterConfig":
        has_adapter = self.adapter is not None
        has_callable = self.callable is not None
        if has_adapter and has_callable:
            raise ValueError(
                "local config must specify either 'adapter' or 'callable', not both"
            )
        if not has_adapter and not has_callable:
            raise ValueError(
                "local config must specify either 'adapter' (subprocess command) "
                "or 'callable' (module:function path)"
            )
        return self


class TargetConfig(BaseModel):
    """Defines what is being evaluated and how the toolkit interacts with it."""

    type: TargetType
    hosting: Hosting
    execution_mode: ExecutionMode
    agent_mode: Optional[AgentMode] = None
    framework: Optional[Framework] = None
    endpoint: Optional[TargetEndpointConfig] = None
    local: Optional[LocalAdapterConfig] = None

    @model_validator(mode="after")
    def _validate_target(self) -> "TargetConfig":
        if self.agent_mode is not None and self.hosting != "foundry":
            raise ValueError(
                "target.agent_mode is only valid when hosting is 'foundry'"
            )
        if self.framework is not None and self.type != "agent":
            raise ValueError(
                "target.framework is only valid when type is 'agent'"
            )
        if self.execution_mode == "remote":
            if self.endpoint is None:
                raise ValueError(
                    "target.endpoint is required when execution_mode is 'remote'"
                )
        if self.execution_mode == "local":
            if self.local is None:
                raise ValueError(
                    "target.local is required when execution_mode is 'local'"
                )
        return self


class BundleRef(BaseModel):
    name: Optional[str] = None
    path: Optional[Path] = None

    @model_validator(mode="after")
    def _require_name_or_path(self) -> "BundleRef":
        if not self.name and not self.path:
            raise ValueError("bundle requires 'name' or 'path'")
        return self


class DatasetRef(BaseModel):
    name: Optional[str] = None
    path: Optional[Path] = None

    @model_validator(mode="after")
    def _require_name_or_path(self) -> "DatasetRef":
        if not self.name and not self.path:
            raise ValueError("dataset requires 'name' or 'path'")
        return self


class ExecutionConfig(BaseModel):
    concurrency: int = 1
    timeout_seconds: int = 300


class RunMetadata(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class OutputConfig(BaseModel):
    path: Optional[Path] = None
    write_report: bool = True
    publish_foundry_evaluation: bool = False
    fail_on_foundry_publish_error: bool = False


class RunConfig(BaseModel):
    version: int
    run: Optional[RunMetadata] = None
    target: TargetConfig
    bundle: BundleRef
    dataset: DatasetRef
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)


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
    input: Optional[str] = None
    response: Optional[str] = None
    context: Optional[str] = None
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
