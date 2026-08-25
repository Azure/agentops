"""Focused tests for pure attribution contracts and deterministic helpers."""

from __future__ import annotations

import json
from copy import deepcopy
from uuid import UUID

import pytest
from pydantic import ValidationError

from agentops.core import attribution as attribution_module
from agentops.core.attribution import (
    AttributionConfiguration,
    AttributionConfigurationLoadResult,
    AttributionTokenValidationError,
    attribution_config_fingerprint,
    canonical_attribution_config_json,
    derive_pseudonymous_user_key,
    issue_department_filter_token,
    issue_user_filter_token,
    load_attribution_config,
    resolve_attribution,
    validate_department_filter_token,
    validate_user_filter_token,
)
from agentops.core.observe import ObserveScope


NAMESPACE = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
TENANT = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
GROUP_A = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
GROUP_B = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
PROJECT = (
    "/subscriptions/11111111-1111-1111-1111-111111111111/"
    "resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/foundry/"
    "projects/project-a"
)


def _key(identity: str, *, generation: int = 1, namespace: UUID = NAMESPACE) -> str:
    return derive_pseudonymous_user_key(
        deployment_namespace=namespace,
        generation=generation,
        tenant_id=TENANT,
        raw_identity=identity,
    )


def _payload() -> dict:
    return {
        "version": 1,
        "enabled": True,
        "deployment_namespace": str(NAMESPACE),
        "generation": 1,
        "departments": [
            {
                "id": "engineering",
                "label": "Engineering",
                "user_keys": [_key("alice@example.com")],
                "group_ids": [str(GROUP_A)],
            },
            {
                "id": "finance",
                "label": "Finance",
                "user_keys": [_key("bob@example.com")],
                "group_ids": [str(GROUP_B)],
            },
        ],
    }


def _scope(project: str = PROJECT) -> ObserveScope:
    return ObserveScope(mode="projects", project_resource_ids=[project])


def test_loader_supports_absent_disabled_valid_and_invalid_states() -> None:
    disabled_payload = _payload()
    disabled_payload.update(
        {"enabled": False, "deployment_namespace": None, "generation": None}
    )
    disabled_payload["departments"] = []

    absent = load_attribution_config(None)
    disabled = load_attribution_config(json.dumps(disabled_payload))
    valid = load_attribution_config(json.dumps(_payload()))
    invalid = load_attribution_config('{"enabled":true,"raw_identity":"do-not-echo"')

    assert absent == AttributionConfigurationLoadResult(state="absent")
    assert disabled.state == "disabled"
    assert disabled.config is not None
    assert disabled.fingerprint
    assert valid.state == "valid"
    assert valid.config is not None
    assert valid.fingerprint == attribution_config_fingerprint(valid.config)
    assert "Engineering" not in repr(valid)
    assert str(GROUP_A) not in repr(valid)
    assert _key("alice@example.com") not in repr(valid)
    assert invalid.state == "invalid"
    assert invalid.config is None
    assert "do-not-echo" not in (invalid.message or "")


def test_loader_enforces_64_kib_utf8_limit_before_parsing() -> None:
    result = load_attribution_config("{" + ("é" * 32_768) + "}")
    assert result.state == "invalid"
    assert result.error_code == "attribution_config_too_large"
    assert "64 KiB" in (result.message or "")


@pytest.mark.parametrize("version", [0, 2, "1", True])
def test_configuration_requires_strict_version_one(version: object) -> None:
    payload = _payload()
    payload["version"] = version
    with pytest.raises(ValidationError):
        AttributionConfiguration.model_validate(payload)


def test_configuration_requires_enablement_values_and_mapping_entries() -> None:
    payload = _payload()
    payload["deployment_namespace"] = None
    with pytest.raises(ValidationError, match="requires"):
        AttributionConfiguration.model_validate(payload)

    payload = _payload()
    payload["departments"][0]["user_keys"] = []
    payload["departments"][0]["group_ids"] = []
    with pytest.raises(ValidationError, match="at least one"):
        AttributionConfiguration.model_validate(payload)


