"""Tests for :mod:`agentops.utils.foundry_discovery`."""

from __future__ import annotations

from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def _reset_discovery_cache():
    """Clear the per-process discovery cache before every test so
    success/failure results from a previous test never leak across."""
    from agentops.utils.foundry_discovery import reset_cache
    reset_cache()
    yield
    reset_cache()


def test_returns_none_when_endpoint_empty():
    from agentops.utils.foundry_discovery import resolve_appinsights_connection

    assert resolve_appinsights_connection("") is None


def test_returns_none_when_sdk_missing(monkeypatch):
    """Simulate azure-ai-projects not being installed."""
    import sys

    monkeypatch.setitem(sys.modules, "azure.ai.projects", None)
    monkeypatch.setitem(sys.modules, "azure.identity", None)

    from agentops.utils import foundry_discovery

    # The import inside the function will raise ImportError because
    # ``None`` is registered in sys.modules.
    assert foundry_discovery.resolve_appinsights_connection(
        "https://x.services.ai.azure.com/api/projects/p"
    ) is None


def test_returns_connection_string_from_telemetry_get_connection_string():
    """Happy path: AIProjectClient.telemetry.get_connection_string() works."""
    fake_telemetry = mock.MagicMock()
    fake_telemetry.get_connection_string.return_value = (
        "InstrumentationKey=abc-123;IngestionEndpoint=https://example.in"
    )

    fake_client = mock.MagicMock()
    fake_client.telemetry = fake_telemetry

    fake_projects_mod = mock.MagicMock()
    fake_projects_mod.AIProjectClient.return_value = fake_client
    fake_identity_mod = mock.MagicMock()

    with mock.patch.dict(
        "sys.modules",
        {
            "azure.ai.projects": fake_projects_mod,
            "azure.identity": fake_identity_mod,
        },
    ):
        from agentops.utils.foundry_discovery import resolve_appinsights_connection

        result = resolve_appinsights_connection(
            "https://contoso.services.ai.azure.com/api/projects/p"
        )

    assert result == "InstrumentationKey=abc-123;IngestionEndpoint=https://example.in"
    fake_projects_mod.AIProjectClient.assert_called_once()
    _, kwargs = fake_projects_mod.AIProjectClient.call_args
    assert kwargs["endpoint"].endswith("/api/projects/p")


def test_falls_through_aliases_when_primary_method_missing():
    """Older SDKs use get_application_insights_connection_string."""
    fake_telemetry = mock.MagicMock(spec=["get_application_insights_connection_string"])
    fake_telemetry.get_application_insights_connection_string.return_value = (
        "InstrumentationKey=xyz"
    )

    fake_client = mock.MagicMock()
    fake_client.telemetry = fake_telemetry

    fake_projects_mod = mock.MagicMock()
    fake_projects_mod.AIProjectClient.return_value = fake_client
    fake_identity_mod = mock.MagicMock()

    with mock.patch.dict(
        "sys.modules",
        {
            "azure.ai.projects": fake_projects_mod,
            "azure.identity": fake_identity_mod,
        },
    ):
        from agentops.utils.foundry_discovery import resolve_appinsights_connection

        result = resolve_appinsights_connection(
            "https://x.services.ai.azure.com/api/projects/p"
        )

    assert result == "InstrumentationKey=xyz"


def test_returns_none_when_no_telemetry_attribute_on_client():
    """Very old SDK without a .telemetry helper at all."""
    fake_client = mock.MagicMock(spec=[])  # no telemetry attribute

    fake_projects_mod = mock.MagicMock()
    fake_projects_mod.AIProjectClient.return_value = fake_client
    fake_identity_mod = mock.MagicMock()

    with mock.patch.dict(
        "sys.modules",
        {
            "azure.ai.projects": fake_projects_mod,
            "azure.identity": fake_identity_mod,
        },
    ):
        from agentops.utils.foundry_discovery import resolve_appinsights_connection

        result = resolve_appinsights_connection(
            "https://x.services.ai.azure.com/api/projects/p"
        )

    assert result is None


