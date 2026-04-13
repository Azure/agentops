"""YAML config loaders for AgentOps schemas."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Type, TypeVar

from pydantic import ValidationError

from agentops.core.models import (
    BundleConfig,
    BundleRef,
    DatasetConfig,
    DatasetRef,
    RunConfig,
    WorkspaceConfig,
)
from agentops.utils.yaml import load_yaml

logger = logging.getLogger(__name__)

TModel = TypeVar("TModel")


def _load_model(path: Path, model_cls: Type[TModel], label: str) -> TModel:
    data = load_yaml(path)
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"{label} validation error: {exc}") from exc


def load_workspace_config(path: Path) -> WorkspaceConfig:
    return _load_model(path, WorkspaceConfig, "WorkspaceConfig")


def load_bundle_config(path: Path) -> BundleConfig:
    return _load_model(path, BundleConfig, "BundleConfig")


def load_dataset_config(path: Path) -> DatasetConfig:
    return _load_model(path, DatasetConfig, "DatasetConfig")


def load_run_config(path: Path) -> RunConfig:
    data = load_yaml(path)
    if isinstance(data, dict) and "backend" in data:
        raise ValueError(
            "Invalid run config: the top-level 'backend' key is not supported. "
            "Did you mean 'target.hosting'? The backend is now determined by the "
            "'target' section (type, hosting, execution_mode). Remove the 'backend' "
            "key and configure 'target.hosting' and 'target.execution_mode' instead. "
            "See docs/how-it-works.md for the current schema."
        )
    try:
        return RunConfig.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"RunConfig validation error: {exc}") from exc


def resolve_bundle_ref(ref: BundleRef, base_dir: Path, workspace_dir: Path) -> Path:
    """Resolve a bundle reference to an absolute path.

    If ``ref.path`` is set, resolve relative to *base_dir*.
    If ``ref.name`` is set, resolve to ``<workspace_dir>/bundles/<name>.yaml``.
    """
    if ref.path is not None:
        if ref.path.is_absolute():
            return ref.path
        candidate = (base_dir / ref.path).resolve()
        if candidate.exists():
            return candidate
        fallback = (Path.cwd() / ref.path).resolve()
        if fallback.exists():
            return fallback
        return candidate

    assert ref.name is not None
    return (workspace_dir / "bundles" / f"{ref.name}.yaml").resolve()


def resolve_dataset_ref(ref: DatasetRef, base_dir: Path, workspace_dir: Path) -> Path:
    """Resolve a dataset reference to an absolute path.

    If ``ref.path`` is set, resolve relative to *base_dir*.
    If ``ref.name`` is set, resolve to ``<workspace_dir>/datasets/<name>.yaml``.
    """
    if ref.path is not None:
        if ref.path.is_absolute():
            return ref.path
        candidate = (base_dir / ref.path).resolve()
        if candidate.exists():
            return candidate
        fallback = (Path.cwd() / ref.path).resolve()
        if fallback.exists():
            return fallback
        return candidate

    assert ref.name is not None
    return (workspace_dir / "datasets" / f"{ref.name}.yaml").resolve()