@pytest.mark.parametrize("duplicate_field", ["id", "user_keys", "group_ids"])
def test_configuration_enforces_global_uniqueness(duplicate_field: str) -> None:
    payload = _payload()
    if duplicate_field == "id":
        payload["departments"][1]["id"] = payload["departments"][0]["id"]
    elif duplicate_field == "user_keys":
        payload["departments"][1]["user_keys"] = payload["departments"][0]["user_keys"]
    else:
        payload["departments"][1]["group_ids"] = payload["departments"][0]["group_ids"]
    with pytest.raises(ValidationError, match="globally unique"):
        AttributionConfiguration.model_validate(payload)


def test_configuration_enforces_global_cardinality_and_current_generation() -> None:
    payload = _payload()
    payload["departments"] = [
        {
            "id": "large",
            "label": "Large",
            "user_keys": [_key(f"user-{index}") for index in range(501)],
            "group_ids": [],
        }
    ]
    with pytest.raises(ValidationError):
        AttributionConfiguration.model_validate(payload)

    payload = _payload()
    payload["departments"][0]["user_keys"] = [_key("alice", generation=2)]
    with pytest.raises(ValidationError, match="current generation"):
        AttributionConfiguration.model_validate(payload)


def test_pseudonyms_are_stable_separated_and_retain_full_digest() -> None:
    first = _key("  Alice@Example.com  ")
    assert first == _key("Alice@Example.com")
    assert len(first.rsplit(".", 1)[1]) == 64
    assert first != _key("alice@example.com")
    assert first != _key("Alice@Example.com", generation=2)
    assert first != _key(
        "Alice@Example.com",
        namespace=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
    )
    assert first != derive_pseudonymous_user_key(
        deployment_namespace=NAMESPACE,
        generation=1,
        tenant_id=UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
        raw_identity="Alice@Example.com",
    )


def test_canonical_fingerprint_ignores_semantic_order_only() -> None:
    payload = _payload()
    reordered = {
        "departments": list(reversed(deepcopy(payload["departments"]))),
        "generation": payload["generation"],
        "deployment_namespace": payload["deployment_namespace"],
        "enabled": payload["enabled"],
        "version": payload["version"],
    }
    for department in reordered["departments"]:
        department["user_keys"].reverse()
        department["group_ids"].reverse()
    first = AttributionConfiguration.model_validate(payload)
    second = AttributionConfiguration.model_validate(reordered)

    assert canonical_attribution_config_json(first) == canonical_attribution_config_json(
        second
    )
    assert attribution_config_fingerprint(first) == attribution_config_fingerprint(
        second
    )
    changed = deepcopy(payload)
    changed["departments"][0]["label"] = "Product Engineering"
    assert attribution_config_fingerprint(
        AttributionConfiguration.model_validate(changed)
    ) != attribution_config_fingerprint(first)


def test_mapping_resolution_honors_explicit_precedence_and_group_ambiguity() -> None:
    config = AttributionConfiguration.model_validate(_payload())
    explicit = resolve_attribution(
        _key("alice@example.com"),
        config,
        identity_matches_principal=True,
        principal_group_ids=[GROUP_B],
    )
    grouped = resolve_attribution(
        _key("unknown"),
        config,
        identity_matches_principal=True,
        principal_group_ids=[GROUP_A],
    )
    ambiguous = resolve_attribution(
        _key("unknown"),
        config,
        identity_matches_principal=True,
        principal_group_ids=[GROUP_A, GROUP_B],
    )
    unrelated = resolve_attribution(
        _key("unknown"),
        config,
        identity_matches_principal=False,
        principal_group_ids=[GROUP_A],
    )

    assert (explicit.source, explicit.department_id) == (
        "explicit_user",
        "engineering",
    )
    assert (grouped.source, grouped.department_id) == (
        "principal_group",
        "engineering",
    )
    assert ambiguous.source == "ambiguous"
    assert ambiguous.department_id is None
    assert unrelated.source == "unmapped"


