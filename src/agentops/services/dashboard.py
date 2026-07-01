"""Thin service layer for the Foundry operations Azure Monitor workbook.

This module is the single place that knows how to:

* load the packaged ``foundry-ops.workbook.json`` gallery template,
* build the Azure portal deep link for the deployed workbook,
* run the RBAC / diagnostic-settings preflight for ``agentops telemetry
  dashboard deploy``, and
* wrap the workbook content into a ``Microsoft.Insights/workbooks`` ARM
  template and (optionally) deploy it via the Azure CLI.

Azure SDK / CLI access is kept lazy and fail-open so importing this module
never requires the management SDKs or a live Azure session. The CLI layer in
:mod:`agentops.cli.app` stays thin and delegates the Azure logic here.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from importlib.resources import files as _pkg_files
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

log = logging.getLogger(__name__)

_TEMPLATE_PACKAGE = "agentops.templates"
_WORKBOOK_RESOURCE_PATH = "workbooks/foundry-ops.workbook.json"

#: ARM resource type for an Azure Monitor workbook.
WORKBOOK_RESOURCE_TYPE = "Microsoft.Insights/workbooks"

#: Diagnostic log categories the Azure OpenAI resource must emit for the
#: workbook queries to return data.
REQUIRED_DIAGNOSTIC_CATEGORIES = ("RequestResponse", "AzureOpenAIRequestUsage")

_PORTAL_BASE = "https://portal.azure.com"

# Deterministic namespace so ``deploy`` and ``open`` agree on the workbook
# resource name without querying Azure.
_WORKBOOK_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "agentops.foundry-ops.workbook")

# Built-in Azure role definition GUIDs used by the preflight. A role that can
# write workbooks (Owner / Contributor / Workbook Contributor) satisfies the
# deploy requirement; a role that can read the workspace satisfies the query
# requirement.
_ROLE_WORKBOOK_CONTRIBUTOR = "e8ddcd69-c73f-4f9f-9844-4100522f16ad"
_ROLE_LOG_ANALYTICS_READER = "73c42c96-874c-492b-b04d-ab87d138a893"
_ROLE_LOG_ANALYTICS_CONTRIBUTOR = "92aaf0da-9dab-42b6-94a3-d43ce8d16293"
_ROLE_READER = "acdd72a7-3385-48ef-bd42-f606fba81ae7"
_ROLE_CONTRIBUTOR = "b24988ac-6180-42a0-ab88-20f7382dd24c"
_ROLE_OWNER = "8e3af657-a8ff-443c-a75c-2fe8c4bcb635"

_WORKBOOK_WRITE_ROLES = frozenset(
    {_ROLE_WORKBOOK_CONTRIBUTOR, _ROLE_CONTRIBUTOR, _ROLE_OWNER}
)
_WORKSPACE_READ_ROLES = frozenset(
    {
        _ROLE_LOG_ANALYTICS_READER,
        _ROLE_LOG_ANALYTICS_CONTRIBUTOR,
        _ROLE_READER,
        _ROLE_CONTRIBUTOR,
        _ROLE_OWNER,
    }
)


class DashboardError(RuntimeError):
    """Raised for user-facing dashboard failures (surfaced by the CLI)."""


@dataclass
class DashboardTarget:
    """Resolved Azure context for the workbook deploy."""

    name: str = "AgentOps Foundry operations"
    subscription_id: Optional[str] = None
    resource_group: Optional[str] = None
    workspace_id: Optional[str] = None
    aoai_resource_id: Optional[str] = None
    aoai_account_name: Optional[str] = None
    tenant_id: Optional[str] = None
    location: Optional[str] = None
    discovery: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PreflightResult:
    """Outcome of a single preflight check.

    ``ok`` is ``True`` when the check passed. ``level`` is ``"ok"``,
    ``"warn"`` (deploy can still proceed) or ``"error"`` (deploy must stop).
    ``messages`` carries friendly, actionable text for the CLI to print.
    """

    ok: bool
    level: str
    messages: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Template loading
# ---------------------------------------------------------------------------
def load_workbook_template() -> str:
    """Return the packaged workbook JSON as a string."""
    resource = _pkg_files(_TEMPLATE_PACKAGE).joinpath(_WORKBOOK_RESOURCE_PATH)
    return resource.read_text(encoding="utf-8")


def load_workbook_content() -> Dict[str, Any]:
    """Return the packaged workbook JSON parsed into a dict."""
    try:
        return json.loads(load_workbook_template())
    except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - packaging
        raise DashboardError(
            f"Could not load the packaged workbook template: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Portal URL
# ---------------------------------------------------------------------------
def make_workbook_resource_id(
    subscription_id: str, resource_group: str, name: str
) -> str:
    """Build the deterministic ARM id for the deployed workbook.

    The workbook resource name is a GUID derived from the display name and
    resource group so ``deploy`` and ``open`` agree without a live lookup.
    """
    guid = str(uuid.uuid5(_WORKBOOK_NAMESPACE, f"{resource_group}:{name}"))
    return (
        f"/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/{WORKBOOK_RESOURCE_TYPE}/{guid}"
    )


def build_workbook_portal_url(
    *,
    workbook_resource_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    subscription_id: Optional[str] = None,
    resource_group: Optional[str] = None,
    name: Optional[str] = None,
) -> str:
    """Return an Azure portal deep link for the workbook.

    When the workbook ARM id is known (directly, or derivable from
    subscription/resource-group/name) the link opens that workbook. Otherwise
    it falls back to the Azure Monitor Workbooks gallery so the tile is never
    broken.
    """
    rid = workbook_resource_id
    if not rid and subscription_id and resource_group and name:
        rid = make_workbook_resource_id(subscription_id, resource_group, name)
    if rid:
        prefix = f"{_PORTAL_BASE}/#@{tenant_id}" if tenant_id else f"{_PORTAL_BASE}/#"
        return f"{prefix}/resource{rid}/workbook"
    return (
        f"{_PORTAL_BASE}/#view/Microsoft_Azure_Monitoring_Workbooks"
        "/WorkbookMenuBlade/~/gallery"
    )


# ---------------------------------------------------------------------------
# Diagnostic settings
# ---------------------------------------------------------------------------
def missing_diagnostic_categories(enabled_categories: Iterable[str]) -> List[str]:
    """Return the required categories not present in ``enabled_categories``."""
    enabled = {str(c) for c in enabled_categories}
    return [c for c in REQUIRED_DIAGNOSTIC_CATEGORIES if c not in enabled]


def build_diagnostic_settings_command(
    *,
    aoai_resource_id: Optional[str],
    workspace_id: Optional[str],
    name: str = "agentops-foundry-ops",
) -> str:
    """Return the exact ``az`` command that wires the required categories."""
    resource = aoai_resource_id or "<azure-openai-resource-id>"
    workspace = workspace_id or "<log-analytics-workspace-id>"
    logs = json.dumps(
        [{"category": c, "enabled": True} for c in REQUIRED_DIAGNOSTIC_CATEGORIES]
    )
    return (
        "az monitor diagnostic-settings create "
        f"--name {name} "
        f"--resource {resource} "
        f"--workspace {workspace} "
        f"--logs '{logs}'"
    )


# ---------------------------------------------------------------------------
# RBAC preflight
# ---------------------------------------------------------------------------
def check_rbac(
    *,
    subscription_id: Optional[str],
    resource_group: Optional[str],
    workspace_id: Optional[str],
) -> PreflightResult:
    """Preflight the caller's RBAC for a workbook deploy.

    Requires ``Workbook Contributor`` (or a superset) on the resource group and
    ``Log Analytics Reader`` (or a superset) on the workspace. When the RBAC
    listing cannot run (SDK missing, no credential, listing denied) the check
    fails **open** with a warning so ``deploy`` can still be attempted and let
    ARM enforce permissions.
    """
    if not subscription_id or not resource_group:
        return PreflightResult(
            ok=False,
            level="error",
            messages=[
                "Cannot check RBAC: subscription and resource group are "
                "unknown. Pass --subscription and --resource-group, or run "
                "from an initialized AgentOps workspace.",
            ],
        )

    rg_scope = f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
    try:
        from agentops.agent.checks._rbac_authorization import (
            AuthorizationCheckError,
            list_principal_role_definition_ids,
            resolve_signed_in_principal_object_id,
        )
    except ImportError as exc:  # pragma: no cover - shipped together
        return PreflightResult(
            ok=True,
            level="warn",
            messages=[
                "Skipping RBAC preflight (authorization helpers unavailable: "
                f"{exc}). ARM will enforce permissions at deploy time.",
            ],
        )

    try:
        principal = resolve_signed_in_principal_object_id()
        rg_roles = set(
            list_principal_role_definition_ids(
                subscription_id=subscription_id,
                scope=rg_scope,
                principal_object_id=principal,
            )
        )
    except AuthorizationCheckError as exc:
        return PreflightResult(
            ok=True,
            level="warn",
            messages=[
                f"Skipping RBAC preflight ({exc}). ARM will enforce "
                "permissions at deploy time.",
            ],
        )

    messages: List[str] = []
    ok = True
    if not (rg_roles & _WORKBOOK_WRITE_ROLES):
        ok = False
        messages.append(
            "You need the 'Workbook Contributor' role on resource group "
            f"'{resource_group}'. Ask an admin to grant it, or run with "
            "--dry-run to emit the ARM template instead."
        )

    if workspace_id:
        try:
            ws_roles = set(
                list_principal_role_definition_ids(
                    subscription_id=subscription_id,
                    scope=workspace_id,
                    principal_object_id=principal,
                )
            )
        except AuthorizationCheckError as exc:
            messages.append(
                f"Could not verify Log Analytics access ({exc}); make sure you "
                "have 'Log Analytics Reader' on the workspace."
            )
            ws_roles = set()
        if ws_roles and not (ws_roles & _WORKSPACE_READ_ROLES):
            ok = False
            messages.append(
                "You need the 'Log Analytics Reader' role on workspace "
                f"'{workspace_id}' so the workbook can query it."
            )

    if ok and not messages:
        messages.append(
            "RBAC preflight passed: you can write workbooks to "
            f"'{resource_group}' and read the workspace."
        )
    return PreflightResult(ok=ok, level="ok" if ok else "error", messages=messages)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
_WORKSPACE_ENV_KEYS = (
    "AZURE_LOG_ANALYTICS_WORKSPACE_ID",
    "AZURE_LOG_ANALYTICS_WORKSPACE_RESOURCE_ID",
    "LOG_ANALYTICS_WORKSPACE_ID",
    "AZURE_MONITOR_WORKSPACE_ID",
    "AZURE_MONITOR_WORKSPACE_RESOURCE_ID",
)
_ACCOUNT_ENV_KEYS = (
    "AZURE_OPENAI_RESOURCE",
    "AZURE_OPENAI_RESOURCE_NAME",
    "AZURE_AI_SERVICES_RESOURCE_NAME",
    "AZURE_AI_SERVICES_NAME",
)
_TENANT_ENV_KEYS = ("AZURE_TENANT_ID",)


def _first(values: Mapping[str, str], keys: Iterable[str]) -> Optional[str]:
    for key in keys:
        val = values.get(key)
        if val:
            return val
    return None


def discover_target(
    workspace: Optional[Path],
    *,
    subscription_id: Optional[str] = None,
    resource_group: Optional[str] = None,
    workspace_id: Optional[str] = None,
    name: Optional[str] = None,
) -> DashboardTarget:
    """Resolve the deploy target from explicit flags, then the AZD env.

    Reuses the AgentOps foundry discovery path (the AZD ``.env`` read used by
    the Azure resources doctor source). Explicit arguments always win.
    """
    env_values: Dict[str, str] = {}
    discovery: Dict[str, Any] = {}
    try:
        from agentops.agent.sources.azure_resources import (
            _discover_azd_environment,
        )

        _env_name, env_values, azd_diag = _discover_azd_environment(workspace)
        discovery["azd"] = azd_diag
    except Exception as exc:  # noqa: BLE001 - discovery is best-effort
        discovery["azd"] = {"status": "error", "reason": str(exc)}

    sub = subscription_id or env_values.get("AZURE_SUBSCRIPTION_ID")
    rg = resource_group or env_values.get("AZURE_RESOURCE_GROUP")
    ws = workspace_id or _first(env_values, _WORKSPACE_ENV_KEYS)
    tenant = _first(env_values, _TENANT_ENV_KEYS)
    account = _first(env_values, _ACCOUNT_ENV_KEYS)

    aoai_resource_id: Optional[str] = None
    if account and account.startswith("/subscriptions/"):
        aoai_resource_id = account
        account = account.rstrip("/").rsplit("/", 1)[-1]
    elif account and sub and rg:
        aoai_resource_id = (
            f"/subscriptions/{sub}/resourceGroups/{rg}"
            f"/providers/Microsoft.CognitiveServices/accounts/{account}"
        )

    return DashboardTarget(
        name=name or "AgentOps Foundry operations",
        subscription_id=sub,
        resource_group=rg,
        workspace_id=ws,
        aoai_resource_id=aoai_resource_id,
        aoai_account_name=account,
        tenant_id=tenant,
        discovery=discovery,
    )


# ---------------------------------------------------------------------------
# ARM template + deploy
# ---------------------------------------------------------------------------
def build_arm_template(
    *,
    target: DashboardTarget,
    workbook_content: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Wrap the workbook content into a deployable ARM template."""
    content = (
        workbook_content if workbook_content is not None else load_workbook_content()
    )
    guid = str(
        uuid.uuid5(_WORKBOOK_NAMESPACE, f"{target.resource_group}:{target.name}")
    )
    resource: Dict[str, Any] = {
        "type": WORKBOOK_RESOURCE_TYPE,
        "apiVersion": "2022-04-01",
        "name": guid,
        # Workbooks reject "global"; resolve to the target region at deploy
        # time. The RG-scoped deployment always has a valid location, and an
        # explicit target.location (a real Azure region) still wins when set.
        "location": target.location or "[resourceGroup().location]",
        "kind": "shared",
        "properties": {
            "displayName": target.name,
            "serializedData": json.dumps(content),
            "version": "1.0",
            "sourceId": target.workspace_id or "Azure Monitor",
            "category": "workbook",
        },
    }
    return {
        "$schema": (
            "https://schema.management.azure.com/schemas/2019-04-01/"
            "deploymentTemplate.json#"
        ),
        "contentVersion": "1.0.0.0",
        "resources": [resource],
        "outputs": {
            "workbookId": {
                "type": "string",
                "value": f"[resourceId('{WORKBOOK_RESOURCE_TYPE}', '{guid}')]",
            }
        },
    }


