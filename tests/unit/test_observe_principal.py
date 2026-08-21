"""Tests for Easy Auth header parsing/validation (production composition)."""

from __future__ import annotations

import base64
import json

import pytest

from agentops.agent.observe.principal import (
    ACCESS_TOKEN_CONTEXT_KEY,
    AudienceMismatchError,
    EasyAuthConfig,
    EasyAuthenticationError,
    EasyAuthError,
    EasyAuthorizationError,
    GroupClaimsOverageError,
    GroupNotAllowedError,
    MalformedPrincipalHeaderError,
    MissingPrincipalHeaderError,
    TenantMismatchError,
    build_easy_auth_resolver,
    parse_easy_auth_principal,
)

_TENANT_ID = "11111111-1111-1111-1111-111111111111"
_CLIENT_ID = "22222222-2222-2222-2222-222222222222"
_USER_OID = "33333333-3333-3333-3333-333333333333"
_GROUP_ID = "44444444-4444-4444-4444-444444444444"


def _encode_principal(claims: list[dict[str, str]]) -> str:
    payload = {"auth_typ": "aad", "claims": claims, "name_typ": "name", "role_typ": "roles"}
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def _base_claims(*, groups: list[str] | None = None) -> list[dict[str, str]]:
    claims = [
        {"typ": "tid", "val": _TENANT_ID},
        {"typ": "aud", "val": _CLIENT_ID},
        {"typ": "oid", "val": _USER_OID},
        {"typ": "name", "val": "Ada Lovelace"},
    ]
    for group in groups or []:
        claims.append({"typ": "groups", "val": group})
    return claims


def _config(*, allowed_group: str | None = None) -> EasyAuthConfig:
    return EasyAuthConfig(
        tenant_id=_TENANT_ID, application_client_id=_CLIENT_ID, allowed_group_object_id=allowed_group
    )


def test_from_env_reads_canonical_hosted_env_var_names() -> None:
    env = {
        "AGENTOPS_TENANT_ID": _TENANT_ID,
        "AGENTOPS_APPLICATION_CLIENT_ID": _CLIENT_ID,
        "AGENTOPS_UAMI_CLIENT_ID": "uami-1",
        "AGENTOPS_ALLOWED_GROUP_OBJECT_ID": _GROUP_ID,
    }
    config = EasyAuthConfig.from_env(env=env)
    assert config.tenant_id == _TENANT_ID
    assert config.application_client_id == _CLIENT_ID
    assert config.allowed_group_object_id == _GROUP_ID


def test_from_env_requires_tenant_and_client_id() -> None:
    with pytest.raises(ValueError):
        EasyAuthConfig.from_env(env={})
    with pytest.raises(ValueError):
        EasyAuthConfig.from_env(env={"AGENTOPS_TENANT_ID": _TENANT_ID})


def test_parse_valid_principal_returns_safe_context() -> None:
    headers = {"x-ms-client-principal": _encode_principal(_base_claims())}
    principal = parse_easy_auth_principal(headers, config=_config())

    assert principal.tenant_id == _TENANT_ID
    assert principal.user_id == _USER_OID
    assert principal.user_name == "Ada Lovelace"
    assert principal.groups == ()
    assert principal.safe_context() == {
        "tenant_id": _TENANT_ID,
        "user_id": _USER_OID,
        "user_name": "Ada Lovelace",
        "groups": [],
    }


def test_missing_principal_header_raises_permission_error() -> None:
    with pytest.raises(MissingPrincipalHeaderError):
        parse_easy_auth_principal({}, config=_config())
    # Every Easy Auth failure must map to HTTP 401 via cockpit.py's bare
    # ``except PermissionError`` handler, with no cockpit.py changes needed.
    assert issubclass(MissingPrincipalHeaderError, EasyAuthError)
    assert issubclass(EasyAuthError, PermissionError)


@pytest.mark.parametrize(
    "raw",
    ["not-base64!!!", base64.b64encode(b"not json").decode("ascii"), base64.b64encode(b"[]").decode("ascii")],
)
def test_malformed_principal_header_raises(raw: str) -> None:
    with pytest.raises(MalformedPrincipalHeaderError):
        parse_easy_auth_principal({"x-ms-client-principal": raw}, config=_config())


