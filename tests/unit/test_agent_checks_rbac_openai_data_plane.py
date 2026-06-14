"""Unit tests for ``agentops.agent.checks.rbac_openai_data_plane``.

The check must:

- Stay silent when the Azure resources source skipped or did not finish.
- Decode the signed-in principal's ``oid`` claim from the access token
  exposed by the shared credential factory.
- Detect that the principal already holds **Cognitive Services OpenAI
  User** (or another role granting the OpenAI data action) at the AI
  Services account scope or above.
- Emit a single :class:`Finding` with the ``az role assignment create``
  remediation when the role is missing.

All Azure SDK calls are mocked so the test never hits Azure.
"""

from __future__ import annotations

import base64
import json
from typing import Any, List
from unittest.mock import MagicMock, patch

import pytest

from agentops.agent.checks import rbac_openai_data_plane as check
from agentops.agent.checks._rbac_authorization import AuthorizationCheckError
from agentops.agent.findings import Category, Severity
from agentops.agent.sources.azure_resources import (
    AzureResourcesPayload,
    CognitiveAccountSnapshot,
)


_SUBSCRIPTION_ID = "11111111-1111-1111-1111-111111111111"
_PRINCIPAL_OID = "22222222-2222-2222-2222-222222222222"
_ACCOUNT_TARGET = (
    f"/subscriptions/{_SUBSCRIPTION_ID}/resourceGroups/rg-x/providers/"
    "Microsoft.CognitiveServices/accounts/ai-account"
)


def _payload(*, status: str = "ok") -> AzureResourcesPayload:
    return AzureResourcesPayload(
        account=CognitiveAccountSnapshot(name="ai-account"),
        diagnostics={
            "status": status,
            "target": _ACCOUNT_TARGET,
            "account": "ai-account",
            "resource_group": "rg-x",
        },
    )


