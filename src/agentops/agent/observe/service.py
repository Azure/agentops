"""Observe orchestration: normalization, deterministic coverage, and caching.

Coordinates scope-bounded discovery (:mod:`agentops.agent.observe.discovery`)
and bounded per-source querying (:mod:`agentops.agent.observe.queries`) with
the shared, non-sensitive :class:`~agentops.agent.observe.cache.ObserveCache`
into one identity/scope/filter-keyed Observe response.

Normalizes raw query rows into the versioned contracts in
:mod:`agentops.core.observe` (:class:`ObservedAgent`, :class:`ObservedTool`,
:class:`ObservedRun`, :class:`ModelUsage`, :class:`CoverageResult`) and
classifies coverage deterministically: it never infers ``available`` from
the mere absence of an error, and never treats an empty result or an
all-null reportable field as a failure. Raw
generative-AI content never reaches the shared cache -- only normalized
aggregates are ever passed to :meth:`ObserveCache.set`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Coroutine, Hashable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, Mapping, Protocol, Sequence

from agentops.agent.observe.cache import ObserveCache
from agentops.agent.observe.adapters import normalize_user_attribution_coverage
from agentops.agent.observe.attribution import (
    SingletonAttributionError,
    classify_department_cardinality,
    config_with_principal_group_mappings,
    principal_alias_user_keys,
    rank_and_fold_user_usage,
    resolve_department,
    sum_usage,
    usage_from_row,
    zero_usage,
)
from agentops.agent.observe.cost_allocation import allocate_cost_period
from agentops.agent.observe.queries import (
    MAX_ROWS_PER_QUERY,
    TOKEN_CLASS_ALIASES,
    TOKEN_CLASS_ALIAS_NAMES,
    SourceResult,
    SourceStatus,
)
from agentops.core.cost import (
    AllocationKey,
    CostBreakdown,
    CostComponent,
    CostComponentSummary,
    CostModel,
    CostPeriod,
    CostUsageObservation,
)
from agentops.core.attribution import (
    AttributionConfiguration,
    AttributionCost,
    AttributionResolution,
    AttributionUsage,
    AttributionViewData,
    CostAttributionSummary,
    DepartmentAttributionRow,
    OtherUsersAttributionRow,
    UsageAttributionSummary,
    UserAttributionRow,
    issue_department_filter_token,
    issue_user_filter_token,
    validate_department_filter_token,
    validate_user_filter_token,
)
from agentops.core.observe import (
    AttributionQueryRequest,
    AttributionResponse,
    CoverageResult,
    CoverageState,
    ModelUsage,
    ObserveFilterState,
    ObserveScope,
    ObservedAgent,
    ObservedRun,
    ObservedTool,
    QueryDiagnostics,
    QuerySourceFailure,
    ResultBounds,
    ResourceInventory,
    RuntimeKind,
    TelemetrySource,
    UserAttributionCoverage,
    canonical_arm_id,
)

#: Identity/scope/filter cache entries stay fresh for two minutes (T046).
CACHE_TTL_SECONDS = 120.0
INVENTORY_CACHE_TTL_SECONDS = 15 * 60.0
VIEW_STALE_TTL_SECONDS = 5 * 60.0

logger = logging.getLogger(__name__)

View = Literal["overview", "agents", "models", "tools", "runs", "cost"]


class Clock(Protocol):
    """Injectable UTC clock."""

    def __call__(self) -> datetime: ...


class DiscoveryClient(Protocol):
    """Discovers readable Azure resources inside one configured scope."""

    async def discover(self, scope: ObserveScope) -> ResourceInventory: ...


class QueryClient(Protocol):
    """Executes bounded per-source telemetry queries for one Observe view."""

    async def query(
        self,
        sources: Sequence[TelemetrySource],
        filters: ObserveFilterState,
        *,
        view: View,
    ) -> list[SourceResult]: ...

    async def query_department_usage(
        self,
        sources: Sequence[TelemetrySource],
        filters: ObserveFilterState,
        *,
        config: AttributionConfiguration,
        tenant_id: str,
        department_id: str | None = None,
        principal_user_keys: Sequence[str] = (),
        cost_component: CostComponent | None = None,
    ) -> list[SourceResult]: ...

    async def query_user_usage(
        self,
        sources: Sequence[TelemetrySource],
        filters: ObserveFilterState,
        *,
        config: AttributionConfiguration,
        tenant_id: str,
        department_id: str | None = None,
        selected_user_key: str | None = None,
        cost_component: CostComponent | None = None,
    ) -> list[SourceResult]: ...


class RuntimeContext(Protocol):
    """Supplies the identity and mode for one Observe request."""

    @property
    def mode(self) -> str: ...

    @property
    def credential_identity(self) -> str: ...


@dataclass(frozen=True)
class PartialFailure:
    """One safe, actionable summary of a source that did not fully succeed (T061).

    ``reason`` is always passed through :func:`safe_failure_reason`, so raw
    Azure SDK error text/content never reaches the response -- only a short,
    redacted, single-line summary plus a fixed, generic ``next_action`` are
    exposed. Every query/agent-detail response carries this list (it is
    empty when every source succeeded) alongside diagnostics, source
    counts, coverage, and ``refreshed_at``.
    """

    source_id: str
    status: SourceStatus
    reason: str
    next_action: str


@dataclass(frozen=True)
class ObserveResult:
    """Normalized, cache-safe response for one Observe request."""

    view: View
    data: Any
    coverage: list[CoverageResult]
    diagnostics: QueryDiagnostics
    partial_failures: list[PartialFailure]
    bounds: ResultBounds | None
    refreshed_at: datetime
    cache_status: Literal["hit", "miss", "bypass", "stale"]


@dataclass(frozen=True)
class _CachedView:
    """The subset of :class:`ObserveResult` that is safe to cache.

    ``cache_status`` and ``refreshed_at`` describe the *current* request,
    not the underlying data, so they are recomputed on every read instead of
    being persisted alongside the cached value. ``partial_failures``, like
    ``diagnostics`` and ``coverage``, describes the underlying query
    execution itself, so it is cached and served as-is on a cache hit.
    ``bounds`` is similarly cached because it describes the query result,
    rather than the request that reads it.
    """

    view: View
    data: Any
    coverage: list[CoverageResult]
    diagnostics: QueryDiagnostics
    partial_failures: list[PartialFailure]
    bounds: ResultBounds | None
    refreshed_at: datetime
    """When the underlying data was produced; stable across cache hits."""


# ---------------------------------------------------------------------------
# Normalization: rows -> contracts, source attribution, token labelling (T045)
# ---------------------------------------------------------------------------


_COPILOT_STUDIO_PROVIDERS = frozenset(
    {
        "copilot studio",
        "copilot_studio",
        "microsoft copilot studio",
        "power virtual agents",
        "power_virtual_agents",
    }
)

_HOSTED_AGENT_KINDS = frozenset({"hosted", "container", "foundry_hosted"})
_PROMPT_AGENT_KINDS = frozenset({"prompt", "foundry_prompt"})
_HOSTED_AGENT_PROVIDERS = frozenset(
    {"azure.ai.foundry", "microsoft.agent_framework", "microsoft agent framework"}
)
_PROMPT_AGENT_PROVIDERS = frozenset({"microsoft.foundry", "microsoft foundry"})


def _normalized_runtime_value(value: Any) -> str | None:
    """Return a normalized, non-empty metadata value without coercing objects."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("-", "_")
    return normalized or None


def _is_copilot_studio_provider(provider_name: Any, system: Any) -> bool:
    values = (
        _normalized_runtime_value(provider_name),
        _normalized_runtime_value(system),
    )
    return any(
        value is not None and value.replace("_", " ") in _COPILOT_STUDIO_PROVIDERS
        for value in values
    )


def _inventory_agent_records(
    inventory: ResourceInventory | None,
) -> list[Mapping[str, Any]]:
    """Return explicit agent records exposed by discovery, if any.

    Discovery does not manufacture an agent registry. This helper only reads
    explicit ``agents``/``agent_definitions`` collections already returned by
    the control plane, so a missing record cannot accidentally become a
    hosted-or-prompt guess.
    """
    if inventory is None:
        return []

    records: list[Mapping[str, Any]] = []
    for resource in [*inventory.projects, *inventory.foundry_resources]:
        if not isinstance(resource, Mapping):
            continue
        containers: list[Any] = [resource]
        properties = resource.get("properties")
        if isinstance(properties, Mapping):
            containers.append(properties)
        for container in containers:
            if not isinstance(container, Mapping):
                continue
            for key in ("agents", "agent_definitions", "agentDefinitions"):
                candidates = container.get(key)
                if isinstance(candidates, Mapping):
                    candidates = list(candidates.values())
                if isinstance(candidates, Sequence) and not isinstance(
                    candidates, (str, bytes, bytearray)
                ):
                    records.extend(
                        item for item in candidates if isinstance(item, Mapping)
                    )
    return records


def _inventory_agent_kind(
    inventory: ResourceInventory | None,
    *,
    agent_id: str | None = None,
    agent_name: str | None = None,
) -> RuntimeKind | None:
    """Resolve one exact agent identity to an explicit control-plane kind."""
    if not agent_id and not agent_name:
        return None

    matches: set[RuntimeKind] = set()
    for record in _matching_inventory_agents(
        inventory, agent_id=agent_id, agent_name=agent_name
    ):
        kind = _normalized_runtime_value(
            record.get("runtime_kind", record.get("kind", record.get("type")))
        )
        if kind in _HOSTED_AGENT_KINDS:
            matches.add("foundry_hosted")
        elif kind in _PROMPT_AGENT_KINDS:
            matches.add("foundry_prompt")

    return matches.pop() if len(matches) == 1 else None


def _matching_inventory_agents(
    inventory: ResourceInventory | None,
    *,
    agent_id: str | None = None,
    agent_name: str | None = None,
) -> list[Mapping[str, Any]]:
    """Return records that explicitly match a supplied telemetry identity."""
    matches: list[Mapping[str, Any]] = []
    for record in _inventory_agent_records(inventory):
        record_id = record.get("agent_id", record.get("id"))
        record_name = record.get("agent_name", record.get("name"))
        matches_id = agent_id is not None and record_id == agent_id
        matches_name = agent_name is not None and record_name == agent_name
        if matches_id or matches_name:
            matches.append(record)
    return matches


def classify_runtime(
    *,
    agent_id: str | None,
    agent_name: str | None,
    provider_name: Any = None,
    system: Any = None,
    inventory: ResourceInventory | None = None,
) -> RuntimeKind:
    """Classify from explicit telemetry and control-plane evidence only.

    A managed identifier alone proves neither the Foundry runtime shape nor
    an inventory match. Conflicting or incomplete evidence therefore remains
    ``unknown`` instead of preserving the retired coarse classification.
    """
    if _is_copilot_studio_provider(provider_name, system):
        return "copilot_studio"

    provider = _normalized_runtime_value(provider_name)
    if provider is not None:
        readable_provider = provider.replace("_", " ")
        if provider in _HOSTED_AGENT_PROVIDERS or readable_provider in _HOSTED_AGENT_PROVIDERS:
            return "foundry_hosted"
        if provider in _PROMPT_AGENT_PROVIDERS or readable_provider in _PROMPT_AGENT_PROVIDERS:
            return "foundry_prompt"

    if agent_id:
        return _inventory_agent_kind(inventory, agent_id=agent_id) or "unknown"

    if agent_name:
        return (
            "external_registered"
            if _matching_inventory_agents(inventory, agent_name=agent_name)
            else "external_unregistered"
        )

    return "unknown"


def token_reporting_state(
    *, input_tokens: int | None, output_tokens: int | None
) -> Literal["reported", "not_reported"]:
    """Distinguish "this dimension is not emitted" from a genuine zero (T059)."""
    return (
        "reported"
        if input_tokens is not None or output_tokens is not None
        else "not_reported"
    )


@dataclass(frozen=True)
class TokenClassInventory:
    state: Literal["reported", "partial", "not_reported"]
    reported_classes: tuple[str, ...]
    missing_classes: tuple[str, ...]
    partially_reported_classes: tuple[str, ...] = ()


_TOKEN_CLASS_FIELDS = (
    ("cache-read", "cache_read_tokens"),
    ("cache-write", "cache_write_tokens"),
    ("reasoning", "reasoning_tokens"),
)


def token_class_inventory(rows: Sequence[ModelUsage]) -> TokenClassInventory:
    """Summarize normalized class reporting across rows that carry token data."""
    qualifying_rows = [
        row
        for row in rows
        if any(
            value is not None
            for value in (
                row.input_tokens,
                row.output_tokens,
                row.cache_read_tokens,
                row.cache_write_tokens,
                row.reasoning_tokens,
            )
        )
        or bool(row.additional_token_classes)
    ]
    reported_classes = tuple(
        label
        for label, field in _TOKEN_CLASS_FIELDS
        if any(getattr(row, field) is not None for row in qualifying_rows)
    )
    missing_classes = tuple(
        label for label, _field in _TOKEN_CLASS_FIELDS if label not in reported_classes
    )
    partially_reported_names = {
        label
        for row in qualifying_rows
        for label in row.partially_reported_token_classes
    }
    partially_reported_classes = tuple(
        label
        for label, _field in _TOKEN_CLASS_FIELDS
        if label in partially_reported_names
    )
    if not reported_classes:
        state: Literal["reported", "partial", "not_reported"] = "not_reported"
    elif missing_classes or partially_reported_classes:
        state = "partial"
    else:
        state = "reported"
    return TokenClassInventory(
        state=state,
        reported_classes=reported_classes,
        missing_classes=missing_classes,
        partially_reported_classes=partially_reported_classes,
    )


def _project_resource_id_for_row(
    row: Mapping[str, Any], *, source: TelemetrySource
) -> str | None:
    """Resolve row-level project attribution within the telemetry source boundary."""
    raw_project_id = row.get("project_resource_id")
    if raw_project_id in (None, ""):
        if len(source.project_resource_ids) == 1:
            return source.project_resource_ids[0]
        return None
    if not isinstance(raw_project_id, str):
        raise ValueError("telemetry row project_resource_id must be a string")
    try:
        project_resource_id = canonical_arm_id(raw_project_id)
    except ValueError as exc:
        raise ValueError("telemetry row has an invalid project_resource_id") from exc
    if project_resource_id not in source.project_resource_ids:
        raise ValueError(
            "telemetry row project_resource_id is outside its source boundary"
        )
    return project_resource_id