def test_principal_missing_tenant_claim_is_malformed() -> None:
    claims = [c for c in _base_claims() if c["typ"] != "tid"]
    headers = {"x-ms-client-principal": _encode_principal(claims)}
    with pytest.raises(MalformedPrincipalHeaderError):
        parse_easy_auth_principal(headers, config=_config())


def test_principal_missing_audience_claim_is_malformed() -> None:
    claims = [c for c in _base_claims() if c["typ"] != "aud"]
    headers = {"x-ms-client-principal": _encode_principal(claims)}
    with pytest.raises(MalformedPrincipalHeaderError):
        parse_easy_auth_principal(headers, config=_config())


def test_principal_missing_user_id_falls_back_to_header() -> None:
    claims = [c for c in _base_claims() if c["typ"] != "oid"]
    headers = {
        "x-ms-client-principal": _encode_principal(claims),
        "x-ms-client-principal-id": _USER_OID,
    }
    principal = parse_easy_auth_principal(headers, config=_config())
    assert principal.user_id == _USER_OID


def test_tenant_mismatch_raises_actionable_error() -> None:
    other_tenant = "99999999-9999-9999-9999-999999999999"
    claims = [c for c in _base_claims() if c["typ"] != "tid"]
    claims.append({"typ": "tid", "val": other_tenant})
    headers = {"x-ms-client-principal": _encode_principal(claims)}
    with pytest.raises(TenantMismatchError, match="AGENTOPS_TENANT_ID"):
        parse_easy_auth_principal(headers, config=_config())


def test_audience_mismatch_raises_actionable_error() -> None:
    claims = [c for c in _base_claims() if c["typ"] != "aud"]
    claims.append({"typ": "aud", "val": "other-client"})
    headers = {"x-ms-client-principal": _encode_principal(claims)}
    with pytest.raises(AudienceMismatchError, match="AGENTOPS_APPLICATION_CLIENT_ID"):
        parse_easy_auth_principal(headers, config=_config())


def test_audience_accepted_as_api_uri_form() -> None:
    """Bicep configures the app's exposed API audience as ``api://<client-id>``."""
    claims = [c for c in _base_claims() if c["typ"] != "aud"]
    claims.append({"typ": "aud", "val": f"api://{_CLIENT_ID}"})
    headers = {"x-ms-client-principal": _encode_principal(claims)}
    principal = parse_easy_auth_principal(headers, config=_config())
    assert principal.tenant_id == _TENANT_ID


def test_audience_accepted_as_api_uri_form_case_insensitive() -> None:
    claims = [c for c in _base_claims() if c["typ"] != "aud"]
    claims.append({"typ": "aud", "val": f"API://{_CLIENT_ID.upper()}"})
    headers = {"x-ms-client-principal": _encode_principal(claims)}
    principal = parse_easy_auth_principal(headers, config=_config())
    assert principal.tenant_id == _TENANT_ID


@pytest.mark.parametrize(
    "audience",
    [
        f"api://{_CLIENT_ID}/extra",
        f"spn:{_CLIENT_ID}",
        f"api:{_CLIENT_ID}",
        "api://other-client",
    ],
)
def test_audience_rejects_non_exact_variants(audience: str) -> None:
    claims = [c for c in _base_claims() if c["typ"] != "aud"]
    claims.append({"typ": "aud", "val": audience})
    headers = {"x-ms-client-principal": _encode_principal(claims)}
    with pytest.raises(AudienceMismatchError, match="AGENTOPS_APPLICATION_CLIENT_ID"):
        parse_easy_auth_principal(headers, config=_config())


def test_group_membership_required_and_satisfied() -> None:
    headers = {
        "x-ms-client-principal": _encode_principal(_base_claims(groups=[_GROUP_ID, "other-group"]))
    }
    principal = parse_easy_auth_principal(headers, config=_config(allowed_group=_GROUP_ID))
    assert _GROUP_ID in principal.groups


