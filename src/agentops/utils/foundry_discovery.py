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
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveredTarget:
    """A single Foundry-attached resource that can serve as an eval target.

    ``agent_ref`` is the *canonical* reference AgentOps persists to
    ``agentops.yaml`` (``<name>:<version>`` for prompt agents, a
    version-pinned hosted URL for hosted agents, ``model:<deployment>`` for
    model deployments). The remaining fields are display metadata used to
    render the selectable list in the init wizard.
    """

    target_type: str  # "prompt" | "hosted" | "model"
    display_name: str
    name: str
    agent_ref: str
    version: Optional[str] = None
    status: Optional[str] = None
    protocol: Optional[str] = None

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

# Separate cache for target-discovery listings so re-prompting the init
# wizard (or a cockpit refresh) does not re-hit Azure. Keyed by
# ``"<kind>:<endpoint>"``. Successful lookups (reason is None) are held for
# the long window even when the list is empty (an empty project is a stable
# fact); failures use the short window so transient blips clear quickly.
_discovery_cache_lock = threading.Lock()
_discovery_cache: dict[
    str, Tuple[float, Tuple["DiscoveredTarget", ...], Optional[str]]
] = {}


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


def _discovery_store(
    key: str,
    targets: Tuple["DiscoveredTarget", ...],
    reason: Optional[str],
) -> None:
    with _discovery_cache_lock:
        _discovery_cache[key] = (time.time(), targets, reason)


def _discovery_lookup(
    key: str,
) -> Optional[Tuple[Tuple["DiscoveredTarget", ...], Optional[str]]]:
    with _discovery_cache_lock:
        entry = _discovery_cache.get(key)
    if entry is None:
        return None
    ts, targets, reason = entry
    # Success (reason is None) is stable even when empty; only failures use
    # the short TTL so a transient error does not pin the wizard.
    ttl = _SUCCESS_TTL_SECONDS if reason is None else _FAILURE_TTL_SECONDS
    if time.time() - ts > ttl:
        return None
    return targets, reason


