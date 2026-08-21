"""CLI contract tests for the hosted Cockpit deployment command."""

from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.services import cockpit_deployment


runner = CliRunner()


def test_cockpit_deploy_help_exposes_preview_and_explicit_scope_options() -> None:
    result = runner.invoke(app, ["cockpit", "deploy", "--help"])

    assert result.exit_code == 0
    for option in (
        "--workspace",
        "--scope",
        "--project-id",
        "--scope-resource-id",
        "--subscription",
        "--resource-group",
        "--location",
        "--tenant-id",
        "--client-id",
        "--allowed-group",
        "--name",
        "--preview",
        "--yes",
    ):
        assert option in result.output


def test_nested_cockpit_deploy_explain_is_available() -> None:
    result = runner.invoke(app, ["explain", "cockpit", "deploy", "--no-pager"])

    assert result.exit_code == 0
    assert "Deploy the hosted Cockpit" in result.output
    assert "preview" in result.output.lower()


class _Scope:
    mode = "projects"

    def model_dump_json(self) -> str:
        return '{"version":1,"mode":"projects","project_resource_ids":["/project"]}'


def _plan() -> SimpleNamespace:
    selection = SimpleNamespace(app_name="agentops-test", scope=_Scope())
    preview = SimpleNamespace(
        selection=selection,
        resources=[
            SimpleNamespace(
                change_type="create",
                resource_type="web_app",
                resource_id="/subscriptions/sub/resourcegroups/rg/providers/microsoft.web/sites/app",
            )
        ],
        role_assignments=[
            SimpleNamespace(
                role="Reader",
                scope_resource_id="/project",
                principal_id="principal",
            )
        ],
        federated_credential=SimpleNamespace(
            action="create",
            name="agentops-cockpit",
            subject="principal",
        ),
        warnings=[],
    )
    return SimpleNamespace(
        selection=selection,
        preview=preview,
        scope_warnings=[],
        bundle_dir="bundle",
    )


def _explicit_options() -> list[str]:
    return [
        "--project-id",
        (
            "/subscriptions/00000000-0000-0000-0000-000000000000/"
            "resourceGroups/rg/providers/Microsoft.CognitiveServices/"
            "accounts/foundry/projects/project"
        ),
        "--subscription",
        "00000000-0000-0000-0000-000000000000",
        "--resource-group",
        "rg",
        "--location",
        "eastus",
        "--tenant-id",
        "11111111-1111-1111-1111-111111111111",
        "--client-id",
        "22222222-2222-2222-2222-222222222222",
    ]


def test_cockpit_deploy_preview_never_executes(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def prepare(request):
        captured["request"] = request
        return _plan()

    monkeypatch.setattr(cockpit_deployment, "prepare_deployment", prepare)
    monkeypatch.setattr(
        cockpit_deployment,
        "execute_deployment",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not deploy")),
    )

    result = runner.invoke(
        app,
        ["cockpit", "deploy", "--preview", *_explicit_options()],
    )

    assert result.exit_code == 0
    assert "preview only" in result.output
    assert "web_app" in result.output
    assert captured["request"].scope_mode == "projects"


def test_cockpit_deploy_maps_resource_group_scope_and_confirms(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def prepare(request):
        captured["request"] = request
        return _plan()

    def execute(plan, *, yes, interactive_confirmed):
        captured["confirmation"] = (yes, interactive_confirmed)
        return SimpleNamespace(
            app_url="https://app.azurewebsites.net",
            health="healthy",
            web_app_resource_id="/web-app",
            managed_identity_resource_id="/uami",
            portal_url="https://portal.azure.com/#resource/web-app",
            scope=_Scope(),
            deployed_version="1.2.3",
        )

    monkeypatch.setattr(cockpit_deployment, "prepare_deployment", prepare)
    monkeypatch.setattr(cockpit_deployment, "execute_deployment", execute)
    resource_group_id = (
        "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg"
    )

    result = runner.invoke(
        app,
        [
            "cockpit",
            "deploy",
            "--scope",
            "resource-group",
            "--scope-resource-id",
            resource_group_id,
            "--subscription",
            "00000000-0000-0000-0000-000000000000",
            "--resource-group",
            "rg",
            "--location",
            "eastus",
            "--tenant-id",
            "11111111-1111-1111-1111-111111111111",
            "--client-id",
            "22222222-2222-2222-2222-222222222222",
        ],
        input="y\n",
    )

    assert result.exit_code == 0
    assert captured["request"].scope_mode == "resource_group"
    assert captured["confirmation"] == (False, True)
    assert "healthy" in result.output
    assert "https://app.azurewebsites.net" in result.output


def test_cockpit_deploy_yes_passes_noninteractive_confirmation(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(cockpit_deployment, "prepare_deployment", lambda request: _plan())

    def execute(plan, *, yes, interactive_confirmed):
        captured["confirmation"] = (yes, interactive_confirmed)
        return SimpleNamespace(
            app_url="https://app.azurewebsites.net",
            health="healthy",
            web_app_resource_id="/web-app",
            managed_identity_resource_id="/uami",
            portal_url="https://portal.azure.com/#resource/web-app",
            scope=_Scope(),
            deployed_version="1.2.3",
        )

    monkeypatch.setattr(cockpit_deployment, "execute_deployment", execute)

    result = runner.invoke(
        app,
        ["cockpit", "deploy", "--yes", *_explicit_options()],
    )

    assert result.exit_code == 0
    assert captured["confirmation"] == (True, False)


def test_cockpit_deploy_reports_actionable_errors(monkeypatch) -> None:
    def fail(_request):
        raise cockpit_deployment.CockpitDeploymentError(
            "permission denied",
            stage="preflight",
            remediation="Grant the required role.",
        )

    monkeypatch.setattr(cockpit_deployment, "prepare_deployment", fail)

    result = runner.invoke(
        app,
        ["cockpit", "deploy", "--preview", *_explicit_options()],
    )

    assert result.exit_code == 1
    assert "preflight" in result.output
    assert "Grant the required role." in result.output


def test_cockpit_deploy_returns_exit_one_when_health_verification_fails(monkeypatch) -> None:
    monkeypatch.setattr(cockpit_deployment, "prepare_deployment", lambda request: _plan())

    def fail_verify(*_args, **_kwargs):
        raise cockpit_deployment.DeploymentStageError(
            "Hosted Cockpit health verification failed.",
            stage="verify",
            remediation="Resolve the failed health signals and rerun deployment.",
        )

    monkeypatch.setattr(cockpit_deployment, "execute_deployment", fail_verify)

    result = runner.invoke(app, ["cockpit", "deploy", "--yes", *_explicit_options()])

    assert result.exit_code == 1
    assert "verify" in result.output
    assert "health verification failed" in result.output


def test_cockpit_deploy_rejects_unknown_scope_with_exit_one() -> None:
    result = runner.invoke(
        app,
        ["cockpit", "deploy", "--scope", "tenant", "--client-id", "client"],
    )

    assert result.exit_code == 1
    assert "--scope must be" in result.output
