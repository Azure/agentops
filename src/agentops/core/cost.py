"""Pure contracts and bounded loading for billed-cost allocation."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    field_serializer,
    model_validator,
)

from agentops.core.observe import CoverageState, RuntimeKind, canonical_arm_id


MAX_COST_MODEL_BYTES = 32 * 1024
MAX_COST_PERIODS = 24
MAX_COST_COMPONENTS = 50
MAX_COST_ROWS = 500

CostComponentType = Literal[
    "provisioned_throughput",
    "standard_model",
    "search",
    "grounding",
    "content_safety",
    "storage",
    "hosted_compute",
    "customer_compute",
    "credit_payg",
    "credit_prepaid",
]
AllocationModel = Literal["metered", "commitment"]
AllocationKey = Literal[
    "weighted_tokens",
    "total_tokens",
    "tool_invocations",
    "active_session_seconds",
    "credits",
    "credit_events",
]
BillingBoundaryKind = Literal[
    "resource", "subscription", "account", "pool", "custom"
]
CostBreakdown = Literal["agents", "tools", "runs"]
CostConsumerKind = Literal[
    "agent",
    "tool",
    "run",
    "department",
    "user",
    "other_users",
    "unattributed",
]
CostConfidence = Literal["high", "medium", "low", "unavailable"]
CostModelState = Literal["absent", "valid", "invalid"]

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
_PSEUDONYMOUS_USER_KEY_PATTERN = r"^usr1\.g[1-9][0-9]*\.[0-9a-f]{64}$"
_NON_NEGATIVE_DECIMAL_RE = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
_POSITIVE_DECIMAL_RE = re.compile(
    r"^(?:0\.[0-9]*[1-9][0-9]*|[1-9][0-9]*(?:\.[0-9]+)?)$"
)
_SECRET_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "bearer_token",
    "client_secret",
    "connection_string",
    "credential",
    "credentials",
    "password",
    "passwd",
    "secret",
    "token",
}
_CREDENTIAL_VALUE_PATTERNS = (
    re.compile(
        r"(?i)(?:^|[;,\s])(?:access_?token|account_?key|api_?key|"
        r"authorization|bearer_?token|client_?secret|connection_?string|"
        r"credential|password|passwd|shared_?access_?key)\s*[:=]"
    ),
    re.compile(r"(?i)@Microsoft\.KeyVault\s*\(\s*SecretUri\s*="),
    re.compile(r"(?i)https?://[^\s]+/secrets(?:/|$)"),
    re.compile(r"(?i)-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----"),
)
_TOKEN_WEIGHT_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)
_NARROWING_SELECTOR_FIELDS = (
    "source_resource_ids",
    "project_resource_ids",
    "agent_keys",
    "deployments",
    "models",
    "tool_names",
    "runtime_kinds",
)


def _contains_secret_shaped_field(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = (
                re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
            )
            if (
                normalized in _SECRET_KEYS
                or "secret" in normalized
                or "password" in normalized
                or "credential" in normalized
                or normalized.endswith("_token")
            ):
                return True
            if _contains_secret_shaped_field(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_secret_shaped_field(item) for item in value)
    elif isinstance(value, str):
        return any(pattern.search(value) for pattern in _CREDENTIAL_VALUE_PATTERNS)
    return False


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


class CostContract(BaseModel):
    """Strict base for cost configuration and response contracts."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _reject_credential_bearing_content(cls, value: Any) -> Any:
        if _contains_secret_shaped_field(value):
            raise ValueError(
                "secret-shaped fields and credential-bearing values are not supported"
            )
        return value

    @field_serializer("*", when_used="json", check_fields=False)
    def _serialize_decimal(self, value: Any) -> Any:
        return _decimal_text(value) if isinstance(value, Decimal) else value