def normalize_agent_row(
    row: Mapping[str, Any],
    *,
    source: TelemetrySource,
    inventory: ResourceInventory | None = None,
) -> ObservedAgent:
    """Normalize one ``build_agents_query`` result row into an :class:`ObservedAgent`."""
    agent_key = str(row.get("agent_key") or "unknown")
    last_seen = row.get("last_seen")
    if not isinstance(last_seen, datetime):
        raise ValueError(
            f"agent row for {agent_key!r} is missing a last_seen timestamp"
        )
    agent_id = row.get("agent_id") or None
    agent_name = row.get("agent_name") or None
    project_resource_id = _project_resource_id_for_row(row, source=source)
    return ObservedAgent(
        key=agent_key,
        source_id=source.source_id,
        agent_id=agent_id,
        agent_name=agent_name,
        project_resource_id=project_resource_id,
        foundry_resource_id=source.foundry_resource_id,
        source_kind=classify_runtime(
            agent_id=agent_id,
            agent_name=agent_name,
            provider_name=row.get("provider_name", row.get("gen_ai.provider.name")),
            system=row.get("system", row.get("gen_ai.system")),
            inventory=inventory,
        ),
        model=row.get("model") or None,
        last_seen=last_seen,
        invocations=int(row.get("invocations") or 0),
        failures=int(row.get("failures") or 0),
        p95_latency_ms=row.get("p95_latency_ms"),
        input_tokens=row.get("input_tokens"),
        output_tokens=row.get("output_tokens"),
        cache_read_tokens=row.get("cache_read_tokens"),
        cache_write_tokens=row.get("cache_write_tokens"),
        reasoning_tokens=row.get("reasoning_tokens"),
    )


def _required_datetime(
    row: Mapping[str, Any], *, field: str, row_label: str
) -> datetime:
    value = row.get(field)
    if not isinstance(value, datetime):
        raise ValueError(f"{row_label} row is missing a {field} timestamp")
    return value


