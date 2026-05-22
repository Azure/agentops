from pathlib import Path

from agentops.services.initializer import initialize_flat_workspace
from agentops.utils.yaml import load_yaml


def test_init_creates_flat_workspace(tmp_path: Path) -> None:
    result = initialize_flat_workspace(tmp_path, force=False)

    agentops_yaml = tmp_path / "agentops.yaml"
    smoke_jsonl = tmp_path / ".agentops" / "data" / "smoke.jsonl"
    sample_traces = tmp_path / ".agentops" / "traces" / "sample-traces.jsonl"

    assert agentops_yaml.is_file()
    assert smoke_jsonl.is_file()
    assert sample_traces.is_file()

    assert agentops_yaml in result.created_files
    assert smoke_jsonl in result.created_files
    assert sample_traces in result.created_files
    assert len(result.overwritten_files) == 0

    config = load_yaml(agentops_yaml)
    assert config["version"] == 1
    assert "agent" in config
    assert "dataset" in config


def test_init_does_not_overwrite_without_force(tmp_path: Path) -> None:
    initialize_flat_workspace(tmp_path, force=False)

    agentops_yaml = tmp_path / "agentops.yaml"
    sentinel = "# user edit\n"
    agentops_yaml.write_text(sentinel + agentops_yaml.read_text(encoding="utf-8"), encoding="utf-8")

    result = initialize_flat_workspace(tmp_path, force=False)

    assert agentops_yaml.read_text(encoding="utf-8").startswith("# user edit")
    assert agentops_yaml in result.skipped_files
    assert agentops_yaml not in result.overwritten_files


def test_init_overwrites_with_force(tmp_path: Path) -> None:
    initialize_flat_workspace(tmp_path, force=False)

    agentops_yaml = tmp_path / "agentops.yaml"
    agentops_yaml.write_text("# tampered\n", encoding="utf-8")

    result = initialize_flat_workspace(tmp_path, force=True)

    assert "tampered" not in agentops_yaml.read_text(encoding="utf-8")
    assert agentops_yaml in result.overwritten_files