def _az_executable() -> Optional[str]:
    return shutil.which("az") or shutil.which("az.cmd")


def deploy_workbook(
    *,
    target: DashboardTarget,
    workbook_content: Optional[Dict[str, Any]] = None,
    timeout: int = 300,
) -> str:
    """Deploy the workbook via ``az deployment group create``.

    Returns the portal URL for the deployed workbook. Raises
    :class:`DashboardError` with a friendly message on any failure.
    """
    if not target.subscription_id or not target.resource_group:
        raise DashboardError(
            "Deploy needs a subscription and resource group. Pass "
            "--subscription and --resource-group, or run from an initialized "
            "AgentOps workspace."
        )
    az = _az_executable()
    if az is None:
        raise DashboardError(
            "The Azure CLI ('az') was not found on PATH. Install it and run "
            "'az login', or use --dry-run to emit the ARM template."
        )

    template = build_arm_template(target=target, workbook_content=workbook_content)
    with tempfile.TemporaryDirectory(prefix="agentops-workbook-") as tmp:
        template_path = Path(tmp) / "foundry-ops.deploy.json"
        template_path.write_text(json.dumps(template), encoding="utf-8")
        cmd = [
            az,
            "deployment",
            "group",
            "create",
            "--subscription",
            target.subscription_id,
            "--resource-group",
            target.resource_group,
            "--name",
            "agentops-foundry-ops",
            "--template-file",
            str(template_path),
            "--only-show-errors",
            "--output",
            "json",
        ]
        try:
            completed = subprocess.run(  # noqa: S603 - args are controlled
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DashboardError(
                f"Failed to run 'az deployment group create': {exc}"
            ) from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise DashboardError(
            f"Workbook deployment failed. The Azure CLI reported:\n{detail}"
        )

    return build_workbook_portal_url(
        subscription_id=target.subscription_id,
        resource_group=target.resource_group,
        name=target.name,
        tenant_id=target.tenant_id,
    )
