"""Shared workspace resolution for CLI services."""

from __future__ import annotations

from pathlib import Path

_DEFAULT_AGENTOPS_DIR = ".agentops"


def resolve_workspace(directory: Path) -> Path:
    """Resolve the .agentops workspace directory.

    Raises:
        FileNotFoundError: If no .agentops directory exists.
    """
    workspace = (directory / _DEFAULT_AGENTOPS_DIR).resolve()
    if not workspace.is_dir():
        raise FileNotFoundError(
            f"No .agentops workspace found at {workspace}. Run 'agentops init' first."
        )
    return workspace
