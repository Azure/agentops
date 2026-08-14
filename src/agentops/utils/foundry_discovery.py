"""Discover Foundry-attached resources from a project endpoint.

Currently exposes one helper: :func:`resolve_appinsights_connection`,
which asks a Foundry project for the connection string of the
Application Insights resource attached to it. Used by
:func:`agentops.utils.telemetry.init_tracing` as a fallback when the
user has configured ``AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`` but not the
explicit ``APPLICATIONINSIGHTS_CONNECTION_STRING`` env var.

All Azure SDK imports are lazy; the discovery is best-effort and never
raises into callers - a missing SDK, a 404, or any unexpected response
shape returns ``None`` and the caller falls back to its no-op path.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional, Tuple

log = logging.getLogger(__name__)

PROJECT_MANAGED_IDENTITY_APPINSIGHTS_REASON = (
    "Foundry Application Insights connection uses ProjectManagedIdentity; "
    "API Key credentials are not required."
)
_PROJECT_MANAGED_IDENTITY_APPINSIGHTS_ERROR = (
    "Application Insights connection does not use API Key credentials."
)
_NO_APPINSIGHTS_CONNECTION_ERROR = "No Application Insights connection found."


# Per-process cache so the cockpit does not re-query Foundry on every
# page load. Successful results are remembered for a long window
# (discovery rarely changes); failures are remembered for a short
# window so transient blips do not pin the cockpit into the error
# state across many reloads.
_SUCCESS_TTL_SECONDS = 30 * 60
_FAILURE_TTL_SECONDS = 60
_cache_lock = threading.Lock()
_cache: dict[str, Tuple[float, Optional[str], Optional[str]]] = {}


def _store(key: str, conn: Optional[str], reason: Optional[str]) -> None:
    with _cache_lock:
        _cache[key] = (time.time(), conn, reason)


def _lookup(key: str) -> Optional[Tuple[Optional[str], Optional[str]]]:
    with _cache_lock:
        entry = _cache.get(key)
    if entry is None:
        return None
    ts, conn, reason = entry
    ttl = _SUCCESS_TTL_SECONDS if conn else _FAILURE_TTL_SECONDS
    if time.time() - ts > ttl:
        return None
    return conn, reason


def reset_cache() -> None:
    """Clear the per-process discovery cache (test helper)."""
    with _cache_lock:
        _cache.clear()


def _summarize_discovery_exception(exc: Exception, *, context: str) -> str:
    text = str(exc)
    lower = text.lower()
    auth_markers = (
        "defaultazurecredential failed to retrieve a token",
        "azureclicredential: failed to invoke the azure cli",
        "azurepowershellcredential: failed to invoke powershell",
        "environmentcredential authentication unavailable",
        "sharedtokencachecredential authentication unavailable",
        "clientauthenticationerror",
    )
    if type(exc).__name__ == "ClientAuthenticationError" or any(
        marker in lower for marker in auth_markers
    ):
        return (
            "Foundry authentication failed while reading telemetry metadata. "
            "Run `az login` in this shell, confirm the active account has "
            "Reader on the Foundry project resource group, then re-run."
        )

    permission_markers = (
        "authorizationfailed",
        "forbidden",
        "does not have authorization",
        "insufficient privileges",
    )
    if any(marker in lower for marker in permission_markers):
        return (
            "Foundry telemetry metadata is not readable by the signed-in "
            "identity. Grant Reader on the Foundry project resource group "
            "or set APPLICATIONINSIGHTS_CONNECTION_STRING manually."
        )

    snippet = text.splitlines()[0].strip() if text else type(exc).__name__
    if len(snippet) > 220:
        snippet = snippet[:217] + "..."
    return f"{context} failed ({type(exc).__name__}: {snippet})."


def check_foundry_project_reachable_with_reason(
    project_endpoint: str,
) -> Tuple[bool, Optional[str]]:
    """Return whether *project_endpoint* is reachable with the current identity.

    The reachability probe lists project connections without requesting their
    credentials. This keeps project validation independent from Application
    Insights connection-string discovery, which is unavailable for
    ProjectManagedIdentity connections by design.
    """
    if not project_endpoint:
        return False, "no AZURE_AI_FOUNDRY_PROJECT_ENDPOINT set"

    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
    except ImportError:
        return (
            False,
            "azure-ai-projects / azure-identity not installed in the cockpit's "
            "Python environment. Install with "
            "`pip install azure-ai-projects azure-identity`.",
        )

    try:
        credential = DefaultAzureCredential(
            exclude_developer_cli_credential=True,
            process_timeout=30,
        )
        client = AIProjectClient(
            endpoint=project_endpoint,
            credential=credential,
        )
        connections = getattr(client, "connections", None)
        list_connections = getattr(connections, "list", None)
        if not callable(list_connections):
            return (
                False,
                "AIProjectClient has no connections.list helper "
                "(azure-ai-projects too old).",
            )
        next(iter(list_connections()), None)
    except Exception as exc:  # noqa: BLE001
        return False, _summarize_discovery_exception(
            exc,
            context="Foundry project reachability check",
        )
    return True, None


def resolve_appinsights_connection_with_reason(
    project_endpoint: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(connection_string, error_reason)`` for *project_endpoint*.

    On success, ``connection_string`` is the App Insights connection
    string and ``error_reason`` is ``None``. On failure, the
    connection string is ``None`` and ``error_reason`` is a short,
    user-actionable explanation suitable for surfacing in the
    cockpit tile.

    Successful results are cached in-process for 30 minutes; failure
    results for 60 seconds (so a transient Foundry hiccup does not
    pin the cockpit into the error state for half an hour).
    """
    if not project_endpoint:
        return None, "no AZURE_AI_FOUNDRY_PROJECT_ENDPOINT set"

    cached = _lookup(project_endpoint)
    if cached is not None:
        return cached

    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
    except ImportError:
        reason = (
            "azure-ai-projects / azure-identity not installed in the "
            "cockpit's Python environment. Install with "
            "`pip install azure-ai-projects azure-identity`."
        )
        log.debug(reason)
        _store(project_endpoint, None, reason)
        return None, reason

    try:
        credential = DefaultAzureCredential(exclude_developer_cli_credential=True, process_timeout=30)
        client = AIProjectClient(
            endpoint=project_endpoint,
            credential=credential,
        )
    except Exception as exc:  # noqa: BLE001
        reason = _summarize_discovery_exception(
            exc,
            context="Could not build AIProjectClient",
        )
        log.debug(reason)
        _store(project_endpoint, None, reason)
        return None, reason

    telemetry_attr = getattr(client, "telemetry", None)
    if telemetry_attr is None:
        reason = (
            "AIProjectClient has no .telemetry helper "
            "(azure-ai-projects too old). Set "
            "APPLICATIONINSIGHTS_CONNECTION_STRING manually."
        )
        log.debug(reason)
        _store(project_endpoint, None, reason)
        return None, reason

    # The exact method name has shifted slightly across SDK versions;
    # try the documented one first, then a couple of known aliases.
    candidate_methods = (
        "get_application_insights_connection_string",
        "get_connection_string",
        "connection_string",
    )
    last_exc: Optional[Exception] = None
    for name in candidate_methods:
        fn = getattr(telemetry_attr, name, None)
        if fn is None:
            continue
        try:
            value = fn() if callable(fn) else fn
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            log.debug(
                "AIProjectClient.telemetry.%s raised %s; trying next.",
                name,
                exc,
            )
            continue
        if isinstance(value, str) and value:
            _store(project_endpoint, value, None)
            return value, None

    if (
        isinstance(last_exc, ValueError)
        and str(last_exc) == _PROJECT_MANAGED_IDENTITY_APPINSIGHTS_ERROR
    ):
        reason = PROJECT_MANAGED_IDENTITY_APPINSIGHTS_REASON
    elif (
        type(last_exc).__name__ == "ResourceNotFoundError"
        and str(last_exc) == _NO_APPINSIGHTS_CONNECTION_ERROR
    ):
        reason = (
            "Foundry returned no Application Insights connection. Wire "
            "one in: Project details \u2192 Connected resources \u2192 "
            "Add connection \u2192 Application Insights."
        )
    elif last_exc is not None:
        reason = _summarize_discovery_exception(
            last_exc,
            context="Foundry telemetry discovery",
        )
    else:
        reason = (
            "Foundry returned no Application Insights connection. Wire "
            "one in: Project details \u2192 Connected resources \u2192 "
            "Add connection \u2192 Application Insights."
        )
    log.debug(reason)
    _store(project_endpoint, None, reason)
    return None, reason


