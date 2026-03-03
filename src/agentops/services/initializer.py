"""Workspace initialization service for `agentops init`."""
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
_TEMPLATE_FILES: tuple[str, ...] = (
    "config.yaml",
    "run.yaml",
    ".gitignore",
    "bundles/model_direct_baseline.yaml",
    "bundles/rag_retrieval_baseline.yaml",
    "bundles/agent_tools_baseline.yaml",
    "datasets/smoke-model-direct.yaml",
    "datasets/smoke-model-direct.jsonl",
    "datasets/smoke-rag.yaml",
    "datasets/smoke-rag.jsonl",
    "datasets/smoke-agent-tools.yaml",
    "datasets/smoke-agent-tools.jsonl",
)


def _load_seed_templates() -> Dict[str, str]:
    """Load workspace seed files from packaged template assets."""
    templates_root = files(_TEMPLATE_PACKAGE)
    loaded: Dict[str, str] = {}

    for relative_path in _TEMPLATE_FILES:
        template = templates_root.joinpath(relative_path)
        loaded[relative_path] = template.read_text(encoding="utf-8")

    return loaded


def initialize_workspace(directory: Path, force: bool = False) -> InitResult:
    workspace_root = directory.resolve()
    agentops_dir = workspace_root / ".agentops"

    result = InitResult(workspace_dir=agentops_dir)

    folders = [
        agentops_dir,
        agentops_dir / "bundles",
        agentops_dir / "datasets",
        agentops_dir / "results",
    ]

    for folder in folders:
        if not folder.exists():
            folder.mkdir(parents=True, exist_ok=True)
            result.created_dirs.append(folder)

    for relative_path, content in _load_seed_templates().items():
        file_path = agentops_dir / relative_path
        existed_before = file_path.exists()
        if existed_before and not force:
            result.skipped_files.append(file_path)
            continue

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        if existed_before:
            result.overwritten_files.append(file_path)
        else:
            result.created_files.append(file_path)

    return result
