"""Tests for `agentops workflow generate`."""

from pathlib import Path

import yaml
from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.services.cicd import (
    ALL_KINDS,
    DEFAULT_KINDS,
    DEPLOY_MODES,
    generate_cicd_workflow,
    generate_cicd_workflows,
)


runner = CliRunner()

_WORKFLOW_DIR = ".github/workflows"
_PR_PATH = f"{_WORKFLOW_DIR}/agentops-pr.yml"
_DEV_PATH = f"{_WORKFLOW_DIR}/agentops-deploy-dev.yml"
_QA_PATH = f"{_WORKFLOW_DIR}/agentops-deploy-qa.yml"
_PROD_PATH = f"{_WORKFLOW_DIR}/agentops-deploy-prod.yml"

_DOCTOR_PATH = f"{_WORKFLOW_DIR}/agentops-doctor.yml"

DEFAULT_PATHS = (_PR_PATH, _DEV_PATH, _QA_PATH, _PROD_PATH)
ALL_PATHS = (*DEFAULT_PATHS, _DOCTOR_PATH)


# ---------------------------------------------------------------------------
# generate_cicd_workflows
# ---------------------------------------------------------------------------


def test_default_generates_release_path_templates(tmp_path: Path) -> None:
    result = generate_cicd_workflows(directory=tmp_path)

    assert {p.name for p in result.created_files} == {
        "agentops-pr.yml",
        "agentops-deploy-dev.yml",
        "agentops-deploy-qa.yml",
        "agentops-deploy-prod.yml",
    }
    for rel in DEFAULT_PATHS:
        assert (tmp_path / rel).exists()
    assert not (tmp_path / _DOCTOR_PATH).exists()


def test_kinds_filter_subset(tmp_path: Path) -> None:
    result = generate_cicd_workflows(directory=tmp_path, kinds=["pr", "dev"])

    assert {p.name for p in result.created_files} == {
        "agentops-pr.yml",
        "agentops-deploy-dev.yml",
    }
    assert result.kinds == ["pr", "dev"]
    assert (tmp_path / _PR_PATH).exists()
    assert (tmp_path / _DEV_PATH).exists()
    assert not (tmp_path / _QA_PATH).exists()
    assert not (tmp_path / _PROD_PATH).exists()
    assert not (tmp_path / _DOCTOR_PATH).exists()


def test_legacy_watchdog_kind_aliases_to_doctor(tmp_path: Path) -> None:
    result = generate_cicd_workflows(directory=tmp_path, kinds=["watchdog"])

    assert result.kinds == ["doctor"]
    assert {p.name for p in result.created_files} == {"agentops-doctor.yml"}
    assert (tmp_path / _DOCTOR_PATH).exists()


def test_kinds_unknown_value_is_ignored(tmp_path: Path) -> None:
    result = generate_cicd_workflows(directory=tmp_path, kinds=["pr", "bogus"])
    assert {p.name for p in result.created_files} == {"agentops-pr.yml"}


def test_kinds_dedupes(tmp_path: Path) -> None:
    result = generate_cicd_workflows(directory=tmp_path, kinds=["pr", "pr", "dev"])
    assert len(result.created_files) == 2


def test_skips_existing_without_force(tmp_path: Path) -> None:
    pr = tmp_path / _PR_PATH
    pr.parent.mkdir(parents=True, exist_ok=True)
    pr.write_text("existing", encoding="utf-8")

    result = generate_cicd_workflows(directory=tmp_path, kinds=["pr", "dev"])

    assert len(result.skipped_files) == 1
    assert len(result.created_files) == 1
    assert pr.read_text(encoding="utf-8") == "existing"
    assert (tmp_path / _DEV_PATH).exists()


def test_force_overwrites_all(tmp_path: Path) -> None:
    for rel in DEFAULT_PATHS:
        wf = tmp_path / rel
        wf.parent.mkdir(parents=True, exist_ok=True)
        wf.write_text("old", encoding="utf-8")

    result = generate_cicd_workflows(directory=tmp_path, force=True)

    assert len(result.overwritten_files) == len(DEFAULT_PATHS)
    assert len(result.skipped_files) == 0
    for rel in DEFAULT_PATHS:
        assert (tmp_path / rel).read_text(encoding="utf-8") != "old"