def test_swallows_runtime_errors_from_telemetry_call():
    """A 4xx/5xx from get_connection_string must not propagate."""
    fake_telemetry = mock.MagicMock()
    fake_telemetry.get_connection_string.side_effect = RuntimeError("403")

    fake_client = mock.MagicMock()
    fake_client.telemetry = fake_telemetry

    fake_projects_mod = mock.MagicMock()
    fake_projects_mod.AIProjectClient.return_value = fake_client
    fake_identity_mod = mock.MagicMock()

    with mock.patch.dict(
        "sys.modules",
        {
            "azure.ai.projects": fake_projects_mod,
            "azure.identity": fake_identity_mod,
        },
    ):
        from agentops.utils.foundry_discovery import resolve_appinsights_connection

        result = resolve_appinsights_connection(
            "https://x.services.ai.azure.com/api/projects/p"
        )

    assert result is None


def test_from_env_uses_env_var(monkeypatch):
    """resolve_appinsights_connection_from_env reads the right env var."""
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://x.services.ai.azure.com/api/projects/p",
    )

    fake_telemetry = mock.MagicMock()
    fake_telemetry.get_connection_string.return_value = "InstrumentationKey=via-env"

    fake_client = mock.MagicMock()
    fake_client.telemetry = fake_telemetry

    fake_projects_mod = mock.MagicMock()
    fake_projects_mod.AIProjectClient.return_value = fake_client
    fake_identity_mod = mock.MagicMock()

    with mock.patch.dict(
        "sys.modules",
        {
            "azure.ai.projects": fake_projects_mod,
            "azure.identity": fake_identity_mod,
        },
    ):
        from agentops.utils.foundry_discovery import (
            resolve_appinsights_connection_from_env,
        )

        assert resolve_appinsights_connection_from_env() == "InstrumentationKey=via-env"


def test_from_env_returns_none_when_env_unset(monkeypatch):
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)

    from agentops.utils.foundry_discovery import resolve_appinsights_connection_from_env

    assert resolve_appinsights_connection_from_env() is None


def test_with_reason_returns_success_tuple():
    fake_telemetry = mock.MagicMock(spec=["get_application_insights_connection_string"])
    fake_telemetry.get_application_insights_connection_string.return_value = (
        "InstrumentationKey=ok"
    )
    fake_client = mock.MagicMock()
    fake_client.telemetry = fake_telemetry
    fake_projects_mod = mock.MagicMock()
    fake_projects_mod.AIProjectClient.return_value = fake_client
    fake_identity_mod = mock.MagicMock()

    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": fake_projects_mod, "azure.identity": fake_identity_mod},
    ):
        from agentops.utils.foundry_discovery import (
            resolve_appinsights_connection_with_reason,
        )
        conn, reason = resolve_appinsights_connection_with_reason(
            "https://x.services.ai.azure.com/api/projects/with-reason-ok"
        )
    assert conn == "InstrumentationKey=ok"
    assert reason is None


def test_with_reason_surfaces_telemetry_call_failure():
    fake_telemetry = mock.MagicMock(spec=["get_application_insights_connection_string"])
    fake_telemetry.get_application_insights_connection_string.side_effect = (
        RuntimeError("403 Forbidden")
    )
    fake_client = mock.MagicMock()
    fake_client.telemetry = fake_telemetry
    fake_projects_mod = mock.MagicMock()
    fake_projects_mod.AIProjectClient.return_value = fake_client
    fake_identity_mod = mock.MagicMock()

    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": fake_projects_mod, "azure.identity": fake_identity_mod},
    ):
        from agentops.utils.foundry_discovery import (
            resolve_appinsights_connection_with_reason,
        )
        conn, reason = resolve_appinsights_connection_with_reason(
            "https://x.services.ai.azure.com/api/projects/with-reason-403"
        )
    assert conn is None
    assert reason and "telemetry metadata is not readable" in reason
    assert "Reader on the Foundry project resource group" in reason


