"""CI/CD workflow generation service for `agentops workflow generate`."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import List, Sequence


_TEMPLATE_PACKAGE = "agentops.templates"

# Mapping of workflow kind → (template path inside package, output path in repo).
#
# The four templates form a complete GenAIOps GitFlow scaffold:
#
#   pr   -> agentops-pr.yml          (PR gate; PRs to develop, release/**, main)
#   dev  -> agentops-deploy-dev.yml  (push to develop -> environment: dev)
#   qa   -> agentops-deploy-qa.yml   (push to release/** -> environment: qa)
#   prod -> agentops-deploy-prod.yml (push to main -> environment: production)
_WORKFLOW_TEMPLATES = {
    "pr": ("workflows/agentops-pr.yml", ".github/workflows/agentops-pr.yml"),
    "dev": ("workflows/agentops-deploy-dev.yml", ".github/workflows/agentops-deploy-dev.yml"),
    "qa": ("workflows/agentops-deploy-qa.yml", ".github/workflows/agentops-deploy-qa.yml"),
    "prod": ("workflows/agentops-deploy-prod.yml", ".github/workflows/agentops-deploy-prod.yml"),
}

ALL_KINDS: tuple[str, ...] = ("pr", "dev", "qa", "prod")


@dataclass
class CicdResult:
    """Result of generating CI/CD workflow files."""

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
) -> CicdResult:
    """Generate the AgentOps GitFlow GitHub Actions workflows.

    By default writes all four templates (``pr``, ``dev``, ``qa``,
    ``prod``). Pass *kinds* to opt into a subset.

    Args:
        directory: Root directory of the consumer repository.
        force: When True, overwrite existing workflow files.
        kinds: Optional explicit list of workflow kinds. ``None`` means
            "generate all four". Unknown kinds are ignored.

    Returns:
        CicdResult with paths of created, overwritten, or skipped files.
    """
    if kinds is None:
        kinds = ALL_KINDS

    result = CicdResult()
    templates_root = files(_TEMPLATE_PACKAGE)

    seen: set[str] = set()
    for kind in kinds:
        if kind in seen or kind not in _WORKFLOW_TEMPLATES:
            continue
        seen.add(kind)
        template_path, output_rel = _WORKFLOW_TEMPLATES[kind]
        output_path = (directory / output_rel).resolve()
        _write_template(templates_root, template_path, output_path, force, result)

    return result


def generate_cicd_workflow(
    directory: Path,
    force: bool = False,
) -> CicdResult:
    """Generate only the PR workflow template (legacy convenience)."""
    return generate_cicd_workflows(directory, force=force, kinds=["pr"])
