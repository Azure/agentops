"""Scope-bounded discovery of Foundry and linked telemetry resources."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Optional, Sequence, Union

from agentops.core.observe import ObserveScope, ResourceInventory, TelemetrySource, canonical_arm_id


_APPINSIGHTS_PROVIDER_PATH = "/providers/microsoft.insights/components/"

#: How long a discovered :class:`~agentops.core.observe.ResourceInventory`
#: remains valid before Resource Graph and connection metadata are re-read.
DISCOVERY_CACHE_TTL_SECONDS = 15 * 60

_FOUNDRY_ACCOUNT_TYPE = "microsoft.cognitiveservices/accounts"
_FOUNDRY_PROJECT_TYPE = "microsoft.cognitiveservices/accounts/projects"

ConnectionsByProject = Union[Mapping[str, Any], Callable[[str], Any]]


def discover_appinsights_resource_ids(connections: Any) -> tuple[str, ...]:
    """Return App Insights ARM IDs from credential-free connection metadata."""
    list_connections = getattr(connections, "list", None)
    if not callable(list_connections):
        return ()

    discovered: list[str] = []
    for connection in list_connections():
        connection_type = str(getattr(connection, "type", "") or "").lower()
        target = str(getattr(connection, "target", "") or "").strip()
        if (
            "application_insights" not in connection_type
            and "applicationinsights" not in connection_type
        ):
            continue
        if _APPINSIGHTS_PROVIDER_PATH not in target.lower():
            continue
        discovered.append(canonical_arm_id(target))
    return tuple(dict.fromkeys(discovered))


def resolve_log_analytics_workspace_resource_id(
    appinsights_resource_id: str,
    application_insights_client: Any,
) -> Optional[str]:
    """Resolve the workspace linked to an Application Insights component."""
    try:
        canonical_id = canonical_arm_id(appinsights_resource_id)
    except ValueError:
        return None
    lower_id = canonical_id.lower()
    if _APPINSIGHTS_PROVIDER_PATH not in lower_id:
        return None

    segments = canonical_id.strip("/").split("/")
    try:
        resource_group = segments[segments.index("resourceGroups") + 1]
    except (ValueError, IndexError):
        lower_segments = [segment.lower() for segment in segments]
        try:
            resource_group = segments[lower_segments.index("resourcegroups") + 1]
        except (ValueError, IndexError):
            return None
    component_name = segments[-1]
    components = getattr(application_insights_client, "components", None)
    get_component = getattr(components, "get", None)
    if not callable(get_component):
        return None
    component = get_component(
        resource_group_name=resource_group,
        resource_name=component_name,
    )
    workspace_id = getattr(component, "workspace_resource_id", None)
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        return None
    return canonical_arm_id(workspace_id)


def _path_segment_value(resource_id: str, key: str) -> Optional[str]:
    segments = resource_id.strip("/").split("/")
    lower_segments = [segment.lower() for segment in segments]
    try:
        return segments[lower_segments.index(key) + 1]
    except (ValueError, IndexError):
        return None


def _foundry_account_id_for_project(project_resource_id: str) -> Optional[str]:
    """Return the parent Foundry account ARM ID embedded in a project ID."""
    segments = project_resource_id.strip("/").split("/")
    lower_segments = [segment.lower() for segment in segments]
    try:
        index = lower_segments.index("projects")
    except ValueError:
        return None
    if index == 0:
        return None
    return "/" + "/".join(segments[:index])


def subscription_ids_for_scope(scope: ObserveScope) -> tuple[str, ...]:
    """Return the distinct subscription IDs implied by *scope*."""
    if scope.mode == "projects":
        ids = (
            _path_segment_value(project_id, "subscriptions")
            for project_id in scope.project_resource_ids
        )
        return tuple(dict.fromkeys(sub_id for sub_id in ids if sub_id))
    if scope.root_resource_id:
        sub_id = _path_segment_value(scope.root_resource_id, "subscriptions")
        return (sub_id,) if sub_id else ()
    return ()


def _scope_where_clauses(scope: ObserveScope) -> list[str]:
    """Return Resource Graph ``where`` clauses that bound a query to *scope*."""
    root = scope.root_resource_id
    if not root:
        return []
    if scope.mode == "foundry":
        return [f"(id =~ '{root}' or id startswith '{root}/')"]
    if scope.mode == "resource_group":
        resource_group = _path_segment_value(root, "resourcegroups")
        subscription_id = _path_segment_value(root, "subscriptions")
        clauses = []
        if subscription_id:
            clauses.append(f"subscriptionId =~ '{subscription_id}'")
        if resource_group:
            clauses.append(f"resourceGroup =~ '{resource_group}'")
        return clauses
    if scope.mode == "subscription":
        subscription_id = _path_segment_value(root, "subscriptions")
        return [f"subscriptionId =~ '{subscription_id}'"] if subscription_id else []
    return []


def _resource_graph_query(resource_type: str, where_clauses: Sequence[str]) -> str:
    """Build a bounded Resource Graph KQL query for *resource_type*."""
    clauses = [f"type =~ '{resource_type}'", *where_clauses]
    where = " | where ".join(clauses)
    return f"Resources | where {where} | project id, name, resourceGroup, subscriptionId, kind, properties"


def _safe_failure_reason(exc: Exception, *, context: str) -> str:
    """Map a discovery exception to a short, safe, actionable reason."""
    text = str(exc)
    lower = text.lower()
    if any(
        marker in lower
        for marker in ("forbidden", "authorizationfailed", "does not have authorization")
    ):
        return (
            f"{context} is not accessible with the current identity. "
            "Grant Reader on the target subscription or resource group."
        )
    if any(marker in lower for marker in ("throttl", "toomanyrequests", "429")):
        return f"{context} was throttled by Azure Resource Graph. Retry shortly."
    snippet = text.splitlines()[0].strip() if text else type(exc).__name__
    if len(snippet) > 200:
        snippet = snippet[:197] + "..."
    return f"{context} failed ({type(exc).__name__}: {snippet})."


def _run_resource_graph_query(
    resource_graph_client: Any,
    *,
    query: str,
    subscriptions: Sequence[str],
    source: str,
) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
    try:
        response = resource_graph_client.resources(query=query, subscriptions=list(subscriptions))
    except Exception as exc:  # noqa: BLE001 -- recorded as a partial failure, never raised
        return [], {"source": source, "reason": _safe_failure_reason(exc, context=source)}

    data = getattr(response, "data", None)
    if data is None and isinstance(response, Mapping):
        data = response.get("data")
    if not isinstance(data, list):
        return [], {
            "source": source,
            "reason": f"{source} returned an unexpected response shape.",
        }
    return list(data), None


def discover_scoped_foundry_resources(
    scope: ObserveScope,
    *,
    resource_graph_client: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(foundry_accounts, foundry_projects, partial_failures)`` in *scope*.

    When *scope* is already expressed as explicit Foundry project IDs
    (``mode == "projects"``) no Resource Graph query is issued -- those
    project IDs are the discovery result. Otherwise this issues one
    scope-bounded query for Foundry accounts and a separate one for Foundry
    projects; either query's failure is caught independently and recorded in
    ``partial_failures`` instead of raised, so a denied or slow query for one
    resource type never blocks results for the other.
    """
    if scope.mode == "projects":
        projects = [{"id": project_id} for project_id in scope.project_resource_ids]
        return [], projects, []

    subscriptions = subscription_ids_for_scope(scope)
    if not subscriptions:
        return [], [], []

    where_clauses = _scope_where_clauses(scope)
    accounts, accounts_failure = _run_resource_graph_query(
        resource_graph_client,
        query=_resource_graph_query(_FOUNDRY_ACCOUNT_TYPE, where_clauses),
        subscriptions=subscriptions,
        source="resource_graph_accounts",
    )
    projects, projects_failure = _run_resource_graph_query(
        resource_graph_client,
        query=_resource_graph_query(_FOUNDRY_PROJECT_TYPE, where_clauses),
        subscriptions=subscriptions,
        source="resource_graph_projects",
    )
    partial_failures = [
        failure for failure in (accounts_failure, projects_failure) if failure is not None
    ]

    def _within_scope(resource: Mapping[str, Any]) -> bool:
        resource_id = resource.get("id")
        if not isinstance(resource_id, str) or not resource_id:
            return False
        try:
            return scope.contains(resource_id)
        except ValueError:
            return False

    accounts = [account for account in accounts if _within_scope(account)]
    projects = [project for project in projects if _within_scope(project)]
    return accounts, projects, partial_failures