def _canonical_decimal(
    value: Any,
    *,
    positive: bool,
    allow_decimal: bool = False,
) -> Decimal:
    if allow_decimal and isinstance(value, Decimal):
        if not value.is_finite() or value < 0 or (positive and value <= 0):
            raise ValueError(
                "value must be a finite positive decimal"
                if positive
                else "value must be a finite non-negative decimal"
            )
        return value
    if not isinstance(value, str):
        raise ValueError("value must be a canonical decimal string")
    pattern = _POSITIVE_DECIMAL_RE if positive else _NON_NEGATIVE_DECIMAL_RE
    if pattern.fullmatch(value) is None:
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"value must be a canonical {qualifier} decimal string")
    return Decimal(value)


def _normalize_string(value: Any, *, max_length: int = 512) -> str:
    if not isinstance(value, str):
        raise ValueError("selector value must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("selector value must not be empty")
    if len(normalized) > max_length:
        raise ValueError(f"selector value must be at most {max_length} characters")
    return normalized


def _normalize_selector_list(
    value: Any,
    *,
    max_items: int,
    max_length: int,
    arm_ids: bool = False,
) -> Any:
    if not isinstance(value, list):
        return value
    if len(value) > max_items:
        raise ValueError(f"selector list must contain at most {max_items} values")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        candidate = _normalize_string(item, max_length=max_length)
        if arm_ids:
            candidate = canonical_arm_id(candidate)
        if candidate not in seen:
            normalized.append(candidate)
            seen.add(candidate)
    return normalized


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError("timestamp must use UTC")
    return value.astimezone(timezone.utc)


def _validate_currency_precision(
    value: Decimal, minor_units: int, *, field_name: str
) -> None:
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        raise ValueError(f"{field_name} must be finite")
    fractional_digits = max(0, -exponent)
    if fractional_digits > minor_units:
        raise ValueError(
            f"{field_name} exceeds currency_minor_units precision ({minor_units})"
        )


class BillingBoundary(CostContract):
    kind: BillingBoundaryKind
    value: str = Field(min_length=1, max_length=512)
    label: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("value")
    @classmethod
    def _normalize_value(cls, value: str, info: Any) -> str:
        normalized = _normalize_string(value)
        if info.data.get("kind") in {"resource", "subscription"}:
            return canonical_arm_id(normalized)
        return normalized

    @field_validator("label")
    @classmethod
    def _normalize_label(cls, value: str | None) -> str | None:
        return _normalize_string(value, max_length=128) if value is not None else None


class UsageMatch(CostContract):
    source_resource_ids: list[str] = Field(default_factory=list, max_length=100)
    project_resource_ids: list[str] = Field(default_factory=list, max_length=100)
    agent_keys: list[str] = Field(default_factory=list, max_length=100)
    deployments: list[str] = Field(default_factory=list, max_length=100)
    models: list[str] = Field(default_factory=list, max_length=100)
    tool_names: list[str] = Field(default_factory=list, max_length=100)
    runtime_kinds: list[RuntimeKind] = Field(default_factory=list, max_length=6)
    credit_event_operations: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("source_resource_ids", "project_resource_ids", mode="before")
    @classmethod
    def _normalize_arm_selectors(cls, value: Any) -> Any:
        return _normalize_selector_list(
            value, max_items=100, max_length=512, arm_ids=True
        )

    @field_validator(
        "agent_keys", "deployments", "models", "tool_names", mode="before"
    )
    @classmethod
    def _normalize_selectors(cls, value: Any) -> Any:
        return _normalize_selector_list(
            value, max_items=100, max_length=512
        )

    @field_validator("runtime_kinds", mode="before")
    @classmethod
    def _normalize_runtime_kinds(cls, value: Any) -> Any:
        return _normalize_selector_list(value, max_items=6, max_length=64)

    @field_validator("credit_event_operations", mode="before")
    @classmethod
    def _normalize_credit_operations(cls, value: Any) -> Any:
        return _normalize_selector_list(value, max_items=32, max_length=128)

    @model_validator(mode="after")
    def _require_narrowing_selector(self) -> "UsageMatch":
        if not any(getattr(self, field) for field in _NARROWING_SELECTOR_FIELDS):
            raise ValueError("usage_match requires at least one narrowing selector")
        return self


class TokenWeights(CostContract):
    input_tokens: Decimal | None = None
    output_tokens: Decimal | None = None
    cache_read_tokens: Decimal | None = None
    cache_write_tokens: Decimal | None = None
    reasoning_tokens: Decimal | None = None

    @field_validator(*_TOKEN_WEIGHT_FIELDS, mode="before")
    @classmethod
    def _validate_weight(cls, value: Any) -> Any:
        return None if value is None else _canonical_decimal(value, positive=True)

    @model_validator(mode="after")
    def _require_weight(self) -> "TokenWeights":
        if not any(getattr(self, field) is not None for field in _TOKEN_WEIGHT_FIELDS):
            raise ValueError("token_weights requires at least one positive weight")
        return self


class CostComponent(CostContract):
    id: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER_PATTERN)
    type: CostComponentType
    billing_boundary: BillingBoundary
    billed_source: str = Field(min_length=1, max_length=256)
    billed_total: Decimal
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    currency_minor_units: StrictInt = Field(ge=0, le=6)
    allocation_model: AllocationModel
    allocation_key: AllocationKey
    fallback_key: AllocationKey | None = None
    token_weights: TokenWeights | None = None
    usage_match: UsageMatch

    @field_validator("billed_source")
    @classmethod
    def _normalize_source(cls, value: str) -> str:
        return _normalize_string(value, max_length=256)

    @field_validator("billed_total", mode="before")
    @classmethod
    def _validate_billed_total(cls, value: Any) -> Decimal:
        return _canonical_decimal(value, positive=False)

    @model_validator(mode="after")
    def _validate_component(self) -> "CostComponent":
        _validate_currency_precision(
            self.billed_total,
            self.currency_minor_units,
            field_name="billed_total",
        )

        allowed: dict[
            CostComponentType, tuple[set[AllocationModel], set[AllocationKey]]
        ] = {
            "provisioned_throughput": (
                {"commitment"},
                {"weighted_tokens", "total_tokens"},
            ),
            "standard_model": (
                {"metered"},
                {"weighted_tokens", "total_tokens"},
            ),
            "search": ({"metered"}, {"tool_invocations"}),
            "grounding": ({"metered"}, {"tool_invocations"}),
            "content_safety": ({"metered"}, {"tool_invocations"}),
            "storage": ({"metered"}, {"tool_invocations"}),
            "hosted_compute": ({"metered"}, {"active_session_seconds"}),
            "customer_compute": (
                {"metered", "commitment"},
                {"active_session_seconds"},
            ),
            "credit_payg": ({"metered"}, {"credits", "credit_events"}),
            "credit_prepaid": ({"commitment"}, {"credits", "credit_events"}),
        }
        models, keys = allowed[self.type]
        if self.allocation_model not in models or self.allocation_key not in keys:
            raise ValueError(
                "component type, allocation_model, and allocation_key "
                "must be a compatible combination"
            )

        allowed_fallback: AllocationKey | None = None
        if self.allocation_key == "weighted_tokens":
            allowed_fallback = "total_tokens"
        elif self.allocation_key == "credits":
            allowed_fallback = "credit_events"
        if self.fallback_key is not None and self.fallback_key != allowed_fallback:
            raise ValueError(
                "fallback_key is not compatible with the allocation_key"
            )
        if self.fallback_key == self.allocation_key:
            raise ValueError("fallback_key must differ from allocation_key")

        if self.allocation_key == "weighted_tokens":
            if self.token_weights is None:
                raise ValueError(
                    "weighted_tokens allocation requires token_weights"
                )
        elif self.token_weights is not None:
            raise ValueError(
                "token_weights is valid only for weighted_tokens allocation"
            )

        uses_credit_events = (
            self.allocation_key == "credit_events"
            or self.fallback_key == "credit_events"
        )
        has_credit_operations = bool(self.usage_match.credit_event_operations)
        if uses_credit_events != has_credit_operations:
            requirement = "requires" if uses_credit_events else "forbids"
            raise ValueError(
                f"credit_events allocation {requirement} "
                "usage_match.credit_event_operations"
            )
        return self