def test_user_token_is_bound_to_generation_config_scope_and_principal() -> None:
    config = AttributionConfiguration.model_validate(_payload())
    user_key = _key("alice@example.com")
    token = issue_user_filter_token(
        user_key,
        config=config,
        scope=_scope(),
        tenant_id=TENANT,
        principal_id="operator-a",
    )
    assert validate_user_filter_token(
        token,
        config=config,
        scope=_scope(),
        tenant_id=TENANT,
        principal_id="operator-a",
    ) == user_key

    changed = deepcopy(_payload())
    changed["departments"][0]["label"] = "Changed"
    cases = [
        (
            {"config": config, "scope": _scope(), "principal_id": "operator-b"},
            "attribution_token_principal_changed",
        ),
        (
            {
                "config": config,
                "scope": _scope(PROJECT.replace("project-a", "project-b")),
                "principal_id": "operator-a",
            },
            "attribution_token_scope_changed",
        ),
        (
            {
                "config": AttributionConfiguration.model_validate(changed),
                "scope": _scope(),
                "principal_id": "operator-a",
            },
            "attribution_token_config_changed",
        ),
    ]
    for arguments, code in cases:
        with pytest.raises(AttributionTokenValidationError) as exc_info:
            validate_user_filter_token(
                token,
                tenant_id=TENANT,
                **arguments,
            )
        assert exc_info.value.code == code
        assert token not in str(exc_info.value)


def test_user_token_supports_unmapped_bootstrap_keys() -> None:
    payload = _payload()
    payload["departments"] = []
    config = AttributionConfiguration.model_validate(payload)
    user_key = _key("unmapped@example.com")
    token = issue_user_filter_token(
        user_key,
        config=config,
        scope=_scope(),
        tenant_id=TENANT,
        principal_id="operator-a",
    )
    assert validate_user_filter_token(
        token,
        config=config,
        scope=_scope(),
        tenant_id=TENANT,
        principal_id="operator-a",
    ) == user_key


def test_user_token_issuance_requires_a_complete_current_generation_key() -> None:
    config = AttributionConfiguration.model_validate(_payload())
    for invalid in (
        "usr1.g1.not-a-digest",
        f"usr1.g1.{'A' * 64}",
        _key("alice@example.com", generation=2),
    ):
        with pytest.raises(ValueError, match="current generation"):
            issue_user_filter_token(
                invalid,
                config=config,
                scope=_scope(),
                tenant_id=TENANT,
                principal_id="operator-a",
            )


def test_user_token_survives_semantic_reordering_but_not_rotation_or_tampering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AttributionConfiguration.model_validate(_payload())
    user_key = _key("alice@example.com")
    token = issue_user_filter_token(
        user_key,
        config=config,
        scope=_scope(),
        tenant_id=TENANT,
        principal_id="operator-a",
    )
    reordered = deepcopy(_payload())
    reordered["departments"].reverse()
    reordered_config = AttributionConfiguration.model_validate(reordered)
    assert validate_user_filter_token(
        token,
        config=reordered_config,
        scope=_scope(),
        tenant_id=TENANT,
        principal_id="operator-a",
    ) == user_key

    rotated = deepcopy(_payload())
    rotated["generation"] = 2
    rotated["deployment_namespace"] = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    for department in rotated["departments"]:
        department["user_keys"] = [
            _key(
                "alice@example.com" if department["id"] == "engineering" else "bob@example.com",
                generation=2,
                namespace=UUID(rotated["deployment_namespace"]),
            )
        ]
    with pytest.raises(AttributionTokenValidationError) as exc_info:
        validate_user_filter_token(
            token,
            config=AttributionConfiguration.model_validate(rotated),
            scope=_scope(),
            tenant_id=TENANT,
            principal_id="operator-a",
        )
    assert exc_info.value.code == "attribution_token_generation_changed"

    parts = token.split("~")
    parts[5] = _key("alice@example.com", generation=2)
    stale_key_token = "~".join(parts)
    with pytest.raises(AttributionTokenValidationError) as exc_info:
        validate_user_filter_token(
            stale_key_token,
            config=config,
            scope=_scope(),
            tenant_id=TENANT,
            principal_id="operator-a",
        )
    assert exc_info.value.code == "attribution_token_generation_changed"
    assert stale_key_token not in str(exc_info.value)

    calls: list[tuple[str, str]] = []
    real_compare = attribution_module.hmac.compare_digest

    def _compare(left: str, right: str) -> bool:
        calls.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(attribution_module.hmac, "compare_digest", _compare)
    assert validate_user_filter_token(
        token,
        config=config,
        scope=_scope(),
        tenant_id=TENANT,
        principal_id="operator-a",
    ) == user_key
    assert len(calls) >= 3
    assert calls[-1][0] == token.rsplit("~", 1)[1]
    assert len(calls[-1][1]) == 64