def test_with_reason_accepts_project_managed_identity_connection():
    fake_telemetry = mock.MagicMock(spec=["get_application_insights_connection_string"])
    fake_telemetry.get_application_insights_connection_string.side_effect = ValueError(
        "Application Insights connection does not use API Key credentials."
    )
    fake_client = mock.MagicMock()
    fake_client.telemetry = fake_telemetry
    fake_projects_mod = mock.MagicMock()
    fake_projects_mod.AIProjectClient.return_value = fake_client
    fake_identity_mod = mock.MagicMock()

    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": fake_projects_mod, "azure.identity": fake_identity_mod},
    ):
        from agentops.utils.foundry_discovery import (
            PROJECT_MANAGED_IDENTITY_APPINSIGHTS_REASON,
            resolve_appinsights_connection_with_reason,
        )

        conn, reason = resolve_appinsights_connection_with_reason(
            "https://x.services.ai.azure.com/api/projects/project-managed-identity"
        )

    assert conn is None
    assert reason == PROJECT_MANAGED_IDENTITY_APPINSIGHTS_REASON


def test_resource_id_discovery_accepts_project_managed_identity_metadata():
    resource_id = (
        "/subscriptions/000/resourceGroups/rg/providers/"
        "Microsoft.Insights/components/appi"
    )
    connection = mock.Mock()
    connection.type = "ConnectionType.APPLICATION_INSIGHTS"
    connection.target = resource_id
    fake_connections = mock.MagicMock()
    fake_connections.list.return_value = iter([connection])
    fake_client = mock.MagicMock()
    fake_client.connections = fake_connections
    fake_projects_mod = mock.MagicMock()
    fake_projects_mod.AIProjectClient.return_value = fake_client
    fake_identity_mod = mock.MagicMock()

    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": fake_projects_mod, "azure.identity": fake_identity_mod},
    ):
        from agentops.utils.foundry_discovery import (
            resolve_appinsights_resource_id_with_reason,
        )

        result, reason = resolve_appinsights_resource_id_with_reason(
            "https://x.services.ai.azure.com/api/projects/pmi"
        )

    assert result == resource_id
    assert reason is None
    fake_connections.list.assert_called_once_with()


def test_with_reason_reports_missing_app_insights_connection():
    class ResourceNotFoundError(Exception):
        pass

    fake_telemetry = mock.MagicMock(spec=["get_application_insights_connection_string"])
    fake_telemetry.get_application_insights_connection_string.side_effect = (
        ResourceNotFoundError("No Application Insights connection found.")
    )
    fake_client = mock.MagicMock()
    fake_client.telemetry = fake_telemetry
    fake_projects_mod = mock.MagicMock()
    fake_projects_mod.AIProjectClient.return_value = fake_client
    fake_identity_mod = mock.MagicMock()

    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": fake_projects_mod, "azure.identity": fake_identity_mod},
    ):
        from agentops.utils.foundry_discovery import (
            resolve_appinsights_connection_with_reason,
        )

        conn, reason = resolve_appinsights_connection_with_reason(
            "https://x.services.ai.azure.com/api/projects/no-app-insights"
        )

    assert conn is None
    assert reason and "returned no Application Insights connection" in reason


def test_project_reachability_does_not_request_connection_credentials():
    fake_connections = mock.MagicMock()
    fake_connections.list.return_value = iter([])
    fake_client = mock.MagicMock()
    fake_client.connections = fake_connections
    fake_projects_mod = mock.MagicMock()
    fake_projects_mod.AIProjectClient.return_value = fake_client
    fake_identity_mod = mock.MagicMock()

    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": fake_projects_mod, "azure.identity": fake_identity_mod},
    ):
        from agentops.utils.foundry_discovery import (
            check_foundry_project_reachable_with_reason,
        )

        reachable, reason = check_foundry_project_reachable_with_reason(
            "https://x.services.ai.azure.com/api/projects/reachable"
        )

    assert reachable is True
    assert reason is None
    fake_connections.list.assert_called_once_with()
    assert not fake_client.telemetry.get_application_insights_connection_string.called