class CostPeriod(CostContract):
    id: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER_PATTERN)
    starts_at: datetime
    ends_at: datetime
    components: list[CostComponent] = Field(
        min_length=1, max_length=MAX_COST_COMPONENTS
    )

    @field_validator("starts_at", "ends_at")
    @classmethod
    def _validate_utc(cls, value: datetime) -> datetime:
        return _utc_datetime(value)

    @model_validator(mode="after")
    def _validate_period(self) -> "CostPeriod":
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be later than starts_at")
        component_ids = [component.id for component in self.components]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("component IDs must be unique within a cost period")
        return self


class CostModel(CostContract):
    version: Literal[1]
    periods: list[CostPeriod] = Field(min_length=1, max_length=MAX_COST_PERIODS)

    @field_validator("version", mode="before")
    @classmethod
    def _require_strict_version(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("version must be the integer 1")
        return value

    @model_validator(mode="after")
    def _validate_model(self) -> "CostModel":
        period_ids = [period.id for period in self.periods]
        if len(period_ids) != len(set(period_ids)):
            raise ValueError("period IDs must be unique")

        intervals: dict[
            tuple[str, BillingBoundaryKind, str], list[tuple[datetime, datetime]]
        ] = {}
        for period in self.periods:
            for component in period.components:
                key = (
                    component.id,
                    component.billing_boundary.kind,
                    component.billing_boundary.value,
                )
                existing = intervals.setdefault(key, [])
                if any(
                    period.starts_at < end and start < period.ends_at
                    for start, end in existing
                ):
                    raise ValueError(
                        "cost periods overlap for the same component and "
                        "billing boundary"
                    )
                existing.append((period.starts_at, period.ends_at))
        return self


class CostModelLoadResult(CostContract):
    state: CostModelState
    model: CostModel | None = None
    fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error_code: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$"
    )
    message: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def _validate_state(self) -> "CostModelLoadResult":
        if self.state == "valid":
            if self.model is None or self.fingerprint is None:
                raise ValueError("valid state requires model and fingerprint")
            if self.error_code is not None or self.message is not None:
                raise ValueError("valid state forbids error fields")
        elif self.state == "invalid":
            if self.model is not None or self.fingerprint is not None:
                raise ValueError("invalid state forbids model and fingerprint")
            if self.error_code is None or self.message is None:
                raise ValueError("invalid state requires error_code and message")
        elif any(
            value is not None
            for value in (
                self.model,
                self.fingerprint,
                self.error_code,
                self.message,
            )
        ):
            raise ValueError("absent state forbids model, fingerprint, and errors")
        return self


