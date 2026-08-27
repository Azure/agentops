"""Hosted Cockpit authentication and delegated-token helpers.

Easy Auth authenticates the signed-in browser user and terminates at the App
Service edge; this module does not parse or validate that principal (header
and claim validation is a separate concern). It only turns an already
validated user access token ("user assertion") into a delegated credential
that can query Azure Monitor Logs on the user's behalf, federated through the
app's user-assigned managed identity (UAMI) instead of a stored application
secret.

Two credential chains are exposed and are intentionally incompatible with one
another, so delegated access can never be silently widened into aggregate
access or vice versa (FR-071, FR-072):

* :func:`build_delegated_monitor_credential` -- On-Behalf-Of, scoped to
  delegated Azure Monitor Logs reads. Requires a genuine user assertion and
  raises when one is missing; there is no fallback to identity-only access.
* :func:`build_aggregate_credential` -- UAMI-only, used for discovery and any
  access that is deliberately *not* delegated. It has no ``user_assertion``
  parameter, so a caller cannot accidentally use it for delegated reads.

Azure SDK imports happen lazily inside the functions below so importing this
module never requires ``azure-identity`` to be installed and never touches
the network at import time.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Protocol

#: Scope the app's user-assigned managed identity requests when acting as
#: the federated client assertion for :class:`~azure.identity.OnBehalfOfCredential`.
UAMI_FEDERATION_SCOPE = "api://AzureADTokenExchange/.default"

#: Scope requested for delegated (on-behalf-of) Azure Monitor Logs reads.
DELEGATED_MONITOR_LOGS_SCOPE = "https://api.loganalytics.io/.default"


class TokenCredential(Protocol):
    """Structural shape of an ``azure-identity`` credential used here."""

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        ...


CredentialFactory = Callable[..., TokenCredential]
ObeFactory = Callable[..., TokenCredential]


class MissingUserAssertionError(ValueError):
    """Raised when delegated Monitor access is requested without a user token."""


def _default_managed_identity_factory(*, client_id: str) -> TokenCredential:
    from azure.identity import ManagedIdentityCredential  # lazy: optional dep

    return ManagedIdentityCredential(client_id=client_id, process_timeout=30)


def _default_obo_factory(
    *,
    tenant_id: str,
    client_id: str,
    client_assertion_func: Callable[[], str],
    user_assertion: str,
) -> TokenCredential:
    from azure.identity import OnBehalfOfCredential  # lazy: optional dep

    return OnBehalfOfCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_assertion_func=client_assertion_func,
        user_assertion=user_assertion,
        process_timeout=30,
    )


def _build_uami_client_assertion(
    uami_client_id: str,
    *,
    credential_factory: Optional[CredentialFactory] = None,
) -> Callable[[], str]:
    """Return a zero-arg callable producing a fresh UAMI federation token.

    ``OnBehalfOfCredential`` accepts a ``client_assertion_func`` callback
    instead of a stored application secret; this wires that callback to the
    user-assigned managed identity so the app registration never holds a
    federated-credential secret of its own (FR-071).
    """
    factory = credential_factory or _default_managed_identity_factory

    def _assertion() -> str:
        credential = factory(client_id=uami_client_id)
        token = credential.get_token(UAMI_FEDERATION_SCOPE)
        return token.token

    return _assertion


def build_delegated_monitor_credential(
    *,
    tenant_id: str,
    client_id: str,
    uami_client_id: str,
    user_assertion: str,
    credential_factory: Optional[CredentialFactory] = None,
    obo_factory: Optional[ObeFactory] = None,
) -> TokenCredential:
    """Build the delegated OBO credential used for a user's Monitor Logs reads.

    Raises :class:`MissingUserAssertionError` when *user_assertion* is empty
    so a caller can never silently downgrade a delegated request into
    identity-only (UAMI) access -- there is no fallback path in either
    direction (FR-072).
    """
    if not user_assertion or not user_assertion.strip():
        raise MissingUserAssertionError(
            "delegated Azure Monitor access requires a validated Easy Auth "
            "user assertion; none was supplied"
        )

    client_assertion_func = _build_uami_client_assertion(
        uami_client_id, credential_factory=credential_factory
    )
    factory = obo_factory or _default_obo_factory
    return factory(
        tenant_id=tenant_id,
        client_id=client_id,
        client_assertion_func=client_assertion_func,
        user_assertion=user_assertion,
    )


def build_aggregate_credential(
    uami_client_id: str,
    *,
    credential_factory: Optional[CredentialFactory] = None,
) -> TokenCredential:
    """Build the UAMI-only credential used for aggregate/discovery access.

    This intentionally has no ``user_assertion`` parameter: aggregate access
    must never be upgraded into a delegated identity, matching the
    "no fallback either direction" requirement in FR-072.
    """
    factory = credential_factory or _default_managed_identity_factory
    return factory(client_id=uami_client_id)


def build_local_developer_credential(
    *,
    credential_factory: Optional[Callable[[], TokenCredential]] = None,
) -> TokenCredential:
    """Build the ambient credential used for local developer Cockpit access.

    Unlike the two hosted chains above, this uses the developer's own signed-in
    Azure identity (Azure CLI / VS Code / environment) via
    :class:`~azure.identity.DefaultAzureCredential`. It requires none of the
    hosted identity configuration (``AGENTOPS_TENANT_ID`` /
    ``AGENTOPS_APPLICATION_CLIENT_ID`` / ``AGENTOPS_UAMI_CLIENT_ID``) and is only
    ever used for aggregate discovery/query reads -- local mode has no hosted
    end-user identity, so it never grants delegated (per-user) access.

    ``process_timeout=30`` is mandatory: the 10s default times out the
    ``az.cmd`` cold start on Windows.
    """
    if credential_factory is not None:
        return credential_factory()
    from azure.identity import DefaultAzureCredential  # lazy: optional dep

    return DefaultAzureCredential(process_timeout=30)