def test_legacy_generate_cicd_workflow_writes_pr_only(tmp_path: Path) -> None:
    result = generate_cicd_workflow(directory=tmp_path)
    assert {p.name for p in result.created_files} == {"agentops-pr.yml"}
    assert (tmp_path / _PR_PATH).exists()
    assert not (tmp_path / _DEV_PATH).exists()


# ---------------------------------------------------------------------------
# Template content checks
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_all_templates_are_valid_yaml(tmp_path: Path) -> None:
    generate_cicd_workflows(directory=tmp_path, kinds=ALL_KINDS)
    for rel in ALL_PATHS:
        data = _read_yaml(tmp_path / rel)
        assert isinstance(data, dict)
        assert "jobs" in data
        # `on:` is parsed as the boolean True by yaml.safe_load when the key
        # is bare; just check the raw text contains the trigger block.
        text = (tmp_path / rel).read_text(encoding="utf-8")
        assert "\non:" in text


def test_all_templates_pass_foundry_and_evaluator_environment(tmp_path: Path) -> None:
    generate_cicd_workflows(directory=tmp_path, kinds=ALL_KINDS)
    expected_env = (
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT: "
        "${{ vars.AZURE_AI_FOUNDRY_PROJECT_ENDPOINT }}",
        "AZURE_OPENAI_ENDPOINT: ${{ vars.AZURE_OPENAI_ENDPOINT }}",
        "AZURE_OPENAI_DEPLOYMENT: ${{ vars.AZURE_OPENAI_DEPLOYMENT }}",
        "APPLICATIONINSIGHTS_CONNECTION_STRING: "
        "${{ secrets.APPLICATIONINSIGHTS_CONNECTION_STRING || "
        "vars.APPLICATIONINSIGHTS_CONNECTION_STRING }}",
    )
    for rel in ALL_PATHS:
        content = (tmp_path / rel).read_text(encoding="utf-8")

        for env_line in expected_env:
            assert env_line in content


def test_doctor_templates_emit_doctor_findings_to_app_insights(tmp_path: Path) -> None:
    generate_cicd_workflows(directory=tmp_path, kinds=["doctor"])

    content = (tmp_path / _DOCTOR_PATH).read_text(encoding="utf-8")
    assert 'agentops-toolkit[foundry,agent]' in content
    assert "--evidence-pack" in content
    assert ".agentops/release/latest/evidence.md" in content
    assert "watchdog" not in content.lower()
    assert (
        "APPLICATIONINSIGHTS_CONNECTION_STRING: "
        "${{ secrets.APPLICATIONINSIGHTS_CONNECTION_STRING || "
        "vars.APPLICATIONINSIGHTS_CONNECTION_STRING }}"
    ) in content
    assert "auto-discovers the Foundry project's Application Insights" in content

    generate_cicd_workflows(
        directory=tmp_path,
        platform="azure-devops",
        kinds=["doctor"],
        force=True,
    )
    ado_content = (tmp_path / _ADO_DOCTOR).read_text(encoding="utf-8")
    assert 'agentops-toolkit[foundry,agent]' in ado_content
    assert "--evidence-pack" in ado_content
    assert "agentops-doctor-release-evidence" in ado_content
    assert "watchdog" not in ado_content.lower()
    assert (
        "APPLICATIONINSIGHTS_CONNECTION_STRING: "
        "$(APPLICATIONINSIGHTS_CONNECTION_STRING)"
    ) in ado_content


def test_azure_devops_templates_pass_app_insights_for_eval_telemetry(tmp_path: Path) -> None:
    generate_cicd_workflows(directory=tmp_path, platform="azure-devops", kinds=ALL_KINDS)

    for rel in _ADO_PATHS:
        content = (tmp_path / rel).read_text(encoding="utf-8")
        assert (
            "APPLICATIONINSIGHTS_CONNECTION_STRING: "
            "$(APPLICATIONINSIGHTS_CONNECTION_STRING)"
        ) in content