class CostUsageObservation(CostContract):
    source_resource_id: str
    project_resource_id: str | None = None
    agent_key: str | None = Field(default=None, min_length=1, max_length=512)
    user_key: StrictStr | None = Field(
        default=None,
        pattern=_PSEUDONYMOUS_USER_KEY_PATTERN,
        repr=False,
    )
    tool_name: str | None = Field(default=None, min_length=1, max_length=512)
    run_key: str | None = Field(default=None, min_length=1, max_length=512)
    runtime_kind: RuntimeKind
    deployment: str | None = Field(default=None, min_length=1, max_length=512)
    model: str | None = Field(default=None, min_length=1, max_length=512)
    operation_name: str | None = Field(default=None, min_length=1, max_length=128)
    input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)
    cache_read_tokens: StrictInt | None = Field(default=None, ge=0)
    cache_write_tokens: StrictInt | None = Field(default=None, ge=0)
    reasoning_tokens: StrictInt | None = Field(default=None, ge=0)
    tool_invocations: StrictInt | None = Field(default=None, ge=0)
    active_session_seconds: Decimal | None = None
    credits: Decimal | None = None
    credit_events: StrictInt | None = Field(default=None, ge=0)
    latest_observed_at: datetime | None = None
    coverage_complete: StrictBool

    _canonicalize_source = field_validator(
        "source_resource_id", mode="before"
    )(canonical_arm_id)
    _canonicalize_project = field_validator(
        "project_resource_id", mode="before"
    )(lambda value: canonical_arm_id(value) if isinstance(value, str) else value)

    @field_validator(
        "agent_key",
        "tool_name",
        "run_key",
        "deployment",
        "model",
        "operation_name",
    )
    @classmethod
    def _normalize_optional_identity(cls, value: str | None) -> str | None:
        return _normalize_string(value) if value is not None else None

    @field_validator("active_session_seconds", "credits", mode="before")
    @classmethod
    def _validate_usage_decimal(cls, value: Any) -> Any:
        return (
            None
            if value is None
            else _canonical_decimal(value, positive=False, allow_decimal=True)
        )

    @field_validator("latest_observed_at")
    @classmethod
    def _validate_latest_utc(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value) if value is not None else None


