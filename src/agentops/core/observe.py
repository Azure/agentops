"""Pure contracts for hosted Cockpit deployment and Observe telemetry."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentops.core.attribution import AttributionViewData


ScopeMode = Literal["projects", "foundry", "resource_group", "subscription"]
ObserveView = Literal[
    "overview", "agents", "models", "coverage", "tools", "runs", "cost"
]
CostBreakdown = Literal["agents", "tools", "runs"]
AllocationKey = Literal[
    "weighted_tokens",
    "total_tokens",
    "tool_invocations",
    "active_session_seconds",
    "credits",
    "credit_events",
]
RuntimeKind = Literal[
    "foundry_hosted",
    "foundry_prompt",
    "external_registered",
    "external_unregistered",
    "copilot_studio",
    "unknown",
]
CoverageState = Literal[
    "available",
    "inaccessible",
    "not_configured",
    "no_data",
    "not_reported",
    "partial",
    "ambiguous",
    "error",
    "protected_or_unavailable",
]

# This mirrors the bounded Observe query contract without coupling core to agent code.
MAX_ROWS_PER_QUERY = 500

_SUBSCRIPTION_RE = re.compile(r"^/subscriptions/[^/]+$", re.IGNORECASE)
_RESOURCE_GROUP_RE = re.compile(
    r"^/subscriptions/[^/]+/resourcegroups/[^/]+$", re.IGNORECASE
)
_FOUNDRY_RE = re.compile(
    r"^/subscriptions/[^/]+/resourcegroups/[^/]+/providers/"
    r"microsoft\.cognitiveservices/accounts/[^/]+$",
    re.IGNORECASE,
)
_PROJECT_RE = re.compile(
    r"^/subscriptions/[^/]+/resourcegroups/[^/]+/providers/"
    r"microsoft\.cognitiveservices/accounts/[^/]+/projects/[^/]+$",
    re.IGNORECASE,
)
_COST_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DECIMAL_STRING_RE = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")


def canonical_arm_id(value: str) -> str:
    """Return a stable ARM identifier suitable for equality and cache keys."""
    normalized = value.strip().rstrip("/")
    if not normalized.startswith("/"):
        raise ValueError("ARM resource ID must start with '/'")
    if "//" in normalized:
        raise ValueError("ARM resource ID must not contain empty segments")
    return normalized.lower()


class ContractModel(BaseModel):
    """Strict base for versioned public contracts."""

    model_config = ConfigDict(extra="forbid")


class ObserveScope(ContractModel):
    """Versioned, non-secret Azure discovery boundary."""

    version: Literal[1] = 1
    mode: ScopeMode
    root_resource_id: str | None = None
    project_resource_ids: list[str] = Field(default_factory=list)
    default_project_resource_id: str | None = None

    @field_validator(
        "root_resource_id", "default_project_resource_id", mode="before"
    )
    @classmethod
    def _canonicalize_optional_id(cls, value: Any) -> Any:
        return canonical_arm_id(value) if isinstance(value, str) else value

    @field_validator("project_resource_ids", mode="before")
    @classmethod
    def _canonicalize_projects(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return list(dict.fromkeys(canonical_arm_id(item) for item in value))

    @model_validator(mode="after")
    def _validate_mode_shape(self) -> "ObserveScope":
        if self.mode == "projects":
            if self.root_resource_id is not None:
                raise ValueError("projects mode forbids root_resource_id")
            if not self.project_resource_ids:
                raise ValueError("projects mode requires project_resource_ids")
        else:
            if self.root_resource_id is None:
                raise ValueError(f"{self.mode} mode requires root_resource_id")
            if self.project_resource_ids:
                raise ValueError(f"{self.mode} mode forbids project_resource_ids")

        for project_id in self.project_resource_ids:
            if not _PROJECT_RE.fullmatch(project_id):
                raise ValueError(f"invalid Foundry project resource ID: {project_id}")

        expected = {
            "foundry": _FOUNDRY_RE,
            "resource_group": _RESOURCE_GROUP_RE,
            "subscription": _SUBSCRIPTION_RE,
        }.get(self.mode)
        if expected is not None and not expected.fullmatch(self.root_resource_id or ""):
            raise ValueError(f"root_resource_id does not match {self.mode} mode")

        if self.default_project_resource_id is not None:
            if not _PROJECT_RE.fullmatch(self.default_project_resource_id):
                raise ValueError("default_project_resource_id is not a project ARM ID")
            if not self.contains(self.default_project_resource_id):
                raise ValueError("default project is outside the Observe scope")
        return self

    def contains(self, resource_id: str) -> bool:
        """Return whether *resource_id* is inside this configured boundary."""
        candidate = canonical_arm_id(resource_id)
        if self.mode == "projects":
            return candidate in self.project_resource_ids
        root = self.root_resource_id or ""
        return candidate == root or candidate.startswith(f"{root}/")


class DeploymentSelection(ContractModel):
    workspace: Path
    subscription_id: UUID
    resource_group: str
    location: str
    app_name: str
    tenant_id: UUID
    application_object_id: UUID
    client_id: UUID
    service_principal_object_id: UUID
    allowed_group_id: UUID | None = None
    scope: ObserveScope


class PlannedResource(ContractModel):
    resource_id: str
    resource_type: Literal[
        "app_service_plan", "web_app", "user_assigned_managed_identity"
    ]
    change_type: Literal["create", "modify", "no_change", "unknown"]
    location: str

    _canonicalize_resource_id = field_validator("resource_id", mode="before")(
        canonical_arm_id
    )


class RoleAssignmentPlan(ContractModel):
    assignment_id: UUID
    principal_id: UUID
    role: Literal["Reader", "Log Analytics Reader"]
    role_definition_id: str
    scope_resource_id: str
    reason: str

    _canonicalize_scope = field_validator("scope_resource_id", mode="before")(
        canonical_arm_id
    )


class FederatedCredentialPlan(ContractModel):
    application_object_id: UUID
    name: str
    issuer: str
    subject: UUID
    audiences: list[Literal["api://AzureADTokenExchange"]]
    action: Literal["create", "reuse", "conflict"]

    @model_validator(mode="after")
    def _single_audience(self) -> "FederatedCredentialPlan":
        if self.audiences != ["api://AzureADTokenExchange"]:
            raise ValueError("federated credential requires one token-exchange audience")
        return self


class DeploymentPreview(ContractModel):
    selection: DeploymentSelection
    resources: list[PlannedResource] = Field(default_factory=list)
    role_assignments: list[RoleAssignmentPlan] = Field(default_factory=list)
    federated_credential: FederatedCredentialPlan
    application_settings: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    infrastructure_preview: dict[str, Any] = Field(default_factory=dict)

    @field_validator("application_settings")
    @classmethod
    def _reject_secret_settings(cls, value: dict[str, str]) -> dict[str, str]:
        forbidden = ("secret", "password", "connection_string", "token", "private_key")
        bad = [key for key in value if any(term in key.lower() for term in forbidden)]
        if bad:
            raise ValueError(f"secret-bearing application settings are prohibited: {bad}")
        return value


class MutationRecord(ContractModel):
    target_resource_id: str
    action: Literal["create", "modify", "reuse", "assign", "federate", "deploy"]
    pre_existing: bool
    status: Literal["planned", "completed", "incomplete", "uncertain", "failed"]
    resulting_resource_id: str | None = None

    _canonicalize_target = field_validator("target_resource_id", mode="before")(
        canonical_arm_id
    )
    _canonicalize_result = field_validator("resulting_resource_id", mode="before")(
        lambda value: canonical_arm_id(value) if isinstance(value, str) else value
    )


class DeploymentFailure(ContractModel):
    stage: str
    summary: str
    completed_mutations: list[str] = Field(default_factory=list)
    incomplete_mutations: list[str] = Field(default_factory=list)
    uncertain_mutations: list[str] = Field(default_factory=list)
    local_rollbacks: list[str] = Field(default_factory=list)
    rollback_failures: list[str] = Field(default_factory=list)
    preserved_resource_ids: list[str] = Field(default_factory=list)
    usability: Literal["not_deployed", "unverified", "unusable", "partially_usable"]
    retry_guidance: str


class DeploymentJournal(ContractModel):
    version: Literal[1] = 1
    attempt_id: UUID
    selection_fingerprint: str
    last_completed_stage: (
        Literal[
            "validated",
            "previewed",
            "confirmed",
            "provisioned",
            "federated",
            "deployed",
            "verified",
        ]
        | None
    ) = None
    mutations: list[MutationRecord] = Field(default_factory=list)
    resource_ids: list[str] = Field(default_factory=list)
    initiated_by: str | None = None
    approval_method: Literal["interactive", "non_interactive"] | None = None
    approved_at: datetime | None = None
    updated_at: datetime
    failure: DeploymentFailure | None = None

    @field_validator("resource_ids", mode="before")
    @classmethod
    def _canonicalize_resources(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return list(dict.fromkeys(canonical_arm_id(item) for item in value))


class HostedCockpitDeployment(ContractModel):
    web_app_resource_id: str
    managed_identity_resource_id: str
    scope: ObserveScope
    app_url: str
    portal_url: str
    health: Literal["healthy", "auth_pending", "rbac_pending", "failed"]
    deployed_version: str

    _canonicalize_web_app = field_validator("web_app_resource_id", mode="before")(
        canonical_arm_id
    )
    _canonicalize_identity = field_validator(
        "managed_identity_resource_id", mode="before"
    )(canonical_arm_id)


class TelemetrySource(ContractModel):
    source_id: str
    resource_id: str
    workspace_id: str | None = None
    foundry_resource_id: str | None = None
    project_resource_ids: list[str] = Field(default_factory=list)
    state: Literal["available", "inaccessible", "not_configured", "not_found", "error"]
    reason: str | None = None
    last_query_duration_ms: int | None = Field(default=None, ge=0)

    _canonicalize_resource = field_validator("resource_id", mode="before")(
        canonical_arm_id
    )
    _canonicalize_foundry = field_validator("foundry_resource_id", mode="before")(
        lambda value: canonical_arm_id(value) if isinstance(value, str) else value
    )
    _canonicalize_source_projects = field_validator(
        "project_resource_ids", mode="before"
    )(lambda value: list(dict.fromkeys(canonical_arm_id(item) for item in value)))


class ResourceInventory(ContractModel):
    scope: ObserveScope
    foundry_resources: list[dict[str, Any]] = Field(default_factory=list)
    projects: list[dict[str, Any]] = Field(default_factory=list)
    telemetry_sources: list[TelemetrySource] = Field(default_factory=list)
    discovered_at: datetime
    expires_at: datetime
    partial_failures: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ordered_expiry(self) -> "ResourceInventory":
        if self.expires_at <= self.discovered_at:
            raise ValueError("inventory expiry must be after discovery")
        return self


class ObserveFilterState(ContractModel):
    foundry_resource_id: str | None = None
    project_resource_id: str | None = None
    agent_id: str | None = None
    model: str | None = None
    tool_name: str | None = None
    run_key: str | None = None
    cost_period_id: str | None = None
    cost_breakdown: CostBreakdown | None = None
    cost_component_id: str | None = None
    cost_agent_key: str | None = None
    user_filter_token: str | None = None
    department_filter_token: str | None = None
    start: datetime
    end: datetime

    _canonicalize_foundry_filter = field_validator(
        "foundry_resource_id", mode="before"
    )(lambda value: canonical_arm_id(value) if isinstance(value, str) else value)
    _canonicalize_project_filter = field_validator(
        "project_resource_id", mode="before"
    )(lambda value: canonical_arm_id(value) if isinstance(value, str) else value)

    @field_validator("tool_name", "run_key")
    @classmethod
    def _trim_narrowing_filter(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Observe narrowing filters must not be empty")
        if len(value) > 256:
            raise ValueError("Observe narrowing filters must be at most 256 characters")
        return value

    @field_validator("cost_period_id", "cost_component_id")
    @classmethod
    def _validate_cost_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) > 64:
            raise ValueError("cost identifiers must be at most 64 characters")
        if not _COST_IDENTIFIER_RE.fullmatch(value):
            raise ValueError(
                "cost identifiers must start with an alphanumeric character and "
                "contain only letters, numbers, '.', '_', or '-'"
            )
        return value

    @field_validator("cost_agent_key")
    @classmethod
    def _validate_cost_agent_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("cost_agent_key must not be empty")
        if len(value) > 512:
            raise ValueError("cost_agent_key must be at most 512 characters")
        return value

    @field_validator("user_filter_token", "department_filter_token")
    @classmethod
    def _validate_attribution_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or len(value) > 1024:
            raise ValueError("attribution filter tokens must contain 1-1024 characters")
        if re.fullmatch(r"[A-Za-z0-9._~-]+", value) is None:
            raise ValueError("attribution filter tokens must be URL-safe opaque values")
        return value

    @model_validator(mode="after")
    def _ordered_range(self) -> "ObserveFilterState":
        if self.start >= self.end:
            raise ValueError("Observe start must be before end")
        return self

    def validate_scope(self, scope: ObserveScope) -> None:
        for resource_id in (self.foundry_resource_id, self.project_resource_id):
            if resource_id is not None and not scope.contains(resource_id):
                raise ValueError(f"filter resource is outside Observe scope: {resource_id}")


class ObserveQueryRequest(ContractModel):
    view: ObserveView
    filters: ObserveFilterState
    refresh: bool = False


class AgentDetailRequest(ContractModel):
    agent_key: str = Field(min_length=1)
    filters: ObserveFilterState
    refresh: bool = False


class TraceContentRequest(ContractModel):
    source_resource_id: str
    trace_id: str = Field(min_length=1)
    span_id: str | None = None

    _canonicalize_source = field_validator("source_resource_id", mode="before")(
        canonical_arm_id
    )


class QueryDiagnostics(ContractModel):
    started_at: datetime
    completed_at: datetime
    duration_ms: int = Field(ge=0)
    source_count: int = Field(ge=0, le=10)
    successful_sources: int = Field(ge=0)
    partial_sources: int = Field(ge=0)
    failed_sources: int = Field(ge=0)
    cache_status: Literal["hit", "miss", "bypass"]

    @model_validator(mode="after")
    def _validate_source_counts(self) -> "QueryDiagnostics":
        if (
            self.successful_sources + self.partial_sources + self.failed_sources
            > self.source_count
        ):
            raise ValueError("source result counts exceed source_count")
        return self


class ObservedAgent(ContractModel):
    source_id: str = Field(min_length=1)
    key: str
    agent_id: str | None = None
    agent_name: str | None = None
    project_resource_id: str | None = None
    foundry_resource_id: str | None = None
    source_kind: RuntimeKind
    model: str | None = None
    last_seen: datetime
    invocations: int = Field(ge=0)
    failures: int = Field(ge=0)
    p95_latency_ms: float | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _failures_not_greater_than_invocations(self) -> "ObservedAgent":
        if self.failures > self.invocations:
            raise ValueError("failures cannot exceed invocations")
        return self


class ModelUsage(ContractModel):
    project_resource_id: str | None = None
    agent_id: str | None = None
    deployment: str | None = None
    model: str | None = None
    requests: int = Field(ge=0)
    failures: int = Field(ge=0)
    p95_latency_ms: float | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    additional_token_classes: dict[str, int] = Field(default_factory=dict)
    additional_token_classes_truncated: bool = False
    partially_reported_token_classes: tuple[
        Literal["cache-read", "cache-write", "reasoning"], ...
    ] = ()
    token_classes_partial: bool = False
    last_seen: datetime | None = None

    @field_validator("additional_token_classes")
    @classmethod
    def _validate_additional_token_classes(cls, value: dict[str, int]) -> dict[str, int]:
        if len(value) > 5:
            raise ValueError("additional_token_classes cannot contain more than five entries")
        if any(count < 0 for count in value.values()):
            raise ValueError("additional_token_classes values must be non-negative")
        return value


class ObservedTool(ContractModel):
    source_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    agent_key: str = Field(min_length=1)
    agent_id: str | None = None
    agent_name: str | None = None
    project_resource_id: str | None = None
    foundry_resource_id: str | None = None
    source_kind: RuntimeKind
    last_seen: datetime
    invocations: int = Field(ge=0)
    failures: int = Field(ge=0)
    p95_latency_ms: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _failures_not_greater_than_invocations(self) -> "ObservedTool":
        if self.failures > self.invocations:
            raise ValueError("failures cannot exceed invocations")
        return self


class ObservedRun(ContractModel):
    source_id: str = Field(min_length=1)
    run_key: str = Field(min_length=1)
    run_key_kind: Literal["conversation", "trace"]
    agent_key: str = Field(min_length=1)
    agent_id: str | None = None
    agent_name: str | None = None
    project_resource_id: str | None = None
    foundry_resource_id: str | None = None
    source_kind: RuntimeKind
    started_at: datetime
    last_activity_at: datetime
    duration_ms: float | None = Field(default=None, ge=0)
    status: Literal["succeeded", "failed", "in_progress"]
    turns: int = Field(ge=1)
    failed_turns: int = Field(ge=0)
    tool_invocations: int = Field(ge=0)
    tool_failures: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    credits: str | None = None
    credit_events: int | None = Field(default=None, ge=0)

    @field_validator("credits")
    @classmethod
    def _validate_credits(cls, value: str | None) -> str | None:
        if value is not None and not _DECIMAL_STRING_RE.fullmatch(value):
            raise ValueError("credits must be a non-negative decimal string")
        return value

    @model_validator(mode="after")
    def _validate_run(self) -> "ObservedRun":
        if self.last_activity_at < self.started_at:
            raise ValueError("last_activity_at cannot precede started_at")
        if self.failed_turns > self.turns:
            raise ValueError("failed_turns cannot exceed turns")
        if self.tool_failures > self.tool_invocations:
            raise ValueError("tool_failures cannot exceed tool_invocations")
        if self.status == "succeeded" and (
            self.failed_turns > 0 or self.tool_failures > 0
        ):
            raise ValueError("succeeded runs cannot contain failures")
        return self


class ResultBounds(ContractModel):
    rows_shown: int = Field(ge=0, le=MAX_ROWS_PER_QUERY)
    rows_total_in_scope: int | None = Field(default=None, ge=0)
    truncated: bool = False

    @model_validator(mode="after")
    def _validate_bounds(self) -> "ResultBounds":
        if (
            self.rows_total_in_scope is not None
            and self.rows_total_in_scope < self.rows_shown
        ):
            raise ValueError("rows_total_in_scope cannot be less than rows_shown")
        if self.truncated and self.rows_shown != MAX_ROWS_PER_QUERY:
            raise ValueError(
                "truncated results must show exactly MAX_ROWS_PER_QUERY rows"
            )
        return self


class CoverageResult(ContractModel):
    source_id: str = Field(min_length=1)
    dimension: Literal[
        "resource_access",
        "telemetry_connection",
        "recent_traces",
        "agent_attribution",
        "model_attribution",
        "token_usage",
        "trace_correlation",
        "protected_content",
        "tool_attribution",
        "run_correlation",
        "cost_attribution",
        "user_attribution",
    ]
    state: CoverageState
    reason: str
    next_action: str
    refreshed_at: datetime
    component_id: str | None = None
    cost_breakdown: CostBreakdown | None = None
    allocation_key: AllocationKey | None = None
    metric: Literal["usage", "cost"] | None = None
    attribution_level: Literal["department", "user"] | None = None
    eligible_records: int | None = Field(default=None, ge=0)
    identified_records: int | None = Field(default=None, ge=0)
    mapped_records: int | None = Field(default=None, ge=0)
    unattributed_records: int | None = Field(default=None, ge=0)
    ambiguous_records: int | None = Field(default=None, ge=0)
    returned_records: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_attribution_details(self) -> "CoverageResult":
        detail_fields = {
            "metric",
            "attribution_level",
            "eligible_records",
            "identified_records",
            "mapped_records",
            "unattributed_records",
            "ambiguous_records",
            "returned_records",
        }
        if self.dimension == "user_attribution":
            if not detail_fields.issubset(self.model_fields_set):
                raise ValueError(
                    "user_attribution coverage requires attribution detail fields"
                )
            if self.metric is None or self.attribution_level is None:
                raise ValueError(
                    "user_attribution coverage requires metric and attribution_level"
                )
        elif any(getattr(self, field) is not None for field in detail_fields):
            raise ValueError(
                "attribution detail fields are valid only for user_attribution"
            )
        return self


class UserAttributionCoverage(CoverageResult):
    """Strict per-source coverage for the attribution endpoint."""

    dimension: Literal["user_attribution"]
    metric: Literal["usage", "cost"]
    attribution_level: Literal["department", "user"]
    eligible_records: int | None = Field(ge=0)
    identified_records: int | None = Field(ge=0)
    mapped_records: int | None = Field(ge=0)
    unattributed_records: int | None = Field(ge=0)
    ambiguous_records: int | None = Field(ge=0)
    returned_records: int | None = Field(ge=0)


class QuerySourceFailure(ContractModel):
    source_id: str = Field(min_length=1)
    status: Literal["success", "partial", "timeout", "inaccessible", "error"]
    reason: str = Field(min_length=1)
    next_action: str = Field(min_length=1)


class AttributionQueryRequest(ContractModel):
    metric: Literal["usage", "cost"]
    group_by: Literal["department", "user"]
    filters: ObserveFilterState
    refresh: bool = False

    @model_validator(mode="after")
    def _require_cost_pool(self) -> "AttributionQueryRequest":
        if self.metric == "cost" and (
            self.filters.cost_period_id is None
            or self.filters.cost_component_id is None
        ):
            raise ValueError(
                "cost attribution requires cost_period_id and cost_component_id"
            )
        return self


class AttributionResponse(ContractModel):
    data: AttributionViewData
    coverage: list[UserAttributionCoverage]
    partial_failures: list[QuerySourceFailure]
    diagnostics: QueryDiagnostics
    refreshed_at: datetime
    cache_status: Literal["hit", "miss", "bypass"]
    bounds: ResultBounds

    @model_validator(mode="after")
    def _validate_response(self) -> "AttributionResponse":
        if self.cache_status != self.diagnostics.cache_status:
            raise ValueError("response and diagnostics cache_status must match")
        if self.data.access_boundary == "delegated" and self.cache_status != "bypass":
            raise ValueError("delegated attribution responses must bypass caches")
        if self.bounds.rows_shown != len(self.data.rows):
            raise ValueError("bounds rows_shown must match returned row count")
        if self.bounds.truncated:
            other = [row for row in self.data.rows if row.kind == "other_users"]
            if self.data.group_by != "user" or len(other) != 1:
                raise ValueError(
                    "truncated user attribution requires exactly one Other users row"
                )
            if other[0].member_count != self.data.summary.omitted_users:
                raise ValueError(
                    "Other users member_count must match summary omitted_users"
                )
        return self


class GenerativeAIContent(ContractModel):
    trace_id: str
    span_id: str | None = None
    source_resource_id: str
    protection_state: Literal[
        "available", "protected_or_unavailable", "not_configured"
    ]
    input_messages: Any | None = None
    output_messages: Any | None = None
    system_instructions: Any | None = None
    tool_content: Any | None = None
    evaluation_explanation: Any | None = None

    _canonicalize_content_source = field_validator(
        "source_resource_id", mode="before"
    )(canonical_arm_id)

    @model_validator(mode="after")
    def _hide_unavailable_content(self) -> "GenerativeAIContent":
        raw = (
            self.input_messages,
            self.output_messages,
            self.system_instructions,
            self.tool_content,
            self.evaluation_explanation,
        )
        if self.protection_state != "available" and any(item is not None for item in raw):
            raise ValueError("unavailable protected content must omit raw fields")
        return self