def test_successful_discovery_is_cached_in_process():
    """A second call must reuse the cached connection string instead of
    invoking the SDK again."""
    fake_telemetry = mock.MagicMock(spec=["get_application_insights_connection_string"])
    fake_telemetry.get_application_insights_connection_string.return_value = (
        "InstrumentationKey=cached"
    )
    fake_client = mock.MagicMock()
    fake_client.telemetry = fake_telemetry
    fake_projects_mod = mock.MagicMock()
    fake_projects_mod.AIProjectClient.return_value = fake_client
    fake_identity_mod = mock.MagicMock()

    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": fake_projects_mod, "azure.identity": fake_identity_mod},
    ):
        from agentops.utils.foundry_discovery import resolve_appinsights_connection
        endpoint = "https://x.services.ai.azure.com/api/projects/cached"
        first = resolve_appinsights_connection(endpoint)
        second = resolve_appinsights_connection(endpoint)
    assert first == second == "InstrumentationKey=cached"
    # Second call must NOT have built a new client.
    assert fake_projects_mod.AIProjectClient.call_count == 1


def test_telemetry_status_surfaces_discovery_reason_in_cockpit_tile(monkeypatch):
    """The cockpit tile must include the actual failure reason so the
    user does not have to dig through server logs to see why discovery
    failed."""
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://x.services.ai.azure.com/api/projects/cockpit-reason",
    )
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AGENTOPS_OTLP_ENDPOINT", raising=False)

    fake_telemetry = mock.MagicMock(spec=["get_application_insights_connection_string"])
    fake_telemetry.get_application_insights_connection_string.side_effect = (
        RuntimeError("simulated Foundry 401 Unauthorized")
    )
    fake_client = mock.MagicMock()
    fake_client.telemetry = fake_telemetry
    fake_projects_mod = mock.MagicMock()
    fake_projects_mod.AIProjectClient.return_value = fake_client
    fake_identity_mod = mock.MagicMock()

    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": fake_projects_mod, "azure.identity": fake_identity_mod},
    ):
        from agentops.agent.cockpit import _telemetry_status
        status = _telemetry_status()

    assert status["enabled"] is False
    assert status["source"] == "discovery_failed"
    # The actionable reason text appears inline in the tile detail.
    assert "401 Unauthorized" in status["detail"]
    assert "Why:" in status["detail"]


# ---------------------------------------------------------------------------
# Target discovery (issue #457): discover_prompt_agents / discover_hosted_agents
# / discover_model_deployments. All Azure SDK calls are mocked; the suite runs
# with no Azure credentials.
# ---------------------------------------------------------------------------


class _AuthError(Exception):
    """Stand-in whose class name matches the auth classifier branch."""


_AuthError.__name__ = "ClientAuthenticationError"


def _fake_modules(*, client=None, credential_exc=None):
    """Build patched azure.ai.projects / azure.identity module doubles.

    ``client`` is returned from ``AIProjectClient(...)``. When
    ``credential_exc`` is set, ``DefaultAzureCredential(...)`` raises it so the
    client-build failure path is exercised.
    """
    fake_projects_mod = mock.MagicMock()
    if client is not None:
        fake_projects_mod.AIProjectClient.return_value = client
    fake_identity_mod = mock.MagicMock()
    if credential_exc is not None:
        fake_identity_mod.DefaultAzureCredential.side_effect = credential_exc
    return fake_projects_mod, fake_identity_mod


def _client_with_agents(agents):
    client = mock.MagicMock()
    client.agents.list.return_value = agents
    return client


def _client_with_agents_error(exc):
    client = mock.MagicMock()
    client.agents.list.side_effect = exc
    return client


def _client_with_deployments(deployments):
    client = mock.MagicMock()
    client.deployments.list.return_value = deployments
    return client


def test_discover_prompt_agents_happy_path_sorted():
    from agentops.utils import foundry_discovery

    agents = [
        {"name": "zeta", "version": "1", "status": "ready"},
        {"name": "alpha", "version": "2", "status": "ready"},
    ]
    projects, identity = _fake_modules(client=_client_with_agents(agents))
    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": projects, "azure.identity": identity},
    ):
        targets, reason = foundry_discovery.discover_prompt_agents(
            "https://c.services.ai.azure.com/api/projects/p"
        )

    assert reason is None
    # Deterministic sort by (name.lower(), version): alpha before zeta.
    assert [t.agent_ref for t in targets] == ["alpha:2", "zeta:1"]
    assert all(t.target_type == "prompt" for t in targets)


