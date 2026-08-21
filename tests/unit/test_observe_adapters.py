"""Tests for the lazy Azure SDK adapters wiring discovery.py/queries.py to
real ``azure-mgmt-resourcegraph``/``azure-mgmt-applicationinsights``/
``azure-monitor-query``/``azure-ai-projects`` clients.

Every test fakes the *real* SDK's response shapes (rather than the
already-tested duck types in ``discovery.py``/``queries.py``) via
``sys.modules`` injection, matching the pattern already used in
``test_observe_auth.py``, so the translation logic in ``adapters.py`` is
what is actually exercised.
"""

from __future__ import annotations

import builtins
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agentops.agent.observe.adapters import (
    AzureDiscoveryClient,
    AzureQueryClient,
    _ApplicationInsightsAdapter,
    _default_project_connections,
    _flatten_logs_query_response,
    _flatten_logs_table,
    _project_endpoint_from_arm_id,
    _ResourceGraphAdapter,
)
from agentops.agent.observe.queries import MAX_SOURCES_PER_BATCH, SourceResult
from agentops.core.observe import ObserveFilterState, ObserveScope, TelemetrySource

_PROJECT_ARM_ID = (
    "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg1"
    "/providers/Microsoft.CognitiveServices/accounts/acct1/projects/proj1"
)


# ---------------------------------------------------------------------------
# _project_endpoint_from_arm_id (pure function)
# ---------------------------------------------------------------------------


def test_project_endpoint_from_arm_id_builds_foundry_endpoint() -> None:
    assert (
        _project_endpoint_from_arm_id(_PROJECT_ARM_ID)
        == "https://acct1.services.ai.azure.com/api/projects/proj1"
    )


@pytest.mark.parametrize(
    "resource_id",
    [
        "/subscriptions/sub1/resourceGroups/rg1/providers/Microsoft.CognitiveServices/accounts/acct1",
        "/subscriptions/sub1/resourceGroups/rg1/providers/Microsoft.Storage/storageAccounts/acct1",
        "not-an-arm-id",
        "",
    ],
)
def test_project_endpoint_from_arm_id_returns_none_for_non_project_ids(resource_id: str) -> None:
    assert _project_endpoint_from_arm_id(resource_id) is None


# ---------------------------------------------------------------------------
# _ResourceGraphAdapter: translates the real QueryRequest-based API into
# discovery.py's ``.resources(query=, subscriptions=)`` duck type.
# ---------------------------------------------------------------------------


def test_resource_graph_adapter_translates_to_query_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeQueryRequest:
        def __init__(self, *, subscriptions, query, options) -> None:
            captured["subscriptions"] = subscriptions
            captured["query"] = query
            captured["options"] = options

    class _FakeQueryRequestOptions:
        def __init__(self, *, top) -> None:
            self.top = top

    class _FakeResourceGraphClient:
        def __init__(self, credential) -> None:
            captured["credential"] = credential

        def resources(self, request) -> SimpleNamespace:
            captured["request"] = request
            return SimpleNamespace(data=[{"id": "resource-1"}])

    fake_package = SimpleNamespace(ResourceGraphClient=_FakeResourceGraphClient)
    fake_models = SimpleNamespace(
        QueryRequest=_FakeQueryRequest, QueryRequestOptions=_FakeQueryRequestOptions
    )
    monkeypatch.setitem(sys.modules, "azure.mgmt.resourcegraph", fake_package)
    monkeypatch.setitem(sys.modules, "azure.mgmt.resourcegraph.models", fake_models)

    adapter = _ResourceGraphAdapter(credential="fake-credential")
    response = adapter.resources(query="Resources | take 1", subscriptions=["sub1", "sub2"])

    assert captured["credential"] == "fake-credential"
    assert captured["subscriptions"] == ["sub1", "sub2"]
    assert captured["query"] == "Resources | take 1"
    assert response.data == [{"id": "resource-1"}]

    # The underlying client is memoized, not rebuilt per call.
    adapter.resources(query="Resources | take 1", subscriptions=["sub1"])
    assert captured["credential"] == "fake-credential"


