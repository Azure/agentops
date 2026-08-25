"""Deployment service for ``agentops cockpit deploy`` (issue #433, Phase 3).

This module implements the hosted-Cockpit deployment state machine described
in ``specs/011-deploy-hosted-cockpit/plan.md`` (Implementation Design, section
2) on top of the pure contracts already defined in
:mod:`agentops.core.observe` (``ObserveScope``, ``DeploymentSelection``,
``DeploymentPreview``, ``DeploymentJournal``, ``HostedCockpitDeployment``,
etc.). It intentionally does not import the Azure SDK, Azure CLI, or ``azd``
directly: every external effect (Azure/Graph context, permission checks, app
registration lookups, telemetry discovery, ``azd`` command execution, and
post-deploy health checks) is expressed as a small ``typing.Protocol`` so unit
tests can supply deterministic fakes.

Covered behavior (see the module docstrings of the three owning test files
for the FR-by-FR mapping):

* Selection precedence/ambiguity and workspace project resolution (FR-068).
* Observe scope validation, reusing ``ObserveScope`` from ``core.observe``.
* Deterministic deployment-name normalization.
* Deterministic role-assignment and federated-credential identity (FR-056,
  FR-059).
* ``authsettingsV2``-oriented, non-secret application settings (FR-060).
* Preview construction, merged with an injected ``azd``/Bicep preview
  adapter, and prerequisite validation through injectable adapters
  (FR-054, FR-055, FR-062, FR-063).
* Preview-before-mutation and guarded ``--yes`` / confirmation rules
  (FR-067, FR-070).
* Ordered ``azd provision --preview`` / ``azd provision`` / FIC
  reconciliation / ``azd deploy`` orchestration through an injectable
  command runner.
* Versioned deployment-journal persistence, reconciliation, and resume
  semantics under ``.agentops/deploy/cockpit/`` (FR-010A-F).
* Post-deployment health verification and rerun idempotency (FR-071).
* Actionable, stage-tagged errors that never silently discard Azure
  mutation state (FR-062, FR-063).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Protocol, Sequence

from agentops.core.cost import load_cost_model
from agentops.core.observe import (
    DeploymentFailure,
    DeploymentJournal,
    DeploymentPreview,
    DeploymentSelection,
    FederatedCredentialPlan,
    HostedCockpitDeployment,
    MutationRecord,
    ObserveScope,
    PlannedResource,
    RoleAssignmentPlan,
    ScopeMode,
    canonical_arm_id,
)

__all__ = [
    "CockpitDeploymentError",
    "WorkspaceResolutionError",
    "AmbiguousSelectionError",
    "PrerequisiteError",
    "PreviewBlockedError",
    "ConfirmationRequiredError",
    "FederationConflictError",
    "DeploymentStageError",
    "AzureContext",
    "ProjectResolver",
    "PermissionResult",
    "PermissionChecker",
    "AppRegistrationInfo",
    "FederatedCredentialInfo",
    "AppRegistrationClient",
    "TelemetryDiscovery",
    "ManagedIdentityResolver",
    "RoleAssignmentClient",
    "AzdPreviewResult",
    "AzdCommandResult",
    "AzdCommandRunner",
    "HealthSignals",
    "HealthChecker",
    "Clock",
    "SystemClock",
    "DeploymentRequest",
    "ResolvedScope",
    "ResolvedContext",
    "FIC_AUDIENCE",
    "ROLE_DEFINITION_IDS",
    "ALLOWED_RESOURCE_TYPES",
    "ALLOWED_SETTINGS_KEYS",
    "DEPLOY_STATE_DIRNAME",
    "JOURNAL_FILENAME",
    "STAGE_ORDER",
    "normalize_deployment_name",
    "resolve_scope",
    "resolve_context",
    "resolve_selection",
    "validate_prerequisites",
    "derive_role_assignment_id",
    "build_application_settings",
    "build_preview",
    "blocking_reasons",
    "validate_confirmation",
    "journal_path",
    "selection_fingerprint",
    "load_journal",
    "save_journal",
    "reconcile_journal",
    "detect_drift",
    "classify_health",
    "deploy",
    "bundle_dir_for",
    "run_cli",
    "redact_secrets",
    "AzCliContext",
    "WorkspaceProjectResolver",
    "AzCliPermissionChecker",
    "AzCliAppRegistrationClient",
    "AzCliTelemetryDiscovery",
    "AzCliManagedIdentityResolver",
    "AzdCliCommandRunner",
    "AzCliHealthChecker",
    "materialize_bundle",
    "classify_role_assignment_scope",
    "bicep_role_assignment_parameters",
    "explicit_role_assignments",
    "AzCliRoleAssignmentClient",
    "DeploymentAdapters",
    "DeploymentPlan",
    "prepare_deployment",
    "execute_deployment",
]


# ---------------------------------------------------------------------------
# Errors (FR-062 / FR-063: every error names the stage, whether a mutation
# already happened, and how to recover)
# ---------------------------------------------------------------------------


class CockpitDeploymentError(Exception):
    """Base class for actionable hosted-Cockpit deployment errors."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        remediation: str = "",
        mutation_occurred: bool = False,
        retry_safe: bool = True,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.remediation = remediation
        self.mutation_occurred = mutation_occurred
        self.retry_safe = retry_safe

    def to_dict(self) -> dict[str, Any]:
        """Render this error as a plain dict for CLI/report consumption."""
        return {
            "message": str(self),
            "stage": self.stage,
            "remediation": self.remediation,
            "mutation_occurred": self.mutation_occurred,
            "retry_safe": self.retry_safe,
        }


class WorkspaceResolutionError(CockpitDeploymentError):
    """Raised when no Foundry project can be resolved from the workspace."""


class AmbiguousSelectionError(CockpitDeploymentError):
    """Raised when more than one candidate exists and none was chosen explicitly."""

    def __init__(
        self,
        message: str,
        *,
        candidates: Sequence[str],
        stage: str = "resolve_scope",
        remediation: str = "",
    ) -> None:
        super().__init__(message, stage=stage, remediation=remediation)
        self.candidates: list[str] = list(candidates)


class PrerequisiteError(CockpitDeploymentError):
    """Raised when one or more deployment prerequisites are not satisfied."""

    def __init__(
        self,
        message: str,
        *,
        failed_checks: Sequence[str],
        stage: str = "validate_prerequisites",
        remediation: str = "",
    ) -> None:
        super().__init__(message, stage=stage, remediation=remediation)
        self.failed_checks: list[str] = list(failed_checks)


class PreviewBlockedError(CockpitDeploymentError):
    """Raised when a preview proposes a change outside the resource allowlist."""


class ConfirmationRequiredError(CockpitDeploymentError):
    """Raised when confirmation is missing or ``--yes`` is not permitted."""


class FederationConflictError(CockpitDeploymentError):
    """Raised when an existing federated credential conflicts with the plan."""


class DeploymentStageError(CockpitDeploymentError):
    """Raised when an ``azd`` stage (provision/deploy) fails."""


# ---------------------------------------------------------------------------
# Injectable adapters (Protocols). Tests provide fakes; production callers
# (owned elsewhere) provide Azure CLI/SDK/azd-backed implementations.
# ---------------------------------------------------------------------------


class AzureContext(Protocol):
    """Current Azure/azd context used to fill in unset selection inputs."""

    def current_subscription_id(self) -> str | None: ...

    def current_tenant_id(self) -> str | None: ...

    def current_location(self) -> str | None: ...

    def current_cloud_name(self) -> str | None: ...

    def current_actor_id(self) -> str | None: ...

    def check_web_app_name_available(self, name: str) -> PermissionResult: ...

    def resource_exists(self, resource_id: str) -> bool: ...


class ProjectResolver(Protocol):
    """Resolves the Foundry project(s) linked to the current workspace."""

    def discover_projects(self, workspace: Path) -> list[str]: ...

    def validate_project(
        self, project_resource_id: str, *, subscription_id: str, tenant_id: str
    ) -> PermissionResult: ...


@dataclass(frozen=True)
class PermissionResult:
    """Outcome of a single deployer-permission preflight check."""

    name: str
    granted: bool
    reason: str = ""


class PermissionChecker(Protocol):
    """Preflight checks for the ARM/Graph permissions the deployer needs."""

    def check_arm_deployment(
        self, *, subscription_id: str, resource_group: str
    ) -> PermissionResult: ...

    def check_role_assignment_write(self, *, scope_resource_id: str) -> PermissionResult: ...

    def check_graph_application_readwrite(
        self, *, application_object_id: str
    ) -> PermissionResult: ...

    def check_group_read(self, *, group_id: str) -> PermissionResult: ...


@dataclass(frozen=True)
class AppRegistrationInfo:
    """Metadata about the pre-existing app registration referenced by the CLI."""

    application_object_id: str
    client_id: str
    service_principal_object_id: str
    tenant_id: str
    redirect_uris: tuple[str, ...] = ()
    has_delegated_consent: bool = True
    single_tenant: bool = True
    auth_prerequisites_checked: bool = False


@dataclass(frozen=True)
class FederatedCredentialInfo:
    """An existing federated identity credential on the app registration."""

    id: str
    name: str
    issuer: str
    subject: str
    audiences: tuple[str, ...]


class AppRegistrationClient(Protocol):
    """Graph-backed operations against the existing app registration.

    The deployment service never creates an app registration or service
    principal (FR safety requirement); it only validates and federates.
    """

    def get_app_registration(
        self, *, tenant_id: str, client_id: str
    ) -> AppRegistrationInfo: ...

    def list_federated_credentials(
        self, application_object_id: str
    ) -> list[FederatedCredentialInfo]: ...

    def create_federated_credential(
        self,
        application_object_id: str,
        *,
        name: str,
        issuer: str,
        subject: str,
        audiences: Sequence[str],
    ) -> FederatedCredentialInfo: ...


class TelemetryDiscovery(Protocol):
    """Resolves the telemetry resources (Log Analytics workspaces) in scope."""

    def discover_telemetry_resources(self, scope: ObserveScope) -> list[str]: ...


class ManagedIdentityResolver(Protocol):
    """Resolves live identifiers for a (possibly not-yet-created) UAMI."""

    def resolve_principal_id(self, resource_id: str) -> str | None: ...

    def resolve_client_id(self, resource_id: str) -> str | None: ...


class RoleAssignmentClient(Protocol):
    """Idempotent, out-of-template role assignment (FR-064).

    The packaged, resource-group-scoped Bicep template (see ``main.bicep``'s
    own role-assignment scope note) can only express Reader / Log Analytics
    Reader for the deployment's own resource group and named Foundry
    accounts/projects/Log Analytics workspaces that live in that *same*
    resource group. A subscription-scope grant, or any target in a
    *different* resource group, is applied through this seam instead
    (see :func:`explicit_role_assignments`), never by silently broadening
    what the template itself grants.
    """

    def ensure_role_assignment(
        self,
        *,
        assignment_id: uuid.UUID,
        scope_resource_id: str,
        principal_id: str,
        role_definition_id: str,
    ) -> bool:
        """Create the role assignment if it does not already exist.

        Returns ``True`` when newly created, ``False`` when it already
        existed (idempotent no-op), so reruns never fail and never create
        duplicates.
        """
        ...


@dataclass(frozen=True)
class DriftInspection:
    """Live-state differences that make a persisted journal unsafe to trust."""

    differences: tuple[str, ...] = ()


class DeploymentStateInspector(Protocol):
    """Read-only ARM/Graph reconciliation before a journaled deployment resumes."""

    def inspect(
        self, selection: DeploymentSelection, preview: DeploymentPreview
    ) -> DriftInspection: ...


