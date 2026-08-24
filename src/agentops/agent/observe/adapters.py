"""Lazy-imported Azure SDK adapters for :mod:`agentops.agent.observe`.

This module is the *only* place in ``agentops.agent.observe`` that imports
concrete Azure SDK packages (``azure-mgmt-resourcegraph``,
``azure-mgmt-applicationinsights``, ``azure-monitor-query``,
``azure-ai-projects``). Every import is deferred to inside a method body so
importing :mod:`agentops.agent.observe.adapters` itself never requires those
packages to be installed, and importing any other module in this package
never pulls in the Azure SDKs transitively.

:mod:`agentops.agent.observe.discovery` and
:mod:`agentops.agent.observe.queries` are written entirely against small
duck-typed client protocols (``resource_graph_client.resources(...)``,
``application_insights_client.components.get(...)``,
``connections_by_project(project_id).list()``,
``client.query_batch(requests)``) precisely so they never need to import an
Azure SDK and stay trivially testable with fakes. The classes in this module
translate those duck types to and from the real, network-calling Azure SDK
clients, and are exercised in tests with fakes that imitate the *real* SDK's
response shapes (rather than the already-tested duck types), so the
translation itself is what is covered.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence, TypeVar

from agentops.agent.observe.discovery import build_resource_inventory, subscription_ids_for_scope
from agentops.agent.observe.queries import (
    DEFAULT_REQUEST_DEADLINE_SECONDS,
    MAX_SOURCES_PER_BATCH,
    SOURCE_TIMEOUT_SECONDS,
    SourceQuery,
    SourceResult,
    build_agent_detail_query,
    build_agents_query,
    build_models_query,
    build_overview_query,
    build_runs_query,
    build_tools_query,
    execute_source_batch,
)
from agentops.agent.observe.service import View
from agentops.core.observe import ObserveFilterState, ObserveScope, ResourceInventory, TelemetrySource

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

_VIEW_QUERY_BUILDERS: dict[View, Callable[..., str]] = {
    "overview": build_overview_query,
    "agents": build_agents_query,
    "models": build_models_query,
    "tools": build_tools_query,
    "runs": build_runs_query,
}


def _chunked(items: Sequence[_T], size: int) -> Iterator[list[_T]]:
    """Yield *items* in consecutive chunks of at most *size* elements."""
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


# ---------------------------------------------------------------------------
# Resource Graph discovery adapter (T036/T037): App Service has no ``az``
# CLI, so scope-bounded discovery must go through ``azure-mgmt-resourcegraph``
# rather than shelling out to ``az graph query``.
# ---------------------------------------------------------------------------


class _ResourceGraphAdapter:
    """Adapts ``azure-mgmt-resourcegraph`` to ``discovery.py``'s
    ``resource_graph_client.resources(query=, subscriptions=)`` duck type.
    """

    def __init__(self, *, credential: Any) -> None:
        self._credential = credential
        self._client: Any = None

    def _client_instance(self) -> Any:
        if self._client is None:
            from azure.mgmt.resourcegraph import ResourceGraphClient  # lazy Azure import

            self._client = ResourceGraphClient(self._credential)
        return self._client

    def resources(self, *, query: str, subscriptions: Sequence[str]) -> Any:
        from azure.mgmt.resourcegraph.models import (  # lazy Azure import
            QueryRequest,
            QueryRequestOptions,
        )

        request = QueryRequest(
            subscriptions=list(subscriptions),
            query=query,
            options=QueryRequestOptions(top=1000),
        )
        return self._client_instance().resources(request)


# ---------------------------------------------------------------------------
# Application Insights component -> Log Analytics workspace resolution.
# ---------------------------------------------------------------------------


class _ApplicationInsightsAdapter:
    """Binds ``azure-mgmt-applicationinsights`` to one subscription and
    exposes ``.components`` so ``discovery.py`` can call
    ``application_insights_client.components.get(resource_group_name=,
    resource_name=)`` unchanged.
    """

    def __init__(self, *, credential: Any, subscription_id: str) -> None:
        self._credential = credential
        self._subscription_id = subscription_id
        self._client: Any = None

    def _client_instance(self) -> Any:
        if self._client is None:
            from azure.mgmt.applicationinsights import (  # lazy Azure import
                ApplicationInsightsManagementClient,
            )

            self._client = ApplicationInsightsManagementClient(self._credential, self._subscription_id)
        return self._client

    @property
    def components(self) -> Any:
        return self._client_instance().components


# ---------------------------------------------------------------------------
# Foundry project connection metadata (credential-free; no delegated
# access -- discovering *that* a connection exists is project metadata, not
# telemetry content).
# ---------------------------------------------------------------------------


def _project_endpoint_from_arm_id(project_resource_id: str) -> str | None:
    """Return the Foundry project endpoint for a project ARM ID, or ``None``.

    Inverse of the ``https://<account>.services.ai.azure.com/api/projects/<project>``
    -> ARM ID mapping used across AgentOps (see ``infra/e2e/bootstrap.bicep``).
    """
    segments = project_resource_id.strip("/").split("/")
    lower_segments = [segment.lower() for segment in segments]
    try:
        accounts_index = lower_segments.index("accounts")
        projects_index = lower_segments.index("projects")
    except ValueError:
        return None
    if projects_index <= accounts_index or projects_index + 1 >= len(segments):
        return None
    account_name = segments[accounts_index + 1]
    project_name = segments[projects_index + 1]
    if not account_name or not project_name:
        return None
    return f"https://{account_name}.services.ai.azure.com/api/projects/{project_name}"


def _default_project_connections(project_resource_id: str, *, credential: Any) -> Any:
    """Return the ``.connections`` accessor for one Foundry project, or ``None``."""
    endpoint = _project_endpoint_from_arm_id(project_resource_id)
    if endpoint is None:
        return None
    from azure.ai.projects import AIProjectClient  # lazy Azure import

    client = AIProjectClient(endpoint=endpoint, credential=credential)
    return client.connections


# ---------------------------------------------------------------------------
# AzureDiscoveryClient: implements service.py's ``DiscoveryClient`` protocol.
# ---------------------------------------------------------------------------


class AzureDiscoveryClient:
    """Scope-bounded Foundry/telemetry discovery over real Azure SDK clients.

    Discovery itself (``discovery.py``) is synchronous -- Resource Graph and
    Application Insights management calls are blocking network calls with no
    async client -- so :meth:`discover` runs it on a worker thread via
    :func:`asyncio.to_thread` to keep the event loop free.

    Known limitation: ``discovery.py``'s ``application_insights_client`` is a
    single client bound to one subscription. When ``scope.mode == "projects"``
    spans multiple subscriptions, this adapter binds to the *first*
    subscription discovered across the configured project IDs; workspace
    resolution for projects in any other subscription will safely report
    ``not_configured`` rather than raise. Fixing this would require changing
    ``discovery.py``'s contract, which is out of scope for this adapter.
    """

    def __init__(
        self,
        *,
        credential: Any,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._credential = credential
        self._clock = clock
        self._resource_graph_client = _ResourceGraphAdapter(credential=credential)
        self._app_insights_clients: dict[str, _ApplicationInsightsAdapter] = {}

    def _app_insights_client_for(self, subscription_id: str | None) -> Any:
        if subscription_id is None:
            return None
        client = self._app_insights_clients.get(subscription_id)
        if client is None:
            client = _ApplicationInsightsAdapter(credential=self._credential, subscription_id=subscription_id)
            self._app_insights_clients[subscription_id] = client
        return client

    async def discover(self, scope: ObserveScope) -> ResourceInventory:
        return await asyncio.to_thread(self.discover_sync, scope)

    def discover_sync(self, scope: ObserveScope) -> ResourceInventory:
        """Discover inventory synchronously for CLI preflight callers."""
        subscriptions = subscription_ids_for_scope(scope)
        application_insights_client = self._app_insights_client_for(
            subscriptions[0] if subscriptions else None
        )
        connections_by_project = functools.partial(_default_project_connections, credential=self._credential)
        return build_resource_inventory(
            scope,
            resource_graph_client=self._resource_graph_client,
            connections_by_project=connections_by_project,
            application_insights_client=application_insights_client,
            clock=self._clock,
        )


# ---------------------------------------------------------------------------
# Logs Query batch adapter (T044): flattens the real
# ``azure.monitor.query(.aio)`` response shapes into the flat dict rows
# ``queries.py``'s ``_classify_batch_item``/``service.py``'s
# ``normalize_agent_row``/``normalize_model_row`` expect.
# ---------------------------------------------------------------------------


class _FlattenedBatchItem:
    """Duck-typed batch-item shape consumed by ``queries._classify_batch_item``."""

    __slots__ = ("error", "partial_error", "partial_data", "tables", "status")

    def __init__(
        self,
        *,
        error: Any = None,
        partial_error: Any = None,
        partial_data: Any = None,
        tables: Any = None,
        status: Any = None,
    ) -> None:
        self.error = error
        self.partial_error = partial_error
        self.partial_data = partial_data
        self.tables = tables
        self.status = status


def _flatten_logs_table(table: Any) -> list[dict[str, Any]]:
    """Flatten one ``LogsTable``-shaped object (``.columns``/``.rows``) into dict rows.

    Aggregate metadata such as ``total_in_scope`` is preserved verbatim for
    the service layer to calculate bounds; absent metadata is never invented.
    """
    raw_columns = getattr(table, "columns", None) or []
    column_names = [
        column if isinstance(column, str) else str(getattr(column, "name", column)) for column in raw_columns
    ]
    rows = getattr(table, "rows", None) or []
    return [dict(zip(column_names, row)) for row in rows]


def _flatten_tables(tables: Any) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for table in tables or []:
        flattened.extend(_flatten_logs_table(table))
    return flattened


def _flatten_logs_query_response(response: Any) -> _FlattenedBatchItem:
    error = getattr(response, "error", None)
    if error is not None:
        return _FlattenedBatchItem(error=error)

    partial_error = getattr(response, "partial_error", None)
    tables = getattr(response, "tables", None)
    if partial_error is not None:
        partial_tables = _flatten_tables(getattr(response, "partial_data", None) or tables)
        return _FlattenedBatchItem(partial_error=partial_error, partial_data=partial_tables, tables=partial_tables)

    return _FlattenedBatchItem(tables=_flatten_tables(tables), status=getattr(response, "status", None))


def _query_workspace_rest(
    *,
    workspace_id: str,
    query: str,
    access_token: str,
    timeout_seconds: int,
) -> _FlattenedBatchItem:
    """Execute one Logs query when the SDK cannot decode the service response."""
    request = urllib.request.Request(
        f"https://api.loganalytics.io/v1/workspaces/{workspace_id}/query",
        data=json.dumps({"query": query}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))

    return _flatten_logs_rest_payload(payload)


def _flatten_logs_rest_payload(payload: Mapping[str, Any]) -> _FlattenedBatchItem:
    """Match the Azure SDK's Python value types for a Logs REST response."""
    rows: list[dict[str, Any]] = []
    for table in payload.get("tables", []):
        columns = table.get("columns", [])
        for values in table.get("rows", []):
            row: dict[str, Any] = {}
            for column, value in zip(columns, values):
                if column.get("type") == "datetime" and isinstance(value, str):
                    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
                row[column["name"]] = value
            rows.append(row)
    return _FlattenedBatchItem(tables=rows, status="SUCCESS")


