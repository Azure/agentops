"""Workspace initialization service for ``agentops init``."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Dict, List


@dataclass
class InitResult:
    workspace_dir: Path
    created_dirs: List[Path] = field(default_factory=list)
    created_files: List[Path] = field(default_factory=list)
    overwritten_files: List[Path] = field(default_factory=list)
    skipped_files: List[Path] = field(default_factory=list)


_TEMPLATE_PACKAGE = "agentops.templates"

# 1.0 flat workspace: a single agentops.yaml at the project root and a tiny
# seed dataset under .agentops/data/. Everything else (bundles, datasets YAML,
# run-*.yaml variants) was removed in the revamp.
_FLAT_FILES: Dict[str, str] = {
    "agentops.yaml": "agentops.yaml",
    ".agentops/data/smoke.jsonl": "smoke.jsonl",
    ".agentops/waf-checklist.csv": "waf-checklist.csv",
}


# Project-root .gitignore. Only written when one doesn't already exist so we
# never clobber a user's curated ignore file.
_PROJECT_GITIGNORE_TEMPLATE = "project.gitignore"
_PROJECT_GITIGNORE_TARGET = ".gitignore"


def initialize_flat_workspace(directory: Path, force: bool = False) -> InitResult:
    """Bootstrap the AgentOps 1.0 workspace.

    Creates ``agentops.yaml`` at the project root and a tiny seed dataset at
    ``.agentops/data/smoke.jsonl``. Also drops a starter ``.gitignore`` at the
    project root if one does not exist yet (covers ``.venv/``, Python build
    artifacts, and the ``.agentops/results/`` runtime output).
    """
    project_root = directory.resolve()
    result = InitResult(workspace_dir=project_root / ".agentops")

    templates_root = files(_TEMPLATE_PACKAGE)
    for relative_path, template_name in _FLAT_FILES.items():
        target = project_root / relative_path
        existed_before = target.exists()
        if existed_before and not force:
            result.skipped_files.append(target)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.parent.exists():
            result.created_dirs.append(target.parent)

        content = templates_root.joinpath(template_name).read_text(encoding="utf-8")
        target.write_text(content, encoding="utf-8")

        if existed_before:
            result.overwritten_files.append(target)
        else:
            result.created_files.append(target)

    # Write a starter project-root .gitignore. We never overwrite an existing
    # one (even with --force) - users often have curated ignores we don't want
    # to clobber.
    gitignore_target = project_root / _PROJECT_GITIGNORE_TARGET
    if not gitignore_target.exists():
        content = templates_root.joinpath(_PROJECT_GITIGNORE_TEMPLATE).read_text(
            encoding="utf-8"
        )
        gitignore_target.write_text(content, encoding="utf-8")
        result.created_files.append(gitignore_target)
    else:
        result.skipped_files.append(gitignore_target)

    return result