def test_discover_prompt_agents_multi_version_disambiguation():
    from agentops.utils import foundry_discovery

    agents = [
        {
            "name": "my-agent",
            "versions": [
                {"version": "2", "status": "draft"},
                {"version": "1", "status": "ready"},
            ],
        }
    ]
    projects, identity = _fake_modules(client=_client_with_agents(agents))
    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": projects, "azure.identity": identity},
    ):
        targets, reason = foundry_discovery.discover_prompt_agents(
            "https://c.services.ai.azure.com/api/projects/p"
        )

    assert reason is None
    assert [t.agent_ref for t in targets] == ["my-agent:1", "my-agent:2"]


def test_discover_hosted_agents_keeps_only_agent_urls():
    from agentops.utils import foundry_discovery

    hosted_url = (
        "https://c.services.ai.azure.com/api/projects/p/agents/a/versions/3/"
    )
    agents = [
        {"name": "hosted", "endpoint": hosted_url},
        {"name": "not-hosted", "endpoint": "https://c.services.ai.azure.com/x"},
    ]
    projects, identity = _fake_modules(client=_client_with_agents(agents))
    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": projects, "azure.identity": identity},
    ):
        targets, reason = foundry_discovery.discover_hosted_agents(
            "https://c.services.ai.azure.com/api/projects/p"
        )

    assert reason is None
    assert len(targets) == 1
    assert targets[0].target_type == "hosted"
    # Trailing slash stripped; only the /agents/ URL survives.
    assert targets[0].agent_ref == hosted_url.rstrip("/")


def test_discover_model_deployments_happy_path():
    from agentops.utils import foundry_discovery

    deployments = [
        {"name": "gpt-4o", "model": "gpt-4o"},
        {"name": "embed", "type": "embedding"},
    ]
    projects, identity = _fake_modules(
        client=_client_with_deployments(deployments)
    )
    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": projects, "azure.identity": identity},
    ):
        targets, reason = foundry_discovery.discover_model_deployments(
            "https://c.services.ai.azure.com/api/projects/p"
        )

    assert reason is None
    # Non-model deployment type is filtered out.
    assert [t.agent_ref for t in targets] == ["model:gpt-4o"]
    assert targets[0].version is None


def test_discover_empty_endpoint_returns_reason():
    from agentops.utils import foundry_discovery

    targets, reason = foundry_discovery.discover_prompt_agents("")
    assert targets == []
    assert reason is not None
    assert "endpoint" in reason.lower()


def test_discover_empty_results_is_not_an_error():
    from agentops.utils import foundry_discovery

    projects, identity = _fake_modules(client=_client_with_agents([]))
    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": projects, "azure.identity": identity},
    ):
        targets, reason = foundry_discovery.discover_prompt_agents(
            "https://c.services.ai.azure.com/api/projects/p"
        )

    assert targets == []
    assert reason is None


def test_discover_sdk_not_installed(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "azure.ai.projects", None)
    monkeypatch.setitem(sys.modules, "azure.identity", None)

    from agentops.utils import foundry_discovery

    targets, reason = foundry_discovery.discover_prompt_agents(
        "https://c.services.ai.azure.com/api/projects/p"
    )
    assert targets == []
    assert reason is not None
    assert "azure-ai-projects" in reason.lower()


def test_discover_auth_unavailable_distinct_message():
    from agentops.utils import foundry_discovery

    projects, identity = _fake_modules(
        credential_exc=_AuthError("DefaultAzureCredential failed to retrieve a token")
    )
    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": projects, "azure.identity": identity},
    ):
        targets, reason = foundry_discovery.discover_prompt_agents(
            "https://c.services.ai.azure.com/api/projects/p"
        )

    assert targets == []
    assert reason is not None
    assert "authentication failed" in reason.lower()