class CostPeriodRef(CostContract):
    id: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER_PATTERN)
    starts_at: datetime
    ends_at: datetime

    @field_validator("starts_at", "ends_at")
    @classmethod
    def _validate_utc(cls, value: datetime) -> datetime:
        return _utc_datetime(value)

    @model_validator(mode="after")
    def _validate_interval(self) -> "CostPeriodRef":
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be later than starts_at")
        return self


class _CostComponentProvenance(CostContract):
    period_id: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER_PATTERN)
    starts_at: datetime
    ends_at: datetime
    component_id: str = Field(
        min_length=1, max_length=64, pattern=_IDENTIFIER_PATTERN
    )
    component_type: CostComponentType
    billing_boundary: BillingBoundary
    billed_source: str = Field(min_length=1, max_length=256)
    allocation_model: AllocationModel
    preferred_key: AllocationKey
    applied_key: AllocationKey | None
    fallback_used: StrictBool
    breakdown: CostBreakdown
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    currency_minor_units: StrictInt = Field(ge=0, le=6)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def _validate_utc(cls, value: datetime) -> datetime:
        return _utc_datetime(value)

    @field_validator("billed_source")
    @classmethod
    def _normalize_source(cls, value: str) -> str:
        return _normalize_string(value, max_length=256)

    @model_validator(mode="after")
    def _validate_provenance(self) -> "_CostComponentProvenance":
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be later than starts_at")
        if self.applied_key is None:
            if self.fallback_used:
                raise ValueError("fallback_used requires an applied_key")
        elif self.fallback_used != (self.applied_key != self.preferred_key):
            raise ValueError(
                "fallback_used must indicate whether applied_key differs "
                "from preferred_key"
            )
        return self


