"""Unit tests for hosted-Cockpit deployment preview construction.

Covers (issue #433, Phase 3, tasks T018-T020):

* Deterministic role-assignment identity (FR-056) — stable across repeated
  calls with identical inputs, distinct when principal/role/scope differ.
* ``authsettingsV2``-oriented, non-secret application settings and the
  allowlist enforcement backstop (FR-060).
* Prerequisite validation through the injectable ``PermissionChecker``
  adapter (FR-054/FR-055), including conditional group-read checks.
* End-to-end ``build_preview`` construction merged with a fake ``azd``
  preview adapter: planned resources/role-assignments/federated-credential/
  application-settings, BLOCKED warnings for out-of-allowlist changes and
  federated-credential conflicts, subscription-wide scope warnings, and
  preview-before-mutation (no adapter mutation calls happen during preview).
* ``validate_confirmation`` gating: blocked previews, missing confirmation,
  and guarded ``--yes`` (FR-067/FR-070).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentops.core.observe import DeploymentSelection, ObserveScope, RoleAssignmentPlan
from agentops.services.cockpit_deployment import (
    ALLOWED_SETTINGS_KEYS,
    AzCliAppRegistrationClient,
    AzCliManagedIdentityResolver,
    AzCliPermissionChecker,
    AzCliRoleAssignmentClient,
    AzCliTelemetryDiscovery,
    AzdCliCommandRunner,
    AzdCommandResult,
    AzdPreviewResult,
    CockpitDeploymentError,
    ConfirmationRequiredError,
    FederatedCredentialInfo,
    PermissionResult,
    PreviewBlockedError,
    PrerequisiteError,
    bicep_role_assignment_parameters,
    blocking_reasons,
    build_application_settings,
    build_preview,
    classify_role_assignment_scope,
    derive_role_assignment_id,
    explicit_role_assignments,
    validate_confirmation,
    validate_prerequisites,
)
import agentops.services.cockpit_deployment as cockpit_deployment


SUBSCRIPTION_ID = "11111111-1111-1111-1111-111111111111"
TENANT_ID = "22222222-2222-2222-2222-222222222222"
APP_OBJECT_ID = "33333333-3333-3333-3333-333333333333"
CLIENT_ID = "44444444-4444-4444-4444-444444444444"
SP_OBJECT_ID = "55555555-5555-5555-5555-555555555555"
GROUP_ID = "66666666-6666-6666-6666-666666666666"
RESOURCE_GROUP = "rg-agentops"
LOCATION = "eastus"
APP_NAME = "agentops-cockpit-test"

PROJECT_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourcegroups/rg-agentops/providers/"
    "microsoft.cognitiveservices/accounts/foundry1/projects/proj1"
)
PRINCIPAL_ID = "77777777-7777-7777-7777-777777777777"
OWN_RESOURCE_GROUP = cockpit_deployment._resource_group_scope(SUBSCRIPTION_ID, RESOURCE_GROUP)
OTHER_RESOURCE_GROUP = cockpit_deployment._resource_group_scope(SUBSCRIPTION_ID, "rg-other")


def _scope(**overrides) -> ObserveScope:
    payload = {
        "mode": "projects",
        "project_resource_ids": [PROJECT_ID],
        "default_project_resource_id": PROJECT_ID,
    }
    payload.update(overrides)
    return ObserveScope.model_validate(payload)


def _selection(*, allowed_group_id: str | None = None, scope: ObserveScope | None = None):
    return DeploymentSelection(
        workspace=Path("/workspace"),
        subscription_id=SUBSCRIPTION_ID,
        resource_group=RESOURCE_GROUP,
        location=LOCATION,
        app_name=APP_NAME,
        tenant_id=TENANT_ID,
        application_object_id=APP_OBJECT_ID,
        client_id=CLIENT_ID,
        service_principal_object_id=SP_OBJECT_ID,
        allowed_group_id=allowed_group_id,
        scope=scope or _scope(),
    )


def _role_assignment_plan(
    *, role: str, scope_resource_id: str, principal_id: str = PRINCIPAL_ID
) -> RoleAssignmentPlan:
    """Build a standalone :class:`RoleAssignmentPlan` fixture for scope-classification tests."""
    return RoleAssignmentPlan(
        assignment_id=derive_role_assignment_id(
            principal_id=principal_id, role=role, scope_resource_id=scope_resource_id
        ),
        principal_id=principal_id,
        role=role,
        role_definition_id=cockpit_deployment._role_definition_resource_id(
            SUBSCRIPTION_ID, role
        ),
        scope_resource_id=scope_resource_id,
        reason="test fixture",
    )


class FakeTelemetryDiscovery:
    def __init__(self, resource_ids: list[str] | None = None):
        self._resource_ids = resource_ids or []
        self.calls = 0

    def discover_telemetry_resources(self, scope):
        self.calls += 1
        return list(self._resource_ids)


class FakeManagedIdentityResolver:
    def __init__(self, *, principal_id: str | None = None, client_id: str | None = None):
        self._principal_id = principal_id
        self._client_id = client_id

    def resolve_principal_id(self, resource_id: str) -> str | None:
        return self._principal_id

    def resolve_client_id(self, resource_id: str) -> str | None:
        return self._client_id


def _fic_info(*, name: str, issuer: str, subject: str, audiences=("api://AzureADTokenExchange",)):
    return FederatedCredentialInfo(
        id=str(uuid.uuid4()),
        name=name,
        issuer=issuer,
        subject=subject,
        audiences=tuple(audiences),
    )


class FakeAppRegistrationClient:
    """Read-only Graph fake: only ``list_federated_credentials`` is used by
    ``build_preview``; ``create_federated_credential`` is asserted unused so
    tests catch any preview-time mutation regression."""

    def __init__(self, existing: list[FederatedCredentialInfo] | None = None):
        self._existing = existing or []
        self.list_calls = 0
        self.create_calls = 0

    def get_app_registration(self, *, tenant_id: str, client_id: str):  # pragma: no cover
        raise AssertionError("build_preview must not look up the app registration")

    def list_federated_credentials(self, application_object_id: str):
        self.list_calls += 1
        return list(self._existing)

    def create_federated_credential(self, application_object_id, *, name, issuer, subject, audiences):
        self.create_calls += 1
        raise AssertionError("build_preview must not create a federated credential")


class FakeAzdCommandRunner:
    def __init__(self, *, resources: list[dict] | None = None, raw: dict | None = None):
        self._resources = resources or []
        self._raw = raw or {}
        self.preview_calls: list[tuple[Path, dict]] = []

    def preview(self, bundle_dir: Path, env_values: dict[str, str]) -> AzdPreviewResult:
        self.preview_calls.append((bundle_dir, dict(env_values)))
        return AzdPreviewResult(resources=tuple(self._resources), raw=dict(self._raw))

    def provision(self, bundle_dir: Path, env_values: dict[str, str]) -> AzdCommandResult:  # pragma: no cover
        raise AssertionError("build_preview must not provision")

    def deploy(self, bundle_dir: Path, env_values: dict[str, str]) -> AzdCommandResult:  # pragma: no cover
        raise AssertionError("build_preview must not deploy")


class FakePermissionChecker:
    def __init__(
        self,
        *,
        arm_deployment: bool = True,
        role_assignment_write: bool = True,
        graph_application_readwrite: bool = True,
        group_read: bool = True,
    ):
        self._results = {
            "arm_deployment": arm_deployment,
            "role_assignment_write": role_assignment_write,
            "graph_application_readwrite": graph_application_readwrite,
            "group_read": group_read,
        }
        self.group_read_calls = 0
        self.role_assignment_scopes: list[str] = []

    def check_arm_deployment(self, *, subscription_id, resource_group):
        return PermissionResult(
            name="arm_deployment",
            granted=self._results["arm_deployment"],
            reason="" if self._results["arm_deployment"] else "missing Contributor",
        )

    def check_role_assignment_write(self, *, scope_resource_id):
        self.role_assignment_scopes.append(scope_resource_id)
        return PermissionResult(
            name="role_assignment_write",
            granted=self._results["role_assignment_write"],
            reason="" if self._results["role_assignment_write"] else "missing RBAC write",
        )

    def check_graph_application_readwrite(self, *, application_object_id):
        return PermissionResult(
            name="graph_application_readwrite",
            granted=self._results["graph_application_readwrite"],
            reason="" if self._results["graph_application_readwrite"] else "missing Graph scope",
        )

    def check_group_read(self, *, group_id):
        self.group_read_calls += 1
        return PermissionResult(
            name="group_read",
            granted=self._results["group_read"],
            reason="" if self._results["group_read"] else "missing group read",
        )


# ---------------------------------------------------------------------------
# Deterministic role-assignment identity (T018)
# ---------------------------------------------------------------------------


def test_derive_role_assignment_id_is_deterministic_across_calls():
    first = derive_role_assignment_id(
        principal_id=SP_OBJECT_ID, role="Reader", scope_resource_id=PROJECT_ID
    )
    second = derive_role_assignment_id(
        principal_id=SP_OBJECT_ID, role="Reader", scope_resource_id=PROJECT_ID
    )
    assert first == second
    assert isinstance(first, uuid.UUID)


def test_derive_role_assignment_id_differs_by_scope():
    a = derive_role_assignment_id(
        principal_id=SP_OBJECT_ID, role="Reader", scope_resource_id=PROJECT_ID
    )
    b = derive_role_assignment_id(
        principal_id=SP_OBJECT_ID,
        role="Reader",
        scope_resource_id=PROJECT_ID + "2",
    )
    assert a != b


def test_derive_role_assignment_id_differs_by_role():
    a = derive_role_assignment_id(
        principal_id=SP_OBJECT_ID, role="Reader", scope_resource_id=PROJECT_ID
    )
    b = derive_role_assignment_id(
        principal_id=SP_OBJECT_ID, role="Log Analytics Reader", scope_resource_id=PROJECT_ID
    )
    assert a != b


def test_derive_role_assignment_id_differs_by_principal():
    a = derive_role_assignment_id(
        principal_id=SP_OBJECT_ID, role="Reader", scope_resource_id=PROJECT_ID
    )
    b = derive_role_assignment_id(
        principal_id=GROUP_ID, role="Reader", scope_resource_id=PROJECT_ID
    )
    assert a != b


def test_derive_role_assignment_id_is_case_insensitive_on_scope_and_principal():
    lower = derive_role_assignment_id(
        principal_id=SP_OBJECT_ID.lower(), role="Reader", scope_resource_id=PROJECT_ID.lower()
    )
    upper_principal = derive_role_assignment_id(
        principal_id=SP_OBJECT_ID.upper(), role="Reader", scope_resource_id=PROJECT_ID
    )
    assert lower == upper_principal


# ---------------------------------------------------------------------------
# Non-secret application settings (T018)
# ---------------------------------------------------------------------------


def test_build_application_settings_includes_core_allowlisted_keys():
    selection = _selection()
    settings = build_application_settings(selection, uami_client_id="uami-client-id")

    assert settings["AGENTOPS_COCKPIT_MODE"] == "hosted"
    assert settings["AGENTOPS_TENANT_ID"] == TENANT_ID
    # Canonical name matches the packaged Bicep template's
    # ``applicationClientId`` app setting (no ``AGENTOPS_CLIENT_ID`` alias).
    assert settings["AGENTOPS_APPLICATION_CLIENT_ID"] == CLIENT_ID
    assert settings["AGENTOPS_UAMI_CLIENT_ID"] == "uami-client-id"
    assert "AGENTOPS_OBSERVE_SCOPE" in settings
    assert set(settings) <= ALLOWED_SETTINGS_KEYS
    assert "AGENTOPS_ALLOWED_GROUP_OBJECT_ID" not in settings


def test_build_application_settings_includes_allowed_group_when_set():
    selection = _selection(allowed_group_id=GROUP_ID)
    settings = build_application_settings(selection, uami_client_id="uami-client-id")

    # Canonical name matches the packaged Bicep template's
    # ``allowedGroupObjectId`` app setting (no ``AGENTOPS_ALLOWED_GROUP_ID``
    # alias).
    assert settings["AGENTOPS_ALLOWED_GROUP_OBJECT_ID"] == GROUP_ID


def test_build_application_settings_never_contains_secret_shaped_keys():
    selection = _selection()
    settings = build_application_settings(selection, uami_client_id="uami-client-id")

    forbidden = ("secret", "password", "connection_string", "token", "private_key")
    for key in settings:
        assert not any(term in key.lower() for term in forbidden)


def test_build_application_settings_raises_when_allowlist_shrinks_below_required_keys(monkeypatch):
    monkeypatch.setattr(
        cockpit_deployment,
        "ALLOWED_SETTINGS_KEYS",
        frozenset({"AGENTOPS_COCKPIT_MODE"}),
    )
    selection = _selection()

    with pytest.raises(CockpitDeploymentError) as excinfo:
        build_application_settings(selection, uami_client_id="uami-client-id")

    assert excinfo.value.stage == "build_preview"


# ---------------------------------------------------------------------------
# Prerequisite validation through injectable PermissionChecker (T019)
# ---------------------------------------------------------------------------


def test_validate_prerequisites_returns_all_checks_when_granted():
    selection = _selection()
    checker = FakePermissionChecker()

    results = validate_prerequisites(selection, permissions=checker)

    assert {r.name for r in results} == {
        "arm_deployment",
        "role_assignment_write",
        "graph_application_readwrite",
    }
    assert all(r.granted for r in results)
    assert checker.group_read_calls == 0


def test_validate_prerequisites_includes_group_check_only_when_allowed_group_id_set():
    selection = _selection(allowed_group_id=GROUP_ID)
    checker = FakePermissionChecker()

    results = validate_prerequisites(selection, permissions=checker)

    assert "group_read" in {r.name for r in results}
    assert checker.group_read_calls == 1


def test_validate_prerequisites_raises_prerequisite_error_naming_every_failed_check():
    selection = _selection(allowed_group_id=GROUP_ID)
    checker = FakePermissionChecker(role_assignment_write=False, group_read=False)

    with pytest.raises(PrerequisiteError) as excinfo:
        validate_prerequisites(selection, permissions=checker)

    assert set(excinfo.value.failed_checks) == {"role_assignment_write", "group_read"}
    assert excinfo.value.stage == "validate_prerequisites"
    assert excinfo.value.remediation


def test_validate_prerequisites_passes_when_only_optional_group_check_fails_is_absent():
    selection = _selection()
    checker = FakePermissionChecker(group_read=False)

    results = validate_prerequisites(selection, permissions=checker)

    assert all(r.granted for r in results)


def test_validate_prerequisites_checks_every_unique_planned_rbac_scope():
    selection = _selection()
    checker = FakePermissionChecker()
    telemetry_scope = (
        f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/telemetry/providers/"
        "Microsoft.OperationalInsights/workspaces/logs"
    )

    validate_prerequisites(
        selection,
        permissions=checker,
        role_assignment_scopes=[PROJECT_ID, telemetry_scope, PROJECT_ID],
    )

    assert set(checker.role_assignment_scopes) == {
        f"/subscriptions/{SUBSCRIPTION_ID}/resourcegroups/{RESOURCE_GROUP.lower()}",
        PROJECT_ID.lower(),
        telemetry_scope.lower(),
    }


# ---------------------------------------------------------------------------
# Preview construction (T020)
# ---------------------------------------------------------------------------


def _build(
    *,
    selection=None,
    telemetry_resource_ids=None,
    principal_id=None,
    client_id=None,
    existing_fics=None,
    azd_resources=None,
    azd_raw=None,
    bundle_dir=None,
):
    selection = selection or _selection()
    telemetry_discovery = FakeTelemetryDiscovery(telemetry_resource_ids)
    identity_resolver = FakeManagedIdentityResolver(principal_id=principal_id, client_id=client_id)
    app_registration = FakeAppRegistrationClient(existing_fics)
    azd_runner = FakeAzdCommandRunner(resources=azd_resources, raw=azd_raw)
    preview = build_preview(
        selection,
        telemetry_discovery=telemetry_discovery,
        identity_resolver=identity_resolver,
        app_registration=app_registration,
        azd_runner=azd_runner,
        bundle_dir=bundle_dir or Path("/bundle"),
    )
    return preview, telemetry_discovery, identity_resolver, app_registration, azd_runner


def test_build_preview_includes_app_service_plan_web_app_and_uami_resources():
    preview, *_ = _build()

    resource_types = {r.resource_type for r in preview.resources}
    assert resource_types == {"app_service_plan", "web_app", "user_assigned_managed_identity"}
    assert all(r.location == LOCATION for r in preview.resources)


def test_build_preview_includes_discovery_boundary_role_assignments():
    preview, *_ = _build()

    reader_assignments = [r for r in preview.role_assignments if r.role == "Reader"]
    assert len(reader_assignments) == 1
    assert reader_assignments[0].scope_resource_id == PROJECT_ID


def test_build_preview_includes_telemetry_role_assignments():
    telemetry_id = (
        f"/subscriptions/{SUBSCRIPTION_ID}/resourcegroups/rg-monitoring/providers/"
        "microsoft.operationalinsights/workspaces/law1"
    )
    preview, telemetry_discovery, *_ = _build(telemetry_resource_ids=[telemetry_id])

    telemetry_assignments = [
        r for r in preview.role_assignments if r.role == "Log Analytics Reader"
    ]
    assert len(telemetry_assignments) == 1
    assert telemetry_assignments[0].scope_resource_id == telemetry_id
    assert telemetry_discovery.calls == 1


def test_build_preview_uses_resolved_identity_when_available():
    principal_id = "77777777-7777-7777-7777-777777777777"
    client_id = "88888888-8888-8888-8888-888888888888"
    preview, *_ = _build(principal_id=principal_id, client_id=client_id)

    assert all(str(r.principal_id) == principal_id for r in preview.role_assignments)
    assert preview.application_settings["AGENTOPS_UAMI_CLIENT_ID"] == client_id
    assert str(preview.federated_credential.subject) == principal_id


def test_build_preview_uses_deterministic_placeholder_identity_when_not_yet_created():
    first, *_ = _build()
    second, *_ = _build()

    first_principal = {str(r.principal_id) for r in first.role_assignments}
    second_principal = {str(r.principal_id) for r in second.role_assignments}
    assert first_principal == second_principal


def test_build_preview_marks_federated_credential_create_when_none_exists():
    preview, *_ = _build()
    assert preview.federated_credential.action == "create"
    assert preview.federated_credential.audiences == ["api://AzureADTokenExchange"]


def test_build_preview_marks_federated_credential_reuse_when_matching_existing():
    principal_id = "77777777-7777-7777-7777-777777777777"
    name = f"agentops-cockpit-{APP_NAME}"
    issuer = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"
    existing = [_fic_info(name=name, issuer=issuer, subject=principal_id)]

    preview, *_ = _build(principal_id=principal_id, existing_fics=existing)

    assert preview.federated_credential.action == "reuse"
    assert not any(w.startswith("BLOCKED:") for w in preview.warnings)


def test_build_preview_marks_federated_credential_conflict_and_blocks():
    principal_id = "77777777-7777-7777-7777-777777777777"
    name = f"agentops-cockpit-{APP_NAME}"
    existing = [
        _fic_info(
            name=name,
            issuer="https://login.microsoftonline.com/other-tenant/v2.0",
            subject=principal_id,
        )
    ]

    preview, *_ = _build(principal_id=principal_id, existing_fics=existing)

    assert preview.federated_credential.action == "conflict"
    assert any(w.startswith("BLOCKED:") for w in preview.warnings)
    assert blocking_reasons(preview)


def test_build_preview_blocks_duplicate_named_federated_credentials():
    principal_id = "77777777-7777-7777-7777-777777777777"
    name = f"agentops-cockpit-{APP_NAME}"
    issuer = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"
    duplicate = _fic_info(name=name, issuer=issuer, subject=principal_id)

    preview, *_ = _build(
        principal_id=principal_id, existing_fics=[duplicate, duplicate]
    )

    assert preview.federated_credential.action == "conflict"
    assert blocking_reasons(preview)


def test_build_preview_blocks_federated_credential_capacity_exhaustion():
    existing = [
        _fic_info(name=f"existing-{index}", issuer="https://issuer", subject=PRINCIPAL_ID)
        for index in range(cockpit_deployment.MAX_FEDERATED_CREDENTIALS)
    ]

    preview, *_ = _build(existing_fics=existing)

    assert preview.federated_credential.action == "conflict"
    assert blocking_reasons(preview)


def test_build_preview_merges_change_types_from_azd_preview():
    azd_resources = [
        {"resource_type": "web_app", "change_type": "create"},
        {"resource_type": "app_service_plan", "change_type": "no_change"},
    ]
    preview, *_ = _build(azd_resources=azd_resources)

    by_type = {r.resource_type: r.change_type for r in preview.resources}
    assert by_type["web_app"] == "create"
    assert by_type["app_service_plan"] == "no_change"
    assert by_type["user_assigned_managed_identity"] == "unknown"


def test_build_preview_warns_blocked_for_disallowed_resource_type():
    azd_resources = [{"resource_type": "sql_server", "change_type": "create"}]
    preview, *_ = _build(azd_resources=azd_resources)

    assert blocking_reasons(preview)


def test_build_preview_warns_blocked_for_disallowed_change_type():
    azd_resources = [{"resource_type": "web_app", "change_type": "delete"}]
    preview, *_ = _build(azd_resources=azd_resources)

    assert blocking_reasons(preview)


def test_build_preview_warns_subscription_wide_scope():
    subscription_root = f"/subscriptions/{SUBSCRIPTION_ID}"
    selection = _selection(scope=_scope(
        mode="subscription",
        project_resource_ids=[],
        default_project_resource_id=None,
        root_resource_id=subscription_root,
    ))

    preview, *_ = _build(selection=selection)

    assert any("subscription-wide" in w.lower() for w in preview.warnings)
    assert not blocking_reasons(preview)


def test_build_preview_does_not_mutate_app_registration_or_azd_state():
    preview, _telemetry, _identity, app_registration, azd_runner = _build()

    assert app_registration.list_calls == 1
    assert app_registration.create_calls == 0
    assert len(azd_runner.preview_calls) == 1


def test_build_preview_passes_env_values_including_resource_group_and_location():
    preview, *_, azd_runner = _build()

    bundle_dir, env_values = azd_runner.preview_calls[0]
    assert bundle_dir == Path("/bundle")
    assert env_values["AZURE_SUBSCRIPTION_ID"] == SUBSCRIPTION_ID
    assert env_values["AZURE_RESOURCE_GROUP"] == RESOURCE_GROUP
    assert env_values["AZURE_LOCATION"] == LOCATION
    assert env_values["AGENTOPS_COCKPIT_APP_NAME"] == APP_NAME


def test_build_preview_env_values_satisfy_every_main_parameters_json_placeholder():
    """The azd env passed to ``preview`` must cover every substitution
    variable referenced by the packaged
    ``infra/main.parameters.json`` (``WEB_APP_NAME``, ``AZURE_LOCATION``,
    ``AZURE_ENV_NAME``, ``AZURE_TENANT_ID``,
    ``AGENTOPS_APPLICATION_CLIENT_ID``,
    ``AGENTOPS_ALLOWED_GROUP_OBJECT_ID``, ``AGENTOPS_OBSERVE_SCOPE``,
    ``AGENTOPS_VERSION``) so ``azd provision``/``azd deploy`` never fail on
    an unresolved parameter.
    """
    preview, *_, azd_runner = _build()

    _bundle_dir, env_values = azd_runner.preview_calls[0]
    assert env_values["WEB_APP_NAME"] == APP_NAME
    assert env_values["AZURE_ENV_NAME"] == APP_NAME
    assert env_values["AZURE_TENANT_ID"] == TENANT_ID
    assert env_values["AGENTOPS_APPLICATION_CLIENT_ID"] == CLIENT_ID
    assert "AGENTOPS_OBSERVE_SCOPE" in env_values
    assert env_values["AGENTOPS_VERSION"]
    # ``AGENTOPS_ALLOWED_GROUP_OBJECT_ID`` has an empty-string default in the
    # parameters file, so it is only present when the selection sets it;
    # this default selection has no allowed-group restriction.
    assert "AGENTOPS_ALLOWED_GROUP_OBJECT_ID" not in env_values
    assert preview is not None


def test_blocking_reasons_returns_empty_list_when_no_warnings_are_blocked():
    preview, *_ = _build()
    assert blocking_reasons(preview) == []


def test_blocking_reasons_filters_out_non_blocked_warnings():
    subscription_root = f"/subscriptions/{SUBSCRIPTION_ID}"
    selection = _selection(scope=_scope(
        mode="subscription",
        project_resource_ids=[],
        default_project_resource_id=None,
        root_resource_id=subscription_root,
    ))
    preview, *_ = _build(selection=selection)

    assert preview.warnings
    assert blocking_reasons(preview) == []


# ---------------------------------------------------------------------------
# Preview-before-mutation confirmation gating (T020)
# ---------------------------------------------------------------------------


def test_validate_confirmation_raises_preview_blocked_when_warnings_are_blocked():
    preview, *_ = _build(azd_resources=[{"resource_type": "sql_server", "change_type": "create"}])

    with pytest.raises(PreviewBlockedError) as excinfo:
        validate_confirmation(preview, yes=True, explicit_inputs_complete=True)

    assert excinfo.value.stage == "validate_confirmation"


def test_validate_confirmation_raises_confirmation_required_when_not_confirmed():
    preview, *_ = _build()

    with pytest.raises(ConfirmationRequiredError):
        validate_confirmation(preview, yes=False, explicit_inputs_complete=True)


def test_validate_confirmation_raises_confirmation_required_when_yes_but_inputs_incomplete():
    preview, *_ = _build()

    with pytest.raises(ConfirmationRequiredError) as excinfo:
        validate_confirmation(preview, yes=True, explicit_inputs_complete=False)

    assert "--yes" in str(excinfo.value)


def test_validate_confirmation_passes_when_yes_and_inputs_complete_and_not_blocked():
    preview, *_ = _build()

    # Should not raise.
    validate_confirmation(preview, yes=True, explicit_inputs_complete=True)


def test_validate_confirmation_accepts_interactive_confirmation_with_defaults():
    preview, *_ = _build()

    validate_confirmation(
        preview,
        yes=False,
        explicit_inputs_complete=False,
        interactive_confirmed=True,
    )


def test_validate_confirmation_blocked_preview_takes_precedence_over_missing_yes():
    preview, *_ = _build(azd_resources=[{"resource_type": "sql_server", "change_type": "create"}])

    with pytest.raises(PreviewBlockedError):
        validate_confirmation(preview, yes=False, explicit_inputs_complete=False)


# ---------------------------------------------------------------------------
# Production adapters: AzCliPermissionChecker, AzCliAppRegistrationClient,
# AzCliTelemetryDiscovery, AzCliManagedIdentityResolver, AzdCliCommandRunner.
#
# Every test below monkeypatches the single ``run_cli`` subprocess seam --
# no real ``az``/``azd`` invocation, no Azure SDK, no network calls.
# ---------------------------------------------------------------------------


def _fake_run_cli(responses: dict[tuple[str, ...], tuple[int, str, str]]):
    """Build a ``run_cli`` fake keyed by the leading CLI args tuple."""

    def _run_cli(args, *, cwd=None, env=None, timeout=300.0):
        key = tuple(args)
        for prefix, result in responses.items():
            if key[: len(prefix)] == prefix:
                return result
        raise AssertionError(f"unexpected run_cli invocation: {args}")

    return _run_cli


SIGNED_IN_OK = {("az", "ad", "signed-in-user", "show"): (0, '"user-1"', "")}


def test_azcli_permission_checker_arm_deployment_granted(monkeypatch):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli(
            {
                **SIGNED_IN_OK,
                ("az", "role", "assignment", "list"): (
                    0,
                    '[{"roleDefinitionName": "Contributor"}]',
                    "",
                ),
            }
        ),
    )
    checker = AzCliPermissionChecker()

    result = checker.check_arm_deployment(
        subscription_id=SUBSCRIPTION_ID, resource_group=RESOURCE_GROUP
    )

    assert result == PermissionResult(name="arm_deployment", granted=True, reason="")


def test_azcli_permission_checker_arm_deployment_denied_without_matching_role(monkeypatch):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli(
            {
                **SIGNED_IN_OK,
                ("az", "role", "assignment", "list"): (0, "[]", ""),
            }
        ),
    )
    checker = AzCliPermissionChecker()

    result = checker.check_arm_deployment(
        subscription_id=SUBSCRIPTION_ID, resource_group=RESOURCE_GROUP
    )

    assert result.name == "arm_deployment"
    assert result.granted is False
    assert "none of the required role" in result.reason


def test_azcli_permission_checker_arm_deployment_denied_when_not_signed_in(monkeypatch):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli({("az", "ad", "signed-in-user", "show"): (1, "", "not logged in")}),
    )
    checker = AzCliPermissionChecker()

    result = checker.check_arm_deployment(
        subscription_id=SUBSCRIPTION_ID, resource_group=RESOURCE_GROUP
    )

    assert result.granted is False
    assert "az login" in result.reason


def test_azcli_permission_checker_role_assignment_write_granted(monkeypatch):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli(
            {
                **SIGNED_IN_OK,
                ("az", "role", "assignment", "list"): (
                    0,
                    '[{"roleDefinitionName": "Owner"}]',
                    "",
                ),
            }
        ),
    )
    checker = AzCliPermissionChecker()

    result = checker.check_role_assignment_write(scope_resource_id=PROJECT_ID)

    assert result == PermissionResult(name="role_assignment_write", granted=True, reason="")


def test_azcli_permission_checker_graph_application_readwrite_granted(monkeypatch):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli(
            {
                **SIGNED_IN_OK,
                ("az", "ad", "app", "owner", "list"): (
                    0,
                    '[{"id": "user-1"}]',
                    "",
                ),
            }
        ),
    )
    checker = AzCliPermissionChecker()

    result = checker.check_graph_application_readwrite(application_object_id=APP_OBJECT_ID)

    assert result == PermissionResult(
        name="graph_application_readwrite", granted=True, reason=""
    )


def test_azcli_permission_checker_graph_application_readwrite_denied(monkeypatch):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli(
            {
                **SIGNED_IN_OK,
                ("az", "ad", "app", "owner", "list"): (0, "[]", ""),
                ("az", "rest"): (0, '{"value": []}', ""),
            }
        ),
    )
    checker = AzCliPermissionChecker()

    result = checker.check_graph_application_readwrite(application_object_id=APP_OBJECT_ID)

    assert result.granted is False
    assert result.name == "graph_application_readwrite"


def test_azcli_permission_checker_group_read_granted(monkeypatch):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli({("az", "ad", "group", "show"): (0, '{"id": "x"}', "")}),
    )
    checker = AzCliPermissionChecker()

    result = checker.check_group_read(group_id=GROUP_ID)

    assert result == PermissionResult(name="group_read", granted=True, reason="")


def test_azcli_permission_checker_group_read_denied(monkeypatch):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli({("az", "ad", "group", "show"): (1, "", "not found")}),
    )
    checker = AzCliPermissionChecker()

    result = checker.check_group_read(group_id=GROUP_ID)

    assert result.granted is False
    assert result.name == "group_read"


def test_azcli_app_registration_client_get_app_registration(monkeypatch):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli(
            {
                ("az", "ad", "app", "show"): (
                    0,
                    (
                        '{"id": "%s", "appId": "%s", "signInAudience": "AzureADMyOrg",'
                        ' "web": {"redirectUris": ["https://example.com/.auth/login/aad/callback"]}}'
                    )
                    % (APP_OBJECT_ID, CLIENT_ID),
                    "",
                ),
                (
                    "az",
                    "ad",
                    "sp",
                    "show",
                    "--id",
                    "ca7f3f0b-7d91-482c-8e09-c5d840d0eac5",
                ): (
                    0,
                    '{"id": "log-analytics-service-principal"}',
                    "",
                ),
                ("az", "ad", "sp", "show"): (
                    0,
                    '{"id": "%s"}' % SP_OBJECT_ID,
                    "",
                ),
                ("az", "rest"): (
                    0,
                    '{"value": [{"scope": "Data.Read"}]}',
                    "",
                ),
            }
        ),
    )
    client = AzCliAppRegistrationClient()

    info = client.get_app_registration(tenant_id=TENANT_ID, client_id=CLIENT_ID)

    assert cockpit_deployment.LOG_ANALYTICS_API_APP_ID == (
        "ca7f3f0b-7d91-482c-8e09-c5d840d0eac5"
    )
    assert cockpit_deployment.LOG_ANALYTICS_DELEGATED_SCOPE == "Data.Read"
    assert info.application_object_id == APP_OBJECT_ID
    assert info.client_id == CLIENT_ID
    assert info.service_principal_object_id == SP_OBJECT_ID
    assert info.tenant_id == TENANT_ID
    assert info.single_tenant is True
    assert info.redirect_uris == ("https://example.com/.auth/login/aad/callback",)
    assert info.has_delegated_consent is True
    assert info.auth_prerequisites_checked is True


def test_azcli_app_registration_rejects_unrelated_delegated_consent(monkeypatch):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli(
            {
                ("az", "ad", "app", "show"): (
                    0,
                    (
                        '{"id": "%s", "appId": "%s", "signInAudience": "AzureADMyOrg",'
                        ' "web": {"redirectUris": []}}'
                    )
                    % (APP_OBJECT_ID, CLIENT_ID),
                    "",
                ),
                (
                    "az",
                    "ad",
                    "sp",
                    "show",
                    "--id",
                    "ca7f3f0b-7d91-482c-8e09-c5d840d0eac5",
                ): (0, '{"id": "log-analytics-service-principal"}', ""),
                ("az", "ad", "sp", "show"): (0, '{"id": "%s"}' % SP_OBJECT_ID, ""),
                ("az", "rest"): (
                    0,
                    '{"value": [{"scope": "User.Read Mail.Read"}]}',
                    "",
                ),
            }
        ),
    )

    info = AzCliAppRegistrationClient().get_app_registration(
        tenant_id=TENANT_ID, client_id=CLIENT_ID
    )

    assert info.has_delegated_consent is False


def test_azcli_app_registration_client_get_app_registration_raises_lookup_error(monkeypatch):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli({("az", "ad", "app", "show"): (1, "", "app not found")}),
    )
    client = AzCliAppRegistrationClient()

    with pytest.raises(LookupError):
        client.get_app_registration(tenant_id=TENANT_ID, client_id=CLIENT_ID)


def test_azcli_app_registration_client_list_federated_credentials(monkeypatch):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli(
            {
                ("az", "ad", "app", "federated-credential", "list"): (
                    0,
                    (
                        '[{"id": "fic-1", "name": "n", "issuer": "https://token.actions'
                        '.githubusercontent.com", "subject": "s", "audiences": ["a"]}]'
                    ),
                    "",
                )
            }
        ),
    )
    client = AzCliAppRegistrationClient()

    credentials = client.list_federated_credentials(APP_OBJECT_ID)

    assert credentials == [
        FederatedCredentialInfo(
            id="fic-1",
            name="n",
            issuer="https://token.actions.githubusercontent.com",
            subject="s",
            audiences=("a",),
        )
    ]


def test_azcli_app_registration_client_create_federated_credential(monkeypatch):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli(
            {
                ("az", "ad", "app", "federated-credential", "create"): (
                    0,
                    '{"id": "fic-2", "name": "n2", "issuer": "iss", "subject": "sub",'
                    ' "audiences": ["a"]}',
                    "",
                )
            }
        ),
    )
    client = AzCliAppRegistrationClient()

    credential = client.create_federated_credential(
        APP_OBJECT_ID, name="n2", issuer="iss", subject="sub", audiences=("a",)
    )

    assert credential.id == "fic-2"
    assert credential.name == "n2"


def test_azcli_app_registration_client_create_federated_credential_raises_deployment_error(
    monkeypatch,
):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli(
            {
                ("az", "ad", "app", "federated-credential", "create"): (
                    1,
                    "",
                    "Insufficient privileges",
                )
            }
        ),
    )
    client = AzCliAppRegistrationClient()

    with pytest.raises(CockpitDeploymentError) as excinfo:
        client.create_federated_credential(
            APP_OBJECT_ID, name="n2", issuer="iss", subject="sub", audiences=("a",)
        )

    assert excinfo.value.stage == "federate"
    assert excinfo.value.mutation_occurred is False


def test_azcli_telemetry_discovery_returns_only_linked_workspaces():
    workspace_id = (
        f"/subscriptions/{SUBSCRIPTION_ID}/resourcegroups/{RESOURCE_GROUP}/providers/"
        "microsoft.operationalinsights/workspaces/law1"
    )
    client = SimpleNamespace(
        discover_sync=lambda scope: SimpleNamespace(
            partial_failures=[],
            telemetry_sources=[
                SimpleNamespace(state="available", workspace_id=workspace_id),
                SimpleNamespace(state="not_configured", workspace_id=None),
            ],
        )
    )
    discovery = AzCliTelemetryDiscovery(discovery_client=client)
    scope = _scope()

    resources = discovery.discover_telemetry_resources(scope)

    assert resources == [workspace_id]


def test_azcli_telemetry_discovery_blocks_incomplete_inventory():
    client = SimpleNamespace(
        discover_sync=lambda scope: SimpleNamespace(
            partial_failures=[{"source": "project", "reason": "denied"}],
            telemetry_sources=[],
        )
    )
    discovery = AzCliTelemetryDiscovery(discovery_client=client)

    with pytest.raises(PrerequisiteError, match="incomplete"):
        discovery.discover_telemetry_resources(_scope())


def test_azcli_managed_identity_resolver_resolves_principal_and_client_id(monkeypatch):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli(
            {
                ("az", "identity", "show"): (
                    0,
                    '{"principalId": "principal-1", "clientId": "client-1"}',
                    "",
                )
            }
        ),
    )
    resolver = AzCliManagedIdentityResolver()

    assert resolver.resolve_principal_id("uami-resource-id") == "principal-1"
    assert resolver.resolve_client_id("uami-resource-id") == "client-1"


def test_azcli_managed_identity_resolver_returns_none_when_identity_missing(monkeypatch):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli({("az", "identity", "show"): (1, "", "not found")}),
    )
    resolver = AzCliManagedIdentityResolver()

    assert resolver.resolve_principal_id("uami-resource-id") is None
    assert resolver.resolve_client_id("uami-resource-id") is None


def test_azd_cli_command_runner_preview_normalizes_changes(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def _run_cli(args, *, cwd=None, env=None, timeout=300.0):
        calls.append(list(args))
        if args[:2] == ["azd", "env"]:
            return 0, "", ""
        if args[:3] == ["azd", "provision", "--preview"]:
            return (
                0,
                (
                    '{"changes": [{"type": "Microsoft.Web/sites", "changeType": "create"},'
                    ' {"type": "Microsoft.Unknown/thing", "changeType": "create"}]}'
                ),
                "",
            )
        raise AssertionError(f"unexpected run_cli invocation: {args}")

    monkeypatch.setattr(cockpit_deployment, "run_cli", _run_cli)
    runner = AzdCliCommandRunner()

    result = runner.preview(tmp_path, {"AZURE_ENV_NAME": "env1"})

    assert result.resources == (
        {"resource_type": "web_app", "change_type": "create"},
        {"resource_type": "microsoft.unknown/thing", "change_type": "create"},
    )
    assert any(call[:3] == ["azd", "env", "select"] for call in calls)
    assert any(call[:3] == ["azd", "env", "set"] for call in calls)


def test_azd_cli_command_runner_preview_raises_on_failure(monkeypatch, tmp_path):
    def _run_cli(args, *, cwd=None, env=None, timeout=300.0):
        if args[:2] == ["azd", "env"]:
            return 0, "", ""
        if args[:3] == ["azd", "provision", "--preview"]:
            return 1, "", "provision failed"
        raise AssertionError(f"unexpected run_cli invocation: {args}")

    monkeypatch.setattr(cockpit_deployment, "run_cli", _run_cli)
    runner = AzdCliCommandRunner()

    with pytest.raises(CockpitDeploymentError) as excinfo:
        runner.preview(tmp_path, {"AZURE_ENV_NAME": "env1"})

    assert excinfo.value.stage == "preview"


def test_azd_cli_command_runner_provision_and_deploy_report_success(monkeypatch, tmp_path):
    def _run_cli(args, *, cwd=None, env=None, timeout=300.0):
        if args[:2] == ["azd", "env"]:
            return 0, "", ""
        if args[:2] == ["azd", "provision"]:
            return 0, "provisioned", ""
        if args[:2] == ["azd", "deploy"]:
            return 0, "deployed", ""
        raise AssertionError(f"unexpected run_cli invocation: {args}")

    monkeypatch.setattr(cockpit_deployment, "run_cli", _run_cli)
    runner = AzdCliCommandRunner()

    provision_result = runner.provision(tmp_path, {"AZURE_ENV_NAME": "env1"})
    deploy_result = runner.deploy(tmp_path, {"AZURE_ENV_NAME": "env1"})

    assert provision_result == AzdCommandResult(success=True, message="provisioned")
    assert deploy_result == AzdCommandResult(success=True, message="deployed")


def test_azd_cli_command_runner_provision_redacts_secrets_in_failure_message(
    monkeypatch, tmp_path
):
    def _run_cli(args, *, cwd=None, env=None, timeout=300.0):
        if args[:2] == ["azd", "env"]:
            return 0, "", ""
        if args[:2] == ["azd", "provision"]:
            return 1, "", 'error: "AZURE_CLIENT_SECRET": "abc123"'
        raise AssertionError(f"unexpected run_cli invocation: {args}")

    monkeypatch.setattr(cockpit_deployment, "run_cli", _run_cli)
    runner = AzdCliCommandRunner()

    result = runner.provision(tmp_path, {"AZURE_ENV_NAME": "env1"})

    assert result.success is False
    assert "abc123" not in result.message


# ---------------------------------------------------------------------------
# Template-expressible vs. explicit role assignment scope classification
# (critical parity: the packaged resource-group-scoped Bicep template must
# never be told to grant Reader/log-analytics access beyond exactly what
# FR-064 already computed -- no broadening, no silent under-provisioning).
# ---------------------------------------------------------------------------


def test_classify_role_assignment_scope_own_resource_group_is_resource_group():
    assignment = _role_assignment_plan(role="Reader", scope_resource_id=OWN_RESOURCE_GROUP)

    assert classify_role_assignment_scope(_selection(), assignment) == "resource_group"


def test_classify_role_assignment_scope_own_resource_group_rejects_non_reader_role():
    # Log Analytics Reader is never valid *at* resource-group scope --
    # _planned_role_assignments never emits this combination, but
    # classify_role_assignment_scope must still refuse to fold it into the
    # resource-group parameter if it were ever encountered.
    assignment = _role_assignment_plan(
        role="Log Analytics Reader", scope_resource_id=OWN_RESOURCE_GROUP
    )

    assert classify_role_assignment_scope(_selection(), assignment) is None


def test_classify_role_assignment_scope_foundry_account_same_resource_group():
    scope_id = f"{OWN_RESOURCE_GROUP}/providers/Microsoft.CognitiveServices/accounts/foundry1"
    assignment = _role_assignment_plan(role="Reader", scope_resource_id=scope_id)

    assert classify_role_assignment_scope(_selection(), assignment) == "foundry_account"


def test_classify_role_assignment_scope_foundry_project_same_resource_group():
    assignment = _role_assignment_plan(role="Reader", scope_resource_id=PROJECT_ID)

    assert classify_role_assignment_scope(_selection(), assignment) == "foundry_project"


def test_classify_role_assignment_scope_log_analytics_workspace_same_resource_group():
    scope_id = (
        f"{OWN_RESOURCE_GROUP}/providers/Microsoft.OperationalInsights/workspaces/law1"
    )
    assignment = _role_assignment_plan(
        role="Log Analytics Reader", scope_resource_id=scope_id
    )

    assert (
        classify_role_assignment_scope(_selection(), assignment)
        == "log_analytics_workspace"
    )


def test_classify_role_assignment_scope_role_mismatch_at_workspace_suffix_is_none():
    scope_id = (
        f"{OWN_RESOURCE_GROUP}/providers/Microsoft.OperationalInsights/workspaces/law1"
    )
    assignment = _role_assignment_plan(role="Reader", scope_resource_id=scope_id)

    assert classify_role_assignment_scope(_selection(), assignment) is None


def test_classify_role_assignment_scope_different_resource_group_is_none():
    scope_id = (
        f"{OTHER_RESOURCE_GROUP}/providers/Microsoft.CognitiveServices/accounts/foundry-x"
    )
    assignment = _role_assignment_plan(role="Reader", scope_resource_id=scope_id)

    assert classify_role_assignment_scope(_selection(), assignment) is None


def test_classify_role_assignment_scope_subscription_scope_is_none():
    scope_id = f"/subscriptions/{SUBSCRIPTION_ID}"
    assignment = _role_assignment_plan(role="Reader", scope_resource_id=scope_id)

    assert classify_role_assignment_scope(_selection(), assignment) is None


def test_bicep_role_assignment_parameters_sets_flags_and_dedups():
    selection = _selection()
    duplicate_workspace_scope = (
        f"{OWN_RESOURCE_GROUP}/providers/Microsoft.OperationalInsights/workspaces/law1"
    )
    assignments = [
        _role_assignment_plan(role="Reader", scope_resource_id=OWN_RESOURCE_GROUP),
        _role_assignment_plan(
            role="Reader",
            scope_resource_id=(
                f"{OWN_RESOURCE_GROUP}/providers/Microsoft.CognitiveServices/accounts/foundry1"
            ),
        ),
        _role_assignment_plan(role="Reader", scope_resource_id=PROJECT_ID),
        _role_assignment_plan(
            role="Log Analytics Reader", scope_resource_id=duplicate_workspace_scope
        ),
        _role_assignment_plan(
            role="Log Analytics Reader",
            scope_resource_id=duplicate_workspace_scope,
            principal_id="88888888-8888-8888-8888-888888888888",
        ),
        # Cross-resource-group -- must never leak into the Bicep parameters.
        _role_assignment_plan(
            role="Reader",
            scope_resource_id=(
                f"{OTHER_RESOURCE_GROUP}/providers/Microsoft.CognitiveServices/accounts/foundry-x"
            ),
        ),
    ]

    parameters = bicep_role_assignment_parameters(selection, assignments)

    assert parameters == {
        "grantReaderOnResourceGroup": True,
        "foundryAccountNames": ["foundry1"],
        "foundryProjectRefs": [{"accountName": "foundry1", "projectName": "proj1"}],
        "logAnalyticsWorkspaceNames": ["law1"],
    }


def test_bicep_role_assignment_parameters_never_broadens_for_cross_resource_group_only():
    selection = _selection()
    assignments = [
        _role_assignment_plan(
            role="Reader",
            scope_resource_id=(
                f"{OTHER_RESOURCE_GROUP}/providers/Microsoft.CognitiveServices/accounts/foundry-x"
            ),
        ),
        _role_assignment_plan(
            role="Reader", scope_resource_id=f"/subscriptions/{SUBSCRIPTION_ID}"
        ),
    ]

    parameters = bicep_role_assignment_parameters(selection, assignments)

    assert parameters == {
        "grantReaderOnResourceGroup": False,
        "foundryAccountNames": [],
        "foundryProjectRefs": [],
        "logAnalyticsWorkspaceNames": [],
    }


def test_explicit_role_assignments_returns_only_out_of_template_targets():
    selection = _selection()
    in_template = _role_assignment_plan(role="Reader", scope_resource_id=OWN_RESOURCE_GROUP)
    cross_resource_group = _role_assignment_plan(
        role="Reader",
        scope_resource_id=(
            f"{OTHER_RESOURCE_GROUP}/providers/Microsoft.CognitiveServices/accounts/foundry-x"
        ),
    )
    subscription_scope = _role_assignment_plan(
        role="Reader", scope_resource_id=f"/subscriptions/{SUBSCRIPTION_ID}"
    )

    result = explicit_role_assignments(
        selection, [in_template, cross_resource_group, subscription_scope]
    )

    assert result == [cross_resource_group, subscription_scope]


def test_planned_role_assignments_cross_resource_group_project_stays_out_of_template():
    other_project = (
        f"/subscriptions/{SUBSCRIPTION_ID}/resourcegroups/rg-other/providers/"
        "microsoft.cognitiveservices/accounts/foundry2/projects/proj2"
    )
    scope = _scope(
        project_resource_ids=[other_project], default_project_resource_id=other_project
    )
    selection = _selection(scope=scope)
    telemetry = FakeTelemetryDiscovery()

    assignments = cockpit_deployment._planned_role_assignments(
        selection, telemetry_discovery=telemetry, principal_id=PRINCIPAL_ID
    )
    parameters = bicep_role_assignment_parameters(selection, assignments)
    explicit = explicit_role_assignments(selection, assignments)

    # Under-provisioning check: nothing leaks into the resource-group-scoped
    # Bicep parameters for a project living in a different resource group.
    assert parameters == {
        "grantReaderOnResourceGroup": False,
        "foundryAccountNames": [],
        "foundryProjectRefs": [],
        "logAnalyticsWorkspaceNames": [],
    }
    # Over-provisioning check: the cross-RG grant is still tracked so it can
    # be applied explicitly (see AzCliRoleAssignmentClient), never dropped.
    assert len(explicit) == 1
    assert explicit[0].scope_resource_id == other_project.lower()


def test_resolve_role_assignment_scope_matches_default_projects_fixture():
    selection = _selection()
    telemetry = FakeTelemetryDiscovery()

    resolved = cockpit_deployment._resolve_role_assignment_scope(
        selection, telemetry_discovery=telemetry
    )

    assert resolved == {
        "grantReaderOnResourceGroup": False,
        "foundryAccountNames": [],
        "foundryProjectRefs": [{"accountName": "foundry1", "projectName": "proj1"}],
        "logAnalyticsWorkspaceNames": [],
    }


# ---------------------------------------------------------------------------
# AzCliRoleAssignmentClient: the subprocess adapter that applies exactly the
# explicit_role_assignments() targets the packaged template cannot express.
# ---------------------------------------------------------------------------


def test_azcli_role_assignment_client_short_circuits_when_already_exists(monkeypatch):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli(
            {
                ("az", "role", "assignment", "list"): (0, '[{"id": "existing"}]', ""),
                ("az", "role", "assignment", "create"): (
                    1,
                    "",
                    "must not be invoked when already assigned",
                ),
            }
        ),
    )
    client = AzCliRoleAssignmentClient()

    created = client.ensure_role_assignment(
        assignment_id=uuid.uuid4(),
        scope_resource_id=OWN_RESOURCE_GROUP,
        principal_id=PRINCIPAL_ID,
        role_definition_id=cockpit_deployment._role_definition_resource_id(
            SUBSCRIPTION_ID, "Reader"
        ),
    )

    assert created is False


def test_azcli_role_assignment_client_creates_when_missing(monkeypatch):
    calls: list[list[str]] = []

    def _run_cli(args, *, cwd=None, env=None, timeout=300.0):
        calls.append(list(args))
        if args[:4] == ["az", "role", "assignment", "list"]:
            return 0, "[]", ""
        if args[:4] == ["az", "role", "assignment", "create"]:
            return 0, '{"id": "new"}', ""
        raise AssertionError(f"unexpected run_cli invocation: {args}")

    monkeypatch.setattr(cockpit_deployment, "run_cli", _run_cli)
    client = AzCliRoleAssignmentClient()
    assignment_id = uuid.uuid4()
    role_definition_id = cockpit_deployment._role_definition_resource_id(
        SUBSCRIPTION_ID, "Reader"
    )

    created = client.ensure_role_assignment(
        assignment_id=assignment_id,
        scope_resource_id=OWN_RESOURCE_GROUP,
        principal_id=PRINCIPAL_ID,
        role_definition_id=role_definition_id,
    )

    assert created is True
    create_call = next(call for call in calls if call[:4] == ["az", "role", "assignment", "create"])
    assert "--assignee-object-id" in create_call
    assert PRINCIPAL_ID in create_call
    assert role_definition_id in create_call
    assert OWN_RESOURCE_GROUP in create_call
    assert str(assignment_id) in create_call


def test_azcli_role_assignment_client_raises_actionable_redacted_error_on_failure(
    monkeypatch,
):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli(
            {
                ("az", "role", "assignment", "list"): (0, "[]", ""),
                ("az", "role", "assignment", "create"): (
                    1,
                    "",
                    'error: "client_secret": "abc123" insufficient privileges',
                ),
            }
        ),
    )
    client = AzCliRoleAssignmentClient()

    with pytest.raises(CockpitDeploymentError) as excinfo:
        client.ensure_role_assignment(
            assignment_id=uuid.uuid4(),
            scope_resource_id=OWN_RESOURCE_GROUP,
            principal_id=PRINCIPAL_ID,
            role_definition_id=cockpit_deployment._role_definition_resource_id(
                SUBSCRIPTION_ID, "Reader"
            ),
        )

    assert excinfo.value.stage == "provision"
    assert excinfo.value.mutation_occurred is False
    assert "abc123" not in str(excinfo.value)