class _LogsQueryAdapter:
    """Adapts ``azure.monitor.query.aio.LogsQueryClient.query_batch`` to
    ``queries.py``'s ``client.query_batch(requests)`` duck type, flattening
    each response's tables into plain dict rows.
    """

    def __init__(self, *, credential: Any) -> None:
        self._credential = credential
        self._client: Any = None

    def _client_instance(self) -> Any:
        if self._client is None:
            from azure.monitor.query.aio import LogsQueryClient  # lazy Azure import

            self._client = LogsQueryClient(self._credential)
        return self._client

    async def _query_individually(
        self, requests: Sequence[Any]
    ) -> list[_FlattenedBatchItem]:
        try:
            token = await self._credential.get_token(
                "https://api.loganalytics.io/.default"
            )
        except Exception as error:
            return [_FlattenedBatchItem(error=error) for _ in requests]

        responses = await asyncio.gather(
            *(
                asyncio.to_thread(
                    _query_workspace_rest,
                    workspace_id=request.workspace_id,
                    query=request.query,
                    access_token=token.token,
                    timeout_seconds=request.server_timeout_seconds,
                )
                for request in requests
            ),
            return_exceptions=True,
        )
        flattened: list[_FlattenedBatchItem] = []
        for response in responses:
            if isinstance(response, BaseException):
                if not isinstance(response, Exception):
                    raise response
                flattened.append(_FlattenedBatchItem(error=response))
            else:
                flattened.append(response)
        return flattened

    async def query_batch(self, requests: Sequence[Any]) -> list[_FlattenedBatchItem]:
        from azure.monitor.query import LogsBatchQuery  # lazy Azure import

        batch = [
            LogsBatchQuery(
                query=request.query,
                timespan=request.timespan,
                workspace_id=request.workspace_id,
                server_timeout=request.server_timeout_seconds,
            )
            for request in requests
        ]
        try:
            responses = await self._client_instance().query_batch(
                batch, headers={"Accept-Encoding": "identity"}
            )
        except UnicodeDecodeError:
            logger.warning(
                "Azure Monitor batch response could not be decoded; "
                "retrying Observe queries individually through the Logs REST endpoint"
            )
            return await self._query_individually(requests)
        return [_flatten_logs_query_response(response) for response in responses]

    async def aclose(self) -> None:
        if self._client is not None:
            close = getattr(self._client, "close", None)
            if callable(close):
                result = close()
                if asyncio.iscoroutine(result):
                    await result