def test_pr_template_triggers_and_no_environment(tmp_path: Path) -> None:
    generate_cicd_workflows(directory=tmp_path, kinds=["pr"])
    content = (tmp_path / _PR_PATH).read_text(encoding="utf-8")

    assert "pull_request" in content
    # PR fires for develop, release/**, and main
    assert "develop" in content
    assert "release/**" in content
    assert "main" in content

    assert "agentops eval run" in content
    assert "agentops doctor --workspace ." in content
    assert "--severity-fail none" in content
    assert "--severity-fail critical" not in content
    assert "--evidence-pack" in content
    assert ".agentops/release/latest/evidence.md" in content
    assert "agentops-toolkit" in content
    assert "azure/login@v2" in content
    assert "actions/setup-python@v5" in content
    assert "3.11" in content

    # PR template runs inside the dev environment so the OIDC token subject
    # is `repo:<owner>/<repo>:environment:dev` and `vars.AZURE_*` resolve from
    # the dev environment scope. Without this the gate fails on `azure/login`.
    assert "environment: dev" in content

    # PR comment idempotency marker
    assert "<!-- agentops-pr-report -->" in content


def test_dev_template_triggers_and_environment(tmp_path: Path) -> None:
    generate_cicd_workflows(directory=tmp_path, kinds=["dev"])
    content = (tmp_path / _DEV_PATH).read_text(encoding="utf-8")

    assert "push" in content
    assert "develop" in content
    assert "environment: dev" in content
    assert "agentops eval run" in content
    # Has eval, build, deploy jobs
    assert "needs: eval" in content
    assert "needs: build" in content


def test_auto_deploy_mode_uses_placeholder_without_azure_yaml(tmp_path: Path) -> None:
    result = generate_cicd_workflows(directory=tmp_path, kinds=["dev"])
    content = (tmp_path / _DEV_PATH).read_text(encoding="utf-8")

    assert result.deploy_mode == "placeholder"
    assert "Build (placeholder)" in content
    assert "Deploy (placeholder)" in content


def test_auto_deploy_mode_uses_prompt_agent_for_foundry_prompt_config(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: quickstart-agent:2\ndataset: data.jsonl\n",
        encoding="utf-8",
    )

    result = generate_cicd_workflows(directory=tmp_path, kinds=["dev"])
    content = (tmp_path / _DEV_PATH).read_text(encoding="utf-8")

    assert result.deploy_mode == "prompt-agent"
    assert "agentops:deploy-mode=prompt-agent" in content
    assert "prompt_deploy stage" in content
    assert "agentops.candidate.yaml" in content
    assert "needs: stage-candidate" in content
    assert "needs: eval" in content
    assert "Build (placeholder)" not in content
    assert "Deploy (placeholder)" not in content


def test_prompt_agent_config_uses_official_eval_action_when_supported(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: quickstart-agent:2\ndataset: data.jsonl\n",
        encoding="utf-8",
    )
    (tmp_path / "data.jsonl").write_text(
        '{"input": "Hello", "expected": "Hello!"}\n',
        encoding="utf-8",
    )

    result = generate_cicd_workflows(directory=tmp_path, kinds=["pr"])
    content = (tmp_path / _PR_PATH).read_text(encoding="utf-8")

    assert result.eval_runner == "official-ai-agent-evaluation"
    assert isinstance(_read_yaml(tmp_path / _PR_PATH), dict)
    assert "microsoft/ai-agent-evals@v3-beta" in content
    assert "python -m agentops.pipeline.official_eval prepare" in content
    assert ".agentops/official-eval/input.json" in content
    assert ".agentops/official-eval/result.json" in content
    assert "Record official eval result" in content
    assert "agentops eval run" not in content


def test_official_eval_action_can_point_to_preview_fork(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENTOPS_OFFICIAL_EVAL_ACTION", "placerda/ai-agent-evals@v3-beta")
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: quickstart-agent:2\ndataset: data.jsonl\n",
        encoding="utf-8",
    )
    (tmp_path / "data.jsonl").write_text(
        '{"input": "Hello", "expected": "Hello!"}\n',
        encoding="utf-8",
    )

    generate_cicd_workflows(directory=tmp_path, kinds=["pr"])
    content = (tmp_path / _PR_PATH).read_text(encoding="utf-8")

    assert "uses: placerda/ai-agent-evals@v3-beta" in content
    assert "AGENTOPS_OFFICIAL_EVAL_ACTION: placerda/ai-agent-evals@v3-beta" in content


