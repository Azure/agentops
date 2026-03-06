from pathlib import Path

import pytest

from agentops.core.config_loader import (
    load_bundle_config,
    load_dataset_config,
    load_run_config,
    load_workspace_config,
)
from agentops.utils.yaml import load_yaml, save_yaml


def test_load_yaml_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    data = {"version": 1, "name": "example"}

    save_yaml(path, data)
    loaded = load_yaml(path)

    assert loaded == data


def test_load_yaml_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "missing.yaml"
    with pytest.raises(FileNotFoundError):
        load_yaml(path)


def test_load_yaml_invalid(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("version: [", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid YAML"):
        load_yaml(path)


def test_load_workspace_config(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
version: 1
paths:
  bundles_dir: ".agentops/bundles"
  datasets_dir: ".agentops/datasets"
  data_dir: ".agentops/data"
  results_dir: ".agentops/results"

defaults:
  backend: "subprocess"
  timeout_seconds: 1800

report:
  generate_markdown: true
""".lstrip(),
        encoding="utf-8",
    )

    cfg = load_workspace_config(path)
    assert cfg.paths.bundles_dir.as_posix() == ".agentops/bundles"
    assert cfg.paths.datasets_dir.as_posix() == ".agentops/datasets"
    assert cfg.paths.data_dir.as_posix() == ".agentops/data"


def test_load_bundle_config_validation_error(tmp_path: Path) -> None:
    path = tmp_path / "bundle.yaml"
    path.write_text(
        """
version: 1
description: "missing name"
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="BundleConfig validation error"):
        load_bundle_config(path)


def test_load_dataset_config(tmp_path: Path) -> None:
    path = tmp_path / "dataset.yaml"
    path.write_text(
        """
version: 1
name: "smoke"
description: "small dataset"
source:
  type: "file"
  path: "./eval/datasets/smoke.jsonl"
format:
  type: "jsonl"
  input_field: "input"
  expected_field: "expected"
""".lstrip(),
        encoding="utf-8",
    )

    cfg = load_dataset_config(path)
    assert cfg.name == "smoke"


def test_load_run_config_requires_subprocess_command(tmp_path: Path) -> None:
    path = tmp_path / "run.yaml"
    path.write_text(
        """
version: 1
bundle:
  path: ".agentops/bundles/rag_baseline.yaml"
dataset:
  path: ".agentops/datasets/smoke-agent.yaml"
backend:
  type: "subprocess"
  args: ["-m", "runner"]
output:
  write_report: true
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="RunConfig validation error"):
        load_run_config(path)
