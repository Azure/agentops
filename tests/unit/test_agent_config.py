"""Tests for the watchdog agent config loader."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from agentops.agent.config import AgentConfig, load_agent_config


def test_load_agent_config_returns_defaults_when_missing(tmp_path: Path) -> None:
    config = load_agent_config(tmp_path / "missing.yaml")
    assert isinstance(config, AgentConfig)
    assert config.version == 1
    assert config.sources.results_history.enabled is True


def test_load_agent_config_parses_yaml(tmp_path: Path) -> None:
    cfg = tmp_path / "agent.yaml"
    cfg.write_text(
        """
version: 1
lookback_days: 14
sources:
  results_history:
    enabled: false
    path: custom/results
checks:
  regression:
    threshold_drop: 0.25
""",
        encoding="utf-8",
    )
    config = load_agent_config(cfg)
    assert config.lookback_days == 14
    assert config.sources.results_history.enabled is False
    assert config.sources.results_history.path == "custom/results"
    assert config.checks.regression.threshold_drop == 0.25


def test_load_agent_config_rejects_unknown_keys(tmp_path: Path) -> None:
    cfg = tmp_path / "agent.yaml"
    cfg.write_text("version: 1\nbogus_field: 1\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_agent_config(cfg)