def test_resource_graph_adapter_does_not_import_azure_eagerly(monkeypatch: pytest.MonkeyPatch) -> None:
    # Reloading the module would otherwise leave later ``monkeypatch.setattr``
    # calls (which resolve dotted string targets via ``getattr`` on the parent
    # *package* before falling back to ``sys.modules``) pointed at a different
    # module object than the one this test file's top-level
    # ``from ... import ...`` already bound its classes against -- restore
    # both ``sys.modules`` and the parent package's ``adapters`` attribute
    # once the guarded re-import has been checked.
    import agentops.agent.observe as observe_package

    original_module = sys.modules.get("agentops.agent.observe.adapters")
    original_attr = getattr(observe_package, "adapters", None)
    sys.modules.pop("agentops.agent.observe.adapters", None)
    original_import = builtins.__import__

    def guard(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("azure."):
            raise AssertionError(f"{name} must not be imported at module import time")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard)

    try:
        import agentops.agent.observe.adapters  # noqa: F401 -- re-import under the guard
    finally:
        if original_module is not None:
            sys.modules["agentops.agent.observe.adapters"] = original_module
        if original_attr is not None:
            observe_package.adapters = original_attr


# ---------------------------------------------------------------------------
# _ApplicationInsightsAdapter: binds one subscription, exposes ``.components``.
# ---------------------------------------------------------------------------