def test_discover_rbac_distinct_message():
    from agentops.utils import foundry_discovery

    client = _client_with_agents_error(Exception("AuthorizationFailed: no access"))
    projects, identity = _fake_modules(client=client)
    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": projects, "azure.identity": identity},
    ):
        targets, reason = foundry_discovery.discover_prompt_agents(
            "https://c.services.ai.azure.com/api/projects/p"
        )

    assert targets == []
    assert reason is not None
    assert "not readable" in reason.lower()


def test_discover_unsupported_api_distinct_message():
    from agentops.utils import foundry_discovery

    client = _client_with_agents_error(
        Exception("Unsupported api version '2020-01-01'")
    )
    projects, identity = _fake_modules(client=client)
    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": projects, "azure.identity": identity},
    ):
        targets, reason = foundry_discovery.discover_prompt_agents(
            "https://c.services.ai.azure.com/api/projects/p"
        )

    assert targets == []
    assert reason is not None
    assert "not supported by this foundry project" in reason.lower()


def test_discover_network_failure_fallback_message():
    from agentops.utils import foundry_discovery

    client = _client_with_agents_error(Exception("connection reset by peer"))
    projects, identity = _fake_modules(client=client)
    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": projects, "azure.identity": identity},
    ):
        targets, reason = foundry_discovery.discover_prompt_agents(
            "https://c.services.ai.azure.com/api/projects/p"
        )

    assert targets == []
    assert reason is not None
    assert "connection reset by peer" in reason
    assert "failed" in reason.lower()


def test_discover_unsupported_sdk_when_accessor_missing():
    from agentops.utils import foundry_discovery

    # A client with no ``agents`` accessor at all (spec=[] blocks attribute
    # access) degrades to a clean "SDK too old" reason, not AttributeError.
    client = mock.MagicMock(spec=[])
    projects, identity = _fake_modules(client=client)
    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": projects, "azure.identity": identity},
    ):
        targets, reason = foundry_discovery.discover_prompt_agents(
            "https://c.services.ai.azure.com/api/projects/p"
        )

    assert targets == []
    assert reason is not None


def test_discover_caches_second_call():
    from agentops.utils import foundry_discovery

    projects, identity = _fake_modules(
        client=_client_with_agents([{"name": "a", "version": "1"}])
    )
    endpoint = "https://c.services.ai.azure.com/api/projects/p"
    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": projects, "azure.identity": identity},
    ):
        first, _ = foundry_discovery.discover_prompt_agents(endpoint)
        second, _ = foundry_discovery.discover_prompt_agents(endpoint)

    assert [t.agent_ref for t in first] == [t.agent_ref for t in second]
    # Second call is served from cache: the SDK client is built only once.
    assert projects.AIProjectClient.call_count == 1


def test_reset_cache_forces_rediscovery():
    from agentops.utils import foundry_discovery

    projects, identity = _fake_modules(
        client=_client_with_agents([{"name": "a", "version": "1"}])
    )
    endpoint = "https://c.services.ai.azure.com/api/projects/p"
    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": projects, "azure.identity": identity},
    ):
        foundry_discovery.discover_prompt_agents(endpoint)
        foundry_discovery.reset_cache()
        foundry_discovery.discover_prompt_agents(endpoint)

    assert projects.AIProjectClient.call_count == 2


def test_discover_credential_uses_process_timeout():
    from agentops.utils import foundry_discovery

    projects, identity = _fake_modules(
        client=_client_with_agents([{"name": "a", "version": "1"}])
    )
    with mock.patch.dict(
        "sys.modules",
        {"azure.ai.projects": projects, "azure.identity": identity},
    ):
        foundry_discovery.discover_prompt_agents(
            "https://c.services.ai.azure.com/api/projects/p"
        )

    identity.DefaultAzureCredential.assert_called_once()
    _, kwargs = identity.DefaultAzureCredential.call_args
    # Hard rule: az.cmd cold start needs a 30s timeout on Windows.
    assert kwargs.get("process_timeout") == 30

