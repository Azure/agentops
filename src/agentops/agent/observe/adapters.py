"""Lazy-imported Azure SDK adapters for :mod:`agentops.agent.observe`.

This module is the *only* place in ``agentops.agent.observe`` that imports
concrete Azure SDK packages (``azure-mgmt-resourcegraph``,
``azure-mgmt-applicationinsights``, ``azure-monitor-query``,
``azure-ai-projects``). Every import is deferred to inside a method body so
importing :mod:`agentops.agent.observe.adapters` itself never requires those
packages to be installed, and importing any other module in this package
never pulls in the Azure SDKs transitively.

:mod:`agentops.agent.observe.discovery` and
:mod:`agentops.agent.observe.queries` are written entirely against small
duck-typed client protocols (``resource_graph_client.resources(...)``,
``application_insights_client.components.get(...)``,
``connections_by_project(project_id).list()``,
``client.query_batch(requests)``) precisely so they never need to import an
Azure SDK and stay trivially testable with fakes. The classes in this module
translate those duck types to and from the real, network-calling Azure SDK
clients, and are exercised in tests with fakes that imitate the *real* SDK's
response shapes (rather than the already-tested duck types), so the
translation itself is what is covered.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import (
    Any,
    Callable,
    Iterable,
    Iterator,
    Literal,
    Mapping,
    Sequence,
    TypeVar,
)

from agentops.agent.observe.discovery import (
    build_resource_inventory,
    subscription_ids_for_scope,
)
from agentops.agent.observe.queries import (
    DEFAULT_REQUEST_DEADLINE_SECONDS,
    MAX_SOURCES_PER_BATCH,
    SOURCE_TIMEOUT_SECONDS,
    SourceQuery,
    SourceResult,
    build_agent_detail_query,
    build_agents_query,
    build_department_usage_query,
    build_models_query,
    build_overview_query,
    build_runs_query,
    build_tools_query,
    build_user_usage_query,
    execute_source_batch,
)
from agentops.core.attribution import (
    AttributionConfiguration,
    AttributionUsage,
    derive_pseudonymous_user_key,
)
from agentops.core.cost import CostComponent
from agentops.core.observe import (
    ObserveFilterState,
    ObserveScope,
    ResourceInventory,
    TelemetrySource,
    UserAttributionCoverage,
)

logger = logging.getLogger(__name__)

_T = TypeVar("_T")
View = Literal["overview", "agents", "models", "tools", "runs", "cost"]

_VIEW_QUERY_BUILDERS: dict[View, Callable[..., str]] = {
    "overview": build_overview_query,
    "agents": build_agents_query,
    "models": build_models_query,
    "tools": build_tools_query,
    "runs": build_runs_query,
}

_AGGREGATE_IDENTITY_FIELDS = frozenset(
    {
        "authenticatedid",
        "effectiveidentity",
        "enduserid",
        "otelenduserid",
        "rawidentity",
        "userauthenticatedid",
        "userid",
        "userkey",
    }
)


@dataclass(frozen=True)
class AggregateDepartmentUsageRow:
    """Privacy-safe normalized row returned by an aggregate department query."""

    source_id: str
    source_resource_id: str
    department_id: str | None
    department_label: str | None
    mapping_state: Literal["mapped", "unmapped", "ambiguous"]
    member_count: int
    usage: AttributionUsage
    eligible_records: int | None = None
    identified_records: int | None = None
    mapped_records: int | None = None
    unattributed_records: int | None = None
    ambiguous_records: int | None = None
    returned_records: int | None = None
    metadata_only: bool = False
    principal_member_present: int = 0
    identity_state: str | None = None
    project_resource_id: str | None = None
    agent_key: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    provider_name: str | None = None
    system: str | None = None
    deployment: str | None = None
    model: str | None = None
    tool_name: str | None = None
    operation_name: str | None = None


@dataclass(frozen=True)
class DelegatedUserUsageRow:
    """Normalized delegated row; raw identity must not cross aggregate paths."""

    source_id: str
    source_resource_id: str
    row_kind: Literal["user", "other_users", "unattributed"]
    user_key: str | None
    raw_identity: str | None
    rank: int | None
    distinct_users: int
    usage: AttributionUsage
    project_resource_id: str | None = None
    agent_key: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    provider_name: str | None = None
    system: str | None = None
    deployment: str | None = None
    model: str | None = None
    tool_name: str | None = None
    operation_name: str | None = None


def _non_negative_int(value: Any, *, field: str, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool):
        raise ValueError(
            f"aggregate attribution {field} must be a non-negative integer"
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"aggregate attribution {field} must be a non-negative integer"
        ) from exc
    if parsed < 0 or (isinstance(value, float) and not value.is_integer()):
        raise ValueError(
            f"aggregate attribution {field} must be a non-negative integer"
        )
    return parsed


def _nullable_decimal_text(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"aggregate attribution {field} must be non-negative")
    try:
        parsed = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"aggregate attribution {field} must be non-negative") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"aggregate attribution {field} must be non-negative")
    return format(parsed, "f")


def _optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _reject_aggregate_identity_fields(row: Mapping[str, Any]) -> None:
    for field, value in row.items():
        normalized = re.sub(r"[^a-z0-9]", "", str(field).lower())
        if any(
            normalized.endswith(identity) for identity in _AGGREGATE_IDENTITY_FIELDS
        ):
            raise ValueError("aggregate attribution row violates the privacy boundary")
        if isinstance(value, Mapping):
            _reject_aggregate_identity_fields(value)


def normalize_department_usage_row(
    row: Mapping[str, Any],
    *,
    source: TelemetrySource,
) -> AggregateDepartmentUsageRow:
    """Normalize one aggregate row and fail closed if identity crossed KQL."""
    _reject_aggregate_identity_fields(row)
    mapping_state = row.get("mapping_state")
    if mapping_state not in {"mapped", "unmapped", "ambiguous"}:
        raise ValueError("aggregate attribution row has an invalid mapping state")

    department_id = row.get("department_id") or None
    department_label = row.get("department_label") or None
    if department_id is not None and (
        not isinstance(department_id, str) or not department_id.strip()
    ):
        raise ValueError("aggregate attribution department ID must be text")
    if department_label is not None and (
        not isinstance(department_label, str) or not department_label.strip()
    ):
        raise ValueError("aggregate attribution department label must be text")
    if (department_id is None) != (department_label is None):
        raise ValueError("aggregate attribution department ID and label must be paired")
    if mapping_state == "mapped" and department_id is None:
        raise ValueError("mapped aggregate attribution row requires a department")
    if mapping_state != "mapped" and department_id is not None:
        raise ValueError("unmapped aggregate attribution row forbids a department")

    member_count = _non_negative_int(row.get("member_count"), field="member_count")
    assert member_count is not None
    if mapping_state == "mapped" and member_count == 0:
        raise ValueError("mapped aggregate attribution department must have members")
    principal_member_present = _non_negative_int(
        row.get("principal_member_present", 0),
        field="principal_member_present",
    )
    assert principal_member_present is not None
    if principal_member_present > 1 or principal_member_present > member_count:
        raise ValueError(
            "aggregate attribution principal membership must be zero or one"
        )

    usage = AttributionUsage(
        invocations=_non_negative_int(row.get("invocations"), field="invocations"),
        input_tokens=_non_negative_int(
            row.get("input_tokens"), field="input_tokens", nullable=True
        ),
        output_tokens=_non_negative_int(
            row.get("output_tokens"), field="output_tokens", nullable=True
        ),
        tool_invocations=_non_negative_int(
            row.get("tool_invocations"), field="tool_invocations", nullable=True
        ),
        active_session_seconds=_nullable_decimal_text(
            row.get("active_session_seconds"), field="active_session_seconds"
        ),
    )
    counters = {
        field: _non_negative_int(row.get(field), field=field, nullable=True)
        for field in (
            "eligible_records",
            "identified_records",
            "mapped_records",
            "unattributed_records",
            "ambiguous_records",
            "returned_records",
        )
    }
    return AggregateDepartmentUsageRow(
        source_id=source.source_id,
        source_resource_id=source.foundry_resource_id or source.resource_id,
        department_id=department_id.strip() if department_id else None,
        department_label=department_label.strip() if department_label else None,
        mapping_state=mapping_state,
        member_count=member_count,
        usage=usage,
        metadata_only=row.get("_metadata_only") is True,
        principal_member_present=principal_member_present,
        identity_state=_optional_text(row.get("identity_state")),
        project_resource_id=_optional_text(row.get("project_resource_id")),
        agent_key=_optional_text(row.get("agent_key")),
        agent_id=_optional_text(row.get("agent_id")),
        agent_name=_optional_text(row.get("agent_name")),
        provider_name=_optional_text(row.get("provider_name")),
        system=_optional_text(row.get("system")),
        deployment=_optional_text(row.get("deployment")),
        model=_optional_text(row.get("model")),
        tool_name=_optional_text(row.get("tool_name")),
        operation_name=_optional_text(row.get("operation_name")),
        **counters,
    )


# Alternate descriptive spelling retained for integration callers.
normalize_aggregate_department_usage_row = normalize_department_usage_row


def normalize_user_usage_row(
    row: Mapping[str, Any],
    *,
    source: TelemetrySource,
    config: AttributionConfiguration,
    tenant_id: str,
    access_boundary: Literal["aggregate", "delegated"],
) -> DelegatedUserUsageRow:
    """Normalize one user row and enforce the delegated privacy boundary."""
    if access_boundary != "delegated":
        raise ValueError("identity-bearing attribution rows require delegated access")
    if (
        not config.enabled
        or config.deployment_namespace is None
        or config.generation is None
    ):
        raise ValueError("user attribution requires an enabled configuration")
    row_kind = row.get("row_kind")
    if row_kind not in {"user", "other_users", "unattributed"}:
        raise ValueError("delegated attribution row has an invalid row kind")
    raw_identity = row.get("raw_identity") or None
    user_key = row.get("user_key") or None
    rank = _non_negative_int(row.get("user_rank"), field="user_rank", nullable=True)
    distinct_users = _non_negative_int(
        row.get("distinct_users", 1 if row_kind == "user" else 0),
        field="distinct_users",
    )
    assert distinct_users is not None
    if row_kind == "user":
        if not isinstance(raw_identity, str) or not raw_identity.strip():
            raise ValueError("delegated user row requires a raw identity")
        if not isinstance(user_key, str):
            raise ValueError("delegated user row requires a pseudonymous key")
        expected = derive_pseudonymous_user_key(
            deployment_namespace=config.deployment_namespace,
            generation=config.generation,
            tenant_id=tenant_id,
            raw_identity=raw_identity,
        )
        if user_key != expected:
            raise ValueError(
                "delegated user row pseudonymous key does not match identity"
            )
        raw_identity = raw_identity.strip()
        if rank is None or rank < 1:
            raise ValueError("delegated user row requires a positive rank")
        distinct_users = 1
    elif raw_identity is not None or user_key is not None or rank is not None:
        raise ValueError("aggregate delegated rows must not contain identity")
    elif row_kind == "other_users" and distinct_users < 1:
        raise ValueError("other_users row requires an omitted user count")
    elif row_kind == "unattributed" and distinct_users != 0:
        raise ValueError("unattributed row cannot contain identified users")

    return DelegatedUserUsageRow(
        source_id=source.source_id,
        source_resource_id=source.foundry_resource_id or source.resource_id,
        row_kind=row_kind,
        user_key=user_key,
        raw_identity=raw_identity,
        rank=rank,
        distinct_users=distinct_users,
        project_resource_id=_optional_text(row.get("project_resource_id")),
        agent_key=_optional_text(row.get("agent_key")),
        agent_id=_optional_text(row.get("agent_id")),
        agent_name=_optional_text(row.get("agent_name")),
        provider_name=_optional_text(row.get("provider_name")),
        system=_optional_text(row.get("system")),
        deployment=_optional_text(row.get("deployment")),
        model=_optional_text(row.get("model")),
        tool_name=_optional_text(row.get("tool_name")),
        operation_name=_optional_text(row.get("operation_name")),
        usage=AttributionUsage(
            invocations=_non_negative_int(row.get("invocations"), field="invocations"),
            input_tokens=_non_negative_int(
                row.get("input_tokens"), field="input_tokens", nullable=True
            ),
            output_tokens=_non_negative_int(
                row.get("output_tokens"), field="output_tokens", nullable=True
            ),
            tool_invocations=_non_negative_int(
                row.get("tool_invocations"), field="tool_invocations", nullable=True
            ),
            active_session_seconds=_nullable_decimal_text(
                row.get("active_session_seconds"), field="active_session_seconds"
            ),
        ),
    )


normalize_delegated_user_usage_row = normalize_user_usage_row


_ATTRIBUTION_COUNTER_FIELDS = (
    "eligible_records",
    "identified_records",
    "mapped_records",
    "unattributed_records",
    "ambiguous_records",
    "returned_records",
)

_ATTRIBUTION_COVERAGE_TEXT = {
    "available": (
        "Supported user identity and department mapping cover this source.",
        "No action needed.",
    ),
    "partial": (
        "Only part of this source could be attributed to a department.",
        "Add missing explicit mappings and verify authenticated identity telemetry.",
    ),
    "not_reported": (
        "Telemetry records do not report a supported authenticated user identity.",
        "Emit UserAuthenticatedId or the OpenTelemetry enduser.id attribute.",
    ),
    "ambiguous": (
        "Conflicting identity or department evidence prevents safe attribution.",
        "Remove conflicting aliases or mappings; ambiguous records remain unattributed.",
    ),
    "no_data": (
        "No eligible telemetry records were reported for this source and period.",
        "Widen the time range or verify that the agent emitted telemetry.",
    ),
    "inaccessible": (
        "This telemetry source could not be read.",
        "Verify source access and the configured Observe scope.",
    ),
    "error": (
        "The attribution query did not complete successfully.",
        "Review source health and retry the attribution query.",
    ),
}


def _coverage_row_value(row: Any, field: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(field)
    return getattr(row, field, None)


def _normalize_attribution_counters(
    rows: Sequence[Any] | None,
) -> dict[str, int] | None:
    """Read one repeated KQL counter set without manufacturing missing zeroes."""
    if not rows:
        return None

    counter_sets: list[dict[str, int]] = []
    for row in rows:
        _reject_aggregate_identity_fields(
            row if isinstance(row, Mapping) else vars(row)
        )
        values = {
            field: _coverage_row_value(row, field)
            for field in _ATTRIBUTION_COUNTER_FIELDS
        }
        present = {field for field, value in values.items() if value is not None}
        if present and len(present) != len(_ATTRIBUTION_COUNTER_FIELDS):
            raise ValueError(
                "aggregate attribution coverage must report all six counters"
            )
        if not present:
            continue
        counter_sets.append(
            {
                field: int(_non_negative_int(value, field=field))
                for field, value in values.items()
            }
        )

    if not counter_sets:
        return None
    first = counter_sets[0]
    if any(counters != first for counters in counter_sets[1:]):
        raise ValueError(
            "aggregate attribution coverage counters must be consistent across rows"
        )
    if first["identified_records"] > first["eligible_records"]:
        raise ValueError(
            "identified attribution records cannot exceed eligible records"
        )
    if first["mapped_records"] > first["identified_records"]:
        raise ValueError("mapped attribution records cannot exceed identified records")
    if first["unattributed_records"] > first["eligible_records"]:
        raise ValueError("unattributed records cannot exceed eligible records")
    if first["ambiguous_records"] > first["unattributed_records"]:
        raise ValueError("ambiguous records must remain unattributed")
    if (
        first["mapped_records"] + first["unattributed_records"]
        != first["eligible_records"]
    ):
        raise ValueError(
            "mapped and unattributed records must reconcile to eligible records"
        )
    return first


def normalize_user_attribution_coverage(
    *,
    source: TelemetrySource,
    status: Literal["success", "partial", "timeout", "throttled", "error"] | None,
    rows: Sequence[Any] | None,
    metric: Literal["usage", "cost"],
    attribution_level: Literal["department", "user"],
    refreshed_at: datetime,
    component_id: str | None = None,
) -> UserAttributionCoverage:
    """Return one strict, identity-free per-source attribution coverage record."""
    if metric == "usage" and component_id is not None:
        raise ValueError("usage attribution coverage forbids component_id")
    if metric == "cost" and not component_id:
        raise ValueError("cost attribution coverage requires component_id")

    counters: dict[str, int] | None = None
    if source.state != "available":
        state = "inaccessible"
    elif status in {"timeout", "throttled", "error", None}:
        state = "error"
    else:
        counters = _normalize_attribution_counters(rows)
        if counters is None:
            state = "partial" if status == "partial" else "error"
        elif status == "partial":
            state = "partial"
        elif counters["eligible_records"] == 0:
            state = "no_data"
        elif counters["identified_records"] == 0:
            state = "not_reported"
        elif counters["ambiguous_records"]:
            state = "ambiguous" if counters["mapped_records"] == 0 else "partial"
        elif counters["mapped_records"] < counters["eligible_records"]:
            state = "partial"
        else:
            state = "available"

    reason, next_action = _ATTRIBUTION_COVERAGE_TEXT[state]
    if source.state == "available" and status == "success" and counters is None:
        reason = "The source did not return attribution counters."
        next_action = "Verify the attribution query projection and retry."
    null_counts = {field: None for field in _ATTRIBUTION_COUNTER_FIELDS}
    return UserAttributionCoverage(
        source_id=source.source_id,
        dimension="user_attribution",
        state=state,
        reason=reason,
        next_action=next_action,
        refreshed_at=refreshed_at,
        component_id=component_id,
        metric=metric,
        attribution_level=attribution_level,
        **(counters or null_counts),
    )


def _chunked(items: Sequence[_T], size: int) -> Iterator[list[_T]]:
    """Yield *items* in consecutive chunks of at most *size* elements."""
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


# ---------------------------------------------------------------------------
# Resource Graph discovery adapter (T036/T037): App Service has no ``az``
# CLI, so scope-bounded discovery must go through ``azure-mgmt-resourcegraph``
# rather than shelling out to ``az graph query``.
# ---------------------------------------------------------------------------


class _ResourceGraphAdapter:
    """Adapts ``azure-mgmt-resourcegraph`` to ``discovery.py``'s
    ``resource_graph_client.resources(query=, subscriptions=)`` duck type.
    """

    def __init__(self, *, credential: Any) -> None:
        self._credential = credential
        self._client: Any = None

    def _client_instance(self) -> Any:
        if self._client is None:
            from azure.mgmt.resourcegraph import (
                ResourceGraphClient,
            )  # lazy Azure import

            self._client = ResourceGraphClient(self._credential)
        return self._client

    def resources(self, *, query: str, subscriptions: Sequence[str]) -> Any:
        from azure.mgmt.resourcegraph.models import (  # lazy Azure import
            QueryRequest,
            QueryRequestOptions,
        )

        request = QueryRequest(
            subscriptions=list(subscriptions),
            query=query,
            options=QueryRequestOptions(top=1000),
        )
        return self._client_instance().resources(request)


# ---------------------------------------------------------------------------
# Application Insights component -> Log Analytics workspace resolution.
# ---------------------------------------------------------------------------


class _ApplicationInsightsAdapter:
    """Binds ``azure-mgmt-applicationinsights`` to one subscription and
    exposes ``.components`` so ``discovery.py`` can call
    ``application_insights_client.components.get(resource_group_name=,
    resource_name=)`` unchanged.
    """

    def __init__(self, *, credential: Any, subscription_id: str) -> None:
        self._credential = credential
        self._subscription_id = subscription_id
        self._client: Any = None

    def _client_instance(self) -> Any:
        if self._client is None:
            from azure.mgmt.applicationinsights import (  # lazy Azure import
                ApplicationInsightsManagementClient,
            )

            self._client = ApplicationInsightsManagementClient(
                self._credential, self._subscription_id
            )
        return self._client

    @property
    def components(self) -> Any:
        return self._client_instance().components


# ---------------------------------------------------------------------------
# Foundry project connection metadata (credential-free; no delegated
# access -- discovering *that* a connection exists is project metadata, not
# telemetry content).
# ---------------------------------------------------------------------------


def _project_endpoint_from_arm_id(project_resource_id: str) -> str | None:
    """Return the Foundry project endpoint for a project ARM ID, or ``None``.

    Inverse of the ``https://<account>.services.ai.azure.com/api/projects/<project>``
    -> ARM ID mapping used across AgentOps (see ``infra/e2e/bootstrap.bicep``).
    """
    segments = project_resource_id.strip("/").split("/")
    lower_segments = [segment.lower() for segment in segments]
    try:
        accounts_index = lower_segments.index("accounts")
        projects_index = lower_segments.index("projects")
    except ValueError:
        return None
    if projects_index <= accounts_index or projects_index + 1 >= len(segments):
        return None
    account_name = segments[accounts_index + 1]
    project_name = segments[projects_index + 1]
    if not account_name or not project_name:
        return None
    return f"https://{account_name}.services.ai.azure.com/api/projects/{project_name}"


def _default_project_connections(project_resource_id: str, *, credential: Any) -> Any:
    """Return the ``.connections`` accessor for one Foundry project, or ``None``."""
    endpoint = _project_endpoint_from_arm_id(project_resource_id)
    if endpoint is None:
        return None
    from azure.ai.projects import AIProjectClient  # lazy Azure import

    client = AIProjectClient(endpoint=endpoint, credential=credential)
    return client.connections


# ---------------------------------------------------------------------------
# AzureDiscoveryClient: implements service.py's ``DiscoveryClient`` protocol.
# ---------------------------------------------------------------------------


class AzureDiscoveryClient:
    """Scope-bounded Foundry/telemetry discovery over real Azure SDK clients.

    Discovery itself (``discovery.py``) is synchronous -- Resource Graph and
    Application Insights management calls are blocking network calls with no
    async client -- so :meth:`discover` runs it on a worker thread via
    :func:`asyncio.to_thread` to keep the event loop free.

    Known limitation: ``discovery.py``'s ``application_insights_client`` is a
    single client bound to one subscription. When ``scope.mode == "projects"``
    spans multiple subscriptions, this adapter binds to the *first*
    subscription discovered across the configured project IDs; workspace
    resolution for projects in any other subscription will safely report
    ``not_configured`` rather than raise. Fixing this would require changing
    ``discovery.py``'s contract, which is out of scope for this adapter.
    """

    def __init__(
        self,
        *,
        credential: Any,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._credential = credential
        self._clock = clock
        self._resource_graph_client = _ResourceGraphAdapter(credential=credential)
        self._app_insights_clients: dict[str, _ApplicationInsightsAdapter] = {}

    def _app_insights_client_for(self, subscription_id: str | None) -> Any:
        if subscription_id is None:
            return None
        client = self._app_insights_clients.get(subscription_id)
        if client is None:
            client = _ApplicationInsightsAdapter(
                credential=self._credential, subscription_id=subscription_id
            )
            self._app_insights_clients[subscription_id] = client
        return client

    async def discover(self, scope: ObserveScope) -> ResourceInventory:
        return await asyncio.to_thread(self.discover_sync, scope)

    def discover_sync(self, scope: ObserveScope) -> ResourceInventory:
        """Discover inventory synchronously for CLI preflight callers."""
        subscriptions = subscription_ids_for_scope(scope)
        application_insights_client = self._app_insights_client_for(
            subscriptions[0] if subscriptions else None
        )
        connections_by_project = functools.partial(
            _default_project_connections, credential=self._credential
        )
        return build_resource_inventory(
            scope,
            resource_graph_client=self._resource_graph_client,
            connections_by_project=connections_by_project,
            application_insights_client=application_insights_client,
            clock=self._clock,
        )


# ---------------------------------------------------------------------------
# Logs Query batch adapter (T044): flattens the real
# ``azure.monitor.query(.aio)`` response shapes into the flat dict rows
# ``queries.py``'s ``_classify_batch_item``/``service.py``'s
# ``normalize_agent_row``/``normalize_model_row`` expect.
# ---------------------------------------------------------------------------


class _FlattenedBatchItem:
    """Duck-typed batch-item shape consumed by ``queries._classify_batch_item``."""

    __slots__ = ("error", "partial_error", "partial_data", "tables", "status")

    def __init__(
        self,
        *,
        error: Any = None,
        partial_error: Any = None,
        partial_data: Any = None,
        tables: Any = None,
        status: Any = None,
    ) -> None:
        self.error = error
        self.partial_error = partial_error
        self.partial_data = partial_data
        self.tables = tables
        self.status = status


def _flatten_logs_table(table: Any) -> list[dict[str, Any]]:
    """Flatten one ``LogsTable``-shaped object (``.columns``/``.rows``) into dict rows.

    Aggregate metadata such as ``total_in_scope`` is preserved verbatim for
    the service layer to calculate bounds; absent metadata is never invented.
    """
    raw_columns = getattr(table, "columns", None) or []
    column_names = [
        column if isinstance(column, str) else str(getattr(column, "name", column))
        for column in raw_columns
    ]
    rows = getattr(table, "rows", None) or []
    return [dict(zip(column_names, row)) for row in rows]


def _flatten_tables(tables: Any) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for table in tables or []:
        flattened.extend(_flatten_logs_table(table))
    return flattened


def _flatten_logs_query_response(response: Any) -> _FlattenedBatchItem:
    error = getattr(response, "error", None)
    if error is not None:
        return _FlattenedBatchItem(error=error)

    partial_error = getattr(response, "partial_error", None)
    tables = getattr(response, "tables", None)
    if partial_error is not None:
        partial_tables = _flatten_tables(
            getattr(response, "partial_data", None) or tables
        )
        return _FlattenedBatchItem(
            partial_error=partial_error,
            partial_data=partial_tables,
            tables=partial_tables,
        )

    return _FlattenedBatchItem(
        tables=_flatten_tables(tables), status=getattr(response, "status", None)
    )


def _query_workspace_rest(
    *,
    workspace_id: str,
    query: str,
    access_token: str,
    timeout_seconds: int,
) -> _FlattenedBatchItem:
    """Execute one Logs query when the SDK cannot decode the service response."""
    request = urllib.request.Request(
        f"https://api.loganalytics.io/v1/workspaces/{workspace_id}/query",
        data=json.dumps({"query": query}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))

    return _flatten_logs_rest_payload(payload)


def _flatten_logs_rest_payload(payload: Mapping[str, Any]) -> _FlattenedBatchItem:
    """Match the Azure SDK's Python value types for a Logs REST response."""
    rows: list[dict[str, Any]] = []
    for table in payload.get("tables", []):
        columns = table.get("columns", [])
        for values in table.get("rows", []):
            row: dict[str, Any] = {}
            for column, value in zip(columns, values):
                if column.get("type") == "datetime" and isinstance(value, str):
                    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
                row[column["name"]] = value
            rows.append(row)
    return _FlattenedBatchItem(tables=rows, status="SUCCESS")


