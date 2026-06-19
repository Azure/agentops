"""Wrapper helpers for ``azd ai agent eval`` recipe generation.

The ``azure.ai.agents`` azd extension renamed this subcommand in 0.1.40:
``azd ai agent eval init`` became ``azd ai agent eval generate``. These helpers
prefer the new ``generate`` name and fall back to the legacy ``init`` name so
AgentOps keeps working across extension versions.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from agentops.core.agentops_config import classify_agent
from agentops.core.azd_eval import AzdEvalRecipeError, find_eval_yaml
from agentops.core.config_loader import load_agentops_config
from agentops.core.evaluators import detect_dataset_shape, select_evaluators
from agentops.pipeline.azd_runner import (
    AZD_EVAL_TIMEOUT_SECONDS,
    AZD_EXTENSION_NAME,
    AzdBackendError,
    azd_available,
)
from agentops.utils.yaml import load_yaml, save_yaml

_DEFAULT_AZD_EVALUATORS = (
    "builtin.coherence",
    "builtin.fluency",
)

_AI_ASSISTED_AZD_EVALUATORS = {
    "builtin.coherence",
    "builtin.fluency",
    "builtin.text_similarity",
    "builtin.groundedness",
    "builtin.relevance",
    "builtin.retrieval",
    "builtin.response_completeness",
    "builtin.tool_call_accuracy",
    "builtin.intent_resolution",
    "builtin.task_adherence",
}

_EVALUATOR_NAME_TO_AZD = {
    "CoherenceEvaluator": "builtin.coherence",
    "FluencyEvaluator": "builtin.fluency",
    "SimilarityEvaluator": "builtin.text_similarity",
    "F1ScoreEvaluator": "builtin.f1_score",
    "GroundednessEvaluator": "builtin.groundedness",
    "RelevanceEvaluator": "builtin.relevance",
    "RetrievalEvaluator": "builtin.retrieval",
    "ResponseCompletenessEvaluator": "builtin.response_completeness",
    "ToolCallAccuracyEvaluator": "builtin.tool_call_accuracy",
    "IntentResolutionEvaluator": "builtin.intent_resolution",
    "TaskAdherenceEvaluator": "builtin.task_adherence",
    "ToolSelectionEvaluator": "builtin.tool_selection",
    "ToolInputAccuracyEvaluator": "builtin.tool_input_accuracy",
    "TaskCompletionEvaluator": "builtin.task_completion",
}


@dataclass(frozen=True)
class AzdEvalInitResult:
    """Result from an azd eval init wrapper run."""

    recipe_path: Path
    config_path: Path
    config_updated: bool
    command_ran: bool
    stdout: str = ""
    stderr: str = ""
    evaluator_source: str = "unknown"
    evaluator_signals: tuple[str, ...] = ()
    evaluators: tuple[str, ...] = ()


@dataclass(frozen=True)
class AzdEvaluatorSelection:
    """Evaluators selected for azd eval init and the reason for them."""

    names: tuple[str, ...]
    source: str
    signals: tuple[str, ...]


@dataclass(frozen=True)
class EvaluatorModelEnvResult:
    """Evaluator model environment setup for local judge-based runs."""

    deployment: Optional[str] = None
    model: Optional[str] = None
    env_path: Optional[Path] = None
    changed_keys: tuple[str, ...] = ()
    source: str = "not configured"

    @property
    def configured(self) -> bool:
        return bool(self.deployment and self.model)


@dataclass(frozen=True)
class _DiscoveredModelDeployment:
    deployment: str
    model: str


def recommend_evaluators_for_config(
    *,
    config_path: Path,
    dataset: Optional[Path] = None,
) -> AzdEvaluatorSelection:
    resolved_config = config_path.resolve()
    effective_dataset = dataset or _dataset_from_config(resolved_config)
    if effective_dataset is None:
        return AzdEvaluatorSelection(
            names=(),
            source="no dataset",
            signals=("No dataset was available for evaluator inference.",),
        )
    return _azd_evaluator_selection_from_config(resolved_config, effective_dataset)


def ensure_local_evaluator_model_env(
    *,
    workspace: Path,
    selection: AzdEvaluatorSelection,
) -> EvaluatorModelEnvResult:
    """Discover and persist judge model env for local AI-assisted evaluators."""

    if not _selection_needs_ai_evaluator(selection):
        return EvaluatorModelEnvResult(source="not needed")

    env_path, env_values = _active_env_file_and_values(workspace)
    deployment = env_values.get("AZURE_OPENAI_DEPLOYMENT") or env_values.get(
        "AZURE_AI_MODEL_DEPLOYMENT_NAME"
    )
    model = env_values.get("AZURE_OPENAI_MODEL_NAME") or env_values.get(
        "AZURE_AI_MODEL_NAME"
    )
    if deployment and model:
        return EvaluatorModelEnvResult(
            deployment=deployment,
            model=model,
            env_path=env_path,
            source="existing env",
        )

    discovered = _discover_single_chat_deployment(workspace, env_values)
    if discovered is None:
        return EvaluatorModelEnvResult(
            deployment=deployment,
            model=model,
            env_path=env_path,
            source="not found",
        )

    updates = {
        "AZURE_OPENAI_DEPLOYMENT": discovered.deployment,
        "AZURE_OPENAI_MODEL_NAME": discovered.model,
    }
    if env_path is None:
        from agentops.services.setup_wizard import ensure_agentops_env  # noqa: PLC0415

        env_path = ensure_agentops_env(workspace)
    from agentops.utils.azd_env import set_env_values  # noqa: PLC0415

    changed = tuple(set_env_values(env_path, updates))
    return EvaluatorModelEnvResult(
        deployment=discovered.deployment,
        model=discovered.model,
        env_path=env_path,
        changed_keys=changed,
        source="Azure resource discovery",
    )


def _selection_needs_ai_evaluator(selection: AzdEvaluatorSelection) -> bool:
    return any(name in _AI_ASSISTED_AZD_EVALUATORS for name in selection.names)


def _active_env_file_and_values(workspace: Path) -> tuple[Optional[Path], dict[str, str]]:
    from agentops.utils.azd_env import discover_azd_env, parse_env_file  # noqa: PLC0415

    location = discover_azd_env(workspace)
    values: dict[str, str] = {}
    env_path: Optional[Path] = None
    if location.found and location.env_path is not None:
        env_path = location.env_path
        values.update(parse_env_file(location.env_path))
    else:
        agentops_env = workspace / ".agentops" / ".env"
        if agentops_env.is_file():
            env_path = agentops_env
            values.update(parse_env_file(agentops_env))
    for key in (
        "AZURE_RESOURCE_GROUP",
        "AZURE_SUBSCRIPTION_ID",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_AI_MODEL_DEPLOYMENT_NAME",
        "AZURE_OPENAI_MODEL_NAME",
        "AZURE_AI_MODEL_NAME",
    ):
        if key not in values and os.environ.get(key):
            values[key] = os.environ[key]
    return env_path, values


def _discover_single_chat_deployment(
    workspace: Path,
    env_values: dict[str, str],
) -> Optional[_DiscoveredModelDeployment]:
    resource_group = env_values.get("AZURE_RESOURCE_GROUP")
    if not resource_group:
        return None
    subscription_id = env_values.get("AZURE_SUBSCRIPTION_ID")
    accounts = _run_json(
        [
            _az_cli_command(),
            "cognitiveservices",
            "account",
            "list",
            "-g",
            resource_group,
            "-o",
            "json",
            *(["--subscription", subscription_id] if subscription_id else []),
        ],
        workspace=workspace,
    )
    if not isinstance(accounts, list):
        return None

    candidates: list[_DiscoveredModelDeployment] = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        kind = str(account.get("kind") or "").lower()
        if kind not in {"aiservices", "openai"}:
            continue
        account_name = account.get("name")
        if not isinstance(account_name, str) or not account_name.strip():
            continue
        deployments = _run_json(
            [
                _az_cli_command(),
                "cognitiveservices",
                "account",
                "deployment",
                "list",
                "-g",
                resource_group,
                "-n",
                account_name,
                "-o",
                "json",
                *(["--subscription", subscription_id] if subscription_id else []),
            ],
            workspace=workspace,
        )
        if not isinstance(deployments, list):
            continue
        for deployment in deployments:
            if not isinstance(deployment, dict):
                continue
            deployment_name = deployment.get("name")
            properties = deployment.get("properties")
            model_data = properties.get("model") if isinstance(properties, dict) else None
            model_name = model_data.get("name") if isinstance(model_data, dict) else None
            if not isinstance(deployment_name, str) or not isinstance(model_name, str):
                continue
            if not deployment_name.strip() or not model_name.strip():
                continue
            if "embedding" in model_name.lower():
                continue
            candidates.append(
                _DiscoveredModelDeployment(
                    deployment=deployment_name.strip(),
                    model=model_name.strip(),
                )
            )
    if len(candidates) == 1:
        return candidates[0]
    return None


def _run_json(command: list[str], *, workspace: Path) -> Any:
    try:
        completed = subprocess.run(  # noqa: S603,S607
            command,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if completed.returncode != 0:
        return None
    try:
        return json.loads(completed.stdout or "null")
    except json.JSONDecodeError:
        return None


def _az_cli_command() -> str:
    return shutil.which("az") or shutil.which("az.cmd") or shutil.which("az.exe") or "az"


def run_azd_eval_init(
    *,
    workspace: Path,
    config_path: Path,
    dataset: Optional[Path] = None,
    force: bool = False,
    timeout_seconds: float = AZD_EVAL_TIMEOUT_SECONDS,
) -> AzdEvalInitResult:
    """Run ``azd ai agent eval generate`` and persist ``eval_recipe``.

    Prefers the ``generate`` subcommand (azure.ai.agents >= 0.1.40) and falls
    back to the legacy ``init`` subcommand on older extensions. The azd command
    remains the source of truth for generating datasets,
    evaluators, and rubric assets. AgentOps only delegates the command, finds the
    generated recipe, and records the recipe path in ``agentops.yaml`` so future
    gates are deterministic.
    """

    root = workspace.resolve()
    resolved_config = config_path if config_path.is_absolute() else root / config_path
    resolved_config = resolved_config.resolve()
    if not resolved_config.exists():
        raise AzdBackendError(
            f"config not found at {resolved_config}. Run `agentops init` first."
        )

    existing_recipe = _find_recipe_if_unambiguous(root)
    if existing_recipe is not None and not force:
        return _persist_recipe(
            config_path=resolved_config,
            recipe_path=existing_recipe,
            command_ran=False,
        )

    _ensure_prompt_agent_azd_context(root, resolved_config)

    if not azd_available(cwd=root):
        raise AzdBackendError(
            "azd AI agent evaluation is not available. Install azd and the "
            f"`{AZD_EXTENSION_NAME}` extension (`azd extension install "
            f"{AZD_EXTENSION_NAME}`), then rerun `agentops eval init`."
        )

    base_command = ["azd", "--no-prompt", "ai", "agent", "eval"]
    arguments: list[str] = []
    project_endpoint = _project_endpoint_from_config_or_env(resolved_config)
    if project_endpoint:
        arguments.extend(["--project-endpoint", project_endpoint])
    agent_name = _agent_name_from_config(resolved_config)
    if agent_name:
        arguments.extend(["--agent", agent_name])
    effective_dataset = dataset or _dataset_from_config(resolved_config)
    instruction_file = _prompt_file_from_config(resolved_config)
    if effective_dataset is None and instruction_file is not None:
        arguments.extend(
            [
                "--gen-instruction-file",
                _command_path(instruction_file, workspace=root),
            ]
        )
    eval_model = _eval_model_from_config(resolved_config)
    if eval_model:
        arguments.extend(["--eval-model", eval_model])
    evaluator_selection = AzdEvaluatorSelection(
        names=(),
        source="no dataset",
        signals=("No dataset was available for evaluator inference.",),
    )
    if effective_dataset is not None:
        evaluator_selection = _azd_evaluator_selection_from_config(
            resolved_config,
            effective_dataset,
        )
        effective_dataset = _azd_dataset_from_agentops_dataset(
            effective_dataset,
            workspace=root,
        )
        arguments.extend(
            ["--dataset", _command_path(effective_dataset, workspace=root)]
        )
        for evaluator in evaluator_selection.names:
            arguments.extend(["--evaluator", evaluator])

    completed = _run_eval_subcommand(
        base_command,
        arguments,
        cwd=root,
        timeout_seconds=timeout_seconds,
    )

    recipe = find_eval_yaml(root)
    if recipe is None:
        raise AzdBackendError(
            "azd ai agent eval completed, but AgentOps could not find the "
            "generated eval.yaml. Move it under the workspace root or src/<agent>/ "
            "and set `eval_recipe:` in agentops.yaml."
        )

    result = _persist_recipe(
        config_path=resolved_config,
        recipe_path=recipe,
        command_ran=True,
        stdout=completed.stdout,
        stderr=completed.stderr,
        evaluator_selection=evaluator_selection,
    )
    return result


def _find_recipe_if_unambiguous(workspace: Path) -> Optional[Path]:
    try:
        return find_eval_yaml(workspace)
    except AzdEvalRecipeError:
        return None


# azd renamed this subcommand in the ``azure.ai.agents`` extension 0.1.40:
# ``init`` became ``generate``. Try the new name first and fall back to the
# legacy name so AgentOps works whether the consumer has an old or new
# extension installed.
_EVAL_SUBCOMMANDS: tuple[str, ...] = ("generate", "init")


def _eval_subcommand_unsupported(*outputs: str) -> bool:
    """Return True when azd reports the eval subcommand name is unknown/deprecated.

    Matches the azd/cobra-style messages emitted when an installed
    ``azure.ai.agents`` extension does not recognise a subcommand name (older
    extensions lack ``generate``) or reports the legacy ``init`` name as
    deprecated (newer extensions). Centralised here so the fallback decision is
    unit-testable and robust to minor wording changes.
    """
    haystack = " ".join(text.lower() for text in outputs if text)
    return any(
        phrase in haystack
        for phrase in (
            "unknown command",
            "unrecognized",
            "is not a valid",
            "invalid command",
            "is deprecated, use",
        )
    )


def _azd_failure_detail(completed: "subprocess.CompletedProcess[str]") -> str:
    return (
        completed.stderr.strip()
        or completed.stdout.strip()
        or f"exit code {completed.returncode}"
    )


def _run_eval_subcommand(
    base_command: list[str],
    arguments: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
) -> "subprocess.CompletedProcess[str]":
    """Run ``azd ai agent eval <subcommand>`` resiliently across extensions.

    Prefers ``generate`` (azure.ai.agents >= 0.1.40) and falls back to the
    legacy ``init`` subcommand when the installed extension does not recognise
    ``generate``. A non-zero result that is not a subcommand-name problem (for
    example an authentication or endpoint error) is surfaced immediately rather
    than masked by the fallback, preserving the previous error behaviour.
    """
    last_completed: Optional["subprocess.CompletedProcess[str]"] = None
    for subcommand in _EVAL_SUBCOMMANDS:
        command = [*base_command, subcommand, *arguments]
        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise AzdBackendError(
                "azd was not found on PATH. Install the Azure Developer CLI and "
                f"the `{AZD_EXTENSION_NAME}` extension, then rerun `agentops eval "
                "init`."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AzdBackendError(
                f"{' '.join(command)} timed out after {timeout_seconds:g}s."
            ) from exc

        if completed.returncode == 0:
            return completed

        if _eval_subcommand_unsupported(completed.stderr, completed.stdout):
            # This subcommand name is not supported (or is deprecated) by the
            # installed extension. Remember it and try the next candidate.
            last_completed = completed
            continue

        # A real failure (not a subcommand-name issue): surface it now.
        raise AzdBackendError(
            f"azd ai agent eval {subcommand} failed: {_azd_failure_detail(completed)}"
        )

    if last_completed is not None:
        detail = _azd_failure_detail(last_completed)
    else:  # pragma: no cover - _EVAL_SUBCOMMANDS is never empty
        detail = (
            "no azd eval subcommand (generate/init) was accepted by the "
            f"installed `{AZD_EXTENSION_NAME}` extension"
        )
    raise AzdBackendError(f"azd ai agent eval failed: {detail}")


def _dataset_from_config(config_path: Path) -> Optional[Path]:
    data = load_yaml(config_path)
    raw_dataset = data.get("dataset")
    if not raw_dataset:
        return None
    dataset = Path(str(raw_dataset))
    if not dataset.is_absolute():
        dataset = config_path.parent / dataset
    if not dataset.exists():
        return None
    return dataset


def _project_endpoint_from_config_or_env(config_path: Path) -> Optional[str]:
    data = load_yaml(config_path)
    raw_endpoint = data.get("project_endpoint")
    if isinstance(raw_endpoint, str) and raw_endpoint.strip():
        return raw_endpoint.strip()

    try:
        from agentops.utils.azd_env import discover_azd_env  # noqa: PLC0415
        from agentops.utils.dotenv_loader import parse_env_file  # noqa: PLC0415

        location = discover_azd_env(config_path.parent)
        if location.found and location.env_path is not None:
            endpoint = parse_env_file(location.env_path).get(
                "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT"
            )
            if endpoint:
                return endpoint
    except Exception:  # noqa: BLE001
        pass

    try:
        from agentops.utils.dotenv_loader import parse_env_file  # noqa: PLC0415

        for path in (
            config_path.parent / ".agentops" / ".env",
            config_path.parent / ".env",
        ):
            endpoint = parse_env_file(path).get("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
            if endpoint:
                return endpoint
    except Exception:  # noqa: BLE001
        pass

    return None


def _ensure_prompt_agent_azd_context(workspace: Path, config_path: Path) -> None:
    data = load_yaml(config_path)
    raw_agent = data.get("agent")
    if not isinstance(raw_agent, str) or not raw_agent.strip():
        return
    try:
        target = classify_agent(raw_agent)
    except ValueError:
        return
    if target.kind != "foundry_prompt" or not target.name:
        return

    model = _eval_model_from_config(config_path) or "gpt-4o-mini"
    description = _agent_description_from_config(config_path)
    service_dir = workspace / "src" / target.name
    azure_yaml = workspace / "azure.yaml"
    agent_yaml = service_dir / "agent.yaml"

    if not azure_yaml.exists():
        azure_yaml.write_text(
            _minimal_azure_yaml(
                project_name=workspace.name,
                service_name=target.name,
                model=model,
            ),
            encoding="utf-8",
        )
    if not agent_yaml.exists():
        service_dir.mkdir(parents=True, exist_ok=True)
        agent_yaml.write_text(
            _minimal_agent_yaml(
                agent_name=target.name,
                description=description,
                model=model,
            ),
            encoding="utf-8",
        )

    _ensure_azd_project_env_metadata(workspace, config_path, model=model)


def _agent_description_from_config(config_path: Path) -> str:
    data = load_yaml(config_path)
    raw_bootstrap = data.get("prompt_agent_bootstrap")
    if isinstance(raw_bootstrap, dict):
        raw_description = raw_bootstrap.get("description")
        if isinstance(raw_description, str) and raw_description.strip():
            return raw_description.strip()
    return "Foundry prompt agent evaluated by AgentOps."


def _minimal_azure_yaml(*, project_name: str, service_name: str, model: str) -> str:
    return f"""# yaml-language-server: $schema=https://raw.githubusercontent.com/Azure/azure-dev/main/schemas/v1.0/azure.yaml.json