def test_user_token_fails_closed_for_a_different_tenant() -> None:
    config = AttributionConfiguration.model_validate(_payload())
    token = issue_user_filter_token(
        _key("alice@example.com"),
        config=config,
        scope=_scope(),
        tenant_id=TENANT,
        principal_id="operator-a",
    )
    with pytest.raises(AttributionTokenValidationError) as exc_info:
        validate_user_filter_token(
            token,
            config=config,
            scope=_scope(),
            tenant_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
            principal_id="operator-a",
        )
    assert exc_info.value.code == "attribution_token_principal_changed"
    assert token not in str(exc_info.value)


@pytest.mark.parametrize(
    "token",
    [
        "",
        "at1",
        "at1~u",
        "at1~x~g1~bad~bad~bad",
        "at1~u~g1~bad space~bad~bad~bad",
        "x" * 1025,
    ],
)
def test_malformed_attribution_tokens_fail_with_safe_syntax_error(token: str) -> None:
    config = AttributionConfiguration.model_validate(_payload())
    with pytest.raises(AttributionTokenValidationError) as exc_info:
        validate_user_filter_token(
            token,
            config=config,
            scope=_scope(),
            tenant_id=TENANT,
            principal_id="operator-a",
        )
    assert exc_info.value.code in {
        "attribution_token_invalid_syntax",
        "attribution_token_wrong_type",
    }
    if token:
        assert token not in str(exc_info.value)


def test_department_token_resolves_without_exposing_department_or_group() -> None:
    config = AttributionConfiguration.model_validate(_payload())
    token = issue_department_filter_token(
        "engineering", config=config, scope=_scope()
    )
    resolved = validate_department_filter_token(
        token, config=config, scope=_scope()
    )
    assert resolved.id == "engineering"
    assert "engineering" not in token
    assert str(GROUP_A) not in token
    with pytest.raises(AttributionTokenValidationError) as exc_info:
        validate_user_filter_token(
            token,
            config=config,
            scope=_scope(),
            tenant_id=TENANT,
            principal_id="operator-a",
        )
    assert exc_info.value.code == "attribution_token_wrong_type"


def test_department_token_requires_exactly_one_current_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AttributionConfiguration.model_validate(_payload())
    token = issue_department_filter_token(
        "engineering", config=config, scope=_scope()
    )

    monkeypatch.setattr(attribution_module, "_department_digest", lambda _: "0" * 64)
    with pytest.raises(AttributionTokenValidationError) as exc_info:
        validate_department_filter_token(token, config=config, scope=_scope())
    assert exc_info.value.code == "attribution_token_unresolved"
    assert exc_info.value.next_action == "Select a current configured department filter."

    digest = token.rsplit("~", 1)[1]
    monkeypatch.setattr(attribution_module, "_department_digest", lambda _: digest)
    with pytest.raises(AttributionTokenValidationError) as exc_info:
        validate_department_filter_token(token, config=config, scope=_scope())
    assert exc_info.value.code == "attribution_token_ambiguous"


def test_sensitive_contract_fields_are_hidden_from_representations_and_errors() -> None:
    config = AttributionConfiguration.model_validate(_payload())
    rendered = repr(config)
    assert "Engineering" not in rendered
    assert str(GROUP_A) not in rendered
    assert _key("alice@example.com") not in rendered

    payload = _payload()
    payload["client_secret"] = "do-not-echo"
    result = load_attribution_config(json.dumps(payload))
    assert result.error_code == "attribution_config_secret_field"
    assert "do-not-echo" not in repr(result)
    assert "do-not-echo" not in (result.message or "")
