"""Route-facing async facade wiring Observe production composition.

:func:`create_observe_facade` builds the single object ``cockpit.py``'s
``create_app(observe_service=...)`` needs: one :class:`ObserveFacade` that
owns a configured :class:`~agentops.core.observe.ObserveScope` and exposes
``discover``/``query``/``agent_detail``/``trace_content`` coroutines whose
signatures match exactly what ``cockpit.py``'s ``_service_call`` dispatches
(``**kwargs`` by name, JSON-safe ``dict``/``None`` return values).

Two credential chains are deliberately kept apart end to end (FR-071/FR-072):

* ``discover``/``query``/``agent_detail`` share one aggregate, UAMI-only
  :class:`~agentops.agent.observe.service.ObserveService` (built once, in
  :meth:`ObserveFacade.__init__`) backed by the shared, two-minute
  :class:`~agentops.agent.observe.cache.ObserveCache`.
* ``trace_content`` is the only Observe operation permitted to read the
  protected ``AppGenAIContent`` table. It builds a **fresh, per-request**
  delegated (On-Behalf-Of) credential from the caller's Easy Auth access
  token and never touches ``ObserveService``'s cache -- raw generative-AI
  content must never enter a shared cache.

This module imports :mod:`agentops.agent.observe.adapters` (which lazily
imports the Azure SDKs *inside* its methods), so importing
:mod:`agentops.agent.observe.facade` itself never requires the Azure SDKs to
be installed; they are only touched the first time a credential or client is
actually used.
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence, cast, get_args

from agentops.agent.observe import adapters
from agentops.agent.observe.adapters import AzureDiscoveryClient, AzureQueryClient
from agentops.agent.observe.auth import (
    CredentialFactory,
    ObeFactory,
    TokenCredential,
    build_aggregate_credential,
    build_delegated_monitor_credential,
)
from agentops.agent.observe.cache import ObserveCache
from agentops.agent.observe.principal import (
    ACCESS_TOKEN_CONTEXT_KEY,
    ENV_APPLICATION_CLIENT_ID,
    ENV_TENANT_ID,
    ENV_UAMI_CLIENT_ID,
)
from agentops.agent.observe.queries import (
    SourceQuery,
    SourceResult,
    build_appgenai_content_query,
    classify_appgenai_content_result,
    execute_source_batch,
)
from agentops.agent.observe.service import CACHE_TTL_SECONDS, ObserveResult, ObserveService, View
from agentops.agent.observe.ui import build_azure_resource_portal_url
from agentops.core.observe import (
    GenerativeAIContent,
    ObservedAgent,
    ObserveFilterState,
    ObserveScope,
    TraceContentRequest,
)

#: Views the facade forwards directly to ``ObserveService.query_view``. Derived
#: from ``ObserveService.View`` itself (rather than hand-copied) so this set
#: can never silently drift out of sync with the service's own type bounds.
_NATIVE_QUERY_VIEWS: frozenset[str] = frozenset(get_args(View))

#: All views the ``/api/observe/query`` route accepts (matches ``cockpit.py``
#: and ``ObserveQueryRequest.view`` exactly). ``"coverage"`` has no matching
#: entry in ``ObserveService.View``/``adapters._VIEW_QUERY_BUILDERS`` -- the
#: facade bridges it below by re-using the ``"overview"`` query and keeping
#: only its ``coverage`` list. It is intentionally *not* added to
#: ``ObserveService.View``/``ObserveResult.view``'s type bounds: the bridged
#: result keeps ``view="overview"`` at the dataclass level (a real, typed
#: native view) and only the serialized wire payload's ``"view"`` key is
#: overridden to ``"coverage"`` -- see ``_serialize_observe_result``'s
#: ``view_override`` parameter.
_SUPPORTED_QUERY_VIEWS: frozenset[str] = _NATIVE_QUERY_VIEWS | {"coverage"}

#: T053: bounded trend series. Each metric produced by
#: ``build_agent_detail_query`` becomes one named, unit-labelled series; the
#: point count is additionally capped here (on top of the KQL ``take`` bound
#: and the 10-source batch bound) so a facade response can never grow
#: unbounded even if a caller requests a very wide filter window.
MAX_TREND_POINTS = 200
_TREND_METRICS: tuple[tuple[str, str, str], ...] = (
    ("invocations", "Invocations", "count"),
    ("failures", "Failures", "count"),
    ("p95_latency_ms", "P95 Latency", "ms"),
)


@dataclass(frozen=True)
class _AggregateRuntimeContext:
    """``RuntimeContext`` for the shared, aggregate (UAMI) reads.

    Aggregate reads are not tied to any individual signed-in user, so the
    identity used for cache-key derivation is the UAMI client ID itself --
    stable across every caller, which is exactly what makes it safe to share
    the two-minute cache across concurrent requests.
    """

    uami_client_id: str

    @property
    def mode(self) -> str:
        return "aggregate"

    @property
    def credential_identity(self) -> str:
        return self.uami_client_id


def _serialize_data(data: Any) -> Any:
    """Serialize :attr:`ObserveResult.data`, whose shape depends on ``view``.

    ``"overview"`` yields a plain ``dict`` of aggregate totals (see
    ``ObserveService._normalize_view``); ``"agents"``/``"models"`` yield a
    list of pydantic :class:`~agentops.core.observe.ObservedAgent`/
    :class:`~agentops.core.observe.ModelUsage` values. Both must round-trip
    to JSON-safe plain Python.
    """
    if isinstance(data, Mapping):
        return dict(data)
    if isinstance(data, (list, tuple)):
        return [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in data]
    return data


def _serialize_observe_result(result: ObserveResult, *, view_override: str | None = None) -> dict[str, Any]:
    """Manually serialize :class:`ObserveResult` (a frozen dataclass, not a
    pydantic model) into the plain JSON-safe ``dict`` ``cockpit.py``'s
    ``_service_call`` needs -- it only auto-serializes ``dict``/``list``
    results or objects exposing ``.model_dump``.

    Every response includes ``partial_failures`` (T061) alongside
    ``diagnostics``, ``coverage``, and ``refreshed_at`` -- a redacted,
    actionable, per-source list that is empty when every source succeeded.

    ``view_override`` lets a caller report a different wire-facing ``"view"``
    label than ``result.view`` without ever assigning an out-of-bounds value
    to ``ObserveResult.view`` (typed as ``ObserveService.View`` --
    ``Literal["overview", "agents", "models"]``). This is how the
    ``"coverage"`` bridge in :meth:`ObserveFacade.query` reports
    ``"view": "coverage"`` while the underlying, actually-executed
    ``ObserveResult`` legitimately keeps ``view="overview"``.
    """
    return {
        "view": view_override if view_override is not None else result.view,
        "data": _serialize_data(result.data),
        "coverage": [item.model_dump(mode="json") for item in result.coverage],
        "diagnostics": result.diagnostics.model_dump(mode="json"),
        "partial_failures": [asdict(item) for item in result.partial_failures],
        "refreshed_at": result.refreshed_at.isoformat(),
        "cache_status": result.cache_status,
    }


def _trend_bucket_label(row: Mapping[str, Any]) -> str:
    bucket = row.get("TimeGenerated")
    if bucket is None:
        bucket = row.get("bin_TimeGenerated") or row.get("timegenerated")
    if isinstance(bucket, datetime):
        if bucket.tzinfo is None:
            bucket = bucket.replace(tzinfo=timezone.utc)
        return bucket.astimezone(timezone.utc).isoformat()
    return str(bucket)


def _build_trend_series(source_results: Sequence[SourceResult]) -> list[dict[str, Any]]:
    """Flatten bounded ``build_agent_detail_query`` rows into T053 trend series.

    Only ``success``/``partial`` sources contribute rows -- a ``timeout``,
    ``throttled``, or ``error`` source safely contributes nothing rather than
    raising, matching the rest of Observe's "safe actionable failures" rule.
    Rows are merged across sources (an agent can be attributed to more than
    one telemetry source), sorted by bucket, and capped at
    :data:`MAX_TREND_POINTS` so the response always stays bounded.
    """
    rows: list[Mapping[str, Any]] = []
    for result in source_results:
        if result.status not in ("success", "partial"):
            continue
        rows.extend(result.tables or [])
    rows.sort(key=_trend_bucket_label)
    rows = rows[:MAX_TREND_POINTS]

    trends: list[dict[str, Any]] = []
    for field, title, unit in _TREND_METRICS:
        points = [
            (_trend_bucket_label(row), row[field]) for row in rows if row.get(field) is not None
        ]
        if not points:
            continue
        trends.append({"title": title, "unit": unit, "series": [{"label": title, "points": points}]})
    return trends


def _agent_detail_portal_links(
    agent: ObservedAgent, sources: Sequence[Any]
) -> dict[str, str]:
    """T053: portal links derived only from known resource IDs (no trace IDs).

    ``ObservedAgent`` carries no trace/correlation ID, so only resource-level
    Foundry/Azure Monitor links can be derived here; a trace-level link would
    require the protected, OBO-gated :meth:`ObserveFacade.trace_content`
    path, which ``agent_detail`` must never call (aggregate UAMI reads only).
    """
    links: dict[str, str] = {}
    if agent.foundry_resource_id:
        links["foundry_resource"] = build_azure_resource_portal_url(agent.foundry_resource_id)
    if agent.project_resource_id:
        links["foundry_project"] = build_azure_resource_portal_url(agent.project_resource_id)
    for source in sources:
        resource_id = getattr(source, "resource_id", None)
        if resource_id:
            links.setdefault("azure_monitor_resource", build_azure_resource_portal_url(resource_id))
            break
    return links


class ObserveFacade:
    """Route-facing async facade for one configured :class:`ObserveScope`.

    Construct via :func:`create_observe_facade`; the constructor here is kept
    fully explicit (every collaborator is a plain constructor argument) so
    tests can inject fakes for every Azure-touching dependency without any
    monkeypatching.
    """

    def __init__(
        self,
        *,
        scope: ObserveScope,
        tenant_id: str,
        application_client_id: str,
        uami_client_id: str,
        discovery_client: Any | None = None,
        query_client: Any | None = None,
        cache: ObserveCache | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic_clock: Callable[[], float] = time.monotonic,
        credential_factory: CredentialFactory | None = None,
        obo_factory: ObeFactory | None = None,
        aggregate_credential: TokenCredential | None = None,
    ) -> None:
        self._scope = scope
        self._tenant_id = tenant_id
        self._application_client_id = application_client_id
        self._uami_client_id = uami_client_id
        self._clock = clock
        self._monotonic_clock = monotonic_clock
        self._credential_factory = credential_factory
        self._obo_factory = obo_factory

        credential = aggregate_credential or build_aggregate_credential(
            uami_client_id, credential_factory=credential_factory
        )
        self._discovery_client = discovery_client or AzureDiscoveryClient(
            credential=credential, clock=clock
        )
        self._query_client = query_client or AzureQueryClient(
            credential=credential, clock=monotonic_clock
        )
        self._cache = cache or ObserveCache(ttl_seconds=CACHE_TTL_SECONDS)
        self._runtime = _AggregateRuntimeContext(uami_client_id=uami_client_id)
        self._service = ObserveService(
            discovery_client=self._discovery_client,
            query_client=self._query_client,
            runtime=self._runtime,
            clock=clock,
            cache=self._cache,
        )

    # -- discover -----------------------------------------------------

    async def discover(
        self, *, refresh: bool = False, user_context: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Return the scope-bounded resource inventory (aggregate UAMI read)."""
        inventory = await self._service.get_inventory(self._scope, refresh=refresh)
        return inventory.model_dump(mode="json")

    # -- query ----------------------------------------------------------

    async def query(
        self,
        *,
        view: str,
        filters: Mapping[str, Any],
        refresh: bool = False,
        user_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return one normalized, coverage-annotated Observe view.

        ``view="coverage"`` has no native entry in ``ObserveService.View``
        (``Literal["overview", "agents", "models"]``) or
        ``adapters._VIEW_QUERY_BUILDERS`` -- it is bridged here by running
        the *bounded, native* ``"overview"`` query (the same source batch,
        KQL builders, and 10-source/timeout/deadline handling as a real
        ``"overview"`` request -- no separate, unsupported "coverage query"
        is ever issued) and keeping only its full ``coverage`` list
        (discovery + query-level entries), with ``data`` cleared, matching
        ``ui.py``'s coverage/troubleshooting view (a pure coverage table, not
        a data table).

        The underlying :class:`ObserveResult` intentionally keeps
        ``view="overview"`` -- ``ObserveResult.view`` is typed as
        ``ObserveService.View`` and must never hold an out-of-bounds value
        such as ``"coverage"``. Only the serialized wire payload's
        ``"view"`` key is overridden via ``_serialize_observe_result``'s
        ``view_override``, so ``ObserveService``'s own type bounds are never
        weakened to accommodate a route-facing label it does not know about.
        """
        if view not in _SUPPORTED_QUERY_VIEWS:
            raise ValueError(f"unknown Observe view: {view!r}")
        filter_state = ObserveFilterState.model_validate(dict(filters))

        if view == "coverage":
            result = await self._service.query_view(
                self._scope, filter_state, view="overview", refresh=refresh
            )
            result = replace(result, data=[])
            return _serialize_observe_result(result, view_override="coverage")

        native_view = cast(View, view)
        result = await self._service.query_view(
            self._scope, filter_state, view=native_view, refresh=refresh
        )
        return _serialize_observe_result(result)

    # -- agent_detail -----------------------------------------------------

    async def agent_detail(
        self,
        *,
        agent_key: str,
        filters: Mapping[str, Any],
        refresh: bool = False,
        user_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Return one agent's normalized detail plus T053 trends/portal links.

        Returns ``None`` when ``agent_key`` was not observed in the current
        filter window, matching ``ObserveService.agent_detail``'s contract
        exactly; ``cockpit.py`` maps that result to HTTP 404.
        """
        filter_state = ObserveFilterState.model_validate(dict(filters))
        result = await self._service.agent_detail(
            self._scope, filter_state, agent_key=agent_key, refresh=refresh
        )
        if result is None:
            return None

        payload = _serialize_observe_result(result)
        agent = result.data[0] if result.data else None
        trends: list[dict[str, Any]] = []
        portal_links: dict[str, str] = {}
        if agent is not None:
            trends, portal_links = await self._agent_detail_enrichment(
                agent, filter_state, agent_key=agent_key
            )
        payload["trends"] = trends
        payload["portal_links"] = portal_links
        return payload

    async def _agent_detail_enrichment(
        self, agent: ObservedAgent, filters: ObserveFilterState, *, agent_key: str
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        inventory = await self._service.get_inventory(self._scope)
        available = [s for s in inventory.telemetry_sources if s.state == "available"]
        matching = [
            source
            for source in available
            if (agent.foundry_resource_id and source.foundry_resource_id == agent.foundry_resource_id)
            or (agent.project_resource_id and agent.project_resource_id in source.project_resource_ids)
        ]
        sources = matching or available

        trends: list[dict[str, Any]] = []
        query_agent_detail = getattr(self._query_client, "query_agent_detail", None)
        if sources and callable(query_agent_detail):
            source_results = await query_agent_detail(sources, filters, agent_key=agent_key)
            trends = _build_trend_series(source_results)

        portal_links = _agent_detail_portal_links(agent, sources)
        return trends, portal_links

    # -- trace_content -----------------------------------------------------

    async def trace_content(
        self, *, request: Mapping[str, Any], user_context: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Return one delegated, correlation-keyed ``AppGenAIContent`` read.

        This is the *only* Observe operation that reads protected
        generative-AI content and the only one that builds a delegated
        (On-Behalf-Of) credential; the credential and its result are both
        local to this call and are never written to ``ObserveCache`` --
        raw/protected content must never enter a shared cache.
        """
        trace_request = TraceContentRequest.model_validate(dict(request))
        context = user_context or {}
        user_assertion = context.get(ACCESS_TOKEN_CONTEXT_KEY) or ""

        # Raises MissingUserAssertionError (a ValueError) when the assertion
        # is missing/blank -- cockpit.py's _service_call maps that to a safe
        # HTTP 422 with an actionable message; there is no identity-only
        # fallback for a protected-table read (FR-072).
        credential = build_delegated_monitor_credential(
            tenant_id=self._tenant_id,
            client_id=self._application_client_id,
            uami_client_id=self._uami_client_id,
            user_assertion=user_assertion,
            credential_factory=self._credential_factory,
            obo_factory=self._obo_factory,
        )

        inventory = await self._service.get_inventory(self._scope)
        source = next(
            (
                s
                for s in inventory.telemetry_sources
                if s.resource_id == trace_request.source_resource_id
            ),
            None,
        )
        if source is None or not source.workspace_id:
            content = GenerativeAIContent(
                trace_id=trace_request.trace_id,
                span_id=trace_request.span_id,
                source_resource_id=trace_request.source_resource_id,
                protection_state="not_configured",
            )
            return content.model_dump(mode="json")

        logs_client = adapters._LogsQueryAdapter(credential=credential)
        try:
            query = build_appgenai_content_query(
                trace_id=trace_request.trace_id, span_id=trace_request.span_id
            )
            source_query = SourceQuery(
                source_id=source.source_id, workspace_id=source.workspace_id, query=query
            )
            results = await execute_source_batch(
                [source_query], client=logs_client, clock=self._monotonic_clock
            )
        finally:
            await logs_client.aclose()

        result = results[0] if results else None
        rows: list[Mapping[str, Any]] = []
        if result is not None and result.status in ("success", "partial"):
            rows = list(result.tables or [])

        content = classify_appgenai_content_result(
            rows,
            source_resource_id=trace_request.source_resource_id,
            trace_id=trace_request.trace_id,
            span_id=trace_request.span_id,
        )
        return content.model_dump(mode="json")

    async def aclose(self) -> None:
        """Release the aggregate query client's pooled network resources."""
        aclose = getattr(self._query_client, "aclose", None)
        if callable(aclose):
            await aclose()


def create_observe_facade(
    *,
    scope: ObserveScope | Mapping[str, Any],
    tenant_id: str | None = None,
    application_client_id: str | None = None,
    uami_client_id: str | None = None,
    env: Mapping[str, str] | None = None,
    discovery_client: Any | None = None,
    query_client: Any | None = None,
    cache: ObserveCache | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic_clock: Callable[[], float] = time.monotonic,
    credential_factory: CredentialFactory | None = None,
    obo_factory: ObeFactory | None = None,
) -> ObserveFacade:
    """Build the single ``ObserveFacade`` ``create_app(observe_service=...)`` needs.

    Reads the canonical hosted environment variables when the corresponding
    keyword is omitted: ``AGENTOPS_TENANT_ID``, ``AGENTOPS_APPLICATION_CLIENT_ID``,
    ``AGENTOPS_UAMI_CLIENT_ID`` (see ``principal.py``'s ``ENV_*`` constants,
    the same names ``build_easy_auth_resolver`` reads). Raises ``ValueError``
    listing every missing variable so a misconfigured hosted deployment fails
    fast with an actionable message instead of a confusing Azure SDK error
    deep inside the first request.
    """
    env_map = env if env is not None else os.environ
    resolved_scope = scope if isinstance(scope, ObserveScope) else ObserveScope.model_validate(scope)
    resolved_tenant_id = tenant_id or env_map.get(ENV_TENANT_ID)
    resolved_application_client_id = application_client_id or env_map.get(ENV_APPLICATION_CLIENT_ID)
    resolved_uami_client_id = uami_client_id or env_map.get(ENV_UAMI_CLIENT_ID)

    missing = [
        name
        for name, value in (
            (ENV_TENANT_ID, resolved_tenant_id),
            (ENV_APPLICATION_CLIENT_ID, resolved_application_client_id),
            (ENV_UAMI_CLIENT_ID, resolved_uami_client_id),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "missing required Observe hosted configuration: " + ", ".join(missing)
        )

    return ObserveFacade(
        scope=resolved_scope,
        tenant_id=resolved_tenant_id,  # type: ignore[arg-type]
        application_client_id=resolved_application_client_id,  # type: ignore[arg-type]
        uami_client_id=resolved_uami_client_id,  # type: ignore[arg-type]
        discovery_client=discovery_client,
        query_client=query_client,
        cache=cache,
        clock=clock,
        monotonic_clock=monotonic_clock,
        credential_factory=credential_factory,
        obo_factory=obo_factory,
    )
