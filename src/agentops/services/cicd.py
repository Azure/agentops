"""CI/CD workflow generation service for `agentops workflow generate`."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

from agentops.pipeline.official_eval import (
    AGENTOPS_LOCAL_RUNNER,
    OFFICIAL_EVAL_ACTION_ENV,
    OFFICIAL_EVAL_ADO_TASK_ENV,
    OFFICIAL_EVAL_RUNNER,
    official_eval_action_ref,
    official_eval_ado_task_ref,
)
from agentops.services.workflow_analysis import (
    has_ailz_preflight,
    recommended_deploy_mode,
    recommended_eval_runner,
)


_TEMPLATE_PACKAGE = "agentops.templates"

# CI/CD platforms supported by ``agentops workflow generate``.
PLATFORMS: Tuple[str, ...] = ("github", "azure-devops")

# Deployment template modes. ``placeholder`` keeps the stack-agnostic
# scaffold; ``azd`` delegates infrastructure and app deployment to Azure
# Developer CLI; ``prompt-agent`` creates/evaluates a candidate Foundry prompt
# agent version from a source-controlled prompt file. ``auto`` selects
# ``azd`` when the target repo has ``azure.yaml`` and ``prompt-agent`` when
# ``agentops.yaml`` targets a Foundry prompt agent.
DEPLOY_MODES: Tuple[str, ...] = ("auto", "placeholder", "azd", "prompt-agent")

# Per-platform mapping of workflow kind -> (template path inside package,
# output path in repo).
#
# The five templates form a complete GenAIOps GitFlow scaffold:
#
#   pr   -> agentops-pr            (PR gate; PRs to develop, release/**, main)
#   dev  -> agentops-deploy-dev    (push to develop -> environment: dev)
#   qa   -> agentops-deploy-qa     (push to release/** -> environment: qa)
#   prod -> agentops-deploy-prod   (push to main -> environment: production)
#   watchdog -> agentops-watchdog  (scheduled Doctor + eval health check)
_TEMPLATES_BY_PLATFORM: Dict[str, Dict[str, Tuple[str, str]]] = {
    "github": {
        "pr": ("workflows/agentops-pr.yml", ".github/workflows/agentops-pr.yml"),
        "dev": ("workflows/agentops-deploy-dev.yml", ".github/workflows/agentops-deploy-dev.yml"),
        "qa": ("workflows/agentops-deploy-qa.yml", ".github/workflows/agentops-deploy-qa.yml"),
        "prod": ("workflows/agentops-deploy-prod.yml", ".github/workflows/agentops-deploy-prod.yml"),
        "watchdog": ("workflows/agentops-watchdog.yml", ".github/workflows/agentops-watchdog.yml"),
    },
    "azure-devops": {
        "pr": (
            "pipelines/azuredevops/agentops-pr.yml",
            ".azuredevops/pipelines/agentops-pr.yml",
        ),
        "dev": (
            "pipelines/azuredevops/agentops-deploy-dev.yml",
            ".azuredevops/pipelines/agentops-deploy-dev.yml",
        ),
        "qa": (
            "pipelines/azuredevops/agentops-deploy-qa.yml",
            ".azuredevops/pipelines/agentops-deploy-qa.yml",
        ),
        "prod": (
            "pipelines/azuredevops/agentops-deploy-prod.yml",
            ".azuredevops/pipelines/agentops-deploy-prod.yml",
        ),
        "watchdog": (
            "pipelines/azuredevops/agentops-watchdog.yml",
            ".azuredevops/pipelines/agentops-watchdog.yml",
        ),
    },
}

_AZD_TEMPLATES_BY_PLATFORM: Dict[str, Dict[str, Tuple[str, str]]] = {
    "github": {
        "dev": ("workflows/agentops-deploy-dev-azd.yml", ".github/workflows/agentops-deploy-dev.yml"),
        "qa": ("workflows/agentops-deploy-qa-azd.yml", ".github/workflows/agentops-deploy-qa.yml"),
        "prod": ("workflows/agentops-deploy-prod-azd.yml", ".github/workflows/agentops-deploy-prod.yml"),
    },
    "azure-devops": {
        "dev": (
            "pipelines/azuredevops/agentops-deploy-dev-azd.yml",
            ".azuredevops/pipelines/agentops-deploy-dev.yml",
        ),
        "qa": (
            "pipelines/azuredevops/agentops-deploy-qa-azd.yml",
            ".azuredevops/pipelines/agentops-deploy-qa.yml",
        ),
        "prod": (
            "pipelines/azuredevops/agentops-deploy-prod-azd.yml",
            ".azuredevops/pipelines/agentops-deploy-prod.yml",
        ),
    },
}

ALL_KINDS: tuple[str, ...] = ("pr", "dev", "qa", "prod", "watchdog")


@dataclass
class CicdResult:
    """Result of generating CI/CD workflow files."""

    platform: str = "github"
    deploy_mode: str = "placeholder"
    eval_runner: str = AGENTOPS_LOCAL_RUNNER
    created_files: List[Path] = field(default_factory=list)
    overwritten_files: List[Path] = field(default_factory=list)
    skipped_files: List[Path] = field(default_factory=list)


def _write_template(
    templates_root,
    template_path: str,
    output_path: Path,
    force: bool,
    result: CicdResult,
    substitutions: Mapping[str, str] | None = None,
) -> None:
    template_resource = templates_root.joinpath(template_path)
    template_content = template_resource.read_text(encoding="utf-8")
    for key, value in (substitutions or {}).items():
        template_content = template_content.replace(key, value)

    existed_before = output_path.exists()

    if existed_before and not force:
        result.skipped_files.append(output_path)
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(template_content, encoding="utf-8")

    if existed_before:
        result.overwritten_files.append(output_path)
    else:
        result.created_files.append(output_path)


def _branch_block_github(*branches: str) -> str:
    return "".join(f"      - {branch}\n" for branch in branches).rstrip()


def _branch_block_ado(*branches: str) -> str:
    return "".join(f"      - {branch}\n" for branch in branches).rstrip()


_PROMPT_AGENT_VALUES: Dict[str, Dict[str, str]] = {
    "dev": {
        "__ENV_LABEL__": "DEV",
        "__ENV_KEY__": "dev",
        "__ENV_NAME__": "dev",
        "__BRANCHES__": _branch_block_github("develop"),
        "__EVAL_JOB_NAME__": "Eval candidate (gate)",
    },
    "qa": {
        "__ENV_LABEL__": "QA",
        "__ENV_KEY__": "qa",
        "__ENV_NAME__": "qa",
        "__BRANCHES__": _branch_block_github('"release/**"'),
        "__EVAL_JOB_NAME__": "Eval candidate (gate)",
    },
    "prod": {
        "__ENV_LABEL__": "PROD",
        "__ENV_KEY__": "prod",
        "__ENV_NAME__": "production",
        "__BRANCHES__": _branch_block_github("main"),
        "__EVAL_JOB_NAME__": "Safety eval candidate (gate)",
    },
}

_PROMPT_AGENT_VALUES_ADO: Dict[str, Dict[str, str]] = {
    "dev": {
        "__ENV_LABEL__": "dev",
        "__ENV_KEY__": "dev",
        "__ENV_NAME__": "dev",
        "__BRANCHES__": _branch_block_ado("develop"),
    },
    "qa": {
        "__ENV_LABEL__": "qa",
        "__ENV_KEY__": "qa",
        "__ENV_NAME__": "qa",
        "__BRANCHES__": _branch_block_ado("release/*"),
    },
    "prod": {
        "__ENV_LABEL__": "production",
        "__ENV_KEY__": "prod",
        "__ENV_NAME__": "production",
        "__BRANCHES__": _branch_block_ado("main"),
    },
}

_PROMPT_AGENT_TEMPLATES_BY_PLATFORM: Dict[str, Dict[str, Tuple[str, str]]] = {
    "github": {
        "dev": ("workflows/agentops-deploy-prompt-agent.yml", ".github/workflows/agentops-deploy-dev.yml"),
        "qa": ("workflows/agentops-deploy-prompt-agent.yml", ".github/workflows/agentops-deploy-qa.yml"),
        "prod": ("workflows/agentops-deploy-prompt-agent.yml", ".github/workflows/agentops-deploy-prod.yml"),
    },
    "azure-devops": {
        "dev": (
            "pipelines/azuredevops/agentops-deploy-prompt-agent.yml",
            ".azuredevops/pipelines/agentops-deploy-dev.yml",
        ),
        "qa": (
            "pipelines/azuredevops/agentops-deploy-prompt-agent.yml",
            ".azuredevops/pipelines/agentops-deploy-qa.yml",
        ),
        "prod": (
            "pipelines/azuredevops/agentops-deploy-prompt-agent.yml",
            ".azuredevops/pipelines/agentops-deploy-prod.yml",
        ),
    },
}


def _eval_substitutions(
    platform: str,
    eval_runner: str,
    config_path: str,
    *,
    ado_indent: int = 10,
) -> Mapping[str, str]:
    if platform == "azure-devops":
        return _ado_eval_substitutions(eval_runner, config_path, base_indent=ado_indent)
    return _github_eval_substitutions(eval_runner, config_path)


def _github_eval_substitutions(eval_runner: str, config_path: str) -> Mapping[str, str]:
    if eval_runner == OFFICIAL_EVAL_RUNNER:
        official_action = official_eval_action_ref()
        return {
            "__EVAL_STEPS__": f"""      - name: Prepare official AI Agent Evaluation input
        id: official_eval_input
        env:
          AZURE_OPENAI_DEPLOYMENT: ${{{{ vars.AZURE_OPENAI_DEPLOYMENT }}}}
          {OFFICIAL_EVAL_ACTION_ENV}: {official_action}
        run: |
          python -m agentops.pipeline.official_eval prepare \\
            --config \"{config_path}\" \\
            --out \".agentops/official-eval/input.json\" \\
            --github-output \"$GITHUB_OUTPUT\"

      # Official runner for Foundry prompt agents. AgentOps keeps the prepared
      # input/metadata as release evidence until the action exposes a stable
      # machine-readable threshold artifact.
      - name: Run official AI Agent Evaluation
        id: official_eval
        uses: {official_action}
        with:
          azure-ai-project-endpoint: ${{{{ vars.AZURE_AI_FOUNDRY_PROJECT_ENDPOINT }}}}
          deployment-name: ${{{{ steps.official_eval_input.outputs.deployment_name }}}}
          agent-ids: ${{{{ steps.official_eval_input.outputs.agent_ids }}}}
          data-path: ${{{{ steps.official_eval_input.outputs.data_path }}}}

      - name: Record official eval result
        if: always()
        id: eval
        run: |
          mkdir -p .agentops/official-eval
          outcome=\"${{{{ steps.official_eval.outcome }}}}\"
          conclusion=\"${{{{ steps.official_eval.conclusion }}}}\"
          if [ \"$outcome\" = \"success\" ]; then
            echo \"exit_code=0\" >> \"$GITHUB_OUTPUT\"
            echo \"result=official-ai-agent-evaluation\" >> \"$GITHUB_OUTPUT\"
          else
            echo \"exit_code=1\" >> \"$GITHUB_OUTPUT\"
            echo \"result=official-ai-agent-evaluation-failed\" >> \"$GITHUB_OUTPUT\"
          fi
          python - <<'PY'
          import json
          from datetime import datetime, timezone
          from pathlib import Path

          output = Path('.agentops/official-eval/result.json')
          outcome = \"${{{{ steps.official_eval.outcome }}}}\"
          conclusion = \"${{{{ steps.official_eval.conclusion }}}}\"
          status = 'success' if outcome == 'success' else 'failed' if outcome else 'unknown'
          payload = dict(
              runner='{OFFICIAL_EVAL_RUNNER}',
              system='github-actions',
              action='{official_action}',
              status=status,
              outcome=outcome,
              conclusion=conclusion,
              agent_ids=\"${{{{ steps.official_eval_input.outputs.agent_ids }}}}\",
              deployment_name=\"${{{{ steps.official_eval_input.outputs.deployment_name }}}}\",
              data_path=\"${{{{ steps.official_eval_input.outputs.data_path }}}}\",
              metadata_path=\"${{{{ steps.official_eval_input.outputs.metadata_path }}}}\",
              machine_readable_thresholds=False,
              recorded_at=datetime.now(timezone.utc).isoformat(),
          )
          output.write_text(json.dumps(payload, indent=2) + '\\n', encoding='utf-8')
          PY""",
            "__EVAL_ARTIFACT_PATHS__": """.agentops/official-eval/input.json
            .agentops/official-eval/metadata.json
            .agentops/official-eval/result.json""",
        }
    return {
        "__EVAL_STEPS__": f"""      - name: Run AgentOps eval
        id: eval
        env:
          AZURE_AI_FOUNDRY_PROJECT_ENDPOINT: ${{{{ vars.AZURE_AI_FOUNDRY_PROJECT_ENDPOINT }}}}
          AZURE_OPENAI_ENDPOINT: ${{{{ vars.AZURE_OPENAI_ENDPOINT }}}}
          AZURE_OPENAI_DEPLOYMENT: ${{{{ vars.AZURE_OPENAI_DEPLOYMENT }}}}
          APPLICATIONINSIGHTS_CONNECTION_STRING: ${{{{ secrets.APPLICATIONINSIGHTS_CONNECTION_STRING || vars.APPLICATIONINSIGHTS_CONNECTION_STRING }}}}
        run: |
          set +e
          agentops eval run --config \"{config_path}\"
          ec=$?
          echo \"exit_code=$ec\" >> \"$GITHUB_OUTPUT\"
          if [ $ec -eq 0 ]; then
            echo \"result=pass\" >> \"$GITHUB_OUTPUT\"
          elif [ $ec -eq 2 ]; then
            echo \"result=threshold_failed\" >> \"$GITHUB_OUTPUT\"
          else
            echo \"result=error\" >> \"$GITHUB_OUTPUT\"
          fi
          exit $ec""",
        "__EVAL_ARTIFACT_PATHS__": """.agentops/results/latest/results.json
            .agentops/results/latest/report.md
            .agentops/results/latest/cloud_evaluation.json""",
    }


def _ado_eval_substitutions(
    eval_runner: str,
    config_path: str,
    *,
    base_indent: int,
) -> Mapping[str, str]:
    if eval_runner == OFFICIAL_EVAL_RUNNER:
        official_task = official_eval_ado_task_ref()
        return {
            "__EVAL_TASKS__": _indent_block(
                f"""- task: AzureCLI@2
  name: official_eval_input
  displayName: Prepare official AI Agent Evaluation input
  inputs:
    azureSubscription: $(AZURE_SERVICE_CONNECTION)
    scriptType: bash
    scriptLocation: inlineScript
    inlineScript: |
      python -m agentops.pipeline.official_eval prepare \\
        --config \"{config_path}\" \\
        --out \".agentops/official-eval/input.json\" \\
        --ado-output
  env:
    AZURE_OPENAI_DEPLOYMENT: $(AZURE_OPENAI_DEPLOYMENT)
    {OFFICIAL_EVAL_ADO_TASK_ENV}: {official_task}

- task: {official_task}
  displayName: Run official AI Agent Evaluation
  inputs:
    azure-ai-project-endpoint: $(AZURE_AI_FOUNDRY_PROJECT_ENDPOINT)
    deployment-name: $(official_eval_input.officialDeploymentName)
    data-path: $(official_eval_input.officialDataPath)
    agent-ids: $(official_eval_input.officialAgentIds)

- bash: |
    mkdir -p .agentops/official-eval
    python - <<'PY'
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    job_status = "$(Agent.JobStatus)"
    normalized = job_status.strip().lower().replace("_", "").replace("-", "")
    status = "success" if normalized == "succeeded" else "failed" if normalized in ("failed", "canceled", "cancelled") else "unknown"
    payload = dict(
        runner="{OFFICIAL_EVAL_RUNNER}",
        system="azure-devops",
        task="{official_task}",
        status=status,
        job_status=job_status,
        agent_ids="$(official_eval_input.officialAgentIds)",
        deployment_name="$(official_eval_input.officialDeploymentName)",
        data_path="$(official_eval_input.officialDataPath)",
        metadata_path="$(official_eval_input.officialMetadataPath)",
        machine_readable_thresholds=False,
        recorded_at=datetime.now(timezone.utc).isoformat(),
    )
    Path(".agentops/official-eval/result.json").write_text(
        json.dumps(payload, indent=2) + "\\n",
        encoding="utf-8",
    )
    PY
  displayName: Record official eval result
  condition: always()""",
                base_indent,
            ),
            "__EVAL_ARTIFACT_TARGET__": ".agentops/official-eval",
        }
    return {
        "__EVAL_TASKS__": _indent_block(
            f"""- task: AzureCLI@2
  displayName: Run AgentOps eval
  inputs:
    azureSubscription: $(AZURE_SERVICE_CONNECTION)
    scriptType: bash
    scriptLocation: inlineScript
    inlineScript: |
      set +e
      agentops eval run --config \"{config_path}\"
      code=$?
      echo \"##vso[task.setvariable variable=AGENTOPS_EVAL_EXIT_CODE]$code\"
      exit $code
  env:
    AZURE_AI_FOUNDRY_PROJECT_ENDPOINT: $(AZURE_AI_FOUNDRY_PROJECT_ENDPOINT)
    AZURE_OPENAI_ENDPOINT: $(AZURE_OPENAI_ENDPOINT)
    AZURE_OPENAI_DEPLOYMENT: $(AZURE_OPENAI_DEPLOYMENT)
    APPLICATIONINSIGHTS_CONNECTION_STRING: $(APPLICATIONINSIGHTS_CONNECTION_STRING)""",
            base_indent,
        ),
        "__EVAL_ARTIFACT_TARGET__": ".agentops/results/latest",
    }


def _indent_block(block: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line else "" for line in block.splitlines())


def generate_cicd_workflows(
    directory: Path,
    force: bool = False,
    kinds: Sequence[str] | None = None,
    platform: str = "github",
    deploy_mode: str = "auto",
) -> CicdResult:
    """Generate AgentOps GitFlow CI/CD workflows.

    By default writes all five templates (``pr``, ``dev``, ``qa``,
    ``prod``, ``watchdog``) for the requested *platform*. Pass *kinds* to opt into a
    subset.

    Args:
        directory: Root directory of the consumer repository.
        force: When True, overwrite existing workflow files.
        kinds: Optional explicit list of workflow kinds. ``None`` means
            "generate all five". Unknown kinds are ignored.
        platform: ``"github"`` (default) writes ``.github/workflows/*.yml``
            using GitHub Actions; ``"azure-devops"`` writes
            ``.azuredevops/pipelines/*.yml`` using Azure DevOps Pipelines.
            The conceptual workflows (PR gate + three deploy stages) are
            identical across platforms.
        deploy_mode: ``"placeholder"`` writes the stack-agnostic deploy
            scaffold, ``"azd"`` writes Azure Developer CLI provision/deploy
            workflows, ``"prompt-agent"`` writes Foundry prompt-agent
            candidate/eval/deploy workflows, and ``"auto"`` selects
            ``"azd"`` when ``azure.yaml`` exists or ``"prompt-agent"`` when
            ``agentops.yaml`` targets a Foundry prompt agent.

    Returns:
        CicdResult with platform and paths of created, overwritten, or
        skipped files.
    """
    if platform not in _TEMPLATES_BY_PLATFORM:
        raise ValueError(
            f"unknown platform {platform!r}; valid: {', '.join(PLATFORMS)}"
        )
    if deploy_mode not in DEPLOY_MODES:
        raise ValueError(
            f"unknown deploy mode {deploy_mode!r}; valid: {', '.join(DEPLOY_MODES)}"
        )

    if kinds is None:
        kinds = ALL_KINDS

    directory = directory.resolve()
    effective_deploy_mode = deploy_mode
    if effective_deploy_mode == "auto":
        effective_deploy_mode = recommended_deploy_mode(directory)
    effective_eval_runner = recommended_eval_runner(directory)

    result = CicdResult(
        platform=platform,
        deploy_mode=effective_deploy_mode,
        eval_runner=effective_eval_runner,
    )
    templates_root = files(_TEMPLATE_PACKAGE)
    template_map = _TEMPLATES_BY_PLATFORM[platform]
    azd_template_map = _AZD_TEMPLATES_BY_PLATFORM.get(platform, {})
    prompt_agent_template_map = _PROMPT_AGENT_TEMPLATES_BY_PLATFORM.get(platform, {})
    azd_substitutions = _azd_substitutions(platform, has_ailz_preflight(directory))

    seen: set[str] = set()
    for kind in kinds:
        if kind in seen or kind not in template_map:
            continue
        seen.add(kind)
        substitutions: dict[str, str] = {}
        eval_config = (
            "${{ inputs.config || 'agentops.yaml' }}"
            if platform == "github" and kind == "pr"
            else "$(AGENTOPS_CONFIG)"
            if platform == "azure-devops"
            else "agentops.yaml"
        )
        if effective_deploy_mode == "azd" and kind in azd_template_map:
            template_path, output_rel = azd_template_map[kind]
            substitutions.update(azd_substitutions)
        elif effective_deploy_mode == "prompt-agent" and kind in prompt_agent_template_map:
            template_path, output_rel = prompt_agent_template_map[kind]
            eval_config = ".agentops/deployments/agentops.candidate.yaml"
            prompt_values = (
                _PROMPT_AGENT_VALUES if platform == "github" else _PROMPT_AGENT_VALUES_ADO
            )[kind]
            substitutions.update(prompt_values)
        else:
            template_path, output_rel = template_map[kind]
        ado_indent = (
            10
            if kind == "pr"
            or (effective_deploy_mode == "azd" and kind in azd_template_map)
            else 16
        )
        substitutions.update(
            _eval_substitutions(
                platform,
                effective_eval_runner,
                eval_config,
                ado_indent=ado_indent,
            )
        )
        output_path = (directory / output_rel).resolve()
        _write_template(
            templates_root,
            template_path,
            output_path,
            force,
            result,
            substitutions=substitutions,
        )

    return result


def _azd_substitutions(platform: str, ailz_preflight: bool) -> Mapping[str, str]:
    if not ailz_preflight:
        return {"__AILZ_PREFLIGHT_COMMAND__": ""}
    if platform == "azure-devops":
        return {
            "__AILZ_PREFLIGHT_COMMAND__": (
                "                        echo \"Running AI Landing Zone preflight.\"\n"
                "                        pwsh ./scripts/Invoke-PreflightChecks.ps1 -Strict"
            )
        }
    return {
        "__AILZ_PREFLIGHT_COMMAND__": (
            "            echo \"Running AI Landing Zone preflight.\"\n"
            "            pwsh ./scripts/Invoke-PreflightChecks.ps1 -Strict"
        )
    }


def generate_cicd_workflow(
    directory: Path,
    force: bool = False,
    platform: str = "github",
) -> CicdResult:
    """Generate only the PR workflow template (legacy convenience)."""
    return generate_cicd_workflows(directory, force=force, kinds=["pr"], platform=platform)
