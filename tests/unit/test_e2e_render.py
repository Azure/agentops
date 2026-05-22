"""Tests for ``scripts/e2e_render_config.py``.

Locks in the contract that each rendered ``agentops.yaml`` parses with the
AgentOps loader and classifies into the expected target kind. Catches
schema drift before the live e2e workflow runs against real Azure.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "e2e_render_config.py"


@pytest.fixture
def render_module(monkeypatch, tmp_path):
    """Import e2e_render_config with ROOT pointing at tmp_path."""

    spec = importlib.util.spec_from_file_location("e2e_render_config", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["e2e_render_config"] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(
        module, "DATASET_BASIC", tmp_path / "scripts" / "e2e_data" / "basic.jsonl"
    )
    monkeypatch.setattr(
        module, "DATASET_RAG", tmp_path / "scripts" / "e2e_data" / "rag.jsonl"
    )
    monkeypatch.setattr(
        module, "DATASET_TOOLS", tmp_path / "scripts" / "e2e_data" / "tools.jsonl"
    )
    yield module
    sys.modules.pop("e2e_render_config", None)


@pytest.fixture
def all_scenarios_env(monkeypatch):
    monkeypatch.setenv("AGENTOPS_E2E_FOUNDRY_PROMPT_AGENT", "e2e-prompt:1")
    monkeypatch.setenv("AGENTOPS_E2E_FOUNDRY_HOSTED_AGENT", "e2e-hosted-run42:1")
    monkeypatch.setenv(
        "AGENTOPS_E2E_ACA_URL",
        "https://aca-echo-run123.icy.eastus2.azurecontainerapps.io",
    )
    monkeypatch.setenv("AGENTOPS_E2E_MODEL_DEPLOYMENT", "gpt-4o-mini")


def test_render_writes_only_for_set_env_vars(render_module, monkeypatch, tmp_path):
    for var in (
        "AGENTOPS_E2E_FOUNDRY_PROMPT_AGENT",
        "AGENTOPS_E2E_FOUNDRY_HOSTED_AGENT",
        "AGENTOPS_E2E_ACA_URL",
        "AGENTOPS_E2E_MODEL_DEPLOYMENT",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AGENTOPS_E2E_MODEL_DEPLOYMENT", "gpt-4o-mini")

    written = render_module.render()

    assert written == ["model-direct"]
    assert (tmp_path / "e2e-runs" / "model-direct" / "agentops.yaml").exists()
    assert not (tmp_path / "e2e-runs" / "foundry-prompt").exists()


def test_render_all_scenarios_load_and_classify(
    render_module, all_scenarios_env, tmp_path
):
    from agentops.core.config_loader import load_agentops_config

    written = render_module.render()
    assert set(written) == {
        "foundry-prompt",
        "foundry-hosted",
        "http-aca",
        "model-direct",
    }

    expected_kinds = {
        "foundry-prompt": ("foundry_prompt", None),
        # The hosted agent is created dynamically and referenced as
        # name:version, so it routes through the foundry_prompt
        # (agent_reference) invocation path — same as the prompt scenario.
        "foundry-hosted": ("foundry_prompt", None),
        "http-aca": ("http_json", "http-json"),
        "model-direct": ("model_direct", None),
    }
    for scenario, (kind, protocol) in expected_kinds.items():
        cfg_path = tmp_path / "e2e-runs" / scenario / "agentops.yaml"
        cfg = load_agentops_config(cfg_path)
        target = cfg.resolved_target()
        assert target.kind == kind, (
            f"{scenario}: expected kind={kind}, got {target.kind}"
        )
        assert target.protocol == protocol, (
            f"{scenario}: expected protocol={protocol}, got {target.protocol}"
        )

        # Each rendered scenario must also write a HEADER.md so the
        # transcript script can produce a self-explanatory artifact.
        header = cfg_path.parent / "HEADER.md"
        assert header.exists(), f"{scenario}: HEADER.md is missing"
        assert header.stat().st_size > 0


def test_render_creates_datasets_when_missing(render_module, all_scenarios_env, tmp_path):
    render_module.render()
    basic = tmp_path / "scripts" / "e2e_data" / "basic.jsonl"
    rag = tmp_path / "scripts" / "e2e_data" / "rag.jsonl"
    assert basic.exists() and basic.stat().st_size > 0
    assert rag.exists() and rag.stat().st_size > 0


def test_render_main_exits_nonzero_with_no_env(render_module, monkeypatch, capsys):
    for var in (
        "AGENTOPS_E2E_FOUNDRY_PROMPT_AGENT",
        "AGENTOPS_E2E_FOUNDRY_HOSTED_AGENT",
        "AGENTOPS_E2E_ACA_URL",
        "AGENTOPS_E2E_MODEL_DEPLOYMENT",
    ):
        monkeypatch.delenv(var, raising=False)

    rc = render_module.main()
    err = capsys.readouterr().err

    assert rc == 1
    assert "no scenario env vars set" in err
