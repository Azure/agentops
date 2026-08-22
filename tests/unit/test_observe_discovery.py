"""Tests for credential-free Observe resource discovery."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agentops.agent.observe.discovery import (
    DISCOVERY_CACHE_TTL_SECONDS,
    build_resource_inventory,
    build_telemetry_sources,
    dedupe_telemetry_sources,
    discover_appinsights_resource_ids,
    discover_scoped_foundry_resources,
    resolve_log_analytics_workspace_resource_id,
    subscription_ids_for_scope,
)
from agentops.core.observe import ObserveScope, TelemetrySource


def test_discovers_appinsights_ids_without_requesting_credentials() -> None:
    resource_id = (
        "/subscriptions/sub/resourceGroups/rg/providers/"
        "Microsoft.Insights/components/appi"
    )
    connections = SimpleNamespace(
        list=lambda: [
            SimpleNamespace(
                type="application_insights",
                target=resource_id,
            )
        ]
    )

    assert discover_appinsights_resource_ids(connections) == (resource_id.lower(),)


def test_discovery_ignores_non_appinsights_connections() -> None:
    connections = SimpleNamespace(
        list=lambda: [
            SimpleNamespace(
                type="azure_open_ai",
                target="/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.CognitiveServices/accounts/openai",
            )
        ]
    )

    assert discover_appinsights_resource_ids(connections) == ()


def test_resolves_workspace_from_appinsights_component() -> None:
    workspace_id = (
        "/subscriptions/sub/resourceGroups/rg/providers/"
        "Microsoft.OperationalInsights/workspaces/logs"
    )
    client = SimpleNamespace(
        components=SimpleNamespace(
            get=lambda resource_group_name, resource_name: SimpleNamespace(
                workspace_resource_id=workspace_id
            )
        )
    )

    actual = resolve_log_analytics_workspace_resource_id(
        "/subscriptions/sub/resourceGroups/rg/providers/"
        "Microsoft.Insights/components/appi",
        client,
    )

    assert actual == workspace_id.lower()


def test_workspace_resolution_rejects_non_appinsights_id() -> None:
    client = SimpleNamespace(components=SimpleNamespace())

    actual = resolve_log_analytics_workspace_resource_id(
        "/subscriptions/sub/resourceGroups/rg/providers/"
        "Microsoft.Storage/storageAccounts/data",
        client,
    )

    assert actual is None


def _foundry_scope() -> ObserveScope:
    return ObserveScope.model_validate(
        {
            "version": 1,
            "mode": "foundry",
            "root_resource_id": (
                "/subscriptions/00000000-0000-0000-0000-000000000001/"
                "resourceGroups/rg-agentops/providers/Microsoft.CognitiveServices/"
                "accounts/foundry"
            ),
        }
    )


class _FakeResourceGraphResponse:
    def __init__(self, data: list[dict[str, object]]) -> None:
        self.data = data


def test_projects_mode_scope_skips_resource_graph_entirely(
    observe_scope_project: ObserveScope,
) -> None:
    calls: list[str] = []

    class _ExplodingClient:
        def resources(self, *, query: str, subscriptions: list[str]) -> object:
            calls.append(query)
            raise AssertionError("Resource Graph must not be queried in projects mode")

    accounts, projects, partial_failures = discover_scoped_foundry_resources(
        observe_scope_project, resource_graph_client=_ExplodingClient()
    )

    assert calls == []
    assert accounts == []
    assert partial_failures == []
    assert projects == [{"id": observe_scope_project.project_resource_ids[0]}]


def test_foundry_scope_bounds_resource_graph_query_and_isolates_partial_failures() -> None:
    scope = _foundry_scope()
    project_id = f"{scope.root_resource_id}/projects/project-a"
    outside_id = (
        "/subscriptions/00000000-0000-0000-0000-000000000099/"
        "resourceGroups/other/providers/Microsoft.CognitiveServices/"
        "accounts/foundry/projects/outside"
    )

    class _PartiallyFailingClient:
        def resources(self, *, query: str, subscriptions: list[str]) -> object:
            assert subscriptions == ["00000000-0000-0000-0000-000000000001"]
            if "accounts/projects" in query:
                raise RuntimeError("403 Forbidden: AuthorizationFailed")
            assert scope.root_resource_id in query
            return _FakeResourceGraphResponse(
                [{"id": scope.root_resource_id}, {"id": outside_id}]
            )

    accounts, projects, partial_failures = discover_scoped_foundry_resources(
        scope, resource_graph_client=_PartiallyFailingClient()
    )

    # The project query failed but the account query still returned data --
    # one denied/slow query never blocks the other (T041).
    assert accounts == [{"id": scope.root_resource_id}]
    assert projects == []
    assert len(partial_failures) == 1
    assert partial_failures[0]["source"] == "resource_graph_projects"
    assert "not accessible" in partial_failures[0]["reason"]
    assert project_id  # sanity: fixture id constructed but unused by the failing query


def test_subscription_ids_for_scope_covers_projects_and_root_modes(
    observe_scope_expanded: ObserveScope,
) -> None:
    assert subscription_ids_for_scope(observe_scope_expanded) == (
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    )
    assert subscription_ids_for_scope(_foundry_scope()) == (
        "00000000-0000-0000-0000-000000000001",
    )


def test_build_telemetry_sources_dedupes_shared_workspace_and_preserves_attribution() -> None:
    workspace_id = (
        "/subscriptions/sub/resourceGroups/rg/providers/"
        "Microsoft.OperationalInsights/workspaces/shared-logs"
    )
    appinsights_id = (
        "/subscriptions/sub/resourceGroups/rg/providers/"
        "Microsoft.Insights/components/shared-appi"
    )
    project_a = (
        "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/"
        "accounts/foundry/projects/project-a"
    )
    project_b = (
        "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/"
        "accounts/foundry/projects/project-b"
    )

    def connections_for(project_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            list=lambda: [
                SimpleNamespace(type="application_insights", target=appinsights_id)
            ]
        )

    client = SimpleNamespace(
        components=SimpleNamespace(
            get=lambda resource_group_name, resource_name: SimpleNamespace(
                workspace_resource_id=workspace_id
            )
        )
    )

    sources = build_telemetry_sources(
        [project_a, project_b],
        connections_by_project=connections_for,
        application_insights_client=client,
    )

    assert len(sources) == 1
    merged = sources[0]
    assert merged.state == "available"
    assert merged.workspace_id == workspace_id.lower()
    assert merged.foundry_resource_id == (
        "/subscriptions/sub/resourcegroups/rg/providers/microsoft.cognitiveservices/"
        "accounts/foundry"
    )
    assert set(merged.project_resource_ids) == {project_a.lower(), project_b.lower()}


def test_dedupe_prefers_available_state_over_error_state() -> None:
    workspace_id = (
        "/subscriptions/sub/resourceGroups/rg/providers/"
        "Microsoft.OperationalInsights/workspaces/ws"
    )
    available = TelemetrySource(
        source_id=workspace_id,
        resource_id=workspace_id,
        workspace_id=workspace_id,
        project_resource_ids=["/subscriptions/sub/resourceGroups/rg/providers/x/a"],
        state="available",
    )
    erroring = TelemetrySource(
        source_id=workspace_id,
        resource_id=workspace_id,
        workspace_id=workspace_id,
        project_resource_ids=["/subscriptions/sub/resourceGroups/rg/providers/x/b"],
        state="error",
        reason="boom",
    )

    merged = dedupe_telemetry_sources([erroring, available])

    assert len(merged) == 1
    assert merged[0].state == "available"
    assert set(merged[0].project_resource_ids) == {
        "/subscriptions/sub/resourcegroups/rg/providers/x/a",
        "/subscriptions/sub/resourcegroups/rg/providers/x/b",
    }


def test_project_without_connections_is_not_configured() -> None:
    project_id = (
        "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/"
        "accounts/foundry/projects/no-connection"
    )

    sources = build_telemetry_sources(
        [project_id],
        connections_by_project={},
        application_insights_client=SimpleNamespace(),
    )

    assert len(sources) == 1
    assert sources[0].state == "not_configured"
    assert sources[0].reason


def test_connection_lookup_failure_yields_error_state_with_safe_reason() -> None:
    project_id = (
        "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/"
        "accounts/foundry/projects/broken"
    )

    def explode(_project_id: str) -> None:
        raise RuntimeError("boom: some internal detail")

    sources = build_telemetry_sources(
        [project_id],
        connections_by_project=explode,
        application_insights_client=SimpleNamespace(),
    )

    assert len(sources) == 1
    assert sources[0].state == "error"
    assert sources[0].reason is not None
    assert "boom" in sources[0].reason


def test_build_resource_inventory_sets_ttl_bounded_expiry_and_carries_partial_failures(
    observe_scope_project: ObserveScope,
) -> None:
    fixed_now = datetime(2024, 1, 1, tzinfo=timezone.utc)

    class _FailingResourceGraphClient:
        def resources(self, *, query: str, subscriptions: list[str]) -> object:
            raise AssertionError("projects-mode scope must not query Resource Graph")

    def failing_connections(_project_id: str) -> None:
        raise RuntimeError("connection lookup unavailable")

    inventory = build_resource_inventory(
        observe_scope_project,
        resource_graph_client=_FailingResourceGraphClient(),
        connections_by_project=failing_connections,
        application_insights_client=SimpleNamespace(),
        clock=lambda: fixed_now,
    )

    assert inventory.discovered_at == fixed_now
    assert (inventory.expires_at - inventory.discovered_at).total_seconds() == pytest.approx(
        DISCOVERY_CACHE_TTL_SECONDS
    )
    assert inventory.partial_failures == []
    assert len(inventory.telemetry_sources) == 1
    assert inventory.telemetry_sources[0].state == "error"
