"""Check: signed-in principal has Cognitive Services OpenAI User RBAC.

Cloud eval graders and any other data-plane Azure OpenAI call run by
``agentops eval run`` need the **Cognitive Services OpenAI User** role
(or another role granting the
``Microsoft.CognitiveServices/accounts/OpenAI/*/action`` data action) on
the AI Services account that backs the Foundry project. Without it the
runtime raises ``PermissionDenied`` / ``AuthenticationError`` mid-run
even after Azure CLI authentication looks fine.

Today the tutorial and the ``agentops-eval`` skill document this, and
the CLI surfaces a clearer warning since v0.3.6, but Doctor was silent
on the missing assignment. This check fills the gap by inspecting the
existing :class:`AzureResourcesPayload` (resource group, account name,
account resource id) and querying ``azure-mgmt-authorization`` for role
assignments at and above the account scope.

The check is **read-only**: it never grants or modifies RBAC. It stays
silent when:

- the Azure resources source is disabled or returned a non-``ok`` status;
- ``azure-mgmt-authorization`` is not installed;
- the signed-in principal cannot be identified (no ``oid`` claim);
- the role assignments listing fails (permissions / network / etc.).

Issue: https://github.com/Azure/agentops/issues/228
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from typing import Any, List, Optional, Sequence

from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.sources.azure_resources import AzureResourcesPayload

log = logging.getLogger(__name__)

SOURCE_NAME = "azure_resources"

# https://learn.microsoft.com/azure/role-based-access-control/built-in-roles/ai-machine-learning#cognitive-services-openai-user
COGNITIVE_SERVICES_OPENAI_USER_ROLE_ID: str = (
    "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"
)

# Roles that *also* grant the OpenAI data action and therefore satisfy the
# same need as Cognitive Services OpenAI User. Listed by definition GUID
# so renames in display name do not break detection.
_OPENAI_DATA_PLANE_ROLE_IDS: frozenset[str] = frozenset(
    {
        # Cognitive Services OpenAI User
        "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd",
        # Cognitive Services OpenAI Contributor
        "a001fd3d-188f-4b5d-821b-7da978bf7442",
        # Cognitive Services Contributor (broader admin role; superset of data
        # action via control plane).
        "25fbc0a9-bd7c-42a3-aa1a-3b75d497ee68",
    }
)

_FINDING_ID = "security.missing_openai_data_plane_rbac"


def run_rbac_openai_data_plane_check(
    resources: Optional[AzureResourcesPayload],
) -> List[Finding]:
    """Return findings if the signed-in principal lacks OpenAI data-plane RBAC."""
    if resources is None:
        return []
    diag = resources.diagnostics or {}
    if diag.get("status") != "ok":
        return []
    target = diag.get("target")
    account_name = diag.get("account") or (
        resources.account.name if resources.account else None
    )
    resource_group = diag.get("resource_group")
    subscription_id = _subscription_id_from_target(target)
    if not target or not account_name or not subscription_id:
        return []

    try:  # lazy import — keeps `agentops` CLI light
        from ._rbac_authorization import (
            AuthorizationCheckError,
            list_principal_role_definition_ids,
            resolve_signed_in_principal_object_id,
        )
    except ImportError as exc:  # pragma: no cover - test env always installs it
        log.debug("rbac_openai_data_plane: helper unavailable: %s", exc)
        return []

    try:
        principal_object_id = resolve_signed_in_principal_object_id()
    except AuthorizationCheckError as exc:
        log.info(
            "rbac_openai_data_plane: skipped (cannot resolve principal): %s", exc
        )
        return []

    try:
        role_definition_ids = list_principal_role_definition_ids(
            subscription_id=subscription_id,
            scope=target,
            principal_object_id=principal_object_id,
        )
    except AuthorizationCheckError as exc:
        log.info(
            "rbac_openai_data_plane: skipped (role assignments listing failed): %s",
            exc,
        )
        return []

    granted_role_ids = {rid.lower() for rid in role_definition_ids}
    if granted_role_ids & {rid.lower() for rid in _OPENAI_DATA_PLANE_ROLE_IDS}:
        return []

    return [
        _missing_rbac_finding(
            account_name=account_name,
            resource_group=resource_group,
            subscription_id=subscription_id,
            scope=target,
            principal_object_id=principal_object_id,
            granted_role_ids=sorted(granted_role_ids),
        )
    ]


def _subscription_id_from_target(target: Optional[str]) -> Optional[str]:
    """Extract the subscription GUID from an ARM resource id."""
    if not target:
        return None
    parts = target.strip("/").split("/")
    try:
        idx = parts.index("subscriptions")
    except ValueError:
        return None
    if idx + 1 >= len(parts):
        return None
    return parts[idx + 1]


def _missing_rbac_finding(
    *,
    account_name: str,
    resource_group: Optional[str],
    subscription_id: str,
    scope: str,
    principal_object_id: str,
    granted_role_ids: Sequence[str],
) -> Finding:
    rg_clause = (
        f" (resource group `{resource_group}`)" if resource_group else ""
    )
    az_command = (
        "az role assignment create "
        f"--assignee {principal_object_id} "
        '--role "Cognitive Services OpenAI User" '
        f"--scope {scope}"
    )
    return Finding(
        id=_FINDING_ID,
        severity=Severity.WARNING,
        category=Category.SECURITY,
        title=(
            "Signed-in principal is missing Cognitive Services OpenAI User on "
            f"`{account_name}`"
        ),
        summary=(
            "The Doctor signed-in principal does not hold the **Cognitive "
            "Services OpenAI User** role (or any role with the OpenAI "
            "data-plane action) at or above the AI Services account "
            f"`{account_name}`{rg_clause}. Cloud eval graders and other "
            "data-plane Azure OpenAI calls will fail with `PermissionDenied` "
            "/ `AuthenticationError` until this assignment exists and Entra "
            "ID propagates it (typically a few minutes)."
        ),
        recommendation=(
            "Grant the role at the AI Services account scope (preferred) or "
            "the resource group scope, then wait for propagation before "
            f"re-running `agentops eval run`. Suggested command:\n\n"
            f"```bash\n{az_command}\n```\n\n"
            "If your team scopes this role at the subscription or resource "
            "group level instead, grant it there. Doctor will detect any "
            "role at or above the account scope. If you intentionally use a "
            "different role granting "
            "`Microsoft.CognitiveServices/accounts/OpenAI/*/action`, add its "
            "definition GUID to "
            "`agentops/agent/checks/rbac_openai_data_plane.py:"
            "_OPENAI_DATA_PLANE_ROLE_IDS`."
        ),
        source=SOURCE_NAME,
        evidence={
            "account": account_name,
            "resource_group": resource_group,
            "subscription_id": subscription_id,
            "scope": scope,
            "principal_object_id": principal_object_id,
            "granted_role_definition_ids": list(granted_role_ids),
            "required_role": "Cognitive Services OpenAI User",
            "required_role_id": COGNITIVE_SERVICES_OPENAI_USER_ROLE_ID,
            "remediation_command": az_command,
        },
    )


# Re-exported only so callers and tests can decode an Entra ID access token's
# ``oid`` claim without depending on a dedicated JWT library. Kept here (not
# in the helper module) because the helper module lazy-imports azure SDKs.


def decode_oid_from_jwt(token: str) -> Optional[str]:
    """Return the ``oid`` (object id) claim of an Entra access token.

    Returns ``None`` when the token is malformed, when the payload cannot
    be base64-decoded, or when the JSON has no ``oid`` field. The token's
    signature is **not** verified — Doctor consumes it strictly as a
    self-attested identifier already produced by the calling Azure SDK.
    """
    if not token or token.count(".") < 2:
        return None
    payload = token.split(".")[1]
    padding = "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload + padding)
        claims: Any = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(claims, dict):
        return None
    oid = claims.get("oid")
    return oid if isinstance(oid, str) and oid else None
