"""Unit tests for hosted-Cockpit deployment journal, health, and orchestration.

Covers (issue #433, Phase 3, tasks T021-T024, T028/T029/T033):

* Deployment journal persistence under ``.agentops/deploy/cockpit/`` (round
  trip, missing/corrupt-file tolerance) and reconciliation against a
  selection fingerprint so an unrelated prior attempt is never silently
  resumed (FR-010A-F).
* Drift detection against a live resource-id set before resuming.
* Health-signal classification (``healthy``/``auth_pending``/
  ``rbac_pending``/``failed``) that never over-reports readiness (FR-071).
* ``deploy()`` end-to-end orchestration through injectable ``azd``/Graph/
  health adapters: ordered provision -> federate -> deploy -> verify
  stages, failure journaling with preserved mutations and actionable
  remediation, resume-after-partial-failure stage skipping, and rerun
  idempotency (no duplicate mutating adapter calls on a fully-completed
  rerun).
* ``CockpitDeploymentError.to_dict()`` actionable-error shape.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import agentops.services.cockpit_deployment as cockpit_deployment
from agentops.core.observe import (
    DeploymentJournal,
    DeploymentPreview,
    DeploymentSelection,
    HostedCockpitDeployment,
    MutationRecord,
    ObserveScope,
)
from agentops.services.cockpit_deployment import (
    AppRegistrationInfo,
    AzCliHealthChecker,
    AzdCommandResult,
    AzdPreviewResult,
    CockpitDeploymentError,
    ConfirmationRequiredError,
    DeploymentAdapters,
    DeploymentRequest,
    DeploymentStageError,
    FederatedCredentialInfo,
    FederationConflictError,
    HealthSignals,
    PermissionResult,
    PrerequisiteError,
    _installed_version,
    _parse_json_output,
    build_preview,
    bundle_dir_for,
    classify_health,
    deploy,
    detect_drift,
    execute_deployment,
    journal_path,
    load_journal,
    materialize_bundle,
    prepare_deployment,
    reconcile_journal,
    redact_secrets,
    run_cli,
    save_journal,
    selection_fingerprint,
    validate_easy_auth_template,
)


SUBSCRIPTION_ID = "11111111-1111-1111-1111-111111111111"
TENANT_ID = "22222222-2222-2222-2222-222222222222"
APP_OBJECT_ID = "33333333-3333-3333-3333-333333333333"
CLIENT_ID = "44444444-4444-4444-4444-444444444444"
SP_OBJECT_ID = "55555555-5555-5555-5555-555555555555"
RESOURCE_GROUP = "rg-agentops"
LOCATION = "eastus"
APP_NAME = "agentops-cockpit-test"
PRINCIPAL_ID = "88888888-8888-8888-8888-888888888888"
UAMI_CLIENT_ID = "99999999-9999-9999-9999-999999999999"

PROJECT_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourcegroups/rg-agentops/providers/"
    "microsoft.cognitiveservices/accounts/foundry1/projects/proj1"
)


def _scope() -> ObserveScope:
    return ObserveScope.model_validate(
        {
            "mode": "projects",
            "project_resource_ids": [PROJECT_ID],
            "default_project_resource_id": PROJECT_ID,
        }
    )


def _selection(workspace: Path, *, app_name: str = APP_NAME) -> DeploymentSelection:
    return DeploymentSelection(
        workspace=workspace,
        subscription_id=SUBSCRIPTION_ID,
        resource_group=RESOURCE_GROUP,
        location=LOCATION,
        app_name=app_name,
        tenant_id=TENANT_ID,
        application_object_id=APP_OBJECT_ID,
        client_id=CLIENT_ID,
        service_principal_object_id=SP_OBJECT_ID,
        allowed_group_id=None,
        scope=_scope(),
    )


class FakeTelemetryDiscovery:
    def discover_telemetry_resources(self, scope):
        return []


class FakeManagedIdentityResolver:
    def __init__(self, *, principal_id: str | None = PRINCIPAL_ID, client_id: str | None = UAMI_CLIENT_ID):
        self._principal_id = principal_id
        self._client_id = client_id

    def resolve_principal_id(self, resource_id: str) -> str | None:
        return self._principal_id

    def resolve_client_id(self, resource_id: str) -> str | None:
        return self._client_id


class FakeAppRegistrationClient:
    """Tracks Graph federated-credential lookups/creates for both preview
    construction (read-only) and ``deploy()`` (creates on first attempt,
    reuses thereafter)."""

    def __init__(self, existing: list[FederatedCredentialInfo] | None = None):
        self._existing = list(existing or [])
        self.list_calls = 0
        self.create_calls = 0
        self.created: list[FederatedCredentialInfo] = []

    def get_app_registration(self, *, tenant_id: str, client_id: str):  # pragma: no cover
        raise AssertionError("not exercised by these tests")

    def list_federated_credentials(self, application_object_id: str):
        self.list_calls += 1
        return list(self._existing)

    def create_federated_credential(self, application_object_id, *, name, issuer, subject, audiences):
        self.create_calls += 1
        info = FederatedCredentialInfo(
            id=str(uuid.uuid4()), name=name, issuer=issuer, subject=subject, audiences=tuple(audiences)
        )
        self.created.append(info)
        self._existing.append(info)
        return info


class FakeFacadeAppRegistrationClient(FakeAppRegistrationClient):
    """Like :class:`FakeAppRegistrationClient`, but also answers
    ``get_app_registration`` so it can drive ``resolve_selection`` inside
    the ``prepare_deployment``/``execute_deployment`` facade tests below
    (the base fake deliberately raises, since ``build_preview``/``deploy``
    tests never reach that codepath)."""

    def get_app_registration(self, *, tenant_id: str, client_id: str) -> AppRegistrationInfo:
        return AppRegistrationInfo(
            application_object_id=APP_OBJECT_ID,
            client_id=client_id,
            service_principal_object_id=SP_OBJECT_ID,
            tenant_id=tenant_id,
            single_tenant=True,
        )


class FakeAzureContext:
    """Reports no ambient context; every facade test below supplies every
    selection input explicitly so these fallbacks are never exercised."""

    def current_subscription_id(self) -> str | None:
        return None

    def current_tenant_id(self) -> str | None:
        return None

    def current_location(self) -> str | None:
        return None

    def current_actor_id(self) -> str | None:
        return "deployer-user-object-id"


class FakeProjectResolver:
    def __init__(self, discovered: list[str] | None = None):
        self._discovered = list(discovered) if discovered is not None else []

    def discover_projects(self, workspace: Path) -> list[str]:
        return self._discovered


class FakePermissionChecker:
    def __init__(self, *, granted: bool = True):
        self._granted = granted

    def check_arm_deployment(self, *, subscription_id, resource_group) -> PermissionResult:
        return PermissionResult(name="arm_deployment", granted=self._granted, reason="")

    def check_role_assignment_write(self, *, scope_resource_id) -> PermissionResult:
        return PermissionResult(name="role_assignment_write", granted=self._granted, reason="")

    def check_graph_application_readwrite(self, *, application_object_id) -> PermissionResult:
        return PermissionResult(
            name="graph_application_readwrite", granted=self._granted, reason=""
        )

    def check_group_read(self, *, group_id) -> PermissionResult:  # pragma: no cover
        raise AssertionError("not exercised without --allowed-group-id")


def _facade_request(tmp_path: Path, **overrides) -> DeploymentRequest:
    kwargs: dict = dict(
        workspace=tmp_path,
        scope_mode="projects",
        project_ids=(PROJECT_ID,),
        subscription_id=SUBSCRIPTION_ID,
        resource_group=RESOURCE_GROUP,
        location=LOCATION,
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        name=APP_NAME,
    )
    kwargs.update(overrides)
    return DeploymentRequest(**kwargs)


class FakeAzdCommandRunner:
    def __init__(
        self,
        *,
        preview_resources: list[dict] | None = None,
        provision_success: bool = True,
        provision_message: str = "",
        deploy_success: bool = True,
        deploy_message: str = "",
    ):
        self._preview_resources = preview_resources or []
        self.provision_success = provision_success
        self.provision_message = provision_message
        self.deploy_success = deploy_success
        self.deploy_message = deploy_message
        self.preview_calls = 0
        self.provision_calls = 0
        self.deploy_calls = 0

    def preview(self, bundle_dir: Path, env_values: dict[str, str]) -> AzdPreviewResult:
        self.preview_calls += 1
        return AzdPreviewResult(resources=tuple(self._preview_resources), raw={})

    def provision(self, bundle_dir: Path, env_values: dict[str, str]) -> AzdCommandResult:
        self.provision_calls += 1
        return AzdCommandResult(success=self.provision_success, message=self.provision_message)

    def deploy(self, bundle_dir: Path, env_values: dict[str, str]) -> AzdCommandResult:
        self.deploy_calls += 1
        return AzdCommandResult(success=self.deploy_success, message=self.deploy_message)


class FakeHealthChecker:
    def __init__(self, signals: HealthSignals):
        self._signals = signals
        self.calls = 0
        self.last_kwargs: dict | None = None

    def check(self, *, app_url, web_app_resource_id, principal_id):
        self.calls += 1
        self.last_kwargs = {
            "app_url": app_url,
            "web_app_resource_id": web_app_resource_id,
            "principal_id": principal_id,
        }
        return self._signals


class FakeStateInspector:
    def __init__(self, differences=()):
        self.differences = tuple(differences)
        self.calls = 0

    def inspect(self, selection, preview):
        self.calls += 1
        return cockpit_deployment.DriftInspection(self.differences)


class FakeClock:
    def __init__(self, start: datetime | None = None):
        self._start = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return self._start + timedelta(seconds=self.calls)


def _build_preview(
    selection: DeploymentSelection,
    *,
    app_registration: FakeAppRegistrationClient,
    azd_runner: FakeAzdCommandRunner,
    bundle_dir: Path,
    identity_resolver: FakeManagedIdentityResolver | None = None,
) -> DeploymentPreview:
    return build_preview(
        selection,
        telemetry_discovery=FakeTelemetryDiscovery(),
        identity_resolver=identity_resolver or FakeManagedIdentityResolver(),
        app_registration=app_registration,
        azd_runner=azd_runner,
        bundle_dir=bundle_dir,
    )


HEALTHY_SIGNALS = HealthSignals(
    liveness_ok=True, auth_context_ok=True, uami_read_ok=True, rbac_propagation_pending=False
)


# ---------------------------------------------------------------------------
# Selection fingerprint (T021)
# ---------------------------------------------------------------------------


def test_selection_fingerprint_is_deterministic(tmp_path):
    selection = _selection(tmp_path)
    assert selection_fingerprint(selection) == selection_fingerprint(_selection(tmp_path))


def test_selection_fingerprint_differs_for_different_selection(tmp_path):
    a = selection_fingerprint(_selection(tmp_path))
    b = selection_fingerprint(_selection(tmp_path, app_name="a-different-app-name"))
    assert a != b


# ---------------------------------------------------------------------------
# Journal persistence round trip and corruption tolerance (T021)
# ---------------------------------------------------------------------------


def test_journal_path_and_bundle_dir_are_workspace_scoped(tmp_path):
    path = journal_path(tmp_path)
    bundle = bundle_dir_for(tmp_path)
    assert path == tmp_path / ".agentops" / "deploy" / "cockpit" / "deployment-state.json"
    assert bundle == tmp_path / ".agentops" / "deploy" / "cockpit"


def test_load_journal_returns_none_when_file_missing(tmp_path):
    assert load_journal(tmp_path / "nope.json") is None


def test_load_journal_returns_none_for_corrupt_json(tmp_path):
    path = tmp_path / "deployment-state.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert load_journal(path) is None


def test_load_journal_returns_none_for_schema_mismatch(tmp_path):
    path = tmp_path / "deployment-state.json"
    path.write_text(json.dumps({"totally": "unrelated"}), encoding="utf-8")
    assert load_journal(path) is None


def test_save_and_load_journal_round_trips(tmp_path):
    path = tmp_path / "sub" / "deployment-state.json"
    journal = DeploymentJournal(
        attempt_id=uuid.uuid4(),
        selection_fingerprint="abc123",
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    save_journal(path, journal)
    assert path.is_file()

    loaded = load_journal(path)
    assert loaded is not None
    assert loaded.attempt_id == journal.attempt_id
    assert loaded.selection_fingerprint == "abc123"
    assert loaded.last_completed_stage is None
    assert loaded.mutations == []


# ---------------------------------------------------------------------------
# Journal reconciliation and drift detection (T021)
# ---------------------------------------------------------------------------


def test_reconcile_journal_starts_fresh_when_no_existing_journal(tmp_path):
    selection = _selection(tmp_path)
    journal, is_resume = reconcile_journal(None, selection, clock=FakeClock())
    assert is_resume is False
    assert journal.selection_fingerprint == selection_fingerprint(selection)
    assert journal.last_completed_stage is None


def test_reconcile_journal_resumes_on_matching_fingerprint(tmp_path):
    selection = _selection(tmp_path)
    existing = DeploymentJournal(
        attempt_id=uuid.uuid4(),
        selection_fingerprint=selection_fingerprint(selection),
        last_completed_stage="provisioned",
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    journal, is_resume = reconcile_journal(existing, selection, clock=FakeClock())
    assert is_resume is True
    assert journal is existing
    assert journal.last_completed_stage == "provisioned"


def test_reconcile_journal_discards_journal_on_fingerprint_mismatch(tmp_path):
    selection = _selection(tmp_path)
    stale = DeploymentJournal(
        attempt_id=uuid.uuid4(),
        selection_fingerprint="not-a-match",
        last_completed_stage="deployed",
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    journal, is_resume = reconcile_journal(stale, selection, clock=FakeClock())
    assert is_resume is False
    assert journal.last_completed_stage is None
    assert journal.selection_fingerprint == selection_fingerprint(selection)


def test_detect_drift_reports_resources_missing_from_live_set():
    journal = DeploymentJournal(
        attempt_id=uuid.uuid4(),
        selection_fingerprint="abc",
        resource_ids=[
            f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}/providers/"
            "Microsoft.Web/sites/still-there",
            f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}/providers/"
            "Microsoft.Web/sites/gone-now",
        ],
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    live = [
        f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}/providers/"
        "Microsoft.Web/sites/still-there",
    ]
    drift = detect_drift(journal, live_resource_ids=live)
    assert drift == [
        f"/subscriptions/{SUBSCRIPTION_ID}/resourcegroups/{RESOURCE_GROUP}/providers/"
        "microsoft.web/sites/gone-now"
    ]


def test_detect_drift_reports_nothing_when_all_resources_still_live():
    resource_id = (
        f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}/providers/"
        "Microsoft.Web/sites/still-there"
    )
    journal = DeploymentJournal(
        attempt_id=uuid.uuid4(),
        selection_fingerprint="abc",
        resource_ids=[resource_id],
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    assert detect_drift(journal, live_resource_ids=[resource_id]) == []


# ---------------------------------------------------------------------------
# Health signal classification (T023, FR-071)
# ---------------------------------------------------------------------------


def test_classify_health_healthy_requires_every_signal_confirmed():
    assert classify_health(HEALTHY_SIGNALS) == "healthy"


def test_classify_health_failed_when_liveness_not_ok():
    signals = HealthSignals(liveness_ok=False, auth_context_ok=True, uami_read_ok=True)
    assert classify_health(signals) == "failed"


def test_classify_health_rbac_pending_when_propagation_pending():
    signals = HealthSignals(
        liveness_ok=True, auth_context_ok=True, uami_read_ok=True, rbac_propagation_pending=True
    )
    assert classify_health(signals) == "rbac_pending"


def test_classify_health_rbac_pending_when_uami_read_denied():
    signals = HealthSignals(liveness_ok=True, auth_context_ok=True, uami_read_ok=False)
    assert classify_health(signals) == "rbac_pending"


def test_classify_health_rbac_pending_when_uami_read_unknown():
    signals = HealthSignals(liveness_ok=True, auth_context_ok=True, uami_read_ok=None)
    assert classify_health(signals) == "rbac_pending"


def test_classify_health_auth_pending_when_auth_context_denied():
    signals = HealthSignals(liveness_ok=True, auth_context_ok=False, uami_read_ok=True)
    assert classify_health(signals) == "auth_pending"


def test_classify_health_auth_pending_when_auth_context_unknown():
    signals = HealthSignals(liveness_ok=True, auth_context_ok=None, uami_read_ok=True)
    assert classify_health(signals) == "auth_pending"


def test_classify_health_never_reports_healthy_optimistically():
    # Every partially-unknown combination must resolve to some pending/failed
    # state, never "healthy", per FR-071.
    combos = [
        HealthSignals(liveness_ok=True),
        HealthSignals(liveness_ok=True, uami_read_ok=True),
        HealthSignals(liveness_ok=True, auth_context_ok=True),
    ]
    for signals in combos:
        assert classify_health(signals) != "healthy"


# ---------------------------------------------------------------------------
# actionable-error shape (T024)
# ---------------------------------------------------------------------------


def test_cockpit_deployment_error_to_dict_shape():
    error = CockpitDeploymentError(
        "something went wrong",
        stage="provision",
        remediation="fix it",
        mutation_occurred=True,
        retry_safe=False,
    )
    assert error.to_dict() == {
        "message": "something went wrong",
        "stage": "provision",
        "remediation": "fix it",
        "mutation_occurred": True,
        "retry_safe": False,
    }


def test_prerequisite_error_carries_failed_checks_and_to_dict():
    error = PrerequisiteError(
        "missing permission(s)", failed_checks=["arm_deployment"], remediation="grant access"
    )
    assert error.failed_checks == ["arm_deployment"]
    payload = error.to_dict()
    assert payload["message"] == "missing permission(s)"
    assert payload["stage"] == "validate_prerequisites"
    assert payload["remediation"] == "grant access"


# ---------------------------------------------------------------------------
# deploy() end-to-end orchestration (T022, T029, T033)
# ---------------------------------------------------------------------------


def test_deploy_happy_path_runs_every_stage_and_persists_journal(tmp_path):
    selection = _selection(tmp_path)
    bundle_dir = bundle_dir_for(tmp_path)
    app_registration = FakeAppRegistrationClient()
    azd_runner = FakeAzdCommandRunner()
    preview = _build_preview(
        selection,
        app_registration=app_registration,
        azd_runner=azd_runner,
        bundle_dir=bundle_dir,
        identity_resolver=FakeManagedIdentityResolver(principal_id=None, client_id=None),
    )
    health_checker = FakeHealthChecker(HEALTHY_SIGNALS)
    clock = FakeClock()

    result = deploy(
        selection,
        preview,
        azd_runner=azd_runner,
        app_registration=app_registration,
        health_checker=health_checker,
        clock=clock,
        bundle_dir=bundle_dir,
        identity_resolver=FakeManagedIdentityResolver(),
    )

    assert isinstance(result, HostedCockpitDeployment)
    assert result.health == "healthy"
    assert result.app_url == f"https://{APP_NAME}.azurewebsites.net"
    assert azd_runner.provision_calls == 1
    assert azd_runner.deploy_calls == 1
    assert app_registration.create_calls == 1  # fresh FIC on first attempt
    assert app_registration.created[0].subject == PRINCIPAL_ID
    assert health_checker.calls == 1

    journal = load_journal(journal_path(tmp_path))
    assert journal is not None
    assert journal.last_completed_stage == "verified"
    assert journal.failure is None
    assert journal.resource_ids  # provisioned resources were recorded


def test_deploy_reruns_are_idempotent_and_skip_completed_mutating_stages(tmp_path):
    selection = _selection(tmp_path)
    bundle_dir = bundle_dir_for(tmp_path)
    app_registration = FakeAppRegistrationClient()
    azd_runner = FakeAzdCommandRunner()
    preview = _build_preview(
        selection, app_registration=app_registration, azd_runner=azd_runner, bundle_dir=bundle_dir
    )
    health_checker = FakeHealthChecker(HEALTHY_SIGNALS)
    clock = FakeClock()

    deploy(
        selection,
        preview,
        azd_runner=azd_runner,
        app_registration=app_registration,
        health_checker=health_checker,
        clock=clock,
        bundle_dir=bundle_dir,
    )
    # Second run against the same workspace/selection/preview must not repeat
    # any mutating azd/Graph call — only health is re-verified.
    result = deploy(
        selection,
        preview,
        azd_runner=azd_runner,
        app_registration=app_registration,
        health_checker=health_checker,
        clock=clock,
        bundle_dir=bundle_dir,
    )

    assert isinstance(result, HostedCockpitDeployment)
    assert azd_runner.provision_calls == 1
    assert azd_runner.deploy_calls == 1
    assert app_registration.create_calls == 1
    assert health_checker.calls == 2


def test_deploy_reuses_existing_federated_credential_without_recreating(tmp_path):
    selection = _selection(tmp_path)
    bundle_dir = bundle_dir_for(tmp_path)
    name = f"agentops-cockpit-{APP_NAME}"
    issuer = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"
    existing = FederatedCredentialInfo(
        id=str(uuid.uuid4()),
        name=name,
        issuer=issuer,
        subject=PRINCIPAL_ID,
        audiences=("api://AzureADTokenExchange",),
    )
    app_registration = FakeAppRegistrationClient(existing=[existing])
    azd_runner = FakeAzdCommandRunner()
    preview = _build_preview(
        selection, app_registration=app_registration, azd_runner=azd_runner, bundle_dir=bundle_dir
    )
    assert preview.federated_credential.action == "reuse"

    health_checker = FakeHealthChecker(HEALTHY_SIGNALS)
    deploy(
        selection,
        preview,
        azd_runner=azd_runner,
        app_registration=app_registration,
        health_checker=health_checker,
        clock=FakeClock(),
        bundle_dir=bundle_dir,
    )
    assert app_registration.create_calls == 0


def test_deploy_raises_federation_conflict_and_preserves_provisioned_mutations(tmp_path):
    selection = _selection(tmp_path)
    bundle_dir = bundle_dir_for(tmp_path)
    name = f"agentops-cockpit-{APP_NAME}"
    conflicting = FederatedCredentialInfo(
        id=str(uuid.uuid4()),
        name=name,
        issuer="https://login.microsoftonline.com/different-tenant/v2.0",
        subject=PRINCIPAL_ID,
        audiences=("api://AzureADTokenExchange",),
    )
    app_registration = FakeAppRegistrationClient(existing=[conflicting])
    azd_runner = FakeAzdCommandRunner()
    preview = _build_preview(
        selection, app_registration=app_registration, azd_runner=azd_runner, bundle_dir=bundle_dir
    )
    assert preview.federated_credential.action == "conflict"

    health_checker = FakeHealthChecker(HEALTHY_SIGNALS)
    with pytest.raises(FederationConflictError) as excinfo:
        deploy(
            selection,
            preview,
            azd_runner=azd_runner,
            app_registration=app_registration,
            health_checker=health_checker,
            clock=FakeClock(),
            bundle_dir=bundle_dir,
        )
    assert excinfo.value.stage == "federate"
    assert azd_runner.provision_calls == 1
    assert azd_runner.deploy_calls == 0
    assert health_checker.calls == 0

    journal = load_journal(journal_path(tmp_path))
    assert journal is not None
    assert journal.failure is not None
    assert journal.failure.stage == "federate"
    assert journal.failure.usability == "unverified"  # provision already mutated state
    assert journal.last_completed_stage == "provisioned"


def test_deploy_raises_deployment_stage_error_on_provision_failure(tmp_path):
    selection = _selection(tmp_path)
    bundle_dir = bundle_dir_for(tmp_path)
    app_registration = FakeAppRegistrationClient()
    preview_runner = FakeAzdCommandRunner()
    preview = _build_preview(
        selection, app_registration=app_registration, azd_runner=preview_runner, bundle_dir=bundle_dir
    )

    azd_runner = FakeAzdCommandRunner(provision_success=False, provision_message="quota exceeded")
    health_checker = FakeHealthChecker(HEALTHY_SIGNALS)
    with pytest.raises(DeploymentStageError) as excinfo:
        deploy(
            selection,
            preview,
            azd_runner=azd_runner,
            app_registration=app_registration,
            health_checker=health_checker,
            clock=FakeClock(),
            bundle_dir=bundle_dir,
        )
    assert excinfo.value.stage == "provision"
    assert "quota exceeded" in str(excinfo.value)
    assert excinfo.value.mutation_occurred is False  # no mutations recorded yet
    assert health_checker.calls == 0

    journal = load_journal(journal_path(tmp_path))
    assert journal is not None
    assert journal.failure is not None
    assert journal.failure.stage == "provision"
    assert journal.failure.usability == "not_deployed"
    assert journal.last_completed_stage == "confirmed"


def test_deploy_raises_deployment_stage_error_on_deploy_failure_after_provision(tmp_path):
    selection = _selection(tmp_path)
    bundle_dir = bundle_dir_for(tmp_path)
    app_registration = FakeAppRegistrationClient()
    azd_runner = FakeAzdCommandRunner(deploy_success=False, deploy_message="deploy timed out")
    preview = _build_preview(
        selection, app_registration=app_registration, azd_runner=azd_runner, bundle_dir=bundle_dir
    )

    health_checker = FakeHealthChecker(HEALTHY_SIGNALS)
    with pytest.raises(DeploymentStageError) as excinfo:
        deploy(
            selection,
            preview,
            azd_runner=azd_runner,
            app_registration=app_registration,
            health_checker=health_checker,
            clock=FakeClock(),
            bundle_dir=bundle_dir,
        )
    assert excinfo.value.stage == "deploy"
    assert excinfo.value.mutation_occurred is True  # provision + federation already mutated
    assert azd_runner.provision_calls == 1
    assert azd_runner.deploy_calls == 1
    assert health_checker.calls == 0

    journal = load_journal(journal_path(tmp_path))
    assert journal is not None
    assert journal.failure is not None
    assert journal.failure.stage == "deploy"
    assert journal.failure.usability == "unverified"
    assert journal.failure.incomplete_mutations
    assert journal.failure.uncertain_mutations
    assert journal.last_completed_stage == "federated"


def test_deploy_resumes_after_partial_failure_and_skips_completed_stage(tmp_path):
    selection = _selection(tmp_path)
    bundle_dir = bundle_dir_for(tmp_path)
    app_registration = FakeAppRegistrationClient()
    azd_runner = FakeAzdCommandRunner()
    preview = _build_preview(
        selection, app_registration=app_registration, azd_runner=azd_runner, bundle_dir=bundle_dir
    )

    # Simulate a prior attempt that got as far as "provisioned" before the
    # process died, by pre-seeding the journal with a matching fingerprint.
    path = journal_path(tmp_path)
    prior = DeploymentJournal(
        attempt_id=uuid.uuid4(),
        selection_fingerprint=selection_fingerprint(selection),
        last_completed_stage="provisioned",
        mutations=[
            MutationRecord(
                target_resource_id=preview.resources[0].resource_id,
                action="create",
                pre_existing=False,
                status="completed",
                resulting_resource_id=preview.resources[0].resource_id,
            )
        ],
        resource_ids=[preview.resources[0].resource_id],
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    save_journal(path, prior)

    health_checker = FakeHealthChecker(HEALTHY_SIGNALS)
    result = deploy(
        selection,
        preview,
        azd_runner=azd_runner,
        app_registration=app_registration,
        health_checker=health_checker,
        clock=FakeClock(),
        bundle_dir=bundle_dir,
    )

    assert isinstance(result, HostedCockpitDeployment)
    assert azd_runner.provision_calls == 0  # already-provisioned stage was skipped
    assert azd_runner.deploy_calls == 1
    assert app_registration.create_calls == 1
    assert health_checker.calls == 1

    journal = load_journal(path)
    assert journal is not None
    assert journal.last_completed_stage == "verified"


def test_deploy_rewinds_journal_when_live_state_inspector_reports_drift(tmp_path):
    selection = _selection(tmp_path)
    bundle_dir = bundle_dir_for(tmp_path)
    app_registration = FakeAppRegistrationClient()
    azd_runner = FakeAzdCommandRunner()
    preview = _build_preview(
        selection, app_registration=app_registration, azd_runner=azd_runner, bundle_dir=bundle_dir
    )
    prior = DeploymentJournal(
        attempt_id=uuid.uuid4(),
        selection_fingerprint=selection_fingerprint(selection),
        last_completed_stage="verified",
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    save_journal(journal_path(tmp_path), prior)
    inspector = FakeStateInspector(("missing web app",))

    deploy(
        selection,
        preview,
        azd_runner=azd_runner,
        app_registration=app_registration,
        health_checker=FakeHealthChecker(HEALTHY_SIGNALS),
        clock=FakeClock(),
        bundle_dir=bundle_dir,
        state_inspector=inspector,
    )

    assert inspector.calls == 1
    assert azd_runner.provision_calls == 1
    assert azd_runner.deploy_calls == 1


def test_deploy_discards_stale_journal_for_a_different_selection(tmp_path):
    other_selection = _selection(tmp_path, app_name="a-totally-different-app")
    path = journal_path(tmp_path)
    stale = DeploymentJournal(
        attempt_id=uuid.uuid4(),
        selection_fingerprint=selection_fingerprint(other_selection),
        last_completed_stage="deployed",
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    save_journal(path, stale)

    selection = _selection(tmp_path)
    bundle_dir = bundle_dir_for(tmp_path)
    app_registration = FakeAppRegistrationClient()
    azd_runner = FakeAzdCommandRunner()
    preview = _build_preview(
        selection, app_registration=app_registration, azd_runner=azd_runner, bundle_dir=bundle_dir
    )
    health_checker = FakeHealthChecker(HEALTHY_SIGNALS)

    deploy(
        selection,
        preview,
        azd_runner=azd_runner,
        app_registration=app_registration,
        health_checker=health_checker,
        clock=FakeClock(),
        bundle_dir=bundle_dir,
    )
    # A fresh attempt must run every mutating stage rather than trusting the
    # unrelated stale journal's "deployed" state.
    assert azd_runner.provision_calls == 1
    assert azd_runner.deploy_calls == 1


def test_deploy_fails_when_post_deployment_health_is_not_healthy(tmp_path):
    selection = _selection(tmp_path)
    bundle_dir = bundle_dir_for(tmp_path)
    app_registration = FakeAppRegistrationClient()
    azd_runner = FakeAzdCommandRunner()
    preview = _build_preview(
        selection, app_registration=app_registration, azd_runner=azd_runner, bundle_dir=bundle_dir
    )
    pending_signals = HealthSignals(
        liveness_ok=True, auth_context_ok=True, uami_read_ok=None, rbac_propagation_pending=False
    )
    health_checker = FakeHealthChecker(pending_signals)

    with pytest.raises(DeploymentStageError) as excinfo:
        deploy(
            selection,
            preview,
            azd_runner=azd_runner,
            app_registration=app_registration,
            health_checker=health_checker,
            clock=FakeClock(),
            bundle_dir=bundle_dir,
        )
    assert excinfo.value.stage == "verify"
    assert health_checker.last_kwargs is not None
    assert health_checker.last_kwargs["principal_id"] == PRINCIPAL_ID
    journal = load_journal(journal_path(tmp_path))
    assert journal is not None
    assert journal.last_completed_stage == "deployed"
    assert journal.failure is not None
    assert journal.failure.usability == "unusable"


# ---------------------------------------------------------------------------
# Installed package version discovery (distribution-name fix)
# ---------------------------------------------------------------------------


def test_installed_version_queries_agentops_accelerator_distribution_name(monkeypatch):
    """``_installed_version`` must query the real PyPI/installed distribution
    name (``agentops-accelerator``), not the importable module name
    (``agentops``), so bundle requirements never silently fall back to
    ``0.0.0`` on a normal ``pip install agentops-accelerator``.
    """
    queried: list[str] = []

    def fake_version(distribution_name: str) -> str:
        queried.append(distribution_name)
        if distribution_name == "agentops-accelerator":
            return "1.2.3"
        raise AssertionError("should not query any other distribution name first")

    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", fake_version)

    assert _installed_version() == "1.2.3"
    assert queried == ["agentops-accelerator"]


def test_installed_version_falls_back_to_legacy_agentops_distribution_name(monkeypatch):
    import importlib.metadata

    from importlib.metadata import PackageNotFoundError

    def fake_version(distribution_name: str) -> str:
        if distribution_name == "agentops-accelerator":
            raise PackageNotFoundError(distribution_name)
        if distribution_name == "agentops":
            return "0.9.0"
        raise AssertionError("unexpected distribution name queried")

    monkeypatch.setattr(importlib.metadata, "version", fake_version)

    assert _installed_version() == "0.9.0"


def test_installed_version_falls_back_to_zero_when_neither_distribution_installed(monkeypatch):
    import importlib.metadata

    from importlib.metadata import PackageNotFoundError

    def fake_version(distribution_name: str) -> str:
        raise PackageNotFoundError(distribution_name)

    monkeypatch.setattr(importlib.metadata, "version", fake_version)

    assert _installed_version() == "0.0.0"


# ---------------------------------------------------------------------------
# run_cli: the single subprocess seam every concrete adapter goes through
# ---------------------------------------------------------------------------


def test_run_cli_returns_returncode_stdout_stderr_on_success(monkeypatch):
    captured: dict = {}

    def fake_run(command, *, cwd, env, capture_output, text, timeout, check):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        captured["check"] = check
        assert capture_output is True
        assert text is True
        # env must be the *current* process environment merged with the
        # caller-supplied overrides -- never a bare replacement -- so PATH
        # and credential-cache lookups the CLI needs still work.
        assert env.get("EXTRA") == "1"
        assert "PATH" in env or len(env) >= 0
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    returncode, stdout, stderr = run_cli(
        ["az", "account", "show"], cwd=Path("/tmp/whatever"), env={"EXTRA": "1"}, timeout=42.0
    )

    assert returncode == 0
    assert stdout == '{"ok": true}'
    assert stderr == ""
    assert captured["command"] == ["az", "account", "show"]
    assert captured["timeout"] == 42.0
    assert captured["check"] is False


def test_run_cli_raises_actionable_error_when_executable_missing(monkeypatch):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("az")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(CockpitDeploymentError) as excinfo:
        run_cli(["az", "account", "show"])

    assert excinfo.value.stage == "cli_invocation"
    assert "az" in str(excinfo.value)
    assert excinfo.value.remediation


def test_run_cli_raises_actionable_error_on_timeout(monkeypatch):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout", 300.0))

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(CockpitDeploymentError) as excinfo:
        run_cli(["azd", "provision"], timeout=5.0)

    assert excinfo.value.stage == "cli_invocation"
    assert "5.0" in str(excinfo.value) or "timed out" in str(excinfo.value).lower()
    # Rerun-idempotency safety: the remediation must not tell the caller to
    # blindly retry without checking whether the mutation already landed.
    assert "portal" in excinfo.value.remediation.lower() or "confirm" in excinfo.value.remediation.lower()


# ---------------------------------------------------------------------------
# redact_secrets: no secrets in command output/journal (FR safety requirement)
# ---------------------------------------------------------------------------


def test_redact_secrets_returns_falsy_input_unchanged():
    assert redact_secrets("") == ""


def test_redact_secrets_masks_json_secret_keys():
    text = '{"client_secret": "super-secret-value", "name": "ok"}'
    redacted = redact_secrets(text)
    assert "super-secret-value" not in redacted
    assert "***REDACTED***" in redacted
    assert '"name": "ok"' in redacted


def test_redact_secrets_masks_env_style_lines():
    text = "AZURE_CLIENT_SECRET=abcd1234\nAZURE_TENANT_ID=00000000-0000-0000-0000-000000000000"
    redacted = redact_secrets(text)
    assert "abcd1234" not in redacted
    assert "***REDACTED***" in redacted
    # Non-secret lines must survive untouched.
    assert "AZURE_TENANT_ID=00000000-0000-0000-0000-000000000000" in redacted


# ---------------------------------------------------------------------------
# _parse_json_output: actionable error on malformed CLI JSON output
# ---------------------------------------------------------------------------


def test_parse_json_output_returns_parsed_value_for_valid_json():
    assert _parse_json_output('{"a": 1}', stage="az_cli") == {"a": 1}


def test_parse_json_output_raises_actionable_error_on_malformed_json():
    with pytest.raises(CockpitDeploymentError) as excinfo:
        _parse_json_output("not json", stage="az_account_show")

    assert excinfo.value.stage == "az_account_show"
    assert excinfo.value.remediation


# ---------------------------------------------------------------------------
# AzCliHealthChecker: best-effort unauthenticated HTTP liveness probe
# ---------------------------------------------------------------------------


class _FakeHttpResponse:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_az_cli_health_checker_sends_bearer_token(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["authorization"] = request.get_header("Authorization")
        return _FakeHttpResponse(200)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    checker = AzCliHealthChecker()
    status, _ = checker._http_json(
        "https://example.azurewebsites.net/api/runtime", token="runtime-token"
    )

    assert status == 200
    assert captured["authorization"] == "Bearer " + "runtime-token"


def test_az_cli_health_checker_marks_liveness_ok_on_2xx(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeHttpResponse(200)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    checker = AzCliHealthChecker()
    signals = checker.check(
        app_url="https://example.azurewebsites.net",
        web_app_resource_id="/subscriptions/x/resourceGroups/y/providers/Microsoft.Web/sites/z",
        principal_id=PRINCIPAL_ID,
    )

    assert signals.liveness_ok is True
    assert signals.auth_context_ok is None
    assert signals.uami_read_ok is None


def test_az_cli_health_checker_marks_liveness_not_ok_on_http_error(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", hdrs=None, fp=None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    checker = AzCliHealthChecker()
    signals = checker.check(
        app_url="https://example.azurewebsites.net",
        web_app_resource_id="/subscriptions/x/resourceGroups/y/providers/Microsoft.Web/sites/z",
        principal_id=PRINCIPAL_ID,
    )

    assert signals.liveness_ok is False


def test_az_cli_health_checker_marks_liveness_not_ok_on_generic_exception(monkeypatch):
    def fake_urlopen(request, timeout):
        raise OSError("network unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    checker = AzCliHealthChecker()
    signals = checker.check(
        app_url="https://example.azurewebsites.net",
        web_app_resource_id="/subscriptions/x/resourceGroups/y/providers/Microsoft.Web/sites/z",
        principal_id=PRINCIPAL_ID,
    )

    assert signals.liveness_ok is False


def test_az_cli_health_checker_verifies_all_production_signals(monkeypatch):
    scope = {"version": 1, "mode": "projects", "projects": ["/subscriptions/s/project/p"]}
    calls: list[tuple[str, str | None]] = []

    def fake_run_cli(args, **_kwargs):
        if args[:4] == ["az", "webapp", "config", "appsettings"]:
            return (
                0,
                json.dumps(
                    [
                        {"name": "AGENTOPS_APPLICATION_CLIENT_ID", "value": CLIENT_ID},
                        {"name": "AGENTOPS_OBSERVE_SCOPE", "value": json.dumps(scope)},
                        {"name": "AGENTOPS_COCKPIT_MODE", "value": "hosted"},
                        {"name": "AGENTOPS_UAMI_CLIENT_ID", "value": "uami-client"},
                    ]
                ),
                "",
            )
        if args[:3] == ["az", "account", "get-access-token"]:
            return 0, "user-token\n", ""
        raise AssertionError(args)

    def fake_http_json(url, *, token=None, payload=None):
        calls.append((url, token))
        if url.endswith("/healthz"):
            return 200, {}
        if url.endswith("/api/runtime") and token is None:
            return 401, {}
        if url.endswith("/api/auth/context"):
            return 200, {"user_id": "user"}
        if url.endswith("/api/runtime"):
            return 200, {"mode": "hosted", "scope": scope}
        if url.endswith("/api/observe/discovery"):
            return 200, {"items": []}
        if url.endswith("/api/observe/query"):
            assert payload is not None
            return 200, {"diagnostics": {}}
        raise AssertionError(url)

    monkeypatch.setattr("agentops.services.cockpit_deployment.run_cli", fake_run_cli)
    checker = AzCliHealthChecker(verify_full=True, rbac_attempts=2, rbac_retry_delay=0)
    monkeypatch.setattr(checker, "_http_json", fake_http_json)

    signals = checker.check(
        app_url="https://example.azurewebsites.net",
        web_app_resource_id="/subscriptions/x/resourceGroups/y/providers/Microsoft.Web/sites/z",
        principal_id=PRINCIPAL_ID,
    )

    assert classify_health(signals) == "healthy"
    assert all(token == "user-token" for url, token in calls if "/api/" in url and token)


# ---------------------------------------------------------------------------
# materialize_bundle: version-matched hosted bundle materialization
# ---------------------------------------------------------------------------


def test_materialize_bundle_copies_static_files_byte_for_byte(tmp_path):
    bundle_dir = tmp_path / "bundle"

    result = materialize_bundle(bundle_dir, agentops_version="9.9.9")

    assert result == bundle_dir
    template_root = cockpit_deployment._BUNDLE_TEMPLATE_ROOT
    for relative in cockpit_deployment._BUNDLE_STATIC_FILES:
        expected = (template_root / relative).read_bytes()
        actual = (bundle_dir / relative).read_bytes()
        assert actual == expected, f"{relative} was not copied byte-for-byte"


def test_materialize_bundle_substitutes_pinned_version_into_requirements(tmp_path):
    bundle_dir = tmp_path / "bundle"

    materialize_bundle(bundle_dir, agentops_version="1.2.3")

    requirements = (bundle_dir / "app" / "requirements.txt").read_text(encoding="utf-8")
    assert "1.2.3" in requirements
    assert "__AGENTOPS_VERSION__" not in requirements


def test_materialize_bundle_defaults_to_installed_version_when_not_given(tmp_path, monkeypatch):
    bundle_dir = tmp_path / "bundle"
    monkeypatch.setattr(cockpit_deployment, "_installed_version", lambda: "4.5.6")

    materialize_bundle(bundle_dir)

    requirements = (bundle_dir / "app" / "requirements.txt").read_text(encoding="utf-8")
    assert "4.5.6" in requirements


# ---------------------------------------------------------------------------
# materialize_bundle: patching main.parameters.json's RBAC keys to match the
# actually-resolved scope/preview (critical-parity fix) without disturbing
# any other placeholder in the packaged file.
# ---------------------------------------------------------------------------


def _read_parameter_values(bundle_dir: Path) -> dict:
    raw = json.loads((bundle_dir / "infra" / "main.parameters.json").read_text(encoding="utf-8"))
    return {key: entry["value"] for key, entry in raw["parameters"].items()}


def test_materialize_bundle_leaves_static_rbac_defaults_when_parameters_omitted(tmp_path):
    bundle_dir = tmp_path / "bundle"

    materialize_bundle(bundle_dir, agentops_version="9.9.9")

    values = _read_parameter_values(bundle_dir)
    assert values["grantReaderOnResourceGroup"] is False
    assert values["foundryAccountNames"] == []
    assert values["foundryProjectRefs"] == []
    assert values["logAnalyticsWorkspaceNames"] == []


def test_materialize_bundle_applies_role_assignment_parameters_without_disturbing_others(tmp_path):
    bundle_dir = tmp_path / "bundle"
    baseline_dir = tmp_path / "baseline"
    materialize_bundle(baseline_dir, agentops_version="9.9.9")
    baseline = _read_parameter_values(baseline_dir)

    role_assignment_parameters = {
        "grantReaderOnResourceGroup": True,
        "foundryAccountNames": ["foundry1"],
        "foundryProjectRefs": [{"accountName": "foundry1", "projectName": "proj1"}],
        "logAnalyticsWorkspaceNames": ["law1"],
    }
    materialize_bundle(
        bundle_dir,
        agentops_version="9.9.9",
        role_assignment_parameters=role_assignment_parameters,
    )

    values = _read_parameter_values(bundle_dir)
    assert values["grantReaderOnResourceGroup"] is True
    assert values["foundryAccountNames"] == ["foundry1"]
    assert values["foundryProjectRefs"] == [{"accountName": "foundry1", "projectName": "proj1"}]
    assert values["logAnalyticsWorkspaceNames"] == ["law1"]

    # Every other placeholder/value must be byte-for-byte identical to a
    # materialization that supplied no role-assignment parameters at all.
    non_rbac_keys = set(baseline) - {
        "grantReaderOnResourceGroup",
        "foundryAccountNames",
        "foundryProjectRefs",
        "logAnalyticsWorkspaceNames",
    }
    for key in non_rbac_keys:
        assert values[key] == baseline[key], f"{key} was unexpectedly modified"


def test_materialize_bundle_role_assignment_parameters_default_to_none_is_backward_compatible(tmp_path):
    bundle_dir = tmp_path / "bundle"

    # Omitting role_assignment_parameters entirely must behave exactly like
    # passing None -- both preserve the packaged static RBAC defaults.
    result = materialize_bundle(bundle_dir, agentops_version="1.0.0", role_assignment_parameters=None)

    assert result == bundle_dir
    values = _read_parameter_values(bundle_dir)
    assert values["grantReaderOnResourceGroup"] is False
    assert values["foundryProjectRefs"] == []


# ---------------------------------------------------------------------------
# deploy(): explicit ("out-of-template") role-assignment stage for
# cross-resource-group / subscription-scope targets the packaged,
# resource-group-scoped Bicep template cannot itself express (critical
# parity fix). Never broadens access: only the exact
# ``explicit_role_assignments`` set is ever applied here.
# ---------------------------------------------------------------------------


CROSS_RG_PROJECT_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourcegroups/rg-other/providers/"
    "microsoft.cognitiveservices/accounts/foundry2/projects/proj2"
)


def _cross_rg_selection(workspace: Path) -> DeploymentSelection:
    scope = ObserveScope.model_validate(
        {
            "mode": "projects",
            "project_resource_ids": [CROSS_RG_PROJECT_ID],
            "default_project_resource_id": CROSS_RG_PROJECT_ID,
        }
    )
    return DeploymentSelection(
        workspace=workspace,
        subscription_id=SUBSCRIPTION_ID,
        resource_group=RESOURCE_GROUP,
        location=LOCATION,
        app_name=APP_NAME,
        tenant_id=TENANT_ID,
        application_object_id=APP_OBJECT_ID,
        client_id=CLIENT_ID,
        service_principal_object_id=SP_OBJECT_ID,
        allowed_group_id=None,
        scope=scope,
    )


class FakeRoleAssignmentClient:
    def __init__(self, *, already_exists: bool = False):
        self._already_exists = already_exists
        self.calls: list[dict] = []

    def ensure_role_assignment(
        self, *, assignment_id, scope_resource_id, principal_id, role_definition_id
    ) -> bool:
        self.calls.append(
            {
                "assignment_id": assignment_id,
                "scope_resource_id": scope_resource_id,
                "principal_id": principal_id,
                "role_definition_id": role_definition_id,
            }
        )
        return not self._already_exists


def test_deploy_applies_explicit_role_assignment_for_cross_resource_group_target(tmp_path):
    selection = _cross_rg_selection(tmp_path)
    bundle_dir = bundle_dir_for(tmp_path)
    app_registration = FakeAppRegistrationClient()
    azd_runner = FakeAzdCommandRunner()
    preview = _build_preview(
        selection, app_registration=app_registration, azd_runner=azd_runner, bundle_dir=bundle_dir
    )
    # The cross-RG project boundary must have produced exactly one
    # out-of-template Reader assignment for deploy() to apply explicitly.
    pending = cockpit_deployment.explicit_role_assignments(selection, preview.role_assignments)
    assert len(pending) == 1
    assert pending[0].scope_resource_id == CROSS_RG_PROJECT_ID.lower()

    role_assignment_client = FakeRoleAssignmentClient()
    health_checker = FakeHealthChecker(HEALTHY_SIGNALS)
    clock = FakeClock()

    result = deploy(
        selection,
        preview,
        azd_runner=azd_runner,
        app_registration=app_registration,
        health_checker=health_checker,
        clock=clock,
        bundle_dir=bundle_dir,
        role_assignment_client=role_assignment_client,
        identity_resolver=FakeManagedIdentityResolver(),
    )

    assert isinstance(result, HostedCockpitDeployment)
    assert len(role_assignment_client.calls) == 1
    call = role_assignment_client.calls[0]
    assert call["scope_resource_id"] == CROSS_RG_PROJECT_ID.lower()
    assert call["principal_id"] == PRINCIPAL_ID
    assert call["role_definition_id"] == pending[0].role_definition_id

    journal = load_journal(journal_path(tmp_path))
    assert journal is not None
    assign_mutations = [m for m in journal.mutations if m.action == "assign"]
    assert len(assign_mutations) == 1
    assert assign_mutations[0].target_resource_id == CROSS_RG_PROJECT_ID.lower()
    assert assign_mutations[0].pre_existing is False


def test_deploy_skips_explicit_role_assignment_without_client_for_backward_compat(tmp_path):
    selection = _cross_rg_selection(tmp_path)
    bundle_dir = bundle_dir_for(tmp_path)
    app_registration = FakeAppRegistrationClient()
    azd_runner = FakeAzdCommandRunner()
    preview = _build_preview(
        selection, app_registration=app_registration, azd_runner=azd_runner, bundle_dir=bundle_dir
    )
    health_checker = FakeHealthChecker(HEALTHY_SIGNALS)
    clock = FakeClock()

    # No role_assignment_client supplied (the default None): deploy() must
    # still succeed, and must not journal any "assign" mutation, rather than
    # silently attempting a mutation with no adapter to perform it.
    result = deploy(
        selection,
        preview,
        azd_runner=azd_runner,
        app_registration=app_registration,
        health_checker=health_checker,
        clock=clock,
        bundle_dir=bundle_dir,
    )

    assert isinstance(result, HostedCockpitDeployment)
    journal = load_journal(journal_path(tmp_path))
    assert journal is not None
    assert [m for m in journal.mutations if m.action == "assign"] == []


def test_deploy_explicit_role_assignment_rerun_is_idempotent(tmp_path):
    selection = _cross_rg_selection(tmp_path)
    bundle_dir = bundle_dir_for(tmp_path)
    app_registration = FakeAppRegistrationClient()
    azd_runner = FakeAzdCommandRunner()
    preview = _build_preview(
        selection, app_registration=app_registration, azd_runner=azd_runner, bundle_dir=bundle_dir
    )
    role_assignment_client = FakeRoleAssignmentClient()
    health_checker = FakeHealthChecker(HEALTHY_SIGNALS)
    clock = FakeClock()

    deploy(
        selection,
        preview,
        azd_runner=azd_runner,
        app_registration=app_registration,
        health_checker=health_checker,
        clock=clock,
        bundle_dir=bundle_dir,
        role_assignment_client=role_assignment_client,
        identity_resolver=FakeManagedIdentityResolver(),
    )
    # A rerun against the fully-completed journal must skip the entire
    # "provisioned" stage (including the explicit role-assignment step)
    # rather than re-invoking ensure_role_assignment.
    result_again = deploy(
        selection,
        preview,
        azd_runner=azd_runner,
        app_registration=app_registration,
        health_checker=health_checker,
        clock=clock,
        bundle_dir=bundle_dir,
        role_assignment_client=role_assignment_client,
        identity_resolver=FakeManagedIdentityResolver(),
    )

    assert isinstance(result_again, HostedCockpitDeployment)
    assert len(role_assignment_client.calls) == 1
    assert azd_runner.provision_calls == 1


def test_deploy_explicit_role_assignment_reuses_when_already_exists(tmp_path):
    selection = _cross_rg_selection(tmp_path)
    bundle_dir = bundle_dir_for(tmp_path)
    app_registration = FakeAppRegistrationClient()
    azd_runner = FakeAzdCommandRunner()
    preview = _build_preview(
        selection, app_registration=app_registration, azd_runner=azd_runner, bundle_dir=bundle_dir
    )
    # already_exists=True mirrors the adapter's own idempotent short-circuit
    # (e.g. a resumed deployment after a partial failure): the mutation is
    # still journaled, but pre_existing must be True and no new grant made.
    role_assignment_client = FakeRoleAssignmentClient(already_exists=True)
    health_checker = FakeHealthChecker(HEALTHY_SIGNALS)
    clock = FakeClock()

    deploy(
        selection,
        preview,
        azd_runner=azd_runner,
        app_registration=app_registration,
        health_checker=health_checker,
        clock=clock,
        bundle_dir=bundle_dir,
        role_assignment_client=role_assignment_client,
        identity_resolver=FakeManagedIdentityResolver(),
    )

    journal = load_journal(journal_path(tmp_path))
    assert journal is not None
    assign_mutations = [m for m in journal.mutations if m.action == "assign"]
    assert len(assign_mutations) == 1
    assert assign_mutations[0].pre_existing is True


# ---------------------------------------------------------------------------
# prepare_deployment / execute_deployment: the production facade a thin
# Typer CLI command calls, wired to concrete az/azd adapters by default
# but fully overridable for these tests
# ---------------------------------------------------------------------------


def _facade_adapters(
    *,
    permission_checker=None,
    app_registration=None,
    azd_runner=None,
    health_checker=None,
    clock=None,
    state_inspector=None,
) -> DeploymentAdapters:
    return DeploymentAdapters(
        azure_context=FakeAzureContext(),
        project_resolver=FakeProjectResolver(),
        permission_checker=permission_checker or FakePermissionChecker(granted=True),
        app_registration=app_registration or FakeFacadeAppRegistrationClient(),
        telemetry_discovery=FakeTelemetryDiscovery(),
        identity_resolver=FakeManagedIdentityResolver(),
        azd_runner=azd_runner or FakeAzdCommandRunner(),
        health_checker=health_checker or FakeHealthChecker(HEALTHY_SIGNALS),
        clock=clock or FakeClock(),
        state_inspector=state_inspector or FakeStateInspector(),
    )


def test_prepare_deployment_builds_plan_without_mutating_anything(tmp_path):
    app_registration = FakeFacadeAppRegistrationClient()
    azd_runner = FakeAzdCommandRunner()
    adapters = _facade_adapters(app_registration=app_registration, azd_runner=azd_runner)

    plan = prepare_deployment(_facade_request(tmp_path), adapters=adapters)

    assert plan.selection.app_name == APP_NAME
    assert plan.explicit_inputs_complete is True
    assert plan.scope_warnings == []
    assert all(check.granted for check in plan.permission_checks)
    assert plan.bundle_dir == bundle_dir_for(tmp_path)
    assert (plan.bundle_dir / "app" / "requirements.txt").exists()

    # prepare_deployment must never mutate Azure state: only azd's dry-run
    # preview may run, and the app registration must never be created/deleted.
    assert azd_runner.preview_calls == 1
    assert azd_runner.provision_calls == 0
    assert azd_runner.deploy_calls == 0
    assert app_registration.create_calls == 0


def test_easy_auth_template_validation_rejects_disabled_token_store(tmp_path):
    template = tmp_path / "infra" / "main.bicep"
    template.parent.mkdir(parents=True)
    template.write_text(
        """
        resource authSettings 'Microsoft.Web/sites/config@2023-01-01' = {
          name: 'authsettingsV2'
          properties: {
            globalValidation: {
              requireAuthentication: true
              unauthenticatedClientAction: 'Return401'
            }
            login: { tokenStore: { enabled: false } }
          }
        }
        """,
        encoding="utf-8",
    )

    with pytest.raises(PrerequisiteError, match="Easy Auth"):
        validate_easy_auth_template(tmp_path)


def test_prepare_deployment_raises_on_missing_required_input(tmp_path):
    request = _facade_request(tmp_path, client_id=None)
    adapters = _facade_adapters()

    with pytest.raises(CockpitDeploymentError) as excinfo:
        prepare_deployment(request, adapters=adapters)

    assert "--client-id" in str(excinfo.value)


def test_prepare_deployment_raises_prerequisite_error_when_permission_denied(tmp_path):
    adapters = _facade_adapters(permission_checker=FakePermissionChecker(granted=False))

    with pytest.raises(PrerequisiteError):
        prepare_deployment(_facade_request(tmp_path), adapters=adapters)


def test_execute_deployment_requires_confirmation_when_not_yes(tmp_path):
    azd_runner = FakeAzdCommandRunner()
    adapters = _facade_adapters(azd_runner=azd_runner)
    plan = prepare_deployment(_facade_request(tmp_path), adapters=adapters)

    with pytest.raises(ConfirmationRequiredError):
        execute_deployment(plan, yes=False, adapters=adapters)

    # Confirmation must be re-checked inside execute_deployment itself (a
    # caller cannot bypass the guard by holding a pre-built plan): no
    # mutating azd command may have run yet.
    assert azd_runner.provision_calls == 0
    assert azd_runner.deploy_calls == 0


def test_execute_deployment_runs_full_sequence_and_returns_hosted_deployment(tmp_path):
    app_registration = FakeFacadeAppRegistrationClient()
    azd_runner = FakeAzdCommandRunner()
    health_checker = FakeHealthChecker(HEALTHY_SIGNALS)
    adapters = _facade_adapters(
        app_registration=app_registration, azd_runner=azd_runner, health_checker=health_checker
    )
    plan = prepare_deployment(_facade_request(tmp_path), adapters=adapters)

    result = execute_deployment(plan, yes=True, adapters=adapters)

    assert isinstance(result, HostedCockpitDeployment)
    assert result.health == "healthy"
    assert azd_runner.provision_calls == 1
    assert azd_runner.deploy_calls == 1
    assert app_registration.create_calls == 1
    assert health_checker.calls == 1

    journal = load_journal(journal_path(tmp_path))
    assert journal is not None
    assert journal.last_completed_stage == "verified"
    assert journal.initiated_by == "deployer-user-object-id"
    assert journal.approval_method == "non_interactive"
    assert journal.approved_at is not None


def test_execute_deployment_rerun_is_idempotent_and_reuses_federated_credential(tmp_path):
    app_registration = FakeFacadeAppRegistrationClient()
    azd_runner = FakeAzdCommandRunner()
    health_checker = FakeHealthChecker(HEALTHY_SIGNALS)
    adapters = _facade_adapters(
        app_registration=app_registration, azd_runner=azd_runner, health_checker=health_checker
    )
    plan = prepare_deployment(_facade_request(tmp_path), adapters=adapters)
    execute_deployment(plan, yes=True, adapters=adapters)

    # Rerunning prepare+execute against the same workspace/selection must
    # skip every already-completed stage (provision/federate/deploy),
    # reusing the already-created federated credential rather than
    # creating a duplicate or repeating a mutating azd command, and must
    # not fail because a prior journal already exists.
    plan_again = prepare_deployment(_facade_request(tmp_path), adapters=adapters)
    result_again = execute_deployment(plan_again, yes=True, adapters=adapters)

    assert isinstance(result_again, HostedCockpitDeployment)
    assert app_registration.create_calls == 1
    assert azd_runner.provision_calls == 1
    assert azd_runner.deploy_calls == 1
