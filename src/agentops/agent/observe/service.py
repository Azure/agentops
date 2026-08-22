"""Observe orchestration: normalization, deterministic coverage, and caching.

Coordinates scope-bounded discovery (:mod:`agentops.agent.observe.discovery`)
and bounded per-source querying (:mod:`agentops.agent.observe.queries`) with
the shared, non-sensitive :class:`~agentops.agent.observe.cache.ObserveCache`
into one identity/scope/filter-keyed Observe response.

Normalizes raw query rows into the versioned contracts in
:mod:`agentops.core.observe` (:class:`ObservedAgent`, :class:`ModelUsage`,
:class:`CoverageResult`) and classifies coverage deterministically: it never
infers ``available`` from the mere absence of an error, and never treats an
empty result or an all-null reportable field as a failure. Raw
generative-AI content never reaches the shared cache -- only normalized
aggregates are ever passed to :meth:`ObserveCache.set`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Mapping, Protocol, Sequence

from agentops.agent.observe.cache import ObserveCache
from agentops.agent.observe.queries import SourceResult, SourceStatus
from agentops.core.observe import (
    CoverageResult,
    CoverageState,
    ModelUsage,
    ObserveFilterState,
    ObserveScope,
    ObservedAgent,
    QueryDiagnostics,
    ResourceInventory,
    TelemetrySource,
    canonical_arm_id,
)

#: Identity/scope/filter cache entries stay fresh for two minutes (T046).
CACHE_TTL_SECONDS = 120.0

View = Literal["overview", "agents", "models"]


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
    """

    view: View
    data: Any
    coverage: list[CoverageResult]
    diagnostics: QueryDiagnostics
    partial_failures: list[PartialFailure]
    refreshed_at: datetime
    """When the underlying data was produced; stable across cache hits."""


# ---------------------------------------------------------------------------
# Normalization: rows -> contracts, source attribution, token labelling (T045)
# ---------------------------------------------------------------------------


def agent_source_kind(
    *, agent_id: str | None, agent_name: str | None
) -> Literal["foundry", "external", "unknown"]:
    """Classify an agent row using OTel ``gen_ai`` semantic conventions.

    Foundry Agent Service always reports ``gen_ai.agent.id``. Externally
    instrumented workloads (LangGraph, custom agents, ...) commonly report
    only ``gen_ai.agent.name``. Neither present means the row cannot be
    attributed to any agent at all.
    """
    if agent_id:
        return "foundry"
    if agent_name:
        return "external"
    return "unknown"


def token_reporting_state(
    *, input_tokens: int | None, output_tokens: int | None
) -> Literal["reported", "not_reported"]:
    """Distinguish "this dimension is not emitted" from a genuine zero (T059)."""
    return "reported" if input_tokens is not None or output_tokens is not None else "not_reported"


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


def normalize_agent_row(row: Mapping[str, Any], *, source: TelemetrySource) -> ObservedAgent:
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
        agent_id=agent_id,
        agent_name=agent_name,
        project_resource_id=project_resource_id,
        foundry_resource_id=source.foundry_resource_id,
        source_kind=agent_source_kind(agent_id=agent_id, agent_name=agent_name),
        model=row.get("model") or None,
        last_seen=last_seen,
        invocations=int(row.get("invocations") or 0),
        failures=int(row.get("failures") or 0),
        p95_latency_ms=row.get("p95_latency_ms"),
        input_tokens=row.get("input_tokens"),
        output_tokens=row.get("output_tokens"),
    )


def normalize_model_row(row: Mapping[str, Any], *, source: TelemetrySource) -> ModelUsage:
    """Normalize one ``build_models_query`` result row into a :class:`ModelUsage`."""
    project_resource_id = _project_resource_id_for_row(row, source=source)
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
        "recent_traces", "agent_attribution", "model_attribution", "token_usage"
    ],
    status: SourceStatus,
    row_count: int,
    reported: bool = True,
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
    if status == "success":
        if row_count == 0:
            state: CoverageState = "no_data"
            default_reason = "No matching telemetry rows were found in this window."
            next_action = "Widen the time range or confirm the workload was active."
        elif not reported:
            state = "not_reported"
            default_reason = "Telemetry rows exist but do not report this dimension."
            next_action = "Confirm the workload emits the expected gen_ai.* attributes."
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


class ObserveService:
    """Coordinates discovery, bounded querying, normalization, and caching.

    Every request is keyed by ``(identity, scope, filters, view)`` and
    cached for two minutes (``CACHE_TTL_SECONDS``); ``refresh=True`` bypasses
    the cache read, but a freshly normalized response is always re-cached
    afterwards. Only normalized :class:`ObservedAgent`/:class:`ModelUsage`/
    :class:`CoverageResult`/:class:`QueryDiagnostics` values are ever
    cached -- raw per-row query results and any ``GenerativeAIContent``
    never reach ``cache``.
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
            view, source_results, available_sources, refreshed_at=completed_at
        )
        coverage.extend(query_coverage)

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
                refreshed_at=completed_at,
            ),
        )
        return ObserveResult(
            view=view,
            data=data,
            coverage=coverage,
            diagnostics=diagnostics,
            partial_failures=partial_failures,
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
                        agents.append(normalize_agent_row(row, source=source))
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
                for row in rows:
                    try:
                        models.append(normalize_model_row(row, source=source))
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
            return models, coverage

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
