"""YAML config loaders for AgentOps schemas."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError

from agentops.core.agentops_config import AgentOpsConfig
from agentops.utils.yaml import load_yaml

logger = logging.getLogger(__name__)


def load_agentops_config(path: Path) -> AgentOpsConfig:
    """Load the flat 1.0 ``agentops.yaml`` schema."""
    data = load_yaml(path)
    try:
        return AgentOpsConfig.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"AgentOpsConfig validation error: {exc}") from exc
