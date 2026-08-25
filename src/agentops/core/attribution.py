"""Pure contracts and deterministic helpers for privacy-safe attribution."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Mapping
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)


MAX_ATTRIBUTION_CONFIG_BYTES = 64 * 1024
MAX_ATTRIBUTION_DEPARTMENTS = 100
MAX_ATTRIBUTION_USER_KEYS = 500
MAX_ATTRIBUTION_GROUP_IDS = 100
MAX_ATTRIBUTION_ROWS = 500

PseudonymousUserKey = Annotated[
    str,
    StringConstraints(pattern=r"^usr1\.g[1-9][0-9]*\.[0-9a-f]{64}$"),
]
AttributionConfigState = Literal["absent", "disabled", "valid", "invalid"]
AttributionMetric = Literal["usage", "cost"]
AttributionLevel = Literal["department", "user"]
MappingState = Literal["mapped", "unmapped", "ambiguous", "not_applicable"]
AllocationKey = Literal[
    "weighted_tokens",
    "total_tokens",
    "tool_invocations",
    "active_session_seconds",
    "credits",
    "credit_events",
]
CostConfidence = Literal["high", "medium", "low", "unavailable"]

_DEPARTMENT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,63}$"
_NON_NEGATIVE_DECIMAL_RE = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~-]{1,1024}$")
_TOKEN_PREFIX = "at1"
_FINGERPRINT_PREFIX_LENGTH = 16
_SECRET_PARTS = ("secret", "password", "credential", "private_key")
_SECRET_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "bearer_token",
    "client_secret",
    "connection_string",
    "password",
    "token",
}


def _contains_secret_shaped_field(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
            if (
                normalized in _SECRET_KEYS
                or any(part in normalized for part in _SECRET_PARTS)
                or normalized.endswith("_token")
            ):
                return True
            if _contains_secret_shaped_field(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_secret_shaped_field(item) for item in value)
    return False


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        if value.is_finite() and value >= 0:
            return value
        raise ValueError("value must be a finite non-negative decimal")
    if not isinstance(value, str) or _NON_NEGATIVE_DECIMAL_RE.fullmatch(value) is None:
        raise ValueError("value must be a canonical non-negative decimal string")
    return Decimal(value)


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


class AttributionContract(BaseModel):
    """Strict base for attribution configuration and response contracts."""

    model_config = ConfigDict(extra="forbid")

    @field_serializer("*", when_used="json", check_fields=False)
    def _serialize_decimal(self, value: Any) -> Any:
        return _decimal_text(value) if isinstance(value, Decimal) else value


class DepartmentDefinition(AttributionContract):
    """One operator-owned department mapping."""

    id: str = Field(
        min_length=1, max_length=64, pattern=_DEPARTMENT_ID_PATTERN, repr=False
    )
    label: str = Field(min_length=1, max_length=128, repr=False)
    user_keys: list[PseudonymousUserKey] = Field(
        default_factory=list, max_length=MAX_ATTRIBUTION_USER_KEYS, repr=False
    )
    group_ids: list[UUID] = Field(
        default_factory=list, max_length=MAX_ATTRIBUTION_GROUP_IDS, repr=False
    )

    @field_validator("label")
    @classmethod
    def _require_trimmed_label(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("department label must not have surrounding whitespace")
        return value

    @model_validator(mode="after")
    def _validate_mapping(self) -> "DepartmentDefinition":
        if not self.user_keys and not self.group_ids:
            raise ValueError("department requires at least one user key or group ID")
        if len(self.user_keys) != len(set(self.user_keys)):
            raise ValueError("user keys must be unique within a department")
        if len(self.group_ids) != len(set(self.group_ids)):
            raise ValueError("group IDs must be unique within a department")
        return self


class AttributionConfiguration(AttributionContract):
    """Versioned deployment-local attribution configuration."""

    version: Literal[1]
    enabled: StrictBool
    deployment_namespace: UUID | None = Field(repr=False)
    generation: StrictInt | None = Field(default=None, ge=1, le=2_147_483_647)
    departments: list[DepartmentDefinition] = Field(
        max_length=MAX_ATTRIBUTION_DEPARTMENTS, repr=False
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_sensitive_shape(cls, value: Any) -> Any:
        if _contains_secret_shaped_field(value):
            raise ValueError("secret-shaped fields are not supported")
        if isinstance(value, Mapping) and type(value.get("version")) is not int:
            raise ValueError("version must be the integer 1")
        return value

    @model_validator(mode="after")
    def _validate_configuration(self) -> "AttributionConfiguration":
        if self.enabled and (
            self.deployment_namespace is None or self.generation is None
        ):
            raise ValueError(
                "enabled attribution requires deployment_namespace and generation"
            )

        department_ids = [department.id for department in self.departments]
        if len(department_ids) != len(set(department_ids)):
            raise ValueError("department IDs must be globally unique")

        all_user_keys = [
            key for department in self.departments for key in department.user_keys
        ]
        all_group_ids = [
            group_id
            for department in self.departments
            for group_id in department.group_ids
        ]
        if len(all_user_keys) > MAX_ATTRIBUTION_USER_KEYS:
            raise ValueError("configuration contains more than 500 user keys")
        if len(all_group_ids) > MAX_ATTRIBUTION_GROUP_IDS:
            raise ValueError("configuration contains more than 100 group IDs")
        if len(all_user_keys) != len(set(all_user_keys)):
            raise ValueError("user keys must be globally unique")
        if len(all_group_ids) != len(set(all_group_ids)):
            raise ValueError("group IDs must be globally unique")
        if all_user_keys:
            if self.generation is None:
                raise ValueError("mapped user keys require a current generation")
            prefix = f"usr1.g{self.generation}."
            if any(not key.startswith(prefix) for key in all_user_keys):
                raise ValueError("every user key must use the current generation")
        return self


class AttributionConfigurationLoadResult(AttributionContract):
    """Non-sensitive startup outcome for the optional configuration."""

    state: AttributionConfigState
    config: AttributionConfiguration | None = Field(default=None, repr=False)
    fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error_code: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$"
    )
    message: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def _validate_state(self) -> "AttributionConfigurationLoadResult":
        if self.state in {"valid", "disabled"}:
            if self.config is None or self.fingerprint is None:
                raise ValueError(
                    f"{self.state} state requires config and fingerprint"
                )
            if self.config.enabled != (self.state == "valid"):
                raise ValueError("load state must match config.enabled")
            if self.error_code is not None or self.message is not None:
                raise ValueError(f"{self.state} state forbids error fields")
        elif self.state == "invalid":
            if self.config is not None or self.fingerprint is not None:
                raise ValueError("invalid state forbids config and fingerprint")
            if self.error_code is None or self.message is None:
                raise ValueError("invalid state requires error_code and message")
        elif any(
            value is not None
            for value in (
                self.config,
                self.fingerprint,
                self.error_code,
                self.message,
            )
        ):
            raise ValueError("absent state forbids config, fingerprint, and errors")
        return self


def canonical_attribution_config_json(config: AttributionConfiguration) -> str:
    """Return semantic JSON insensitive to mapping and property ordering."""
    payload = config.model_dump(mode="json")
    payload["departments"] = sorted(
        (
            {
                **department,
                "user_keys": sorted(department["user_keys"]),
                "group_ids": sorted(department["group_ids"]),
            }
            for department in payload["departments"]
        ),
        key=lambda department: department["id"],
    )
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def attribution_config_fingerprint(config: AttributionConfiguration) -> str:
    """Return the full SHA-256 semantic configuration fingerprint."""
    canonical = canonical_attribution_config_json(config)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_observe_scope_json(scope: Any) -> str:
    """Return compact semantic JSON for an Observe scope or equivalent mapping."""
    if hasattr(scope, "model_dump"):
        payload = scope.model_dump(mode="json")
    elif isinstance(scope, Mapping):
        payload = dict(scope)
    else:
        raise TypeError("Observe scope must be a model or mapping")
    if isinstance(payload.get("project_resource_ids"), list):
        payload["project_resource_ids"] = sorted(set(payload["project_resource_ids"]))
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def observe_scope_fingerprint(scope: Any) -> str:
    """Return the full SHA-256 semantic Observe-scope fingerprint."""
    canonical = canonical_observe_scope_json(scope)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def derive_pseudonymous_user_key(
    *,
    deployment_namespace: UUID | str,
    generation: int,
    tenant_id: UUID | str,
    raw_identity: str,
) -> str:
    """Derive the deployment-scoped full SHA-256 user key."""
    namespace = str(UUID(str(deployment_namespace)))
    tenant = str(UUID(str(tenant_id)))
    if type(generation) is not int or generation < 1:
        raise ValueError("generation must be a positive integer")
    if not isinstance(raw_identity, str) or not raw_identity.strip():
        raise ValueError("raw_identity must be a non-empty string")
    identity = raw_identity.strip()
    canonical = (
        f"agentops-attribution-v1|{namespace}|{generation}|{tenant}|{identity}"
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"usr1.g{generation}.{digest}"


# A concise alias for callers that already use attribution terminology.
pseudonymize_user = derive_pseudonymous_user_key
derive_user_key = derive_pseudonymous_user_key


class AttributionResolution(AttributionContract):
    """Pure, non-identifying result of one department mapping resolution."""

    user_key: PseudonymousUserKey = Field(repr=False)
    department_id: str | None = Field(
        default=None, min_length=1, max_length=64, repr=False
    )
    department_label: str | None = Field(
        default=None, min_length=1, max_length=128, repr=False
    )
    source: Literal["explicit_user", "principal_group", "unmapped", "ambiguous"]
    matched_group_ids: StrictInt = Field(default=0, ge=0)
    reason: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def _validate_resolution(self) -> "AttributionResolution":
        mapped = self.source in {"explicit_user", "principal_group"}
        if mapped != (
            self.department_id is not None and self.department_label is not None
        ):
            raise ValueError("mapped resolution requires department ID and label")
        if self.source != "principal_group" and self.matched_group_ids:
            raise ValueError(
                "matched_group_ids is nonzero only for principal-group resolution"
            )
        return self


def resolve_attribution(
    user_key: str,
    config: AttributionConfiguration,
    *,
    identity_matches_principal: bool = False,
    principal_group_ids: list[UUID | str] | tuple[UUID | str, ...] = (),
) -> AttributionResolution:
    """Resolve one pseudonymous key with explicit-user precedence."""
    explicit = [
        department
        for department in config.departments
        if user_key in department.user_keys
    ]
    if len(explicit) == 1:
        department = explicit[0]
        return AttributionResolution(
            user_key=user_key,
            department_id=department.id,
            department_label=department.label,
            source="explicit_user",
            reason="An explicit pseudonymous-user mapping was applied.",
        )
    if len(explicit) > 1:
        return AttributionResolution(
            user_key=user_key,
            source="ambiguous",
            reason="More than one explicit mapping matched.",
        )
    if not identity_matches_principal:
        return AttributionResolution(
            user_key=user_key,
            source="unmapped",
            reason="No explicit mapping applies and principal claims are not applicable.",
        )

    claimed = {UUID(str(group_id)) for group_id in principal_group_ids}
    matches = [
        (department, set(department.group_ids) & claimed)
        for department in config.departments
        if set(department.group_ids) & claimed
    ]
    matched_count = sum(len(group_ids) for _, group_ids in matches)
    if not matches:
        return AttributionResolution(
            user_key=user_key,
            source="unmapped",
            reason="No configured principal group mapping applies.",
        )
    departments = {department.id: department for department, _ in matches}
    if len(departments) != 1:
        return AttributionResolution(
            user_key=user_key,
            source="ambiguous",
            reason="Principal group mappings resolve to multiple departments.",
        )
    department = next(iter(departments.values()))
    return AttributionResolution(
        user_key=user_key,
        department_id=department.id,
        department_label=department.label,
        source="principal_group",
        matched_group_ids=matched_count,
        reason="Validated current-principal group claims were applied.",
    )


class AttributionTokenValidationError(ValueError):
    """Stable, non-identifying fail-closed token validation error."""

    def __init__(self, code: str, message: str, next_action: str | None = None) -> None:
        self.code = code
        self.next_action = next_action or "Select the attribution filter again."
        super().__init__(message)


def _token_error(code: str, correction: str) -> AttributionTokenValidationError:
    return AttributionTokenValidationError(
        code,
        "The attribution selector is not valid for this request.",
        correction,
    )


def _fingerprints(config: AttributionConfiguration, scope: Any) -> tuple[str, str]:
    return attribution_config_fingerprint(config), observe_scope_fingerprint(scope)


def _principal_binding(
    tenant_id: UUID | str,
    principal_id: str,
    scope_fingerprint: str,
    config_fingerprint: str,
    user_key: str,
) -> str:
    tenant = str(UUID(str(tenant_id)))
    if not isinstance(principal_id, str) or not principal_id.strip():
        raise ValueError("principal_id must be a non-empty string")
    canonical = (
        "agentops-attribution-user-token-v1|"
        f"{tenant}|{principal_id.strip()}|{scope_fingerprint}|"
        f"{config_fingerprint}|{user_key}"
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def issue_user_filter_token(
    user_key: str,
    *,
    config: AttributionConfiguration,
    scope: Any,
    tenant_id: UUID | str,
    principal_id: str,
) -> str:
    """Issue an opaque current-principal user selector."""
    if config.generation is None:
        raise ValueError("attribution configuration has no generation")
    if re.fullmatch(
        rf"usr1\.g{config.generation}\.[0-9a-f]{{64}}", user_key
    ) is None:
        raise ValueError("user key must be a complete current generation key")
    config_fp, scope_fp = _fingerprints(config, scope)
    binding = _principal_binding(
        tenant_id, principal_id, scope_fp, config_fp, user_key
    )
    return "~".join(
        (
            _TOKEN_PREFIX,
            "u",
            f"g{config.generation}",
            config_fp[:_FINGERPRINT_PREFIX_LENGTH],
            scope_fp[:_FINGERPRINT_PREFIX_LENGTH],
            user_key,
            binding,
        )
    )


def validate_user_filter_token(
    token: str,
    *,
    config: AttributionConfiguration,
    scope: Any,
    tenant_id: UUID | str,
    principal_id: str,
) -> str:
    """Validate a user selector and return its current pseudonymous key."""
    parts = _parse_token(token)
    if parts[1] != "u":
        raise _token_error(
            "attribution_token_wrong_type", "Select the filter again."
        )
    if len(parts) != 7:
        raise _token_error(
            "attribution_token_invalid_syntax", "Select the filter again."
        )
    _validate_common_token(parts, config=config, scope=scope)
    user_key = parts[5]
    if not re.fullmatch(r"usr1\.g[1-9][0-9]*\.[0-9a-f]{64}", user_key):
        raise _token_error(
            "attribution_token_invalid_syntax", "Select the filter again."
        )
    if config.generation is None or not user_key.startswith(
        f"usr1.g{config.generation}."
    ):
        raise _token_error(
            "attribution_token_generation_changed",
            "Select the filter again after attribution rotation.",
        )
    config_fp, scope_fp = _fingerprints(config, scope)
    expected = _principal_binding(
        tenant_id, principal_id, scope_fp, config_fp, user_key
    )
    if not hmac.compare_digest(parts[6], expected):
        raise _token_error(
            "attribution_token_principal_changed",
            "Sign in with the original authorized account or select the filter again.",
        )
    return user_key


def _department_digest(department_id: str) -> str:
    canonical = f"agentops-attribution-department-token-v1|{department_id}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def issue_department_filter_token(
    department_id: str,
    *,
    config: AttributionConfiguration,
    scope: Any,
) -> str:
    """Issue an opaque selector for one currently configured department."""
    if config.generation is None:
        raise ValueError("attribution configuration has no generation")
    matches = [
        department for department in config.departments if department.id == department_id
    ]
    if len(matches) != 1:
        raise ValueError("department must resolve exactly once")
    config_fp, scope_fp = _fingerprints(config, scope)
    return "~".join(
        (
            _TOKEN_PREFIX,
            "d",
            f"g{config.generation}",
            config_fp[:_FINGERPRINT_PREFIX_LENGTH],
            scope_fp[:_FINGERPRINT_PREFIX_LENGTH],
            _department_digest(department_id),
        )
    )


def validate_department_filter_token(
    token: str,
    *,
    config: AttributionConfiguration,
    scope: Any,
) -> DepartmentDefinition:
    """Validate a department selector and resolve it against current config."""
    parts = _parse_token(token)
    if parts[1] != "d":
        raise _token_error(
            "attribution_token_wrong_type", "Select the filter again."
        )
    if len(parts) != 6:
        raise _token_error(
            "attribution_token_invalid_syntax", "Select the filter again."
        )
    _validate_common_token(parts, config=config, scope=scope)
    matches = [
        department
        for department in config.departments
        if hmac.compare_digest(_department_digest(department.id), parts[5])
    ]
    if len(matches) != 1:
        code = (
            "attribution_token_unresolved"
            if not matches
            else "attribution_token_ambiguous"
        )
        raise _token_error(code, "Select a current configured department filter.")
    return matches[0]


def _parse_token(token: str) -> list[str]:
    if (
        not isinstance(token, str)
        or _TOKEN_RE.fullmatch(token) is None
        or len(parts := token.split("~")) < 2
        or parts[0] != _TOKEN_PREFIX
    ):
        raise _token_error(
            "attribution_token_invalid_syntax", "Select the filter again."
        )
    return parts


def _validate_common_token(
    parts: list[str], *, config: AttributionConfiguration, scope: Any
) -> None:
    if config.generation is None or parts[2] != f"g{config.generation}":
        raise _token_error(
            "attribution_token_generation_changed",
            "Select the filter again after attribution rotation.",
        )
    config_fp, scope_fp = _fingerprints(config, scope)
    if not hmac.compare_digest(
        parts[3], config_fp[:_FINGERPRINT_PREFIX_LENGTH]
    ):
        raise _token_error(
            "attribution_token_config_changed",
            "Select the filter again after the mapping change.",
        )
    if not hmac.compare_digest(
        parts[4], scope_fp[:_FINGERPRINT_PREFIX_LENGTH]
    ):
        raise _token_error(
            "attribution_token_scope_changed",
            "Select the filter again in the current Observe scope.",
        )


class AttributionUsage(AttributionContract):
    invocations: StrictInt = Field(ge=0)
    input_tokens: StrictInt | None = Field(ge=0)
    output_tokens: StrictInt | None = Field(ge=0)
    tool_invocations: StrictInt | None = Field(ge=0)
    active_session_seconds: Decimal | None

    @field_validator("active_session_seconds", mode="before")
    @classmethod
    def _validate_duration(cls, value: Any) -> Any:
        return None if value is None else _decimal(value)


def _usage_reconciles(
    total: AttributionUsage,
    attributed: AttributionUsage,
    unattributed: AttributionUsage,
) -> bool:
    for field in (
        "invocations",
        "input_tokens",
        "output_tokens",
        "tool_invocations",
        "active_session_seconds",
    ):
        expected = getattr(total, field)
        parts = [
            value
            for value in (
                getattr(attributed, field),
                getattr(unattributed, field),
            )
            if value is not None
        ]
        actual = sum(parts) if parts else None
        if actual != expected:
            return False
    return True


class AttributionCost(AttributionContract):
    period_id: str = Field(min_length=1, max_length=64)
    component_id: str = Field(min_length=1, max_length=64)
    amount: Decimal
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    currency_minor_units: StrictInt = Field(ge=0, le=6)
    usage_numerator: Decimal
    usage_denominator: Decimal
    allocation_key: AllocationKey
    confidence: CostConfidence

    @field_validator("amount", "usage_numerator", "usage_denominator", mode="before")
    @classmethod
    def _validate_amount(cls, value: Any) -> Decimal:
        return _decimal(value)

    @model_validator(mode="after")
    def _validate_precision(self) -> "AttributionCost":
        exponent = self.amount.as_tuple().exponent
        if not isinstance(exponent, int) or max(0, -exponent) > self.currency_minor_units:
            raise ValueError("amount exceeds currency_minor_units precision")
        return self


class DepartmentAttributionRow(AttributionContract):
    kind: Literal["department"]
    department_id: str = Field(min_length=1, max_length=64, repr=False)
    department_label: str = Field(min_length=1, max_length=128, repr=False)
    filter_token: str = Field(min_length=1, max_length=1024, repr=False)
    member_count: StrictInt = Field(ge=1)
    usage: AttributionUsage
    cost: AttributionCost | None
    mapping_state: Literal["mapped"]


class UserAttributionRow(AttributionContract):
    kind: Literal["user"]
    user_key: PseudonymousUserKey = Field(repr=False)
    filter_token: str = Field(min_length=1, max_length=1024, repr=False)
    raw_identity: str = Field(min_length=1, max_length=1024, repr=False)
    department_id: str | None = Field(
        default=None, min_length=1, max_length=64, repr=False
    )
    department_label: str | None = Field(
        default=None, min_length=1, max_length=128, repr=False
    )
    usage: AttributionUsage
    cost: AttributionCost | None
    mapping_state: Literal["mapped", "unmapped", "ambiguous"]

    @model_validator(mode="after")
    def _validate_department(self) -> "UserAttributionRow":
        if (self.department_id is None) != (self.department_label is None):
            raise ValueError("department ID and label must be provided together")
        if self.mapping_state == "mapped" and self.department_id is None:
            raise ValueError("mapped user rows require a department")
        if self.mapping_state != "mapped" and self.department_id is not None:
            raise ValueError("unmapped or ambiguous user rows forbid a department")
        return self


class OtherUsersAttributionRow(AttributionContract):
    kind: Literal["other_users"]
    member_count: StrictInt = Field(ge=1)
    usage: AttributionUsage
    cost: AttributionCost | None
    mapping_state: Literal["not_applicable"]


AttributionRow = Annotated[
    DepartmentAttributionRow | UserAttributionRow | OtherUsersAttributionRow,
    Field(discriminator="kind"),
]


class UsageAttributionSummary(AttributionContract):
    metric: Literal["usage"]
    total: AttributionUsage
    attributed: AttributionUsage
    unattributed: AttributionUsage
    distinct_users: StrictInt | None = Field(ge=0)
    omitted_users: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def _reconcile(self) -> "UsageAttributionSummary":
        if not _usage_reconciles(self.total, self.attributed, self.unattributed):
            raise ValueError("usage attribution must reconcile to total")
        return self


class CostAttributionSummary(AttributionContract):
    metric: Literal["cost"]
    period_id: str = Field(min_length=1, max_length=64)
    component_id: str = Field(min_length=1, max_length=64)
    declared_total: Decimal
    attributed_amount: Decimal
    unattributed_amount: Decimal
    unallocated_amount: Decimal
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    currency_minor_units: StrictInt = Field(ge=0, le=6)
    allocation_key: AllocationKey
    confidence: CostConfidence
    total_usage: AttributionUsage
    attributed_usage: AttributionUsage
    unattributed_usage: AttributionUsage
    distinct_users: StrictInt | None = Field(ge=0)
    omitted_users: StrictInt = Field(ge=0)

    @field_validator(
        "declared_total",
        "attributed_amount",
        "unattributed_amount",
        "unallocated_amount",
        mode="before",
    )
    @classmethod
    def _validate_amount(cls, value: Any) -> Decimal:
        return _decimal(value)

    @model_validator(mode="after")
    def _reconcile(self) -> "CostAttributionSummary":
        amounts = (
            self.declared_total,
            self.attributed_amount,
            self.unattributed_amount,
            self.unallocated_amount,
        )
        for amount in amounts:
            exponent = amount.as_tuple().exponent
            if (
                not isinstance(exponent, int)
                or max(0, -exponent) > self.currency_minor_units
            ):
                raise ValueError("cost amount exceeds currency_minor_units precision")
        if (
            self.attributed_amount
            + self.unattributed_amount
            + self.unallocated_amount
            != self.declared_total
        ):
            raise ValueError("cost attribution must reconcile to declared_total")
        if not _usage_reconciles(
            self.total_usage, self.attributed_usage, self.unattributed_usage
        ):
            raise ValueError("cost usage evidence must reconcile to total_usage")
        return self


AttributionSummary = Annotated[
    UsageAttributionSummary | CostAttributionSummary,
    Field(discriminator="metric"),
]


class AttributionViewData(AttributionContract):
    metric: AttributionMetric
    group_by: AttributionLevel
    access_boundary: Literal["aggregate", "delegated"]
    rows: list[AttributionRow] = Field(max_length=MAX_ATTRIBUTION_ROWS)
    summary: AttributionSummary
    primary_measure: Literal["invocations", "allocated_amount"]
    calculated_at: datetime
    latest_observed_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_view(self) -> "AttributionViewData":
        if self.metric != self.summary.metric:
            raise ValueError("summary metric must match view metric")
        if self.primary_measure != (
            "invocations" if self.metric == "usage" else "allocated_amount"
        ):
            raise ValueError("primary_measure must match metric")
        if self.group_by == "user":
            if self.access_boundary != "delegated":
                raise ValueError("user attribution requires delegated access")
            if any(row.kind == "department" for row in self.rows):
                raise ValueError("user grouping forbids department rows")
        else:
            if any(row.kind != "department" for row in self.rows):
                raise ValueError("department grouping accepts only department rows")
            if (
                self.access_boundary == "aggregate"
                and any(row.member_count == 1 for row in self.rows)
            ):
                raise ValueError("singleton departments require delegated access")
        for row in self.rows:
            if self.metric == "cost" and row.cost is None:
                raise ValueError("cost attribution rows require cost")
            if self.metric == "usage" and row.cost is not None:
                raise ValueError("usage attribution rows forbid cost")
        if self.group_by == "user":
            other_indexes = [
                index for index, row in enumerate(self.rows) if row.kind == "other_users"
            ]
            if len(other_indexes) > 1 or (
                other_indexes and other_indexes[0] != len(self.rows) - 1
            ):
                raise ValueError("Other users must be the final and only aggregate row")
            user_rows = [row for row in self.rows if row.kind == "user"]
            if self.metric == "usage":
                expected = sorted(
                    user_rows,
                    key=lambda row: (-row.usage.invocations, row.user_key),
                )
            else:
                expected = sorted(
                    user_rows,
                    key=lambda row: (
                        -(row.cost.amount if row.cost is not None else Decimal(0)),
                        row.user_key,
                    ),
                )
            if user_rows != expected:
                raise ValueError(
                    "user rows must be ranked by primary measure descending "
                    "with user_key ascending ties"
                )
        return self


def _safe_error_location(error: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for part in error.get("loc", ()):
        if isinstance(part, int):
            if parts:
                parts[-1] += f"[{part}]"
            else:
                parts.append(f"[{part}]")
        else:
            cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", str(part))[:64]
            parts.append(cleaned or "field")
    return ".".join(parts) or "attribution_config"


def _safe_validation_message(exc: ValidationError) -> str:
    error = exc.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )[0]
    location = _safe_error_location(error)
    return (
        f"Invalid attribution configuration field '{location}'. "
        "Correct the setting and restart Cockpit."
    )


def load_attribution_config(raw: str | None) -> AttributionConfigurationLoadResult:
    """Load the bounded optional setting without echoing privacy-sensitive input."""
    if raw is None:
        return AttributionConfigurationLoadResult(state="absent")
    if not isinstance(raw, str):
        return AttributionConfigurationLoadResult(
            state="invalid",
            error_code="attribution_config_invalid_type",
            message="AGENTOPS_ATTRIBUTION_CONFIG must be a UTF-8 JSON string.",
        )
    try:
        encoded = raw.encode("utf-8")
    except UnicodeEncodeError:
        return AttributionConfigurationLoadResult(
            state="invalid",
            error_code="attribution_config_invalid_encoding",
            message="AGENTOPS_ATTRIBUTION_CONFIG must contain valid UTF-8 text.",
        )
    if len(encoded) > MAX_ATTRIBUTION_CONFIG_BYTES:
        return AttributionConfigurationLoadResult(
            state="invalid",
            error_code="attribution_config_too_large",
            message=(
                "AGENTOPS_ATTRIBUTION_CONFIG must not exceed 64 KiB of UTF-8 JSON."
            ),
        )
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, RecursionError):
        return AttributionConfigurationLoadResult(
            state="invalid",
            error_code="attribution_config_invalid_json",
            message=(
                "AGENTOPS_ATTRIBUTION_CONFIG must be a complete valid JSON object."
            ),
        )
    if _contains_secret_shaped_field(payload):
        return AttributionConfigurationLoadResult(
            state="invalid",
            error_code="attribution_config_secret_field",
            message=(
                "AGENTOPS_ATTRIBUTION_CONFIG contains an unsupported secret-shaped "
                "field. Remove credentials, tokens, and secret references."
            ),
        )
    try:
        config = AttributionConfiguration.model_validate(payload)
    except ValidationError as exc:
        return AttributionConfigurationLoadResult(
            state="invalid",
            error_code="attribution_config_validation_error",
            message=_safe_validation_message(exc),
        )
    fingerprint = attribution_config_fingerprint(config)
    return AttributionConfigurationLoadResult(
        state="valid" if config.enabled else "disabled",
        config=config,
        fingerprint=fingerprint,
    )


# Compatibility aliases matching the environment-setting terminology.
load_attribution_configuration = load_attribution_config
canonical_attribution_configuration_json = canonical_attribution_config_json
attribution_configuration_fingerprint = attribution_config_fingerprint
