"""Tests for the shared credential factory used by Doctor data sources."""

from __future__ import annotations

import importlib
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from agentops.agent.sources import _credentials


@pytest.fixture(autouse=True)
def _clear_cache():
    _credentials.reset_shared_credentials()
    yield
    _credentials.reset_shared_credentials()


def _install_fake_identity(monkeypatch, default_cls, cli_cls=None):
    """Replace ``azure.identity`` with stub credentials."""
    if cli_cls is None:
        cli_cls = default_cls
    fake_module = SimpleNamespace(
        DefaultAzureCredential=default_cls,
        AzureCliCredential=cli_cls,
    )
    monkeypatch.setitem(importlib.sys.modules, "azure.identity", fake_module)


def _force_default_credential(monkeypatch):
    """Pretend the Azure CLI is not logged in so the default chain is used."""
    monkeypatch.setattr(_credentials, "_az_cli_logged_in", lambda _t: False)


def test_get_shared_credential_returns_singleton(monkeypatch):
    instances: list[dict[str, Any]] = []

    class _FakeCredential:
        def __init__(self, **kwargs: Any) -> None:
            instances.append(kwargs)
            self.kwargs = kwargs

    _install_fake_identity(monkeypatch, _FakeCredential)
    _force_default_credential(monkeypatch)

    first = _credentials.get_shared_credential(process_timeout=30)
    second = _credentials.get_shared_credential(process_timeout=30)

    assert first is second
    assert len(instances) == 1
    assert instances[0] == {
        "exclude_developer_cli_credential": False,
        "process_timeout": 30,
    }


def test_get_shared_credential_keys_by_options(monkeypatch):
    class _FakeCredential:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    _install_fake_identity(monkeypatch, _FakeCredential)
    _force_default_credential(monkeypatch)

    a = _credentials.get_shared_credential(exclude_developer_cli_credential=False)
    b = _credentials.get_shared_credential(exclude_developer_cli_credential=True)

    assert a is not b
    assert a.kwargs["exclude_developer_cli_credential"] is False
    assert b.kwargs["exclude_developer_cli_credential"] is True


def test_get_shared_credential_prefers_azure_cli_when_logged_in(monkeypatch):
    cli_instances: list[dict[str, Any]] = []
    default_instances: list[dict[str, Any]] = []

    class _FakeDefault:
        def __init__(self, **kwargs: Any) -> None:
            default_instances.append(kwargs)

    class _FakeCli:
        def __init__(self, **kwargs: Any) -> None:
            cli_instances.append(kwargs)

    _install_fake_identity(monkeypatch, _FakeDefault, _FakeCli)
    monkeypatch.setattr(_credentials, "_az_cli_logged_in", lambda _t: True)

    cred = _credentials.get_shared_credential(process_timeout=45)

    assert isinstance(cred, _FakeCli)
    assert default_instances == []
    assert cli_instances == [{"process_timeout": 45}]


def test_summarise_credential_error_keeps_first_line():
    msg = "DefaultAzureCredential failed to retrieve a token from the included credentials."
    exc = RuntimeError(msg)
    assert _credentials.summarise_credential_error(exc) == msg


def test_summarise_credential_error_extracts_failed_legs():
    raw = (
        "DefaultAzureCredential failed to retrieve a token from the included credentials.\n"
        "Attempted credentials:\n"
        "\tEnvironmentCredential: EnvironmentCredential authentication unavailable. "
        "Environment variables are not fully configured.\n"
        "Visit https://aka.ms/azsdk/python/identity/environmentcredential/troubleshoot\n"
        "\tWorkloadIdentityCredential: WorkloadIdentityCredential authentication unavailable.\n"
        "\tManagedIdentityCredential: ManagedIdentityCredential authentication unavailable.\n"
        "\tAzureCliCredential: Failed to invoke the Azure CLI\n"
        "\tAzurePowerShellCredential: Failed to invoke PowerShell.\n"
        "To mitigate this issue, please refer to the troubleshooting guidelines here"
    )
    summary = _credentials.summarise_credential_error(RuntimeError(raw))

    assert summary.startswith(
        "DefaultAzureCredential failed to retrieve a token"
    )
    assert "chain:" in summary
    assert "EnvironmentCredential" in summary
    assert "AzureCliCredential" in summary
    # Ensure we did not regurgitate the entire dump.
    assert "Visit https" not in summary
    assert "To mitigate" not in summary
    assert "\n" not in summary


def test_summarise_credential_error_truncates_long_chains():
    legs = "\n".join(
        f"\t{name}Credential: unavailable"
        for name in [
            "Environment",
            "WorkloadIdentity",
            "ManagedIdentity",
            "SharedTokenCache",
            "VisualStudioCode",
            "AzureCli",
            "AzurePowerShell",
        ]
    )
    raw = f"DefaultAzureCredential failed to retrieve a token\nAttempted credentials:\n{legs}"
    summary = _credentials.summarise_credential_error(RuntimeError(raw))
    assert "+3 more" in summary


def test_summarise_credential_error_falls_back_to_class_name():
    class _Empty(Exception):
        def __str__(self) -> str:
            return ""

    assert _credentials.summarise_credential_error(_Empty()) == "_Empty"


def test_format_source_error_passes_through_non_auth():
    exc = ValueError("plain error")
    assert _credentials.format_source_error(exc) == "plain error"


def test_format_source_error_summarises_known_auth_error_by_name():
    class ClientAuthenticationError(Exception):
        pass

    raw = (
        "DefaultAzureCredential failed to retrieve a token\n"
        "Attempted credentials:\n"
        "\tAzureCliCredential: Failed to invoke the Azure CLI\n"
        "To mitigate this issue..."
    )
    summary = _credentials.format_source_error(ClientAuthenticationError(raw))
    assert summary.startswith("DefaultAzureCredential failed to retrieve a token")
    assert "AzureCliCredential" in summary
    assert "\n" not in summary


def test_log_source_error_downgrades_credential_errors(caplog):
    class ClientAuthenticationError(Exception):
        pass

    logger = logging.getLogger("agentops.test.credentials")
    caplog.set_level(logging.INFO, logger=logger.name)
    exc = ClientAuthenticationError(
        "DefaultAzureCredential failed to retrieve a token\n"
        "Attempted credentials:\n"
        "\tAzureCliCredential: Failed to invoke the Azure CLI\n"
        "To mitigate ..."
    )

    reason = _credentials.log_source_error(logger, "App Insights skipped", exc)

    assert "DefaultAzureCredential" in reason
    matched = [
        r for r in caplog.records if r.message.startswith("App Insights skipped")
    ]
    assert matched, "expected one log record"
    assert matched[0].levelno == logging.INFO


def test_log_source_error_keeps_real_errors_at_warning(caplog):
    logger = logging.getLogger("agentops.test.credentials")
    caplog.set_level(logging.DEBUG, logger=logger.name)
    exc = ValueError("network unreachable")

    reason = _credentials.log_source_error(logger, "Azure Monitor query failed", exc)

    assert reason == "network unreachable"
    matched = [
        r
        for r in caplog.records
        if r.message.startswith("Azure Monitor query failed")
    ]
    assert matched and matched[0].levelno == logging.WARNING