def resolve_appinsights_connection(project_endpoint: str) -> Optional[str]:
    """Return the App Insights connection string for *project_endpoint*.

    Returns ``None`` on any failure. See
    :func:`resolve_appinsights_connection_with_reason` for the
    diagnostic-aware variant used by the cockpit.
    """
    conn, _ = resolve_appinsights_connection_with_reason(project_endpoint)
    return conn


def resolve_appinsights_resource_id_with_reason(
    project_endpoint: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Return the ARM resource ID of the Foundry-linked App Insights resource.

    Connection metadata is credential-free, so this works for both API-key and
    ProjectManagedIdentity connections without requesting connection secrets.
    """
    if not project_endpoint:
        return None, "no AZURE_AI_FOUNDRY_PROJECT_ENDPOINT set"

    cache_key = f"appinsights-resource:{project_endpoint}"
    cached = _lookup(cache_key)
    if cached is not None:
        return cached

    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
    except ImportError:
        reason = (
            "azure-ai-projects / azure-identity not installed in the cockpit's "
            "Python environment. Install with "
            "`pip install azure-ai-projects azure-identity`."
        )
        _store(cache_key, None, reason)
        return None, reason

    try:
        credential = DefaultAzureCredential(
            exclude_developer_cli_credential=True,
            process_timeout=30,
        )
        client = AIProjectClient(endpoint=project_endpoint, credential=credential)
        connections = getattr(client, "connections", None)
        list_connections = getattr(connections, "list", None)
        if not callable(list_connections):
            reason = (
                "AIProjectClient has no connections.list helper "
                "(azure-ai-projects too old)."
            )
            _store(cache_key, None, reason)
            return None, reason

        for connection in list_connections():
            connection_type = str(getattr(connection, "type", "") or "").lower()
            target = str(getattr(connection, "target", "") or "").strip()
            is_app_insights = (
                "application_insights" in connection_type
                or "applicationinsights" in connection_type
            )
            is_resource_id = (
                target.lower().startswith("/subscriptions/")
                and "/providers/microsoft.insights/components/" in target.lower()
            )
            if is_app_insights and is_resource_id:
                _store(cache_key, target, None)
                return target, None
    except Exception as exc:  # noqa: BLE001
        reason = _summarize_discovery_exception(
            exc,
            context="Foundry App Insights connection metadata discovery",
        )
        _store(cache_key, None, reason)
        return None, reason

    reason = (
        "Foundry returned no Application Insights connection metadata. Wire "
        "one in: Project details \u2192 Connected resources \u2192 "
        "Add connection \u2192 Application Insights."
    )
    _store(cache_key, None, reason)
    return None, reason


def resolve_appinsights_connection_from_env() -> Optional[str]:
    """Resolve using ``AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`` if set."""
    endpoint = os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        return None
    return resolve_appinsights_connection(endpoint)


def resolve_appinsights_connection_from_env_with_reason() -> Tuple[
    Optional[str], Optional[str]
]:
    """Variant of :func:`resolve_appinsights_connection_from_env` that
    also returns the error reason when discovery fails."""
    endpoint = os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        return None, "no AZURE_AI_FOUNDRY_PROJECT_ENDPOINT set"
    return resolve_appinsights_connection_with_reason(endpoint)


def resolve_appinsights_resource_id_from_env_with_reason() -> Tuple[
    Optional[str], Optional[str]
]:
    """Resolve App Insights ARM metadata from the configured Foundry project."""
    endpoint = os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        return None, "no AZURE_AI_FOUNDRY_PROJECT_ENDPOINT set"
    return resolve_appinsights_resource_id_with_reason(endpoint)
