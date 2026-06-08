"""Wrapper helpers for ``azd ai agent eval init``."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agentops.core.agentops_config import classify_agent
from agentops.core.azd_eval import AzdEvalRecipeError, find_eval_yaml
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


def run_azd_eval_init(
    *,
    workspace: Path,
    config_path: Path,
    dataset: Optional[Path] = None,
    force: bool = False,
    timeout_seconds: float = AZD_EVAL_TIMEOUT_SECONDS,
) -> AzdEvalInitResult:
    """Run ``azd ai agent eval init`` and persist ``eval_recipe``.

    The azd command remains the source of truth for generating datasets,
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

    if not (root / "azure.yaml").exists():
        raise AzdBackendError(
            "`agentops eval init` requires a full azd AI agent project with "
            "`azure.yaml`. Add the azd service descriptor and Foundry project "
            "metadata from the prompt-agent tutorial step 10, or run "
            "`azd ai agent init` for a hosted-agent project, then rerun "
            "`agentops eval init`."
        )

    if not azd_available(cwd=root):
        raise AzdBackendError(
            "azd AI agent evaluation is not available. Install azd and the "
            f"`{AZD_EXTENSION_NAME}` extension (`azd extension install "
            f"{AZD_EXTENSION_NAME}`), then rerun `agentops eval init`."
        )

    command = ["azd", "--no-prompt", "ai", "agent", "eval", "init"]
    project_endpoint = _project_endpoint_from_config_or_env(resolved_config)
    if project_endpoint:
        command.extend(["--project-endpoint", project_endpoint])
    agent_name = _agent_name_from_config(resolved_config)
    if agent_name:
        command.extend(["--agent", agent_name])
    effective_dataset = dataset or _dataset_from_config(resolved_config)
    instruction_file = _prompt_file_from_config(resolved_config)
    if effective_dataset is None and instruction_file is not None:
        command.extend(
            [
                "--gen-instruction-file",
                _command_path(instruction_file, workspace=root),
            ]
        )
    eval_model = _eval_model_from_config(resolved_config)
    if eval_model:
        command.extend(["--eval-model", eval_model])
    if effective_dataset is not None:
        effective_dataset = _azd_dataset_from_agentops_dataset(
            effective_dataset,
            workspace=root,
        )
        command.extend(
            ["--dataset", _command_path(effective_dataset, workspace=root)]
        )
        for evaluator in _azd_evaluators_from_config(resolved_config):
            command.extend(["--evaluator", evaluator])

    try:
        completed = subprocess.run(
            command,
            cwd=str(root),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AzdBackendError(
            "azd was not found on PATH. Install the Azure Developer CLI and the "
            f"`{AZD_EXTENSION_NAME}` extension, then rerun `agentops eval init`."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise AzdBackendError(
            f"{' '.join(command)} timed out after {timeout_seconds:g}s."
        ) from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise AzdBackendError(f"azd ai agent eval init failed: {detail}")

    recipe = find_eval_yaml(root)
    if recipe is None:
        raise AzdBackendError(
            "azd ai agent eval init completed, but AgentOps could not find the "
            "generated eval.yaml. Move it under the workspace root or src/<agent>/ "
            "and set `eval_recipe:` in agentops.yaml."
        )

    result = _persist_recipe(
        config_path=resolved_config,
        recipe_path=recipe,
        command_ran=True,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    return result


def _find_recipe_if_unambiguous(workspace: Path) -> Optional[Path]:
    try:
        return find_eval_yaml(workspace)
    except AzdEvalRecipeError:
        return None


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


def _azd_evaluators_from_config(config_path: Path) -> tuple[str, ...]:
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
    return tuple(names) if names else _DEFAULT_AZD_EVALUATORS


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
    )


def _relative_config_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _command_path(path: Path, *, workspace: Path) -> str:
    return str(path.resolve())