def test_auto_deploy_mode_uses_azd_when_azure_yaml_exists(tmp_path: Path) -> None:
    (tmp_path / "azure.yaml").write_text("name: sample\n", encoding="utf-8")
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: quickstart-agent:2\ndataset: data.jsonl\n",
        encoding="utf-8",
    )

    result = generate_cicd_workflows(directory=tmp_path, kinds=["dev"])
    content = (tmp_path / _DEV_PATH).read_text(encoding="utf-8")

    assert result.deploy_mode == "azd"
    assert "Azure/setup-azd@v2" in content
    assert content.count("azd env new") == 2
    assert "azd provision --no-prompt" in content
    assert "azd env refresh" in content
    assert "azd deploy --no-prompt" in content
    assert "Build (placeholder)" not in content
    assert "./agentops/deploy.sh" not in content
    assert "__AILZ_PREFLIGHT_COMMAND__" not in content
    assert "Invoke-PreflightChecks.ps1" not in content


def test_azd_prod_template_writes_release_evidence_to_step_summary(tmp_path: Path) -> None:
    (tmp_path / "azure.yaml").write_text("name: sample\n", encoding="utf-8")

    result = generate_cicd_workflows(directory=tmp_path, kinds=["prod"])
    content = (tmp_path / _PROD_PATH).read_text(encoding="utf-8")

    assert result.deploy_mode == "azd"
    assert "GITHUB_STEP_SUMMARY" in content
    assert "cat .agentops/release/latest/evidence.md" in content
    assert "AgentOps Safety Eval (PROD gate)" in content


def test_azd_mode_runs_ailz_preflight_when_script_exists(tmp_path: Path) -> None:
    (tmp_path / "azure.yaml").write_text("name: azure-ai-lz\n", encoding="utf-8")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "Invoke-PreflightChecks.ps1").write_text(
        "Write-Host preflight\n",
        encoding="utf-8",
    )

    result = generate_cicd_workflows(directory=tmp_path, kinds=["dev"])
    content = (tmp_path / _DEV_PATH).read_text(encoding="utf-8")

    assert result.deploy_mode == "azd"
    assert "Running AI Landing Zone preflight" in content
    assert "pwsh ./scripts/Invoke-PreflightChecks.ps1 -Strict" in content
    assert content.index("Invoke-PreflightChecks.ps1") < content.index("azd provision --no-prompt")


def test_azure_devops_azd_mode_runs_ailz_preflight_when_script_exists(tmp_path: Path) -> None:
    (tmp_path / "azure.yaml").write_text("name: azure-ai-lz\n", encoding="utf-8")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "Invoke-PreflightChecks.ps1").write_text(
        "Write-Host preflight\n",
        encoding="utf-8",
    )

    result = generate_cicd_workflows(
        directory=tmp_path,
        platform="azure-devops",
        kinds=["dev"],
    )
    content = (tmp_path / _ADO_DEV).read_text(encoding="utf-8")

    assert result.deploy_mode == "azd"
    assert isinstance(_read_yaml(tmp_path / _ADO_DEV), dict)
    assert "Running AI Landing Zone preflight" in content
    assert "pwsh ./scripts/Invoke-PreflightChecks.ps1 -Strict" in content


def test_force_azd_deploy_mode_without_azure_yaml(tmp_path: Path) -> None:
    result = generate_cicd_workflows(
        directory=tmp_path, kinds=["dev"], deploy_mode="azd"
    )
    content = (tmp_path / _DEV_PATH).read_text(encoding="utf-8")

    assert result.deploy_mode == "azd"
    assert "No azure.yaml found" in content
    assert "azd provision --no-prompt" in content
    assert "azd deploy --no-prompt" in content


def test_force_prompt_agent_deploy_mode_without_agentops_yaml(tmp_path: Path) -> None:
    result = generate_cicd_workflows(
        directory=tmp_path, kinds=["prod"], deploy_mode="prompt-agent"
    )
    content = (tmp_path / _PROD_PATH).read_text(encoding="utf-8")

    assert result.deploy_mode == "prompt-agent"
    assert "AgentOps Deploy (PROD)" in content
    assert "Safety eval candidate (gate)" in content
    assert "prompt_deploy stage" in content
    assert "foundry-agent-prod-deployment" in content
    assert "--evidence-pack" in content


def test_prompt_agent_deploy_templates_are_valid_yaml(tmp_path: Path) -> None:
    generate_cicd_workflows(
        directory=tmp_path,
        kinds=["dev", "qa", "prod"],
        deploy_mode="prompt-agent",
    )
    for rel in (_DEV_PATH, _QA_PATH, _PROD_PATH):
        data = _read_yaml(tmp_path / rel)
        assert isinstance(data, dict)
        assert "jobs" in data

    generate_cicd_workflows(
        directory=tmp_path,
        platform="azure-devops",
        kinds=["dev", "qa", "prod"],
        deploy_mode="prompt-agent",
        force=True,
    )
    for rel in (_ADO_DEV, _ADO_QA, _ADO_PROD):
        data = _read_yaml(tmp_path / rel)
        assert isinstance(data, dict)
        assert "stages" in data