@dataclass(frozen=True)
class AzdPreviewResult:
    """Normalized ``azd provision --preview`` output."""

    resources: tuple[dict[str, Any], ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AzdCommandResult:
    """Outcome of an ``azd provision``/``azd deploy`` invocation."""

    success: bool
    message: str = ""


class AzdCommandRunner(Protocol):
    """Injectable seam over the ``azd`` CLI so tests never spawn a process."""

    def preview(self, bundle_dir: Path, env_values: dict[str, str]) -> AzdPreviewResult: ...

    def provision(self, bundle_dir: Path, env_values: dict[str, str]) -> AzdCommandResult: ...

    def deploy(self, bundle_dir: Path, env_values: dict[str, str]) -> AzdCommandResult: ...


@dataclass(frozen=True)
class HealthSignals:
    """Raw post-deployment observations from an injected health checker."""

    liveness_ok: bool
    anonymous_access_denied: bool | None = True
    auth_context_ok: bool | None = None
    runtime_config_ok: bool | None = True
    resource_graph_ok: bool | None = True
    uami_read_ok: bool | None = None
    rbac_propagation_pending: bool = False


class HealthChecker(Protocol):
    """Post-deployment liveness/auth/RBAC verification (FR-071)."""

    def check(
        self, *, app_url: str, web_app_resource_id: str, principal_id: str
    ) -> HealthSignals: ...


class Clock(Protocol):
    """Injectable time source so journal timestamps are deterministic in tests."""

    def now(self) -> datetime: ...


class SystemClock:
    """Default :class:`Clock` implementation used outside of tests."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIC_AUDIENCE = "api://AzureADTokenExchange"
MAX_FEDERATED_CREDENTIALS = 20
LOG_ANALYTICS_API_APP_ID = "ca7f3f0b-7d91-482c-8e09-c5d840d0eac5"
LOG_ANALYTICS_DELEGATED_SCOPE = "Data.Read"

# Built-in Azure role definition GUIDs. These are stable, well-known IDs
# published by Azure (not secrets) and never change.
ROLE_DEFINITION_IDS: dict[str, str] = {
    "Reader": "acdd72a7-3385-48ef-bd42-f606fba81ae7",
    "Log Analytics Reader": "73c42c96-874c-492b-b04d-ab87d138a893",
}

ALLOWED_RESOURCE_TYPES = frozenset(
    {"app_service_plan", "web_app", "user_assigned_managed_identity"}
)

ALLOWED_SETTINGS_KEYS = frozenset(
    {
        "AGENTOPS_COCKPIT_MODE",
        "AGENTOPS_COST_MODEL",
        "AGENTOPS_OBSERVE_SCOPE",
        "AGENTOPS_TENANT_ID",
        # Canonical name chosen to match the packaged Bicep template
        # (``infra/main.bicep``'s ``applicationClientId`` app setting) so the
        # hosted backend's Easy Auth integration and this service always
        # agree on one App Service setting name; there is no
        # ``AGENTOPS_CLIENT_ID`` alias.
        "AGENTOPS_APPLICATION_CLIENT_ID",
        "AGENTOPS_UAMI_CLIENT_ID",
        # Canonical name chosen to match the packaged Bicep template's
        # ``allowedGroupObjectId`` app setting; there is no
        # ``AGENTOPS_ALLOWED_GROUP_ID`` alias.
        "AGENTOPS_ALLOWED_GROUP_OBJECT_ID",
    }
)

DEPLOY_STATE_DIRNAME = Path(".agentops") / "deploy" / "cockpit"
JOURNAL_FILENAME = "deployment-state.json"

# Fixed forever: used only to derive stable, non-secret UUIDs from ARM IDs
# and selection fingerprints so reruns reuse identical identifiers.
_NAMESPACE = uuid.UUID("6f2f7c4a-6b7c-4c9a-8e0a-2f6b6b0a5b40")

STAGE_ORDER: list[str] = [
    "validated",
    "previewed",
    "confirmed",
    "provisioned",
    "federated",
    "deployed",
    "verified",
]


def _stage_index(stage: str | None) -> int:
    if stage is None:
        return -1
    return STAGE_ORDER.index(stage)


# ---------------------------------------------------------------------------
# Selection precedence, workspace resolution, and scope validation
# (FR-068, deployment-name normalization)
# ---------------------------------------------------------------------------


@dataclass
class DeploymentRequest:
    """Raw, CLI-shaped inputs before precedence/ambiguity resolution."""

    workspace: Path
    scope_mode: ScopeMode = "projects"
    project_ids: tuple[str, ...] = ()
    scope_resource_id: str | None = None
    subscription_id: str | None = None
    resource_group: str | None = None
    location: str | None = None
    tenant_id: str | None = None
    client_id: str | None = None
    allowed_group_id: str | None = None
    name: str | None = None
    non_interactive: bool = False


@dataclass(frozen=True)
class ResolvedScope:
    """Result of resolving scope precedence/ambiguity into an ``ObserveScope``."""

    scope: ObserveScope
    explicit_inputs_complete: bool
    warnings: list[str]


def normalize_deployment_name(
    explicit: str | None,
    *,
    workspace: Path,
    project_hint: str,
    subscription_id: str,
    resource_group: str,
) -> str:
    """Normalize ``--name`` (or derive a deterministic default).

    Explicit names are slugified but left otherwise untouched (no suffix is
    appended, since the operator already made the name unique). Default
    names are derived from the workspace and resolved project, then given a
    deterministic, collision-safe suffix seeded by subscription/resource
    group so unrelated deployments never collide while reruns of the exact
    same deployment always produce the exact same name (rerun idempotency).
    """
    if explicit and explicit.strip():
        slug = _slugify(explicit)
    else:
        base = _slugify(workspace.name) or "workspace"
        hint = _slugify(project_hint)
        candidate = f"agentops-cockpit-{base}-{hint}" if hint else f"agentops-cockpit-{base}"
        suffix = uuid.uuid5(
            _NAMESPACE, f"{subscription_id}|{resource_group}|{candidate}"
        ).hex[:8]
        slug = f"{candidate}-{suffix}"

    if not slug:
        slug = "agentops-cockpit"

    max_len = 60
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")

    slug = slug.strip("-") or "agentops-cockpit"
    return slug


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", value or "").strip("-").lower()
    return re.sub(r"-{2,}", "-", slug)


def _project_hint(scope: ObserveScope) -> str:
    if scope.mode == "projects" and scope.default_project_resource_id:
        return scope.default_project_resource_id.rsplit("/", 1)[-1]
    if scope.root_resource_id:
        return scope.root_resource_id.rsplit("/", 1)[-1]
    return ""


def resolve_scope(
    request: DeploymentRequest, *, project_resolver: ProjectResolver
) -> ResolvedScope:
    """Resolve scope-selection precedence/ambiguity into an ``ObserveScope``.

    Precedence: an explicit non-``projects`` ``--scope`` always requires an
    explicit ``--scope-resource-id`` and forbids ``--project-id``. Under the
    default ``projects`` scope, explicit ``--project-id`` values win over
    workspace discovery; workspace discovery must resolve to exactly one
    project or the selection is ambiguous (FR-068).
    """
    warnings: list[str] = []

    if request.scope_mode == "projects":
        if request.scope_resource_id:
            raise CockpitDeploymentError(
                "--scope-resource-id is not valid for the 'projects' scope mode",
                stage="resolve_scope",
                remediation=(
                    "Pass --scope-resource-id together with --scope "
                    "foundry|resource-group|subscription, or omit it for "
                    "--scope projects."
                ),
            )
        if request.project_ids:
            project_ids = list(request.project_ids)
            explicit_inputs_complete = True
        else:
            discovered = project_resolver.discover_projects(request.workspace)
            if not discovered:
                raise WorkspaceResolutionError(
                    "No Foundry project could be resolved from the current workspace.",
                    stage="resolve_scope",
                    remediation=(
                        "Run `agentops init` to link a Foundry project, or "
                        "pass --project-id explicitly."
                    ),
                )
            if len(discovered) > 1:
                raise AmbiguousSelectionError(
                    "Multiple Foundry projects were discovered from the "
                    "current workspace; the deployment scope is ambiguous.",
                    candidates=discovered,
                    remediation="Pass one or more --project-id values explicitly.",
                )
            project_ids = discovered
            explicit_inputs_complete = False

        try:
            scope = ObserveScope.model_validate(
                {
                    "mode": "projects",
                    "project_resource_ids": project_ids,
                    "default_project_resource_id": project_ids[0],
                }
            )
        except ValueError as exc:
            raise CockpitDeploymentError(
                f"Invalid Observe scope: {exc}",
                stage="resolve_scope",
                remediation=(
                    "Confirm every --project-id is a full Foundry project "
                    "ARM resource ID."
                ),
            ) from exc
        return ResolvedScope(
            scope=scope, explicit_inputs_complete=explicit_inputs_complete, warnings=warnings
        )

    if request.project_ids:
        raise CockpitDeploymentError(
            "--project-id cannot be combined with a non-projects --scope mode",
            stage="resolve_scope",
            remediation="Pass --project-id only together with --scope projects.",
        )
    if not request.scope_resource_id:
        raise CockpitDeploymentError(
            f"--scope-resource-id is required for --scope {request.scope_mode}",
            stage="resolve_scope",
            remediation="Pass the exact ARM resource ID that bounds the requested scope.",
        )
    try:
        scope = ObserveScope.model_validate(
            {"mode": request.scope_mode, "root_resource_id": request.scope_resource_id}
        )
    except ValueError as exc:
        raise CockpitDeploymentError(
            f"Invalid Observe scope: {exc}",
            stage="resolve_scope",
            remediation="Confirm --scope-resource-id matches the requested --scope mode.",
        ) from exc

    if request.scope_mode == "subscription":
        warnings.append(
            "Subscription-wide scope grants Reader across every resource in "
            "the subscription; confirm this is intentional before continuing."
        )

    return ResolvedScope(scope=scope, explicit_inputs_complete=True, warnings=warnings)


def _resource_group_from_scope(scope: ObserveScope) -> str | None:
    """Use the selected workspace scope's resource group as the deploy default."""
    resource_id = (
        scope.project_resource_ids[0]
        if scope.mode == "projects"
        else scope.root_resource_id
    )
    if not resource_id:
        return None
    segments = resource_id.strip("/").split("/")
    lowered = [segment.lower() for segment in segments]
    try:
        index = lowered.index("resourcegroups")
    except ValueError:
        return None
    return segments[index + 1] if index + 1 < len(segments) else None


@dataclass(frozen=True)
class ResolvedContext:
    """Fully-resolved subscription/resource-group/location/tenant/client values."""

    subscription_id: str
    resource_group: str
    location: str
    tenant_id: str
    client_id: str
    explicit_inputs_complete: bool


def resolve_context(
    request: DeploymentRequest,
    *,
    context: AzureContext,
    default_resource_group: str | None = None,
) -> ResolvedContext:
    """Fill unset selection inputs from the current Azure/azd context.

    Explicit CLI flags always win; only unset values fall back to
    ``context``. Anything still missing raises a
    :class:`CockpitDeploymentError` naming the exact missing flag(s).
    """
    cloud_name_getter = getattr(context, "current_cloud_name", None)
    cloud_name = cloud_name_getter() if callable(cloud_name_getter) else "AzureCloud"
    if callable(cloud_name_getter) and cloud_name != "AzureCloud":
        raise PrerequisiteError(
            "Hosted Cockpit deployment supports Azure public cloud only; "
            f"current cloud is {cloud_name or 'unknown'}.",
            failed_checks=["azure_public_cloud"],
            stage="resolve_context",
            remediation="Run `az cloud set --name AzureCloud`, sign in again, and re-run.",
        )

    subscription_id = request.subscription_id or context.current_subscription_id()
    resource_group = request.resource_group or default_resource_group
    location = request.location or context.current_location()
    tenant_id = request.tenant_id or context.current_tenant_id()
    client_id = request.client_id

    missing = [
        label
        for label, value in (
            ("--subscription", subscription_id),
            ("--resource-group", resource_group),
            ("--location", location),
            ("--tenant-id", tenant_id),
            ("--client-id", client_id),
        )
        if not value
    ]
    if missing:
        raise CockpitDeploymentError(
            f"Missing required deployment input(s): {', '.join(missing)}",
            stage="resolve_context",
            remediation=(
                "Pass the missing flag(s) explicitly, or run interactively "
                "so AgentOps can prompt for them."
            ),
        )

    explicit_inputs_complete = all(
        (
            request.subscription_id,
            request.resource_group,
            request.location,
            request.tenant_id,
            request.client_id,
        )
    )
    return ResolvedContext(
        subscription_id=str(subscription_id),
        resource_group=str(resource_group),
        location=str(location),
        tenant_id=str(tenant_id),
        client_id=str(client_id),
        explicit_inputs_complete=explicit_inputs_complete,
    )


def resolve_selection(
    request: DeploymentRequest,
    *,
    context: AzureContext,
    project_resolver: ProjectResolver,
    app_registration: AppRegistrationClient,
) -> tuple[DeploymentSelection, bool, list[str]]:
    """Resolve *request* into a fully-validated ``DeploymentSelection``.

    Returns ``(selection, explicit_inputs_complete, warnings)``. Raises a
    :class:`CockpitDeploymentError` subclass when the workspace, scope, or
    context cannot be resolved unambiguously, or when the referenced app
    registration is unusable (FR-068).
    """
    resolved_scope = resolve_scope(request, project_resolver=project_resolver)
    default_resource_group = _resource_group_from_scope(resolved_scope.scope)
    resolved_context = resolve_context(
        request,
        context=context,
        default_resource_group=default_resource_group,
    )

    validate_project = getattr(project_resolver, "validate_project", None)
    if resolved_scope.scope.mode == "projects" and callable(validate_project):
        project_checks = [
            validate_project(
                project_id,
                subscription_id=resolved_context.subscription_id,
                tenant_id=resolved_context.tenant_id,
            )
            for project_id in resolved_scope.scope.project_resource_ids
        ]
        failed_projects = [check for check in project_checks if not check.granted]
        if failed_projects:
            raise PrerequisiteError(
                "One or more selected Foundry projects failed live validation: "
                + "; ".join(f"{check.name}: {check.reason}" for check in failed_projects),
                failed_checks=[check.name for check in failed_projects],
                stage="resolve_selection",
                remediation=(
                    "Pass readable Microsoft.CognitiveServices/accounts/projects ARM IDs "
                    "from the selected subscription and tenant."
                ),
            )

    try:
        registration = app_registration.get_app_registration(
            tenant_id=resolved_context.tenant_id, client_id=resolved_context.client_id
        )
    except LookupError as exc:
        raise PrerequisiteError(
            f"Existing app registration could not be found: {exc}",
            failed_checks=["app_registration_lookup"],
            stage="resolve_selection",
            remediation=(
                "Confirm --tenant-id/--client-id reference an existing "
                "single-tenant workforce app registration."
            ),
        ) from exc

    if registration.tenant_id.lower() != resolved_context.tenant_id.lower():
        raise PrerequisiteError(
            "The app registration's home tenant does not match --tenant-id.",
            failed_checks=["app_registration_tenant_match"],
            stage="resolve_selection",
            remediation="Pass the tenant ID that owns the app registration.",
        )
    if not registration.single_tenant:
        raise PrerequisiteError(
            "The app registration must be single-tenant (workforce-only).",
            failed_checks=["app_registration_single_tenant"],
            stage="resolve_selection",
            remediation="Reconfigure the app registration's sign-in audience to single tenant.",
        )

    name = normalize_deployment_name(
        request.name,
        workspace=request.workspace,
        project_hint=_project_hint(resolved_scope.scope),
        subscription_id=resolved_context.subscription_id,
        resource_group=resolved_context.resource_group,
    )

    name_available = getattr(context, "check_web_app_name_available", None)
    if callable(name_available):
        availability = name_available(name)
        if not availability.granted:
            expected_web_app_id = (
                f"/subscriptions/{resolved_context.subscription_id}/resourceGroups/"
                f"{resolved_context.resource_group}/providers/Microsoft.Web/sites/{name}"
            )
            resource_exists = getattr(context, "resource_exists", None)
            if not callable(resource_exists) or not resource_exists(expected_web_app_id):
                raise PrerequisiteError(
                    f"App Service hostname '{name}.azurewebsites.net' is unavailable: "
                    f"{availability.reason}",
                    failed_checks=["web_app_name_available"],
                    stage="resolve_selection",
                    remediation=(
                        "Choose a different explicit --name, or change the workspace/project "
                        "selection to derive a different deterministic name."
                    ),
                )

    if registration.auth_prerequisites_checked:
        expected_redirect = f"https://{name}.azurewebsites.net/.auth/login/aad/callback"
        normalized_redirects = {uri.rstrip("/").lower() for uri in registration.redirect_uris}
        failed_registration_checks: list[str] = []
        if expected_redirect.lower() not in normalized_redirects:
            failed_registration_checks.append("app_registration_redirect_uri")
        if not registration.has_delegated_consent:
            failed_registration_checks.append("app_registration_delegated_consent")
        if failed_registration_checks:
            raise PrerequisiteError(
                "The app registration is not ready for hosted Easy Auth and delegated "
                "Log Analytics access.",
                failed_checks=failed_registration_checks,
                stage="resolve_selection",
                remediation=(
                    f"Add redirect URI {expected_redirect} and grant/admin-consent the "
                    "required delegated Log Analytics permission before previewing."
                ),
            )

    selection = DeploymentSelection(
        workspace=request.workspace,
        subscription_id=resolved_context.subscription_id,
        resource_group=resolved_context.resource_group,
        location=resolved_context.location,
        app_name=name,
        tenant_id=registration.tenant_id,
        application_object_id=registration.application_object_id,
        client_id=registration.client_id,
        service_principal_object_id=registration.service_principal_object_id,
        allowed_group_id=request.allowed_group_id,
        scope=resolved_scope.scope,
    )
    explicit_inputs_complete = (
        resolved_context.explicit_inputs_complete and resolved_scope.explicit_inputs_complete
    )
    return selection, explicit_inputs_complete, resolved_scope.warnings


# ---------------------------------------------------------------------------
# Prerequisite validation (Azure CLI/azd/tenant/app-registration/redirect
# URI/consent/group/ARM+Graph permission preflight)
# ---------------------------------------------------------------------------


def validate_prerequisites(
    selection: DeploymentSelection,
    *,
    permissions: PermissionChecker,
    role_assignment_scopes: Sequence[str] = (),
) -> list[PermissionResult]:
    """Validate deployer ARM/Graph permissions before any preview is built.

    Raises :class:`PrerequisiteError` (naming every failed check) if any
    required permission is missing; returns the full list of check results
    on success so the caller can surface them (e.g. in a preflight report).
    """
    unique_role_scopes = list(
        dict.fromkeys(
            canonical_arm_id(scope)
            for scope in (
                _resource_group_scope(selection.subscription_id, selection.resource_group),
                *role_assignment_scopes,
            )
        )
    )
    checks = [
        permissions.check_arm_deployment(
            subscription_id=str(selection.subscription_id),
            resource_group=selection.resource_group,
        ),
        permissions.check_graph_application_readwrite(
            application_object_id=str(selection.application_object_id)
        ),
    ]
    checks.extend(
        permissions.check_role_assignment_write(scope_resource_id=scope)
        for scope in unique_role_scopes
    )
    if selection.allowed_group_id:
        checks.append(
            permissions.check_group_read(group_id=str(selection.allowed_group_id))
        )

    failed = [c.name for c in checks if not c.granted]
    if failed:
        reasons = "; ".join(f"{c.name}: {c.reason}" for c in checks if not c.granted)
        raise PrerequisiteError(
            f"Missing required Azure/Graph permission(s): {reasons}",
            failed_checks=failed,
            stage="validate_prerequisites",
            remediation=(
                "Grant the reported role/permission to the signed-in "
                "deployer identity and re-run."
            ),
        )
    return checks


def _resource_group_scope(subscription_id: Any, resource_group: str) -> str:
    return f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"


def _role_definition_resource_id(subscription_id: Any, role: str) -> str:
    return (
        f"/subscriptions/{subscription_id}/providers/Microsoft.Authorization/"
        f"roleDefinitions/{ROLE_DEFINITION_IDS[role]}"
    )


def _discovery_boundaries(selection: DeploymentSelection) -> list[str]:
    scope = selection.scope
    if scope.mode == "projects":
        return list(scope.project_resource_ids)
    if scope.root_resource_id:
        return [scope.root_resource_id]
    return [_resource_group_scope(selection.subscription_id, selection.resource_group)]


# ---------------------------------------------------------------------------
# Deterministic role assignment / FIC identity and non-secret app settings
# ---------------------------------------------------------------------------


def derive_role_assignment_id(
    *, principal_id: str, role: str, scope_resource_id: str
) -> uuid.UUID:
    """Deterministic role-assignment GUID so reruns reuse the same assignment."""
    role_definition_id = ROLE_DEFINITION_IDS[role]
    seed = f"{canonical_arm_id(scope_resource_id)}|{role_definition_id}|{str(principal_id).lower()}"
    return uuid.uuid5(_NAMESPACE, seed)


def _placeholder_principal_id(resource_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"pending-principal|{canonical_arm_id(resource_id)}"))