def _resolve_project_telemetry_source(
    project_resource_id: str,
    *,
    connections_by_project: ConnectionsByProject,
    application_insights_client: Any,
) -> TelemetrySource:
    canonical_project = canonical_arm_id(project_resource_id)
    foundry_resource_id = _foundry_account_id_for_project(canonical_project)

    try:
        connections = (
            connections_by_project(canonical_project)
            if callable(connections_by_project)
            else connections_by_project.get(canonical_project)
        )
    except Exception as exc:  # noqa: BLE001
        return TelemetrySource(
            source_id=canonical_project,
            resource_id=canonical_project,
            foundry_resource_id=foundry_resource_id,
            project_resource_ids=[canonical_project],
            state="error",
            reason=_safe_failure_reason(exc, context="Telemetry connection lookup"),
        )

    not_configured_reason = "No Application Insights connection is linked to this project."
    if connections is None:
        return TelemetrySource(
            source_id=canonical_project,
            resource_id=canonical_project,
            foundry_resource_id=foundry_resource_id,
            project_resource_ids=[canonical_project],
            state="not_configured",
            reason=not_configured_reason,
        )

    appinsights_ids = discover_appinsights_resource_ids(connections)
    if not appinsights_ids:
        return TelemetrySource(
            source_id=canonical_project,
            resource_id=canonical_project,
            foundry_resource_id=foundry_resource_id,
            project_resource_ids=[canonical_project],
            state="not_configured",
            reason=not_configured_reason,
        )

    appinsights_id = appinsights_ids[0]
    try:
        workspace_id = resolve_log_analytics_workspace_resource_id(
            appinsights_id, application_insights_client
        )
    except Exception as exc:  # noqa: BLE001
        return TelemetrySource(
            source_id=appinsights_id,
            resource_id=appinsights_id,
            foundry_resource_id=foundry_resource_id,
            project_resource_ids=[canonical_project],
            state="inaccessible",
            reason=_safe_failure_reason(exc, context="Log Analytics workspace lookup"),
        )

    if workspace_id is None:
        return TelemetrySource(
            source_id=appinsights_id,
            resource_id=appinsights_id,
            foundry_resource_id=foundry_resource_id,
            project_resource_ids=[canonical_project],
            state="not_configured",
            reason="The Application Insights component has no linked Log Analytics workspace.",
        )

    return TelemetrySource(
        source_id=workspace_id,
        resource_id=appinsights_id,
        workspace_id=workspace_id,
        foundry_resource_id=foundry_resource_id,
        project_resource_ids=[canonical_project],
        state="available",
    )