def reset_cache() -> None:
    """Clear the per-process discovery caches (test helper)."""
    with _cache_lock:
        _cache.clear()
    with _discovery_cache_lock:
        _discovery_cache.clear()


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

    unsupported_markers = (
        "unsupported api version",
        "unsupported api-version",
        "invalid api version",
        "invalid api-version",
        "api version not supported",
        "api-version not supported",
        "no api version",
        "is not supported for this project",
        "unsupported project",
        "not a valid project",
    )
    if any(marker in lower for marker in unsupported_markers):
        return (
            f"{context} is not supported by this Foundry project's type or "
            "API version. Confirm the endpoint is a Microsoft Foundry project "
            "(not a hub/portal URL) and that azure-ai-projects is current, or "
            "enter the target manually."
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


# ---------------------------------------------------------------------------
# Type-scoped target discovery for the init wizard (issue #457)
#
# After the user picks *what kind* of eval target they want, the wizard asks
# these functions for the compatible resources that actually exist in the
# Foundry project. Each returns ``(targets, reason)``:
#
# * ``reason is None`` — success. ``targets`` may be empty (a valid, non-error
#   "no compatible resources" state the wizard renders distinctly).
# * ``reason`` set — a human-actionable failure message. The wizard shows it,
#   then falls back to manual entry / configure-later so init never blocks.
#
# All Azure imports stay lazy; discovery is strictly read-only; listings are
# de-duplicated by canonical ref and returned in a deterministic sorted order.
# ---------------------------------------------------------------------------

_SDK_NOT_INSTALLED_REASON = (
    "Azure discovery needs azure-ai-projects and azure-identity, which are "
    "not installed in this environment. Install them "
    "(`pip install azure-ai-projects azure-identity`) or enter the target "
    "manually."
)

_KIND_LABELS = {
    "prompt": "Foundry prompt agent",
    "hosted": "Foundry hosted agent",
    "model": "Foundry model deployment",
}


class _UnsupportedSdkError(Exception):
    """Raised when the installed SDK lacks the accessor we need to list."""


def _attr(obj: object, *names: str):
    """Return the first non-empty attribute/key from ``names`` on ``obj``.

    Supports both attribute-style SDK models and plain dict payloads so the
    discovery layer tolerates SDK version drift and mocked test doubles.
    """
    for name in names:
        if isinstance(obj, dict):
            value = obj.get(name)
        else:
            value = getattr(obj, name, None)
        if value not in (None, ""):
            return value
    return None


def _iter_agents(client) -> list:
    agents = getattr(client, "agents", None)
    lister = getattr(agents, "list", None) if agents is not None else None
    if not callable(lister):
        raise _UnsupportedSdkError(
            "This azure-ai-projects build cannot list agents "
            "(no agents.list accessor). Upgrade azure-ai-projects, or enter "
            "the target manually."
        )
    return list(lister())


def _iter_deployments(client) -> list:
    deployments = getattr(client, "deployments", None)
    lister = getattr(deployments, "list", None) if deployments is not None else None
    if not callable(lister):
        raise _UnsupportedSdkError(
            "This azure-ai-projects build cannot list deployments "
            "(no deployments.list accessor). Upgrade azure-ai-projects, or "
            "enter the target manually."
        )
    return list(lister())


def _agent_versions(agent: object) -> list:
    """Return ``[(version, status), ...]`` for one listed agent.

    Expands an embedded ``versions`` collection when present so multi-version
    agents surface every version as its own selectable candidate; otherwise
    falls back to the agent's single version.
    """
    out: list = []
    versions = _attr(agent, "versions", "version_list")
    if versions is not None and not isinstance(versions, (str, bytes)):
        try:
            iterator = list(versions)
        except TypeError:
            iterator = []
        for item in iterator:
            if isinstance(item, (str, int, float)):
                out.append((str(item), None))
                continue
            ver = _attr(item, "version", "name", "id")
            if ver is None:
                continue
            status = _attr(item, "status", "state", "lifecycle_state")
            out.append((str(ver), str(status) if status is not None else None))
    if not out:
        ver = _attr(agent, "version", "latest_version")
        if ver is not None:
            status = _attr(agent, "status", "state", "lifecycle_state")
            out.append((str(ver), str(status) if status is not None else None))
    return out


def _hosted_name_from_url(url: str) -> str:
    low = url.lower()
    idx = low.find("/agents/")
    if idx == -1:
        return url
    tail = url[idx + len("/agents/") :].strip("/")
    first = tail.split("/")[0]
    return first or url


def _collect_prompt_agents(client) -> List[DiscoveredTarget]:
    out: List[DiscoveredTarget] = []
    for agent in _iter_agents(client):
        name = _attr(agent, "name", "id")
        if not name:
            continue
        for version, status in _agent_versions(agent):
            out.append(
                DiscoveredTarget(
                    target_type="prompt",
                    display_name=str(name),
                    name=str(name),
                    agent_ref=f"{name}:{version}",
                    version=str(version),
                    status=status,
                )
            )
    return out


def _collect_hosted_agents(client) -> List[DiscoveredTarget]:
    out: List[DiscoveredTarget] = []
    for agent in _iter_agents(client):
        url = _attr(agent, "endpoint", "url", "target", "endpoint_url")
        if not url or "/agents/" not in str(url).lower():
            continue
        ref = str(url).rstrip("/")
        name = _attr(agent, "name", "id") or _hosted_name_from_url(ref)
        version = _attr(agent, "version", "latest_version")
        status = _attr(agent, "status", "state", "lifecycle_state")
        out.append(
            DiscoveredTarget(
                target_type="hosted",
                display_name=str(name),
                name=str(name),
                agent_ref=ref,
                version=str(version) if version is not None else None,
                status=str(status) if status is not None else None,
            )
        )
    return out


def _collect_model_deployments(client) -> List[DiscoveredTarget]:
    out: List[DiscoveredTarget] = []
    for deployment in _iter_deployments(client):
        name = _attr(deployment, "name", "deployment_name", "id")
        if not name:
            continue
        dtype = _attr(deployment, "type", "deployment_type", "kind")
        if dtype is not None and "model" not in str(dtype).lower():
            continue
        model = _attr(deployment, "model_name", "model", "model_id")
        status = _attr(deployment, "status", "state", "provisioning_state")
        display = str(name) if not model else f"{name} ({model})"
        out.append(
            DiscoveredTarget(
                target_type="model",
                display_name=display,
                name=str(name),
                agent_ref=f"model:{name}",
                version=None,
                status=str(status) if status is not None else None,
            )
        )
    return out


def _dedupe_sorted(targets: List[DiscoveredTarget]) -> List[DiscoveredTarget]:
    seen: dict[str, DiscoveredTarget] = {}
    for target in targets:
        seen.setdefault(target.agent_ref, target)
    return sorted(
        seen.values(),
        key=lambda t: (t.name.lower(), str(t.version or "")),
    )


def _build_project_client(project_endpoint: str):
    """Return ``(client, None)`` or ``(None, reason)`` — lazy Azure imports."""
    try:
        from azure.ai.projects import AIProjectClient  # noqa: PLC0415
        from azure.identity import DefaultAzureCredential  # noqa: PLC0415
    except ImportError:
        return None, _SDK_NOT_INSTALLED_REASON
    try:
        credential = DefaultAzureCredential(
            exclude_developer_cli_credential=True,
            process_timeout=30,
        )
        client = AIProjectClient(endpoint=project_endpoint, credential=credential)
    except Exception as exc:  # noqa: BLE001
        return None, _summarize_discovery_exception(
            exc, context="Foundry target discovery"
        )
    return client, None


def _discover(
    project_endpoint: Optional[str],
    kind: str,
    collector: Callable,
) -> Tuple[List[DiscoveredTarget], Optional[str]]:
    if not project_endpoint:
        return [], "no Foundry project endpoint configured"
    label = _KIND_LABELS.get(kind, "Foundry target")
    key = f"{kind}:{project_endpoint}"
    cached = _discovery_lookup(key)
    if cached is not None:
        cached_targets, reason = cached
        return list(cached_targets), reason

    client, reason = _build_project_client(project_endpoint)
    if client is None:
        _discovery_store(key, (), reason)
        return [], reason

    try:
        collected = collector(client)
    except _UnsupportedSdkError as exc:
        reason = str(exc)
        _discovery_store(key, (), reason)
        return [], reason
    except Exception as exc:  # noqa: BLE001
        reason = _summarize_discovery_exception(exc, context=f"{label} discovery")
        _discovery_store(key, (), reason)
        return [], reason

    targets = _dedupe_sorted(collected)
    _discovery_store(key, tuple(targets), None)
    return targets, None


def discover_prompt_agents(
    project_endpoint: Optional[str],
) -> Tuple[List[DiscoveredTarget], Optional[str]]:
    """Discover Foundry prompt agents addressable as ``<name>:<version>``."""
    return _discover(project_endpoint, "prompt", _collect_prompt_agents)


def discover_hosted_agents(
    project_endpoint: Optional[str],
) -> Tuple[List[DiscoveredTarget], Optional[str]]:
    """Discover Foundry hosted agents addressable by hosted endpoint URL."""
    return _discover(project_endpoint, "hosted", _collect_hosted_agents)


def discover_model_deployments(
    project_endpoint: Optional[str],
) -> Tuple[List[DiscoveredTarget], Optional[str]]:
    """Discover Foundry model deployments addressable as ``model:<name>``."""
    return _discover(project_endpoint, "model", _collect_model_deployments)
