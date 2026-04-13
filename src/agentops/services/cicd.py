"""CI/CD workflow generation service for `agentops workflow generate`."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import List, Sequence


_TEMPLATE_PACKAGE = "agentops.templates"
_WORKFLOW_TEMPLATE = "workflows/agentops-eval.yml"
_DEFAULT_OUTPUT_PATH = ".github/workflows/agentops-eval.yml"

# Mapping of workflow kind → (template path inside package, output path in repo)
_WORKFLOW_TEMPLATES = {
    "pr": ("workflows/agentops-eval.yml", ".github/workflows/agentops-eval.yml"),
    "ci": ("workflows/agentops-eval-ci.yml", ".github/workflows/agentops-eval-ci.yml"),
    "cd": ("workflows/agentops-eval-cd.yml", ".github/workflows/agentops-eval-cd.yml"),
}


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


def _detect_workflow_kinds(directory: Path) -> List[str]:
    """Auto-detect which workflow templates to generate based on workspace content.

    Always includes ``"pr"``. Adds ``"ci"`` when multiple bundles or run
    configs exist. Adds ``"cd"`` when two or more bundles or run configs
    are present (mirrors CI detection — production needs the full suite).
    """
    kinds: List[str] = ["pr"]

    agentops_dir = directory / ".agentops"
    bundles_dir = agentops_dir / "bundles"
    bundle_files: List[Path] = []
    if bundles_dir.is_dir():
        bundle_files = [f for f in bundles_dir.iterdir() if f.suffix in (".yaml", ".yml")]

    # Detect multiple bundles or run configs → include CI and CD pipelines
    run_configs = [
        f
        for f in agentops_dir.iterdir()
        if f.is_file() and f.name.startswith("run") and f.suffix in (".yaml", ".yml")
    ] if agentops_dir.is_dir() else []

    if len(bundle_files) > 1 or len(run_configs) > 1:
        kinds.append("ci")
        kinds.append("cd")

    return kinds


def _write_template(
    templates_root,
    template_path: str,
    output_path: Path,
    force: bool,
    result: CicdResult,
) -> None:
    """Read a packaged template and write it to *output_path*."""
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
    output_path = (directory / _DEFAULT_OUTPUT_PATH).resolve()
    _write_template(templates_root, _WORKFLOW_TEMPLATE, output_path, force, result)
    return result


def generate_cicd_workflows(
    directory: Path,
    force: bool = False,
    kinds: Sequence[str] | None = None,
) -> CicdResult:
    """Generate one or more GitHub Actions workflow files.

    When *kinds* is ``None``, auto-detects which templates to generate
    by inspecting the ``.agentops/`` workspace in *directory*.

    Args:
        directory: Root directory of the consumer repository.
        force: When True, overwrite existing workflow files.
        kinds: Explicit list of workflow kinds (``"pr"``, ``"ci"``,
               ``"c``None`` triggers auto-detection.

    Returns:
        CicdResult with paths of created, overwritten, or skipped files
        across all generated templates.
    """
    if kinds is None:
        kinds = _detect_workflow_kinds(directory)

    result = CicdResult()
    templates_root = files(_TEMPLATE_PACKAGE)

    for kind in kinds:
        if kind not in _WORKFLOW_TEMPLATES:
            continue
        template_path, output_rel = _WORKFLOW_TEMPLATES[kind]
        output_path = (directory / output_rel).resolve()
        _write_template(templates_root, template_path, output_path, force, result)

    return result