def dedupe_telemetry_sources(sources: Sequence[TelemetrySource]) -> list[TelemetrySource]:
    """Merge telemetry sources that share one Log Analytics workspace.

    Two Foundry projects commonly point at the same Application Insights /
    Log Analytics workspace; without deduplication that workspace would be
    queried twice and its results double-counted. Merging still preserves
    every contributing project's resource ID in ``project_resource_ids`` so
    attribution is never silently dropped by the merge.
    """
    merged: "OrderedDict[str, TelemetrySource]" = OrderedDict()
    for source in sources:
        key = source.workspace_id or f"{source.source_id}:{source.resource_id}"
        existing = merged.get(key)
        if existing is None:
            merged[key] = source
            continue
        combined_projects = list(
            dict.fromkeys([*existing.project_resource_ids, *source.project_resource_ids])
        )
        base = existing if existing.state == "available" or source.state != "available" else source
        merged[key] = base.model_copy(update={"project_resource_ids": combined_projects})
    return list(merged.values())


def build_telemetry_sources(
    project_resource_ids: Sequence[str],
    *,
    connections_by_project: ConnectionsByProject,
    application_insights_client: Any,
) -> list[TelemetrySource]:
    """Resolve every project's telemetry connection and dedupe shared workspaces."""
    raw_sources = [
        _resolve_project_telemetry_source(
            project_id,
            connections_by_project=connections_by_project,
            application_insights_client=application_insights_client,
        )
        for project_id in project_resource_ids
    ]
    return dedupe_telemetry_sources(raw_sources)


def build_resource_inventory(
    scope: ObserveScope,
    *,
    resource_graph_client: Any,
    connections_by_project: ConnectionsByProject,
    application_insights_client: Any,
    clock: Callable[[], datetime],
    ttl_seconds: float = DISCOVERY_CACHE_TTL_SECONDS,
) -> ResourceInventory:
    """Discover Foundry resources and their telemetry sources inside *scope*.

    Combines scope-bounded Resource Graph discovery with per-project
    telemetry connection resolution and shared-workspace deduplication into
    one :class:`~agentops.core.observe.ResourceInventory`, valid for
    *ttl_seconds* (15 minutes by default).
    """
    accounts, projects, partial_failures = discover_scoped_foundry_resources(
        scope, resource_graph_client=resource_graph_client
    )
    project_ids: list[str] = []
    for project in projects:
        raw_id = project.get("id")
        if isinstance(raw_id, str) and raw_id:
            try:
                project_ids.append(canonical_arm_id(raw_id))
            except ValueError:
                continue

    telemetry_sources = build_telemetry_sources(
        project_ids,
        connections_by_project=connections_by_project,
        application_insights_client=application_insights_client,
    )

    discovered_at = clock()
    expires_at = discovered_at + timedelta(seconds=ttl_seconds)
    return ResourceInventory(
        scope=scope,
        foundry_resources=accounts,
        projects=projects,
        telemetry_sources=telemetry_sources,
        discovered_at=discovered_at,
        expires_at=expires_at,
        partial_failures=partial_failures,
    )