class CostAllocationRow(_CostComponentProvenance):
    consumer_kind: CostConsumerKind
    consumer_key: str = Field(min_length=1, max_length=512)
    source_resource_id: str | None = None
    project_resource_id: str | None = None
    agent_key: str | None = Field(default=None, min_length=1, max_length=512)
    tool_name: str | None = Field(default=None, min_length=1, max_length=512)
    run_key: str | None = Field(default=None, min_length=1, max_length=512)
    amount: Decimal
    usage_numerator: Decimal
    usage_denominator: Decimal
    usage_unit: AllocationKey
    rounding_adjustment_minor_units: StrictInt
    confidence: CostConfidence
    coverage_state: CoverageState
    coverage_reason: str = Field(min_length=1, max_length=512)
    calculated_at: datetime
    latest_observed_at: datetime | None = None

    @field_validator("consumer_key")
    @classmethod
    def _normalize_consumer_key(cls, value: str) -> str:
        return _normalize_string(value)

    @field_validator("source_resource_id", "project_resource_id", mode="before")
    @classmethod
    def _canonicalize_optional_arm_id(cls, value: Any) -> Any:
        return canonical_arm_id(value) if isinstance(value, str) else value

    @field_validator("agent_key", "tool_name", "run_key")
    @classmethod
    def _normalize_optional_identity(cls, value: str | None) -> str | None:
        return _normalize_string(value) if value is not None else None

    @field_validator(
        "amount", "usage_numerator", "usage_denominator", mode="before"
    )
    @classmethod
    def _validate_decimal(cls, value: Any) -> Decimal:
        return _canonical_decimal(value, positive=False, allow_decimal=True)

    @field_validator("calculated_at")
    @classmethod
    def _validate_calculated_utc(cls, value: datetime) -> datetime:
        return _utc_datetime(value)

    @field_validator("latest_observed_at")
    @classmethod
    def _validate_latest_utc(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value) if value is not None else None

    @model_validator(mode="after")
    def _validate_row(self) -> "CostAllocationRow":
        _validate_currency_precision(
            self.amount, self.currency_minor_units, field_name="amount"
        )
        if self.applied_key is None:
            raise ValueError("allocation row requires applied_key")
        if self.usage_unit != self.applied_key:
            raise ValueError("usage_unit must match applied_key")
        return self


class CostComponentSummary(_CostComponentProvenance):
    declared_total: Decimal
    attributed_amount: Decimal
    unattributed_amount: Decimal
    unallocated_amount: Decimal
    omitted_allocated_amount: Decimal
    rows_shown: StrictInt = Field(ge=0)
    rows_total: StrictInt = Field(ge=0)
    confidence: CostConfidence
    coverage_state: CoverageState
    coverage_reason: str = Field(min_length=1, max_length=512)
    next_action: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator(
        "declared_total",
        "attributed_amount",
        "unattributed_amount",
        "unallocated_amount",
        "omitted_allocated_amount",
        mode="before",
    )
    @classmethod
    def _validate_amount(cls, value: Any) -> Decimal:
        return _canonical_decimal(value, positive=False, allow_decimal=True)

    @model_validator(mode="after")
    def _validate_summary(self) -> "CostComponentSummary":
        for field in (
            "declared_total",
            "attributed_amount",
            "unattributed_amount",
            "unallocated_amount",
            "omitted_allocated_amount",
        ):
            _validate_currency_precision(
                getattr(self, field),
                self.currency_minor_units,
                field_name=field,
            )
        if (
            self.attributed_amount
            + self.unattributed_amount
            + self.unallocated_amount
            != self.declared_total
        ):
            raise ValueError(
                "component amounts must reconcile exactly to declared_total"
            )
        if self.omitted_allocated_amount > self.attributed_amount:
            raise ValueError(
                "omitted_allocated_amount cannot exceed attributed_amount"
            )
        if self.rows_shown > self.rows_total:
            raise ValueError("rows_shown cannot exceed rows_total")
        return self


class CurrencySubtotal(CostContract):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    currency_minor_units: StrictInt = Field(ge=0, le=6)
    declared_total: Decimal
    attributed_amount: Decimal
    unattributed_amount: Decimal
    unallocated_amount: Decimal

    @field_validator(
        "declared_total",
        "attributed_amount",
        "unattributed_amount",
        "unallocated_amount",
        mode="before",
    )
    @classmethod
    def _validate_amount(cls, value: Any) -> Decimal:
        return _canonical_decimal(value, positive=False, allow_decimal=True)

    @model_validator(mode="after")
    def _validate_subtotal(self) -> "CurrencySubtotal":
        for field in (
            "declared_total",
            "attributed_amount",
            "unattributed_amount",
            "unallocated_amount",
        ):
            _validate_currency_precision(
                getattr(self, field),
                self.currency_minor_units,
                field_name=field,
            )
        if (
            self.attributed_amount
            + self.unattributed_amount
            + self.unallocated_amount
            != self.declared_total
        ):
            raise ValueError(
                "currency subtotal must reconcile exactly to declared_total"
            )
        return self