def test_qa_template_triggers_and_environment(tmp_path: Path) -> None:
    generate_cicd_workflows(directory=tmp_path, kinds=["qa"])
    content = (tmp_path / _QA_PATH).read_text(encoding="utf-8")

    assert "push" in content
    assert "release/**" in content
    assert "environment: qa" in content
    assert "agentops eval run" in content
    assert "needs: eval" in content
    assert "needs: build" in content


def test_prod_template_triggers_and_environment_with_reviewer_hint(tmp_path: Path) -> None:
    generate_cicd_workflows(directory=tmp_path, kinds=["prod"])
    content = (tmp_path / _PROD_PATH).read_text(encoding="utf-8")

    assert "push" in content
    assert "main" in content
    assert "environment: production" in content
    assert "agentops eval run" in content
    assert "agentops doctor --workspace ." in content
    assert "--severity-fail critical" in content
    assert "--evidence-pack" in content
    assert ".agentops/release/latest/evidence.json" in content
    # Prod uses safety-eval as the gate name and warns about reviewers
    assert "safety-eval" in content
    assert "Required reviewers" in content or "REQUIRED REVIEWERS" in content


def test_all_kinds_constant_matches_documented_set() -> None:
    assert set(ALL_KINDS) == {"pr", "dev", "qa", "prod", "doctor"}


def test_default_kinds_exclude_scheduled_doctor() -> None:
    assert set(DEFAULT_KINDS) == {"pr", "dev", "qa", "prod"}