def _placeholder_client_id(resource_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"pending-client|{canonical_arm_id(resource_id)}"))


def _planned_resources(selection: DeploymentSelection) -> list[PlannedResource]:
    base = (
        f"/subscriptions/{selection.subscription_id}/resourceGroups/"
        f"{selection.resource_group}/providers/Microsoft.Web"
    )
    plan_id = f"{base}/serverfarms/{selection.app_name}-plan"
    web_app_id = f"{base}/sites/{selection.app_name}"
    uami_id = (
        f"/subscriptions/{selection.subscription_id}/resourceGroups/"
        f"{selection.resource_group}/providers/Microsoft.ManagedIdentity/"
        f"userAssignedIdentities/{selection.app_name}-uami"
    )
    return [
        PlannedResource(
            resource_id=plan_id,
            resource_type="app_service_plan",
            change_type="unknown",
            location=selection.location,
        ),
        PlannedResource(
            resource_id=web_app_id,
            resource_type="web_app",
            change_type="unknown",
            location=selection.location,
        ),
        PlannedResource(
            resource_id=uami_id,
            resource_type="user_assigned_managed_identity",
            change_type="unknown",
            location=selection.location,
        ),
    ]


def _merge_change_types(
    resources: list[PlannedResource], azd_resources: Sequence[dict[str, Any]]
) -> list[PlannedResource]:
    by_type: dict[str, str] = {}
    for entry in azd_resources:
        rtype = entry.get("resource_type")
        if rtype in ALLOWED_RESOURCE_TYPES:
            by_type[rtype] = entry.get("change_type", "unknown")
    merged = []
    for resource in resources:
        change_type = by_type.get(resource.resource_type, resource.change_type)
        merged.append(resource.model_copy(update={"change_type": change_type}))
    return merged


def _planned_role_assignments(
    selection: DeploymentSelection,
    *,
    telemetry_discovery: TelemetryDiscovery,
    principal_id: str,
) -> list[RoleAssignmentPlan]:
    assignments: list[RoleAssignmentPlan] = []
    for boundary in _discovery_boundaries(selection):
        assignments.append(
            RoleAssignmentPlan(
                assignment_id=derive_role_assignment_id(
                    principal_id=principal_id, role="Reader", scope_resource_id=boundary
                ),
                principal_id=principal_id,
                role="Reader",
                role_definition_id=_role_definition_resource_id(
                    selection.subscription_id, "Reader"
                ),
                scope_resource_id=boundary,
                reason="Discovery boundary read access for the hosted Cockpit.",
            )
        )

    for telemetry_resource_id in telemetry_discovery.discover_telemetry_resources(
        selection.scope
    ):
        assignments.append(
            RoleAssignmentPlan(
                assignment_id=derive_role_assignment_id(
                    principal_id=principal_id,
                    role="Log Analytics Reader",
                    scope_resource_id=telemetry_resource_id,
                ),
                principal_id=principal_id,
                role="Log Analytics Reader",
                role_definition_id=_role_definition_resource_id(
                    selection.subscription_id, "Log Analytics Reader"
                ),
                scope_resource_id=telemetry_resource_id,
                reason="Aggregate telemetry read access for the hosted Cockpit.",
            )
        )
    return assignments


# ---------------------------------------------------------------------------
# Template-expressible vs. explicit ("out-of-template") role assignments.
#
# The packaged Bicep template (see ``main.bicep``) is resource-group-scoped:
# it can only grant Reader on its own resource group, or on named Foundry
# accounts/projects/Log Analytics workspaces that live in that *same*
# resource group. A subscription-scope grant, or any target in a
# *different* resource group, cannot be expressed by the template and must
# instead be applied explicitly through :class:`RoleAssignmentClient` (see
# :func:`explicit_role_assignments` and the ``deploy`` provisioned stage).
# Neither path is ever allowed to broaden access beyond exactly what
# ``preview.role_assignments`` (FR-064) already computed.
# ---------------------------------------------------------------------------

RoleAssignmentScopeKind = Literal[
    "resource_group", "foundry_account", "foundry_project", "log_analytics_workspace"
]

_FOUNDRY_ACCOUNT_SUFFIX_RE = re.compile(
    r"^/providers/microsoft\.cognitiveservices/accounts/([^/]+)$", re.IGNORECASE
)
_FOUNDRY_PROJECT_SUFFIX_RE = re.compile(
    r"^/providers/microsoft\.cognitiveservices/accounts/([^/]+)/projects/([^/]+)$",
    re.IGNORECASE,
)
_LOG_ANALYTICS_WORKSPACE_SUFFIX_RE = re.compile(
    r"^/providers/microsoft\.operationalinsights/workspaces/([^/]+)$", re.IGNORECASE
)


def classify_role_assignment_scope(
    selection: DeploymentSelection, assignment: RoleAssignmentPlan
) -> RoleAssignmentScopeKind | None:
    """Classify *assignment* against what the packaged Bicep template can express.

    Returns one of ``"resource_group"``, ``"foundry_account"``,
    ``"foundry_project"``, or ``"log_analytics_workspace"`` when the
    template's ``main.parameters.json`` can express the grant (the target
    is the deployment's own resource group, or a named resource inside it);
    returns ``None`` for a subscription-scope target or any target outside
    the deployment's own resource group, which must be applied through
    :func:`explicit_role_assignments` instead.
    """
    own_resource_group = canonical_arm_id(
        _resource_group_scope(selection.subscription_id, selection.resource_group)
    )
    scope_id = assignment.scope_resource_id  # already canonicalized by the model

    if scope_id == own_resource_group:
        return "resource_group" if assignment.role == "Reader" else None
    if not scope_id.startswith(f"{own_resource_group}/"):
        return None  # different resource group, or subscription-scope

    suffix = scope_id[len(own_resource_group) :]
    if assignment.role == "Reader":
        if _FOUNDRY_PROJECT_SUFFIX_RE.fullmatch(suffix):
            return "foundry_project"
        if _FOUNDRY_ACCOUNT_SUFFIX_RE.fullmatch(suffix):
            return "foundry_account"
        return None
    if assignment.role == "Log Analytics Reader":
        if _LOG_ANALYTICS_WORKSPACE_SUFFIX_RE.fullmatch(suffix):
            return "log_analytics_workspace"
        return None
    return None


def bicep_role_assignment_parameters(
    selection: DeploymentSelection, assignments: Sequence[RoleAssignmentPlan]
) -> dict[str, Any]:
    """Build the packaged template's 4 RBAC parameter values for *assignments*.

    Only assignments :func:`classify_role_assignment_scope` maps to a
    same-resource-group kind are ever reflected here, so the template
    itself never grants more than what it is actually capable of
    expressing; anything else is left for :func:`explicit_role_assignments`
    to apply out-of-template. The result is meant for
    :func:`materialize_bundle`'s ``role_assignment_parameters`` argument
    (patched into ``infra/main.parameters.json``).
    """
    own_resource_group = canonical_arm_id(
        _resource_group_scope(selection.subscription_id, selection.resource_group)
    )
    grant_reader_on_resource_group = False
    foundry_account_names: list[str] = []
    foundry_project_refs: list[dict[str, str]] = []
    log_analytics_workspace_names: list[str] = []

    for assignment in assignments:
        kind = classify_role_assignment_scope(selection, assignment)
        if kind == "resource_group":
            grant_reader_on_resource_group = True
        elif kind == "foundry_account":
            name = assignment.scope_resource_id.rsplit("/", 1)[-1]
            if name not in foundry_account_names:
                foundry_account_names.append(name)
        elif kind == "foundry_project":
            suffix = assignment.scope_resource_id[len(own_resource_group) :]
            match = _FOUNDRY_PROJECT_SUFFIX_RE.fullmatch(suffix)
            assert match is not None
            ref = {"accountName": match.group(1), "projectName": match.group(2)}
            if ref not in foundry_project_refs:
                foundry_project_refs.append(ref)
        elif kind == "log_analytics_workspace":
            name = assignment.scope_resource_id.rsplit("/", 1)[-1]
            if name not in log_analytics_workspace_names:
                log_analytics_workspace_names.append(name)

    return {
        "grantReaderOnResourceGroup": grant_reader_on_resource_group,
        "foundryAccountNames": foundry_account_names,
        "foundryProjectRefs": foundry_project_refs,
        "logAnalyticsWorkspaceNames": log_analytics_workspace_names,
    }


def explicit_role_assignments(
    selection: DeploymentSelection, assignments: Sequence[RoleAssignmentPlan]
) -> list[RoleAssignmentPlan]:
    """Return the assignments the packaged Bicep template cannot express.

    These must be applied directly through an injected
    :class:`RoleAssignmentClient` (see the ``deploy`` provisioned stage)
    rather than folded into the template's own resource-group-scoped
    parameters -- see :func:`classify_role_assignment_scope` for the exact
    same-resource-group rule that decides this split.
    """
    return [
        assignment
        for assignment in assignments
        if classify_role_assignment_scope(selection, assignment) is None
    ]


def _resolve_role_assignment_scope(
    selection: DeploymentSelection, *, telemetry_discovery: TelemetryDiscovery
) -> dict[str, Any]:
    """Compute the packaged template's RBAC parameter values for *selection*.

    Used by :func:`prepare_deployment` to patch ``infra/main.parameters.json``
    (via :func:`materialize_bundle`'s ``role_assignment_parameters``
    argument) *before* ``build_preview`` runs, since :func:`build_preview`
    needs the bundle already materialized. The placeholder principal id
    used here never affects the result -- :func:`classify_role_assignment_scope`
    only looks at ``role``/``scope_resource_id``, both independent of the
    principal -- so this is guaranteed to match what :func:`build_preview`
    independently recomputes moments later with the real principal id,
    with no drift between the parameters actually deployed and the preview
    shown to the operator.
    """
    assignments = _resolve_planned_role_assignments(
        selection, telemetry_discovery=telemetry_discovery
    )
    return bicep_role_assignment_parameters(selection, assignments)


def _resolve_planned_role_assignments(
    selection: DeploymentSelection, *, telemetry_discovery: TelemetryDiscovery
) -> list[RoleAssignmentPlan]:
    """Resolve exact deployment RBAC scopes before preview or mutation."""
    uami_resource_id = next(
        resource.resource_id
        for resource in _planned_resources(selection)
        if resource.resource_type == "user_assigned_managed_identity"
    )
    placeholder_principal_id = _placeholder_principal_id(uami_resource_id)
    return _planned_role_assignments(
        selection,
        telemetry_discovery=telemetry_discovery,
        principal_id=placeholder_principal_id,
    )


def _planned_federated_credential(
    selection: DeploymentSelection,
    *,
    app_registration: AppRegistrationClient,
    uami_principal_id: str,
) -> FederatedCredentialPlan:
    name = f"agentops-cockpit-{selection.app_name}"
    issuer = f"https://login.microsoftonline.com/{selection.tenant_id}/v2.0"
    subject = uami_principal_id
    existing = app_registration.list_federated_credentials(str(selection.application_object_id))
    matches = [credential for credential in existing if credential.name == name]
    match = matches[0] if len(matches) == 1 else None
    action: Literal["create", "reuse", "conflict"]
    if len(matches) > 1:
        action = "conflict"
    elif match is None and len(existing) >= MAX_FEDERATED_CREDENTIALS:
        action = "conflict"
    elif match is None:
        action = "create"
    elif (
        match.issuer == issuer
        and match.subject.lower() == str(subject).lower()
        and list(match.audiences) == [FIC_AUDIENCE]
    ):
        action = "reuse"
    else:
        action = "conflict"
    return FederatedCredentialPlan(
        application_object_id=selection.application_object_id,
        name=name,
        issuer=issuer,
        subject=subject,
        audiences=[FIC_AUDIENCE],
        action=action,
    )


