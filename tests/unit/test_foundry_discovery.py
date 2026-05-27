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