class _LogsQueryAdapter:
    """Adapts ``azure.monitor.query.aio.LogsQueryClient.query_batch`` to
    ``queries.py``'s ``client.query_batch(requests)`` duck type, flattening
    each response's tables into plain dict rows.
    """

    def __init__(self, *, credential: Any) -> None:
        self._credential = credential
        self._client: Any = None

    def _client_instance(self) -> Any:
        if self._client is None:
            from azure.monitor.query.aio import LogsQueryClient  # lazy Azure import

            self._client = LogsQueryClient(self._credential)
        return self._client

    async def _query_individually(
        self, requests: Sequence[Any]
    ) -> list[_FlattenedBatchItem]:
        try:
            token = await self._credential.get_token(
                "https://api.loganalytics.io/.default"
            )
        except Exception as error:
            return [_FlattenedBatchItem(error=error) for _ in requests]

        responses = await asyncio.gather(
            *(
                asyncio.to_thread(
                    _query_workspace_rest,
                    workspace_id=request.workspace_id,
                    query=request.query,
                    access_token=token.token,
                    timeout_seconds=request.server_timeout_seconds,
                )
                for request in requests
            ),
            return_exceptions=True,
        )
        flattened: list[_FlattenedBatchItem] = []
        for response in responses:
            if isinstance(response, BaseException):
                if not isinstance(response, Exception):
                    raise response
                flattened.append(_FlattenedBatchItem(error=response))
            else:
                flattened.append(response)
        return flattened

    async def query_batch(self, requests: Sequence[Any]) -> list[_FlattenedBatchItem]:
        from azure.monitor.query import LogsBatchQuery  # lazy Azure import

        batch = [
            LogsBatchQuery(
                query=request.query,
                timespan=request.timespan,
                workspace_id=request.workspace_id,
                server_timeout=request.server_timeout_seconds,
            )
            for request in requests
        ]
        try:
            responses = await self._client_instance().query_batch(
                batch, headers={"Accept-Encoding": "identity"}
            )
        except UnicodeDecodeError:
            logger.warning(
                "Azure Monitor batch response could not be decoded; "
                "retrying Observe queries individually through the Logs REST endpoint"
            )
            return await self._query_individually(requests)
        return [_flatten_logs_query_response(response) for response in responses]

    async def aclose(self) -> None:
        if self._client is not None:
            close = getattr(self._client, "close", None)
            if callable(close):
                result = close()
                if asyncio.iscoroutine(result):
                    await result