def build_application_settings(
    selection: DeploymentSelection, *, uami_client_id: str
) -> dict[str, str]:
    """Build the non-secret ``authsettingsV2``-adjacent App Service settings.

    Only allowlisted, non-secret keys are ever produced (FR-060); the
    resulting dict is additionally validated by
    ``DeploymentPreview.application_settings`` in ``core.observe``, which
    rejects any key containing secret-shaped substrings.

    Key names are chosen to match the packaged hosted-Cockpit Bicep
    template (``infra/main.bicep``) byte-for-byte -- ``AGENTOPS_APPLICATION_
    CLIENT_ID`` and ``AGENTOPS_ALLOWED_GROUP_OBJECT_ID`` -- so the deployed
    App Service settings, this preview/deploy service, and the hosted
    backend's Easy Auth integration all agree on one canonical pair with no
    aliasing.
    """
    settings: dict[str, str] = {
        "AGENTOPS_COCKPIT_MODE": "hosted",
        "AGENTOPS_OBSERVE_SCOPE": selection.scope.model_dump_json(),
        "AGENTOPS_TENANT_ID": str(selection.tenant_id),
        "AGENTOPS_APPLICATION_CLIENT_ID": str(selection.client_id),
        "AGENTOPS_UAMI_CLIENT_ID": uami_client_id,
    }
    if selection.allowed_group_id:
        settings["AGENTOPS_ALLOWED_GROUP_OBJECT_ID"] = str(selection.allowed_group_id)

    raw_cost_model = os.getenv("AGENTOPS_COST_MODEL")
    if raw_cost_model is not None:
        cost_model_result = load_cost_model(raw_cost_model)
        if cost_model_result.state != "valid":
            raise CockpitDeploymentError(
                cost_model_result.message
                or "AGENTOPS_COST_MODEL contains an invalid cost model.",
                stage="build_preview",
                remediation=(
                    "Correct or remove AGENTOPS_COST_MODEL before previewing "
                    "the hosted Cockpit deployment."
                ),
            )
        settings["AGENTOPS_COST_MODEL"] = raw_cost_model

    unexpected = set(settings) - ALLOWED_SETTINGS_KEYS
    if unexpected:
        raise CockpitDeploymentError(
            f"Refusing to plan non-allowlisted App Service settings: {sorted(unexpected)}",
            stage="build_preview",
            remediation="Remove the non-allowlisted setting(s) from the deployment plan.",
        )
    return settings


def _azd_env_values(
    selection: DeploymentSelection, application_settings: dict[str, str]
) -> dict[str, str]:
    """Build the full ``azd``/Bicep substitution environment for one preview
    or deploy call.

    This is the single source of truth for every placeholder referenced by
    the packaged ``infra/main.parameters.json`` (``WEB_APP_NAME``,
    ``AZURE_LOCATION``, ``AZURE_ENV_NAME``, ``AZURE_TENANT_ID``,
    ``AGENTOPS_APPLICATION_CLIENT_ID``, ``AGENTOPS_ALLOWED_GROUP_OBJECT_ID``,
    ``AGENTOPS_OBSERVE_SCOPE``, ``AGENTOPS_COST_MODEL``, ``AGENTOPS_VERSION``)
    plus the standard azd
    subscription/resource-group variables it reads directly. ``application_
    settings`` already supplies ``AGENTOPS_APPLICATION_CLIENT_ID``,
    ``AGENTOPS_ALLOWED_GROUP_OBJECT_ID`` (when set), and
    ``AGENTOPS_OBSERVE_SCOPE`` -- this only adds the azd-conventional names
    the Bicep parameters file additionally expects, and never removes or
    renames a key ``application_settings`` already produced.
    """
    values = dict(application_settings)
    values.update(
        {
            "AZURE_SUBSCRIPTION_ID": str(selection.subscription_id),
            "AZURE_RESOURCE_GROUP": selection.resource_group,
            "AZURE_LOCATION": selection.location,
            "AZURE_TENANT_ID": str(selection.tenant_id),
            # ``AZURE_ENV_NAME`` selects/creates the azd environment folder;
            # using the deployment name keeps it deterministic and stable
            # across reruns (matches the app/plan/UAMI name already derived
            # from it).
            "AZURE_ENV_NAME": selection.app_name,
            "WEB_APP_NAME": selection.app_name,
            # Retained for back-compat with callers/tests that read the app
            # name under its original key name.
            "AGENTOPS_COCKPIT_APP_NAME": selection.app_name,
            "AGENTOPS_VERSION": _installed_version(),
        }
    )
    return values


# ---------------------------------------------------------------------------
# Preview construction and preview-before-mutation confirmation gating
# ---------------------------------------------------------------------------


def build_preview(
    selection: DeploymentSelection,
    *,
    telemetry_discovery: TelemetryDiscovery,
    identity_resolver: ManagedIdentityResolver,
    app_registration: AppRegistrationClient,
    azd_runner: AzdCommandRunner,
    bundle_dir: Path,
) -> DeploymentPreview:
    """Build the combined azd/Bicep + RBAC + FIC + settings deployment preview.

    Nothing in this function mutates Azure state: ``azd_runner.preview`` is
    expected to run ``azd provision --preview`` (a dry run), and the Graph
    federated-credential lookup is read-only.
    """
    resources = _planned_resources(selection)
    uami_resource_id = next(
        r.resource_id for r in resources if r.resource_type == "user_assigned_managed_identity"
    )

    principal_id = identity_resolver.resolve_principal_id(
        uami_resource_id
    ) or _placeholder_principal_id(uami_resource_id)
    client_id = identity_resolver.resolve_client_id(
        uami_resource_id
    ) or _placeholder_client_id(uami_resource_id)

    role_assignments = _planned_role_assignments(
        selection, telemetry_discovery=telemetry_discovery, principal_id=principal_id
    )
    federated_credential = _planned_federated_credential(
        selection, app_registration=app_registration, uami_principal_id=principal_id
    )
    application_settings = build_application_settings(selection, uami_client_id=client_id)

    env_values = _azd_env_values(selection, application_settings)
    azd_preview = azd_runner.preview(bundle_dir, env_values)
    resources = _merge_change_types(resources, azd_preview.resources)

    warnings: list[str] = []
    if selection.scope.mode == "subscription":
        warnings.append(
            "Subscription-wide scope grants Reader across every resource in "
            "the subscription; confirm this is intentional before continuing."
        )

    for entry in azd_preview.resources:
        resource_type = entry.get("resource_type")
        change_type = entry.get("change_type", "unknown")
        if resource_type not in ALLOWED_RESOURCE_TYPES or change_type in {"delete", "replace"}:
            warnings.append(
                "BLOCKED: azd/Bicep preview proposes a change outside the "
                f"approved resource allowlist ({resource_type or 'unknown'} -> "
                f"{change_type}); resolve the template before confirming."
            )

    if federated_credential.action == "conflict":
        warnings.append(
            "BLOCKED: an existing federated credential with the same name "
            "has a different issuer/subject/audience; resolve the conflict "
            "before confirming."
        )

    return DeploymentPreview(
        selection=selection,
        resources=resources,
        role_assignments=role_assignments,
        federated_credential=federated_credential,
        application_settings=application_settings,
        warnings=warnings,
        infrastructure_preview=dict(azd_preview.raw),
    )


def blocking_reasons(preview: DeploymentPreview) -> list[str]:
    """Return every preview warning that blocks confirmation outright."""
    return [warning for warning in preview.warnings if warning.startswith("BLOCKED:")]


def validate_confirmation(
    preview: DeploymentPreview,
    *,
    yes: bool,
    explicit_inputs_complete: bool,
    interactive_confirmed: bool = False,
) -> None:
    """Enforce preview-before-mutation and guarded ``--yes`` (FR-067, FR-070).

    Raises :class:`PreviewBlockedError` if the preview itself is blocked
    (e.g. an out-of-allowlist change or a federated-credential conflict), or
    :class:`ConfirmationRequiredError` if confirmation was not supplied, or
    if ``--yes`` was requested without every required input being explicit.
    """
    blocked = blocking_reasons(preview)
    if blocked:
        raise PreviewBlockedError(
            "Confirmation is blocked until the preview no longer proposes "
            "out-of-allowlist changes: " + "; ".join(blocked),
            stage="validate_confirmation",
            remediation=(
                "Adjust the deployment inputs or bundle so the preview only "
                "touches the approved App Service/UAMI resources, then re-run."
            ),
        )
    if yes and not explicit_inputs_complete:
        raise ConfirmationRequiredError(
            "--yes requires every required deployment and scope value to be "
            "supplied explicitly.",
            stage="validate_confirmation",
            remediation=(
                "Pass --subscription, --resource-group, --location, "
                "--tenant-id, --client-id, and an explicit --scope/"
                "--project-id selection, or omit --yes to confirm interactively."
            ),
        )
    if not yes and not interactive_confirmed:
        raise ConfirmationRequiredError(
            "Deployment requires explicit confirmation before any mutation.",
            stage="validate_confirmation",
            remediation=(
                "Re-run with --yes (only valid when every input was explicit) "
                "or confirm the interactive prompt."
            ),
        )


# ---------------------------------------------------------------------------
# Deployment journal persistence, reconciliation, and resume semantics
# (FR-010A-F) under .agentops/deploy/cockpit/
# ---------------------------------------------------------------------------


def journal_path(workspace: Path) -> Path:
    """Return the local-only journal path for *workspace*."""
    return workspace / DEPLOY_STATE_DIRNAME / JOURNAL_FILENAME


def bundle_dir_for(workspace: Path) -> Path:
    """Return the version-matched azd bundle directory for *workspace*."""
    return workspace / DEPLOY_STATE_DIRNAME


def selection_fingerprint(selection: DeploymentSelection) -> str:
    """Deterministic fingerprint identifying a specific deployment selection.

    Used to distinguish "rerun of the exact same deployment" (safe to
    resume/reuse) from "a different deployment request" (must start a fresh
    attempt rather than silently reusing stale journal state).
    """
    payload = selection.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_journal(path: Path) -> DeploymentJournal | None:
    """Load the deployment journal at *path*, or ``None`` if absent/corrupt."""
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return DeploymentJournal.model_validate(raw)
    except ValueError:
        return None