COST_ALLOCATION_DISCLAIMER = (
    "Operational cost allocation from declared billed totals and observed usage; "
    "not an invoice or billing-accurate charge."
)


class CostViewData(CostContract):
    period: CostPeriodRef
    breakdown: CostBreakdown
    component_filter: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=_IDENTIFIER_PATTERN
    )
    components: list[CostComponentSummary] = Field(max_length=MAX_COST_COMPONENTS)
    rows: list[CostAllocationRow] = Field(max_length=MAX_COST_ROWS)
    currency_subtotals: list[CurrencySubtotal]
    calculated_at: datetime
    latest_observed_at: datetime | None = None
    disclaimer: str = COST_ALLOCATION_DISCLAIMER

    @field_validator("calculated_at")
    @classmethod
    def _validate_calculated_utc(cls, value: datetime) -> datetime:
        return _utc_datetime(value)

    @field_validator("latest_observed_at")
    @classmethod
    def _validate_latest_utc(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value) if value is not None else None


def canonical_cost_model_json(model: CostModel) -> str:
    """Serialize a validated cost model to stable, compact JSON."""
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def cost_model_fingerprint(model: CostModel) -> str:
    """Return a deterministic SHA-256 identity without retaining raw input."""
    canonical = canonical_cost_model_json(model)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _safe_error_location(error: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for part in error.get("loc", ()):
        if isinstance(part, int):
            if parts:
                parts[-1] += f"[{part}]"
            else:
                parts.append(f"[{part}]")
            continue
        cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", str(part))[:64]
        parts.append(cleaned or "field")
    return ".".join(parts) or "cost_model"


def _safe_validation_message(exc: ValidationError) -> str:
    error = exc.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )[0]
    location = _safe_error_location(error)
    reason = str(error.get("msg", "value is invalid"))[:256]
    return f"Invalid cost model field '{location}': {reason}. Correct the field and restart Cockpit."


def load_cost_model(raw: str | None) -> CostModelLoadResult:
    """Load a bounded environment value without exposing its contents in errors."""
    if raw is None:
        return CostModelLoadResult(state="absent")
    if not isinstance(raw, str):
        return CostModelLoadResult(
            state="invalid",
            error_code="cost_model_invalid_type",
            message="AGENTOPS_COST_MODEL must be a UTF-8 JSON string.",
        )
    try:
        encoded = raw.encode("utf-8")
    except UnicodeEncodeError:
        return CostModelLoadResult(
            state="invalid",
            error_code="cost_model_invalid_encoding",
            message="AGENTOPS_COST_MODEL must contain valid UTF-8 text.",
        )
    if len(encoded) > MAX_COST_MODEL_BYTES:
        return CostModelLoadResult(
            state="invalid",
            error_code="cost_model_too_large",
            message="AGENTOPS_COST_MODEL must not exceed 32 KiB of UTF-8 JSON.",
        )
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, RecursionError):
        return CostModelLoadResult(
            state="invalid",
            error_code="cost_model_invalid_json",
            message="AGENTOPS_COST_MODEL must be a complete valid JSON object.",
        )
    if _contains_secret_shaped_field(payload):
        return CostModelLoadResult(
            state="invalid",
            error_code="cost_model_secret_field",
            message=(
                "AGENTOPS_COST_MODEL contains an unsupported secret-shaped field "
                "or credential-bearing value. Remove credentials, tokens, and "
                "secret references."
            ),
        )
    try:
        model = CostModel.model_validate(payload)
    except ValidationError as exc:
        return CostModelLoadResult(
            state="invalid",
            error_code="cost_model_validation_error",
            message=_safe_validation_message(exc),
        )
    return CostModelLoadResult(
        state="valid",
        model=model,
        fingerprint=cost_model_fingerprint(model),
    )


# A descriptive alias is convenient at environment-loading call sites.
parse_cost_model = load_cost_model
