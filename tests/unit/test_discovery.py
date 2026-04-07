"""Tests for discovery services (model list, agent list)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.services.discovery import (
    list_agents,
    list_models,
)

runner = CliRunner()


class _FakeDeployment:
    def __init__(self, name: str, model_name: str, model_version: str, dtype: str):
        self.name = name
        self.model_name = model_name
        self.model_version = model_version
        self.type = dtype


class _FakeAgent:
    def __init__(self, name: str, agent_id: str, model: str = ""):
        self.name = name
        self.id = agent_id
        self.model = model


def _mock_project_client(deployments=None, agents=None):
    """Create a mock AIProjectClient."""
    client = MagicMock()
    client.deployments.list.return_value = deployments or []
    client.agents.list.return_value = agents or []
    return client


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------


class TestListModels:
    @patch("agentops.services.discovery._get_project_client")
    def test_lists_deployments(self, mock_get_client) -> None:
        mock_get_client.return_value = _mock_project_client(
            deployments=[
                _FakeDeployment("gpt-4.1", "gpt-4.1", "2025-04-14", "ModelDeployment"),
                _FakeDeployment(
                    "embed-small", "text-embedding-3-small", "1", "ModelDeployment"
                ),
            ]
        )
        result = list_models(endpoint="https://test.endpoint")
        assert len(result) == 2
        assert result[0].name == "gpt-4.1"
        assert result[0].model_version == "2025-04-14"
        assert result[1].name == "embed-small"

    @patch("agentops.services.discovery._get_project_client")
    def test_empty_list(self, mock_get_client) -> None:
        mock_get_client.return_value = _mock_project_client(deployments=[])
        result = list_models(endpoint="https://test.endpoint")
        assert result == []

    def test_missing_endpoint(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="endpoint is required"):
                list_models(endpoint=None)


class TestListAgents:
    @patch("agentops.services.discovery._get_project_client")
    def test_lists_agents(self, mock_get_client) -> None:
        mock_get_client.return_value = _mock_project_client(
            agents=[
                _FakeAgent("my-agent", "my-agent", "gpt-4.1"),
                _FakeAgent("test-bot", "test-bot"),
            ]
        )
        result = list_agents(endpoint="https://test.endpoint")
        assert len(result) == 2
        assert result[0].name == "my-agent"
        assert result[0].agent_id == "my-agent"
        assert result[1].model == ""


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestModelListCLI:
    @patch("agentops.services.discovery._get_project_client")
    def test_lists_models(self, mock_get_client) -> None:
        mock_get_client.return_value = _mock_project_client(
            deployments=[
                _FakeDeployment("gpt-4.1", "gpt-4.1", "2025-04-14", "ModelDeployment"),
            ]
        )
        result = runner.invoke(app, ["model", "list", "--endpoint", "https://test"])
        assert result.exit_code == 0
        assert "gpt-4.1" in result.stdout

    @patch("agentops.services.discovery._get_project_client")
    def test_empty(self, mock_get_client) -> None:
        mock_get_client.return_value = _mock_project_client(deployments=[])
        result = runner.invoke(app, ["model", "list", "--endpoint", "https://test"])
        assert result.exit_code == 0
        assert "No model deployments" in result.stdout


class TestAgentListCLI:
    @patch("agentops.services.discovery._get_project_client")
    def test_lists_agents(self, mock_get_client) -> None:
        mock_get_client.return_value = _mock_project_client(
            agents=[
                _FakeAgent("agent-eval", "agent-eval", "gpt-4.1"),
            ]
        )
        result = runner.invoke(app, ["agent", "list", "--endpoint", "https://test"])
        assert result.exit_code == 0
        assert "agent-eval" in result.stdout
