from pathlib import Path

from agentops.services.initializer import initialize_workspace
from agentops.utils.yaml import load_yaml, save_yaml


def test_init_creates_expected_files(tmp_path: Path) -> None:
    result = initialize_workspace(tmp_path, force=False)

    assert (tmp_path / ".agentops").is_dir()
    assert (tmp_path / ".agentops" / "bundles").is_dir()
    assert (tmp_path / ".agentops" / "datasets").is_dir()
    assert (tmp_path / ".agentops" / "data").is_dir()
    assert (tmp_path / ".agentops" / "results").is_dir()

    assert (tmp_path / ".agentops" / "config.yaml").is_file()
    assert (tmp_path / ".agentops" / "bundles" / "model_direct_baseline.yaml").is_file()
    assert (
        tmp_path / ".agentops" / "bundles" / "rag_retrieval_baseline.yaml"
    ).is_file()
    assert (tmp_path / ".agentops" / "bundles" / "agent_tools_baseline.yaml").is_file()
    assert (tmp_path / ".agentops" / "datasets" / "smoke-model-direct.yaml").is_file()
    assert (tmp_path / ".agentops" / "datasets" / "smoke-rag.yaml").is_file()
    assert (tmp_path / ".agentops" / "datasets" / "smoke-agent-tools.yaml").is_file()
    assert (tmp_path / ".agentops" / "data" / "smoke-model-direct.jsonl").is_file()
    assert (tmp_path / ".agentops" / "data" / "smoke-rag.jsonl").is_file()
    assert (tmp_path / ".agentops" / "data" / "smoke-agent-tools.jsonl").is_file()
    assert (tmp_path / ".agentops" / "run.yaml").is_file()
    assert (tmp_path / ".agentops" / "run-rag.yaml").is_file()
    assert (tmp_path / ".agentops" / "run-agent.yaml").is_file()
    assert (tmp_path / ".agentops" / ".gitignore").is_file()

    assert len(result.created_files) == 14
    assert len(result.overwritten_files) == 0

    run_config = load_yaml(tmp_path / ".agentops" / "run.yaml")
    assert run_config["backend"]["type"] == "foundry"
    assert run_config["backend"]["target"] == "model"
    assert "agent_id" not in run_config["backend"]
    assert run_config["dataset"]["path"] == "datasets/smoke-model-direct.yaml"


def test_init_does_not_overwrite_without_force(tmp_path: Path) -> None:
    initialize_workspace(tmp_path, force=False)

    config_path = tmp_path / ".agentops" / "config.yaml"
    original = load_yaml(config_path)
    original["defaults"]["timeout_seconds"] = 999
    save_yaml(config_path, original)

    result = initialize_workspace(tmp_path, force=False)
    after = load_yaml(config_path)

    assert after["defaults"]["timeout_seconds"] == 999
    assert config_path in result.skipped_files
    assert config_path not in result.overwritten_files


def test_init_overwrites_with_force(tmp_path: Path) -> None:
    initialize_workspace(tmp_path, force=False)

    config_path = tmp_path / ".agentops" / "config.yaml"
    modified = load_yaml(config_path)
    modified["defaults"]["timeout_seconds"] = 999
    save_yaml(config_path, modified)

    result = initialize_workspace(tmp_path, force=True)
    after = load_yaml(config_path)

    assert after["defaults"]["timeout_seconds"] == 1800
    assert config_path in result.overwritten_files
