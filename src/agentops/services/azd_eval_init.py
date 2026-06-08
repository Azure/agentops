"""Wrapper helpers for ``azd ai agent eval init``."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agentops.core.azd_eval import AzdEvalRecipeError, find_eval_yaml
from agentops.pipeline.azd_runner import (
    AZD_EVAL_TIMEOUT_SECONDS,
    AZD_EXTENSION_NAME,
    AzdBackendError,
    azd_available,
)
from agentops.utils.yaml import load_yaml, save_yaml


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

    if not azd_available(cwd=root):
        raise AzdBackendError(
            "azd AI agent evaluation is not available. Install azd and the "
            f"`{AZD_EXTENSION_NAME}` extension (`azd extension install "
            f"{AZD_EXTENSION_NAME}`), then rerun `agentops eval init`."
        )

    command = ["azd", "--no-prompt", "ai", "agent", "eval", "init"]
    effective_dataset = dataset or _dataset_from_config(resolved_config)
    if effective_dataset is not None:
        command.extend(
            ["--dataset", _command_path(effective_dataset, workspace=root)]
        )

    try:
        completed = subprocess.run(
            command,
            cwd=str(root),
            text=True,
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
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def _command_path(path: Path, *, workspace: Path) -> str:
    try:
        return str(path.resolve().relative_to(workspace.resolve()))
    except ValueError:
        return str(path.resolve())