def test_application_insights_adapter_exposes_components(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeComponents:
        def get(self, *, resource_group_name, resource_name) -> SimpleNamespace:
            captured["resource_group_name"] = resource_group_name
            captured["resource_name"] = resource_name
            return SimpleNamespace(workspace_resource_id="/subscriptions/s/workspace")

    class _FakeManagementClient:
        def __init__(self, credential, subscription_id) -> None:
            captured["credential"] = credential
            captured["subscription_id"] = subscription_id
            self.components = _FakeComponents()

    fake_package = SimpleNamespace(ApplicationInsightsManagementClient=_FakeManagementClient)
    monkeypatch.setitem(sys.modules, "azure.mgmt.applicationinsights", fake_package)

    adapter = _ApplicationInsightsAdapter(credential="fake-credential", subscription_id="sub1")
    component = adapter.components.get(resource_group_name="rg1", resource_name="appinsights1")

    assert captured["credential"] == "fake-credential"
    assert captured["subscription_id"] == "sub1"
    assert captured["resource_group_name"] == "rg1"
    assert captured["resource_name"] == "appinsights1"
    assert component.workspace_resource_id == "/subscriptions/s/workspace"


# ---------------------------------------------------------------------------
# _default_project_connections: AIProjectClient(endpoint=, credential=).connections
# ---------------------------------------------------------------------------


def test_default_project_connections_builds_client_from_project_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeConnections:
        def list(self):
            return iter([SimpleNamespace(type="AppInsights", target="target-1")])

    class _FakeAIProjectClient:
        def __init__(self, *, endpoint, credential) -> None:
            captured["endpoint"] = endpoint
            captured["credential"] = credential
            self.connections = _FakeConnections()

    fake_package = SimpleNamespace(AIProjectClient=_FakeAIProjectClient)
    monkeypatch.setitem(sys.modules, "azure.ai.projects", fake_package)

    connections = _default_project_connections(_PROJECT_ARM_ID, credential="fake-credential")

    assert captured["endpoint"] == "https://acct1.services.ai.azure.com/api/projects/proj1"
    assert captured["credential"] == "fake-credential"
    assert [c.target for c in connections.list()] == ["target-1"]


def test_default_project_connections_returns_none_for_unparseable_project_id() -> None:
    assert _default_project_connections("not-an-arm-id", credential="fake-credential") is None


# ---------------------------------------------------------------------------
# Logs table/response flattening (T044 adapter correctness).
# ---------------------------------------------------------------------------


def test_flatten_logs_table_zips_columns_and_rows() -> None:
    table = SimpleNamespace(
        columns=[SimpleNamespace(name="agent_key"), SimpleNamespace(name="invocations")],
        rows=[["agent-1", 3], ["agent-2", 5]],
    )

    rows = _flatten_logs_table(table)

    assert rows == [
        {"agent_key": "agent-1", "invocations": 3},
        {"agent_key": "agent-2", "invocations": 5},
    ]


def test_flatten_logs_table_accepts_plain_string_columns() -> None:
    table = SimpleNamespace(columns=["a", "b"], rows=[[1, 2]])
    assert _flatten_logs_table(table) == [{"a": 1, "b": 2}]


def test_flatten_logs_query_response_success_has_no_error() -> None:
    response = SimpleNamespace(
        tables=[SimpleNamespace(columns=["a"], rows=[[1]])],
        status="SUCCESS",
    )

    item = _flatten_logs_query_response(response)

    assert item.error is None
    assert item.partial_error is None
    assert item.tables == [{"a": 1}]
    assert item.status == "SUCCESS"


def test_flatten_logs_query_response_error_short_circuits_before_tables() -> None:
    response = SimpleNamespace(error=SimpleNamespace(code="Throttled"), tables=None)

    item = _flatten_logs_query_response(response)

    assert item.error.code == "Throttled"
    assert item.tables is None


def test_flatten_logs_query_response_partial_flattens_partial_data() -> None:
    partial_table = SimpleNamespace(columns=["a"], rows=[[1]])
    response = SimpleNamespace(
        error=None,
        partial_error=SimpleNamespace(code="PartialError"),
        partial_data=[partial_table],
        tables=None,
    )

    item = _flatten_logs_query_response(response)

    assert item.partial_error.code == "PartialError"
    assert item.partial_data == [{"a": 1}]
    assert item.tables == [{"a": 1}]


# ---------------------------------------------------------------------------
# AzureQueryClient: chunking into groups of MAX_SOURCES_PER_BATCH and
# forwarding to execute_source_batch via the internal logs-query adapter.
# ---------------------------------------------------------------------------


class _FakeLogsClient:
    """Fake ``client.query_batch(requests)`` used to test ``AzureQueryClient``
    chunking logic without touching real ``azure.monitor.query``."""

    def __init__(self) -> None:
        self.batches: list[list[object]] = []

    async def query_batch(self, requests):
        self.batches.append(list(requests))
        return [
            SimpleNamespace(error=None, partial_error=None, tables=[], status="SUCCESS")
            for _ in requests
        ]


def _make_source(source_id: str, workspace_id: str | None = "workspace-1") -> TelemetrySource:
    return TelemetrySource(
        source_id=source_id,
        resource_id=f"/subscriptions/s/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/a/projects/{source_id}",
        workspace_id=workspace_id,
        state="available",
    )


def _make_filters() -> ObserveFilterState:
    end = datetime(2024, 1, 2, tzinfo=timezone.utc)
    start = end - timedelta(days=1)
    return ObserveFilterState(start=start, end=end)


@pytest.mark.asyncio
async def test_azure_query_client_chunks_more_than_ten_sources() -> None:
    client = AzureQueryClient(credential="fake-credential")
    fake_logs_client = _FakeLogsClient()
    client._logs_client = fake_logs_client  # white-box: swap the lazy Azure adapter for a fake

    sources = [_make_source(f"source-{i}") for i in range(MAX_SOURCES_PER_BATCH + 3)]
    results = await client.query(sources, _make_filters(), view="overview")

    assert len(fake_logs_client.batches) == 2
    assert len(fake_logs_client.batches[0]) == MAX_SOURCES_PER_BATCH
    assert len(fake_logs_client.batches[1]) == 3
    assert len(results) == MAX_SOURCES_PER_BATCH + 3
    assert all(isinstance(result, SourceResult) for result in results)
    assert all(result.status == "success" for result in results)


@pytest.mark.asyncio
async def test_azure_query_client_skips_sources_without_workspace_id() -> None:
    client = AzureQueryClient(credential="fake-credential")
    fake_logs_client = _FakeLogsClient()
    client._logs_client = fake_logs_client

    sources = [_make_source("has-workspace"), _make_source("no-workspace", workspace_id=None)]
    results = await client.query(sources, _make_filters(), view="agents")

    assert len(fake_logs_client.batches) == 1
    assert len(fake_logs_client.batches[0]) == 1
    assert len(results) == 1


@pytest.mark.asyncio
async def test_azure_query_client_query_agent_detail_uses_bounded_trend_query() -> None:
    client = AzureQueryClient(credential="fake-credential")
    fake_logs_client = _FakeLogsClient()
    client._logs_client = fake_logs_client

    sources = [_make_source("source-1")]
    await client.query_agent_detail(sources, _make_filters(), agent_key="agent-1")

    assert len(fake_logs_client.batches) == 1
    (request,) = fake_logs_client.batches[0]
    assert "agent_key == 'agent-1'" in request.query
    assert "p95_latency_ms" in request.query


@pytest.mark.asyncio
async def test_azure_query_client_always_applies_source_project_boundary() -> None:
    client = AzureQueryClient(credential="fake-credential")
    fake_logs_client = _FakeLogsClient()
    client._logs_client = fake_logs_client
    source = _make_source("source-1").model_copy(
        update={"project_resource_ids": [_PROJECT_ARM_ID]}
    )

    await client.query([source], _make_filters(), view="overview")

    (request,) = fake_logs_client.batches[0]
    assert "Properties" in request.query
    assert _PROJECT_ARM_ID.lower() in request.query.lower()
    assert "DurationMs" in request.query


@pytest.mark.asyncio
async def test_azure_query_client_returns_empty_list_without_sources() -> None:
    client = AzureQueryClient(credential="fake-credential")
    client._logs_client = _FakeLogsClient()

    results = await client.query([], _make_filters(), view="overview")

    assert results == []


# ---------------------------------------------------------------------------
# AzureDiscoveryClient: wraps the synchronous discovery.py entry point in a
# worker thread and binds Application Insights to the first subscription.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_azure_discovery_client_delegates_to_build_resource_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel_inventory = SimpleNamespace(telemetry_sources=[])

    def fake_build_resource_inventory(
        scope,
        *,
        resource_graph_client,
        connections_by_project,
        application_insights_client,
        clock,
    ):
        captured["scope"] = scope
        captured["resource_graph_client"] = resource_graph_client
        captured["connections_by_project"] = connections_by_project
        captured["application_insights_client"] = application_insights_client
        captured["clock"] = clock
        return sentinel_inventory

    monkeypatch.setattr(
        "agentops.agent.observe.adapters.build_resource_inventory",
        fake_build_resource_inventory,
    )

    scope = ObserveScope(mode="projects", project_resource_ids=[_PROJECT_ARM_ID])
    def fixed_clock() -> datetime:
        return datetime(2024, 1, 1, tzinfo=timezone.utc)

    client = AzureDiscoveryClient(credential="fake-credential", clock=fixed_clock)

    result = await client.discover(scope)

    assert result is sentinel_inventory
    assert captured["scope"] is scope
    assert captured["clock"] is fixed_clock
    assert isinstance(captured["resource_graph_client"], _ResourceGraphAdapter)
    assert isinstance(captured["application_insights_client"], _ApplicationInsightsAdapter)
    assert callable(captured["connections_by_project"])


@pytest.mark.asyncio
async def test_azure_discovery_client_binds_no_app_insights_client_without_subscriptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_build_resource_inventory(scope, *, application_insights_client, **kwargs):
        captured["application_insights_client"] = application_insights_client
        return SimpleNamespace(telemetry_sources=[])

    monkeypatch.setattr(
        "agentops.agent.observe.adapters.build_resource_inventory",
        fake_build_resource_inventory,
    )
    monkeypatch.setattr(
        "agentops.agent.observe.adapters.subscription_ids_for_scope",
        lambda scope: (),
    )

    scope = ObserveScope(mode="projects", project_resource_ids=[_PROJECT_ARM_ID])
    client = AzureDiscoveryClient(credential="fake-credential")

    await client.discover(scope)

    assert captured["application_insights_client"] is None


@pytest.mark.asyncio
async def test_azure_discovery_client_runs_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery must not block the event loop -- verified by observing it
    execute on a different thread than the calling coroutine."""
    calling_thread: dict[str, object] = {}

    import threading

    def fake_build_resource_inventory(scope, **kwargs):
        calling_thread["thread_id"] = threading.get_ident()
        return SimpleNamespace(telemetry_sources=[])

    monkeypatch.setattr(
        "agentops.agent.observe.adapters.build_resource_inventory",
        fake_build_resource_inventory,
    )

    scope = ObserveScope(mode="projects", project_resource_ids=[_PROJECT_ARM_ID])
    client = AzureDiscoveryClient(credential="fake-credential")

    main_thread_id = threading.get_ident()
    await client.discover(scope)

    assert calling_thread["thread_id"] != main_thread_id
