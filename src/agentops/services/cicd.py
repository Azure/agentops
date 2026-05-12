"""CI/CD workflow generation service for `agentops workflow generate`."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Dict, List, Sequence, Tuple


_TEMPLATE_PACKAGE = "agentops.templates"

# CI/CD platforms supported by ``agentops workflow generate``.
PLATFORMS: Tuple[str, ...] = ("github", "azure-devops")

# Per-platform mapping of workflow kind → (template path inside package,
# output path in repo).
#
# The four templates form a complete GenAIOps GitFlow scaffold:
#
#   pr   -> agentops-pr            (PR gate; PRs to develop, release/**, main)
#   dev  -> agentops-deploy-dev    (push to develop -> environment: dev)
#   qa   -> agentops-deploy-qa     (push to release/** -> environment: qa)
#   prod -> agentops-deploy-prod   (push to main -> environment: production)
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

ALL_KINDS: tuple[str, ...] = ("pr", "dev", "qa", "prod", "watchdog")


@dataclass
class CicdResult:
    """Result of generating CI/CD workflow files."""

    platform: str = "github"
    created_files: List[Path] = field(default_factory=list)
    overwritten_files: List[Path] = field(default_factory=list)
    skipped_files: List[Path] = field(default_factory=list)


def _write_template(
    templates_root,
    template_path: str,
    output_path: Path,
    force: bool,
    result: CicdResult,
) -> None:
    template_resource = templates_root.joinpath(template_path)
    template_content = template_resource.read_text(encoding="utf-8")

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


def generate_cicd_workflows(
    directory: Path,
    force: bool = False,
    kinds: Sequence[str] | None = None,
    platform: str = "github",
) -> CicdResult:
    """Generate AgentOps GitFlow CI/CD workflows.

    By default writes all four templates (``pr``, ``dev``, ``qa``,
    ``prod``) for the requested *platform*. Pass *kinds* to opt into a
    subset.

    Args:
        directory: Root directory of the consumer repository.
        force: When True, overwrite existing workflow files.
        kinds: Optional explicit list of workflow kinds. ``None`` means
            "generate all four". Unknown kinds are ignored.
        platform: ``"github"`` (default) writes ``.github/workflows/*.yml``
            using GitHub Actions; ``"azure-devops"`` writes
            ``.azuredevops/pipelines/*.yml`` using Azure DevOps Pipelines.
            The conceptual workflows (PR gate + three deploy stages) are
            identical across platforms.

    Returns:
        CicdResult with platform and paths of created, overwritten, or
        skipped files.
    """
    if platform not in _TEMPLATES_BY_PLATFORM:
        raise ValueError(
            f"unknown platform {platform!r}; valid: {', '.join(PLATFORMS)}"
        )

    if kinds is None:
        kinds = ALL_KINDS

    result = CicdResult(platform=platform)
    templates_root = files(_TEMPLATE_PACKAGE)
    template_map = _TEMPLATES_BY_PLATFORM[platform]

    seen: set[str] = set()
    for kind in kinds:
        if kind in seen or kind not in template_map:
            continue
        seen.add(kind)
        template_path, output_rel = template_map[kind]
        output_path = (directory / output_rel).resolve()
        _write_template(templates_root, template_path, output_path, force, result)

    return result


def generate_cicd_workflow(
    directory: Path,
    force: bool = False,
    platform: str = "github",
) -> CicdResult:
    """Generate only the PR workflow template (legacy convenience)."""
    return generate_cicd_workflows(directory, force=force, kinds=["pr"], platform=platform)