def test_group_membership_matches_case_insensitively() -> None:
    headers = {
        "x-ms-client-principal": _encode_principal(_base_claims(groups=[_GROUP_ID.upper()]))
    }
    principal = parse_easy_auth_principal(headers, config=_config(allowed_group=_GROUP_ID.lower()))
    assert _GROUP_ID.upper() in principal.groups


def test_group_not_allowed_raises_actionable_error() -> None:
    headers = {"x-ms-client-principal": _encode_principal(_base_claims(groups=["other-group"]))}
    with pytest.raises(GroupNotAllowedError, match="AGENTOPS_ALLOWED_GROUP_OBJECT_ID"):
        parse_easy_auth_principal(headers, config=_config(allowed_group=_GROUP_ID))


def test_group_overage_raises_distinct_actionable_error() -> None:
    claims = _base_claims()
    claims.append({"typ": "hasgroups", "val": "true"})
    headers = {"x-ms-client-principal": _encode_principal(claims)}
    with pytest.raises(GroupClaimsOverageError, match="overage"):
        parse_easy_auth_principal(headers, config=_config(allowed_group=_GROUP_ID))


@pytest.mark.parametrize(
    ("error_cls", "expected_base", "expected_status"),
    [
        (MissingPrincipalHeaderError, EasyAuthenticationError, 401),
        (MalformedPrincipalHeaderError, EasyAuthenticationError, 401),
        (TenantMismatchError, EasyAuthorizationError, 403),
        (AudienceMismatchError, EasyAuthorizationError, 403),
        (GroupNotAllowedError, EasyAuthorizationError, 403),
        (GroupClaimsOverageError, EasyAuthorizationError, 403),
    ],
)
def test_error_hierarchy_distinguishes_authentication_from_authorization(
    error_cls: type[EasyAuthError], expected_base: type[EasyAuthError], expected_status: int
) -> None:
    assert issubclass(error_cls, expected_base)
    assert issubclass(error_cls, EasyAuthError)
    assert issubclass(error_cls, PermissionError)
    assert error_cls.http_status == expected_status


def test_authentication_and_authorization_bases_are_distinct_and_both_permission_errors() -> None:
    assert issubclass(EasyAuthenticationError, EasyAuthError)
    assert issubclass(EasyAuthorizationError, EasyAuthError)
    assert not issubclass(EasyAuthenticationError, EasyAuthorizationError)
    assert not issubclass(EasyAuthorizationError, EasyAuthenticationError)
    assert issubclass(EasyAuthError, PermissionError)
    assert EasyAuthenticationError.http_status == 401
    assert EasyAuthorizationError.http_status == 403



    headers = {
        "x-ms-client-principal": _encode_principal(_base_claims()),
        "x-ms-token-aad-access-token": "raw-jwt-value",
    }
    resolver = build_easy_auth_resolver(_config())
    context = resolver(headers)

    assert isinstance(context, dict)
    assert context[ACCESS_TOKEN_CONTEXT_KEY] == "raw-jwt-value"
    assert context["tenant_id"] == _TENANT_ID
    # The diagnostic /api/auth/context route redacts any key containing
    # "token"/"assertion"; confirm the chosen key name would be caught by
    # that case-insensitive substring filter.
    assert "token" in ACCESS_TOKEN_CONTEXT_KEY.lower()


def test_resolver_without_access_token_header_omits_key() -> None:
    headers = {"x-ms-client-principal": _encode_principal(_base_claims())}
    resolver = build_easy_auth_resolver(_config())
    context = resolver(headers)
    assert ACCESS_TOKEN_CONTEXT_KEY not in context


def test_resolver_raises_permission_error_for_invalid_principal() -> None:
    resolver = build_easy_auth_resolver(_config())
    with pytest.raises(PermissionError):
        resolver({})


def test_resolver_defaults_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTOPS_TENANT_ID", _TENANT_ID)
    monkeypatch.setenv("AGENTOPS_APPLICATION_CLIENT_ID", _CLIENT_ID)
    monkeypatch.delenv("AGENTOPS_ALLOWED_GROUP_OBJECT_ID", raising=False)

    resolver = build_easy_auth_resolver()
    headers = {"x-ms-client-principal": _encode_principal(_base_claims())}
    context = resolver(headers)
    assert context["tenant_id"] == _TENANT_ID
