"""Easy Auth header parsing and validation for the hosted Cockpit.

Azure App Service Authentication ("Easy Auth") validates the signed-in
user's token at the platform edge and forwards the already-validated
identity as request headers; the application never sees or validates a raw
JWT itself. This module parses those headers into a safe, token-free
:class:`EasyAuthPrincipal`, checks it against the expected tenant, audience
(application client ID, accepted as either the bare client ID or the
``api://<client-id>`` App ID URI form configured by this app's Bicep
deployment), and an optional allowed-group restriction (matched
case-insensitively, since group-object-ID casing is not guaranteed to be
byte-identical between configuration and claims), and builds the
``auth_context_resolver`` callable that ``agentops.agent.cockpit.create_app``
expects.

Every failure raised here is an :class:`EasyAuthError`, but two more
specific bases let a caller distinguish *why* the request failed:
:class:`EasyAuthenticationError` (no usable identity -- missing/malformed
header or claims) versus :class:`EasyAuthorizationError` (a known identity
that is not permitted -- tenant/audience/group mismatch or group-claims
overage). Both remain :class:`PermissionError` subclasses, so
``cockpit.py``'s current blanket ``except PermissionError`` -> HTTP 401
handler keeps working unchanged; a future ``cockpit.py`` update can branch
on ``isinstance(exc, EasyAuthorizationError)`` (or the ``http_status`` class
attribute) to map authorization failures to HTTP 403 instead.

No network calls and no Azure SDK imports are required here -- Easy Auth
headers are plain strings already validated upstream -- so this module has
no lazy-import concerns, unlike :mod:`agentops.agent.observe.auth`.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

#: Header names forwarded by Azure App Service Easy Auth (see
#: ``agentops.agent.cockpit._authorize`` for the exact header aliases this
#: module must stay compatible with).
_HEADER_PRINCIPAL = "x-ms-client-principal"
_HEADER_PRINCIPAL_ID = "x-ms-client-principal-id"
_HEADER_PRINCIPAL_NAME = "x-ms-client-principal-name"
_HEADER_ACCESS_TOKEN = "x-ms-token-aad-access-token"

#: Canonical hosted-environment variable names (FR-070/FR-072).
ENV_TENANT_ID = "AGENTOPS_TENANT_ID"
ENV_APPLICATION_CLIENT_ID = "AGENTOPS_APPLICATION_CLIENT_ID"
ENV_UAMI_CLIENT_ID = "AGENTOPS_UAMI_CLIENT_ID"
ENV_ALLOWED_GROUP_OBJECT_ID = "AGENTOPS_ALLOWED_GROUP_OBJECT_ID"

#: Key the resolver's output context mapping uses for the raw user access
#: token. This is intentionally distinct from the inbound Easy Auth header
#: name so ``ObserveFacade.trace_content`` (the only consumer) does not need
#: to know the Easy Auth header convention. The Cockpit's own
#: ``/api/auth/context`` diagnostic route redacts any context key whose name
#: contains "token" or "assertion", so this key is safely hidden there while
#: remaining available to server-side facade calls.
ACCESS_TOKEN_CONTEXT_KEY = "access_token"

_TENANT_CLAIM_TYPES = {
    "tid",
    "http://schemas.microsoft.com/identity/claims/tenantid",
}
_AUDIENCE_CLAIM_TYPES = {"aud"}
_USER_ID_CLAIM_TYPES = {
    "oid",
    "http://schemas.microsoft.com/identity/claims/objectidentifier",
    "sub",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier",
}
_USER_NAME_CLAIM_TYPES = {
    "name",
    "preferred_username",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
}
_GROUP_CLAIM_TYPES = {
    "groups",
    "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups",
    "roles",
}
_HAS_GROUPS_OVERAGE_TYPES = {"hasgroups"}


class EasyAuthError(PermissionError):
    """Base for Easy Auth validation failures.

    Subclasses :class:`PermissionError` so ``cockpit.py``'s current bare
    ``except PermissionError`` handler keeps mapping every failure here to
    HTTP 401 with no ``cockpit.py`` changes required.

    Two more specific bases -- :class:`EasyAuthenticationError` (the request
    carries no usable identity: missing/malformed header or claims) and
    :class:`EasyAuthorizationError` (the request carries a valid identity
    that is not permitted: tenant/audience/group mismatch or group-claims
    overage) -- let a caller distinguish "who are you" (401) from "you can't
    do that" (403) failures via ``isinstance`` or the :attr:`http_status`
    class attribute, without weakening the existing blanket-401 fallback.
    """

    #: Default status code for callers that want to map failures to HTTP
    #: without enumerating every subclass; overridden per authentication vs.
    #: authorization bases below.
    http_status: int = 401


class EasyAuthenticationError(EasyAuthError):
    """The request carries no usable, verifiable identity (maps to HTTP 401).

    Raised for a missing principal header or a header that cannot be
    decoded, or that decodes but omits claims required to establish *who*
    the caller is (tenant, audience, user identifier).
    """

    http_status = 401


class EasyAuthorizationError(EasyAuthError):
    """The request's identity is known but not permitted (maps to HTTP 403).

    Raised once tenant/audience/user claims have been read successfully but
    the caller is not allowed to proceed: wrong tenant, wrong audience,
    missing allowed-group membership, or group-claims overage that makes an
    allowed-group restriction impossible to evaluate.
    """

    http_status = 403


class MissingPrincipalHeaderError(EasyAuthenticationError):
    """Raised when no Easy Auth principal header is present."""


class MalformedPrincipalHeaderError(EasyAuthenticationError):
    """Raised when the principal header cannot be decoded or is incomplete."""


class TenantMismatchError(EasyAuthorizationError):
    """Raised when the principal's tenant does not match the configured tenant."""


