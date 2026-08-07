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


_AGENT_BASE = "https://acct.services.ai.azure.com/api/projects/proj/agents/helpdeskbot"
_PROTOCOL_ROUTE = f"{_AGENT_BASE}/endpoint/protocols/openai/responses"


def test_hosted_responses_target_rewrites_version_pinned_identity_url():
    url, agent_reference = invocations._foundry_hosted_responses_target(
        f"{_AGENT_BASE}/versions/11"
    )

    assert url == f"{_PROTOCOL_ROUTE}?api-version=v1"
    assert agent_reference == {
        "type": "agent_reference",
        "name": "helpdeskbot",
        "version": "11",
    }


def test_hosted_responses_target_rewrites_unversioned_identity_url():
    url, agent_reference = invocations._foundry_hosted_responses_target(_AGENT_BASE)

    assert url == f"{_PROTOCOL_ROUTE}?api-version=v1"
    assert agent_reference is None


def test_hosted_responses_target_preserves_existing_query_parameters():
    url, _ = invocations._foundry_hosted_responses_target(
        f"{_AGENT_BASE}/endpoint/protocols/openai?api-version=2025-05-01&trace=on"
    )

    assert url == f"{_PROTOCOL_ROUTE}?api-version=2025-05-01&trace=on"


def test_hosted_responses_target_is_idempotent_on_full_route():
    configured = f"{_PROTOCOL_ROUTE}?api-version=v1"

    url, agent_reference = invocations._foundry_hosted_responses_target(configured)

    assert url == configured
    assert agent_reference is None


def test_hosted_responses_target_appends_responses_to_protocol_route():
    url, _ = invocations._foundry_hosted_responses_target(
        f"{_AGENT_BASE}/endpoint/protocols/openai"
    )

    assert url == f"{_PROTOCOL_ROUTE}?api-version=v1"


def test_hosted_responses_target_leaves_custom_endpoints_without_api_version():
    url, agent_reference = invocations._foundry_hosted_responses_target(
        "https://my-agent.azurewebsites.net/chat"
    )

    assert url == "https://my-agent.azurewebsites.net/chat/responses"
    assert agent_reference is None
