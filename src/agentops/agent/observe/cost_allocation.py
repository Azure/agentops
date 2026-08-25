"""Deterministic allocation of declared billed totals over observed usage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR
from hashlib import sha256
from typing import Sequence

from agentops.core.attribution import AttributionResolution
from agentops.core.cost import (
    AllocationKey,
    CostAllocationRow,
    CostBreakdown,
    CostComponent,
    CostComponentSummary,
    CostConfidence,
    CostPeriod,
    CostPeriodRef,
    CostUsageObservation,
    CostViewData,
    CurrencySubtotal,
    MAX_COST_ROWS,
)
from agentops.core.observe import CoverageState


_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)
_UNATTRIBUTED_KEYS: dict[CostBreakdown, str] = {
    "agents": "__unattributed_agent__",
    "tools": "__unattributed_tool__",
    "runs": "__unattributed_run__",
}
_UNATTRIBUTED_DEPARTMENT_KEY = "__unattributed_department__"
_UNATTRIBUTED_USER_KEY = "__unattributed_user__"
_OTHER_USERS_KEY = "__other_users__"


@dataclass(frozen=True)
class _MeasuredUsage:
    values: tuple[Decimal | None, ...]
    reported: int
    complete: bool


@dataclass(frozen=True)
class _ConsumerUsage:
    consumer_key: str
    identity: str | None
    unattributed: bool
    numerator: Decimal
    observations: tuple[CostUsageObservation, ...]


@dataclass
class _ComponentAllocation:
    component: CostComponent
    applied_key: AllocationKey | None
    fallback_used: bool
    confidence: CostConfidence
    coverage_state: CoverageState
    coverage_reason: str
    next_action: str | None
    rows: list[CostAllocationRow]
    attributed_amount: Decimal
    unattributed_amount: Decimal
    unallocated_amount: Decimal
    latest_observed_at: datetime | None


def _matches(
    component: CostComponent,
    observation: CostUsageObservation,
) -> bool:
    match = component.usage_match
    checks = (
        (match.source_resource_ids, observation.source_resource_id),
        (match.project_resource_ids, observation.project_resource_id),
        (match.agent_keys, observation.agent_key),
        (match.deployments, observation.deployment),
        (match.models, observation.model),
        (match.tool_names, observation.tool_name),
        (match.runtime_kinds, observation.runtime_kind),
    )
    return all(not allowed or actual in allowed for allowed, actual in checks)


def _measure(
    component: CostComponent,
    observations: Sequence[CostUsageObservation],
    key: AllocationKey,
) -> _MeasuredUsage:
    values: list[Decimal | None] = []
    completeness: list[bool] = []

    for observation in observations:
        value: Decimal | None
        complete = True
        if key == "weighted_tokens":
            weights = component.token_weights
            if weights is None:
                value = None
                complete = False
            else:
                weighted = Decimal(0)
                for field in _TOKEN_FIELDS:
                    weight = getattr(weights, field)
                    if weight is None:
                        continue
                    token_value = getattr(observation, field)
                    if token_value is None:
                        complete = False
                    else:
                        weighted += Decimal(token_value) * weight
                value = weighted if complete else None
        elif key == "total_tokens":
            input_tokens = observation.input_tokens
            output_tokens = observation.output_tokens
            if input_tokens is None and output_tokens is None:
                value = None
                complete = False
            else:
                value = Decimal((input_tokens or 0) + (output_tokens or 0))
                complete = input_tokens is not None and output_tokens is not None
        elif key == "tool_invocations":
            raw = observation.tool_invocations
            value = Decimal(raw) if raw is not None else None
            complete = raw is not None
        elif key == "active_session_seconds":
            value = observation.active_session_seconds
            complete = value is not None
        elif key == "credits":
            value = observation.credits
            complete = value is not None
        else:
            selected_operations = component.usage_match.credit_event_operations
            if observation.operation_name not in selected_operations:
                values.append(None)
                continue
            raw = observation.credit_events
            value = Decimal(raw) if raw is not None else None
            complete = raw is not None

        values.append(value)
        completeness.append(complete)

    return _MeasuredUsage(
        values=tuple(values),
        reported=sum(value is not None for value in values),
        complete=bool(completeness) and all(completeness),
    )


def _select_measure(
    component: CostComponent,
    observations: Sequence[CostUsageObservation],
) -> tuple[AllocationKey | None, _MeasuredUsage, bool]:
    preferred = _measure(component, observations, component.allocation_key)

    if component.allocation_key == "weighted_tokens" and not preferred.complete:
        if component.fallback_key is not None:
            fallback = _measure(component, observations, component.fallback_key)
            return component.fallback_key, fallback, True
        return None, preferred, False

    # Directly reported credits always win. Event counts are considered only
    # when no direct credit quantity was reported anywhere in the matched set.
    if component.allocation_key == "credits" and preferred.reported == 0:
        if component.fallback_key is not None:
            fallback = _measure(component, observations, component.fallback_key)
            return component.fallback_key, fallback, True
        return None, preferred, False

    if preferred.reported:
        return component.allocation_key, preferred, False
    return None, preferred, False


def _identity(
    observation: CostUsageObservation,
    breakdown: CostBreakdown,
) -> str | None:
    if breakdown == "agents":
        return observation.agent_key
    if breakdown == "tools":
        return observation.tool_name
    return observation.run_key


def _group_usage(
    observations: Sequence[CostUsageObservation],
    values: Sequence[Decimal | None],
    breakdown: CostBreakdown,
    *,
    department_resolutions: dict[str, AttributionResolution] | None = None,
    user_resolutions: dict[str, AttributionResolution] | None = None,
) -> list[_ConsumerUsage]:
    grouped: dict[
        tuple[str | None, str | None],
        tuple[bool, Decimal, list[CostUsageObservation]],
    ] = {}
    for observation, value in zip(observations, values, strict=True):
        if value is None or value <= 0:
            continue
        if department_resolutions is None and user_resolutions is None:
            identity = _identity(observation, breakdown)
        elif user_resolutions is not None:
            identity = (
                observation.user_key
                if observation.user_key in user_resolutions
                else None
            )
        else:
            resolution = (
                department_resolutions.get(observation.user_key)
                if observation.user_key is not None
                else None
            )
            identity = (
                resolution.department_id
                if resolution is not None
                and resolution.source in {"explicit_user", "principal_group"}
                else None
            )
        unattributed = identity is None
        # Tool and run views retain agent provenance so an agent drill-down can
        # hide rows only after the complete billed pool has been allocated.
        agent_scope = (
            observation.agent_key
            if department_resolutions is None
            and user_resolutions is None
            and breakdown != "agents"
            else None
        )
        group_key = (identity, agent_scope)
        current = grouped.get(group_key)
        if current is None:
            grouped[group_key] = (unattributed, value, [observation])
        else:
            grouped[group_key] = (
                current[0],
                current[1] + value,
                [*current[2], observation],
            )

    base_counts: dict[str, int] = {}
    for identity, _ in grouped:
        base = identity or (
            _UNATTRIBUTED_USER_KEY
            if user_resolutions is not None
            else _UNATTRIBUTED_DEPARTMENT_KEY
            if department_resolutions is not None
            else _UNATTRIBUTED_KEYS[breakdown]
        )
        base_counts[base] = base_counts.get(base, 0) + 1

    def consumer_key(identity: str | None, agent_scope: str | None) -> str:
        base = identity or (
            _UNATTRIBUTED_USER_KEY
            if user_resolutions is not None
            else _UNATTRIBUTED_DEPARTMENT_KEY
            if department_resolutions is not None
            else _UNATTRIBUTED_KEYS[breakdown]
        )
        if base_counts[base] == 1:
            return base
        scope = agent_scope or _UNATTRIBUTED_KEYS["agents"]
        candidate = f"{scope}::{base}"
        if len(candidate) <= 512:
            return candidate
        digest = sha256(candidate.encode("utf-8")).hexdigest()
        return f"{base[:446]}::{digest}"

    return [
        _ConsumerUsage(
            consumer_key=consumer_key(identity, agent_scope),
            identity=identity,
            unattributed=unattributed,
            numerator=numerator,
            observations=tuple(items),
        )
        for (identity, agent_scope), (
            unattributed,
            numerator,
            items,
        ) in grouped.items()
    ]


def _minor_quantum(minor_units: int) -> Decimal:
    return Decimal(1).scaleb(-minor_units)


def _minor_amount(minor_value: int, minor_units: int) -> Decimal:
    return (Decimal(minor_value) * _minor_quantum(minor_units)).quantize(
        _minor_quantum(minor_units)
    )


def _single_value(
    observations: Sequence[CostUsageObservation],
    field: str,
) -> str | None:
    values = {
        value
        for observation in observations
        if (value := getattr(observation, field)) is not None
    }
    return next(iter(values)) if len(values) == 1 else None


def _latest(
    observations: Sequence[CostUsageObservation],
) -> datetime | None:
    values = [
        observation.latest_observed_at
        for observation in observations
        if observation.latest_observed_at is not None
    ]
    return max(values, default=None)


def _coverage(
    *,
    matched: Sequence[CostUsageObservation],
    measure: _MeasuredUsage,
    denominator: Decimal,
    fallback_used: bool,
    has_unattributed: bool,
) -> tuple[CostConfidence, CoverageState, str, str | None]:
    if not matched:
        return (
            "unavailable",
            "no_data",
            "No observations matched the configured usage selectors.",
            "Verify the usage selectors and telemetry activity for this period.",
        )
    if measure.reported == 0:
        return (
            "unavailable",
            "not_reported",
            "The matched observations did not report the allocation key.",
            "Enable telemetry for the configured allocation key.",
        )
    if denominator <= 0:
        return (
            "unavailable",
            "no_data",
            "The reported allocation key has no positive usage denominator.",
            "Verify that the period contains positive observed usage.",
        )

    complete = measure.complete and all(
        observation.coverage_complete for observation in matched
    )
    if not complete or has_unattributed:
        reasons: list[str] = []
        if not complete:
            reasons.append("allocation-key or readable-period coverage is partial")
        if has_unattributed:
            reasons.append("some observed usage has no consumer identity")
        return (
            "low",
            "partial",
            "Allocation is based on observed usage, but " + " and ".join(reasons) + ".",
            "Complete telemetry coverage and consumer attribution.",
        )
    if fallback_used:
        return (
            "medium",
            "available",
            "Complete observed usage uses the explicitly configured fallback key.",
            None,
        )
    return (
        "high",
        "available",
        "The preferred allocation key is completely reported and attributed.",
        None,
    )


def _component_provenance(
    period: CostPeriod,
    component: CostComponent,
    *,
    breakdown: CostBreakdown,
    applied_key: AllocationKey | None,
    fallback_used: bool,
) -> dict[str, object]:
    return {
        "period_id": period.id,
        "starts_at": period.starts_at,
        "ends_at": period.ends_at,
        "component_id": component.id,
        "component_type": component.type,
        "billing_boundary": component.billing_boundary,
        "billed_source": component.billed_source,
        "allocation_model": component.allocation_model,
        "preferred_key": component.allocation_key,
        "applied_key": applied_key,
        "fallback_used": fallback_used,
        "breakdown": breakdown,
        "currency": component.currency,
        "currency_minor_units": component.currency_minor_units,
    }


def _allocate_component(
    period: CostPeriod,
    component: CostComponent,
    observations: Sequence[CostUsageObservation],
    *,
    breakdown: CostBreakdown,
    calculated_at: datetime,
    department_resolutions: dict[str, AttributionResolution] | None = None,
    user_resolutions: dict[str, AttributionResolution] | None = None,
) -> _ComponentAllocation:
    matched = tuple(
        observation for observation in observations if _matches(component, observation)
    )
    applied_key, measure, fallback_used = _select_measure(component, matched)
    denominator = sum(
        (value for value in measure.values if value is not None),
        Decimal(0),
    )
    consumers = (
        _group_usage(
            matched,
            measure.values,
            breakdown,
            department_resolutions=department_resolutions,
            user_resolutions=user_resolutions,
        )
        if applied_key is not None
        else []
    )
    has_unattributed = any(consumer.unattributed for consumer in consumers)
    confidence, coverage_state, coverage_reason, next_action = _coverage(
        matched=matched,
        measure=measure,
        denominator=denominator,
        fallback_used=fallback_used,
        has_unattributed=has_unattributed,
    )
    latest = _latest(matched)

    if applied_key is None or denominator <= 0:
        return _ComponentAllocation(
            component=component,
            applied_key=applied_key,
            fallback_used=fallback_used if applied_key is not None else False,
            confidence=confidence,
            coverage_state=coverage_state,
            coverage_reason=coverage_reason,
            next_action=next_action,
            rows=[],
            attributed_amount=_minor_amount(0, component.currency_minor_units),
            unattributed_amount=_minor_amount(0, component.currency_minor_units),
            unallocated_amount=component.billed_total,
            latest_observed_at=latest,
        )

    scale = Decimal(10) ** component.currency_minor_units
    total_minor = int(component.billed_total * scale)
    allocations: dict[str, int] = {}
    remainders: list[tuple[Decimal, str]] = []
    for consumer in consumers:
        raw_minor = Decimal(total_minor) * consumer.numerator / denominator
        base_minor = int(raw_minor.to_integral_value(rounding=ROUND_FLOOR))
        allocations[consumer.consumer_key] = base_minor
        remainders.append((raw_minor - Decimal(base_minor), consumer.consumer_key))

    remaining = total_minor - sum(allocations.values())
    remainder_order = sorted(remainders, key=lambda item: (-item[0], item[1]))
    adjusted = {key for _, key in remainder_order[:remaining]}
    for key in adjusted:
        allocations[key] += 1

    provenance = _component_provenance(
        period,
        component,
        breakdown=breakdown,
        applied_key=applied_key,
        fallback_used=fallback_used,
    )
    rows: list[CostAllocationRow] = []
    for consumer in consumers:
        items = consumer.observations
        identity_fields: dict[str, str | None]
        if user_resolutions is not None:
            identity_fields = {
                "agent_key": _single_value(items, "agent_key"),
                "tool_name": _single_value(items, "tool_name"),
                "run_key": _single_value(items, "run_key"),
            }
            consumer_kind = "unattributed" if consumer.unattributed else "user"
        elif department_resolutions is not None:
            identity_fields = {
                "agent_key": _single_value(items, "agent_key"),
                "tool_name": _single_value(items, "tool_name"),
                "run_key": _single_value(items, "run_key"),
            }
            consumer_kind = "unattributed" if consumer.unattributed else "department"
        elif breakdown == "agents":
            identity_fields = {
                "agent_key": None if consumer.unattributed else consumer.consumer_key,
                "tool_name": _single_value(items, "tool_name"),
                "run_key": _single_value(items, "run_key"),
            }
            consumer_kind = "unattributed" if consumer.unattributed else "agent"
        elif breakdown == "tools":
            identity_fields = {
                "agent_key": _single_value(items, "agent_key"),
                "tool_name": consumer.identity,
                "run_key": _single_value(items, "run_key"),
            }
            consumer_kind = "unattributed" if consumer.unattributed else "tool"
        else:
            identity_fields = {
                "agent_key": _single_value(items, "agent_key"),
                "tool_name": _single_value(items, "tool_name"),
                "run_key": consumer.identity,
            }
            consumer_kind = "unattributed" if consumer.unattributed else "run"

        rows.append(
            CostAllocationRow(
                **provenance,
                consumer_kind=consumer_kind,
                consumer_key=consumer.consumer_key,
                source_resource_id=_single_value(items, "source_resource_id"),
                project_resource_id=_single_value(items, "project_resource_id"),
                **identity_fields,
                amount=_minor_amount(
                    allocations[consumer.consumer_key],
                    component.currency_minor_units,
                ),
                usage_numerator=consumer.numerator,
                usage_denominator=denominator,
                usage_unit=applied_key,
                rounding_adjustment_minor_units=(
                    1 if consumer.consumer_key in adjusted else 0
                ),
                confidence=confidence,
                coverage_state=coverage_state,
                coverage_reason=coverage_reason,
                calculated_at=calculated_at,
                latest_observed_at=_latest(items),
            )
        )

    attributed = sum(
        (row.amount for row in rows if row.consumer_kind != "unattributed"),
        _minor_amount(0, component.currency_minor_units),
    )
    unattributed = sum(
        (row.amount for row in rows if row.consumer_kind == "unattributed"),
        _minor_amount(0, component.currency_minor_units),
    )
    return _ComponentAllocation(
        component=component,
        applied_key=applied_key,
        fallback_used=fallback_used,
        confidence=confidence,
        coverage_state=coverage_state,
        coverage_reason=coverage_reason,
        next_action=next_action,
        rows=rows,
        attributed_amount=attributed,
        unattributed_amount=unattributed,
        unallocated_amount=_minor_amount(0, component.currency_minor_units),
        latest_observed_at=latest,
    )


def _currency_subtotals(
    summaries: Sequence[CostComponentSummary],
) -> list[CurrencySubtotal]:
    grouped: dict[tuple[str, int], list[Decimal]] = {}
    for summary in summaries:
        totals = grouped.setdefault(
            (summary.currency, summary.currency_minor_units),
            [Decimal(0), Decimal(0), Decimal(0), Decimal(0)],
        )
        totals[0] += summary.declared_total
        totals[1] += summary.attributed_amount
        totals[2] += summary.unattributed_amount
        totals[3] += summary.unallocated_amount
    return [
        CurrencySubtotal(
            currency=currency,
            currency_minor_units=minor_units,
            declared_total=totals[0],
            attributed_amount=totals[1],
            unattributed_amount=totals[2],
            unallocated_amount=totals[3],
        )
        for (currency, minor_units), totals in sorted(grouped.items())
    ]


def allocate_cost_period(
    period: CostPeriod,
    observations: Sequence[CostUsageObservation],
    *,
    breakdown: CostBreakdown = "agents",
    calculated_at: datetime,
    component_id: str | None = None,
    cost_agent_key: str | None = None,
    department_resolutions: Sequence[AttributionResolution] | None = None,
    department_id: str | None = None,
    user_resolutions: Sequence[AttributionResolution] | None = None,
    user_key: str | None = None,
    fold_users: bool = True,
) -> CostViewData:
    """Allocate one configured period without mutating inputs or inferring usage.

    Department allocation consumes already resolved pseudonymous-user mappings.
    It allocates the complete selected component before applying a department
    filter, so filtering cannot change a row amount or its denominator.
    """
    if department_resolutions is not None and user_resolutions is not None:
        raise ValueError("department and user cost attribution are mutually exclusive")
    resolution_by_user: dict[str, AttributionResolution] | None = None
    attribution_resolutions = (
        department_resolutions
        if department_resolutions is not None
        else user_resolutions
    )
    if attribution_resolutions is not None:
        attribution_name = (
            "department" if department_resolutions is not None else "user"
        )
        if component_id is None:
            raise ValueError(
                f"{attribution_name} cost allocation requires exactly one selected component"
            )
        if breakdown != "agents":
            raise ValueError(
                f"{attribution_name} cost allocation does not accept a cost breakdown"
            )
        if cost_agent_key is not None:
            raise ValueError(
                f"{attribution_name} cost allocation does not accept an agent filter"
            )
        resolution_by_user = {}
        for resolution in attribution_resolutions:
            if resolution.user_key in resolution_by_user:
                raise ValueError(
                    f"{attribution_name} resolutions must contain unique user keys"
                )
            resolution_by_user[resolution.user_key] = resolution
    elif department_id is not None:
        raise ValueError("department_id requires department_resolutions")
    elif user_key is not None:
        raise ValueError("user_key requires user_resolutions")

    components = list(period.components)
    if component_id is not None:
        components = [
            component for component in components if component.id == component_id
        ]
        if not components:
            raise ValueError(f"Unknown cost component ID: {component_id}")

    allocations = [
        _allocate_component(
            period,
            component,
            observations,
            breakdown=breakdown,
            calculated_at=calculated_at,
            department_resolutions=(
                resolution_by_user if department_resolutions is not None else None
            ),
            user_resolutions=(
                resolution_by_user if user_resolutions is not None else None
            ),
        )
        for component in components
    ]
    all_rows = [row for allocation in allocations for row in allocation.rows]
    all_rows.sort(key=lambda row: (-row.amount, row.component_id, row.consumer_key))

    if fold_users and user_resolutions is not None and user_key is None:
        user_rows = [row for row in all_rows if row.consumer_kind == "user"]
        if len(user_rows) > MAX_COST_ROWS:
            retained = user_rows[: MAX_COST_ROWS - 1]
            hidden = user_rows[MAX_COST_ROWS - 1 :]
            template = hidden[0]
            other = template.model_copy(
                update={
                    "consumer_kind": "other_users",
                    "consumer_key": _OTHER_USERS_KEY,
                    "source_resource_id": None,
                    "project_resource_id": None,
                    "agent_key": None,
                    "tool_name": None,
                    "run_key": None,
                    "amount": sum((row.amount for row in hidden), Decimal(0)),
                    "usage_numerator": sum(
                        (row.usage_numerator for row in hidden), Decimal(0)
                    ),
                    "rounding_adjustment_minor_units": sum(
                        row.rounding_adjustment_minor_units for row in hidden
                    ),
                    "latest_observed_at": max(
                        (
                            row.latest_observed_at
                            for row in hidden
                            if row.latest_observed_at is not None
                        ),
                        default=None,
                    ),
                }
            )
            all_rows = [
                *retained,
                other,
                *(row for row in all_rows if row.consumer_kind != "user"),
            ]

    displayed = all_rows
    filtered_rows: set[int] = set()
    if cost_agent_key is not None:
        displayed = []
        for row in all_rows:
            if row.agent_key is not None and row.agent_key != cost_agent_key:
                filtered_rows.add(id(row))
            else:
                displayed.append(row)
    if department_id is not None:
        filtered = []
        for row in displayed:
            if row.consumer_kind == "department" and row.consumer_key != department_id:
                filtered_rows.add(id(row))
            else:
                filtered.append(row)
        displayed = filtered
    if user_key is not None:
        filtered = []
        for row in displayed:
            if row.consumer_kind == "user" and row.consumer_key != user_key:
                filtered_rows.add(id(row))
            else:
                filtered.append(row)
        displayed = filtered
    preserve_unbounded_users = (
        not fold_users and user_resolutions is not None and user_key is None
    )
    omitted_by_bound = [] if preserve_unbounded_users else displayed[MAX_COST_ROWS:]
    if not preserve_unbounded_users:
        displayed = displayed[:MAX_COST_ROWS]
    omitted_ids = filtered_rows | {id(row) for row in omitted_by_bound}

    summaries: list[CostComponentSummary] = []
    for allocation in allocations:
        component_rows = allocation.rows
        shown = [
            row for row in displayed if row.component_id == allocation.component.id
        ]
        omitted_amount = sum(
            (
                row.amount
                for row in component_rows
                if id(row) in omitted_ids and row.consumer_kind != "unattributed"
            ),
            _minor_amount(0, allocation.component.currency_minor_units),
        )
        summaries.append(
            CostComponentSummary(
                **_component_provenance(
                    period,
                    allocation.component,
                    breakdown=breakdown,
                    applied_key=allocation.applied_key,
                    fallback_used=allocation.fallback_used,
                ),
                declared_total=allocation.component.billed_total,
                attributed_amount=allocation.attributed_amount,
                unattributed_amount=allocation.unattributed_amount,
                unallocated_amount=allocation.unallocated_amount,
                omitted_allocated_amount=omitted_amount,
                rows_shown=len(shown),
                rows_total=len(component_rows),
                confidence=allocation.confidence,
                coverage_state=allocation.coverage_state,
                coverage_reason=allocation.coverage_reason,
                next_action=allocation.next_action,
            )
        )

    payload = {
        "period": CostPeriodRef(
            id=period.id,
            starts_at=period.starts_at,
            ends_at=period.ends_at,
        ),
        "breakdown": breakdown,
        "component_filter": component_id,
        "components": summaries,
        "rows": displayed,
        "currency_subtotals": _currency_subtotals(summaries),
        "calculated_at": calculated_at,
        "latest_observed_at": max(
            (
                allocation.latest_observed_at
                for allocation in allocations
                if allocation.latest_observed_at is not None
            ),
            default=None,
        ),
    }
    return (
        CostViewData.model_construct(**payload)
        if preserve_unbounded_users
        else CostViewData(**payload)
    )