def _required_text(row: Mapping[str, Any], *, field: str, row_label: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{row_label} row is missing a {field}")
    return value


def _nullable_int(row: Mapping[str, Any], *, field: str) -> int | None:
    value = row.get(field)
    return None if value is None else int(value)


def _row_runtime(
    row: Mapping[str, Any],
    *,
    agent_id: str | None,
    agent_name: str | None,
    inventory: ResourceInventory | None,
) -> RuntimeKind:
    return classify_runtime(
        agent_id=agent_id,
        agent_name=agent_name,
        provider_name=row.get("provider_name", row.get("gen_ai.provider.name")),
        system=row.get("system", row.get("gen_ai.system")),
        inventory=inventory,
    )


def normalize_tool_row(
    row: Mapping[str, Any],
    *,
    source: TelemetrySource,
    inventory: ResourceInventory | None = None,
) -> ObservedTool:
    """Normalize one bounded tools aggregate without synthesizing token data."""
    tool_name = _required_text(row, field="tool_name", row_label="tool")
    agent_key = _required_text(row, field="agent_key", row_label="tool")
    agent_id = row.get("agent_id") or None
    agent_name = row.get("agent_name") or None
    return ObservedTool(
        source_id=source.source_id,
        tool_name=tool_name,
        agent_key=agent_key,
        agent_id=agent_id,
        agent_name=agent_name,
        project_resource_id=_project_resource_id_for_row(row, source=source),
        foundry_resource_id=source.foundry_resource_id,
        source_kind=_row_runtime(
            row, agent_id=agent_id, agent_name=agent_name, inventory=inventory
        ),
        last_seen=_required_datetime(row, field="last_seen", row_label="tool"),
        invocations=int(row.get("invocations") or 0),
        failures=int(row.get("failures") or 0),
        p95_latency_ms=row.get("p95_latency_ms"),
    )


def normalize_run_row(
    row: Mapping[str, Any],
    *,
    source: TelemetrySource,
    window_end: datetime,
    inventory: ResourceInventory | None = None,
    settling_margin: timedelta = timedelta(seconds=CACHE_TTL_SECONDS),
) -> ObservedRun:
    """Normalize one run and derive a sticky, window-scoped status."""
    run_key = _required_text(row, field="run_key", row_label="run")
    run_key_kind = _required_text(row, field="run_key_kind", row_label="run")
    agent_key = _required_text(row, field="agent_key", row_label="run")
    started_at = _required_datetime(row, field="started_at", row_label="run")
    last_activity_at = _required_datetime(
        row, field="last_activity_at", row_label="run"
    )
    failed_turns = int(row.get("failed_turns") or 0)
    tool_failures = int(row.get("tool_failures") or 0)
    if failed_turns > 0 or tool_failures > 0:
        status = "failed"
    elif last_activity_at >= window_end - settling_margin:
        status = "in_progress"
    else:
        status = "succeeded"

    agent_id = row.get("agent_id") or None
    agent_name = row.get("agent_name") or None
    normalized: dict[str, Any] = {
        "source_id": source.source_id,
        "run_key": run_key,
        "run_key_kind": run_key_kind,
        "agent_key": agent_key,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "project_resource_id": _project_resource_id_for_row(row, source=source),
        "foundry_resource_id": source.foundry_resource_id,
        "source_kind": _row_runtime(
            row, agent_id=agent_id, agent_name=agent_name, inventory=inventory
        ),
        "started_at": started_at,
        "last_activity_at": last_activity_at,
        "duration_ms": row.get("duration_ms"),
        "status": status,
        "turns": int(row.get("turns") or 0),
        "failed_turns": failed_turns,
        "tool_invocations": int(row.get("tool_invocations") or 0),
        "tool_failures": tool_failures,
        "input_tokens": _nullable_int(row, field="input_tokens"),
        "output_tokens": _nullable_int(row, field="output_tokens"),
        "cache_read_tokens": _nullable_int(row, field="cache_read_tokens"),
        "cache_write_tokens": _nullable_int(row, field="cache_write_tokens"),
        "reasoning_tokens": _nullable_int(row, field="reasoning_tokens"),
        "credits": None if row.get("credits") is None else str(row["credits"]),
        "credit_events": _nullable_int(row, field="credit_events"),
    }
    # These fields are additive in the cost contract. Keeping this conditional
    # lets the service remain compatible while the Observe contract rolls out.
    for field in ("deployment", "model", "operation_name"):
        if field in ObservedRun.model_fields:
            normalized[field] = row.get(field) or None
    return ObservedRun(**normalized)


def normalize_cost_run_observation(
    run: ObservedRun,
    *,
    source_resource_id: str,
    coverage_complete: bool,
    period: CostPeriod | None = None,
) -> CostUsageObservation:
    """Project a normalized run into metadata-only numeric cost usage."""
    if period is None:
        duration_seconds = (
            None
            if run.duration_ms is None
            else Decimal(str(run.duration_ms)) / Decimal(1000)
        )
        latest_observed_at = run.last_activity_at
    else:
        clipped_start = max(run.started_at, period.starts_at)
        clipped_end = min(run.last_activity_at, period.ends_at)
        if clipped_end <= clipped_start:
            raise ValueError("run has no activity inside the selected cost period")
        duration_seconds = (
            None
            if run.duration_ms is None
            else min(
                Decimal(str(run.duration_ms)) / Decimal(1000),
                Decimal(str((clipped_end - clipped_start).total_seconds())),
            )
        )
        latest_observed_at = clipped_end
    credits = None if run.credits is None else Decimal(run.credits)
    return CostUsageObservation(
        source_resource_id=source_resource_id,
        project_resource_id=run.project_resource_id,
        agent_key=_cost_identity(run.agent_key),
        run_key=run.run_key,
        runtime_kind=run.source_kind,
        deployment=getattr(run, "deployment", None),
        model=getattr(run, "model", None),
        operation_name=getattr(run, "operation_name", None),
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        cache_read_tokens=run.cache_read_tokens,
        cache_write_tokens=run.cache_write_tokens,
        reasoning_tokens=run.reasoning_tokens,
        tool_invocations=run.tool_invocations,
        active_session_seconds=duration_seconds,
        credits=credits,
        credit_events=run.credit_events,
        latest_observed_at=latest_observed_at,
        coverage_complete=coverage_complete,
    )


def _cost_identity(value: str | None) -> str | None:
    if value is None or not value.strip() or value.strip().lower() == "unknown":
        return None
    return value


def _normalize_cost_model_observation(
    usage: ModelUsage,
    *,
    source: TelemetrySource,
    inventory: ResourceInventory,
    coverage_complete: bool,
) -> CostUsageObservation:
    return CostUsageObservation(
        source_resource_id=source.foundry_resource_id or source.resource_id,
        project_resource_id=usage.project_resource_id,
        agent_key=_cost_identity(usage.agent_id),
        runtime_kind=classify_runtime(
            agent_id=usage.agent_id,
            agent_name=None,
            inventory=inventory,
        ),
        deployment=usage.deployment,
        model=usage.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        latest_observed_at=usage.last_seen,
        coverage_complete=coverage_complete,
    )


def _normalize_cost_tool_observation(
    tool: ObservedTool,
    *,
    source_resource_id: str,
    coverage_complete: bool,
) -> CostUsageObservation:
    return CostUsageObservation(
        source_resource_id=source_resource_id,
        project_resource_id=tool.project_resource_id,
        agent_key=_cost_identity(tool.agent_key),
        tool_name=tool.tool_name,
        runtime_kind=tool.source_kind,
        tool_invocations=tool.invocations,
        latest_observed_at=tool.last_seen,
        coverage_complete=coverage_complete,
    )


def _normalize_unattributed_tool_observation(
    row: Mapping[str, Any],
    *,
    source: TelemetrySource,
    coverage_complete: bool,
) -> CostUsageObservation | None:
    raw_count = row.get("unattributed_count")
    if (
        row.get("_metadata_only") is not True
        or raw_count is None
        or int(raw_count) <= 0
    ):
        return None
    project_resource_id = (
        source.project_resource_ids[0]
        if len(source.project_resource_ids) == 1
        else None
    )
    return CostUsageObservation(
        source_resource_id=source.foundry_resource_id or source.resource_id,
        project_resource_id=project_resource_id,
        agent_key=None,
        tool_name=None,
        runtime_kind="unknown",
        tool_invocations=int(raw_count),
        latest_observed_at=None,
        coverage_complete=coverage_complete,
    )


def normalize_model_row(
    row: Mapping[str, Any], *, source: TelemetrySource
) -> ModelUsage:
    """Normalize one ``build_models_query`` result row into a :class:`ModelUsage`."""
    project_resource_id = _project_resource_id_for_row(row, source=source)
    raw_classes = row.get("extra_token_classes")
    if isinstance(raw_classes, Mapping):
        candidates = dict(raw_classes)
    elif isinstance(raw_classes, str) and raw_classes.strip():
        try:
            decoded_classes = json.loads(raw_classes)
        except json.JSONDecodeError as exc:
            raise ValueError("extra_token_classes must be a JSON object") from exc
        if not isinstance(decoded_classes, Mapping):
            raise ValueError("extra_token_classes must be a JSON object")
        candidates = dict(decoded_classes)
    else:
        candidates = {}

    def token_count(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number < 0 or not number.is_integer():
            return None
        return int(number)

    normalized: dict[str, int | None] = {}
    for token_class, aliases in TOKEN_CLASS_ALIASES.items():
        value = token_count(row.get(f"{token_class}_tokens"))
        if value is None:
            value = next(
                (
                    parsed
                    for alias in aliases
                    if (parsed := token_count(candidates.get(alias))) is not None
                ),
                None,
            )
        normalized[token_class] = value
        for alias in aliases:
            candidates.pop(alias, None)

    additional_candidates = {
        name: parsed
        for name, value in candidates.items()
        if isinstance(name, str)
        and name.startswith("gen_ai.usage.")
        and name not in TOKEN_CLASS_ALIAS_NAMES
        and name not in {"gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens"}
        and (parsed := token_count(value)) is not None
    }
    ordered_additional = dict(sorted(additional_candidates.items()))
    additional_token_classes = dict(list(ordered_additional.items())[:5])
    token_class_values = tuple(normalized[name] for name in TOKEN_CLASS_ALIASES)
    reported_classes = sum(value is not None for value in token_class_values)

    def is_true(value: Any) -> bool:
        return value is True or (
            isinstance(value, str) and value.strip().lower() == "true"
        )

    partially_reported_token_classes = tuple(
        label
        for label, field in _TOKEN_CLASS_FIELDS
        if is_true(row.get(f"{field}_partial"))
    )
    return ModelUsage(
        source_id=source.source_id,
        project_resource_id=project_resource_id,
        agent_id=row.get("agent_id") or None,
        deployment=row.get("deployment") or None,
        model=row.get("model") or None,
        requests=int(row.get("requests") or 0),
        failures=int(row.get("failures") or 0),
        p95_latency_ms=row.get("p95_latency_ms"),
        input_tokens=row.get("input_tokens"),
        output_tokens=row.get("output_tokens"),
        cache_read_tokens=token_class_values[0],
        cache_write_tokens=token_class_values[1],
        reasoning_tokens=token_class_values[2],
        additional_token_classes=additional_token_classes,
        additional_token_classes_truncated=len(ordered_additional) > 5,
        partially_reported_token_classes=partially_reported_token_classes,
        token_classes_partial=(
            bool(partially_reported_token_classes)
            or 0 < reported_classes < len(token_class_values)
        ),
        last_seen=row.get("last_seen"),
    )


# ---------------------------------------------------------------------------
# Deterministic coverage classification and safe failure reasons (T059/T060)
# ---------------------------------------------------------------------------


def safe_failure_reason(raw_reason: str | None, *, default: str) -> str:
    """Return a short, non-leaky, actionable reason string for a failure."""
    if not raw_reason or not raw_reason.strip():
        return default
    text = raw_reason.strip().splitlines()[0]
    if len(text) > 200:
        text = text[:197] + "..."
    return text


_DISCOVERY_COVERAGE: dict[str, tuple[CoverageState, str, str]] = {
    "available": (
        "available",
        "The telemetry source is reachable for this request.",
        "No action needed.",
    ),
    "inaccessible": (
        "inaccessible",
        "The current identity cannot read this telemetry source.",
        "Grant Monitoring Reader on the Log Analytics workspace to the AgentOps identity.",
    ),
    "not_configured": (
        "not_configured",
        "No Application Insights / Log Analytics connection is linked to this project.",
        "Link Application Insights to this Foundry project.",
    ),
    "not_found": (
        "error",
        "The linked telemetry resource could not be found.",
        "Verify the linked resource still exists and retry discovery.",
    ),
    "error": (
        "error",
        "Discovery failed for this telemetry source.",
        "Check Azure Resource Graph access and retry.",
    ),
}


def classify_discovery_coverage(
    source: TelemetrySource,
    *,
    dimension: Literal["resource_access", "telemetry_connection"],
    refreshed_at: datetime,
) -> CoverageResult:
    """Map one discovery-time :class:`TelemetrySource` state to a :class:`CoverageResult`.

    Deterministic: driven entirely by ``source.state``, never inferred from
    an absent error (T059). Covers ``available``/``inaccessible``/
    ``not_configured``/``error``.
    """
    state, default_reason, next_action = _DISCOVERY_COVERAGE[source.state]
    reason = safe_failure_reason(source.reason, default=default_reason)
    return CoverageResult(
        source_id=source.source_id,
        dimension=dimension,
        state=state,
        reason=reason,
        next_action=next_action,
        refreshed_at=refreshed_at,
    )


#: Safe, actionable default (reason, next_action) for every non-``success``
#: :data:`SourceStatus`, shared by :func:`classify_query_coverage` (per
#: dimension) and :func:`_build_partial_failures` (T060/T061) so both surface
#: identical, redacted wording for the same underlying source outcome.
_PARTIAL_FAILURE_DEFAULTS: dict[SourceStatus, tuple[str, str]] = {
    "partial": (
        "The query returned partial data for this source.",
        "Retry later or narrow filters; some rows may be missing.",
    ),
    "timeout": (
        "The query exceeded its time budget for this source.",
        "Retry with a narrower time range or fewer sources.",
    ),
    "throttled": (
        "The query was throttled by Azure Monitor.",
        "Retry shortly; consider reducing concurrent Observe requests.",
    ),
    "error": (
        "The query failed for this source.",
        "Check workspace permissions and retry.",
    ),
}


def classify_query_coverage(
    *,
    source_id: str,
    dimension: Literal[
        "recent_traces",
        "agent_attribution",
        "model_attribution",
        "token_usage",
        "tool_attribution",
        "run_correlation",
    ],
    status: SourceStatus,
    row_count: int,
    reported: bool
    | Literal["reported", "partial", "not_reported"]
    | TokenClassInventory = True,
    reason: str | None = None,
    refreshed_at: datetime,
) -> CoverageResult:
    """Deterministically classify one source/dimension from its query outcome.

    Never infers ``available`` from the mere absence of an error, and never
    treats an empty result or an all-null reportable field as a failure: a
    genuine zero becomes ``no_data``/``not_reported``, not ``error`` (T059).
    Timeouts and throttling map to safe, actionable ``error`` reasons (T060)
    since :data:`~agentops.core.observe.CoverageState` has no dedicated
    state for either.
    """
    inventory = reported if isinstance(reported, TokenClassInventory) else None
    if inventory is not None:
        reporting_state = inventory.state
    elif reported == "partial":
        reporting_state = "partial"
    elif reported in (False, "not_reported"):
        reporting_state = "not_reported"
    else:
        reporting_state = "reported"

    if status == "success":
        if row_count == 0:
            state: CoverageState = "no_data"
            default_reason = "No matching telemetry rows were found in this window."
            next_action = "Widen the time range or confirm the workload was active."
        elif reporting_state == "not_reported":
            state = "not_reported"
            default_reason = "Telemetry rows exist but do not report this dimension."
            next_action = "Confirm the workload emits the expected gen_ai.* attributes."
        elif reporting_state == "partial":
            state = "partial"
            reported_names = (
                ", ".join(inventory.reported_classes) if inventory else "some"
            )
            missing_names = (
                ", ".join(inventory.missing_classes) if inventory else "some"
            )
            partial_names = (
                ", ".join(inventory.partially_reported_classes) if inventory else ""
            )
            reason_parts = [f"Reported granular token classes: {reported_names}."]
            action_parts: list[str] = []
            if partial_names:
                reason_parts.append(f"Intermittently reported: {partial_names}.")
                action_parts.append(f"emit {partial_names} consistently")
            if missing_names:
                reason_parts.append(f"Not reported: {missing_names}.")
                action_parts.append(f"emit {missing_names} under gen_ai.usage.*")
            default_reason = " ".join(reason_parts)
            next_action = (
                "Configure instrumentation to " + " and ".join(action_parts) + "."
            )
        else:
            state = "available"
            default_reason = "Telemetry rows were returned for this window."
            next_action = "No action needed."
    else:
        state = "partial" if status == "partial" else "error"
        default_reason, next_action = _PARTIAL_FAILURE_DEFAULTS[status]

    return CoverageResult(
        source_id=source_id,
        dimension=dimension,
        state=state,
        reason=safe_failure_reason(reason, default=default_reason),
        next_action=next_action,
        refreshed_at=refreshed_at,
    )


def _attribution_coverage(
    *,
    source_id: str,
    dimension: Literal["tool_attribution", "run_correlation"],
    status: SourceStatus,
    rows: Sequence[Mapping[str, Any]],
    attributed_rows: int,
    reason: str | None,
    refreshed_at: datetime,
) -> CoverageResult:
    """Explain missing tool/run identity without treating it as a zero."""
    coverage = classify_query_coverage(
        source_id=source_id,
        dimension=dimension,
        status=status,
        row_count=len(rows),
        reported=attributed_rows > 0,
        reason=reason,
        refreshed_at=refreshed_at,
    )
    if coverage.state != "not_reported":
        return coverage

    if dimension == "tool_attribution":
        default_reason = "Telemetry rows exist but do not include a usable tool name."
        next_action = "Confirm tool invocations emit the gen_ai.tool.name attribute."
    else:
        default_reason = (
            "Telemetry rows do not include a conversation-level run correlation."
        )
        next_action = (
            "Emit gen_ai.conversation.id or ensure trace correlation is preserved "
            "for agent turns."
        )
    return CoverageResult(
        source_id=source_id,
        dimension=dimension,
        state="not_reported",
        reason=safe_failure_reason(reason, default=default_reason),
        next_action=next_action,
        refreshed_at=refreshed_at,
    )


def _source_attribution_coverage(
    source: TelemetrySource,
    *,
    dimension: Literal["tool_attribution", "run_correlation"],
    refreshed_at: datetime,
) -> CoverageResult:
    """Expose a source's discovery failure on each affected view dimension."""
    state, default_reason, next_action = _DISCOVERY_COVERAGE[source.state]
    return CoverageResult(
        source_id=source.source_id,
        dimension=dimension,
        state=state,
        reason=safe_failure_reason(source.reason, default=default_reason),
        next_action=next_action,
        refreshed_at=refreshed_at,
    )


def _source_total_in_scope(rows: Sequence[Mapping[str, Any]]) -> int | None:
    """Read one source's advertised aggregate total without inventing a value."""
    totals: set[int] = set()
    for row in rows:
        total = row.get("total_in_scope")
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            return None
        totals.add(total)
    if len(totals) != 1:
        return None
    return totals.pop()


def _result_bounds(results: Sequence[SourceResult], *, rows_shown: int) -> ResultBounds:
    """Combine per-source query totals; an absent or partial total stays unknown."""
    total_in_scope = 0
    for result in results:
        if result.status != "success":
            return ResultBounds(rows_shown=rows_shown, rows_total_in_scope=None)
        all_rows = list(result.tables or [])
        rows = [row for row in all_rows if row.get("_metadata_only") is not True]
        source_total = _source_total_in_scope(all_rows)
        if source_total is None or source_total < len(rows):
            return ResultBounds(rows_shown=rows_shown, rows_total_in_scope=None)
        total_in_scope += source_total

    if total_in_scope < rows_shown:
        return ResultBounds(rows_shown=rows_shown, rows_total_in_scope=None)
    if total_in_scope > rows_shown:
        if rows_shown != MAX_ROWS_PER_QUERY:
            return ResultBounds(rows_shown=rows_shown, rows_total_in_scope=None)
        return ResultBounds(
            rows_shown=rows_shown,
            rows_total_in_scope=total_in_scope,
            truncated=True,
        )
    return ResultBounds(
        rows_shown=rows_shown,
        rows_total_in_scope=total_in_scope,
    )


def _bound_view_data(view: View, data: Sequence[Any]) -> list[Any]:
    """Apply the response-wide row bound after merging per-source query results."""
    order_fields = {
        "agents": "invocations",
        "models": "requests",
        "tools": "invocations",
        "runs": "last_activity_at",
    }
    order_field = order_fields.get(view)
    if order_field is None:
        return list(data)
    return sorted(
        data,
        key=lambda item: getattr(item, order_field),
        reverse=True,
    )[:MAX_ROWS_PER_QUERY]


_VIEW_SORT_FIELDS: dict[str, frozenset[str]] = {
    "agents": frozenset(
        {
            "agent_name",
            "agent_id",
            "source_kind",
            "model",
            "last_seen",
            "invocations",
            "failures",
            "failure_rate",
            "p95_latency_ms",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
        }
    ),
    "models": frozenset(
        {
            "model",
            "deployment",
            "last_seen",
            "requests",
            "failures",
            "failure_rate",
            "p95_latency_ms",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
        }
    ),
    "tools": frozenset(
        {
            "tool_name",
            "agent_name",
            "source_id",
            "source_kind",
            "last_seen",
            "invocations",
            "failures",
            "p95_latency_ms",
        }
    ),
    "runs": frozenset(
        {
            "run_key",
            "run_key_kind",
            "agent_name",
            "source_id",
            "source_kind",
            "started_at",
            "last_activity_at",
            "duration_ms",
            "status",
            "turns",
            "tool_invocations",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
        }
    ),
}
_DEFAULT_VIEW_SORT = {
    "agents": "invocations",
    "models": "requests",
    "tools": "invocations",
    "runs": "last_activity_at",
}


def _row_value(row: Any, field: str) -> Any:
    if field == "total_tokens":
        input_tokens = _row_value(row, "input_tokens")
        output_tokens = _row_value(row, "output_tokens")
        if input_tokens is None and output_tokens is None:
            return None
        return (input_tokens or 0) + (output_tokens or 0)
    if field == "failure_rate":
        failures = _row_value(row, "failures")
        total = _row_value(row, "invocations")
        if total is None:
            total = _row_value(row, "requests")
        if failures is None or not total:
            return None
        return failures / total
    if isinstance(row, Mapping):
        return row.get(field)
    return getattr(row, field, None)


def _sortable_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        return value.casefold()
    return value


def _searchable_row(row: Any) -> str:
    if hasattr(row, "model_dump"):
        value = row.model_dump(mode="json")
    elif isinstance(row, Mapping):
        value = dict(row)
    else:
        value = vars(row)
    return json.dumps(value, sort_keys=True, default=str).casefold()


def _page_observe_result(
    result: ObserveResult,
    *,
    page: int,
    page_size: int,
    search: str | None,
    sort_by: str | None,
    sort_direction: Literal["asc", "desc"],
) -> ObserveResult:
    """Sort, filter, and page one cached aggregate without re-querying Azure."""
    if result.view not in _VIEW_SORT_FIELDS or not isinstance(result.data, list):
        return result
    allowed_fields = _VIEW_SORT_FIELDS[result.view]
    field = sort_by or _DEFAULT_VIEW_SORT[result.view]
    if field not in allowed_fields:
        raise ValueError(f"unsupported sort field {field!r} for {result.view}")

    rows = list(result.data)
    normalized_search = (search or "").strip().casefold()
    if normalized_search:
        rows = [row for row in rows if normalized_search in _searchable_row(row)]

    present: list[tuple[int, Any, Any]] = []
    missing: list[tuple[int, Any]] = []
    for index, row in enumerate(rows):
        value = _row_value(row, field)
        if value is None:
            missing.append((index, row))
        else:
            present.append((index, row, _sortable_value(value)))
    present.sort(
        key=lambda item: (item[2], item[0]),
        reverse=sort_direction == "desc",
    )
    ordered = [item[1] for item in present] + [item[1] for item in missing]

    start = (page - 1) * page_size
    end = start + page_size
    visible = ordered[start:end]
    source_bounds = result.bounds
    hard_truncated = bool(source_bounds and source_bounds.truncated)
    if normalized_search and hard_truncated:
        total: int | None = None
    elif normalized_search:
        total = len(ordered)
    elif source_bounds is not None:
        total = source_bounds.rows_total_in_scope
    else:
        total = len(ordered)
    has_next = end < len(ordered)
    bounds = ResultBounds(
        rows_shown=len(visible),
        rows_total_in_scope=total,
        truncated=hard_truncated,
        page=page,
        page_size=page_size,
        has_previous_page=page > 1,
        has_next_page=has_next,
    )
    return replace(result, data=visible, bounds=bounds)


def _build_partial_failures(results: Sequence[SourceResult]) -> list[PartialFailure]:
    """Summarize every non-``success`` source outcome as a safe partial failure (T061).

    Reuses :data:`_PARTIAL_FAILURE_DEFAULTS` so the reason/next_action text
    matches the coverage entries produced for the same source, and always
    routes the raw SDK ``reason`` through :func:`safe_failure_reason` so
    unredacted error text/content never reaches the response. Returns an
    empty list when every source succeeded.
    """
    failures: list[PartialFailure] = []
    for result in results:
        if result.status == "success":
            continue
        default_reason, next_action = _PARTIAL_FAILURE_DEFAULTS[result.status]
        failures.append(
            PartialFailure(
                source_id=result.source_id,
                status=result.status,
                reason=safe_failure_reason(result.reason, default=default_reason),
                next_action=next_action,
            )
        )
    return failures


def classify_protected_content_coverage(
    *, source_id: str, protection_state: str, refreshed_at: datetime
) -> CoverageResult:
    """Map a :class:`GenerativeAIContent` protection state to trace-correlation coverage.

    ``protected_or_unavailable`` is reported as-is rather than collapsed
    into ``error``: it means access could not be confirmed, not that the
    query failed (T059/T060).
    """
    if protection_state == "available":
        state: CoverageState = "available"
        reason = "Protected trace content is available for this correlation key."
        next_action = "No action needed."
    elif protection_state == "not_configured":
        state = "not_configured"
        reason = "Generative AI content protection is not enabled for this workspace."
        next_action = "Enable protectGenAISensitiveData on the Log Analytics workspace."
    else:
        state = "protected_or_unavailable"
        reason = (
            "Protected content could not be confirmed for this identity; "
            "it may be restricted or simply absent."
        )
        next_action = (
            "Request Privileged Monitoring Data Reader on the workspace, "
            "or verify the trace/span identifiers."
        )
    return CoverageResult(
        source_id=source_id,
        dimension="protected_content",
        state=state,
        reason=reason,
        next_action=next_action,
        refreshed_at=refreshed_at,
    )


# ---------------------------------------------------------------------------
# Orchestration: discovery, cached queries, normalization, agent detail (T046)
# ---------------------------------------------------------------------------


def _identity_key(runtime: RuntimeContext) -> str:
    return f"{runtime.mode}:{runtime.credential_identity}"


def _cache_key(
    identity: str, scope: ObserveScope, view: str, filters: ObserveFilterState | None
) -> tuple[Any, ...]:
    key: tuple[Any, ...] = (identity, view, scope.model_dump_json())
    if filters is not None:
        key = key + (filters.model_dump_json(),)
    return key


def _cost_cache_key(
    identity: str,
    scope: ObserveScope,
    *,
    model_fingerprint: str,
    period_id: str,
    breakdown: CostBreakdown,
    component_id: str | None,
    cost_agent_key: str | None,
) -> tuple[Any, ...]:
    return (
        identity,
        "cost",
        scope.model_dump_json(),
        model_fingerprint,
        period_id,
        breakdown,
        component_id,
        cost_agent_key,
    )


def _cost_views_for_components(
    period: CostPeriod, component_id: str | None
) -> tuple[str, ...]:
    components = [
        component
        for component in period.components
        if component_id is None or component.id == component_id
    ]
    if component_id is not None and not components:
        raise ValueError(f"Unknown cost component ID: {component_id}")
    # Cost coverage must also report observable capabilities that have no
    # configured component. Collect each bounded metadata-only usage view once;
    # allocation still selects only explicitly matched component observations.
    return ("models", "runs", "tools")


def _source_results_complete(
    results: Sequence[SourceResult],
    *,
    expected_source_ids: set[str],
) -> bool:
    results_by_source = {result.source_id: result for result in results}
    if set(results_by_source) != expected_source_ids:
        return False
    for result in results:
        rows = [
            row
            for row in list(result.tables or [])
            if row.get("_metadata_only") is not True
        ]
        totals = {
            int(value)
            for row in rows
            if (value := row.get("total_in_scope")) is not None
            and not isinstance(value, bool)
        }
        if result.status != "success":
            return False
        if totals and (len(totals) != 1 or next(iter(totals)) > len(rows)):
            return False
    return True


def _dedupe_coverage(items: Sequence[CoverageResult]) -> list[CoverageResult]:
    unique: list[CoverageResult] = []
    seen: set[str] = set()
    for item in items:
        identity = item.model_dump_json()
        if identity not in seen:
            seen.add(identity)
            unique.append(item)
    return unique


def _dedupe_partial_failures(
    results: Sequence[SourceResult],
) -> list[PartialFailure]:
    status_rank = {"success": 0, "partial": 1, "throttled": 2, "timeout": 3, "error": 4}
    worst_by_source: dict[str, SourceResult] = {}
    for result in results:
        current = worst_by_source.get(result.source_id)
        if current is None or status_rank[result.status] > status_rank[current.status]:
            worst_by_source[result.source_id] = result
    return _build_partial_failures(list(worst_by_source.values()))


def _cost_observation_matches(
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


def _cost_capabilities(
    observation: CostUsageObservation,
) -> set[AllocationKey]:
    capabilities: set[AllocationKey] = set()
    granular_tokens = (
        observation.cache_read_tokens,
        observation.cache_write_tokens,
        observation.reasoning_tokens,
    )
    if any(value is not None for value in granular_tokens):
        capabilities.add("weighted_tokens")
    elif observation.input_tokens is not None or observation.output_tokens is not None:
        capabilities.add("total_tokens")
    if observation.tool_invocations is not None:
        capabilities.add("tool_invocations")
    if observation.active_session_seconds is not None:
        capabilities.add("active_session_seconds")
    if observation.credits is not None:
        capabilities.add("credits")
    if observation.credit_events is not None and observation.operation_name is not None:
        capabilities.add("credit_events")
    return capabilities


def _component_view(component: CostComponent) -> str:
    if component.allocation_key == "tool_invocations":
        return "tools"
    if component.allocation_key == "total_tokens":
        return "models"
    return "runs"


def _cost_component_coverage(
    *,
    summary: CostComponentSummary,
    component: CostComponent,
    breakdown: CostBreakdown,
    inventory: ResourceInventory,
    results_by_view: Mapping[str, Sequence[SourceResult]],
    normalization_error_views: set[str],
    observations: Sequence[CostUsageObservation],
    refreshed_at: datetime,
) -> CoverageResult:
    matching = [
        observation
        for observation in observations
        if _cost_observation_matches(component, observation)
    ]
    relevant_results = results_by_view.get(_component_view(component), ())
    expected_sources = {
        source.source_id
        for source in inventory.telemetry_sources
        if source.state == "available"
    }
    has_query_failure = any(
        result.status in {"timeout", "throttled", "error"}
        for result in relevant_results
    ) or bool(
        expected_sources.difference(result.source_id for result in relevant_results)
    )
    unavailable_sources = [
        source
        for source in inventory.telemetry_sources
        if source.state in {"inaccessible", "error"}
    ]
    unconfigured_sources = [
        source
        for source in inventory.telemetry_sources
        if source.state in {"not_configured", "not_found"}
    ]

    state = summary.coverage_state
    reason = summary.coverage_reason
    next_action = summary.next_action or "No action needed."
    if not matching and _component_view(component) in normalization_error_views:
        state = "error"
        reason = "One or more usage rows for this cost component were malformed."
        next_action = "Correct the emitted usage attributes and retry."
    elif not matching and unavailable_sources:
        state = "inaccessible"
        reason = "A required telemetry source is inaccessible for this cost component."
        next_action = "Restore read access to the telemetry source and retry."
    elif not matching and (
        not inventory.telemetry_sources or bool(unconfigured_sources)
    ):
        state = "not_configured"
        reason = "Telemetry is not configured for this cost component."
        next_action = "Configure a readable telemetry connection and retry."
    elif not matching and has_query_failure:
        state = "error"
        reason = "The cost-component telemetry query did not complete."
        next_action = "Retry after resolving the telemetry query failure."
    elif state == "partial":
        if any(
            observation.agent_key is None
            if breakdown == "agents"
            else observation.tool_name is None
            if breakdown == "tools"
            else observation.run_key is None
            for observation in matching
        ):
            reason = (
                "Allocation is partial because some observed usage has no "
                f"{breakdown[:-1]} identity."
            )
            next_action = "Add stable consumer identity telemetry and retry."
        else:
            reason = (
                "Allocation-key or readable-period coverage is partial for "
                "this component."
            )
            next_action = "Complete allocation-key and period telemetry coverage."

    return CoverageResult(
        source_id=f"cost:{component.id}",
        dimension="cost_attribution",
        state=state,
        reason=reason,
        next_action=next_action,
        refreshed_at=refreshed_at,
        component_id=component.id,
        cost_breakdown=breakdown,
        allocation_key=summary.applied_key or summary.preferred_key,
    )


def _unmatched_capability_coverage(
    *,
    period: CostPeriod,
    observations: Sequence[CostUsageObservation],
    breakdown: CostBreakdown,
    refreshed_at: datetime,
) -> list[CoverageResult]:
    configured = list(period.components)
    unmatched: dict[tuple[str, AllocationKey], CostUsageObservation] = {}
    for observation in observations:
        for capability in _cost_capabilities(observation):
            if capability == "credit_events" and not any(
                observation.operation_name
                in component.usage_match.credit_event_operations
                for component in configured
            ):
                continue
            has_match = any(
                capability in {component.allocation_key, component.fallback_key}
                and _cost_observation_matches(component, observation)
                for component in configured
            )
            if not has_match:
                unmatched.setdefault(
                    (observation.source_resource_id, capability),
                    observation,
                )
    return [
        CoverageResult(
            source_id=f"cost:capability:{capability}:{index}",
            dimension="cost_attribution",
            state="not_configured",
            reason=(
                f"Observed allocation capability {capability!r} has no matching "
                "configured cost component."
            ),
            next_action=(
                "Add an explicit cost component and usage selector if this "
                "capability belongs to a declared billed pool."
            ),
            refreshed_at=refreshed_at,
            component_id=None,
            cost_breakdown=breakdown,
            allocation_key=capability,
        )
        for index, ((_source_id, capability), _observation) in enumerate(
            sorted(unmatched.items()),
            start=1,
        )
    ]


@dataclass
class _DepartmentUsageAccumulator:
    department_id: str
    department_label: str
    member_count: int
    usage: AttributionUsage
    principal_member_present: bool = False


def _attribution_count(row: Mapping[str, Any], field: str, default: int) -> int:
    value = row.get(field)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _attribution_row_mapping(row: Any) -> Mapping[str, Any]:
    """Adapt normalized aggregate rows without coupling to the Azure adapter."""
    if isinstance(row, Mapping):
        return row
    usage = getattr(row, "usage", None)
    if isinstance(usage, AttributionUsage):
        usage = usage.model_dump()
    fields = (
        "department_id",
        "department_label",
        "mapping_state",
        "member_count",
        "principal_member_present",
        "eligible_records",
        "identified_records",
        "mapped_records",
        "unattributed_records",
        "ambiguous_records",
        "returned_records",
        "metadata_only",
        "user_key",
        "raw_identity",
        "row_kind",
        "rank",
        "distinct_users",
        "latest_observed_at",
        "member_user_keys",
        "source_resource_id",
        "project_resource_id",
        "agent_key",
        "agent_id",
        "agent_name",
        "provider_name",
        "system",
        "deployment",
        "model",
        "tool_name",
        "runtime_kind",
        "operation_name",
        "identity_state",
    )
    if usage is None or not any(hasattr(row, field) for field in fields):
        raise TypeError("attribution row must be a mapping or normalized aggregate row")
    return {
        **{field: getattr(row, field, None) for field in fields},
        "usage": usage,
    }


def _attribution_row_matches_cost_component(
    component: CostComponent,
    *,
    row: Mapping[str, Any],
    source: TelemetrySource,
    inventory: ResourceInventory,
) -> bool:
    """Match a component against dimensions actually returned by telemetry."""
    project_resource_id = row.get("project_resource_id")
    if project_resource_id is None and len(source.project_resource_ids) == 1:
        project_resource_id = source.project_resource_ids[0]
    runtime_kind = row.get("runtime_kind") or row.get("source_kind")
    if runtime_kind is None:
        runtime_kind = classify_runtime(
            agent_id=row.get("agent_id") or None,
            agent_name=row.get("agent_name") or None,
            provider_name=row.get("provider_name"),
            system=row.get("system"),
            inventory=inventory,
        )
    actual = (
        (
            component.usage_match.source_resource_ids,
            row.get("source_resource_id")
            or source.foundry_resource_id
            or source.resource_id,
        ),
        (component.usage_match.project_resource_ids, project_resource_id),
        (
            component.usage_match.agent_keys,
            row.get("agent_key") or row.get("agent_id"),
        ),
        (component.usage_match.deployments, row.get("deployment")),
        (component.usage_match.models, row.get("model")),
        (component.usage_match.tool_names, row.get("tool_name")),
        (
            component.usage_match.runtime_kinds,
            runtime_kind,
        ),
    )
    if not all(not allowed or value in allowed for allowed, value in actual):
        return False
    operations = component.usage_match.credit_event_operations
    return not operations or row.get("operation_name") in operations


def _attribution_coverage_state(
    *,
    status: SourceStatus,
    eligible: int,
    identified: int,
    mapped: int,
    ambiguous: int,
) -> CoverageState:
    if status == "partial":
        return "partial"
    if status != "success":
        return "error"
    if eligible == 0:
        return "no_data"
    if ambiguous:
        return "ambiguous" if mapped == 0 else "partial"
    if identified == 0:
        return "not_reported"
    if mapped < eligible:
        return "partial"
    return "available"


def _attribution_coverage_text(state: CoverageState) -> tuple[str, str]:
    messages = {
        "available": (
            "Supported user identity and department mapping cover this source.",
            "No action needed.",
        ),
        "partial": (
            "Only part of this source could be attributed to a department.",
            "Add missing explicit mappings and verify authenticated identity telemetry.",
        ),
        "not_reported": (
            "Telemetry records do not report a supported authenticated user identity.",
            "Emit UserAuthenticatedId or the OpenTelemetry enduser.id attribute.",
        ),
        "ambiguous": (
            "Conflicting identity or department evidence prevented safe attribution.",
            "Correct conflicting aliases or explicit department mappings.",
        ),
        "no_data": (
            "No eligible attribution records were returned for this window.",
            "Widen the time range or verify telemetry ingestion.",
        ),
        "inaccessible": (
            "This telemetry source could not be read.",
            "Verify source access and refresh the attribution view.",
        ),
        "error": (
            "Attribution could not be calculated for this source.",
            "Verify telemetry shape and retry the request.",
        ),
    }
    return messages.get(
        state,
        (
            "Attribution is unavailable for this source.",
            "Verify source access and attribution configuration.",
        ),
    )


def _merge_user_attribution_coverage(
    *coverage_groups: Sequence[UserAttributionCoverage],
) -> list[UserAttributionCoverage]:
    """Merge coverage without collapsing usage and cost/component dimensions."""
    merged: list[UserAttributionCoverage] = []
    seen: set[tuple[str, str, str | None, str]] = set()
    for group in coverage_groups:
        for item in group:
            key = (
                item.source_id,
                item.metric,
                item.component_id,
                item.attribution_level,
            )
            if key in seen:
                raise ValueError(
                    "duplicate user-attribution coverage for one source and metric"
                )
            seen.add(key)
            merged.append(item)
    return merged


def _apply_group_overage_coverage(
    coverage: Sequence[UserAttributionCoverage],
    *,
    config: AttributionConfiguration,
    overage: bool,
) -> list[UserAttributionCoverage]:
    if not overage or not any(department.group_ids for department in config.departments):
        return list(coverage)
    return [
        item.model_copy(
            update={
                "state": "partial",
                "reason": (
                    "Group claims were unavailable because the signed-in token "
                    "reported group overage."
                ),
                "next_action": (
                    "Use explicit user mappings or sign in with group claims "
                    "within the supported token limit."
                ),
            }
        )
        for item in coverage
    ]


class ObserveService:
    """Coordinates discovery, bounded querying, normalization, and caching.

    Every request is keyed by ``(identity, scope, filters, view)`` and
    cached for two minutes (``CACHE_TTL_SECONDS``); ``refresh=True`` bypasses
    the cache read, but a freshly normalized response is always re-cached
    afterwards. Only normalized :class:`ObservedAgent`/:class:`ModelUsage`/
    :class:`CoverageResult`/:class:`QueryDiagnostics` values are ever
    cached -- raw per-row query results and any ``GenerativeAIContent`` never
    reach ``cache``.
    """

    def __init__(
        self,
        *,
        discovery_client: DiscoveryClient,
        query_client: QueryClient,
        runtime: RuntimeContext,
        clock: Clock,
        cache: ObserveCache,
        inventory_cache: ObserveCache | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._discovery_client = discovery_client
        self._query_client = query_client
        self._runtime = runtime
        self._clock = clock
        self._cache = cache
        self._inventory_cache = inventory_cache or cache
        self._monotonic_clock = monotonic_clock
        self._inflight: dict[Hashable, asyncio.Task[Any]] = {}
        self._inflight_lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[Any]] = set()

    async def _run_coalesced(
        self,
        key: Hashable,
        operation: Callable[[], Coroutine[Any, Any, Any]],
    ) -> tuple[Any, bool]:
        """Run one operation per cache key and let concurrent callers share it."""
        async with self._inflight_lock:
            task = self._inflight.get(key)
            owner = task is None
            if task is None:
                task = asyncio.create_task(operation())
                self._inflight[key] = task

                def remove_inflight(completed: asyncio.Task[Any]) -> None:
                    if self._inflight.get(key) is completed:
                        self._inflight.pop(key, None)

                task.add_done_callback(remove_inflight)
        return await asyncio.shield(task), owner

    def _schedule_refresh(
        self,
        key: Hashable,
        operation: Callable[[], Coroutine[Any, Any, Any]],
    ) -> None:
        """Refresh a stale aggregate without delaying the current response."""
        task = asyncio.create_task(self._run_coalesced(key, operation))
        self._background_tasks.add(task)

        def finish(completed: asyncio.Task[Any]) -> None:
            self._background_tasks.discard(completed)
            if completed.cancelled():
                return
            error = completed.exception()
            if error is not None:
                logger.warning(
                    "Observe background refresh failed for key %r",
                    key,
                    exc_info=(type(error), error, error.__traceback__),
                )

        task.add_done_callback(finish)

    def _cached_result(
        self,
        cached: _CachedView,
        *,
        cache_status: Literal["hit", "stale"],
    ) -> ObserveResult:
        return ObserveResult(
            view=cached.view,
            data=cached.data,
            coverage=cached.coverage,
            diagnostics=cached.diagnostics,
            partial_failures=cached.partial_failures,
            bounds=cached.bounds,
            refreshed_at=cached.refreshed_at,
            cache_status=cache_status,
        )

    async def get_inventory(
        self, scope: ObserveScope, *, refresh: bool = False
    ) -> ResourceInventory:
        """Discover (or return the cached) resource inventory for *scope*."""
        identity = _identity_key(self._runtime)
        key = _cache_key(identity, scope, "discovery", None)
        cached = self._inventory_cache.get(key, bypass=refresh)
        if cached is not None:
            return cached

        async def discover() -> ResourceInventory:
            inventory = await self._discovery_client.discover(scope)
            self._inventory_cache.set(key, inventory)
            return inventory

        inventory, _owner = await self._run_coalesced(key, discover)
        return inventory

    async def query_view(
        self,
        scope: ObserveScope,
        filters: ObserveFilterState,
        *,
        view: View,
        refresh: bool = False,
        page: int = 1,
        page_size: int = 50,
        search: str | None = None,
        sort_by: str | None = None,
        sort_direction: Literal["asc", "desc"] = "desc",
        unpaged: bool = False,
    ) -> ObserveResult:
        """Return the normalized, coverage-annotated response for *view*."""
        filters.validate_scope(scope)
        identity = _identity_key(self._runtime)
        key = _cache_key(identity, scope, view, filters)
        cached = self._cache.lookup(
            key,
            bypass=refresh,
            max_stale_seconds=VIEW_STALE_TTL_SECONDS,
        )
        if cached.state == "fresh" and cached.value is not None:
            result = self._cached_result(cached.value, cache_status="hit")
            if unpaged:
                return result
            return _page_observe_result(
                result,
                page=page,
                page_size=page_size,
                search=search,
                sort_by=sort_by,
                sort_direction=sort_direction,
            )
        if cached.state == "stale" and cached.value is not None:
            self._schedule_refresh(
                key,
                lambda: self._query_view_uncached(
                    scope,
                    filters,
                    view=view,
                    key=key,
                    cache_status="miss",
                ),
            )
            result = self._cached_result(cached.value, cache_status="stale")
            if unpaged:
                return result
            return _page_observe_result(
                result,
                page=page,
                page_size=page_size,
                search=search,
                sort_by=sort_by,
                sort_direction=sort_direction,
            )

        result, owner = await self._run_coalesced(
            key,
            lambda: self._query_view_uncached(
                scope,
                filters,
                view=view,
                key=key,
                cache_status="bypass" if refresh else "miss",
            ),
        )
        if owner:
            response = result
        else:
            response = replace(result, cache_status="hit")
        if unpaged:
            return response
        return _page_observe_result(
            response,
            page=page,
            page_size=page_size,
            search=search,
            sort_by=sort_by,
            sort_direction=sort_direction,
        )

    async def _query_view_uncached(
        self,
        scope: ObserveScope,
        filters: ObserveFilterState,
        *,
        view: View,
        key: tuple[Any, ...],
        cache_status: Literal["miss", "bypass"],
    ) -> ObserveResult:
        request_started_at = self._clock()
        request_started = self._monotonic_clock()
        discovery_started = self._monotonic_clock()
        # Refresh telemetry without repeating the slower control-plane discovery.
        # Inventory has its own cache and explicit discover endpoint for forced refreshes.
        inventory = await self.get_inventory(scope)
        discovery_duration_ms = int(
            (self._monotonic_clock() - discovery_started) * 1000
        )
        available_sources = [
            source
            for source in inventory.telemetry_sources
            if source.state == "available"
        ]

        query_started = self._monotonic_clock()
        source_results = list(
            await self._query_client.query(available_sources, filters, view=view)
        )
        query_duration_ms = int((self._monotonic_clock() - query_started) * 1000)

        normalization_started = self._monotonic_clock()
        normalization_refreshed_at = self._clock()
        coverage = self._discovery_coverage(
            inventory.telemetry_sources, refreshed_at=normalization_refreshed_at
        )
        data, query_coverage = self._normalize_view(
            view,
            source_results,
            inventory.telemetry_sources,
            inventory=inventory,
            window_end=filters.end,
            refreshed_at=normalization_refreshed_at,
        )
        if view in ("agents", "models", "tools", "runs"):
            data = _bound_view_data(view, data)
        coverage.extend(query_coverage)
        bounds = (
            _result_bounds(source_results, rows_shown=len(data))
            if view in ("agents", "models", "tools", "runs")
            else None
        )
        completed_at = self._clock()
        normalization_duration_ms = int(
            (self._monotonic_clock() - normalization_started) * 1000
        )
        duration_ms = int((self._monotonic_clock() - request_started) * 1000)

        diagnostics = self._build_diagnostics(
            source_results,
            started_at=request_started_at,
            completed_at=completed_at,
            cache_status=cache_status,
            duration_ms=duration_ms,
            discovery_duration_ms=discovery_duration_ms,
            query_duration_ms=query_duration_ms,
            normalization_duration_ms=normalization_duration_ms,
        )
        partial_failures = _build_partial_failures(source_results)

        self._cache.set(
            key,
            _CachedView(
                view=view,
                data=data,
                coverage=coverage,
                diagnostics=diagnostics,
                partial_failures=partial_failures,
                bounds=bounds,
                refreshed_at=completed_at,
            ),
        )
        return ObserveResult(
            view=view,
            data=data,
            coverage=coverage,
            diagnostics=diagnostics,
            partial_failures=partial_failures,
            bounds=bounds,
            refreshed_at=completed_at,
            cache_status=cache_status,
        )

    async def query_attribution(
        self,
        scope: ObserveScope,
        request: AttributionQueryRequest,
        *,
        config: AttributionConfiguration,
        cost_model: CostModel | None = None,
        principal_context: Mapping[str, Any] | None = None,
        access_boundary: Literal["aggregate", "delegated"] = "aggregate",
        _cost_component: CostComponent | None = None,
        _unbounded_users: bool = False,
    ) -> AttributionResponse:
        """Return a bounded usage attribution projection.

        The query client contract deliberately performs one bounded batch call
        for all sources. Dedicated query adapters may apply mappings in KQL;
        user-shaped synthetic rows are also resolved here for offline tests and
        custom adapters.
        """
        request.filters.validate_scope(scope)
        if not config.enabled:
            raise ValueError("Attribution is not enabled.")
        if request.metric == "cost":
            if cost_model is None:
                raise ValueError(
                    "Cost attribution requires a valid configured cost model."
                )
            return await self._query_cost_attribution(
                scope,
                request,
                config=config,
                cost_model=cost_model,
                principal_context=principal_context or {},
                access_boundary=access_boundary,
            )
        if request.group_by == "user":
            if access_boundary != "delegated":
                raise ValueError("User attribution requires delegated query support.")
            return await self._query_user_usage_attribution(
                scope,
                request,
                config=config,
                principal_context=principal_context or {},
                cost_component=_cost_component,
                unbounded=_unbounded_users,
            )

        resolved_department_id: str | None = None
        if request.filters.department_filter_token is not None:
            resolved_department_id = validate_department_filter_token(
                request.filters.department_filter_token,
                config=config,
                scope=scope,
            ).id

        inventory = await self.get_inventory(scope)
        available_sources = [
            source
            for source in inventory.telemetry_sources
            if source.state == "available"
        ]
        started_at = self._clock()
        principal = principal_context or {}
        tenant_id = principal.get("tenant_id")
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("Attribution requires the signed-in tenant identifier.")
        principal_groups = (
            ()
            if principal.get("groups_overage") or principal.get("group_claims_overage")
            else tuple(principal.get("groups") or principal.get("group_ids") or ())
        )
        query_config = config_with_principal_group_mappings(
            config,
            tenant_id=tenant_id,
            principal_user_id=principal.get("user_id"),
            principal_user_name=principal.get("user_name"),
            principal_group_ids=principal_groups,
        )
        principal_user_keys = principal_alias_user_keys(
            config=config,
            tenant_id=tenant_id,
            principal_user_id=principal.get("user_id"),
            principal_user_name=principal.get("user_name"),
        )

        results = list(
            await self._query_client.query_department_usage(
                available_sources,
                request.filters,
                config=query_config,
                tenant_id=tenant_id,
                department_id=resolved_department_id,
                principal_user_keys=principal_user_keys,
                **(
                    {"cost_component": _cost_component}
                    if _cost_component is not None
                    else {}
                ),
            )
        )
        completed_at = self._clock()

        result_by_source = {result.source_id: result for result in results}
        known_departments = {
            department.id: department for department in config.departments
        }
        groups: dict[str, _DepartmentUsageAccumulator] = {}
        unattributed_parts: list[AttributionUsage] = []
        coverage: list[UserAttributionCoverage] = []
        failures: list[QuerySourceFailure] = []
        latest_observed_at: datetime | None = None
        cardinality_rows: list[Mapping[str, Any]] = []

        for source in inventory.telemetry_sources:
            result = result_by_source.get(source.source_id)
            if source.state != "available":
                state: CoverageState = "inaccessible"
                reason, next_action = _attribution_coverage_text(state)
                coverage.append(
                    normalize_user_attribution_coverage(
                        source=source,
                        status=None,
                        rows=None,
                        refreshed_at=completed_at,
                        metric="usage",
                        attribution_level="department",
                    )
                )
                failures.append(
                    QuerySourceFailure(
                        source_id=source.source_id,
                        status="inaccessible",
                        reason=reason,
                        next_action=next_action,
                    )
                )
                continue

            if result is None:
                result = SourceResult(
                    source_id=source.source_id,
                    status="error",
                    reason="Attribution query returned no source result.",
                )

            rows = list(result.tables or [])
            for raw_row in rows:
                try:
                    row = _attribution_row_mapping(raw_row)
                    if row.get("metadata_only") is True:
                        continue
                    if _cost_component is not None and not _attribution_row_matches_cost_component(
                        _cost_component, row=row, source=source, inventory=inventory
                    ):
                        continue
                    usage = usage_from_row(row)
                    members = _attribution_count(
                        row,
                        "member_count",
                        1 if row.get("user_key") else 0,
                    )
                    principal_present = bool(
                        _attribution_count(row, "principal_member_present", 0)
                    )
                    mapping_state = str(row.get("mapping_state") or "unmapped")
                    department_id = row.get("department_id")
                    user_key = row.get("user_key")
                    if isinstance(user_key, str):
                        resolution = resolve_department(
                            user_key=user_key,
                            raw_identity=row.get("raw_identity"),
                            config=config,
                            principal_user_id=principal.get("user_id"),
                            principal_user_name=principal.get("user_name"),
                            principal_group_ids=principal_groups,
                        )
                        mapping_state = (
                            "mapped"
                            if resolution.department_id is not None
                            else resolution.source
                        )
                        department_id = resolution.department_id

                    observed = row.get("latest_observed_at")
                    if isinstance(observed, datetime) and (
                        latest_observed_at is None or observed > latest_observed_at
                    ):
                        latest_observed_at = observed

                    if (
                        mapping_state == "mapped"
                        and isinstance(department_id, str)
                        and department_id in known_departments
                        and (
                            resolved_department_id is None
                            or department_id == resolved_department_id
                        )
                    ):
                        department = known_departments[department_id]
                        cardinality_rows.append(
                            {
                                "department_id": department_id,
                                "member_count": members,
                                "principal_member_present": int(principal_present),
                                "member_user_keys": row.get("member_user_keys"),
                            }
                        )
                        current = groups.get(department_id)
                        nonprincipal_members = members - int(principal_present)
                        groups[department_id] = _DepartmentUsageAccumulator(
                            department_id=department_id,
                            department_label=department.label,
                            member_count=nonprincipal_members
                            + (current.member_count if current else 0),
                            usage=sum_usage(
                                [current.usage, usage] if current else [usage]
                            ),
                            principal_member_present=principal_present
                            or bool(current and current.principal_member_present),
                        )
                    else:
                        cardinality_rows.append(
                            {
                                "department_id": None,
                                "member_count": members,
                                "principal_member_present": int(principal_present),
                                "member_user_keys": row.get("member_user_keys"),
                            }
                        )
                        unattributed_parts.append(usage)
                except (TypeError, ValueError):
                    unattributed_parts.append(zero_usage())

            coverage_error = False
            try:
                source_coverage = normalize_user_attribution_coverage(
                    source=source,
                    status=result.status,
                    rows=rows,
                    metric="usage",
                    attribution_level="department",
                    refreshed_at=completed_at,
                )
            except (TypeError, ValueError):
                coverage_error = True
                source_coverage = normalize_user_attribution_coverage(
                    source=source,
                    status="error",
                    rows=None,
                    metric="usage",
                    attribution_level="department",
                    refreshed_at=completed_at,
                )
            coverage.append(source_coverage)
            if result.status != "success":
                default_reason, failure_action = _PARTIAL_FAILURE_DEFAULTS[
                    result.status
                ]
                failures.append(
                    QuerySourceFailure(
                        source_id=result.source_id,
                        status=(
                            result.status
                            if result.status in {"partial", "timeout", "error"}
                            else "error"
                        ),
                        reason=default_reason,
                        next_action=failure_action,
                    )
                )
            elif coverage_error:
                reason, next_action = _attribution_coverage_text("error")
                failures.append(
                    QuerySourceFailure(
                        source_id=result.source_id,
                        status="error",
                        reason=reason,
                        next_action=next_action,
                    )
                )

        if access_boundary == "aggregate" and not classify_department_cardinality(
            cardinality_rows,
            principal_aliases=principal_user_keys,
        ):
            raise SingletonAttributionError(
                "Department attribution requires delegated access for this result."
            )

        department_rows = [
            DepartmentAttributionRow(
                kind="department",
                department_id=item.department_id,
                department_label=item.department_label,
                filter_token=issue_department_filter_token(
                    item.department_id, config=config, scope=scope
                ),
                member_count=item.member_count + int(item.principal_member_present),
                usage=item.usage,
                cost=None,
                mapping_state="mapped",
            )
            for item in sorted(
                groups.values(),
                key=lambda item: (-item.usage.invocations, item.department_id),
            )
        ]
        attributed_usage = sum_usage(row.usage for row in department_rows)
        unattributed_usage = sum_usage(unattributed_parts)
        total_usage = sum_usage([attributed_usage, unattributed_usage])
        cache_status: Literal["miss", "bypass"] = (
            "bypass" if access_boundary == "delegated" or request.refresh else "miss"
        )
        successful = sum(1 for result in results if result.status == "success")
        partial = sum(1 for result in results if result.status == "partial")
        unavailable = sum(
            1 for source in inventory.telemetry_sources if source.state != "available"
        )
        failed = unavailable + sum(
            1 for result in results if result.status not in {"success", "partial"}
        )
        diagnostics = QueryDiagnostics(
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=max(int((completed_at - started_at).total_seconds() * 1000), 0),
            source_count=len(inventory.telemetry_sources),
            successful_sources=successful,
            partial_sources=partial,
            failed_sources=failed,
            cache_status=cache_status,
        )
        view = AttributionViewData(
            metric="usage",
            group_by="department",
            access_boundary=access_boundary,
            rows=department_rows,
            summary=UsageAttributionSummary(
                metric="usage",
                total=total_usage,
                attributed=attributed_usage,
                unattributed=unattributed_usage,
                distinct_users=(
                    sum(item.member_count for item in groups.values())
                    if results
                    else None
                ),
                omitted_users=0,
            ),
            primary_measure="invocations",
            calculated_at=completed_at,
            latest_observed_at=latest_observed_at,
        )
        return AttributionResponse(
            data=view,
            coverage=_apply_group_overage_coverage(
                _merge_user_attribution_coverage(coverage),
                config=config,
                overage=bool(
                    principal.get("groups_overage")
                    or principal.get("group_claims_overage")
                ),
            ),
            partial_failures=failures,
            diagnostics=diagnostics,
            refreshed_at=completed_at,
            cache_status=cache_status,
            bounds=ResultBounds(
                rows_shown=len(department_rows),
                rows_total_in_scope=len(department_rows),
            ),
        )

    async def _query_cost_attribution(
        self,
        scope: ObserveScope,
        request: AttributionQueryRequest,
        *,
        config: AttributionConfiguration,
        cost_model: CostModel,
        principal_context: Mapping[str, Any],
        access_boundary: Literal["aggregate", "delegated"],
    ) -> AttributionResponse:
        """Allocate one declared component over full-period attribution usage."""
        period_id = request.filters.cost_period_id
        component_id = request.filters.cost_component_id
        period = next(
            (item for item in cost_model.periods if item.id == period_id),
            None,
        )
        if period is None:
            raise ValueError(f"unknown cost period {period_id!r}")
        component = next(
            (item for item in period.components if item.id == component_id),
            None,
        )
        if component is None:
            raise ValueError(
                f"unknown cost component {component_id!r} for period {period.id!r}"
            )
        if request.filters.cost_agent_key is not None:
            raise ValueError("cost attribution does not accept an agent filter")

        selected_department: str | None = None
        if request.filters.department_filter_token is not None:
            selected_department = validate_department_filter_token(
                request.filters.department_filter_token,
                config=config,
                scope=scope,
            ).id
        selected_user: str | None = None
        if request.filters.user_filter_token is not None:
            tenant_id = principal_context.get("tenant_id")
            principal_id = (
                principal_context.get("user_id")
                or principal_context.get("object_id")
                or principal_context.get("user_name")
            )
            if not isinstance(tenant_id, str) or not isinstance(principal_id, str):
                raise ValueError(
                    "User cost attribution requires signed-in tenant and principal identifiers."
                )
            selected_user = validate_user_filter_token(
                request.filters.user_filter_token,
                config=config,
                scope=scope,
                tenant_id=tenant_id,
                principal_id=principal_id,
            )

        # Identity filters are intentionally removed here. Allocation is performed
        # over the complete declared period and selected pool; narrowing happens
        # only after every numerator, denominator, and minor-unit amount is fixed.
        full_period_filters = request.filters.model_copy(
            update={
                "start": period.starts_at,
                "end": period.ends_at,
                "department_filter_token": None,
                "user_filter_token": None,
            }
        )
        usage_request = AttributionQueryRequest(
            metric="usage",
            group_by=request.group_by,
            filters=full_period_filters,
            refresh=request.refresh,
        )
        usage_response = await self.query_attribution(
            scope,
            usage_request,
            config=config,
            principal_context=principal_context,
            access_boundary=access_boundary,
            _cost_component=component,
            _unbounded_users=request.group_by == "user",
        )
        usage_summary = usage_response.data.summary
        if not isinstance(usage_summary, UsageAttributionSummary):
            raise TypeError("cost allocation requires usage attribution")

        resolutions: list[AttributionResolution] = []
        observations: list[CostUsageObservation] = []
        usage_by_key: dict[str, AttributionUsage] = {}
        row_by_key: dict[str, Any] = {}

        def add_observation(
            key: str | None,
            usage: AttributionUsage,
        ) -> None:
            fallback_source_id = (
                request.filters.project_resource_id
                or scope.default_project_resource_id
                or (scope.project_resource_ids[0] if scope.project_resource_ids else None)
                or scope.root_resource_id
            )
            source_resource_id = fallback_source_id
            if source_resource_id is None:
                raise ValueError(
                    "Cost attribution could not determine an in-scope resource."
                )
            observations.append(
                CostUsageObservation(
                    source_resource_id=source_resource_id,
                    project_resource_id=request.filters.project_resource_id,
                    agent_key=request.filters.agent_id,
                    user_key=key,
                    tool_name=request.filters.tool_name,
                    runtime_kind="unknown",
                    model=request.filters.model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    tool_invocations=usage.tool_invocations,
                    active_session_seconds=usage.active_session_seconds,
                    coverage_complete=all(
                        item.state == "available" for item in usage_response.coverage
                    ),
                )
            )

        for index, row in enumerate(usage_response.data.rows):
            if row.kind == "department":
                key = f"usr1.g{config.generation}.{index:064x}"
                resolutions.append(
                    AttributionResolution(
                        user_key=key,
                        department_id=row.department_id,
                        department_label=row.department_label,
                        source="explicit_user",
                        reason="Department aggregate supplied by the validated mapping.",
                    )
                )
            elif row.kind == "user":
                key = row.user_key
                resolutions.append(
                    resolve_department(
                        user_key=key,
                        raw_identity=row.raw_identity,
                        config=config,
                        principal_user_id=principal_context.get("user_id"),
                        principal_user_name=principal_context.get("user_name"),
                        principal_group_ids=tuple(
                            principal_context.get("groups")
                            or principal_context.get("group_ids")
                            or ()
                        ),
                    )
                )
            else:
                key = f"usr1.g{config.generation}.{'f' * 63}e"
                resolutions.append(
                    AttributionResolution(
                        user_key=key,
                        department_id=None,
                        department_label=None,
                        source="unmapped",
                        reason="Other users remain outside individual mapping.",
                    )
                )
            usage_by_key[key] = row.usage
            row_by_key[key] = row
            add_observation(key, row.usage)
        add_observation(None, usage_summary.unattributed)

        # The attribution query has already matched each source row against the
        # selected component using telemetry dimensions.  Allocation therefore
        # uses a private selector over the in-memory observations rather than
        # copying configured selectors onto observations and manufacturing a
        # match.
        allocation_source = observations[0].source_resource_id if observations else (
            scope.root_resource_id or component.billing_boundary.value
        )
        allocation_component = component.model_copy(
            update={
                "usage_match": component.usage_match.model_copy(
                    update={
                        "source_resource_ids": [allocation_source],
                        "project_resource_ids": [],
                        "agent_keys": [],
                        "deployments": [],
                        "models": [],
                        "tool_names": [],
                        "runtime_kinds": [],
                        "credit_event_operations": (
                            component.usage_match.credit_event_operations
                            if component.allocation_key == "credit_events"
                            or component.fallback_key == "credit_events"
                            else []
                        ),
                    }
                )
            }
        )
        allocation_period = period.model_copy(update={"components": [allocation_component]})
        observations = [
            observation.model_copy(update={"source_resource_id": allocation_source})
            for observation in observations
        ]
        calculated = allocate_cost_period(
            allocation_period,
            observations,
            calculated_at=usage_response.refreshed_at,
            component_id=component.id,
            department_resolutions=(
                resolutions if request.group_by == "department" else None
            ),
            department_id=(
                selected_department if request.group_by == "department" else None
            ),
            user_resolutions=resolutions if request.group_by == "user" else None,
            user_key=selected_user if request.group_by == "user" else None,
            fold_users=request.group_by != "user",
        )
        component_summary = calculated.components[0]
        allocation_by_key = {
            row.consumer_key: row
            for row in calculated.rows
            if row.consumer_kind not in {"unattributed", "other_users"}
        }
        folded_allocation = next(
            (
                row
                for row in calculated.rows
                if row.consumer_kind == "other_users"
            ),
            None,
        )
        result_rows: list[Any] = []
        if request.group_by == "department":
            for row in usage_response.data.rows:
                if row.kind != "department":
                    continue
                if selected_department is not None and row.department_id != selected_department:
                    continue
                allocation = allocation_by_key.get(row.department_id)
                if allocation is None:
                    continue
                result_rows.append(
                    row.model_copy(
                        update={
                            "cost": AttributionCost(
                                period_id=period.id,
                                component_id=component.id,
                                amount=allocation.amount,
                                currency=allocation.currency,
                                currency_minor_units=allocation.currency_minor_units,
                                usage_numerator=allocation.usage_numerator,
                                usage_denominator=allocation.usage_denominator,
                                allocation_key=allocation.usage_unit,
                                confidence=allocation.confidence,
                            )
                        }
                    )
                )
        else:
            for key, source_row in row_by_key.items():
                if selected_user is not None and key != selected_user:
                    continue
                allocation = allocation_by_key.get(key)
                if allocation is None:
                    continue
                cost = AttributionCost(
                    period_id=period.id,
                    component_id=component.id,
                    amount=allocation.amount,
                    currency=allocation.currency,
                    currency_minor_units=allocation.currency_minor_units,
                    usage_numerator=allocation.usage_numerator,
                    usage_denominator=allocation.usage_denominator,
                    allocation_key=allocation.usage_unit,
                    confidence=allocation.confidence,
                )
                result_rows.append(source_row.model_copy(update={"cost": cost}))
            if selected_user is None and folded_allocation is not None:
                hidden_rows = [
                    row
                    for key, row in row_by_key.items()
                    if key not in allocation_by_key
                ]
                result_rows.append(
                    OtherUsersAttributionRow(
                        kind="other_users",
                        member_count=sum(
                            row.member_count if row.kind == "other_users" else 1
                            for row in hidden_rows
                        ),
                        usage=sum_usage(row.usage for row in hidden_rows),
                        cost=AttributionCost(
                            period_id=period.id,
                            component_id=component.id,
                            amount=folded_allocation.amount,
                            currency=folded_allocation.currency,
                            currency_minor_units=folded_allocation.currency_minor_units,
                            usage_numerator=folded_allocation.usage_numerator,
                            usage_denominator=folded_allocation.usage_denominator,
                            allocation_key=folded_allocation.usage_unit,
                            confidence=folded_allocation.confidence,
                        ),
                        mapping_state="not_applicable",
                    )
                )
            user_rows = [row for row in result_rows if row.kind == "user"]
            user_rows.sort(key=lambda row: (-row.cost.amount, row.user_key))
            existing_other = [row for row in result_rows if row.kind == "other_users"]
            needs_other = bool(existing_other) or len(user_rows) > 500
            if selected_user is None and needs_other:
                visible_users = user_rows[:499]
                hidden_users = user_rows[499:]
                hidden_usage = sum_usage(row.usage for row in hidden_users)
                hidden_amount = sum((row.cost.amount for row in hidden_users), Decimal(0))
                hidden_numerator = sum(
                    (row.cost.usage_numerator for row in hidden_users), Decimal(0)
                )
                if existing_other:
                    hidden_usage = sum_usage(
                        [hidden_usage, *(row.usage for row in existing_other)]
                    )
                    hidden_amount += sum(
                        (row.cost.amount for row in existing_other), Decimal(0)
                    )
                    hidden_numerator += sum(
                        (row.cost.usage_numerator for row in existing_other), Decimal(0)
                    )
                exemplar = (
                    hidden_users[0].cost
                    if hidden_users
                    else existing_other[0].cost
                )
                result_rows = [
                    *visible_users,
                    OtherUsersAttributionRow(
                        kind="other_users",
                        member_count=len(hidden_users)
                        + sum(row.member_count for row in existing_other),
                        usage=hidden_usage,
                        cost=exemplar.model_copy(
                            update={
                                "amount": hidden_amount,
                                "usage_numerator": hidden_numerator,
                            }
                        ),
                        mapping_state="not_applicable",
                    ),
                ]
            else:
                result_rows = [*user_rows, *existing_other]

        cost_coverage: list[UserAttributionCoverage] = []
        for item in usage_response.coverage:
            allocation_unavailable = (
                item.state in {"available", "partial"}
                and component_summary.coverage_state != "available"
            )
            cost_coverage.append(
                item.model_copy(
                    update={
                        "metric": "cost",
                        "state": (
                            component_summary.coverage_state
                            if allocation_unavailable
                            else item.state
                        ),
                        "reason": (
                            component_summary.coverage_reason
                            if allocation_unavailable
                            else item.reason
                        ),
                        "next_action": (
                            component_summary.next_action
                            if allocation_unavailable
                            and component_summary.next_action is not None
                            else item.next_action
                        ),
                    }
                )
            )
        summary = CostAttributionSummary(
            metric="cost",
            period_id=period.id,
            component_id=component.id,
            declared_total=component_summary.declared_total,
            attributed_amount=component_summary.attributed_amount,
            unattributed_amount=component_summary.unattributed_amount,
            unallocated_amount=component_summary.unallocated_amount,
            currency=component_summary.currency,
            currency_minor_units=component_summary.currency_minor_units,
            allocation_key=component_summary.applied_key or component_summary.preferred_key,
            confidence=component_summary.confidence,
            total_usage=usage_summary.total,
            attributed_usage=usage_summary.attributed,
            unattributed_usage=usage_summary.unattributed,
            distinct_users=usage_summary.distinct_users,
            omitted_users=(
                sum(
                    row.member_count
                    for row in result_rows
                    if row.kind == "other_users"
                )
                if request.group_by == "user" and selected_user is None
                else 0
            ),
        )
        view = AttributionViewData(
            metric="cost",
            group_by=request.group_by,
            access_boundary=usage_response.data.access_boundary,
            rows=result_rows,
            summary=summary,
            primary_measure="allocated_amount",
            calculated_at=usage_response.data.calculated_at,
            latest_observed_at=usage_response.data.latest_observed_at,
        )
        selection_applied = selected_user is not None or selected_department is not None
        rows_total_in_scope = usage_response.bounds.rows_total_in_scope
        if rows_total_in_scope is None:
            rows_total_in_scope = len(result_rows)
        bounds_total = (
            len(result_rows)
            if selection_applied
            else rows_total_in_scope
        )
        bounds = ResultBounds(
                rows_shown=len(result_rows),
                rows_total_in_scope=bounds_total,
                truncated=(
                    not selection_applied
                    and len(result_rows) < rows_total_in_scope
                ),
            )
        # ``usage_response`` is intentionally an internal unbounded projection
        # for user cost.  Every public nested contract above is validated after
        # cost ranking/folding; avoid revalidating the private >500 precursor.
        return AttributionResponse.model_construct(
            data=view,
            coverage=cost_coverage,
            partial_failures=usage_response.partial_failures,
            diagnostics=usage_response.diagnostics,
            refreshed_at=usage_response.refreshed_at,
            cache_status=usage_response.cache_status,
            bounds=bounds,
        )

    async def _query_user_usage_attribution(
        self,
        scope: ObserveScope,
        request: AttributionQueryRequest,
        *,
        config: AttributionConfiguration,
        principal_context: Mapping[str, Any],
        cost_component: CostComponent | None = None,
        unbounded: bool = False,
    ) -> AttributionResponse:
        """Compose delegated user rows without placing identity in shared cache."""
        tenant_id = principal_context.get("tenant_id")
        principal_id = (
            principal_context.get("user_id")
            or principal_context.get("object_id")
            or principal_context.get("user_name")
        )
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("Attribution requires the signed-in tenant identifier.")
        if not isinstance(principal_id, str) or not principal_id.strip():
            raise ValueError(
                "User attribution requires the signed-in principal identifier."
            )

        department_id: str | None = None
        if request.filters.department_filter_token is not None:
            department_id = validate_department_filter_token(
                request.filters.department_filter_token,
                config=config,
                scope=scope,
            ).id
        selected_user_key: str | None = None
        if request.filters.user_filter_token is not None:
            selected_user_key = validate_user_filter_token(
                request.filters.user_filter_token,
                config=config,
                scope=scope,
                tenant_id=tenant_id,
                principal_id=principal_id,
            )
        groups = tuple(
            ()
            if principal_context.get("groups_overage")
            or principal_context.get("group_claims_overage")
            else principal_context.get("groups")
            or principal_context.get("group_ids")
            or ()
        )
        query_config = config_with_principal_group_mappings(
            config,
            tenant_id=tenant_id,
            principal_user_id=principal_context.get("user_id"),
            principal_user_name=principal_context.get("user_name"),
            principal_group_ids=groups,
        )

        inventory = await self.get_inventory(scope)
        sources = [
            source
            for source in inventory.telemetry_sources
            if source.state == "available"
        ]
        started_at = self._clock()
        results = list(
            await self._query_client.query_user_usage(
                sources,
                request.filters,
                config=query_config,
                tenant_id=tenant_id,
                department_id=department_id,
                selected_user_key=selected_user_key,
                **(
                    {"cost_component": cost_component}
                    if cost_component is not None
                    else {}
                ),
            )
        )
        completed_at = self._clock()
        by_source = {result.source_id: result for result in results}
        known_departments = {
            department.id: department for department in query_config.departments
        }
        allowed_department_keys = (
            set(known_departments[department_id].user_keys)
            if department_id is not None
            else None
        )

        user_parts: list[tuple[str, AttributionUsage]] = []
        identities: dict[str, str] = {}
        other_parts: list[AttributionUsage] = []
        other_count = 0
        unattributed_parts: list[AttributionUsage] = []
        coverage: list[UserAttributionCoverage] = []
        failures: list[QuerySourceFailure] = []

        for source in inventory.telemetry_sources:
            result = by_source.get(source.source_id)
            if source.state != "available":
                state: CoverageState = "inaccessible"
                reason, action = _attribution_coverage_text(state)
                coverage.append(
                    normalize_user_attribution_coverage(
                        source=source,
                        status=None,
                        rows=None,
                        refreshed_at=completed_at,
                        metric="usage",
                        attribution_level="user",
                    )
                )
                failures.append(
                    QuerySourceFailure(
                        source_id=source.source_id,
                        status="inaccessible",
                        reason=reason,
                        next_action=action,
                    )
                )
                continue
            if result is None:
                result = SourceResult(
                    source_id=source.source_id,
                    status="error",
                    reason="Attribution query returned no source result.",
                )
            eligible = identified = mapped = ambiguous = returned = 0
            raw_rows = list(result.tables or [])
            for raw_row in raw_rows:
                try:
                    row = _attribution_row_mapping(raw_row)
                    if cost_component is not None and not _attribution_row_matches_cost_component(
                        cost_component, row=row, source=source, inventory=inventory
                    ):
                        continue
                    usage = usage_from_row(row)
                    row_kind = str(
                        row.get("row_kind")
                        or ("user" if row.get("user_key") else "unattributed")
                    )
                    eligible += usage.invocations
                    if row_kind == "other_users":
                        if selected_user_key is not None:
                            continue
                        count = _attribution_count(row, "distinct_users", 1)
                        other_count += count
                        identified += usage.invocations
                        other_parts.append(usage)
                        returned += 1
                        continue
                    if row_kind == "unattributed":
                        unattributed_parts.append(usage)
                        continue
                    user_key = row.get("user_key")
                    raw_identity = row.get("raw_identity")
                    if not isinstance(user_key, str) or not isinstance(
                        raw_identity, str
                    ):
                        raise ValueError("delegated user row requires identity and key")
                    if selected_user_key is not None and user_key != selected_user_key:
                        continue
                    if (
                        allowed_department_keys is not None
                        and user_key not in allowed_department_keys
                    ):
                        continue
                    prior_identity = identities.setdefault(user_key, raw_identity)
                    if prior_identity != raw_identity:
                        raise ValueError(
                            "pseudonymous user key resolved to multiple identities"
                        )
                    user_parts.append((user_key, usage))
                    resolution = resolve_department(
                        user_key=user_key,
                        raw_identity=raw_identity,
                        config=config,
                        principal_user_id=principal_context.get("user_id"),
                        principal_user_name=principal_context.get("user_name"),
                        principal_group_ids=groups,
                    )
                    identified += usage.invocations
                    mapped += usage.invocations if resolution.department_id else 0
                    ambiguous += (
                        usage.invocations if resolution.source == "ambiguous" else 0
                    )
                    returned += 1
                except (TypeError, ValueError):
                    eligible += 1
                    unattributed_parts.append(zero_usage())

            derived_counters = (
                [
                    {
                        "eligible_records": eligible,
                        "identified_records": identified,
                        "mapped_records": mapped,
                        "unattributed_records": max(eligible - mapped, 0),
                        "ambiguous_records": ambiguous,
                        "returned_records": returned,
                    }
                ]
                if raw_rows
                else None
            )
            source_coverage = normalize_user_attribution_coverage(
                source=source,
                status=result.status,
                rows=derived_counters,
                refreshed_at=completed_at,
                metric="usage",
                attribution_level="user",
            )
            coverage.append(source_coverage)
            if result.status != "success":
                default_reason, action = _PARTIAL_FAILURE_DEFAULTS[result.status]
                failures.append(
                    QuerySourceFailure(
                        source_id=result.source_id,
                        status=result.status
                        if result.status in {"partial", "timeout", "error"}
                        else "error",
                        reason=default_reason,
                        next_action=action,
                    )
                )

        total_distinct_users = len({user_key for user_key, _usage in user_parts})
        combined, folded_count, folded_usage = rank_and_fold_user_usage(
            user_parts, max_rows=None if unbounded else 500
        )
        omitted = other_count + folded_count
        if folded_usage is not None:
            other_parts.append(folded_usage)

        rows: list[Any] = []
        for user_key, usage in combined:
            resolution = resolve_department(
                user_key=user_key,
                raw_identity=identities[user_key],
                config=config,
                principal_user_id=principal_context.get("user_id"),
                principal_user_name=principal_context.get("user_name"),
                principal_group_ids=groups,
            )
            rows.append(
                UserAttributionRow(
                    kind="user",
                    user_key=user_key,
                    filter_token=issue_user_filter_token(
                        user_key,
                        config=config,
                        scope=scope,
                        tenant_id=tenant_id,
                        principal_id=principal_id,
                    ),
                    raw_identity=identities[user_key],
                    department_id=resolution.department_id,
                    department_label=resolution.department_label,
                    usage=usage,
                    cost=None,
                    mapping_state=(
                        "mapped" if resolution.department_id else resolution.source
                    ),
                )
            )
        if other_parts and selected_user_key is None:
            rows.append(
                OtherUsersAttributionRow(
                    kind="other_users",
                    member_count=omitted,
                    usage=sum_usage(other_parts),
                    cost=None,
                    mapping_state="not_applicable",
                )
            )

        attributed_usage = sum_usage(row.usage for row in rows)
        unattributed_usage = sum_usage(unattributed_parts)
        total_usage = sum_usage([attributed_usage, unattributed_usage])
        successful = sum(result.status == "success" for result in results)
        partial = sum(result.status == "partial" for result in results)
        unavailable = sum(
            source.state != "available" for source in inventory.telemetry_sources
        )
        failed = unavailable + sum(
            result.status not in {"success", "partial"} for result in results
        )
        diagnostics = QueryDiagnostics(
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=max(int((completed_at - started_at).total_seconds() * 1000), 0),
            source_count=len(inventory.telemetry_sources),
            successful_sources=successful,
            partial_sources=partial,
            failed_sources=failed,
            cache_status="bypass",
        )
        summary = UsageAttributionSummary(
            metric="usage",
            total=total_usage,
            attributed=attributed_usage,
            unattributed=unattributed_usage,
            distinct_users=total_distinct_users + other_count,
            omitted_users=omitted,
        )
        view = (
            AttributionViewData.model_construct(
                metric="usage",
                group_by="user",
                access_boundary="delegated",
                rows=rows,
                summary=summary,
                primary_measure="invocations",
                calculated_at=completed_at,
            )
            if unbounded
            else AttributionViewData(
                metric="usage",
                group_by="user",
                access_boundary="delegated",
                rows=rows,
                summary=summary,
                primary_measure="invocations",
                calculated_at=completed_at,
            )
        )
        response_coverage = _apply_group_overage_coverage(
            _merge_user_attribution_coverage(coverage),
            config=config,
            overage=bool(
                principal_context.get("groups_overage")
                or principal_context.get("group_claims_overage")
            ),
        )
        bounds = (
            ResultBounds.model_construct(
                rows_shown=len(rows),
                rows_total_in_scope=total_distinct_users + other_count,
                truncated=False,
            )
            if unbounded
            else ResultBounds(
                rows_shown=len(rows),
                rows_total_in_scope=total_distinct_users + other_count,
            )
        )
        return (
            AttributionResponse.model_construct(
                data=view,
                coverage=response_coverage,
                partial_failures=failures,
                diagnostics=diagnostics,
                refreshed_at=completed_at,
                cache_status="bypass",
                bounds=bounds,
            )
            if unbounded
            else AttributionResponse(
                data=view,
                coverage=response_coverage,
                partial_failures=failures,
                diagnostics=diagnostics,
                refreshed_at=completed_at,
                cache_status="bypass",
                bounds=bounds,
            )
        )

    async def query_cost(
        self,
        scope: ObserveScope,
        filters: ObserveFilterState,
        *,
        cost_model: CostModel,
        cost_model_fingerprint: str,
        refresh: bool = False,
    ) -> ObserveResult:
        """Allocate one configured period from bounded metadata-only usage views."""
        filters.validate_scope(scope)
        if filters.cost_period_id is None:
            raise ValueError("cost_period_id is required for the Cost view")
        period = next(
            (
                candidate
                for candidate in cost_model.periods
                if candidate.id == filters.cost_period_id
            ),
            None,
        )
        if period is None:
            raise ValueError(
                f"unknown cost period {filters.cost_period_id!r}; "
                "select a configured cost period"
            )
        if len(cost_model_fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in cost_model_fingerprint
        ):
            raise ValueError(
                "cost_model_fingerprint must be a lowercase SHA-256 digest"
            )
        selected_breakdown = filters.cost_breakdown or "agents"
        selected_component = filters.cost_component_id
        selected_agent = filters.cost_agent_key
        required_views = _cost_views_for_components(period, selected_component)

        identity = _identity_key(self._runtime)
        key = _cost_cache_key(
            identity,
            scope,
            model_fingerprint=cost_model_fingerprint,
            period_id=period.id,
            breakdown=selected_breakdown,
            component_id=selected_component,
            cost_agent_key=selected_agent,
        )
        cached = self._cache.get(key, bypass=refresh)
        if cached is not None:
            return ObserveResult(
                view="cost",
                data=cached.data,
                coverage=cached.coverage,
                diagnostics=cached.diagnostics,
                partial_failures=cached.partial_failures,
                bounds=cached.bounds,
                refreshed_at=cached.refreshed_at,
                cache_status="hit",
            )

        inventory = await self.get_inventory(scope)
        available_sources = [
            source
            for source in inventory.telemetry_sources
            if source.state == "available"
        ]
        sources_by_id = {source.source_id: source for source in available_sources}
        period_filters = ObserveFilterState(
            start=period.starts_at,
            end=period.ends_at,
        )
        started_at = self._clock()
        queried = await asyncio.gather(
            *(
                self._query_client.query(
                    available_sources,
                    period_filters,
                    view=view,  # type: ignore[arg-type]
                )
                for view in required_views
            )
        )
        completed_at = self._clock()
        results_by_view = {
            view: list(results)
            for view, results in zip(required_views, queried, strict=True)
        }
        all_results = [
            result for results in results_by_view.values() for result in results
        ]

        coverage = self._discovery_coverage(
            inventory.telemetry_sources, refreshed_at=completed_at
        )
        observations: list[CostUsageObservation] = []
        normalization_error_views: set[str] = set()
        expected_source_ids = set(sources_by_id)
        for view in required_views:
            source_results = results_by_view[view]
            normalized, view_coverage = self._normalize_view(
                view,  # type: ignore[arg-type]
                source_results,
                inventory.telemetry_sources,
                inventory=inventory,
                window_end=period.ends_at,
                refreshed_at=completed_at,
            )
            coverage.extend(view_coverage)
            if any(item.state == "error" for item in view_coverage):
                normalization_error_views.add(view)
            complete = _source_results_complete(
                source_results,
                expected_source_ids=expected_source_ids,
            )
            complete = (
                complete
                and view not in normalization_error_views
                and all(
                    source.state == "available"
                    for source in inventory.telemetry_sources
                )
            )
            if view == "models":
                for result in source_results:
                    source = sources_by_id.get(result.source_id)
                    if source is None:
                        continue
                    for row in list(result.tables or []):
                        try:
                            usage = normalize_model_row(row, source=source)
                        except ValueError:
                            continue
                        if usage.last_seen is not None and not (
                            period.starts_at <= usage.last_seen < period.ends_at
                        ):
                            continue
                        observations.append(
                            _normalize_cost_model_observation(
                                usage,
                                source=source,
                                inventory=inventory,
                                coverage_complete=complete,
                            )
                        )
            elif view == "tools":
                for tool in normalized:
                    if not (period.starts_at <= tool.last_seen < period.ends_at):
                        continue
                    source = sources_by_id.get(tool.source_id)
                    if source is not None:
                        observations.append(
                            _normalize_cost_tool_observation(
                                tool,
                                source_resource_id=(
                                    source.foundry_resource_id or source.resource_id
                                ),
                                coverage_complete=complete,
                            )
                        )
                for result in source_results:
                    source = sources_by_id.get(result.source_id)
                    if source is None:
                        continue
                    for row in list(result.tables or []):
                        observation = _normalize_unattributed_tool_observation(
                            row,
                            source=source,
                            coverage_complete=complete,
                        )
                        if observation is not None:
                            observations.append(observation)
            else:
                for run in normalized:
                    if (
                        run.last_activity_at <= period.starts_at
                        or run.started_at >= period.ends_at
                    ):
                        continue
                    source = sources_by_id.get(run.source_id)
                    if source is not None:
                        observations.append(
                            normalize_cost_run_observation(
                                run,
                                source_resource_id=(
                                    source.foundry_resource_id or source.resource_id
                                ),
                                coverage_complete=complete,
                                period=period,
                            )
                        )

        calculated = allocate_cost_period(
            period,
            observations,
            breakdown=selected_breakdown,
            calculated_at=completed_at,
            component_id=selected_component,
            cost_agent_key=selected_agent,
        )
        components_by_id = {component.id: component for component in period.components}
        merged_summaries = []
        component_coverage_by_id: dict[str, CoverageResult] = {}
        for summary in calculated.components:
            item = _cost_component_coverage(
                summary=summary,
                component=components_by_id[summary.component_id],
                breakdown=selected_breakdown,
                inventory=inventory,
                results_by_view=results_by_view,
                normalization_error_views=normalization_error_views,
                observations=observations,
                refreshed_at=completed_at,
            )
            component_coverage_by_id[summary.component_id] = item
            coverage.append(item)
            merged_summaries.append(
                summary.model_copy(
                    update={
                        "coverage_state": item.state,
                        "coverage_reason": item.reason,
                        "next_action": (
                            None
                            if item.next_action == "No action needed."
                            else item.next_action
                        ),
                    }
                )
            )
        merged_rows = [
            row.model_copy(
                update={
                    "coverage_state": component_coverage_by_id[row.component_id].state,
                    "coverage_reason": component_coverage_by_id[
                        row.component_id
                    ].reason,
                }
            )
            for row in calculated.rows
        ]
        calculated = calculated.model_copy(
            update={"components": merged_summaries, "rows": merged_rows}
        )
        coverage.extend(
            _unmatched_capability_coverage(
                period=period,
                observations=observations,
                breakdown=selected_breakdown,
                refreshed_at=completed_at,
            )
        )
        coverage = _dedupe_coverage(coverage)
        partial_failures = _dedupe_partial_failures(all_results)
        status_by_source = {
            source_id: next(
                (
                    failure.status
                    for failure in partial_failures
                    if failure.source_id == source_id
                ),
                "success",
            )
            for source_id in expected_source_ids
        }
        partial_count = sum(status == "partial" for status in status_by_source.values())
        failed_count = sum(
            status in {"timeout", "throttled", "error"}
            for status in status_by_source.values()
        )
        diagnostics = QueryDiagnostics(
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=max(int((completed_at - started_at).total_seconds() * 1000), 0),
            source_count=len(expected_source_ids),
            successful_sources=max(
                len(expected_source_ids) - partial_count - failed_count, 0
            ),
            partial_sources=partial_count,
            failed_sources=failed_count,
            cache_status="bypass" if refresh else "miss",
        )
        cached_view = _CachedView(
            view="cost",
            data=calculated,
            coverage=coverage,
            diagnostics=diagnostics,
            partial_failures=partial_failures,
            bounds=None,
            refreshed_at=completed_at,
        )
        self._cache.set(key, cached_view)
        return ObserveResult(
            view="cost",
            data=calculated,
            coverage=coverage,
            diagnostics=diagnostics,
            partial_failures=partial_failures,
            bounds=None,
            refreshed_at=completed_at,
            cache_status="bypass" if refresh else "miss",
        )

    async def agent_detail(
        self,
        scope: ObserveScope,
        filters: ObserveFilterState,
        *,
        agent_key: str,
        source_id: str | None = None,
        project_resource_id: str | None = None,
        refresh: bool = False,
    ) -> ObserveResult | None:
        """Return the ``agents`` view narrowed to one agent, or ``None`` if unseen."""
        if not agent_key:
            raise ValueError("agent_key is required")
        result = await self.query_view(
            scope,
            filters,
            view="agents",
            refresh=refresh,
            unpaged=True,
        )
        agents = [
            agent
            for agent in result.data
            if agent.key == agent_key
            and (source_id is None or agent.source_id.casefold() == source_id.casefold())
            and (
                project_resource_id is None
                or agent.project_resource_id == project_resource_id
            )
        ]
        if not agents:
            return None
        return ObserveResult(
            view=result.view,
            data=agents,
            coverage=result.coverage,
            diagnostics=result.diagnostics,
            partial_failures=result.partial_failures,
            bounds=None,
            refreshed_at=result.refreshed_at,
            cache_status=result.cache_status,
        )

    def _discovery_coverage(
        self, sources: Sequence[TelemetrySource], *, refreshed_at: datetime
    ) -> list[CoverageResult]:
        coverage: list[CoverageResult] = []
        for source in sources:
            coverage.append(
                classify_discovery_coverage(
                    source, dimension="resource_access", refreshed_at=refreshed_at
                )
            )
            coverage.append(
                classify_discovery_coverage(
                    source, dimension="telemetry_connection", refreshed_at=refreshed_at
                )
            )
        return coverage

    def _normalize_view(
        self,
        view: View,
        results: Sequence[SourceResult],
        sources: Sequence[TelemetrySource],
        *,
        inventory: ResourceInventory,
        window_end: datetime,
        refreshed_at: datetime,
    ) -> tuple[Any, list[CoverageResult]]:
        sources_by_id = {source.source_id: source for source in sources}
        coverage: list[CoverageResult] = []

        if view == "agents":
            agents: list[ObservedAgent] = []
            for result in results:
                source = sources_by_id.get(result.source_id)
                if source is None:
                    continue
                rows = list(result.tables or [])
                for row in rows:
                    try:
                        agents.append(
                            normalize_agent_row(row, source=source, inventory=inventory)
                        )
                    except ValueError as exc:
                        coverage.append(
                            CoverageResult(
                                source_id=result.source_id,
                                dimension="agent_attribution",
                                state="error",
                                reason=safe_failure_reason(
                                    str(exc),
                                    default="One or more agent rows were malformed.",
                                ),
                                next_action=(
                                    "Verify the workload emits well-formed gen_ai.* attributes."
                                ),
                                refreshed_at=refreshed_at,
                            )
                        )
                coverage.append(
                    classify_query_coverage(
                        source_id=result.source_id,
                        dimension="agent_attribution",
                        status=result.status,
                        row_count=len(rows),
                        reason=result.reason,
                        refreshed_at=refreshed_at,
                    )
                )
                token_reported = any(
                    token_reporting_state(
                        input_tokens=row.get("input_tokens"),
                        output_tokens=row.get("output_tokens"),
                    )
                    == "reported"
                    for row in rows
                )
                coverage.append(
                    classify_query_coverage(
                        source_id=result.source_id,
                        dimension="token_usage",
                        status=result.status,
                        row_count=len(rows),
                        reported=token_reported,
                        reason=result.reason,
                        refreshed_at=refreshed_at,
                    )
                )
            return agents, coverage

        if view == "models":
            models: list[ModelUsage] = []
            for result in results:
                source = sources_by_id.get(result.source_id)
                if source is None:
                    continue
                rows = list(result.tables or [])
                source_models: list[ModelUsage] = []
                for row in rows:
                    try:
                        model_usage = normalize_model_row(row, source=source)
                        models.append(model_usage)
                        source_models.append(model_usage)
                    except ValueError as exc:
                        coverage.append(
                            CoverageResult(
                                source_id=result.source_id,
                                dimension="model_attribution",
                                state="error",
                                reason=safe_failure_reason(
                                    str(exc),
                                    default="One or more model rows were malformed.",
                                ),
                                next_action=(
                                    "Verify the workload emits well-formed gen_ai.* attributes."
                                ),
                                refreshed_at=refreshed_at,
                            )
                        )
                coverage.append(
                    classify_query_coverage(
                        source_id=result.source_id,
                        dimension="model_attribution",
                        status=result.status,
                        row_count=len(rows),
                        reason=result.reason,
                        refreshed_at=refreshed_at,
                    )
                )
                coverage.append(
                    classify_query_coverage(
                        source_id=result.source_id,
                        dimension="token_usage",
                        status=result.status,
                        row_count=len(rows),
                        reported=token_class_inventory(source_models),
                        reason=result.reason,
                        refreshed_at=refreshed_at,
                    )
                )
            return models, coverage

        if view == "tools":
            tools: list[ObservedTool] = []
            for source in sources:
                if source.state != "available":
                    coverage.append(
                        _source_attribution_coverage(
                            source,
                            dimension="tool_attribution",
                            refreshed_at=refreshed_at,
                        )
                    )
            for result in results:
                source = sources_by_id.get(result.source_id)
                if source is None:
                    continue
                raw_rows = list(result.tables or [])
                unattributed_count = max(
                    (
                        int(row.get("unattributed_count") or 0)
                        for row in raw_rows
                        if not isinstance(row.get("unattributed_count"), bool)
                    ),
                    default=0,
                )
                rows = [
                    row for row in raw_rows if row.get("_metadata_only") is not True
                ]
                attributed_rows = 0
                for row in rows:
                    try:
                        tools.append(
                            normalize_tool_row(row, source=source, inventory=inventory)
                        )
                        attributed_rows += 1
                    except ValueError as exc:
                        coverage.append(
                            CoverageResult(
                                source_id=result.source_id,
                                dimension="tool_attribution",
                                state="error",
                                reason=safe_failure_reason(
                                    str(exc),
                                    default="One or more tool rows were malformed.",
                                ),
                                next_action=(
                                    "Verify tool telemetry emits a non-empty tool name and "
                                    "well-formed gen_ai.* attributes."
                                ),
                                refreshed_at=refreshed_at,
                            )
                        )
                if (
                    result.status == "success"
                    and unattributed_count > 0
                    and attributed_rows > 0
                ):
                    coverage.append(
                        CoverageResult(
                            source_id=result.source_id,
                            dimension="tool_attribution",
                            state="partial",
                            reason=(
                                "Some execute-tool telemetry did not include a usable tool name."
                            ),
                            next_action=(
                                "Confirm every tool invocation emits the gen_ai.tool.name attribute."
                            ),
                            refreshed_at=refreshed_at,
                        )
                    )
                else:
                    coverage.append(
                        _attribution_coverage(
                            source_id=result.source_id,
                            dimension="tool_attribution",
                            status=result.status,
                            rows=rows if unattributed_count == 0 else [{}],
                            attributed_rows=attributed_rows,
                            reason=result.reason,
                            refreshed_at=refreshed_at,
                        )
                    )
            return tools, coverage

        if view == "runs":
            runs: list[ObservedRun] = []
            for source in sources:
                if source.state != "available":
                    coverage.append(
                        _source_attribution_coverage(
                            source,
                            dimension="run_correlation",
                            refreshed_at=refreshed_at,
                        )
                    )
            for result in results:
                source = sources_by_id.get(result.source_id)
                if source is None:
                    continue
                rows = list(result.tables or [])
                attributed_rows = 0
                for row in rows:
                    try:
                        run = normalize_run_row(
                            row,
                            source=source,
                            window_end=window_end,
                            inventory=inventory,
                        )
                        runs.append(run)
                        attributed_rows += 1
                    except ValueError as exc:
                        coverage.append(
                            CoverageResult(
                                source_id=result.source_id,
                                dimension="run_correlation",
                                state="error",
                                reason=safe_failure_reason(
                                    str(exc),
                                    default="One or more run rows were malformed.",
                                ),
                                next_action=(
                                    "Verify agent turns retain a conversation or trace "
                                    "correlation identifier."
                                ),
                                refreshed_at=refreshed_at,
                            )
                        )
                coverage.append(
                    _attribution_coverage(
                        source_id=result.source_id,
                        dimension="run_correlation",
                        status=result.status,
                        rows=rows,
                        attributed_rows=attributed_rows,
                        reason=result.reason,
                        refreshed_at=refreshed_at,
                    )
                )
            return runs, coverage

        # "overview": aggregate totals only, never inferring a zero as failure.
        totals: dict[str, int | float | None] = {
            "invocations": 0,
            "failures": 0,
            "avg_latency_ms": None,
            "p95_latency_ms": None,
        }
        weighted_latency = 0.0
        latency_invocations = 0
        source_p95_values: list[float] = []
        for result in results:
            rows = list(result.tables or [])
            for row in rows:
                invocations = int(row.get("invocations") or 0)
                totals["invocations"] = int(totals["invocations"] or 0) + invocations
                totals["failures"] = int(totals["failures"] or 0) + int(
                    row.get("failures") or 0
                )
                average = row.get("avg_latency_ms")
                if average is not None and invocations > 0:
                    weighted_latency += float(average) * invocations
                    latency_invocations += invocations
                p95 = row.get("p95_latency_ms")
                if p95 is not None:
                    source_p95_values.append(float(p95))
            coverage.append(
                classify_query_coverage(
                    source_id=result.source_id,
                    dimension="recent_traces",
                    status=result.status,
                    row_count=len(rows),
                    reason=result.reason,
                    refreshed_at=refreshed_at,
                )
            )
        if latency_invocations:
            totals["avg_latency_ms"] = weighted_latency / latency_invocations
        if source_p95_values:
            # A percentile cannot be recomputed from per-source aggregates. The
            # maximum is a conservative cross-source operational signal.
            totals["p95_latency_ms"] = max(source_p95_values)
        return totals, coverage

    def _build_diagnostics(
        self,
        results: Sequence[SourceResult],
        *,
        started_at: datetime,
        completed_at: datetime,
        cache_status: Literal["miss", "bypass"],
        duration_ms: int | None = None,
        discovery_duration_ms: int = 0,
        query_duration_ms: int = 0,
        normalization_duration_ms: int = 0,
    ) -> QueryDiagnostics:
        successful = sum(1 for result in results if result.status == "success")
        partial = sum(1 for result in results if result.status == "partial")
        failed = sum(
            1
            for result in results
            if result.status in ("timeout", "throttled", "error")
        )
        if duration_ms is None:
            duration_ms = int((completed_at - started_at).total_seconds() * 1000)
        return QueryDiagnostics(
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=max(duration_ms, 0),
            discovery_duration_ms=max(discovery_duration_ms, 0),
            query_duration_ms=max(query_duration_ms, 0),
            normalization_duration_ms=max(normalization_duration_ms, 0),
            source_count=len(results),
            successful_sources=successful,
            partial_sources=partial,
            failed_sources=failed,
            cache_status=cache_status,
        )