# ---------------------------------------------------------------------------
# AzureQueryClient: implements service.py's ``QueryClient`` protocol, plus an
# extra ``query_agent_detail`` method used by the facade's T053 trend
# enrichment (not part of the strict ``QueryClient`` Protocol -- Protocols do
# not forbid extra methods, and the facade only calls it defensively via
# ``getattr``).
# ---------------------------------------------------------------------------


class AzureQueryClient:
    """Executes bounded, batched per-source telemetry queries (T044) over
    ``azure-monitor-query``, chunking any number of available sources into
    groups of at most :data:`~agentops.agent.observe.queries.MAX_SOURCES_PER_BATCH`.
    """

    def __init__(
        self,
        *,
        credential: Any,
        source_timeout_seconds: int = SOURCE_TIMEOUT_SECONDS,
        request_deadline_seconds: int = DEFAULT_REQUEST_DEADLINE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._source_timeout_seconds = source_timeout_seconds
        self._request_deadline_seconds = request_deadline_seconds
        self._clock = clock
        self._logs_client = _LogsQueryAdapter(credential=credential)

    async def _run(
        self,
        sources: Iterable[TelemetrySource],
        build_query: Callable[[TelemetrySource], str],
    ) -> list[SourceResult]:
        queryable = [source for source in sources if source.workspace_id]
        results: list[SourceResult] = []
        for chunk in _chunked(queryable, MAX_SOURCES_PER_BATCH):
            queries = [
                SourceQuery(
                    source_id=source.source_id,
                    workspace_id=source.workspace_id,
                    query=build_query(source),
                )
                for source in chunk
                if source.workspace_id
            ]
            if not queries:
                continue
            results.extend(
                await execute_source_batch(
                    queries,
                    client=self._logs_client,
                    source_timeout_seconds=self._source_timeout_seconds,
                    request_deadline_seconds=self._request_deadline_seconds,
                    clock=self._clock,
                )
            )
        return results

    async def query(
        self,
        sources: Sequence[TelemetrySource],
        filters: ObserveFilterState,
        *,
        view: View,
    ) -> list[SourceResult]:
        builder = _VIEW_QUERY_BUILDERS[view]
        return await self._run(
            sources, lambda source: builder(filters, scope_source=source)
        )

    async def query_agent_detail(
        self,
        sources: Sequence[TelemetrySource],
        filters: ObserveFilterState,
        *,
        agent_key: str,
    ) -> list[SourceResult]:
        """Bounded single-agent trend query batch (T053 facade enrichment)."""
        return await self._run(
            sources,
            lambda source: build_agent_detail_query(
                filters, agent_key=agent_key, scope_source=source
            ),
        )

    async def aclose(self) -> None:
        await self._logs_client.aclose()
