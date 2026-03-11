"""CI/CD workflow generation service for `agentops config cicd`."""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import List


_TEMPLATE_PACKAGE = "agentops.templates"
_WORKFLOW_TEMPLATE = "workflows/agentops-eval.yml"
_DEFAULT_OUTPUT_PATH = ".github/workflows/agentops-eval.yml"


@dataclass
class CicdResult:
    """Result of generating CI/CD workflow files.

    Attributes:
        created_files: Paths of newly created files.
        overwritten_files: Paths of files that were overwritten.
        skipped_files: Paths of files that already existed and were skipped.
    """

    created_files: List[Path] = field(default_factory=list)
    overwritten_files: List[Path] = field(default_factory=list)
    skipped_files: List[Path] = field(default_factory=list)


def generate_cicd_workflow(
    directory: Path,
    force: bool = False,
) -> CicdResult:
    """Generate a GitHub Actions workflow file for AgentOps evaluation.

    Reads the packaged workflow template and writes it to the target
    repository's ``.github/workflows/`` directory.

    Args:
        directory: Root directory of the consumer repository.
        force: When True, overwrite the workflow file if it already exists.

    Returns:
        CicdResult with paths of created, overwritten, or skipped files.
    """
    result = CicdResult()

    templates_root = files(_TEMPLATE_PACKAGE)
    template_resource = templates_root.joinpath(_WORKFLOW_TEMPLATE)
    template_content = template_resource.read_text(encoding="utf-8")

    output_path = (directory / _DEFAULT_OUTPUT_PATH).resolve()
    existed_before = output_path.exists()

    if existed_before and not force:
        result.skipped_files.append(output_path)
        return result

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(template_content, encoding="utf-8")

    if existed_before:
        result.overwritten_files.append(output_path)
    else:
        result.created_files.append(output_path)

    return result
