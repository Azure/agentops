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
from typing import Literal
from uuid import UUID

import pytest

from agentops.agent.observe.adapters import (
    AggregateDepartmentUsageRow,
    AzureDiscoveryClient,
    AzureQueryClient,
    _VIEW_QUERY_BUILDERS,
    _ApplicationInsightsAdapter,
    _LogsQueryAdapter,
    _default_project_connections,
    _flatten_logs_query_response,
    _flatten_logs_rest_payload,
    _flatten_logs_table,
    _project_endpoint_from_arm_id,
    _ResourceGraphAdapter,
    normalize_user_attribution_coverage,
    normalize_user_usage_row,
    normalize_department_usage_row,
)
from agentops.agent.observe.queries import MAX_SOURCES_PER_BATCH, SourceResult
from agentops.core.attribution import (
    AttributionConfiguration,
    derive_pseudonymous_user_key,
)
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
def test_project_endpoint_from_arm_id_returns_none_for_non_project_ids(
    resource_id: str,
) -> None:
    assert _project_endpoint_from_arm_id(resource_id) is None


# ---------------------------------------------------------------------------
# _ResourceGraphAdapter: translates the real QueryRequest-based API into
# discovery.py's ``.resources(query=, subscriptions=)`` duck type.
# ---------------------------------------------------------------------------


def test_resource_graph_adapter_translates_to_query_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    response = adapter.resources(
        query="Resources | take 1", subscriptions=["sub1", "sub2"]
    )

    assert captured["credential"] == "fake-credential"
    assert captured["subscriptions"] == ["sub1", "sub2"]
    assert captured["query"] == "Resources | take 1"
    assert response.data == [{"id": "resource-1"}]

    # The underlying client is memoized, not rebuilt per call.
    adapter.resources(query="Resources | take 1", subscriptions=["sub1"])
    assert captured["credential"] == "fake-credential"


def test_resource_graph_adapter_does_not_import_azure_eagerly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_application_insights_adapter_exposes_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    fake_package = SimpleNamespace(
        ApplicationInsightsManagementClient=_FakeManagementClient
    )
    monkeypatch.setitem(sys.modules, "azure.mgmt.applicationinsights", fake_package)

    adapter = _ApplicationInsightsAdapter(
        credential="fake-credential", subscription_id="sub1"
    )
    component = adapter.components.get(
        resource_group_name="rg1", resource_name="appinsights1"
    )

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

    connections = _default_project_connections(
        _PROJECT_ARM_ID, credential="fake-credential"
    )

    assert (
        captured["endpoint"] == "https://acct1.services.ai.azure.com/api/projects/proj1"
    )
    assert captured["credential"] == "fake-credential"
    assert [c.target for c in connections.list()] == ["target-1"]


def test_default_project_connections_returns_none_for_unparseable_project_id() -> None:
    assert (
        _default_project_connections("not-an-arm-id", credential="fake-credential")
        is None
    )


# ---------------------------------------------------------------------------
# Logs table/response flattening (T044 adapter correctness).
# ---------------------------------------------------------------------------


