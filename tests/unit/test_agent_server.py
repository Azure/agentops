"""Tests for the FastAPI Copilot Extension server (agent extras)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from agentops.agent.config import AgentConfig, ResultsHistorySourceConfig, SourcesConfig

if importlib.util.find_spec("fastapi") is None:
    pytest.skip("FastAPI not installed; agent extras unavailable", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from agentops.agent.server.app import create_app  # noqa: E402


def _config() -> AgentConfig:
    sources = SourcesConfig()
    sources.results_history = ResultsHistorySourceConfig(
        enabled=True, path=".agentops/results", lookback_runs=10
    )
    sources.azure_monitor.enabled = False
    sources.foundry_control.enabled = False
    return AgentConfig(sources=sources)


def test_healthz(tmp_path: Path) -> None:
    app = create_app(workspace=tmp_path, config=_config(), verify_signature=False)
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_messages_streams_sse(tmp_path: Path) -> None:
    app = create_app(workspace=tmp_path, config=_config(), verify_signature=False)
    client = TestClient(app)
    response = client.post(
        "/agents/messages",
        json={"messages": [{"role": "user", "content": "Run the watchdog"}]},
    )
    assert response.status_code == 200
    body = response.text
    assert "data:" in body
    assert "[DONE]" in body


def test_messages_requires_signature_when_enabled(tmp_path: Path) -> None:
    app = create_app(workspace=tmp_path, config=_config(), verify_signature=True)
    client = TestClient(app)
    response = client.post(
        "/agents/messages",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401
