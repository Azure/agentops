"""YAML config loaders for AgentOps schemas."""

from __future__ import annotations

from pathlib import Path
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from agentops.core.models import (
    BundleConfig,
    DatasetConfig,
    RunConfig,
    WorkspaceConfig,
)
from agentops.utils.yaml import load_yaml

TModel = TypeVar("TModel", bound=BaseModel)


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
    return _load_model(path, RunConfig, "RunConfig")
