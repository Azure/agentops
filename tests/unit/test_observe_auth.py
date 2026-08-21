"""Tests for the UAMI-federated delegated Azure Monitor credential chain."""

from __future__ import annotations

import builtins
import inspect
import sys
from types import SimpleNamespace

import pytest

from agentops.agent.observe.auth import (
    DELEGATED_MONITOR_LOGS_SCOPE,
    UAMI_FEDERATION_SCOPE,
    MissingUserAssertionError,
    build_aggregate_credential,
    build_delegated_monitor_credential,
)


def _fake_managed_identity_factory(calls: list[dict[str, object]]):
    def factory(*, client_id: str) -> SimpleNamespace:
        calls.append({"client_id": client_id})
        return SimpleNamespace(
            get_token=lambda *scopes, **kwargs: SimpleNamespace(token="uami-token")
        )

    return factory


def test_delegated_credential_wires_uami_assertion_and_user_token() -> None:
    identity_calls: list[dict[str, object]] = []
    obo_calls: list[dict[str, object]] = []

    def obo_factory(**kwargs: object) -> SimpleNamespace:
        obo_calls.append(kwargs)
        return SimpleNamespace(kind="obo", **kwargs)

    credential = build_delegated_monitor_credential(
        tenant_id="tenant-1",
        client_id="app-client-1",
        uami_client_id="uami-1",
        user_assertion="user-token",
        credential_factory=_fake_managed_identity_factory(identity_calls),
        obo_factory=obo_factory,
    )

    assert len(obo_calls) == 1
    call = obo_calls[0]
    assert call["tenant_id"] == "tenant-1"
    assert call["client_id"] == "app-client-1"
    assert call["user_assertion"] == "user-token"
    assert callable(call["client_assertion_func"])

    # The client assertion is produced lazily by calling the UAMI factory.
    assert identity_calls == []
    assert call["client_assertion_func"]() == "uami-token"
    assert identity_calls == [{"client_id": "uami-1"}]
    assert credential.kind == "obo"


def test_delegated_monitor_logs_scope_is_requested_through_the_credential() -> None:
    requested_scopes: list[tuple[str, ...]] = []

    def obo_factory(**kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            get_token=lambda *scopes, **kw: requested_scopes.append(scopes)
            or SimpleNamespace(token="delegated-token")
        )

    credential = build_delegated_monitor_credential(
        tenant_id="tenant-1",
        client_id="app-client-1",
        uami_client_id="uami-1",
        user_assertion="user-token",
        credential_factory=_fake_managed_identity_factory([]),
        obo_factory=obo_factory,
    )

    token = credential.get_token(DELEGATED_MONITOR_LOGS_SCOPE)

    assert requested_scopes == [(DELEGATED_MONITOR_LOGS_SCOPE,)]
    assert token.token == "delegated-token"
    assert DELEGATED_MONITOR_LOGS_SCOPE == "https://api.loganalytics.io/.default"
    assert UAMI_FEDERATION_SCOPE == "api://AzureADTokenExchange/.default"


@pytest.mark.parametrize("user_assertion", ["", "   ", None])
def test_missing_user_assertion_raises_without_uami_only_fallback(
    user_assertion: object,
) -> None:
    identity_calls: list[dict[str, object]] = []

    with pytest.raises(MissingUserAssertionError):
        build_delegated_monitor_credential(
            tenant_id="tenant-1",
            client_id="app-client-1",
            uami_client_id="uami-1",
            user_assertion=user_assertion,  # type: ignore[arg-type]
            credential_factory=_fake_managed_identity_factory(identity_calls),
            obo_factory=lambda **kwargs: pytest.fail(
                "OBO credential must never be constructed without a user assertion"
            ),
        )

    # No UAMI token should have been requested either -- there is no partial
    # "identity-only" fallback path.
    assert identity_calls == []


def test_aggregate_credential_has_no_user_assertion_parameter() -> None:
    signature = inspect.signature(build_aggregate_credential)
    assert "user_assertion" not in signature.parameters

    identity_calls: list[dict[str, object]] = []
    credential = build_aggregate_credential(
        "uami-1",
        credential_factory=_fake_managed_identity_factory(identity_calls),
    )

    assert identity_calls == [{"client_id": "uami-1"}]
    assert credential.get_token(DELEGATED_MONITOR_LOGS_SCOPE).token == "uami-token"


def test_default_aggregate_credential_passes_process_timeout(monkeypatch) -> None:
    """Windows ``az.cmd`` cold starts need more than the SDK's 10s default."""
    calls: list[dict[str, object]] = []

    class _FakeManagedIdentityCredential:
        def __init__(self, **kwargs: object) -> None:
            calls.append(kwargs)

    fake_module = SimpleNamespace(ManagedIdentityCredential=_FakeManagedIdentityCredential)
    monkeypatch.setitem(sys.modules, "azure.identity", fake_module)

    build_aggregate_credential("uami-1")

    assert calls == [{"client_id": "uami-1", "process_timeout": 30}]


def test_default_obo_credential_passes_process_timeout(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class _FakeManagedIdentityCredential:
        def __init__(self, **kwargs: object) -> None:
            pass

        def get_token(self, *scopes: str, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(token="uami-token")

    class _FakeOnBehalfOfCredential:
        def __init__(self, **kwargs: object) -> None:
            calls.append(kwargs)

    fake_module = SimpleNamespace(
        ManagedIdentityCredential=_FakeManagedIdentityCredential,
        OnBehalfOfCredential=_FakeOnBehalfOfCredential,
    )
    monkeypatch.setitem(sys.modules, "azure.identity", fake_module)

    build_delegated_monitor_credential(
        tenant_id="tenant-1",
        client_id="app-client-1",
        uami_client_id="uami-1",
        user_assertion="user-token",
    )

    assert len(calls) == 1
    assert calls[0]["process_timeout"] == 30
    assert calls[0]["tenant_id"] == "tenant-1"


def test_module_import_never_imports_azure_identity_eagerly(monkeypatch) -> None:
    sys.modules.pop("agentops.agent.observe.auth", None)
    original_import = builtins.__import__

    def guard(name: str, *args: object, **kwargs: object) -> object:
        if name == "azure.identity" or name.startswith("azure.identity."):
            raise AssertionError(
                "azure.identity must not be imported at module import time"
            )
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard)

    import agentops.agent.observe.auth  # noqa: F401 -- re-import under the guard