class AudienceMismatchError(EasyAuthorizationError):
    """Raised when the token audience does not match the configured client ID."""


class GroupNotAllowedError(EasyAuthorizationError):
    """Raised when an allowed-group restriction is configured but not satisfied."""


class GroupClaimsOverageError(EasyAuthorizationError):
    """Raised when Azure AD omitted the groups claim due to group overage.

    Azure AD replaces the ``groups`` claim with a ``hasgroups`` marker once a
    user belongs to more groups than fit in a token; Easy Auth forwards that
    same limitation. An allowed-group restriction can never be evaluated in
    that case, so this is reported distinctly (and safely/actionably) rather
    than silently treated as "not a member".
    """


@dataclass(frozen=True)
class EasyAuthConfig:
    """Expected tenant / audience / optional-group configuration."""

    tenant_id: str
    application_client_id: str
    allowed_group_object_id: Optional[str] = None

    @classmethod
    def from_env(cls, *, env: Optional[Mapping[str, str]] = None) -> "EasyAuthConfig":
        """Build a config from the canonical hosted environment variables."""
        source: Mapping[str, str] = env if env is not None else os.environ
        tenant_id = (source.get(ENV_TENANT_ID) or "").strip()
        client_id = (source.get(ENV_APPLICATION_CLIENT_ID) or "").strip()
        if not tenant_id:
            raise ValueError(f"{ENV_TENANT_ID} must be set to validate Easy Auth headers")
        if not client_id:
            raise ValueError(
                f"{ENV_APPLICATION_CLIENT_ID} must be set to validate Easy Auth headers"
            )
        allowed_group = (source.get(ENV_ALLOWED_GROUP_OBJECT_ID) or "").strip()
        return cls(
            tenant_id=tenant_id,
            application_client_id=client_id,
            allowed_group_object_id=allowed_group or None,
        )


@dataclass(frozen=True)
class EasyAuthPrincipal:
    """A parsed, validated Easy Auth principal. Never carries a raw token."""

    tenant_id: str
    user_id: str
    user_name: Optional[str]
    groups: tuple[str, ...]
    group_claims_overage: bool = False

    def safe_context(self) -> dict[str, Any]:
        """Return the internal token-free auth context.

        The result still contains protected identity and group identifiers. It
        is for authorization and attribution only, not logging or responses.
        """
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "groups": list(self.groups),
            "group_claims_overage": self.group_claims_overage,
        }