requiredVersions:
  extensions:
    azure.ai.agents: ">=0.1.38-preview"
name: {project_name}
services:
  {service_name}:
    project: src/{service_name}
    host: azure.ai.agent
    language: none
    config:
      deployments:
        - name: {model}
          model:
            format: OpenAI
            name: {model}
          sku:
            name: GlobalStandard
            capacity: 10
"""


def _minimal_agent_yaml(*, agent_name: str, description: str, model: str) -> str:
    return f"""# yaml-language-server: $schema=https://raw.githubusercontent.com/microsoft/AgentSchema/refs/heads/main/schemas/v1.0/ContainerAgent.yaml

kind: hosted
name: {agent_name}
description: {description}
protocols:
  - protocol: responses
    version: 1.0.0
environment_variables:
  - name: AZURE_AI_MODEL_DEPLOYMENT_NAME
    value: {model}
"""


def _ensure_azd_project_env_metadata(
    workspace: Path,
    config_path: Path,
    *,
    model: str,
) -> None:
    endpoint = _project_endpoint_from_config_or_env(config_path)
    if not endpoint:
        return
    parsed = _parse_project_endpoint(endpoint)
    if parsed is None:
        return

    from agentops.utils.azd_env import (  # noqa: PLC0415
        discover_azd_env,
        ensure_azd_env,
        set_env_values,
    )

    location = discover_azd_env(workspace)
    if not location.found or location.env_path is None:
        location = ensure_azd_env(workspace, "sandbox")
    env_path = location.env_path
    if env_path is None:
        return
    updates = {
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT": endpoint,
        "FOUNDRY_PROJECT_ENDPOINT": endpoint,
        "AZURE_AI_PROJECT_NAME": parsed["project_name"],
        "AZURE_AI_ACCOUNT_NAME": parsed["account_name"],
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": model,
        "USE_EXISTING_AI_PROJECT": "true",
    }

    resource = _resolve_project_resource(parsed)
    if resource:
        updates.update(resource)
    set_env_values(env_path, updates)


def _parse_project_endpoint(endpoint: str) -> Optional[dict[str, str]]:
    match = re.match(
        r"^https://(?P<account>[^./]+)\.services\.ai\.azure\.com/api/projects/(?P<project>[^/?#]+)",
        endpoint.rstrip("/"),
    )
    if not match:
        return None
    return {
        "account_name": match.group("account"),
        "project_name": match.group("project"),
    }


def _resolve_project_resource(parsed: dict[str, str]) -> dict[str, str]:
    try:
        listed = subprocess.run(
            [
                "az",
                "resource",
                "list",
                "--resource-type",
                "Microsoft.CognitiveServices/accounts/projects",
                "--query",
                (
                    "[?name=='"
                    f"{parsed['account_name']}/{parsed['project_name']}"
                    "'].{id:id,resourceGroup:resourceGroup,location:location}"
                ),
                "-o",
                "json",
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    if listed.returncode != 0:
        return {}
    try:
        payload: Any = json.loads(listed.stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return {}
    item = payload[0]
    project_id = item.get("id")
    resource_group = item.get("resourceGroup")
    location = item.get("location")
    if not isinstance(project_id, str) or not isinstance(resource_group, str):
        return {}

    subscription_id = _subscription_id_from_resource_id(project_id)
    updates = {
        "AZURE_AI_PROJECT_ID": project_id,
        "AZURE_RESOURCE_GROUP": resource_group,
        "AZURE_OPENAI_ENDPOINT": f"https://{parsed['account_name']}.openai.azure.com/",
    }
    if isinstance(location, str) and location:
        updates["AZURE_LOCATION"] = location
        updates["AZURE_AI_DEPLOYMENTS_LOCATION"] = location
    if subscription_id:
        updates["AZURE_SUBSCRIPTION_ID"] = subscription_id
    return updates


def _subscription_id_from_resource_id(resource_id: str) -> Optional[str]:
    parts = resource_id.strip("/").split("/")
    for index, part in enumerate(parts):
        if part.lower() == "subscriptions" and index + 1 < len(parts):
            return parts[index + 1]
    return None


def _agent_name_from_config(config_path: Path) -> Optional[str]:
    data = load_yaml(config_path)
    raw_agent = data.get("agent")
    if not isinstance(raw_agent, str) or not raw_agent.strip():
        return None
    try:
        parsed = classify_agent(raw_agent)
    except ValueError:
        return None
    return parsed.name


def _prompt_file_from_config(config_path: Path) -> Optional[Path]:
    data = load_yaml(config_path)
    raw_prompt_file = data.get("prompt_file")
    if not raw_prompt_file:
        return None
    prompt_file = Path(str(raw_prompt_file))
    if not prompt_file.is_absolute():
        prompt_file = config_path.parent / prompt_file
    if not prompt_file.exists():
        return None
    return prompt_file


def _eval_model_from_config(config_path: Path) -> Optional[str]:
    data = load_yaml(config_path)
    raw_bootstrap = data.get("prompt_agent_bootstrap")
    if not isinstance(raw_bootstrap, dict):
        return None
    raw_model = raw_bootstrap.get("model")
    if isinstance(raw_model, str) and raw_model.strip():
        return raw_model.strip()
    return None


def _azd_evaluator_selection_from_config(
    config_path: Path,
    dataset: Path,
) -> AzdEvaluatorSelection:
    data = load_yaml(config_path)
    raw_evaluators = data.get("evaluators")
    names: list[str] = []
    if isinstance(raw_evaluators, list):
        for item in raw_evaluators:
            raw_name = item.get("name") if isinstance(item, dict) else item
            if not isinstance(raw_name, str) or not raw_name.strip():
                continue
            name = raw_name.strip()
            mapped = name if name.startswith("builtin.") else _EVALUATOR_NAME_TO_AZD.get(name)
            if mapped and mapped not in names:
                names.append(mapped)
        selection_source = "explicit agentops.yaml evaluators"
        signals = ("Using explicit evaluators from agentops.yaml.",)
    else:
        selection_source = "AgentOps recommendation"
        signals = []
        try:
            config = load_agentops_config(config_path)
            target = classify_agent(config.agent, config.protocol)
            shape = detect_dataset_shape(dataset)
            presets = select_evaluators(
                target,
                shape,
                threshold_metrics=config.thresholds.keys(),
            )
            signals.extend(_dataset_signal_lines(target.kind, shape))
            for preset in presets:
                mapped = _EVALUATOR_NAME_TO_AZD.get(preset.name)
                if mapped and mapped not in names:
                    names.append(mapped)
        except (FileNotFoundError, OSError, ValueError) as exc:
            selection_source = "baseline fallback"
            signals = (
                f"Could not inspect dataset for evaluator inference: {exc}",
                "Using baseline evaluators only.",
            )
        if not names:
            names.extend(_DEFAULT_AZD_EVALUATORS)
    raw_rubrics = data.get("rubrics")
    if isinstance(raw_rubrics, list):
        for item in raw_rubrics:
            if not isinstance(item, dict):
                continue
            raw_name = item.get("evaluator") or item.get("name")
            if not isinstance(raw_name, str) or not raw_name.strip():
                continue
            name = raw_name.strip()
            if name not in names:
                names.append(name)
    return AzdEvaluatorSelection(
        names=tuple(names),
        source=selection_source,
        signals=tuple(signals),
    )


def _dataset_signal_lines(kind: str, shape: Any) -> tuple[str, ...]:
    signals = [f"Target kind: {kind}."]
    if shape.looks_rag:
        signals.append("Dataset context column detected; adding RAG evaluators.")
    if shape.looks_tool_use:
        signals.append("Dataset tool trace columns detected; adding tool-use evaluators.")
    if not shape.looks_rag and not shape.looks_tool_use:
        signals.append("Free-form answer dataset detected; adding answer-quality evaluators.")
    return tuple(signals)


def _azd_dataset_from_agentops_dataset(dataset: Path, *, workspace: Path) -> Path:
    target_dir = workspace / ".agentops" / "azd"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{dataset.stem}.azd.jsonl"

    converted: list[str] = []
    changed = False
    for line in dataset.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict) and "query" not in row and "input" in row:
            row = {**row, "query": row["input"]}
            changed = True
        converted.append(json.dumps(row, ensure_ascii=False))

    if changed:
        target.write_text("\n".join(converted) + "\n", encoding="utf-8")
        return target
    return dataset


def _persist_recipe(
    *,
    config_path: Path,
    recipe_path: Path,
    command_ran: bool,
    stdout: str = "",
    stderr: str = "",
    evaluator_selection: AzdEvaluatorSelection | None = None,
) -> AzdEvalInitResult:
    data = load_yaml(config_path)
    recipe_value = _relative_config_path(recipe_path, config_path.parent)
    previous_recipe = data.get("eval_recipe")
    previous_execution = data.get("execution")
    data["eval_recipe"] = recipe_value
    if previous_execution in (None, "", "local", "auto"):
        data["execution"] = "azd"
    config_updated = previous_recipe != recipe_value or data.get("execution") != previous_execution
    if config_updated:
        save_yaml(config_path, data)
    return AzdEvalInitResult(
        recipe_path=recipe_path,
        config_path=config_path,
        config_updated=config_updated,
        command_ran=command_ran,
        stdout=stdout,
        stderr=stderr,
        evaluator_source=(evaluator_selection.source if evaluator_selection else "existing recipe"),
        evaluator_signals=(evaluator_selection.signals if evaluator_selection else ()),
        evaluators=(evaluator_selection.names if evaluator_selection else ()),
    )


def _relative_config_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _command_path(path: Path, *, workspace: Path) -> str:
    return str(path.resolve())