def save_journal(path: Path, journal: DeploymentJournal) -> None:
    """Persist *journal* to *path*, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(journal.model_dump_json(indent=2) + "\n", encoding="utf-8")


def reconcile_journal(
    existing: DeploymentJournal | None,
    selection: DeploymentSelection,
    *,
    clock: Clock,
    initiated_by: str | None = None,
    approval_method: Literal["interactive", "non_interactive"] | None = None,
) -> tuple[DeploymentJournal, bool]:
    """Reconcile a possibly-stale journal against the current *selection*.

    Returns ``(journal, is_resume)``. A rerun whose selection fingerprint
    matches the persisted journal resumes from ``last_completed_stage``
    (rerun idempotency); any fingerprint mismatch starts a fresh attempt
    rather than silently reusing mutation state from an unrelated request.
    """
    fingerprint = selection_fingerprint(selection)
    approved_at = clock.now() if approval_method else None
    if existing is not None and existing.selection_fingerprint == fingerprint:
        if initiated_by is None and approval_method is None:
            return existing, True
        return (
            existing.model_copy(
                update={
                    "initiated_by": initiated_by,
                    "approval_method": approval_method,
                    "approved_at": approved_at,
                    "updated_at": clock.now(),
                    "failure": None,
                }
            ),
            True,
        )
    return (
        DeploymentJournal(
            attempt_id=uuid.uuid4(),
            selection_fingerprint=fingerprint,
            initiated_by=initiated_by,
            approval_method=approval_method,
            approved_at=approved_at,
            updated_at=clock.now(),
        ),
        False,
    )


def detect_drift(journal: DeploymentJournal, *, live_resource_ids: Sequence[str]) -> list[str]:
    """Return journal-recorded resource IDs that no longer exist live.

    Used before resuming a deployment so the service never assumes a
    resource still exists purely because a prior attempt's journal said so
    (live ARM/Graph reconciliation).
    """
    live = {canonical_arm_id(resource_id) for resource_id in live_resource_ids}
    return [resource_id for resource_id in journal.resource_ids if resource_id not in live]


def _record_stage(journal: DeploymentJournal, stage: str, *, clock: Clock) -> DeploymentJournal:
    if _stage_index(journal.last_completed_stage) >= _stage_index(stage):
        return journal
    return journal.model_copy(update={"last_completed_stage": stage, "updated_at": clock.now()})


def _record_mutation(journal: DeploymentJournal, mutation: MutationRecord) -> DeploymentJournal:
    mutations = [
        existing
        for existing in journal.mutations
        if not (
            existing.target_resource_id == mutation.target_resource_id
            and existing.action == mutation.action
        )
    ]
    mutations.append(mutation)
    resource_ids = list(dict.fromkeys(journal.resource_ids + [mutation.target_resource_id]))
    return journal.model_copy(update={"mutations": mutations, "resource_ids": resource_ids})


# ---------------------------------------------------------------------------
# Health verification
# ---------------------------------------------------------------------------


def classify_health(
    signals: HealthSignals,
) -> Literal["healthy", "auth_pending", "rbac_pending", "failed"]:
    """Map raw health signals to a truthful ``HostedCockpitDeployment.health``.

    Never reports ``healthy`` unless liveness, auth, and RBAC read access
    are all confirmed (FR-071): unresolved/unknown signals fall back to the
    most specific pending state rather than being optimistically rounded up.
    """
    if not signals.liveness_ok:
        return "failed"
    if signals.anonymous_access_denied is not True:
        return "auth_pending"
    if signals.auth_context_ok is False or signals.auth_context_ok is None:
        return "auth_pending"
    if signals.runtime_config_ok is not True:
        return "failed"
    if (
        signals.rbac_propagation_pending
        or signals.resource_graph_ok is False
        or signals.uami_read_ok is False
    ):
        return "rbac_pending"
    if signals.resource_graph_ok is None or signals.uami_read_ok is None:
        return "rbac_pending"
    return "healthy"


def _installed_version() -> str:
    """Return the installed ``agentops-accelerator`` distribution version.

    ``pyproject.toml`` declares ``name = "agentops-accelerator"`` (the
    importable module remains ``agentops``), so this queries package
    metadata under that distribution name -- never the module import name --
    and falls back to the legacy ``agentops`` distribution name for
    forward/backward compatibility, then ``"0.0.0"`` only when neither
    distribution is installed (e.g. an editable checkout with no build
    metadata) so callers never crash formatting a bundle.
    """
    from importlib.metadata import PackageNotFoundError, version

    for distribution_name in ("agentops-accelerator", "agentops"):
        try:
            return version(distribution_name)
        except PackageNotFoundError:
            continue
    return "0.0.0"


# ---------------------------------------------------------------------------
# Orchestration: ordered azd provision/federate/deploy + journal + health
# ---------------------------------------------------------------------------


def deploy(
    selection: DeploymentSelection,
    preview: DeploymentPreview,
    *,
    azd_runner: AzdCommandRunner,
    app_registration: AppRegistrationClient,
    health_checker: HealthChecker,
    clock: Clock,
    bundle_dir: Path | None = None,
    role_assignment_client: RoleAssignmentClient | None = None,
    identity_resolver: ManagedIdentityResolver | None = None,
    state_inspector: DeploymentStateInspector | None = None,
    initiated_by: str | None = None,
    approval_method: Literal["interactive", "non_interactive"] | None = None,
) -> HostedCockpitDeployment:
    """Execute the ordered deployment stages, journaling progress throughout.

    Stage order: ``provisioned`` (``azd provision``, plus any
    :func:`explicit_role_assignments` the packaged Bicep template cannot
    itself express) -> ``federated`` (idempotent FIC create/reuse) ->
    ``deployed`` (``azd deploy``) -> ``verified`` (health check). Any stage
    already recorded as complete in the journal for the same selection
    fingerprint is skipped, which makes reruns of a fully-completed
    deployment idempotent (no duplicate ``azd``/Graph/role-assignment
    calls) and makes a resumed partial deployment continue from the first
    incomplete stage rather than repeat completed work.

    *role_assignment_client* and *identity_resolver* are both optional and
    default to ``None`` (skipping the explicit role-assignment step
    entirely) so existing callers that never pass them keep working
    unchanged. When *role_assignment_client* is supplied, every assignment
    :func:`explicit_role_assignments` returns (i.e. every target the
    resource-group-scoped template cannot express -- a different resource
    group or a subscription-scope grant) is created idempotently and
    journaled with ``action="assign"``; assignments the template *can*
    express are never duplicated here, since :func:`materialize_bundle`
    already patched them into ``main.parameters.json`` before ``azd
    provision`` ran.

    On failure, the journal records a :class:`~agentops.core.observe.
    DeploymentFailure` that preserves every already-completed mutation
    (cloud resources are never rolled back automatically) and the original
    :class:`CockpitDeploymentError` is re-raised with stage/remediation
    context intact.
    """
    workspace = selection.workspace
    bundle_dir = bundle_dir or bundle_dir_for(workspace)
    path = journal_path(workspace)
    existing = load_journal(path)
    journal, is_resume = reconcile_journal(
        existing,
        selection,
        clock=clock,
        initiated_by=initiated_by,
        approval_method=approval_method,
    )
    if is_resume and state_inspector is not None:
        drift = state_inspector.inspect(selection, preview)
        if drift.differences:
            journal = journal.model_copy(
                update={
                    "last_completed_stage": "confirmed",
                    "failure": None,
                    "updated_at": clock.now(),
                }
            )

    journal = _record_stage(journal, "validated", clock=clock)
    journal = _record_stage(journal, "previewed", clock=clock)
    journal = _record_stage(journal, "confirmed", clock=clock)
    save_journal(path, journal)

    env_values = _azd_env_values(selection, preview.application_settings)

    try:
        if _stage_index(journal.last_completed_stage) < _stage_index("provisioned"):
            result = azd_runner.provision(bundle_dir, env_values)
            if not result.success:
                raise DeploymentStageError(
                    f"azd provision failed: {result.message}",
                    stage="provision",
                    remediation=(
                        "Inspect the azd provision output, fix the reported "
                        "error, and re-run; no already-provisioned resources "
                        "are deleted."
                    ),
                    mutation_occurred=bool(journal.mutations),
                )
            for resource in preview.resources:
                journal = _record_mutation(
                    journal,
                    MutationRecord(
                        target_resource_id=resource.resource_id,
                        action="create" if resource.change_type == "create" else "modify",
                        pre_existing=resource.change_type in {"modify", "no_change"},
                        status="completed",
                        resulting_resource_id=resource.resource_id,
                    ),
                )
            if role_assignment_client is not None:
                pending = explicit_role_assignments(selection, preview.role_assignments)
                resolved_principal_id: str | None = None
                if pending and identity_resolver is not None:
                    uami_resource_id = next(
                        r.resource_id
                        for r in preview.resources
                        if r.resource_type == "user_assigned_managed_identity"
                    )
                    resolved_principal_id = identity_resolver.resolve_principal_id(
                        uami_resource_id
                    )
                if (
                    pending
                    and identity_resolver is not None
                    and not resolved_principal_id
                ):
                    raise DeploymentStageError(
                        "The provisioned managed identity principal could not be resolved.",
                        stage="provision",
                        remediation=(
                            "Confirm the user-assigned identity exists and rerun deployment; "
                            "no placeholder identity is used for role assignments."
                        ),
                        mutation_occurred=True,
                    )
                for assignment in pending:
                    principal_id = resolved_principal_id or str(assignment.principal_id)
                    assignment_id = derive_role_assignment_id(
                        principal_id=principal_id,
                        role=assignment.role,
                        scope_resource_id=assignment.scope_resource_id,
                    )
                    created = role_assignment_client.ensure_role_assignment(
                        assignment_id=assignment_id,
                        scope_resource_id=assignment.scope_resource_id,
                        principal_id=principal_id,
                        role_definition_id=assignment.role_definition_id,
                    )
                    journal = _record_mutation(
                        journal,
                        MutationRecord(
                            target_resource_id=assignment.scope_resource_id,
                            action="assign",
                            pre_existing=not created,
                            status="completed",
                            resulting_resource_id=(
                                f"{assignment.scope_resource_id}/providers/"
                                "Microsoft.Authorization/roleAssignments/"
                                f"{assignment_id}"
                            ),
                        ),
                    )
            journal = _record_stage(journal, "provisioned", clock=clock)
            save_journal(path, journal)

        if _stage_index(journal.last_completed_stage) < _stage_index("federated"):
            fic = preview.federated_credential
            if fic.action == "conflict":
                raise FederationConflictError(
                    "The federated credential name already exists with a "
                    "different issuer/subject/audience.",
                    stage="federate",
                    remediation=(
                        "Choose a different deployment name, or remove the "
                        "conflicting federated credential before re-running."
                    ),
                    mutation_occurred=bool(journal.mutations),
                )
            uami_resource_id = next(
                r.resource_id
                for r in preview.resources
                if r.resource_type == "user_assigned_managed_identity"
            )
            resolved_fic_principal = (
                identity_resolver.resolve_principal_id(uami_resource_id)
                if identity_resolver is not None
                else None
            )
            if fic.action == "create":
                if identity_resolver is not None and not resolved_fic_principal:
                    raise DeploymentStageError(
                        "The provisioned managed identity principal could not be resolved.",
                        stage="federate",
                        remediation=(
                            "Confirm the user-assigned identity exists and rerun deployment; "
                            "no placeholder identity is used for federation."
                        ),
                        mutation_occurred=True,
                    )
                app_registration.create_federated_credential(
                    str(fic.application_object_id),
                    name=fic.name,
                    issuer=fic.issuer,
                    subject=resolved_fic_principal or str(fic.subject),
                    audiences=list(fic.audiences),
                )
            journal = _record_mutation(
                journal,
                MutationRecord(
                    target_resource_id=uami_resource_id,
                    action="federate",
                    pre_existing=fic.action == "reuse",
                    status="completed",
                    resulting_resource_id=None,
                ),
            )
            journal = _record_stage(journal, "federated", clock=clock)
            save_journal(path, journal)

        if _stage_index(journal.last_completed_stage) < _stage_index("deployed"):
            result = azd_runner.deploy(bundle_dir, env_values)
            if not result.success:
                raise DeploymentStageError(
                    f"azd deploy failed: {result.message}",
                    stage="deploy",
                    remediation=(
                        "Inspect the azd deploy output and re-run; the "
                        "provisioned infrastructure and federation are "
                        "preserved."
                    ),
                    mutation_occurred=True,
                )
            journal = _record_stage(journal, "deployed", clock=clock)
            save_journal(path, journal)

        web_app_resource_id = next(
            r.resource_id for r in preview.resources if r.resource_type == "web_app"
        )
        uami_resource_id = next(
            r.resource_id
            for r in preview.resources
            if r.resource_type == "user_assigned_managed_identity"
        )
        app_url = f"https://{selection.app_name}.azurewebsites.net"
        portal_url = (
            f"https://portal.azure.com/#@{selection.tenant_id}/resource{web_app_resource_id}"
        )

        principal_id = (
            str(preview.role_assignments[0].principal_id) if preview.role_assignments else ""
        )
        signals = health_checker.check(
            app_url=app_url, web_app_resource_id=web_app_resource_id, principal_id=principal_id
        )
        health = classify_health(signals)
        if health != "healthy":
            raise DeploymentStageError(
                f"Hosted Cockpit post-deployment verification did not pass ({health}).",
                stage="verify",
                remediation=(
                    "Review App Service health, Easy Auth configuration, selected-user access, "
                    "and UAMI Reader/Log Analytics Reader propagation, then re-run deployment."
                ),
                mutation_occurred=bool(journal.mutations),
            )

        journal = _record_stage(journal, "verified", clock=clock)
        save_journal(path, journal)

        return HostedCockpitDeployment(
            web_app_resource_id=web_app_resource_id,
            managed_identity_resource_id=uami_resource_id,
            scope=selection.scope,
            app_url=app_url,
            portal_url=portal_url,
            health=health,
            deployed_version=_installed_version(),
        )
    except CockpitDeploymentError as exc:
        completed = [m.target_resource_id for m in journal.mutations if m.status == "completed"]
        resource_targets = [resource.resource_id for resource in preview.resources]
        federated_target = next(
            (
                resource.resource_id
                for resource in preview.resources
                if resource.resource_type == "user_assigned_managed_identity"
            ),
            "",
        )
        stage_targets = {
            "provision": resource_targets
            + [assignment.scope_resource_id for assignment in preview.role_assignments],
            "federate": [federated_target] if federated_target else [],
            "deploy": [
                resource.resource_id
                for resource in preview.resources
                if resource.resource_type == "web_app"
            ],
        }.get(exc.stage, [])
        uncertain = (
            list(dict.fromkeys(stage_targets))
            if exc.stage in {"provision", "federate", "deploy"} and exc.mutation_occurred
            else []
        )
        incomplete = list(dict.fromkeys(stage_targets))
        if exc.stage == "provision":
            incomplete = [target for target in incomplete if target not in completed]
        usability = (
            "unusable"
            if exc.stage == "verify"
            else ("unverified" if journal.mutations else "not_deployed")
        )
        failure = DeploymentFailure(
            stage=exc.stage,
            summary=str(exc),
            completed_mutations=completed,
            incomplete_mutations=incomplete,
            uncertain_mutations=uncertain,
            local_rollbacks=[],
            rollback_failures=[],
            preserved_resource_ids=list(dict.fromkeys(journal.resource_ids + completed)),
            usability=usability,
            retry_guidance=exc.remediation
            or "Re-run the deployment after addressing the reported error.",
        )
        journal = journal.model_copy(update={"failure": failure, "updated_at": clock.now()})
        save_journal(path, journal)
        raise


# ---------------------------------------------------------------------------
# Production adapters: concrete ``az``/``azd`` CLI-backed implementations of
# every Protocol above, plus a high-level prepare/execute facade a thin
# Typer CLI command can call directly. None of this imports the Azure SDK;
# every external effect goes through the single ``run_cli`` subprocess seam
# below so it stays fully fake-able in tests.
# ---------------------------------------------------------------------------


def run_cli(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 300.0,
) -> tuple[int, str, str]:
    """Run one external CLI command (``az``/``azd``) and capture its output.

    This is the single subprocess seam every concrete adapter in this module
    goes through -- production code never calls :mod:`subprocess` directly --
    so a test can fake every CLI interaction end-to-end by monkeypatching
    this one function. *env* is merged on top of the current process
    environment (never replaces it), so ``PATH``/credential-cache lookups
    the CLI needs still work.

    Raises :class:`CockpitDeploymentError` (stage ``"cli_invocation"``) with
    an actionable remediation if the executable is missing from ``PATH`` or
    the command exceeds *timeout*, instead of letting a raw
    :class:`FileNotFoundError`/:class:`subprocess.TimeoutExpired` escape.
    """
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    command = list(args)
    if command:
        command[0] = shutil.which(command[0], path=full_env.get("PATH")) or command[0]
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            env=full_env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CockpitDeploymentError(
            f"'{command[0]}' was not found on PATH.",
            stage="cli_invocation",
            remediation=(
                f"Install the '{command[0]}' CLI and ensure it is on PATH, "
                "then re-run."
            ),
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CockpitDeploymentError(
            f"'{' '.join(command)}' timed out after {timeout} seconds.",
            stage="cli_invocation",
            remediation=(
                "Re-run once you have confirmed whether the underlying "
                "Azure operation completed (check the Azure portal before "
                "retrying to avoid a duplicate mutation)."
            ),
        ) from exc
    return completed.returncode, completed.stdout, completed.stderr


_SECRET_JSON_KEY_RE = re.compile(
    r'(?i)("[\w.-]*(?:password|secret|token|key|credential|connectionstring)[\w.-]*"\s*:\s*")'
    r"([^\"]*)(\")"
)
_SECRET_ENV_LINE_RE = re.compile(
    r"(?im)^([A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|KEY|CREDENTIAL)[A-Z0-9_]*\s*=\s*)(.+)$"
)


def redact_secrets(text: str) -> str:
    """Redact secret-shaped substrings from CLI stdout/stderr.

    Applied to every error message and journal-bound string derived from a
    subprocess so a leaked client secret, connection string, or access
    token in ``az``/``azd`` output never reaches an exception message, log
    line, or the on-disk deployment journal (FR safety requirement: no
    secrets in command output/journal).
    """
    if not text:
        return text
    redacted = _SECRET_JSON_KEY_RE.sub(r"\1***REDACTED***\3", text)
    redacted = _SECRET_ENV_LINE_RE.sub(lambda m: f"{m.group(1)}***REDACTED***", redacted)
    return redacted


def _parse_json_output(stdout: str, *, stage: str) -> Any:
    """Parse *stdout* as JSON, raising an actionable error on malformed output."""
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise CockpitDeploymentError(
            f"Could not parse JSON output from the '{stage}' command.",
            stage=stage,
            remediation=(
                "Confirm the Azure CLI/azd version installed supports "
                "'--output json' for this command, then re-run."
            ),
        ) from exc


class AzCliContext:
    """Concrete :class:`AzureContext` backed by ``az account show``."""

    def _account(self) -> dict[str, Any] | None:
        code, stdout, _stderr = run_cli(["az", "account", "show", "--output", "json"])
        if code != 0:
            return None
        try:
            account = _parse_json_output(stdout, stage="az_account_show")
        except CockpitDeploymentError:
            return None
        return account if isinstance(account, dict) else None

    def current_subscription_id(self) -> str | None:
        account = self._account()
        value = account.get("id") if account else None
        return str(value) if value else None

    def current_tenant_id(self) -> str | None:
        account = self._account()
        value = account.get("tenantId") if account else None
        return str(value) if value else None

    def current_location(self) -> str | None:
        # ``az account show`` never carries a default location, and there is
        # no reliable CLI-only equivalent without an existing resource group
        # to inspect; deliberately return ``None`` so the caller requires an
        # explicit ``--location`` instead of guessing.
        return None

    def current_cloud_name(self) -> str | None:
        account = self._account()
        value = account.get("environmentName") if account else None
        return str(value) if value else None

    def current_actor_id(self) -> str | None:
        code, stdout, _stderr = run_cli(
            ["az", "ad", "signed-in-user", "show", "--query", "id", "--output", "json"]
        )
        if code != 0:
            return None
        value = _parse_json_output(stdout, stage="az_signed_in_user_show")
        return str(value) if value else None

    def check_web_app_name_available(self, name: str) -> PermissionResult:
        subscription_id = self.current_subscription_id()
        if not subscription_id:
            return PermissionResult(
                name="web_app_name_available",
                granted=False,
                reason="Azure CLI did not return an active subscription.",
            )
        payload = json.dumps({"name": name, "type": "Site", "isFqdn": False})
        code, stdout, stderr = run_cli(
            [
                "az",
                "rest",
                "--method",
                "post",
                "--url",
                (
                    f"https://management.azure.com/subscriptions/{subscription_id}/"
                    "providers/Microsoft.Web/"
                    "checknameavailability?api-version=2023-12-01"
                ),
                "--headers",
                "Content-Type=application/json",
                "--body",
                payload,
                "--output",
                "json",
            ]
        )
        if code != 0:
            return PermissionResult(
                name="web_app_name_available",
                granted=False,
                reason=redact_secrets(stderr.strip() or "name availability check failed"),
            )
        result = _parse_json_output(stdout, stage="az_webapp_name_availability")
        available = isinstance(result, dict) and bool(result.get("nameAvailable"))
        reason = "" if available else str(
            (result or {}).get("message") or "the hostname is already allocated"
        )
        return PermissionResult(
            name="web_app_name_available", granted=available, reason=reason
        )

    def resource_exists(self, resource_id: str) -> bool:
        code, _stdout, _stderr = run_cli(
            ["az", "resource", "show", "--ids", resource_id, "--output", "none"]
        )
        return code == 0


_FOUNDRY_ENDPOINT_RE = re.compile(
    r"^https://(?P<account>[^./]+)\.services\.ai\.azure\.com/api/projects/(?P<project>[^/?#]+)",
    re.IGNORECASE,
)


def _parse_foundry_endpoint(endpoint: str) -> tuple[str, str] | None:
    match = _FOUNDRY_ENDPOINT_RE.match(endpoint.strip())
    if not match:
        return None
    return match.group("account"), match.group("project")


class WorkspaceProjectResolver:
    """Concrete :class:`ProjectResolver` reusing the existing azd-env helpers.

    Discovers the linked Foundry project endpoint from the active azd
    environment's ``.env`` file first (via
    :func:`agentops.utils.azd_env.discover_azd_env`), then falls back to
    ``.agentops/.env`` (the AgentOps-owned local values ``agentops init``
    writes when no azd environment is active). Both reads are filesystem
    only. The endpoint's account name is then resolved to a full ARM
    resource ID via ``az resource list`` so the result matches the
    ``project_resource_ids`` shape ``ObserveScope`` expects.
    """

    def discover_projects(self, workspace: Path) -> list[str]:
        endpoint = self._discover_endpoint(workspace)
        if not endpoint:
            return []
        parsed = _parse_foundry_endpoint(endpoint)
        if parsed is None:
            return []
        account_name, project_name = parsed
        account_resource_id = self._resolve_account_resource_id(account_name)
        if not account_resource_id:
            return []
        return [f"{canonical_arm_id(account_resource_id)}/projects/{project_name}"]

    def validate_project(
        self, project_resource_id: str, *, subscription_id: str, tenant_id: str
    ) -> PermissionResult:
        project_id = canonical_arm_id(project_resource_id)
        project_subscription, _resource_group = _extract_subscription_and_resource_group(
            project_id
        )
        if not project_subscription or project_subscription.lower() != subscription_id.lower():
            return PermissionResult(
                name=f"project_read:{project_id}",
                granted=False,
                reason="project belongs to a different subscription",
            )
        code, stdout, stderr = run_cli(
            ["az", "resource", "show", "--ids", project_id, "--output", "json"]
        )
        if code != 0:
            return PermissionResult(
                name=f"project_read:{project_id}",
                granted=False,
                reason=redact_secrets(stderr.strip() or "project was not readable"),
            )
        resource = _parse_json_output(stdout, stage="az_project_resource_show")
        resource_type = str((resource or {}).get("type") or "").lower()
        if resource_type != "microsoft.cognitiveservices/accounts/projects":
            return PermissionResult(
                name=f"project_read:{project_id}",
                granted=False,
                reason=f"unexpected resource type '{resource_type or 'unknown'}'",
            )
        account_code, account_stdout, account_stderr = run_cli(
            [
                "az",
                "account",
                "show",
                "--subscription",
                project_subscription,
                "--output",
                "json",
            ]
        )
        if account_code != 0:
            return PermissionResult(
                name=f"project_read:{project_id}",
                granted=False,
                reason=redact_secrets(account_stderr.strip() or "subscription was not readable"),
            )
        account = _parse_json_output(account_stdout, stage="az_project_subscription_show")
        if str((account or {}).get("tenantId") or "").lower() != tenant_id.lower():
            return PermissionResult(
                name=f"project_read:{project_id}",
                granted=False,
                reason="project subscription belongs to a different tenant",
            )
        return PermissionResult(name=f"project_read:{project_id}", granted=True)

    @staticmethod
    def _discover_endpoint(workspace: Path) -> str | None:
        from agentops.utils.azd_env import discover_azd_env, parse_env_file

        location = discover_azd_env(workspace)
        if location.found and location.env_path is not None:
            values = parse_env_file(location.env_path)
            endpoint = values.get("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
            if endpoint:
                return endpoint

        fallback_env = workspace / ".agentops" / ".env"
        if fallback_env.is_file():
            values = parse_env_file(fallback_env)
            endpoint = values.get("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
            if endpoint:
                return endpoint
        return None

    @staticmethod
    def _resolve_account_resource_id(account_name: str) -> str | None:
        code, stdout, _stderr = run_cli(
            [
                "az",
                "resource",
                "list",
                "--name",
                account_name,
                "--resource-type",
                "Microsoft.CognitiveServices/accounts",
                "--output",
                "json",
            ]
        )
        if code != 0:
            return None
        try:
            items = _parse_json_output(stdout, stage="az_resource_list")
        except CockpitDeploymentError:
            return None
        if isinstance(items, list) and items and isinstance(items[0], dict):
            resource_id = items[0].get("id")
            return str(resource_id) if resource_id else None
        return None


class AzCliPermissionChecker:
    """Concrete :class:`PermissionChecker` backed by ``az role assignment list``
    and read-only Graph checks (``az ad app show`` / ``az ad group show``).
    """

    def _signed_in_object_id(self) -> str | None:
        code, stdout, _stderr = run_cli(
            ["az", "ad", "signed-in-user", "show", "--query", "id", "--output", "json"]
        )
        if code != 0:
            return None
        try:
            value = _parse_json_output(stdout, stage="az_signed_in_user_show")
        except CockpitDeploymentError:
            return None
        return str(value) if value else None

    def _has_role(self, *, scope: str, roles: tuple[str, ...]) -> tuple[bool, str]:
        assignee = self._signed_in_object_id()
        if not assignee:
            return False, "Could not determine the signed-in Azure identity (run 'az login')."
        code, stdout, stderr = run_cli(
            [
                "az",
                "role",
                "assignment",
                "list",
                "--assignee",
                assignee,
                "--scope",
                scope,
                "--include-inherited",
                "--output",
                "json",
            ]
        )
        if code != 0:
            return False, redact_secrets(stderr.strip() or "az role assignment list failed")
        try:
            assignments = _parse_json_output(stdout, stage="az_role_assignment_list")
        except CockpitDeploymentError as exc:
            return False, str(exc)
        assigned_roles = {
            a.get("roleDefinitionName") for a in assignments if isinstance(a, dict)
        }
        if assigned_roles & set(roles):
            return True, ""
        return (
            False,
            f"Signed-in identity has none of the required role(s): {', '.join(roles)}.",
        )

    def check_arm_deployment(
        self, *, subscription_id: str, resource_group: str
    ) -> PermissionResult:
        scope = _resource_group_scope(subscription_id, resource_group)
        granted, reason = self._has_role(scope=scope, roles=("Contributor", "Owner"))
        return PermissionResult(name="arm_deployment", granted=granted, reason=reason)

    def check_role_assignment_write(self, *, scope_resource_id: str) -> PermissionResult:
        granted, reason = self._has_role(
            scope=scope_resource_id, roles=("Owner", "User Access Administrator")
        )
        return PermissionResult(name="role_assignment_write", granted=granted, reason=reason)

    def check_graph_application_readwrite(
        self, *, application_object_id: str
    ) -> PermissionResult:
        actor_id = self._signed_in_object_id()
        if not actor_id:
            return PermissionResult(
                name="graph_application_readwrite",
                granted=False,
                reason="Could not determine the signed-in Entra user.",
            )
        code, stdout, stderr = run_cli(
            ["az", "ad", "app", "owner", "list", "--id", application_object_id, "--output", "json"]
        )
        if code != 0:
            return PermissionResult(
                name="graph_application_readwrite",
                granted=False,
                reason=redact_secrets(stderr.strip() or "app owner lookup failed"),
            )
        owners = _parse_json_output(stdout, stage="az_ad_app_owner_list")
        granted = any(
            str(owner.get("id") or "").lower() == actor_id.lower()
            for owner in owners
            if isinstance(owner, dict)
        )
        if not granted:
            roles_code, roles_stdout, _roles_stderr = run_cli(
                [
                    "az",
                    "rest",
                    "--method",
                    "get",
                    "--url",
                    (
                        "https://graph.microsoft.com/v1.0/roleManagement/directory/"
                        f"roleAssignments?$filter=principalId eq '{actor_id}'"
                        "&$expand=roleDefinition"
                    ),
                    "--output",
                    "json",
                ]
            )
            if roles_code == 0:
                role_payload = _parse_json_output(
                    roles_stdout, stage="az_directory_role_assignments"
                )
                role_entries = (
                    role_payload.get("value", []) if isinstance(role_payload, dict) else []
                )
                accepted_roles = {
                    "Application Administrator",
                    "Cloud Application Administrator",
                    "Global Administrator",
                }
                granted = any(
                    str(item.get("roleDefinition", {}).get("displayName") or "")
                    in accepted_roles
                    for item in role_entries
                    if isinstance(item, dict)
                )
        reason = (
            ""
            if granted
            else (
                "Signed-in user is neither an app owner nor an Application, "
                "Cloud Application, or Global Administrator."
            )
        )
        return PermissionResult(
            name="graph_application_readwrite", granted=granted, reason=reason
        )

    def check_group_read(self, *, group_id: str) -> PermissionResult:
        code, _stdout, stderr = run_cli(
            ["az", "ad", "group", "show", "--group", group_id, "--output", "json"]
        )
        granted = code == 0
        reason = "" if granted else redact_secrets(stderr.strip() or "az ad group show failed")
        return PermissionResult(name="group_read", granted=granted, reason=reason)


class AzCliAppRegistrationClient:
    """Concrete :class:`AppRegistrationClient` backed by ``az ad app``.

    Only ever reads the existing app registration/service principal and
    creates federated credentials on it -- it never creates an app
    registration or service principal itself (FR safety requirement: the
    deployer must reference a pre-existing app registration).
    """

    def get_app_registration(
        self, *, tenant_id: str, client_id: str
    ) -> AppRegistrationInfo:
        code, stdout, stderr = run_cli(
            ["az", "ad", "app", "show", "--id", client_id, "--output", "json"]
        )
        if code != 0:
            raise LookupError(redact_secrets(stderr.strip() or "az ad app show failed"))
        app = _parse_json_output(stdout, stage="az_ad_app_show")
        if not isinstance(app, dict):
            raise LookupError("az ad app show returned an unexpected response shape.")

        sp_code, sp_stdout, sp_stderr = run_cli(
            ["az", "ad", "sp", "show", "--id", client_id, "--output", "json"]
        )
        if sp_code != 0:
            raise LookupError(redact_secrets(sp_stderr.strip() or "az ad sp show failed"))
        service_principal = _parse_json_output(sp_stdout, stage="az_ad_sp_show")
        if not isinstance(service_principal, dict):
            raise LookupError("az ad sp show returned an unexpected response shape.")

        sign_in_audience = str(app.get("signInAudience") or "")
        redirect_uris = tuple((app.get("web") or {}).get("redirectUris") or ())
        has_delegated_consent = False
        monitor_code, monitor_stdout, _monitor_stderr = run_cli(
            [
                "az",
                "ad",
                "sp",
                "show",
                "--id",
                LOG_ANALYTICS_API_APP_ID,
                "--output",
                "json",
            ]
        )
        if monitor_code == 0:
            monitor_principal = _parse_json_output(
                monitor_stdout, stage="az_log_analytics_service_principal"
            )
            monitor_principal_id = (
                str(monitor_principal.get("id") or "")
                if isinstance(monitor_principal, dict)
                else ""
            )
            grants_code, grants_stdout, _grants_stderr = run_cli(
                [
                    "az",
                    "rest",
                    "--method",
                    "get",
                    "--url",
                    (
                        "https://graph.microsoft.com/v1.0/oauth2PermissionGrants"
                        f"?$filter=clientId eq '{service_principal.get('id', '')}'"
                        f" and resourceId eq '{monitor_principal_id}'"
                    ),
                    "--output",
                    "json",
                ]
            )
            if grants_code == 0:
                grants = _parse_json_output(
                    grants_stdout, stage="az_oauth2_permission_grants"
                )
                entries = grants.get("value", []) if isinstance(grants, dict) else []
                has_delegated_consent = any(
                    isinstance(grant, dict)
                    and LOG_ANALYTICS_DELEGATED_SCOPE
                    in str(grant.get("scope") or "").split()
                    for grant in entries
                )
        return AppRegistrationInfo(
            application_object_id=str(app.get("id", "")),
            client_id=str(app.get("appId", client_id)),
            service_principal_object_id=str(service_principal.get("id", "")),
            tenant_id=tenant_id,
            redirect_uris=redirect_uris,
            has_delegated_consent=has_delegated_consent,
            single_tenant=sign_in_audience in ("", "AzureADMyOrg"),
            auth_prerequisites_checked=True,
        )

    def list_federated_credentials(
        self, application_object_id: str
    ) -> list[FederatedCredentialInfo]:
        code, stdout, stderr = run_cli(
            [
                "az",
                "ad",
                "app",
                "federated-credential",
                "list",
                "--id",
                application_object_id,
                "--output",
                "json",
            ]
        )
        if code != 0:
            raise LookupError(
                redact_secrets(
                    stderr.strip() or "az ad app federated-credential list failed"
                )
            )
        entries = _parse_json_output(
            stdout, stage="az_ad_app_federated_credential_list"
        )
        if not isinstance(entries, list):
            return []
        return [
            FederatedCredentialInfo(
                id=str(entry.get("id", "")),
                name=str(entry.get("name", "")),
                issuer=str(entry.get("issuer", "")),
                subject=str(entry.get("subject", "")),
                audiences=tuple(entry.get("audiences") or ()),
            )
            for entry in entries
            if isinstance(entry, dict)
        ]

    def create_federated_credential(
        self,
        application_object_id: str,
        *,
        name: str,
        issuer: str,
        subject: str,
        audiences: Sequence[str],
    ) -> FederatedCredentialInfo:
        payload = {
            "name": name,
            "issuer": issuer,
            "subject": subject,
            "audiences": list(audiences),
        }
        fd, tmp_name = tempfile.mkstemp(suffix=".json", prefix="agentops-fic-")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            code, stdout, stderr = run_cli(
                [
                    "az",
                    "ad",
                    "app",
                    "federated-credential",
                    "create",
                    "--id",
                    application_object_id,
                    "--parameters",
                    str(tmp_path),
                    "--output",
                    "json",
                ]
            )
        finally:
            tmp_path.unlink(missing_ok=True)
        if code != 0:
            raise CockpitDeploymentError(
                "Failed to create the federated identity credential: "
                + redact_secrets(
                    stderr.strip() or "az ad app federated-credential create failed"
                ),
                stage="federate",
                remediation=(
                    "Confirm the signed-in identity has Application "
                    "Administrator (or equivalent Graph) permission on the "
                    "app registration, then re-run."
                ),
                mutation_occurred=False,
            )
        entry = _parse_json_output(
            stdout, stage="az_ad_app_federated_credential_create"
        )
        if not isinstance(entry, dict):
            entry = {}
        return FederatedCredentialInfo(
            id=str(entry.get("id", "")),
            name=str(entry.get("name", name)),
            issuer=str(entry.get("issuer", issuer)),
            subject=str(entry.get("subject", subject)),
            audiences=tuple(entry.get("audiences") or list(audiences)),
        )


class AzCliDeploymentStateInspector:
    """Read-only ARM/Graph reconciliation for resumable deployment journals."""

    def inspect(
        self, selection: DeploymentSelection, preview: DeploymentPreview
    ) -> DriftInspection:
        differences: list[str] = []
        for resource in preview.resources:
            code, _stdout, _stderr = run_cli(
                ["az", "resource", "show", "--ids", resource.resource_id, "--output", "json"]
            )
            if code != 0:
                differences.append(f"missing resource {resource.resource_id}")

        for assignment in preview.role_assignments:
            code, stdout, _stderr = run_cli(
                [
                    "az",
                    "role",
                    "assignment",
                    "list",
                    "--scope",
                    assignment.scope_resource_id,
                    "--output",
                    "json",
                ]
            )
            entries = (
                _parse_json_output(stdout, stage="az_role_assignment_reconcile")
                if code == 0
                else []
            )
            if not any(
                str(item.get("name") or "").lower()
                == str(assignment.assignment_id).lower()
                for item in entries
                if isinstance(item, dict)
            ):
                differences.append(
                    f"missing role assignment {assignment.assignment_id}"
                )

        fic = preview.federated_credential
        try:
            credentials = AzCliAppRegistrationClient().list_federated_credentials(
                str(fic.application_object_id)
            )
        except LookupError:
            credentials = []
        if not any(
            credential.name == fic.name
            and credential.issuer == fic.issuer
            and credential.subject.lower() == str(fic.subject).lower()
            and credential.audiences == tuple(fic.audiences)
            for credential in credentials
        ):
            differences.append(f"missing or changed federated credential {fic.name}")

        web_app_id = next(
            resource.resource_id
            for resource in preview.resources
            if resource.resource_type == "web_app"
        )
        settings_code, settings_stdout, _settings_stderr = run_cli(
            [
                "az",
                "webapp",
                "config",
                "appsettings",
                "list",
                "--ids",
                web_app_id,
                "--output",
                "json",
            ]
        )
        settings_entries = (
            _parse_json_output(settings_stdout, stage="az_webapp_appsettings_reconcile")
            if settings_code == 0
            else []
        )
        live_settings = {
            str(item.get("name")): str(item.get("value"))
            for item in settings_entries
            if isinstance(item, dict) and item.get("name")
        }
        if any(
            live_settings.get(key) != value
            for key, value in preview.application_settings.items()
        ):
            differences.append("App Service application settings drifted")

        auth_code, auth_stdout, _auth_stderr = run_cli(
            [
                "az",
                "rest",
                "--method",
                "post",
                "--url",
                (
                    f"https://management.azure.com{web_app_id}/config/"
                    "authsettingsV2/list?api-version=2023-12-01"
                ),
                "--output",
                "json",
            ]
        )
        auth = (
            _parse_json_output(auth_stdout, stage="az_webapp_authsettings_reconcile")
            if auth_code == 0
            else {}
        )
        properties = auth.get("properties", auth) if isinstance(auth, dict) else {}
        global_validation = properties.get("globalValidation", {})
        token_store = properties.get("login", {}).get("tokenStore", {})
        if (
            not properties.get("platform", {}).get("enabled")
            or global_validation.get("unauthenticatedClientAction") != "Return401"
            or global_validation.get("excludedPaths") != ["/healthz"]
            or not token_store.get("enabled")
        ):
            differences.append("App Service authsettingsV2 drifted")

        return DriftInspection(tuple(differences))


_ARM_ID_SUB_RG_RE = re.compile(
    r"^/subscriptions/(?P<sub>[^/]+)(?:/resourceGroups/(?P<rg>[^/]+))?", re.IGNORECASE
)


def _extract_subscription_and_resource_group(
    resource_id: str,
) -> tuple[str | None, str | None]:
    match = _ARM_ID_SUB_RG_RE.match(resource_id)
    if not match:
        return None, None
    return match.group("sub"), match.group("rg")


class AzCliTelemetryDiscovery:
    """Resolve only Log Analytics workspaces linked to projects in scope."""

    def __init__(self, *, discovery_client: Any = None) -> None:
        self._discovery_client = discovery_client

    def _client(self) -> Any:
        if self._discovery_client is None:
            from azure.identity import DefaultAzureCredential

            from agentops.agent.observe.adapters import AzureDiscoveryClient

            credential = DefaultAzureCredential(process_timeout=30)
            self._discovery_client = AzureDiscoveryClient(credential=credential)
        return self._discovery_client

    def discover_telemetry_resources(self, scope: ObserveScope) -> list[str]:
        try:
            inventory = self._client().discover_sync(scope)
        except Exception as exc:
            raise PrerequisiteError(
                "Linked telemetry discovery failed before deployment preview.",
                failed_checks=["linked_telemetry_discovery"],
                remediation=(
                    "Confirm the selected Foundry projects and their Application Insights "
                    "connections are readable, then rerun the preview."
                ),
            ) from exc
        if inventory.partial_failures:
            raise PrerequisiteError(
                "Linked telemetry discovery was incomplete.",
                failed_checks=["linked_telemetry_discovery"],
                remediation=(
                    "Resolve the reported Foundry or Application Insights discovery "
                    "permissions before previewing role assignments."
                ),
            )
        return list(
            dict.fromkeys(
                source.workspace_id
                for source in inventory.telemetry_sources
                if source.state == "available" and source.workspace_id
            )
        )


class AzCliManagedIdentityResolver:
    """Concrete :class:`ManagedIdentityResolver` backed by ``az identity show``.

    Returns ``None`` (rather than raising) whenever the UAMI does not exist
    yet -- e.g. before the first ``azd provision`` has run for a brand-new
    deployment -- so callers fall back to the deterministic placeholder id
    (``_placeholder_principal_id``/``_placeholder_client_id``) already used
    by :func:`build_preview`.
    """

    def _show(self, resource_id: str) -> dict[str, Any] | None:
        code, stdout, _stderr = run_cli(
            ["az", "identity", "show", "--ids", resource_id, "--output", "json"]
        )
        if code != 0:
            return None
        try:
            identity = _parse_json_output(stdout, stage="az_identity_show")
        except CockpitDeploymentError:
            return None
        return identity if isinstance(identity, dict) else None

    def resolve_principal_id(self, resource_id: str) -> str | None:
        identity = self._show(resource_id)
        principal_id = identity.get("principalId") if identity else None
        return str(principal_id) if principal_id else None

    def resolve_client_id(self, resource_id: str) -> str | None:
        identity = self._show(resource_id)
        client_id = identity.get("clientId") if identity else None
        return str(client_id) if client_id else None


class AzCliRoleAssignmentClient:
    """Concrete :class:`RoleAssignmentClient` backed by ``az role assignment``.

    Applies exactly the grants :func:`explicit_role_assignments` selects --
    the ones the resource-group-scoped packaged template cannot itself
    express (a subscription-scope grant, or any target outside the
    deployment's own resource group) -- never anything broader. Existence
    is checked first via ``az role assignment list --ids <assignment
    resource id>`` (built from the caller's deterministic *assignment_id*,
    see :func:`derive_role_assignment_id`); only when it does not already
    exist is ``az role assignment create --name <assignment_id>`` invoked,
    so a rerun after a partial failure reuses the exact same assignment
    object instead of creating a duplicate for the same
    principal/role/scope.
    """

    def _assignment_resource_id(
        self, *, scope_resource_id: str, assignment_id: uuid.UUID
    ) -> str:
        return (
            f"{scope_resource_id}/providers/Microsoft.Authorization/"
            f"roleAssignments/{assignment_id}"
        )

    def _already_exists(self, assignment_resource_id: str) -> bool:
        code, stdout, _stderr = run_cli(
            ["az", "role", "assignment", "list", "--ids", assignment_resource_id, "--output", "json"]
        )
        if code != 0:
            return False
        try:
            existing = _parse_json_output(stdout, stage="az_role_assignment_list")
        except CockpitDeploymentError:
            return False
        return bool(existing)

    def ensure_role_assignment(
        self,
        *,
        assignment_id: uuid.UUID,
        scope_resource_id: str,
        principal_id: str,
        role_definition_id: str,
    ) -> bool:
        assignment_resource_id = self._assignment_resource_id(
            scope_resource_id=scope_resource_id, assignment_id=assignment_id
        )
        if self._already_exists(assignment_resource_id):
            return False

        code, _stdout, stderr = run_cli(
            [
                "az",
                "role",
                "assignment",
                "create",
                "--assignee-object-id",
                principal_id,
                "--assignee-principal-type",
                "ServicePrincipal",
                "--role",
                role_definition_id,
                "--scope",
                scope_resource_id,
                "--name",
                str(assignment_id),
                "--output",
                "json",
            ]
        )
        if code != 0:
            message = redact_secrets(stderr.strip() or "az role assignment create failed")
            raise CockpitDeploymentError(
                f"Could not grant the required role on '{scope_resource_id}': {message}",
                stage="provision",
                remediation=(
                    "Confirm the signed-in identity has 'Owner' or 'User Access "
                    "Administrator' on the target scope, then re-run; no other "
                    "already-created role assignments are affected."
                ),
                mutation_occurred=False,
            )
        return True


_ARM_TYPE_TO_RESOURCE_TYPE = {
    "microsoft.web/serverfarms": "app_service_plan",
    "microsoft.web/sites": "web_app",
    "microsoft.managedidentity/userassignedidentities": "user_assigned_managed_identity",
}
_AZD_CHANGE_TYPE_MAP = {
    "create": "create",
    "update": "modify",
    "delete": "delete",
    "replace": "replace",
    "ignore": "no_change",
    "nochange": "no_change",
}


def _normalize_azd_change(entry: dict[str, Any]) -> dict[str, Any]:
    """Translate one raw ``azd provision --preview`` change entry into the
    ``resource_type``/``change_type`` shape :func:`_merge_change_types` expects.

    Unknown ARM types are retained so the preview allowlist can block them.
    """
    arm_type = str(entry.get("type") or entry.get("resourceType") or "").lower()
    resource_type = _ARM_TYPE_TO_RESOURCE_TYPE.get(arm_type, arm_type or "unknown")
    raw_change = str(entry.get("changeType") or entry.get("change_type") or "").lower()
    change_type = _AZD_CHANGE_TYPE_MAP.get(raw_change, "unknown")
    return {"resource_type": resource_type, "change_type": change_type}


class AzdCliCommandRunner:
    """Concrete :class:`AzdCommandRunner` backed by the ``azd`` CLI.

    Every ``env_values`` entry is written into the target azd environment
    via ``azd env set`` (so ``${VAR}``/``${VAR=default}`` substitution in
    the packaged Bicep parameters file resolves identically whether a
    human or this adapter runs ``azd``) and additionally passed as extra
    process-environment variables to every ``azd`` subprocess as a
    defense-in-depth fallback. ``env_values`` is always exactly the preview
    ``application_settings``/azd context dict already built earlier in
    this module and never contains a secret, so persisting it to the azd
    environment's local ``.env`` file cannot leak a credential.
    """

    def __init__(self, *, timeout: float = 900.0) -> None:
        self._timeout = timeout

    def _ensure_environment(self, bundle_dir: Path, env_values: dict[str, str]) -> None:
        name = env_values.get("AZURE_ENV_NAME", "")
        if not name:
            return
        code, _stdout, _stderr = run_cli(
            ["azd", "env", "select", name], cwd=bundle_dir, timeout=self._timeout
        )
        if code != 0:
            run_cli(
                ["azd", "env", "new", name, "--no-prompt"],
                cwd=bundle_dir,
                timeout=self._timeout,
            )
        for key, value in env_values.items():
            run_cli(
                ["azd", "env", "set", key, value], cwd=bundle_dir, timeout=self._timeout
            )

    def preview(self, bundle_dir: Path, env_values: dict[str, str]) -> AzdPreviewResult:
        self._ensure_environment(bundle_dir, env_values)
        code, stdout, stderr = run_cli(
            ["azd", "provision", "--preview", "--output", "json"],
            cwd=bundle_dir,
            env=env_values,
            timeout=self._timeout,
        )
        if code != 0:
            raise CockpitDeploymentError(
                "azd provision --preview failed: "
                + redact_secrets(stderr.strip() or stdout.strip() or "unknown error"),
                stage="preview",
                remediation=(
                    "Inspect the azd/Bicep output above, fix the reported "
                    "error, and re-run the preview."
                ),
            )
        try:
            raw = _parse_json_output(stdout, stage="azd_provision_preview")
        except CockpitDeploymentError:
            return AzdPreviewResult(resources=(), raw={})
        raw_dict = raw if isinstance(raw, dict) else {}
        changes = raw_dict.get("changes")
        resources: list[dict[str, Any]] = []
        if isinstance(changes, list):
            for entry in changes:
                if not isinstance(entry, dict):
                    continue
                resources.append(_normalize_azd_change(entry))
        return AzdPreviewResult(resources=tuple(resources), raw=raw_dict)

    def provision(self, bundle_dir: Path, env_values: dict[str, str]) -> AzdCommandResult:
        self._ensure_environment(bundle_dir, env_values)
        code, stdout, stderr = run_cli(
            ["azd", "provision"], cwd=bundle_dir, env=env_values, timeout=self._timeout
        )
        message = redact_secrets((stderr or stdout or "").strip())
        return AzdCommandResult(success=code == 0, message=message)

    def deploy(self, bundle_dir: Path, env_values: dict[str, str]) -> AzdCommandResult:
        self._ensure_environment(bundle_dir, env_values)
        code, stdout, stderr = run_cli(
            ["azd", "deploy"], cwd=bundle_dir, env=env_values, timeout=self._timeout
        )
        message = redact_secrets((stderr or stdout or "").strip())
        return AzdCommandResult(success=code == 0, message=message)


class AzCliHealthChecker:
    """Verify liveness, Easy Auth, runtime scope, discovery, and telemetry reads."""

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        verify_full: bool = False,
        rbac_attempts: int = 3,
        rbac_retry_delay: float = 5.0,
    ) -> None:
        self._timeout = timeout
        self._verify_full = verify_full
        self._rbac_attempts = max(1, rbac_attempts)
        self._rbac_retry_delay = max(0.0, rbac_retry_delay)

    def _http_json(
        self,
        url: str,
        *,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        import urllib.error
        import urllib.request

        headers = {"Accept": "application/json"}
        data = None
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, headers=headers, method="POST" if data is not None else "GET"
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read().decode("utf-8") if hasattr(response, "read") else ""
                body = json.loads(raw) if raw else {}
                return int(getattr(response, "status", 200)), (
                    body if isinstance(body, dict) else {}
                )
        except urllib.error.HTTPError as exc:
            return int(exc.code), {}
        except Exception:
            return 0, {}

    def check(
        self, *, app_url: str, web_app_resource_id: str, principal_id: str
    ) -> HealthSignals:
        health_status, _ = self._http_json(f"{app_url}/healthz")
        liveness_ok = health_status == 200
        if not self._verify_full:
            return HealthSignals(liveness_ok=liveness_ok)

        anonymous_status, _ = self._http_json(f"{app_url}/api/runtime")
        anonymous_denied = anonymous_status in {401, 403}

        settings_code, settings_stdout, _settings_stderr = run_cli(
            [
                "az",
                "webapp",
                "config",
                "appsettings",
                "list",
                "--ids",
                web_app_resource_id,
                "--output",
                "json",
            ]
        )
        settings_entries = (
            _parse_json_output(settings_stdout, stage="az_webapp_health_settings")
            if settings_code == 0
            else []
        )
        settings = {
            str(item.get("name")): str(item.get("value"))
            for item in settings_entries
            if isinstance(item, dict) and item.get("name")
        }
        client_id = settings.get("AGENTOPS_APPLICATION_CLIENT_ID", "")
        expected_scope_raw = settings.get("AGENTOPS_OBSERVE_SCOPE", "")
        runtime_config_ok = bool(
            client_id
            and expected_scope_raw
            and settings.get("AGENTOPS_COCKPIT_MODE") == "hosted"
            and settings.get("AGENTOPS_UAMI_CLIENT_ID")
        )

        token = ""
        if client_id:
            token_code, token_stdout, _token_stderr = run_cli(
                [
                    "az",
                    "account",
                    "get-access-token",
                    "--scope",
                    f"api://{client_id}/.default",
                    "--query",
                    "accessToken",
                    "--output",
                    "tsv",
                ]
            )
            if token_code == 0:
                token = token_stdout.strip()

        auth_status, auth_context = self._http_json(
            f"{app_url}/api/auth/context", token=token or None
        )
        auth_context_ok = auth_status == 200 and bool(auth_context.get("user_id"))

        runtime_status, runtime = self._http_json(
            f"{app_url}/api/runtime", token=token or None
        )
        if runtime_status == 200:
            try:
                expected_scope = json.loads(expected_scope_raw)
            except json.JSONDecodeError:
                expected_scope = None
            runtime_config_ok = bool(
                runtime_config_ok
                and runtime.get("mode") == "hosted"
                and runtime.get("scope") == expected_scope
            )
        else:
            runtime_config_ok = False

        discovery_status = 0
        query_status = 0
        query: dict[str, Any] = {}
        for attempt in range(self._rbac_attempts):
            discovery_status, _discovery = self._http_json(
                f"{app_url}/api/observe/discovery", token=token or None
            )
            end = datetime.now(timezone.utc)
            query_status, query = self._http_json(
                f"{app_url}/api/observe/query",
                token=token or None,
                payload={
                    "view": "overview",
                    "filters": {
                        "start": (end - timedelta(hours=1)).isoformat(),
                        "end": end.isoformat(),
                    },
                },
            )
            if discovery_status == 200 and query_status == 200:
                break
            if attempt + 1 < self._rbac_attempts:
                import time

                time.sleep(self._rbac_retry_delay)
        diagnostics = query.get("diagnostics", {}) if isinstance(query, dict) else {}
        rbac_pending = (
            discovery_status in {401, 403}
            or query_status in {401, 403}
            or bool(diagnostics.get("permission_denied"))
        )
        return HealthSignals(
            liveness_ok=liveness_ok,
            anonymous_access_denied=anonymous_denied,
            auth_context_ok=auth_context_ok,
            runtime_config_ok=runtime_config_ok,
            resource_graph_ok=discovery_status == 200,
            uami_read_ok=query_status == 200,
            rbac_propagation_pending=rbac_pending,
        )


_BUNDLE_TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "templates" / "cockpit-hosted"
_BUNDLE_STATIC_FILES: tuple[Path, ...] = (
    Path("azure.yaml"),
    Path("app") / "main.py",
    Path("infra") / "main.bicep",
    Path("infra") / "main.parameters.json",
)
_BUNDLE_REQUIREMENTS_TEMPLATE = Path("app") / "requirements.txt.tmpl"
_ROLE_ASSIGNMENT_PARAMETER_KEYS = (
    "grantReaderOnResourceGroup",
    "foundryAccountNames",
    "foundryProjectRefs",
    "logAnalyticsWorkspaceNames",
)


def _apply_role_assignment_parameters(
    parameters_path: Path, role_assignment_parameters: dict[str, Any]
) -> None:
    """Patch the packaged ``main.parameters.json``'s 4 RBAC keys in place.

    The packaged file hardcodes ``grantReaderOnResourceGroup: false`` and
    empty ``foundryAccountNames``/``foundryProjectRefs``/
    ``logAnalyticsWorkspaceNames`` arrays -- if left untouched, a
    ``projects``/``foundry`` scope selection would silently deploy with
    *no* RBAC grants (under-provisioning), never grant Reader on the
    deployment resource group it does not need
    (:func:`classify_role_assignment_scope` already prevents
    over-provisioning), and would drift from the preview shown to the
    operator. Only these exact 4 keys are rewritten -- every azd ``${VAR}``
    placeholder and every other key is left byte-for-byte untouched.
    """
    document = json.loads(parameters_path.read_text(encoding="utf-8"))
    parameters = document.setdefault("parameters", {})
    for key in _ROLE_ASSIGNMENT_PARAMETER_KEYS:
        if key in role_assignment_parameters:
            parameters[key] = {"value": role_assignment_parameters[key]}
    parameters_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def materialize_bundle(
    bundle_dir: Path,
    *,
    agentops_version: str | None = None,
    role_assignment_parameters: dict[str, Any] | None = None,
) -> Path:
    """Materialize the version-matched hosted-Cockpit azd bundle into *bundle_dir*.

    Copies the packaged ``azure.yaml``/app/infra files byte-for-byte and
    renders ``app/requirements.txt.tmpl`` -> ``app/requirements.txt`` with
    ``__AGENTOPS_VERSION__`` substituted for the exact installed
    ``agentops-accelerator`` version (see :func:`_installed_version`, which
    never falls back to ``0.0.0`` on a normal install), so the deployed
    worker always installs the same release that produced the deployment.

    When *role_assignment_parameters* is supplied (see
    :func:`_resolve_role_assignment_scope` /
    :func:`bicep_role_assignment_parameters`), the copied
    ``infra/main.parameters.json``'s 4 RBAC parameter values are patched
    in place to match the actual resolved scope/preview instead of being
    left at their packaged, hardcoded defaults (see
    :func:`_apply_role_assignment_parameters`). Passing ``None`` (the
    default) leaves the file byte-for-byte as packaged, preserving prior
    behavior for any caller that does not yet resolve a scope.
    Returns *bundle_dir* for convenient chaining.
    """
    version = agentops_version or _installed_version()
    bundle_dir.mkdir(parents=True, exist_ok=True)

    for relative in _BUNDLE_STATIC_FILES:
        source = _BUNDLE_TEMPLATE_ROOT / relative
        destination = bundle_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    if role_assignment_parameters is not None:
        _apply_role_assignment_parameters(
            bundle_dir / "infra" / "main.parameters.json", role_assignment_parameters
        )

    requirements_source = _BUNDLE_TEMPLATE_ROOT / _BUNDLE_REQUIREMENTS_TEMPLATE
    requirements_text = requirements_source.read_text(encoding="utf-8")
    requirements_text = requirements_text.replace("__AGENTOPS_VERSION__", version)
    requirements_destination = bundle_dir / "app" / "requirements.txt"
    requirements_destination.parent.mkdir(parents=True, exist_ok=True)
    requirements_destination.write_text(requirements_text, encoding="utf-8")

    return bundle_dir


@dataclass(frozen=True)
class DeploymentAdapters:
    """Concrete adapters used by the production facade below.

    Every field defaults to ``None`` and is filled in by
    :func:`_default_adapters` with the ``az``/``azd``-CLI-backed
    implementation from this module. Tests (and any future non-CLI
    backend) can override individual adapters while leaving the rest at
    their production default.
    """

    azure_context: AzureContext | None = None
    project_resolver: ProjectResolver | None = None
    permission_checker: PermissionChecker | None = None
    app_registration: AppRegistrationClient | None = None
    telemetry_discovery: TelemetryDiscovery | None = None
    identity_resolver: ManagedIdentityResolver | None = None
    role_assignment_client: RoleAssignmentClient | None = None
    state_inspector: DeploymentStateInspector | None = None
    azd_runner: AzdCommandRunner | None = None
    health_checker: HealthChecker | None = None
    clock: Clock | None = None


def validate_easy_auth_template(bundle_dir: Path) -> None:
    """Fail before preview if the deployed Easy Auth contract cannot support OBO."""
    template_path = bundle_dir / "infra" / "main.bicep"
    try:
        compact = "".join(template_path.read_text(encoding="utf-8").split())
    except OSError as exc:
        raise PrerequisiteError(
            "The hosted Cockpit Easy Auth template is unavailable.",
            failed_checks=["easy_auth_template"],
            remediation="Reinstall AgentOps and rerun deployment preview.",
        ) from exc
    required_fragments = (
        "name:'authsettingsV2'",
        "requireAuthentication:true",
        "unauthenticatedClientAction:'Return401'",
        "tokenStore:{enabled:true}",
    )
    missing = [fragment for fragment in required_fragments if fragment not in compact]
    if missing:
        raise PrerequisiteError(
            "The hosted Cockpit Easy Auth template is not ready for protected-content OBO.",
            failed_checks=["easy_auth_token_store"],
            remediation=(
                "Restore the packaged authsettingsV2 configuration with authentication, "
                "401 enforcement, and the Easy Auth token store enabled."
            ),
        )


def _default_adapters(adapters: DeploymentAdapters | None) -> DeploymentAdapters:
    """Fill in every unset adapter with its production ``az``/``azd`` default."""
    current = adapters or DeploymentAdapters()
    return DeploymentAdapters(
        azure_context=current.azure_context or AzCliContext(),
        project_resolver=current.project_resolver or WorkspaceProjectResolver(),
        permission_checker=current.permission_checker or AzCliPermissionChecker(),
        app_registration=current.app_registration or AzCliAppRegistrationClient(),
        telemetry_discovery=current.telemetry_discovery or AzCliTelemetryDiscovery(),
        identity_resolver=current.identity_resolver or AzCliManagedIdentityResolver(),
        role_assignment_client=current.role_assignment_client or AzCliRoleAssignmentClient(),
        state_inspector=current.state_inspector or AzCliDeploymentStateInspector(),
        azd_runner=current.azd_runner or AzdCliCommandRunner(),
        health_checker=current.health_checker or AzCliHealthChecker(verify_full=True),
        clock=current.clock or SystemClock(),
    )


@dataclass(frozen=True)
class DeploymentPlan:
    """Fully-resolved, preview-only deployment plan ready for confirmation.

    Produced by :func:`prepare_deployment` (never mutates Azure state) and
    consumed by :func:`execute_deployment` (the sole mutation entry point).
    """

    selection: DeploymentSelection
    preview: DeploymentPreview
    explicit_inputs_complete: bool
    permission_checks: list[PermissionResult]
    scope_warnings: list[str]
    bundle_dir: Path
    initiated_by: str | None = None


def prepare_deployment(
    request: DeploymentRequest, *, adapters: DeploymentAdapters | None = None
) -> DeploymentPlan:
    """Resolve, validate, and preview a deployment without mutating anything.

    This is the production facade a thin Typer CLI command calls before
    ever prompting for confirmation. In order, it: resolves selection
    precedence/ambiguity and workspace project resolution
    (``resolve_selection``), validates ARM/Graph prerequisites
    (``validate_prerequisites``), resolves the exact RBAC parameter values
    the effective scope requires (``_resolve_role_assignment_scope``),
    materializes the version-matched azd bundle with those parameter
    values patched in (``materialize_bundle``), and builds the combined
    azd/Bicep + RBAC + FIC + settings preview (``build_preview``) --
    exactly the same pure functions already covered by this module's owned
    unit tests, wired to the concrete ``az``/``azd`` adapters by default.
    Nothing here calls ``azd provision``/``azd deploy`` (only ``azd
    provision --preview``, itself a dry run), which preserves
    preview-before-mutation end-to-end.
    """
    resolved = _default_adapters(adapters)
    assert resolved.azure_context is not None
    assert resolved.project_resolver is not None
    assert resolved.permission_checker is not None
    assert resolved.app_registration is not None
    assert resolved.telemetry_discovery is not None
    assert resolved.identity_resolver is not None
    assert resolved.azd_runner is not None

    selection, explicit_inputs_complete, warnings = resolve_selection(
        request,
        context=resolved.azure_context,
        project_resolver=resolved.project_resolver,
        app_registration=resolved.app_registration,
    )
    planned_role_assignments = _resolve_planned_role_assignments(
        selection, telemetry_discovery=resolved.telemetry_discovery
    )
    permission_checks = validate_prerequisites(
        selection,
        permissions=resolved.permission_checker,
        role_assignment_scopes=[
            assignment.scope_resource_id for assignment in planned_role_assignments
        ],
    )
    role_assignment_parameters = bicep_role_assignment_parameters(
        selection, planned_role_assignments
    )
    bundle_dir = bundle_dir_for(selection.workspace)
    materialize_bundle(bundle_dir, role_assignment_parameters=role_assignment_parameters)
    validate_easy_auth_template(bundle_dir)

    preview = build_preview(
        selection,
        telemetry_discovery=resolved.telemetry_discovery,
        identity_resolver=resolved.identity_resolver,
        app_registration=resolved.app_registration,
        azd_runner=resolved.azd_runner,
        bundle_dir=bundle_dir,
    )

    actor_getter = getattr(resolved.azure_context, "current_actor_id", None)
    initiated_by = actor_getter() if callable(actor_getter) else None
    return DeploymentPlan(
        selection=selection,
        preview=preview,
        explicit_inputs_complete=explicit_inputs_complete,
        permission_checks=permission_checks,
        scope_warnings=warnings,
        bundle_dir=bundle_dir,
        initiated_by=initiated_by,
    )


def execute_deployment(
    plan: DeploymentPlan,
    *,
    yes: bool,
    interactive_confirmed: bool = False,
    adapters: DeploymentAdapters | None = None,
) -> HostedCockpitDeployment:
    """Confirm and execute a previously-built :class:`DeploymentPlan`.

    The sole mutation entry point of the production facade: it re-checks
    preview-before-mutation/confirmation gating (``validate_confirmation``)
    before ever calling ``deploy``, so a caller cannot bypass the guard by
    constructing a :class:`DeploymentPlan` and invoking this directly with
    a blocked or unconfirmed preview.
    """
    resolved = _default_adapters(adapters)
    assert resolved.azd_runner is not None
    assert resolved.app_registration is not None
    assert resolved.health_checker is not None
    assert resolved.clock is not None

    validate_confirmation(
        plan.preview,
        yes=yes,
        explicit_inputs_complete=plan.explicit_inputs_complete,
        interactive_confirmed=interactive_confirmed,
    )
    return deploy(
        plan.selection,
        plan.preview,
        azd_runner=resolved.azd_runner,
        app_registration=resolved.app_registration,
        health_checker=resolved.health_checker,
        clock=resolved.clock,
        bundle_dir=plan.bundle_dir,
        role_assignment_client=resolved.role_assignment_client,
        identity_resolver=resolved.identity_resolver,
        state_inspector=resolved.state_inspector,
        initiated_by=plan.initiated_by,
        approval_method="non_interactive" if yes else "interactive",
    )