def test_flatten_logs_table_zips_columns_and_rows() -> None:
    table = SimpleNamespace(
        columns=[
            SimpleNamespace(name="agent_key"),
            SimpleNamespace(name="invocations"),
        ],
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


def test_flatten_logs_table_preserves_total_in_scope_without_fabricating_it() -> None:
    totals = SimpleNamespace(
        columns=["agent_key", "total_in_scope"], rows=[["agent-1", 12]]
    )
    no_totals = SimpleNamespace(columns=["agent_key"], rows=[["agent-2"]])

    assert _flatten_logs_table(totals) == [
        {"agent_key": "agent-1", "total_in_scope": 12}
    ]
    assert _flatten_logs_table(no_totals) == [{"agent_key": "agent-2"}]


def test_normalize_department_usage_row_preserves_source_and_nullable_usage() -> None:
    source = _make_source("source-1")
    row = normalize_department_usage_row(
        {
            "department_id": "engineering",
            "department_label": "Engineering",
            "mapping_state": "mapped",
            "member_count": 2,
            "invocations": 4,
            "input_tokens": None,
            "output_tokens": 0,
            "tool_invocations": None,
            "active_session_seconds": None,
            "eligible_records": 4,
            "identified_records": 4,
            "mapped_records": 4,
            "unattributed_records": 0,
            "ambiguous_records": 0,
            "returned_records": 1,
            "project_resource_id": _PROJECT_ARM_ID,
            "agent_key": "agent-1",
            "deployment": "gpt-prod",
            "provider_name": "Microsoft.Extensions.AI",
        },
        source=source,
    )

    assert isinstance(row, AggregateDepartmentUsageRow)
    assert row.source_id == "source-1"
    assert row.source_resource_id == source.resource_id
    assert row.department_id == "engineering"
    assert row.member_count == 2
    assert row.usage.invocations == 4
    assert row.usage.input_tokens is None
    assert row.usage.output_tokens == 0
    assert row.usage.tool_invocations is None
    assert row.usage.active_session_seconds is None
    assert row.project_resource_id == _PROJECT_ARM_ID
    assert row.agent_key == "agent-1"
    assert row.deployment == "gpt-prod"
    assert row.provider_name == "Microsoft.Extensions.AI"


def test_normalize_user_usage_row_requires_delegation_and_matching_key() -> None:
    config = AttributionConfiguration.model_validate(
        {
            "version": 1,
            "enabled": True,
            "deployment_namespace": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "generation": 3,
            "departments": [],
        }
    )
    tenant = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    identity = "Alice@Example.test"
    key = derive_pseudonymous_user_key(
        deployment_namespace=config.deployment_namespace,
        generation=config.generation,
        tenant_id=tenant,
        raw_identity=identity,
    )
    raw = {
        "row_kind": "user",
        "user_key": key,
        "raw_identity": f" {identity} ",
        "user_rank": 1,
        "invocations": 2,
        "deployment": "gpt-prod",
        "model": "gpt-4o",
    }

    with pytest.raises(ValueError, match="delegated"):
        normalize_user_usage_row(
            raw,
            source=_make_source("source-1"),
            config=config,
            tenant_id=tenant,
            access_boundary="aggregate",
        )
    normalized = normalize_user_usage_row(
        raw,
        source=_make_source("source-1"),
        config=config,
        tenant_id=tenant,
        access_boundary="delegated",
    )
    assert normalized.raw_identity == identity
    assert normalized.user_key == key
    assert normalized.deployment == "gpt-prod"
    assert normalized.model == "gpt-4o"

    with pytest.raises(ValueError, match="does not match"):
        normalize_user_usage_row(
            {**raw, "user_key": f"usr1.g3.{'f' * 64}"},
            source=_make_source("source-1"),
            config=config,
            tenant_id=tenant,
            access_boundary="delegated",
        )


def test_other_users_and_unattributed_rows_forbid_raw_identity() -> None:
    config = AttributionConfiguration.model_validate(
        {
            "version": 1,
            "enabled": True,
            "deployment_namespace": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "generation": 3,
            "departments": [],
        }
    )
    with pytest.raises(ValueError, match="must not contain identity"):
        normalize_user_usage_row(
            {
                "row_kind": "other_users",
                "raw_identity": "leak@example.test",
                "distinct_users": 2,
                "invocations": 2,
            },
            source=_make_source("source-1"),
            config=config,
            tenant_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            access_boundary="delegated",
        )


def test_normalize_user_attribution_coverage_preserves_all_reported_counters() -> None:
    refreshed_at = datetime(2024, 1, 2, tzinfo=timezone.utc)
    coverage = normalize_user_attribution_coverage(
        source=_make_source("source-1"),
        status="success",
        rows=[
            {
                "eligible_records": 10,
                "identified_records": 8,
                "mapped_records": 6,
                "unattributed_records": 4,
                "ambiguous_records": 2,
                "returned_records": 3,
            }
        ],
        metric="cost",
        attribution_level="department",
        component_id="model.ptu",
        refreshed_at=refreshed_at,
    )

    assert coverage.model_dump() == {
        "source_id": "source-1",
        "dimension": "user_attribution",
        "state": "partial",
        "reason": "Only part of this source could be attributed to a department.",
        "next_action": (
            "Add missing explicit mappings and verify authenticated identity telemetry."
        ),
        "refreshed_at": refreshed_at,
        "component_id": "model.ptu",
        "cost_breakdown": None,
        "allocation_key": None,
        "metric": "cost",
        "attribution_level": "department",
        "eligible_records": 10,
        "identified_records": 8,
        "mapped_records": 6,
        "unattributed_records": 4,
        "ambiguous_records": 2,
        "returned_records": 3,
    }


def test_normalize_user_attribution_coverage_uses_null_counts_when_source_inaccessible() -> (
    None
):
    coverage = normalize_user_attribution_coverage(
        source=_make_source("source-1").model_copy(
            update={"state": "inaccessible", "reason": "denied for alice@example.test"}
        ),
        status=None,
        rows=None,
        metric="usage",
        attribution_level="user",
        refreshed_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )

    assert coverage.state == "inaccessible"
    assert coverage.reason == "This telemetry source could not be read."
    assert coverage.component_id is None
    assert {
        coverage.eligible_records,
        coverage.identified_records,
        coverage.mapped_records,
        coverage.unattributed_records,
        coverage.ambiguous_records,
        coverage.returned_records,
    } == {None}
    assert "alice" not in repr(coverage)


def test_normalize_user_attribution_coverage_does_not_invent_missing_zero_counters() -> (
    None
):
    coverage = normalize_user_attribution_coverage(
        source=_make_source("source-1"),
        status="success",
        rows=[],
        metric="usage",
        attribution_level="department",
        refreshed_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )

    assert coverage.state == "error"
    assert coverage.eligible_records is None
    assert "did not return attribution counters" in coverage.reason


def test_normalize_user_attribution_coverage_rejects_partial_or_inconsistent_counters() -> (
    None
):
    arguments = {
        "source": _make_source("source-1"),
        "status": "success",
        "metric": "usage",
        "attribution_level": "department",
        "refreshed_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
    }
    with pytest.raises(ValueError, match="all six"):
        normalize_user_attribution_coverage(
            rows=[{"eligible_records": 1, "identified_records": 1}],
            **arguments,
        )
    with pytest.raises(ValueError, match="consistent"):
        normalize_user_attribution_coverage(
            rows=[
                {
                    "eligible_records": 1,
                    "identified_records": 1,
                    "mapped_records": 1,
                    "unattributed_records": 0,
                    "ambiguous_records": 0,
                    "returned_records": 1,
                },
                {
                    "eligible_records": 2,
                    "identified_records": 1,
                    "mapped_records": 1,
                    "unattributed_records": 1,
                    "ambiguous_records": 0,
                    "returned_records": 1,
                },
            ],
            **arguments,
        )


@pytest.mark.parametrize(
    "sensitive_field",
    [
        "raw_identity",
        "UserAuthenticatedId",
        "authenticated_id",
        "otel_enduser_id",
        "user_key",
    ],
)
def test_normalize_department_usage_row_rejects_identity_fields(
    sensitive_field: str,
) -> None:
    raw = {
        "department_id": "engineering",
        "department_label": "Engineering",
        "mapping_state": "mapped",
        "member_count": 2,
        "invocations": 1,
    }
    raw[sensitive_field] = None

    with pytest.raises(ValueError, match="privacy"):
        normalize_department_usage_row(raw, source=_make_source("source-1"))


def test_normalize_department_usage_row_rejects_invalid_department_shape() -> None:
    with pytest.raises(ValueError, match="department"):
        normalize_department_usage_row(
            {
                "department_id": "engineering",
                "department_label": None,
                "mapping_state": "mapped",
                "member_count": 2,
                "invocations": 1,
            },
            source=_make_source("source-1"),
        )


def test_view_query_builders_register_tools_and_runs() -> None:
    assert _VIEW_QUERY_BUILDERS["tools"].__name__ == "build_tools_query"
    assert _VIEW_QUERY_BUILDERS["runs"].__name__ == "build_runs_query"


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


def test_flatten_logs_rest_payload_converts_datetime_columns() -> None:
    item = _flatten_logs_rest_payload(
        {
            "tables": [
                {
                    "columns": [
                        {"name": "last_seen", "type": "datetime"},
                        {"name": "invocations", "type": "long"},
                    ],
                    "rows": [["2026-04-01T12:30:45.1234567Z", 3]],
                }
            ]
        }
    )

    assert item.tables == [
        {
            "last_seen": datetime(2026, 4, 1, 12, 30, 45, 123456, tzinfo=timezone.utc),
            "invocations": 3,
        }
    ]


@pytest.mark.asyncio
async def test_logs_query_adapter_falls_back_when_batch_response_cannot_be_decoded(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeLogsBatchQuery:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeCredential:
        async def get_token(self, scope):
            assert scope == "https://api.loganalytics.io/.default"
            return SimpleNamespace(token="access-token")

    class FakeClient:
        async def query_batch(self, batch, *, headers):
            assert headers == {"Accept-Encoding": "identity"}
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")

    rest_calls: list[dict[str, object]] = []

    def fake_rest(**kwargs):
        rest_calls.append(kwargs)
        return SimpleNamespace(
            error=None,
            partial_error=None,
            tables=[{"value": "ok"}],
            status="SUCCESS",
        )

    monkeypatch.setattr(
        "agentops.agent.observe.adapters._query_workspace_rest", fake_rest
    )
    monkeypatch.setitem(
        sys.modules,
        "azure.monitor.query",
        SimpleNamespace(LogsBatchQuery=FakeLogsBatchQuery),
    )
    adapter = _LogsQueryAdapter(credential=FakeCredential())
    adapter._client = FakeClient()
    request = SimpleNamespace(
        workspace_id="workspace-1",
        query="AppDependencies | take 1",
        timespan=timedelta(days=1),
        server_timeout_seconds=45,
    )

    result = await adapter.query_batch([request])

    assert result[0].tables == [{"value": "ok"}]
    assert rest_calls == [
        {
            "workspace_id": "workspace-1",
            "query": "AppDependencies | take 1",
            "access_token": "access-token",
            "timeout_seconds": 45,
        }
    ]
    assert "through the Logs REST endpoint" in caplog.text


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


def _make_source(
    source_id: str, workspace_id: str | None = "workspace-1"
) -> TelemetrySource:
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
    client._logs_client = (
        fake_logs_client  # white-box: swap the lazy Azure adapter for a fake
    )

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

    sources = [
        _make_source("has-workspace"),
        _make_source("no-workspace", workspace_id=None),
    ]
    results = await client.query(sources, _make_filters(), view="agents")

    assert len(fake_logs_client.batches) == 1
    assert len(fake_logs_client.batches[0]) == 1
    assert len(results) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("view", "expected_query_fragment"),
    [("tools", "gen_ai.tool.name"), ("runs", "run_key_kind")],
)
async def test_azure_query_client_dispatches_tools_and_runs_to_registered_builders(
    view: Literal["tools", "runs"], expected_query_fragment: str
) -> None:
    client = AzureQueryClient(credential="fake-credential")
    fake_logs_client = _FakeLogsClient()
    client._logs_client = fake_logs_client

    await client.query([_make_source("source-1")], _make_filters(), view=view)

    (request,) = fake_logs_client.batches[0]
    assert expected_query_fragment in request.query
    assert "total_in_scope" in request.query


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


@pytest.mark.asyncio
async def test_azure_query_client_normalizes_department_rows_at_boundary() -> None:
    client = AzureQueryClient(credential="fake-credential")
    fake_logs_client = _FakeLogsClient()
    client._logs_client = fake_logs_client

    async def query_batch(requests):
        fake_logs_client.batches.append(list(requests))
        return [
            SimpleNamespace(
                error=None,
                partial_error=None,
                status="SUCCESS",
                tables=[
                    {
                        "department_id": "engineering",
                        "department_label": "Engineering",
                        "mapping_state": "mapped",
                        "member_count": 2,
                        "invocations": 3,
                        "input_tokens": None,
                        "output_tokens": None,
                        "tool_invocations": None,
                        "active_session_seconds": None,
                    }
                ],
            )
        ]

    fake_logs_client.query_batch = query_batch  # type: ignore[method-assign]
    config = AttributionConfiguration.model_validate(
        {
            "version": 1,
            "enabled": True,
            "deployment_namespace": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "generation": 1,
            "departments": [],
        }
    )

    results = await client.query_department_usage(
        [_make_source("source-1")],
        _make_filters(),
        config=config,
        tenant_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    )

    assert isinstance(results[0].tables[0], AggregateDepartmentUsageRow)
    assert results[0].tables[0].source_id == "source-1"
    assert "UserAuthenticatedId" in fake_logs_client.batches[0][0].query


@pytest.mark.asyncio
async def test_azure_query_client_rejects_raw_identity_at_aggregate_boundary() -> None:
    client = AzureQueryClient(credential="fake-credential")
    fake_logs_client = _FakeLogsClient()
    client._logs_client = fake_logs_client

    async def query_batch(requests):
        return [
            SimpleNamespace(
                error=None,
                partial_error=None,
                status="SUCCESS",
                tables=[
                    {
                        "mapping_state": "unmapped",
                        "member_count": 0,
                        "invocations": 1,
                        "Properties": {"enduser.id": "private@example.test"},
                    }
                ],
            )
        ]

    fake_logs_client.query_batch = query_batch  # type: ignore[method-assign]
    config = AttributionConfiguration.model_validate(
        {
            "version": 1,
            "enabled": True,
            "deployment_namespace": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "generation": 1,
            "departments": [],
        }
    )

    with pytest.raises(ValueError, match="privacy"):
        await client.query_department_usage(
            [_make_source("source-1")],
            _make_filters(),
            config=config,
            tenant_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        )


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
    assert isinstance(
        captured["application_insights_client"], _ApplicationInsightsAdapter
    )
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
async def test_azure_discovery_client_runs_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