def test_deploy_modes_constant_matches_documented_set() -> None:
    assert set(DEPLOY_MODES) == {"auto", "placeholder", "azd", "prompt-agent"}


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_cli_default_creates_release_path_workflows(tmp_path: Path) -> None:
    result = runner.invoke(app, ["workflow", "generate", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.stdout
    assert result.stdout.count("+ created") == 4
    for rel in DEFAULT_PATHS:
        assert (tmp_path / rel).exists()
    assert not (tmp_path / _DOCTOR_PATH).exists()


def test_cli_kinds_subset(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["workflow", "generate", "--dir", str(tmp_path), "--kinds", "pr,prod"],
    )

    assert result.exit_code == 0, result.stdout
    assert (tmp_path / _PR_PATH).exists()
    assert (tmp_path / _PROD_PATH).exists()
    assert not (tmp_path / _DEV_PATH).exists()
    assert not (tmp_path / _QA_PATH).exists()


def test_cli_legacy_watchdog_kind_aliases_to_doctor(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["workflow", "generate", "--dir", str(tmp_path), "--kinds", "watchdog"],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / _DOCTOR_PATH).exists()
    assert "agentops-doctor.yml" in result.output


def test_cli_kinds_invalid_value_fails(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["workflow", "generate", "--dir", str(tmp_path), "--kinds", "pr,nonsense"],
    )

    assert result.exit_code == 1
    assert "unknown" in result.stdout.lower() or "unknown" in (result.stderr or "").lower()


def test_cli_skips_existing_without_force(tmp_path: Path) -> None:
    pr = tmp_path / _PR_PATH
    pr.parent.mkdir(parents=True, exist_ok=True)
    pr.write_text("existing", encoding="utf-8")

    result = runner.invoke(app, ["workflow", "generate", "--dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "skipped" in result.stdout
    assert pr.read_text(encoding="utf-8") == "existing"


def test_cli_force_overwrites(tmp_path: Path) -> None:
    pr = tmp_path / _PR_PATH
    pr.parent.mkdir(parents=True, exist_ok=True)
    pr.write_text("old", encoding="utf-8")

    result = runner.invoke(
        app,
        ["workflow", "generate", "--dir", str(tmp_path), "--force"],
    )

    assert result.exit_code == 0
    assert "overwritten" in result.stdout


def test_cli_next_steps_mention_environments(tmp_path: Path) -> None:
    result = runner.invoke(app, ["workflow", "generate", "--dir", str(tmp_path)])

    assert result.exit_code == 0
    out = result.stdout
    assert "Deploy mode" in out
    assert "placeholder (auto default)" in out
    assert "Next" in out
    assert "dev" in out and "qa" in out and "production" in out
    assert "OIDC" in out or "Workload Identity Federation" in out
    assert "branch" in out.lower()


def test_cli_next_steps_for_pr_only_do_not_request_deploy_envs(
    tmp_path: Path,
) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: travel-agent:1\ndataset: .agentops/data/smoke.jsonl\n",
        encoding="utf-8",
    )
    data_path = tmp_path / ".agentops" / "data" / "smoke.jsonl"
    data_path.parent.mkdir(parents=True)
    data_path.write_text(
        '{"input": "Plan a trip", "expected": "A useful itinerary"}\n',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "workflow",
            "generate",
            "--dir",
            str(tmp_path),
            "--kinds",
            "pr",
        ],
    )

    assert result.exit_code == 0
    out = result.stdout
    assert "Microsoft Foundry AI Agent Evaluation" in out
    assert "used only by deploy workflows" in out
    assert "create GitHub environment: dev" in out
    assert "qa" not in out
    assert "production" not in out
    assert "deploy    not needed yet" in out
    assert "agentops skills install --platform copilot" in out
    assert "/skills" in out and "agentops-workflow" in out
    assert "gh repo create <repo-name>" in out


# ---------------------------------------------------------------------------
# Azure DevOps platform
# ---------------------------------------------------------------------------


_ADO_DIR = ".azuredevops/pipelines"
_ADO_PR = f"{_ADO_DIR}/agentops-pr.yml"
_ADO_DEV = f"{_ADO_DIR}/agentops-deploy-dev.yml"
_ADO_QA = f"{_ADO_DIR}/agentops-deploy-qa.yml"
_ADO_PROD = f"{_ADO_DIR}/agentops-deploy-prod.yml"
_ADO_DOCTOR = f"{_ADO_DIR}/agentops-doctor.yml"
_ADO_DEFAULT_PATHS = (_ADO_PR, _ADO_DEV, _ADO_QA, _ADO_PROD)
_ADO_PATHS = (*_ADO_DEFAULT_PATHS, _ADO_DOCTOR)


def test_azure_devops_platform_writes_pipelines(tmp_path: Path) -> None:
    result = generate_cicd_workflows(directory=tmp_path, platform="azure-devops")

    assert result.platform == "azure-devops"
    for rel in _ADO_DEFAULT_PATHS:
        assert (tmp_path / rel).exists(), f"missing {rel}"
    assert not (tmp_path / _ADO_DOCTOR).exists()
    # GitHub workflows must NOT be created when ADO is selected.
    for rel in ALL_PATHS:
        assert not (tmp_path / rel).exists(), f"unexpected {rel}"


def test_azure_devops_pr_template_uses_ado_idioms(tmp_path: Path) -> None:
    generate_cicd_workflows(directory=tmp_path, platform="azure-devops", kinds=["pr"])
    content = (tmp_path / _ADO_PR).read_text(encoding="utf-8")

    # ADO-specific idioms.
    assert "trigger: none" in content
    assert "pr:" in content
    assert "pool:" in content
    assert "vmImage: ubuntu-latest" in content
    assert "AzureCLI@2" in content
    assert "UsePythonVersion@0" in content
    # Variable group + service connection wiring.
    assert "group: agentops" in content
    assert "AZURE_SERVICE_CONNECTION" in content
    # PR comment marker preserved across platforms.
    assert "<!-- agentops-pr-report -->" in content
    assert "--severity-fail none" in content
    assert "--evidence-pack" in content
    assert "agentops-pr-release-evidence" in content


def test_azure_devops_deploy_templates_use_deployment_job(tmp_path: Path) -> None:
    generate_cicd_workflows(
        directory=tmp_path, platform="azure-devops", kinds=["dev", "qa", "prod"]
    )
    for rel, env in ((_ADO_DEV, "dev"), (_ADO_QA, "qa"), (_ADO_PROD, "production")):
        content = (tmp_path / rel).read_text(encoding="utf-8")
        assert "deployment: agentops_eval_and_deploy" in content
        assert f"TARGET_ENVIRONMENT\n    value: {env}" in content
        assert "agentops eval run" in content
        if env == "production":
            assert "--evidence-pack" in content
            assert "release-evidence" in content


def test_azure_devops_azd_deploy_mode_uses_azd_lifecycle(tmp_path: Path) -> None:
    (tmp_path / "azure.yaml").write_text("name: sample\n", encoding="utf-8")

    result = generate_cicd_workflows(
        directory=tmp_path,
        platform="azure-devops",
        kinds=["dev"],
        deploy_mode="auto",
    )
    content = (tmp_path / _ADO_DEV).read_text(encoding="utf-8")

    assert result.deploy_mode == "azd"
    assert isinstance(_read_yaml(tmp_path / _ADO_DEV), dict)
    assert "curl -fsSL https://aka.ms/install-azd.sh | bash" in content
    assert content.count("azd env new") == 2
    assert "azd provision --no-prompt" in content
    assert "azd env refresh" in content
    assert "azd deploy --no-prompt" in content
    assert "./agentops/deploy.sh" not in content


def test_azure_devops_prompt_agent_deploy_mode_uses_candidate_gate(tmp_path: Path) -> None:
    result = generate_cicd_workflows(
        directory=tmp_path,
        platform="azure-devops",
        kinds=["dev"],
        deploy_mode="prompt-agent",
    )
    content = (tmp_path / _ADO_DEV).read_text(encoding="utf-8")

    assert result.deploy_mode == "prompt-agent"
    assert "agentops:deploy-mode=prompt-agent" in content
    assert "prompt_deploy stage" in content
    assert "agentops.candidate.yaml" in content
    assert "dependsOn: stage_candidate" in content
    assert "foundry-agent-dev-deployment" in content
    assert "./agentops/deploy.sh" not in content


def test_azure_devops_supported_prompt_agent_uses_official_eval_task(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: quickstart-agent:2\ndataset: data.jsonl\n",
        encoding="utf-8",
    )
    (tmp_path / "data.jsonl").write_text(
        '{"input": "Hello", "expected": "Hello!"}\n',
        encoding="utf-8",
    )

    result = generate_cicd_workflows(
        directory=tmp_path,
        platform="azure-devops",
        kinds=["dev"],
    )
    content = (tmp_path / _ADO_DEV).read_text(encoding="utf-8")

    assert result.eval_runner == "official-ai-agent-evaluation"
    assert isinstance(_read_yaml(tmp_path / _ADO_DEV), dict)
    assert "AIAgentEvaluation@2" in content
    assert "python -m agentops.pipeline.official_eval prepare" in content
    assert "Record official eval result" in content
    assert "targetPath: .agentops/official-eval" in content
    assert "agentops eval run" not in content


def test_official_eval_ado_task_can_be_overridden(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENTOPS_OFFICIAL_EVAL_ADO_TASK", "AIAgentEvaluationPreview@2")
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: quickstart-agent:2\ndataset: data.jsonl\n",
        encoding="utf-8",
    )
    (tmp_path / "data.jsonl").write_text(
        '{"input": "Hello", "expected": "Hello!"}\n',
        encoding="utf-8",
    )

    generate_cicd_workflows(
        directory=tmp_path,
        platform="azure-devops",
        kinds=["dev"],
    )
    content = (tmp_path / _ADO_DEV).read_text(encoding="utf-8")

    assert "AIAgentEvaluationPreview@2" in content
    assert "AGENTOPS_OFFICIAL_EVAL_ADO_TASK: AIAgentEvaluationPreview@2" in content


def test_unknown_platform_raises(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError, match="unknown platform"):
        generate_cicd_workflows(directory=tmp_path, platform="circleci")


def test_cli_platform_azure_devops(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "workflow", "generate",
            "--dir", str(tmp_path),
            "--platform", "azure-devops",
            "--kinds", "pr",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert (tmp_path / _ADO_PR).exists()
    assert not (tmp_path / _PR_PATH).exists()
    assert "azure-devops" in result.stdout


def test_cli_platform_invalid_value_fails(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["workflow", "generate", "--dir", str(tmp_path), "--platform", "bitbucket"],
    )

    assert result.exit_code == 1
    # ``result.output`` is the combined stdout+stderr stream. Newer Click
    # releases stopped mixing stderr into ``result.stdout`` by default, so
    # the platform-error message (which is emitted with ``err=True``) lives
    # in ``result.output``. Use that to stay version-tolerant.
    out = result.output.lower()
    assert "unknown" in out and "platform" in out
