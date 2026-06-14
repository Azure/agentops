"""Lazy Azure SDK glue for the ``rbac_openai_data_plane`` Doctor check.

Kept in a private module so the parent check can attempt the lazy
import in a single place and stay silent when ``azure-identity`` /
``azure-mgmt-authorization`` are not installed. All errors that should
make the check skip are normalised into :class:`AuthorizationCheckError`.
"""

from __future__ import annotations

import logging
from typing import List

log = logging.getLogger(__name__)


class AuthorizationCheckError(RuntimeError):
    """Raised when the RBAC check cannot run for an environmental reason."""


def resolve_signed_in_principal_object_id() -> str:
    """Return the ``oid`` claim of the shared Azure credential's access token.

    Raises :class:`AuthorizationCheckError` when the credential chain cannot
    return a token, or when the token does not expose an ``oid`` claim.
    """
    try:
        from agentops.agent.sources._credentials import (
            format_source_error,
            get_shared_credential,
        )
    except ImportError as exc:  # pragma: no cover - shipped together
        raise AuthorizationCheckError(
            f"shared credential factory unavailable: {exc}"
        ) from exc

    try:
        credential = get_shared_credential(process_timeout=30)
        token = credential.get_token("https://management.azure.com/.default")
    except Exception as exc:  # noqa: BLE001 - normalised to skip-error
        raise AuthorizationCheckError(format_source_error(exc)) from exc

    from agentops.agent.checks.rbac_openai_data_plane import decode_oid_from_jwt

    oid = decode_oid_from_jwt(getattr(token, "token", "") or "")
    if not oid:
        raise AuthorizationCheckError(
            "access token did not include an 'oid' claim; cannot identify "
            "the signed-in principal"
        )
    return oid


def list_principal_role_definition_ids(
    *,
    subscription_id: str,
    scope: str,
    principal_object_id: str,
) -> List[str]:
    """List role definition GUIDs assigned to the principal at/above scope.

    Uses ``RoleAssignmentsOperations.list_for_scope`` with the
    ``atScopeAndAbove() and assignedTo('<oid>')`` filter so management-plane
    inheritance (subscription, resource group, account) is honoured.
    """
    try:
        from azure.mgmt.authorization import AuthorizationManagementClient
    except ImportError as exc:
        raise AuthorizationCheckError(
            "azure-mgmt-authorization not installed; install "
            "`agentops-accelerator[agent]` (or add the package directly) to "
            "enable the OpenAI data-plane RBAC check"
        ) from exc

    try:
        from agentops.agent.sources._credentials import (
            format_source_error,
            get_shared_credential,
        )
    except ImportError as exc:  # pragma: no cover - shipped together
        raise AuthorizationCheckError(
            f"shared credential factory unavailable: {exc}"
        ) from exc

    try:
        credential = get_shared_credential(process_timeout=30)
        client = AuthorizationManagementClient(
            credential=credential,
            subscription_id=subscription_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise AuthorizationCheckError(format_source_error(exc)) from exc

    try:
        assignments = list(
            client.role_assignments.list_for_scope(
                scope=scope,
                filter=(
                    f"atScopeAndAbove() and assignedTo('{principal_object_id}')"
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001
        raise AuthorizationCheckError(format_source_error(exc)) from exc

    role_definition_ids: List[str] = []
    for assignment in assignments:
        rd_id = (
            getattr(assignment, "role_definition_id", None)
            or getattr(getattr(assignment, "properties", None), "role_definition_id", None)
        )
        if not rd_id:
            continue
        # role_definition_id is a full ARM id ending in `/<guid>`.
        guid = rd_id.rstrip("/").rsplit("/", 1)[-1]
        if guid:
            role_definition_ids.append(guid)
    return role_definition_ids
