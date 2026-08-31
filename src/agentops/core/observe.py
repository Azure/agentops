"""Pure contracts for hosted Cockpit deployment and Observe telemetry."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentops.core.attribution import AttributionViewData


ScopeMode = Literal["projects", "foundry", "resource_group", "subscription"]
ObserveView = Literal[
    "overview", "agents", "models", "coverage", "tools", "runs", "cost"
]
DrilldownView = Literal["agents", "models", "tools", "runs"]
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
EntityFamily = Literal["Runs", "Agents", "Models", "Tools"]
SummaryTone = Literal["ok", "warn", "crit", "info", "muted"]
ENTITY_SUMMARY_FAMILY_ORDER: tuple[EntityFamily, ...] = (
    "Runs",
    "Agents",
    "Models",
    "Tools",
)
UNATTRIBUTED_MODEL = "(model not reported)"

# Aggregate queries can retain enough rows for large fleets while API responses
# remain independently page-bounded.
MAX_ROWS_PER_QUERY = 5000
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100

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


class SummaryFigure(ContractModel):
    """One entity-qualified headline value in an Overview summary."""

    label: str = Field(min_length=1)
    value: int | float | None
    unit: str | None = None
    tone: SummaryTone = "info"

    @field_validator("label", "unit")
    @classmethod
    def _strip_copy(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("summary figure copy must not be blank")
        return stripped


class EntitySummary(ContractModel):
    """Overview figures owned by one explicit telemetry entity family."""

    entity_family: EntityFamily
    label: str = Field(min_length=1)
    figures: list[SummaryFigure] = Field(default_factory=list)
    coverage_state: CoverageState

    @model_validator(mode="after")
    def _bind_figures_to_family(self) -> "EntitySummary":
        if self.label.strip() != self.entity_family:
            raise ValueError("summary label must match its entity family")
        entity_noun = self.entity_family[:-1].lower()
        for figure in self.figures:
            if re.search(rf"\b{re.escape(entity_noun)}s?\b", figure.label, re.I) is None:
                raise ValueError(
                    f"summary figure label must name its {entity_noun} entity"
                )
        return self


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
    foundry_resource_id: str | tuple[str, ...] | None = None
    project_resource_id: str | tuple[str, ...] | None = None
    agent_id: str | tuple[str, ...] | None = None
    model: str | tuple[str, ...] | None = None
    tool_name: str | tuple[str, ...] | None = None
    run_key: str | tuple[str, ...] | None = None
    cost_period_id: str | None = None
    cost_breakdown: CostBreakdown | None = None
    cost_component_id: str | None = None
    cost_agent_key: str | None = None
    user_filter_token: str | None = None
    department_filter_token: str | None = None
    start: datetime
    end: datetime

    @field_validator(
        "foundry_resource_id",
        "project_resource_id",
        "agent_id",
        "model",
        "tool_name",
        "run_key",
        mode="before",
    )
    @classmethod
    def _normalize_scope_filter(
        cls, value: str | Sequence[str] | None, info: Any
    ) -> str | tuple[str, ...] | None:
        if value is None:
            return None
        values = [value] if isinstance(value, str) else list(value)
        normalized: list[str] = []
        for item in values:
            item = item.strip()
            if not item:
                raise ValueError("Observe narrowing filters must not be empty")
            if len(item) > 256:
                raise ValueError(
                    "Observe narrowing filters must be at most 256 characters"
                )
            if info.field_name in {"foundry_resource_id", "project_resource_id"}:
                item = canonical_arm_id(item)
            if item not in normalized:
                normalized.append(item)
        if not normalized:
            return None
        return normalized[0] if len(normalized) == 1 else tuple(normalized)

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
        for selected in (self.foundry_resource_id, self.project_resource_id):
            values = (selected,) if isinstance(selected, str) else selected or ()
            for resource_id in values:
                if not scope.contains(resource_id):
                    raise ValueError(
                        f"filter resource is outside Observe scope: {resource_id}"
                    )


WindowPreset = Literal["30m", "1h", "6h", "12h", "1d", "3d", "7d", "30d"]


class PresetWindowSelection(ContractModel):
    kind: Literal["preset"]
    preset: WindowPreset = "7d"
    timezone_label: str = "UTC"


class CustomWindowSelection(ContractModel):
    kind: Literal["custom"]
    start: datetime
    end: datetime
    timezone_label: str = "UTC"

    @model_validator(mode="after")
    def _ordered_window(self) -> "CustomWindowSelection":
        if self.end <= self.start:
            raise ValueError("custom window end must be after start")
        return self


WindowSelection = Annotated[
    PresetWindowSelection | CustomWindowSelection,
    Field(discriminator="kind"),
]


class ObserveQueryRequest(ContractModel):
    view: ObserveView
    filters: ObserveFilterState
    window: WindowSelection | None = None
    refresh: bool = False
    page: int = Field(default=1, ge=1, le=1000)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    search: str | None = Field(default=None, max_length=200)
    sort_by: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    sort_direction: Literal["asc", "desc"] = "desc"

    @field_validator("search")
    @classmethod
    def _normalize_search(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class AgentDetailRequest(ContractModel):
    agent_key: str = Field(min_length=1)
    source_id: str | None = Field(default=None, min_length=1, max_length=2048)
    project_resource_id: str | None = None
    filters: ObserveFilterState
    refresh: bool = False

    _canonicalize_project = field_validator("project_resource_id", mode="before")(
        lambda value: canonical_arm_id(value) if isinstance(value, str) else value
    )


class ObserveDrilldownSelector(ContractModel):
    source_id: str = Field(min_length=1, max_length=2048)
    project_resource_id: str | None
    agent_key: str | None = Field(default=None, min_length=1, max_length=512)
    model: str | None = Field(default=None, min_length=1, max_length=512)
    deployment: str | None = Field(default=None, min_length=1, max_length=512)
    tool_name: str | None = Field(default=None, min_length=1, max_length=512)
    run_key: str | None = Field(default=None, min_length=1, max_length=512)

    _canonicalize_project = field_validator("project_resource_id", mode="before")(
        lambda value: canonical_arm_id(value) if isinstance(value, str) else value
    )


class ObserveDrilldownRequest(ContractModel):
    view: DrilldownView
    filters: ObserveFilterState
    selector: ObserveDrilldownSelector
    limit: int = Field(default=50, ge=1, le=100)

    @model_validator(mode="after")
    def _validate_selector_for_view(self) -> "ObserveDrilldownRequest":
        selector = self.selector
        valid = {
            "agents": selector.agent_key is not None,
            "models": selector.model is not None or selector.deployment is not None,
            "tools": selector.tool_name is not None,
            "runs": selector.run_key is not None,
        }[self.view]
        if not valid:
            raise ValueError(f"{self.view} drill-through requires its row identifier")
        return self


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
    discovery_duration_ms: int = Field(default=0, ge=0)
    query_duration_ms: int = Field(default=0, ge=0)
    normalization_duration_ms: int = Field(default=0, ge=0)
    source_count: int = Field(ge=0)
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


CostCompleteness = Literal["complete", "partial", "not_priced"]


class CostEstimate(ContractModel):
    """Published-list-price estimate, deliberately separate from billed allocation."""

    amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    completeness: CostCompleteness
    reason: str | None = None
    excluded_components: list[str] = Field(default_factory=list)
    unpriced_run_count: int | None = Field(default=None, ge=0)
    covered_run_count: int | None = Field(default=None, ge=0)
    scope_run_count: int | None = Field(default=None, ge=0)
    price_reference_version: str | None = None
    price_reference_effective_date: date | None = None
    is_stale: bool = False
    reference_age_days: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_estimate(self) -> "CostEstimate":
        if (self.amount is None) != (self.currency is None):
            raise ValueError("amount and currency must be reported together")
        if self.completeness == "not_priced":
            if self.amount is not None:
                raise ValueError("not_priced estimates must not report an amount")
            if not self.reason:
                raise ValueError("not_priced estimates require a reason")
        elif self.amount is None:
            raise ValueError("complete and partial estimates require an amount")
        if self.amount is not None and (
            self.price_reference_version is None
            or self.price_reference_effective_date is None
        ):
            raise ValueError("priced estimates require price-reference provenance")
        if self.completeness == "complete":
            if self.excluded_components or (self.unpriced_run_count or 0) > 0:
                raise ValueError("complete estimates cannot omit components or runs")
            if (
                self.covered_run_count is not None
                and self.scope_run_count is not None
                and self.covered_run_count != self.scope_run_count
            ):
                raise ValueError(
                    "complete estimates require covered_run_count to equal scope_run_count"
                )
        if self.completeness == "partial" and not (
            self.excluded_components
            or (self.unpriced_run_count or 0) > 0
            or (
                self.covered_run_count is not None
                and self.scope_run_count is not None
                and self.covered_run_count != self.scope_run_count
            )
        ):
            raise ValueError("partial estimates must state what was omitted")
        count_fields = (
            self.unpriced_run_count,
            self.covered_run_count,
            self.scope_run_count,
        )
        if any(value is not None for value in count_fields) and any(
            value is None for value in count_fields
        ):
            raise ValueError("group estimate run counts must be reported together")
        return self


class RunModelUsage(ContractModel):
    """Five observed token classes attributed to one model."""

    model: str = Field(min_length=1)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    run_count: int | None = Field(default=None, ge=0)


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
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    model_usage: list[RunModelUsage] = Field(default_factory=list)
    scope_run_count: int | None = Field(default=None, ge=0)
    estimated_cost: CostEstimate | None = None

    @model_validator(mode="after")
    def _failures_not_greater_than_invocations(self) -> "ObservedAgent":
        if self.failures > self.invocations:
            raise ValueError("failures cannot exceed invocations")
        return self


class ModelUsage(ContractModel):
    source_id: str | None = Field(default=None, min_length=1)
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
    scope_run_count: int | None = Field(default=None, ge=0)
    estimated_cost: CostEstimate | None = None

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
    model_usage: list[RunModelUsage] = Field(default_factory=list)
    model_usage_truncated: bool = False
    estimated_cost: CostEstimate | None = None

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
        models = [entry.model for entry in self.model_usage]
        if len(models) != len(set(models)):
            raise ValueError("model_usage must contain at most one entry per model")
        if self.model_usage and not self.model_usage_truncated:
            for field in (
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
                "reasoning_tokens",
            ):
                total = getattr(self, field)
                if total is None:
                    continue
                attributed = [getattr(entry, field) for entry in self.model_usage]
                if any(value is not None for value in attributed) and sum(
                    value or 0 for value in attributed
                ) != total:
                    raise ValueError(
                        f"model_usage {field} must reconcile to the run-level total"
                    )
        return self


class ResultBounds(ContractModel):
    rows_shown: int = Field(ge=0, le=MAX_ROWS_PER_QUERY)
    rows_total_in_scope: int | None = Field(default=None, ge=0)
    truncated: bool = False
    page: int | None = Field(default=None, ge=1)
    page_size: int | None = Field(default=None, ge=1, le=MAX_PAGE_SIZE)
    has_previous_page: bool = False
    has_next_page: bool = False

    @model_validator(mode="after")
    def _validate_bounds(self) -> "ResultBounds":
        if (
            self.rows_total_in_scope is not None
            and self.rows_total_in_scope < self.rows_shown
        ):
            raise ValueError("rows_total_in_scope cannot be less than rows_shown")
        if (self.page is None) != (self.page_size is None):
            raise ValueError("page and page_size must be reported together")
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


# ---------------------------------------------------------------------------
# Table column declarations (data-model 7)
# ---------------------------------------------------------------------------

_COLUMN_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class TableColumn(ContractModel):
    """A single displayable column, declared once and consumed by every reader.

    The ``identifier`` is the stable name. It is never displayed, and it is the
    only key used for sorting, for the rendered data attribute, and for the
    client-side lookup. ``label`` is the displayed prose and may be reworded at
    any time without changing behaviour -- that separation is the whole point of
    this contract (FR-030).
    """

    identifier: str
    label: str
    sort_key: str | None = None
    help_text: str | None = None
    priority: int = 0

    @field_validator("identifier")
    @classmethod
    def _stable_identifier(cls, value: str) -> str:
        if not _COLUMN_ID_RE.match(value):
            raise ValueError(
                "column identifier must be lowercase snake_case and start with a letter"
            )
        return value

    @field_validator("label")
    @classmethod
    def _non_empty_label(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("column label must not be empty")
        return value


def validate_column_declarations(
    columns: "Sequence[TableColumn]",
) -> tuple[TableColumn, ...]:
    """Return ``columns`` unchanged after proving the identifiers are unique.

    A duplicate identifier would make the sort lookup ambiguous, which is the
    exact failure this contract exists to prevent, so it is rejected at
    declaration time rather than discovered at click time.
    """
    ordered = tuple(columns)
    seen: set[str] = set()
    for column in ordered:
        if column.identifier in seen:
            raise ValueError(f"duplicate column identifier: {column.identifier}")
        seen.add(column.identifier)
    return ordered


# ---------------------------------------------------------------------------
# Scope filter dimensions and bounded option sets (data-model 2)
# ---------------------------------------------------------------------------

ScopeDimension = Literal[
    "foundry_resource",
    "project",
    "agent",
    "model",
    "tool",
    "run_key",
]

# The cascade is strictly left to right in this order. A dimension's options are
# a function of every dimension before it and of none after it, so the order is
# the contract rather than an incidental detail of the tuple.
SCOPE_DIMENSION_ORDER: tuple[ScopeDimension, ...] = (
    "foundry_resource",
    "project",
    "agent",
    "model",
    "tool",
    "run_key",
)

# Enumerating a facet is a convenience, not a census. Past roughly this many
# entries a picker stops being easier than typing, so the set is bounded and the
# operator is told it was bounded rather than shown a list that quietly lies.
MAX_SCOPE_OPTIONS = 50

_MAX_SCOPE_LABEL_LENGTH = 256
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def scope_cascade_position(dimension: ScopeDimension) -> int:
    """Return the fixed left-to-right position of ``dimension``."""
    return SCOPE_DIMENSION_ORDER.index(dimension)


def scope_dimensions_to_the_right(
    dimension: ScopeDimension,
) -> tuple[ScopeDimension, ...]:
    """Return the dimensions whose option sets a change to ``dimension`` voids.

    Selecting a value narrows everything downstream of it, so those option sets
    were computed against a scope that no longer holds. Callers use this to drop
    them deliberately instead of leaving a stale list on screen.
    """
    return SCOPE_DIMENSION_ORDER[scope_cascade_position(dimension) + 1 :]


def _clean_scope_text(value: str, *, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    if len(value) > _MAX_SCOPE_LABEL_LENGTH:
        raise ValueError(
            f"{field} must be at most {_MAX_SCOPE_LABEL_LENGTH} characters"
        )
    # Option labels are identifiers and names drawn from telemetry metadata.
    # Control characters are not part of that vocabulary, and rejecting them
    # here keeps anything unexpected out of the rendered document.
    if _CONTROL_CHARS_RE.search(value):
        raise ValueError(f"{field} must not contain control characters")
    return value


class ScopeFilterOption(ContractModel):
    """One selectable value within a scope dimension.

    ``value`` is what gets applied to the query; ``label`` is what the operator
    recognises. They are frequently the same string, but keeping them separate
    is what lets a resource be shown by name and filtered by ARM id.
    """

    value: str
    label: str
    dimension: ScopeDimension

    @field_validator("value", "label")
    @classmethod
    def _clean(cls, value: str, info: Any) -> str:
        return _clean_scope_text(value, field=info.field_name)


class ScopeFilterOptionSet(ContractModel):
    """The bounded set of values offered for one dimension.

    Derived from the scope, the resolved window, and the selections of every
    dimension to the left -- never from those to the right.
    """

    dimension: ScopeDimension
    options: tuple[ScopeFilterOption, ...] = ()
    truncated: bool = False
    total_observed: int | None = Field(default=None, ge=0)
    coverage_state: CoverageState = "available"

    @model_validator(mode="after")
    def _validate_set(self) -> "ScopeFilterOptionSet":
        if len(self.options) > MAX_SCOPE_OPTIONS:
            raise ValueError(
                f"scope option sets are bounded to {MAX_SCOPE_OPTIONS} entries; "
                "producers must truncate and set 'truncated' instead"
            )
        seen: set[str] = set()
        for option in self.options:
            if option.dimension != self.dimension:
                raise ValueError(
                    f"option for dimension {option.dimension!r} cannot appear in "
                    f"the {self.dimension!r} option set"
                )
            if option.value in seen:
                raise ValueError(f"duplicate scope option value: {option.value}")
            seen.add(option.value)
        if self.total_observed is not None and self.total_observed < len(self.options):
            raise ValueError(
                "total_observed cannot be smaller than the number of options returned"
            )
        # A set that reports a total it did not reach is by definition partial,
        # so the two fields cannot disagree about whether anything was cut.
        if (
            self.total_observed is not None
            and self.total_observed > len(self.options)
            and not self.truncated
        ):
            raise ValueError(
                "an option set that returned fewer options than were observed "
                "must report truncated=True"
            )
        return self

    @property
    def cascade_position(self) -> int:
        return scope_cascade_position(self.dimension)


class ScopeFilterDimension(ContractModel):
    """A dimension together with whatever the operator has applied to it.

    An empty ``selected_values`` means unconstrained, not "nothing matches" --
    that distinction is the difference between a filter that is off and a filter
    that excludes everything.
    """

    dimension: ScopeDimension
    selected_values: tuple[str, ...] = ()

    @field_validator("selected_values")
    @classmethod
    def _clean_selections(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = _clean_scope_text(value, field="selected value")
            if text in seen:
                raise ValueError(f"duplicate scope selection: {text}")
            seen.add(text)
            cleaned.append(text)
        return tuple(cleaned)

    @property
    def cascade_position(self) -> int:
        return scope_cascade_position(self.dimension)


class ScopeOptionsRequest(ContractModel):
    """Request body for one deferred, read-only scope facet lookup."""

    dimension: ScopeDimension
    filters: ObserveFilterState
    window: WindowSelection | None = None
    search: str | None = Field(default=None, max_length=128)
    limit: int = Field(default=MAX_SCOPE_OPTIONS, ge=1, le=MAX_SCOPE_OPTIONS)
    refresh: bool = False

    @field_validator("search")
    @classmethod
    def _normalize_search(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None