def _fake_jwt(claims: dict[str, Any]) -> str:
    """Return a token ``header.payload.signature`` that decodes to ``claims``."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps(claims).encode("utf-8")
    ).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


# ---------------------------------------------------------------------------
# decode_oid_from_jwt
# ---------------------------------------------------------------------------


def test_decode_oid_returns_oid_claim() -> None:
    token = _fake_jwt({"oid": _PRINCIPAL_OID, "aud": "x"})
    assert check.decode_oid_from_jwt(token) == _PRINCIPAL_OID


def test_decode_oid_returns_none_for_malformed_token() -> None:
    assert check.decode_oid_from_jwt("not-a-token") is None
    assert check.decode_oid_from_jwt("") is None
    assert check.decode_oid_from_jwt("a.b") is None


def test_decode_oid_returns_none_when_payload_invalid_json() -> None:
    token = "header." + base64.urlsafe_b64encode(b"not-json").rstrip(b"=").decode() + ".sig"
    assert check.decode_oid_from_jwt(token) is None


def test_decode_oid_returns_none_when_oid_absent() -> None:
    token = _fake_jwt({"aud": "x", "tid": "tenant"})
    assert check.decode_oid_from_jwt(token) is None


# ---------------------------------------------------------------------------
# run_rbac_openai_data_plane_check  -- skip paths
# ---------------------------------------------------------------------------


def test_skips_when_resources_is_none() -> None:
    assert check.run_rbac_openai_data_plane_check(None) == []


def test_skips_when_status_not_ok() -> None:
    assert check.run_rbac_openai_data_plane_check(_payload(status="skipped")) == []


def test_skips_when_target_missing() -> None:
    payload = AzureResourcesPayload(
        account=CognitiveAccountSnapshot(name="ai-account"),
        diagnostics={"status": "ok", "account": "ai-account"},
    )
    assert check.run_rbac_openai_data_plane_check(payload) == []


def test_skips_when_principal_cannot_be_resolved() -> None:
    with patch.object(
        check,
        "run_rbac_openai_data_plane_check",
        wraps=check.run_rbac_openai_data_plane_check,
    ):
        with patch(
            "agentops.agent.checks._rbac_authorization."
            "resolve_signed_in_principal_object_id",
            side_effect=AuthorizationCheckError("no oid"),
        ):
            assert check.run_rbac_openai_data_plane_check(_payload()) == []


def test_skips_when_role_listing_fails() -> None:
    with patch(
        "agentops.agent.checks._rbac_authorization."
        "resolve_signed_in_principal_object_id",
        return_value=_PRINCIPAL_OID,
    ), patch(
        "agentops.agent.checks._rbac_authorization."
        "list_principal_role_definition_ids",
        side_effect=AuthorizationCheckError("boom"),
    ):
        assert check.run_rbac_openai_data_plane_check(_payload()) == []


# ---------------------------------------------------------------------------
# run_rbac_openai_data_plane_check  -- happy + finding paths
# ---------------------------------------------------------------------------


def test_no_finding_when_principal_has_openai_user_role() -> None:
    with patch(
        "agentops.agent.checks._rbac_authorization."
        "resolve_signed_in_principal_object_id",
        return_value=_PRINCIPAL_OID,
    ), patch(
        "agentops.agent.checks._rbac_authorization."
        "list_principal_role_definition_ids",
        return_value=[check.COGNITIVE_SERVICES_OPENAI_USER_ROLE_ID],
    ):
        assert check.run_rbac_openai_data_plane_check(_payload()) == []


def test_no_finding_when_principal_has_openai_contributor_role() -> None:
    with patch(
        "agentops.agent.checks._rbac_authorization."
        "resolve_signed_in_principal_object_id",
        return_value=_PRINCIPAL_OID,
    ), patch(
        "agentops.agent.checks._rbac_authorization."
        "list_principal_role_definition_ids",
        # Cognitive Services OpenAI Contributor (a001fd3d-...) also grants
        # the data action.
        return_value=["a001fd3d-188f-4b5d-821b-7da978bf7442"],
    ):
        assert check.run_rbac_openai_data_plane_check(_payload()) == []


def test_emits_finding_when_role_missing() -> None:
    with patch(
        "agentops.agent.checks._rbac_authorization."
        "resolve_signed_in_principal_object_id",
        return_value=_PRINCIPAL_OID,
    ), patch(
        "agentops.agent.checks._rbac_authorization."
        "list_principal_role_definition_ids",
        # Reader only -- not enough.
        return_value=["acdd72a7-3385-48ef-bd42-f606fba81ae7"],
    ):
        findings = check.run_rbac_openai_data_plane_check(_payload())

    assert len(findings) == 1
    finding = findings[0]
    assert finding.id == "security.missing_openai_data_plane_rbac"
    assert finding.severity is Severity.WARNING
    assert finding.category is Category.SECURITY
    assert finding.source == "azure_resources"
    # Title flags the affected account by name.
    assert "ai-account" in finding.title
    # Recommendation contains the actionable az command with the right scope,
    # role, and principal oid.
    assert "az role assignment create" in finding.recommendation
    assert _PRINCIPAL_OID in finding.recommendation
    assert "Cognitive Services OpenAI User" in finding.recommendation
    assert _ACCOUNT_TARGET in finding.recommendation
    # Evidence captures everything the Doctor report needs to render the row.
    ev = finding.evidence
    assert ev["account"] == "ai-account"
    assert ev["resource_group"] == "rg-x"
    assert ev["subscription_id"] == _SUBSCRIPTION_ID
    assert ev["scope"] == _ACCOUNT_TARGET
    assert ev["principal_object_id"] == _PRINCIPAL_OID
    assert ev["required_role"] == "Cognitive Services OpenAI User"
    assert ev["required_role_id"] == check.COGNITIVE_SERVICES_OPENAI_USER_ROLE_ID
    assert ev["granted_role_definition_ids"] == [
        "acdd72a7-3385-48ef-bd42-f606fba81ae7"
    ]
    assert "az role assignment create" in ev["remediation_command"]


def test_subscription_id_extracted_from_target() -> None:
    assert check._subscription_id_from_target(_ACCOUNT_TARGET) == _SUBSCRIPTION_ID
    assert check._subscription_id_from_target(None) is None
    assert check._subscription_id_from_target("not-an-arm-id") is None


# ---------------------------------------------------------------------------
# Helper module: resolve_signed_in_principal_object_id
# ---------------------------------------------------------------------------


def test_resolve_principal_returns_oid_from_token() -> None:
    from agentops.agent.checks import _rbac_authorization as helper

    token = MagicMock()
    token.token = _fake_jwt({"oid": _PRINCIPAL_OID})
    cred = MagicMock()
    cred.get_token.return_value = token

    with patch(
        "agentops.agent.sources._credentials.get_shared_credential",
        return_value=cred,
    ):
        assert helper.resolve_signed_in_principal_object_id() == _PRINCIPAL_OID
    cred.get_token.assert_called_once_with(
        "https://management.azure.com/.default"
    )


def test_resolve_principal_raises_when_token_lacks_oid() -> None:
    from agentops.agent.checks import _rbac_authorization as helper

    token = MagicMock()
    token.token = _fake_jwt({"aud": "x"})  # no oid claim
    cred = MagicMock()
    cred.get_token.return_value = token

    with patch(
        "agentops.agent.sources._credentials.get_shared_credential",
        return_value=cred,
    ):
        with pytest.raises(AuthorizationCheckError, match="oid"):
            helper.resolve_signed_in_principal_object_id()


def test_resolve_principal_raises_on_credential_failure() -> None:
    from agentops.agent.checks import _rbac_authorization as helper

    cred = MagicMock()
    cred.get_token.side_effect = RuntimeError("network down")

    with patch(
        "agentops.agent.sources._credentials.get_shared_credential",
        return_value=cred,
    ):
        with pytest.raises(AuthorizationCheckError):
            helper.resolve_signed_in_principal_object_id()


# ---------------------------------------------------------------------------
# Helper module: list_principal_role_definition_ids
# ---------------------------------------------------------------------------


def test_list_role_definition_ids_extracts_guid_suffix() -> None:
    from agentops.agent.checks import _rbac_authorization as helper

    a1 = MagicMock(role_definition_id=(
        f"/subscriptions/{_SUBSCRIPTION_ID}/providers/Microsoft.Authorization/"
        "roleDefinitions/" + check.COGNITIVE_SERVICES_OPENAI_USER_ROLE_ID
    ))
    a2 = MagicMock(role_definition_id=(
        f"/subscriptions/{_SUBSCRIPTION_ID}/providers/Microsoft.Authorization/"
        "roleDefinitions/acdd72a7-3385-48ef-bd42-f606fba81ae7"
    ))
    role_assignments = MagicMock()
    role_assignments.list_for_scope.return_value = [a1, a2]
    client = MagicMock()
    client.role_assignments = role_assignments

    with patch(
        "agentops.agent.sources._credentials.get_shared_credential",
        return_value=MagicMock(),
    ), patch(
        "azure.mgmt.authorization.AuthorizationManagementClient",
        return_value=client,
    ):
        ids: List[str] = helper.list_principal_role_definition_ids(
            subscription_id=_SUBSCRIPTION_ID,
            scope=_ACCOUNT_TARGET,
            principal_object_id=_PRINCIPAL_OID,
        )

    assert ids == [
        check.COGNITIVE_SERVICES_OPENAI_USER_ROLE_ID,
        "acdd72a7-3385-48ef-bd42-f606fba81ae7",
    ]
    role_assignments.list_for_scope.assert_called_once()
    kwargs = role_assignments.list_for_scope.call_args.kwargs
    assert kwargs["scope"] == _ACCOUNT_TARGET
    assert _PRINCIPAL_OID in kwargs["filter"]
    assert "atScopeAndAbove" in kwargs["filter"]


def test_list_role_definition_ids_raises_when_sdk_missing() -> None:
    """Simulate ``azure-mgmt-authorization`` not installed."""
    from agentops.agent.checks import _rbac_authorization as helper
    import sys

    saved = sys.modules.pop("azure.mgmt.authorization", None)
    sys.modules["azure.mgmt.authorization"] = None  # type: ignore[assignment]
    try:
        with pytest.raises(AuthorizationCheckError, match="azure-mgmt-authorization"):
            helper.list_principal_role_definition_ids(
                subscription_id=_SUBSCRIPTION_ID,
                scope=_ACCOUNT_TARGET,
                principal_object_id=_PRINCIPAL_OID,
            )
    finally:
        if saved is not None:
            sys.modules["azure.mgmt.authorization"] = saved
        else:
            sys.modules.pop("azure.mgmt.authorization", None)
