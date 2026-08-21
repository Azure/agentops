"""Unit tests for selection precedence/ambiguity, workspace project
resolution, Observe scope validation, and deployment-name normalization in
``agentops.services.cockpit_deployment`` (issue #433, Phase 3, T019/T021).

These tests use only in-memory fakes for the injectable ``ProjectResolver``,
``AzureContext``, and ``AppRegistrationClient`` protocols -- no Azure SDK,
Azure CLI, or network calls occur.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentops.core.observe import ObserveScope
from agentops.services.cockpit_deployment import (
    AmbiguousSelectionError,
    AppRegistrationInfo,
    AzCliContext,
    CockpitDeploymentError,
    DeploymentRequest,
    PrerequisiteError,
    WorkspaceProjectResolver,
    WorkspaceResolutionError,
    normalize_deployment_name,
    resolve_context,
    resolve_scope,
    resolve_selection,
)
import agentops.services.cockpit_deployment as cockpit_deployment

# NOTE: these are intentionally already-lowercase so they equal their
# canonicalized (``canonical_arm_id``) form byte-for-byte, keeping assertions
# below simple. ``canonical_arm_id`` lower-cases the entire ARM ID.
PROJECT_A = (
    "/subscriptions/00000000-0000-0000-0000-000000000001/resourcegroups/"
    "rg-agentops/providers/microsoft.cognitiveservices/accounts/foundry/"
    "projects/project-a"
)
PROJECT_B = (
    "/subscriptions/00000000-0000-0000-0000-000000000001/resourcegroups/"
    "rg-agentops/providers/microsoft.cognitiveservices/accounts/foundry/"
    "projects/project-b"
)
FOUNDRY_ROOT = (
    "/subscriptions/00000000-0000-0000-0000-000000000001/resourcegroups/"
    "rg-agentops/providers/microsoft.cognitiveservices/accounts/foundry"
)
RESOURCE_GROUP_ROOT = (
    "/subscriptions/00000000-0000-0000-0000-000000000001/resourcegroups/rg-agentops"
)
SUBSCRIPTION_ROOT = "/subscriptions/00000000-0000-0000-0000-000000000001"

TENANT_ID = "22222222-2222-2222-2222-222222222222"
CLIENT_ID = "33333333-3333-3333-3333-333333333333"
SUBSCRIPTION_ID = "00000000-0000-0000-0000-000000000001"


class FakeProjectResolver:
    """Deterministic in-memory :class:`ProjectResolver` fake."""

    def __init__(self, projects: list[str]) -> None:
        self._projects = projects
        self.calls = 0

    def discover_projects(self, workspace: Path) -> list[str]:
        self.calls += 1
        return list(self._projects)


class FakeAzureContext:
    """Deterministic in-memory :class:`AzureContext` fake."""

    def __init__(
        self,
        *,
        subscription_id: str | None = SUBSCRIPTION_ID,
        tenant_id: str | None = TENANT_ID,
        location: str | None = "eastus",
    ) -> None:
        self._subscription_id = subscription_id
        self._tenant_id = tenant_id
        self._location = location

    def current_subscription_id(self) -> str | None:
        return self._subscription_id

    def current_tenant_id(self) -> str | None:
        return self._tenant_id

    def current_location(self) -> str | None:
        return self._location


class FakeAppRegistrationClient:
    """Deterministic in-memory :class:`AppRegistrationClient` fake.

    Only the read-only lookup used by selection resolution is exercised in
    this file; federation is covered in ``test_cockpit_deployment_preview.py``
    and ``test_cockpit_auth_settings.py``.
    """

    def __init__(self, registration: AppRegistrationInfo | None) -> None:
        self._registration = registration
        self.lookup_calls = 0

    def get_app_registration(self, *, tenant_id: str, client_id: str) -> AppRegistrationInfo:
        self.lookup_calls += 1
        if self._registration is None:
            raise LookupError(f"no app registration for client_id={client_id}")
        return self._registration

    def list_federated_credentials(self, application_object_id: str) -> list:
        return []

    def create_federated_credential(self, application_object_id: str, **kwargs) -> None:
        raise AssertionError("create_federated_credential must not be called during selection")


def _registration(**overrides) -> AppRegistrationInfo:
    defaults = dict(
        application_object_id="44444444-4444-4444-4444-444444444444",
        client_id=CLIENT_ID,
        service_principal_object_id="55555555-5555-5555-5555-555555555555",
        tenant_id=TENANT_ID,
        single_tenant=True,
    )
    defaults.update(overrides)
    return AppRegistrationInfo(**defaults)


# ---------------------------------------------------------------------------
# normalize_deployment_name
# ---------------------------------------------------------------------------


def test_normalize_deployment_name_explicit_value_is_slugified_without_suffix():
    name = normalize_deployment_name(
        "My Cockpit!!",
        workspace=Path("/workspace"),
        project_hint="project-a",
        subscription_id=SUBSCRIPTION_ID,
        resource_group="rg-agentops",
    )
    assert name == "my-cockpit"


def test_normalize_deployment_name_default_is_derived_and_suffixed():
    name = normalize_deployment_name(
        None,
        workspace=Path("/workspace/my_app"),
        project_hint="project-a",
        subscription_id=SUBSCRIPTION_ID,
        resource_group="rg-agentops",
    )
    assert name.startswith("agentops-cockpit-my-app-project-a-")
    suffix = name.rsplit("-", 1)[-1]
    assert len(suffix) == 8
    assert all(ch in "0123456789abcdef" for ch in suffix)


def test_normalize_deployment_name_is_deterministic_across_reruns():
    kwargs = dict(
        workspace=Path("/workspace/my_app"),
        project_hint="project-a",
        subscription_id=SUBSCRIPTION_ID,
        resource_group="rg-agentops",
    )
    first = normalize_deployment_name(None, **kwargs)
    second = normalize_deployment_name(None, **kwargs)
    assert first == second


def test_normalize_deployment_name_differs_across_subscriptions():
    kwargs = dict(
        workspace=Path("/workspace/my_app"),
        project_hint="project-a",
        resource_group="rg-agentops",
    )
    first = normalize_deployment_name(
        None, subscription_id="00000000-0000-0000-0000-000000000001", **kwargs
    )
    second = normalize_deployment_name(
        None, subscription_id="00000000-0000-0000-0000-000000000002", **kwargs
    )
    assert first != second


def test_normalize_deployment_name_respects_length_and_charset():
    name = normalize_deployment_name(
        "A" * 100,
        workspace=Path("/workspace"),
        project_hint="",
        subscription_id=SUBSCRIPTION_ID,
        resource_group="rg-agentops",
    )
    assert len(name) <= 60
    assert not name.startswith("-")
    assert not name.endswith("-")
    assert all(ch.islower() or ch.isdigit() or ch == "-" for ch in name)


def test_normalize_deployment_name_falls_back_when_workspace_name_is_empty():
    name = normalize_deployment_name(
        None,
        workspace=Path("/"),
        project_hint="",
        subscription_id=SUBSCRIPTION_ID,
        resource_group="rg-agentops",
    )
    assert name.startswith("agentops-cockpit-")


# ---------------------------------------------------------------------------
# resolve_scope: precedence and ambiguity (projects mode)
# ---------------------------------------------------------------------------


def test_resolve_scope_explicit_project_ids_skip_workspace_discovery():
    resolver = FakeProjectResolver([PROJECT_B])
    request = DeploymentRequest(workspace=Path("/workspace"), project_ids=(PROJECT_A,))

    resolved = resolve_scope(request, project_resolver=resolver)

    assert resolver.calls == 0
    assert resolved.scope.mode == "projects"
    assert resolved.scope.project_resource_ids == [PROJECT_A]
    assert resolved.scope.default_project_resource_id == PROJECT_A
    assert resolved.explicit_inputs_complete is True


def test_resolve_scope_discovers_single_project_when_not_explicit():
    resolver = FakeProjectResolver([PROJECT_A])
    request = DeploymentRequest(workspace=Path("/workspace"))

    resolved = resolve_scope(request, project_resolver=resolver)

    assert resolver.calls == 1
    assert resolved.scope.project_resource_ids == [PROJECT_A]
    assert resolved.explicit_inputs_complete is False


def test_resolve_scope_raises_workspace_resolution_error_when_no_project_found():
    resolver = FakeProjectResolver([])
    request = DeploymentRequest(workspace=Path("/workspace"))

    with pytest.raises(WorkspaceResolutionError) as excinfo:
        resolve_scope(request, project_resolver=resolver)

    assert excinfo.value.stage == "resolve_scope"
    assert excinfo.value.remediation


def test_resolve_scope_raises_ambiguous_selection_error_for_multiple_projects():
    resolver = FakeProjectResolver([PROJECT_A, PROJECT_B])
    request = DeploymentRequest(workspace=Path("/workspace"))

    with pytest.raises(AmbiguousSelectionError) as excinfo:
        resolve_scope(request, project_resolver=resolver)

    assert set(excinfo.value.candidates) == {PROJECT_A, PROJECT_B}
    assert excinfo.value.stage == "resolve_scope"


def test_resolve_scope_projects_mode_rejects_explicit_scope_resource_id():
    resolver = FakeProjectResolver([PROJECT_A])
    request = DeploymentRequest(
        workspace=Path("/workspace"), scope_resource_id=FOUNDRY_ROOT
    )

    with pytest.raises(CockpitDeploymentError):
        resolve_scope(request, project_resolver=resolver)


# ---------------------------------------------------------------------------
# resolve_scope: non-projects modes (foundry/resource_group/subscription)
# ---------------------------------------------------------------------------


def test_resolve_scope_non_projects_mode_rejects_project_ids():
    resolver = FakeProjectResolver([])
    request = DeploymentRequest(
        workspace=Path("/workspace"),
        scope_mode="foundry",
        project_ids=(PROJECT_A,),
        scope_resource_id=FOUNDRY_ROOT,
    )

    with pytest.raises(CockpitDeploymentError):
        resolve_scope(request, project_resolver=resolver)


def test_resolve_scope_non_projects_mode_requires_scope_resource_id():
    resolver = FakeProjectResolver([])
    request = DeploymentRequest(workspace=Path("/workspace"), scope_mode="foundry")

    with pytest.raises(CockpitDeploymentError) as excinfo:
        resolve_scope(request, project_resolver=resolver)

    assert "scope-resource-id" in str(excinfo.value)


def test_resolve_scope_foundry_mode_builds_matching_observe_scope():
    resolver = FakeProjectResolver([])
    request = DeploymentRequest(
        workspace=Path("/workspace"), scope_mode="foundry", scope_resource_id=FOUNDRY_ROOT
    )

    resolved = resolve_scope(request, project_resolver=resolver)

    assert isinstance(resolved.scope, ObserveScope)
    assert resolved.scope.mode == "foundry"
    assert resolved.scope.root_resource_id == FOUNDRY_ROOT.lower()
    assert resolved.explicit_inputs_complete is True
    assert resolved.warnings == []


def test_resolve_scope_resource_group_mode_builds_matching_observe_scope():
    resolver = FakeProjectResolver([])
    request = DeploymentRequest(
        workspace=Path("/workspace"),
        scope_mode="resource_group",
        scope_resource_id=RESOURCE_GROUP_ROOT,
    )

    resolved = resolve_scope(request, project_resolver=resolver)

    assert resolved.scope.mode == "resource_group"
    assert resolved.scope.root_resource_id == RESOURCE_GROUP_ROOT.lower()


def test_resolve_scope_subscription_mode_emits_warning():
    resolver = FakeProjectResolver([])
    request = DeploymentRequest(
        workspace=Path("/workspace"),
        scope_mode="subscription",
        scope_resource_id=SUBSCRIPTION_ROOT,
    )

    resolved = resolve_scope(request, project_resolver=resolver)

    assert resolved.scope.mode == "subscription"
    assert any("subscription-wide" in w.lower() for w in resolved.warnings)


def test_resolve_scope_wraps_invalid_resource_id_shape_as_deployment_error():
    resolver = FakeProjectResolver([])
    request = DeploymentRequest(
        workspace=Path("/workspace"),
        scope_mode="foundry",
        scope_resource_id=RESOURCE_GROUP_ROOT,  # wrong shape for "foundry"
    )

    with pytest.raises(CockpitDeploymentError) as excinfo:
        resolve_scope(request, project_resolver=resolver)

    assert excinfo.value.stage == "resolve_scope"


# ---------------------------------------------------------------------------
# resolve_context: precedence and missing-value reporting
# ---------------------------------------------------------------------------


def test_resolve_context_prefers_explicit_values_over_azure_context():
    context = FakeAzureContext(
        subscription_id="99999999-9999-9999-9999-999999999999",
        tenant_id="88888888-8888-8888-8888-888888888888",
        location="westus",
    )
    request = DeploymentRequest(
        workspace=Path("/workspace"),
        subscription_id=SUBSCRIPTION_ID,
        resource_group="rg-agentops",
        location="eastus2",
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
    )

    resolved = resolve_context(request, context=context)

    assert resolved.subscription_id == SUBSCRIPTION_ID
    assert resolved.location == "eastus2"
    assert resolved.tenant_id == TENANT_ID
    assert resolved.explicit_inputs_complete is True


def test_resolve_context_falls_back_to_azure_context_when_unset():
    context = FakeAzureContext()
    request = DeploymentRequest(
        workspace=Path("/workspace"), resource_group="rg-agentops", client_id=CLIENT_ID
    )

    resolved = resolve_context(request, context=context)

    assert resolved.subscription_id == SUBSCRIPTION_ID
    assert resolved.tenant_id == TENANT_ID
    assert resolved.location == "eastus"
    assert resolved.explicit_inputs_complete is False


def test_resolve_context_uses_scope_resource_group_without_marking_it_explicit():
    context = FakeAzureContext()
    request = DeploymentRequest(workspace=Path("/workspace"), client_id=CLIENT_ID)

    resolved = resolve_context(
        request,
        context=context,
        default_resource_group="workspace-rg",
    )

    assert resolved.resource_group == "workspace-rg"
    assert resolved.explicit_inputs_complete is False


def test_resolve_context_raises_on_missing_required_value_naming_the_flag():
    context = FakeAzureContext(subscription_id=None)
    request = DeploymentRequest(
        workspace=Path("/workspace"), resource_group="rg-agentops", client_id=CLIENT_ID
    )

    with pytest.raises(CockpitDeploymentError) as excinfo:
        resolve_context(request, context=context)

    assert "--subscription" in str(excinfo.value)
    assert excinfo.value.stage == "resolve_context"


def test_resolve_context_reports_multiple_missing_values():
    context = FakeAzureContext(subscription_id=None, tenant_id=None, location=None)
    request = DeploymentRequest(workspace=Path("/workspace"))

    with pytest.raises(CockpitDeploymentError) as excinfo:
        resolve_context(request, context=context)

    message = str(excinfo.value)
    assert "--subscription" in message
    assert "--resource-group" in message
    assert "--client-id" in message


# ---------------------------------------------------------------------------
# resolve_selection: end-to-end precedence, app-registration validation
# ---------------------------------------------------------------------------


def test_resolve_selection_happy_path_builds_full_deployment_selection():
    resolver = FakeProjectResolver([PROJECT_A])
    context = FakeAzureContext()
    app_registration = FakeAppRegistrationClient(_registration())
    request = DeploymentRequest(
        workspace=Path("/workspace/my_app"),
        resource_group="rg-agentops",
        client_id=CLIENT_ID,
    )

    selection, explicit_inputs_complete, warnings = resolve_selection(
        request,
        context=context,
        project_resolver=resolver,
        app_registration=app_registration,
    )

    assert str(selection.subscription_id) == SUBSCRIPTION_ID
    assert selection.resource_group == "rg-agentops"
    assert selection.location == "eastus"
    assert str(selection.tenant_id) == TENANT_ID
    assert str(selection.client_id) == CLIENT_ID
    assert selection.scope.mode == "projects"
    assert selection.scope.project_resource_ids == [PROJECT_A]
    assert selection.app_name.startswith("agentops-cockpit-my-app-")
    # Project discovery was not explicit, so --yes must not be permitted yet.
    assert explicit_inputs_complete is False
    assert warnings == []
    assert app_registration.lookup_calls == 1


def test_resolve_selection_explicit_inputs_complete_when_every_flag_is_explicit():
    resolver = FakeProjectResolver([])
    context = FakeAzureContext()
    app_registration = FakeAppRegistrationClient(_registration())
    request = DeploymentRequest(
        workspace=Path("/workspace"),
        project_ids=(PROJECT_A,),
        subscription_id=SUBSCRIPTION_ID,
        resource_group="rg-agentops",
        location="eastus",
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
    )

    _selection, explicit_inputs_complete, _warnings = resolve_selection(
        request,
        context=context,
        project_resolver=resolver,
        app_registration=app_registration,
    )

    assert explicit_inputs_complete is True
    assert resolver.calls == 0


def test_resolve_selection_raises_prerequisite_error_when_app_registration_missing():
    resolver = FakeProjectResolver([PROJECT_A])
    context = FakeAzureContext()
    app_registration = FakeAppRegistrationClient(None)
    request = DeploymentRequest(
        workspace=Path("/workspace"), resource_group="rg-agentops", client_id=CLIENT_ID
    )

    with pytest.raises(PrerequisiteError) as excinfo:
        resolve_selection(
            request,
            context=context,
            project_resolver=resolver,
            app_registration=app_registration,
        )

    assert "app_registration_lookup" in excinfo.value.failed_checks


def test_resolve_selection_raises_prerequisite_error_on_tenant_mismatch():
    resolver = FakeProjectResolver([PROJECT_A])
    context = FakeAzureContext()
    mismatched = _registration(tenant_id="77777777-7777-7777-7777-777777777777")
    app_registration = FakeAppRegistrationClient(mismatched)
    request = DeploymentRequest(
        workspace=Path("/workspace"), resource_group="rg-agentops", client_id=CLIENT_ID
    )

    with pytest.raises(PrerequisiteError) as excinfo:
        resolve_selection(
            request,
            context=context,
            project_resolver=resolver,
            app_registration=app_registration,
        )

    assert "app_registration_tenant_match" in excinfo.value.failed_checks


def test_resolve_selection_raises_prerequisite_error_when_app_is_multi_tenant():
    resolver = FakeProjectResolver([PROJECT_A])
    context = FakeAzureContext()
    app_registration = FakeAppRegistrationClient(_registration(single_tenant=False))
    request = DeploymentRequest(
        workspace=Path("/workspace"), resource_group="rg-agentops", client_id=CLIENT_ID
    )

    with pytest.raises(PrerequisiteError) as excinfo:
        resolve_selection(
            request,
            context=context,
            project_resolver=resolver,
            app_registration=app_registration,
        )

    assert "app_registration_single_tenant" in excinfo.value.failed_checks


def test_resolve_selection_propagates_ambiguous_selection_error():
    resolver = FakeProjectResolver([PROJECT_A, PROJECT_B])
    context = FakeAzureContext()
    app_registration = FakeAppRegistrationClient(_registration())
    request = DeploymentRequest(workspace=Path("/workspace"), resource_group="rg-agentops")

    with pytest.raises(AmbiguousSelectionError):
        resolve_selection(
            request,
            context=context,
            project_resolver=resolver,
            app_registration=app_registration,
        )


def test_resolve_context_rejects_non_public_azure_cloud():
    class SovereignContext(FakeAzureContext):
        def current_cloud_name(self):
            return "AzureUSGovernment"

    request = DeploymentRequest(
        workspace=Path("/workspace"), resource_group="rg-agentops", client_id=CLIENT_ID
    )

    with pytest.raises(PrerequisiteError) as excinfo:
        resolve_context(request, context=SovereignContext())

    assert "azure_public_cloud" in excinfo.value.failed_checks


def test_resolve_selection_live_validates_explicit_projects():
    class ValidatingResolver(FakeProjectResolver):
        def validate_project(self, project_resource_id, *, subscription_id, tenant_id):
            return cockpit_deployment.PermissionResult(
                name=f"project_read:{project_resource_id}",
                granted=False,
                reason="not readable",
            )

    request = DeploymentRequest(
        workspace=Path("/workspace"),
        project_ids=(PROJECT_A,),
        subscription_id=SUBSCRIPTION_ID,
        resource_group="rg-agentops",
        location="eastus",
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
    )

    with pytest.raises(PrerequisiteError) as excinfo:
        resolve_selection(
            request,
            context=FakeAzureContext(),
            project_resolver=ValidatingResolver([]),
            app_registration=FakeAppRegistrationClient(_registration()),
        )

    assert any(check.startswith("project_read:") for check in excinfo.value.failed_checks)


def test_resolve_selection_rejects_unavailable_web_app_hostname():
    class UnavailableContext(FakeAzureContext):
        def check_web_app_name_available(self, name):
            return cockpit_deployment.PermissionResult(
                name="web_app_name_available", granted=False, reason="already allocated"
            )

    request = DeploymentRequest(
        workspace=Path("/workspace"),
        project_ids=(PROJECT_A,),
        resource_group="rg-agentops",
        client_id=CLIENT_ID,
        name="taken-name",
    )

    with pytest.raises(PrerequisiteError) as excinfo:
        resolve_selection(
            request,
            context=UnavailableContext(),
            project_resolver=FakeProjectResolver([]),
            app_registration=FakeAppRegistrationClient(_registration()),
        )

    assert "web_app_name_available" in excinfo.value.failed_checks


def test_resolve_selection_reuses_unavailable_hostname_owned_by_target_deployment():
    class ExistingDeploymentContext(FakeAzureContext):
        def check_web_app_name_available(self, name):
            return cockpit_deployment.PermissionResult(
                name="web_app_name_available", granted=False, reason="already allocated"
            )

        def resource_exists(self, resource_id):
            return resource_id.endswith("/sites/existing-cockpit")

    request = DeploymentRequest(
        workspace=Path("/workspace"),
        name="existing-cockpit",
        project_ids=(PROJECT_A,),
        resource_group="rg-agentops",
        location="eastus",
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
    )

    selection, _, _ = resolve_selection(
        request,
        context=ExistingDeploymentContext(),
        project_resolver=FakeProjectResolver([]),
        app_registration=FakeAppRegistrationClient(_registration()),
    )

    assert selection.app_name == "existing-cockpit"


def test_resolve_selection_requires_expected_redirect_and_delegated_consent():
    request = DeploymentRequest(
        workspace=Path("/workspace"),
        project_ids=(PROJECT_A,),
        resource_group="rg-agentops",
        client_id=CLIENT_ID,
        name="my-cockpit",
    )
    registration = _registration(
        redirect_uris=("https://wrong.example/callback",),
        has_delegated_consent=False,
        auth_prerequisites_checked=True,
    )

    with pytest.raises(PrerequisiteError) as excinfo:
        resolve_selection(
            request,
            context=FakeAzureContext(),
            project_resolver=FakeProjectResolver([]),
            app_registration=FakeAppRegistrationClient(registration),
        )

    assert set(excinfo.value.failed_checks) == {
        "app_registration_redirect_uri",
        "app_registration_delegated_consent",
    }


# ---------------------------------------------------------------------------
# Production adapters: AzCliContext / WorkspaceProjectResolver.
#
# These exercise the concrete ``az``-CLI-backed ``AzureContext`` and
# ``ProjectResolver`` implementations by monkeypatching the single
# ``run_cli`` subprocess seam -- no real ``az`` invocation, no Azure SDK,
# no network calls.
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


def test_azcli_context_reads_subscription_and_tenant_from_account_show(monkeypatch):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli(
            {
                ("az", "account", "show"): (
                    0,
                    '{"id": "sub-1", "tenantId": "tenant-1"}',
                    "",
                )
            }
        ),
    )
    context = AzCliContext()

    assert context.current_subscription_id() == "sub-1"
    assert context.current_tenant_id() == "tenant-1"
    assert context.current_location() is None


def test_azcli_context_returns_none_when_account_show_fails(monkeypatch):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli({("az", "account", "show"): (1, "", "not logged in")}),
    )
    context = AzCliContext()

    assert context.current_subscription_id() is None
    assert context.current_tenant_id() is None


def test_azcli_context_returns_none_when_account_show_output_is_malformed(monkeypatch):
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli({("az", "account", "show"): (0, "not-json", "")}),
    )
    context = AzCliContext()

    assert context.current_subscription_id() is None
    assert context.current_tenant_id() is None


def test_workspace_project_resolver_discovers_from_agentops_env_fallback(
    tmp_path, monkeypatch
):
    workspace = tmp_path
    env_dir = workspace / ".agentops"
    env_dir.mkdir(parents=True)
    (env_dir / ".env").write_text(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="
        "https://acct1.services.ai.azure.com/api/projects/proj1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli(
            {
                ("az", "resource", "list"): (
                    0,
                    '[{"id": "/subscriptions/sub-1/resourceGroups/rg1/providers/'
                    'Microsoft.CognitiveServices/accounts/acct1"}]',
                    "",
                )
            }
        ),
    )
    resolver = WorkspaceProjectResolver()

    projects = resolver.discover_projects(workspace)

    assert projects == [
        "/subscriptions/sub-1/resourcegroups/rg1/providers/"
        "microsoft.cognitiveservices/accounts/acct1/projects/proj1"
    ]


def test_workspace_project_resolver_returns_empty_when_no_endpoint_found(tmp_path):
    resolver = WorkspaceProjectResolver()

    assert resolver.discover_projects(tmp_path) == []


def test_workspace_project_resolver_returns_empty_when_endpoint_shape_is_unrecognized(
    tmp_path,
):
    env_dir = tmp_path / ".agentops"
    env_dir.mkdir(parents=True)
    (env_dir / ".env").write_text(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT=https://example.com/not-a-foundry-endpoint\n",
        encoding="utf-8",
    )
    resolver = WorkspaceProjectResolver()

    assert resolver.discover_projects(tmp_path) == []


def test_workspace_project_resolver_returns_empty_when_account_lookup_fails(
    tmp_path, monkeypatch
):
    env_dir = tmp_path / ".agentops"
    env_dir.mkdir(parents=True)
    (env_dir / ".env").write_text(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="
        "https://acct1.services.ai.azure.com/api/projects/proj1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cockpit_deployment,
        "run_cli",
        _fake_run_cli({("az", "resource", "list"): (1, "", "not found")}),
    )
    resolver = WorkspaceProjectResolver()

    assert resolver.discover_projects(tmp_path) == []
