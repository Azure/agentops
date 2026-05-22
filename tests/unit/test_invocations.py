"""Unit tests for target invocation helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentops.core.agentops_config import AgentOpsConfig
from agentops.pipeline import invocations


def _config(**overrides: object) -> AgentOpsConfig:
    data = {
        "version": 1,
        "agent": "my-agent:1",
        "dataset": Path("data.jsonl"),
        **overrides,
    }
    return AgentOpsConfig(**data)


def test_project_endpoint_prefers_config_over_environment(monkeypatch):
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://from-env.services.ai.azure.com/api/projects/p",
    )
    cfg = _config(
        project_endpoint="https://from-config.services.ai.azure.com/api/projects/p"
    )

    assert (
        invocations._project_endpoint(cfg)
        == "https://from-config.services.ai.azure.com/api/projects/p"
    )


def test_project_endpoint_falls_back_to_environment(monkeypatch):
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://from-env.services.ai.azure.com/api/projects/p",
    )

    assert (
        invocations._project_endpoint(_config())
        == "https://from-env.services.ai.azure.com/api/projects/p"
    )


def test_project_endpoint_requires_config_or_environment(monkeypatch):
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)

    with pytest.raises(RuntimeError, match="project_endpoint"):
        invocations._project_endpoint(_config())
