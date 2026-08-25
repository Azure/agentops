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
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, Mapping, Protocol, Sequence

from agentops.agent.observe.cache import ObserveCache
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
from agentops.core.observe import (
    CoverageResult,
    CoverageState,
    ModelUsage,
    ObserveFilterState,
    ObserveScope,
    ObservedAgent,
    ObservedRun,
    ObservedTool,
    QueryDiagnostics,
    ResultBounds,
    ResourceInventory,
    RuntimeKind,
    TelemetrySource,
    canonical_arm_id,
)

#: Identity/scope/filter cache entries stay fresh for two minutes (T046).
CACHE_TTL_SECONDS = 120.0

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
    cache_status: Literal["hit", "miss", "bypass"]


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


def _normalized_runtime_value(value: Any) -> str | None:
    """Return a normalized, non-empty metadata value without coercing objects."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("-", "_")
    return normalized or None


def _is_copilot_studio_provider(provider_name: Any, system: Any) -> bool:
    values = (_normalized_runtime_value(provider_name), _normalized_runtime_value(system))
    return any(
        value is not None and value.replace("_", " ") in _COPILOT_STUDIO_PROVIDERS
        for value in values
    )


def _inventory_agent_records(inventory: ResourceInventory | None) -> list[Mapping[str, Any]]:
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
                    records.extend(item for item in candidates if isinstance(item, Mapping))
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
    return "reported" if input_tokens is not None or output_tokens is not None else "not_reported"


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
        label for label, _field in _TOKEN_CLASS_FIELDS if label in partially_reported_names
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
        raise ValueError("telemetry row project_resource_id is outside its source boundary")
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
        raise ValueError(f"agent row for {agent_key!r} is missing a last_seen timestamp")
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
    )


def _required_datetime(row: Mapping[str, Any], *, field: str, row_label: str) -> datetime:
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
    last_activity_at = _required_datetime(row, field="last_activity_at", row_label="run")
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
    return ObservedRun(
        **normalized
    )


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
    if row.get("_metadata_only") is not True or raw_count is None or int(raw_count) <= 0:
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


def normalize_model_row(row: Mapping[str, Any], *, source: TelemetrySource) -> ModelUsage:
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
    reported: bool | Literal["reported", "partial", "not_reported"] | TokenClassInventory = True,
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
            reported_names = ", ".join(inventory.reported_classes) if inventory else "some"
            missing_names = ", ".join(inventory.missing_classes) if inventory else "some"
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
            next_action = "Configure instrumentation to " + " and ".join(action_parts) + "."
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
        rows = [
            row for row in all_rows if row.get("_metadata_only") is not True
        ]
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


def _cost_views_for_components(period: CostPeriod, component_id: str | None) -> tuple[str, ...]:
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
        not inventory.telemetry_sources
        or bool(unconfigured_sources)
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
    ) -> None:
        self._discovery_client = discovery_client
        self._query_client = query_client
        self._runtime = runtime
        self._clock = clock
        self._cache = cache

    async def get_inventory(
        self, scope: ObserveScope, *, refresh: bool = False
    ) -> ResourceInventory:
        """Discover (or return the cached) resource inventory for *scope*."""
        identity = _identity_key(self._runtime)
        key = _cache_key(identity, scope, "discovery", None)
        cached = self._cache.get(key, bypass=refresh)
        if cached is not None:
            return cached
        inventory = await self._discovery_client.discover(scope)
        self._cache.set(key, inventory)
        return inventory

    async def query_view(
        self,
        scope: ObserveScope,
        filters: ObserveFilterState,
        *,
        view: View,
        refresh: bool = False,
    ) -> ObserveResult:
        """Return the normalized, coverage-annotated response for *view*."""
        filters.validate_scope(scope)
        identity = _identity_key(self._runtime)
        key = _cache_key(identity, scope, view, filters)
        cached = self._cache.get(key, bypass=refresh)
        if cached is not None:
            return ObserveResult(
                view=cached.view,
                data=cached.data,
                coverage=cached.coverage,
                diagnostics=cached.diagnostics,
                partial_failures=cached.partial_failures,
                bounds=cached.bounds,
                refreshed_at=cached.refreshed_at,
                cache_status="hit",
            )

        inventory = await self.get_inventory(scope, refresh=refresh)
        available_sources = [
            source for source in inventory.telemetry_sources if source.state == "available"
        ]

        started_at = self._clock()
        source_results = list(
            await self._query_client.query(available_sources, filters, view=view)
        )
        completed_at = self._clock()

        coverage = self._discovery_coverage(inventory.telemetry_sources, refreshed_at=completed_at)
        data, query_coverage = self._normalize_view(
            view,
            source_results,
            inventory.telemetry_sources,
            inventory=inventory,
            window_end=filters.end,
            refreshed_at=completed_at,
        )
        if view in ("agents", "models", "tools", "runs"):
            data = _bound_view_data(view, data)
        coverage.extend(query_coverage)
        bounds = (
            _result_bounds(source_results, rows_shown=len(data))
            if view in ("agents", "models", "tools", "runs")
            else None
        )

        diagnostics = self._build_diagnostics(
            source_results,
            started_at=started_at,
            completed_at=completed_at,
            cache_status="bypass" if refresh else "miss",
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
            cache_status="bypass" if refresh else "miss",
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
        if (
            len(cost_model_fingerprint) != 64
            or any(
                character not in "0123456789abcdef"
                for character in cost_model_fingerprint
            )
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

        inventory = await self.get_inventory(scope, refresh=refresh)
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
            view: list(results) for view, results in zip(required_views, queried, strict=True)
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
        components_by_id = {
            component.id: component for component in period.components
        }
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
                    "coverage_state": component_coverage_by_id[
                        row.component_id
                    ].state,
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
            duration_ms=max(
                int((completed_at - started_at).total_seconds() * 1000), 0
            ),
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
        refresh: bool = False,
    ) -> ObserveResult | None:
        """Return the ``agents`` view narrowed to one agent, or ``None`` if unseen."""
        if not agent_key:
            raise ValueError("agent_key is required")
        result = await self.query_view(scope, filters, view="agents", refresh=refresh)
        agents = [agent for agent in result.data if agent.key == agent_key]
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
                        agents.append(normalize_agent_row(row, source=source, inventory=inventory))
                    except ValueError as exc:
                        coverage.append(
                            CoverageResult(
                                source_id=result.source_id,
                                dimension="agent_attribution",
                                state="error",
                                reason=safe_failure_reason(
                                    str(exc), default="One or more agent rows were malformed."
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
                                    str(exc), default="One or more model rows were malformed."
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
                        tools.append(normalize_tool_row(row, source=source, inventory=inventory))
                        attributed_rows += 1
                    except ValueError as exc:
                        coverage.append(
                            CoverageResult(
                                source_id=result.source_id,
                                dimension="tool_attribution",
                                state="error",
                                reason=safe_failure_reason(
                                    str(exc), default="One or more tool rows were malformed."
                                ),
                                next_action=(
                                    "Verify tool telemetry emits a non-empty tool name and "
                                    "well-formed gen_ai.* attributes."
                                ),
                                refreshed_at=refreshed_at,
                            )
                        )
                if result.status == "success" and unattributed_count > 0 and attributed_rows > 0:
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
                                    str(exc), default="One or more run rows were malformed."
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
        totals = {"invocations": 0, "failures": 0}
        for result in results:
            rows = list(result.tables or [])
            for row in rows:
                totals["invocations"] += int(row.get("invocations") or 0)
                totals["failures"] += int(row.get("failures") or 0)
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
        return totals, coverage

    def _build_diagnostics(
        self,
        results: Sequence[SourceResult],
        *,
        started_at: datetime,
        completed_at: datetime,
        cache_status: Literal["miss", "bypass"],
    ) -> QueryDiagnostics:
        successful = sum(1 for result in results if result.status == "success")
        partial = sum(1 for result in results if result.status == "partial")
        failed = sum(
            1 for result in results if result.status in ("timeout", "throttled", "error")
        )
        duration_ms = max(int((completed_at - started_at).total_seconds() * 1000), 0)
        return QueryDiagnostics(
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            source_count=len(results),
            successful_sources=successful,
            partial_sources=partial,
            failed_sources=failed,
            cache_status=cache_status,
        )