# ---------------------------------------------------------------------------
# AzureQueryClient: implements service.py's ``QueryClient`` protocol, plus an
# extra ``query_agent_detail`` method used by the facade's T053 trend
# enrichment (not part of the strict ``QueryClient`` Protocol -- Protocols do
# not forbid extra methods, and the facade only calls it defensively via
# ``getattr``).
# ---------------------------------------------------------------------------


class AzureQueryClient:
    """Executes bounded, batched per-source telemetry queries (T044) over
    ``azure-monitor-query``, chunking any number of available sources into
    groups of at most :data:`~agentops.agent.observe.queries.MAX_SOURCES_PER_BATCH`.
    """

    def __init__(
        self,
        *,
        credential: Any,
        source_timeout_seconds: int = SOURCE_TIMEOUT_SECONDS,
        request_deadline_seconds: int = DEFAULT_REQUEST_DEADLINE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._source_timeout_seconds = source_timeout_seconds
        self._request_deadline_seconds = request_deadline_seconds
        self._clock = clock
        self._logs_client = _LogsQueryAdapter(credential=credential)

    async def _run(
        self,
        sources: Iterable[TelemetrySource],
        build_query: Callable[[TelemetrySource], str],
    ) -> list[SourceResult]:
        queryable = [source for source in sources if source.workspace_id]
        results: list[SourceResult] = []
        for chunk in _chunked(queryable, MAX_SOURCES_PER_BATCH):
            queries = [
                SourceQuery(
                    source_id=source.source_id,
                    workspace_id=source.workspace_id,
                    query=build_query(source),
                )
                for source in chunk
                if source.workspace_id
            ]
            if not queries:
                continue
            results.extend(
                await execute_source_batch(
                    queries,
                    client=self._logs_client,
                    source_timeout_seconds=self._source_timeout_seconds,
                    request_deadline_seconds=self._request_deadline_seconds,
                    clock=self._clock,
                )
            )
        return results

    async def query(
        self,
        sources: Sequence[TelemetrySource],
        filters: ObserveFilterState,
        *,
        view: View,
    ) -> list[SourceResult]:
        builder = _VIEW_QUERY_BUILDERS[view]
        return await self._run(
            sources, lambda source: builder(filters, scope_source=source)
        )

    async def query_agent_detail(
        self,
        sources: Sequence[TelemetrySource],
        filters: ObserveFilterState,
        *,
        agent_key: str,
    ) -> list[SourceResult]:
        """Bounded single-agent trend query batch (T053 facade enrichment)."""
        return await self._run(
            sources,
            lambda source: build_agent_detail_query(
                filters, agent_key=agent_key, scope_source=source
            ),
        )

    async def query_department_usage(
        self,
        sources: Sequence[TelemetrySource],
        filters: ObserveFilterState,
        *,
        config: AttributionConfiguration,
        tenant_id: str,
        department_id: str | None = None,
        principal_user_keys: Sequence[str] = (),
        cost_component: CostComponent | None = None,
    ) -> list[SourceResult]:
        """Run and normalize one aggregate attribution query per source."""
        results = await self._run(
            sources,
            lambda source: build_department_usage_query(
                filters,
                config=config,
                tenant_id=tenant_id,
                department_id=department_id,
                scope_source=source,
                principal_user_keys=principal_user_keys,
                cost_component=cost_component,
            ),
        )
        by_source = {source.source_id: source for source in sources}
        normalized: list[SourceResult] = []
        for result in results:
            source = by_source[result.source_id]
            tables = result.tables
            if tables is not None:
                if not isinstance(tables, Sequence) or isinstance(
                    tables, (str, bytes, bytearray)
                ):
                    raise ValueError(
                        "aggregate attribution query result must contain row mappings"
                    )
                rows: list[AggregateDepartmentUsageRow] = []
                for row in tables:
                    if not isinstance(row, Mapping):
                        raise ValueError(
                            "aggregate attribution query result must contain row mappings"
                        )
                    rows.append(normalize_department_usage_row(row, source=source))
                tables = rows
            normalized.append(
                SourceResult(
                    source_id=result.source_id,
                    status=result.status,
                    tables=tables,
                    reason=result.reason,
                    duration_ms=result.duration_ms,
                )
            )
        return normalized

    query_department_attribution = query_department_usage

    async def query_user_usage(
        self,
        sources: Sequence[TelemetrySource],
        filters: ObserveFilterState,
        *,
        config: AttributionConfiguration,
        tenant_id: str,
        department_id: str | None = None,
        selected_user_key: str | None = None,
        cost_component: CostComponent | None = None,
    ) -> list[SourceResult]:
        """Run delegated user queries and retain identity only in normalized rows."""
        results = await self._run(
            sources,
            lambda source: build_user_usage_query(
                filters,
                config=config,
                tenant_id=tenant_id,
                department_id=department_id,
                selected_user_key=selected_user_key,
                scope_source=source,
                cost_component=cost_component,
            ),
        )
        by_source = {source.source_id: source for source in sources}
        normalized: list[SourceResult] = []
        for result in results:
            source = by_source[result.source_id]
            tables = result.tables
            if tables is not None:
                if not isinstance(tables, Sequence) or isinstance(
                    tables, (str, bytes, bytearray)
                ):
                    raise ValueError(
                        "delegated attribution query result must contain row mappings"
                    )
                rows: list[DelegatedUserUsageRow] = []
                for row in tables:
                    if not isinstance(row, Mapping):
                        raise ValueError(
                            "delegated attribution query result must contain row mappings"
                        )
                    rows.append(
                        normalize_user_usage_row(
                            row,
                            source=source,
                            config=config,
                            tenant_id=tenant_id,
                            access_boundary="delegated",
                        )
                    )
                tables = rows
            normalized.append(
                SourceResult(
                    source_id=result.source_id,
                    status=result.status,
                    tables=tables,
                    reason=result.reason,
                    duration_ms=result.duration_ms,
                )
            )
        return normalized

    query_user_attribution = query_user_usage

    async def aclose(self) -> None:
        await self._logs_client.aclose()
