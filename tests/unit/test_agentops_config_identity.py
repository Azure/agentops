"""Regression tests for the optional ``identity`` block in agentops.yaml.

``AgentOpsConfig`` sets ``extra="forbid"``, so any key the model does not
declare makes every command that calls ``load_agentops_config`` fail. The
Agent 365 identity work reads the block directly from the raw YAML, which
means a missing field declaration would only surface later, in unrelated
commands. These tests pin the contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentops.core.config_loader import load_agentops_config

BASE_CONFIG = "version: 1\nagent: my-rag:3\ndataset: .agentops/data/seed.jsonl\n"


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "agentops.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_config_without_identity_still_loads(tmp_path: Path) -> None:
    config = load_agentops_config(_write(tmp_path, BASE_CONFIG))
    assert config.identity is None


def test_identity_block_is_accepted(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        BASE_CONFIG
        + "identity:\n"
        + "  display_name: support-agent\n"
        + "  sponsor: owner@contoso.com\n"
        + "  verify: true\n",
    )

    config = load_agentops_config(path)

    assert config.identity is not None
    assert config.identity.display_name == "support-agent"
    assert config.identity.sponsor == "owner@contoso.com"
    assert config.identity.verify is True


def test_identity_verify_defaults_to_false(tmp_path: Path) -> None:
    path = _write(tmp_path, BASE_CONFIG + "identity:\n  sponsor: owner@contoso.com\n")

    config = load_agentops_config(path)

    assert config.identity is not None
    assert config.identity.verify is False
    assert config.identity.display_name is None


def test_unknown_identity_key_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, BASE_CONFIG + "identity:\n  sponser: owner@contoso.com\n")

    with pytest.raises(ValueError) as excinfo:
        load_agentops_config(path)

    assert "sponser" in str(excinfo.value)