class _RedactedAuthContext(dict[str, Any]):
    """Auth context with protected values redacted from text representations."""

    def __repr__(self) -> str:
        protected_keys = {"user_id", "user_name", "groups"}
        redacted = {
            key: (
                "<redacted>"
                if (
                    key.lower() in protected_keys
                    or "token" in key.lower()
                    or "assertion" in key.lower()
                )
                else value
            )
            for key, value in self.items()
        }
        return repr(redacted)

    def __str__(self) -> str:
        return self.__repr__()


def validated_groups_for_identity(
    principal: EasyAuthPrincipal, telemetry_identity: Optional[str]
) -> tuple[str, ...]:
    """Return group claims only for an exact current-principal identity match.

    Both the stable object ID and the validated display/user name are eligible
    aliases because either can be the supported identity emitted by telemetry.
    Matching is deliberately byte-for-byte: group claims must never classify a
    different telemetry user through normalization or fuzzy matching.
    """
    if not isinstance(telemetry_identity, str) or not telemetry_identity:
        return ()
    if telemetry_identity not in {principal.user_id, principal.user_name}:
        return ()
    if principal.group_claims_overage:
        return ()
    return principal.groups


def _decode_principal_header(raw: str) -> dict[str, Any]:
    try:
        decoded = base64.b64decode(raw, validate=False)
        payload = json.loads(decoded.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise MalformedPrincipalHeaderError(
            "the Easy Auth principal header could not be decoded as base64 JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise MalformedPrincipalHeaderError(
            "the Easy Auth principal header did not decode to a JSON object"
        )
    return payload


def _expected_audiences(application_client_id: str) -> frozenset[str]:
    """Return the exact audience forms Easy Auth may present for this app.

    Microsoft Entra tokens issued for this app may carry either the bare
    application (client) ID as ``aud``, or the ``api://<client-id>`` App ID
    URI form -- the Bicep deployment for this app explicitly configures the
    latter as the exposed API's application ID URI. Only these two *exact*
    forms are accepted; no other prefix, suffix, or path segment is treated
    as a match, so a token scoped to a different API exposed on the same
    app registration is still rejected.
    """
    normalized = application_client_id.strip().lower()
    return frozenset({normalized, f"api://{normalized}"})


def _claim_values(claims: Any, wanted_types: set[str]) -> list[str]:
    if not isinstance(claims, list):
        return []
    wanted = {name.lower() for name in wanted_types}
    values: list[str] = []
    for claim in claims:
        if not isinstance(claim, Mapping):
            continue
        claim_type = str(claim.get("typ", "")).lower()
        if claim_type in wanted:
            value = claim.get("val")
            if isinstance(value, str) and value:
                values.append(value)
    return values


def parse_easy_auth_principal(
    headers: Mapping[str, Any], *, config: EasyAuthConfig
) -> EasyAuthPrincipal:
    """Parse and validate one request's Easy Auth headers.

    Raises :class:`EasyAuthenticationError` (missing header, malformed
    payload, or a claim needed to establish identity is absent) or
    :class:`EasyAuthorizationError` (tenant/audience mismatch, disallowed
    group membership, or group-claims overage) -- both are
    :class:`EasyAuthError` subclasses so existing blanket-401 callers keep
    working unchanged. Callers never see a raw token through this function.
    """
    principal_header = headers.get(_HEADER_PRINCIPAL)
    if not principal_header or not str(principal_header).strip():
        raise MissingPrincipalHeaderError(
            "no Easy Auth principal header was present on this request; "
            "confirm App Service Authentication is enabled and this app is "
            "not being accessed without signing in"
        )

    payload = _decode_principal_header(str(principal_header))
    claims = payload.get("claims")

    tenant_values = _claim_values(claims, _TENANT_CLAIM_TYPES)
    if not tenant_values:
        raise MalformedPrincipalHeaderError(
            "the Easy Auth principal is missing a tenant (tid) claim"
        )
    tenant_id = tenant_values[0]
    if tenant_id.lower() != config.tenant_id.lower():
        raise TenantMismatchError(
            f"the signed-in tenant {tenant_id!r} does not match the tenant "
            f"configured in {ENV_TENANT_ID}; sign in with an account from "
            "the expected Microsoft Entra tenant"
        )

    audience_values = _claim_values(claims, _AUDIENCE_CLAIM_TYPES)
    if not audience_values:
        raise MalformedPrincipalHeaderError(
            "the Easy Auth principal is missing an audience (aud) claim"
        )
    audience = audience_values[0]
    if audience.strip().lower() not in _expected_audiences(config.application_client_id):
        raise AudienceMismatchError(
            f"the token audience {audience!r} does not match the client ID "
            f"configured in {ENV_APPLICATION_CLIENT_ID} (accepted audiences: "
            "the client ID itself, or 'api://<client-id>'); verify the app "
            "registration used for sign-in matches the hosted app"
        )

    user_id_values = _claim_values(claims, _USER_ID_CLAIM_TYPES)
    header_user_id = headers.get(_HEADER_PRINCIPAL_ID)
    user_id = user_id_values[0] if user_id_values else header_user_id
    if not user_id or not str(user_id).strip():
        raise MalformedPrincipalHeaderError(
            "the Easy Auth principal is missing a user identifier (oid) claim"
        )

    user_name_values = _claim_values(claims, _USER_NAME_CLAIM_TYPES)
    header_user_name = headers.get(_HEADER_PRINCIPAL_NAME)
    user_name = user_name_values[0] if user_name_values else header_user_name

    groups = tuple(dict.fromkeys(_claim_values(claims, _GROUP_CLAIM_TYPES)))
    group_claims_overage = any(
        value.strip().lower() == "true"
        for value in _claim_values(claims, _HAS_GROUPS_OVERAGE_TYPES)
    )

    if config.allowed_group_object_id:
        if not groups:
            if group_claims_overage:
                raise GroupClaimsOverageError(
                    "Microsoft Entra omitted the groups claim because the "
                    "signed-in user belongs to too many groups (overage); "
                    f"configure an app role instead of {ENV_ALLOWED_GROUP_OBJECT_ID}, "
                    "or reduce the user's group memberships"
                )
        # Group object IDs are GUIDs; Microsoft Entra/Easy Auth casing is not
        # guaranteed to be byte-identical between the configured value and
        # the claim, so compare case-insensitively rather than requiring an
        # exact-case match.
        normalized_allowed_group = config.allowed_group_object_id.strip().lower()
        normalized_groups = {group.strip().lower() for group in groups}
        if normalized_allowed_group not in normalized_groups:
            raise GroupNotAllowedError(
                "the signed-in user is not a member of the group configured "
                f"in {ENV_ALLOWED_GROUP_OBJECT_ID}; ask an administrator to "
                "add them to the allowed group, or remove the restriction"
            )

    return EasyAuthPrincipal(
        tenant_id=tenant_id,
        user_id=str(user_id),
        user_name=str(user_name) if user_name else None,
        groups=groups,
        group_claims_overage=group_claims_overage,
    )


def build_easy_auth_resolver(
    config: Optional[EasyAuthConfig] = None,
) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """Build a ``create_app``-compatible ``auth_context_resolver`` callable.

    The returned callable raises an :class:`EasyAuthError` (mapped to HTTP
    401 by ``cockpit.py``'s ``_authorize`` dependency) for any invalid,
    missing, or disallowed principal, and otherwise returns a plain ``dict``
    with the safe principal fields plus the raw user access token (needed
    only for delegated ``trace_content`` reads) under
    :data:`ACCESS_TOKEN_CONTEXT_KEY`. Pass the returned callable as
    ``create_app(auth_context_resolver=...)``.
    """
    resolved_config = config or EasyAuthConfig.from_env()

    def resolver(headers: Mapping[str, Any]) -> dict[str, Any]:
        principal = parse_easy_auth_principal(headers, config=resolved_config)
        context = _RedactedAuthContext(principal.safe_context())
        access_token = headers.get(_HEADER_ACCESS_TOKEN)
        if access_token:
            context[ACCESS_TOKEN_CONTEXT_KEY] = access_token
        return context

    return resolver
