"""Tests for validation services (config validate/show, dataset validate/describe)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.services.validate import (
    describe_dataset,
    show_config,
    validate_config,
    validate_dataset,
)
from agentops.utils.yaml import save_yaml

runner = CliRunner()


def _create_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / ".agentops"
    ws.mkdir()
    (ws / "bundles").mkdir()
    (ws / "datasets").mkdir()
    (ws / "data").mkdir()
    return ws


def _write_full_workspace(tmp_path: Path, *, bad_jsonl: bool = False) -> Path:
    """Create a complete workspace with run.yaml, bundle, dataset, and JSONL."""
    ws = _create_workspace(tmp_path)

    save_yaml(
        ws / "bundles" / "test_bundle.yaml",
        {
            "version": 1,
            "name": "test_bundle",
            "evaluators": [
                {"name": "CoherenceEvaluator", "source": "foundry", "enabled": True},
            ],
            "thresholds": [
                {"evaluator": "CoherenceEvaluator", "criteria": ">=", "value": 3},
            ],
        },
    )

    save_yaml(
        ws / "datasets" / "test_dataset.yaml",
        {
            "version": 1,
            "name": "test_dataset",
            "source": {"type": "file", "path": "../data/test.jsonl"},
            "format": {
                "type": "jsonl",
                "input_field": "input",
                "expected_field": "expected",
            },
        },
    )

    if bad_jsonl:
        (ws / "data" / "test.jsonl").write_text("not valid json\n", encoding="utf-8")
    else:
        (ws / "data" / "test.jsonl").write_text(
            '{"input": "hello", "expected": "world"}\n'
            '{"input": "foo", "expected": "bar"}\n',
            encoding="utf-8",
        )

    save_yaml(
        ws / "run.yaml",
        {
            "version": 1,
            "bundle": {"path": "bundles/test_bundle.yaml"},
            "dataset": {"path": "datasets/test_dataset.yaml"},
            "backend": {
                "type": "foundry",
                "target": "model",
                "model": "gpt-4.1",
            },
            "output": {"write_report": True},
        },
    )

    return ws


# ---------------------------------------------------------------------------
# validate_config tests
# ---------------------------------------------------------------------------


class TestValidateConfig:
    def test_valid_config(self, tmp_path: Path) -> None:
        _write_full_workspace(tmp_path)
        result = validate_config(directory=tmp_path)
        assert result.passed is True
        assert result.files_checked >= 3  # run.yaml + bundle + dataset + jsonl
        assert len([i for i in result.issues if i.severity == "error"]) == 0

    def test_missing_run_yaml(self, tmp_path: Path) -> None:
        _create_workspace(tmp_path)
        result = validate_config(directory=tmp_path)
        assert result.passed is False

    def test_missing_bundle(self, tmp_path: Path) -> None:
        ws = _write_full_workspace(tmp_path)
        (ws / "bundles" / "test_bundle.yaml").unlink()
        result = validate_config(directory=tmp_path)
        assert result.passed is False
        assert any("Bundle file not found" in i.message for i in result.issues)

    def test_missing_dataset(self, tmp_path: Path) -> None:
        ws = _write_full_workspace(tmp_path)
        (ws / "datasets" / "test_dataset.yaml").unlink()
        result = validate_config(directory=tmp_path)
        assert result.passed is False
        assert any("Dataset config not found" in i.message for i in result.issues)

    def test_bad_jsonl(self, tmp_path: Path) -> None:
        _write_full_workspace(tmp_path, bad_jsonl=True)
        result = validate_config(directory=tmp_path)
        assert result.passed is False
        assert any("invalid JSON" in i.message for i in result.issues)

    def test_threshold_unknown_evaluator(self, tmp_path: Path) -> None:
        ws = _write_full_workspace(tmp_path)
        save_yaml(
            ws / "bundles" / "test_bundle.yaml",
            {
                "version": 1,
                "name": "test_bundle",
                "evaluators": [
                    {
                        "name": "CoherenceEvaluator",
                        "source": "foundry",
                        "enabled": True,
                    },
                ],
                "thresholds": [
                    {"evaluator": "NonExistent", "criteria": ">=", "value": 3},
                ],
            },
        )
        result = validate_config(directory=tmp_path)
        assert any("unknown evaluator" in i.message for i in result.issues)


# ---------------------------------------------------------------------------
# show_config tests
# ---------------------------------------------------------------------------


class TestShowConfig:
    def test_shows_config(self, tmp_path: Path) -> None:
        _write_full_workspace(tmp_path)
        result = show_config(directory=tmp_path)
        assert result.bundle_name == "test_bundle"
        assert result.dataset_name == "test_dataset"
        assert result.backend_type == "foundry"
        assert result.target == "model"
        assert result.model == "gpt-4.1"
        assert result.data_rows == 2
        assert len(result.evaluators) == 1

    def test_no_workspace(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            show_config(directory=tmp_path)


# ---------------------------------------------------------------------------
# validate_dataset tests
# ---------------------------------------------------------------------------


class TestValidateDataset:
    def test_valid_dataset(self, tmp_path: Path) -> None:
        ws = _write_full_workspace(tmp_path)
        result = validate_dataset(ws / "datasets" / "test_dataset.yaml")
        assert result.passed is True
        assert result.files_checked == 2  # dataset.yaml + jsonl

    def test_missing_field(self, tmp_path: Path) -> None:
        ws = _write_full_workspace(tmp_path)
        (ws / "data" / "test.jsonl").write_text(
            '{"input": "hello"}\n', encoding="utf-8"
        )
        result = validate_dataset(ws / "datasets" / "test_dataset.yaml")
        assert result.passed is False
        assert any("missing required field" in i.message for i in result.issues)

    def test_missing_data_file(self, tmp_path: Path) -> None:
        ws = _write_full_workspace(tmp_path)
        (ws / "data" / "test.jsonl").unlink()
        result = validate_dataset(ws / "datasets" / "test_dataset.yaml")
        assert result.passed is False
        assert any("Data file not found" in i.message for i in result.issues)

    def test_empty_data(self, tmp_path: Path) -> None:
        ws = _write_full_workspace(tmp_path)
        (ws / "data" / "test.jsonl").write_text("", encoding="utf-8")
        result = validate_dataset(ws / "datasets" / "test_dataset.yaml")
        assert result.passed is True  # warning, not error
        assert any("empty" in i.message for i in result.issues)


# ---------------------------------------------------------------------------
# describe_dataset tests
# ---------------------------------------------------------------------------


class TestDescribeDataset:
    def test_describes_dataset(self, tmp_path: Path) -> None:
        ws = _write_full_workspace(tmp_path)
        desc = describe_dataset(ws / "datasets" / "test_dataset.yaml")
        assert desc.name == "test_dataset"
        assert desc.row_count == 2
        assert desc.input_field == "input"
        assert desc.expected_field == "expected"
        assert "input" in desc.fields
        assert "expected" in desc.fields

    def test_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            describe_dataset(tmp_path / "nonexistent.yaml")


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestConfigValidateCLI:
    def test_valid(self, tmp_path: Path) -> None:
        _write_full_workspace(tmp_path)
        result = runner.invoke(app, ["config", "validate", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "PASSED" in result.stdout

    def test_invalid(self, tmp_path: Path) -> None:
        _write_full_workspace(tmp_path, bad_jsonl=True)
        result = runner.invoke(app, ["config", "validate", "--dir", str(tmp_path)])
        assert result.exit_code == 1
        assert "FAILED" in result.stdout


class TestConfigShowCLI:
    def test_shows(self, tmp_path: Path) -> None:
        _write_full_workspace(tmp_path)
        result = runner.invoke(app, ["config", "show", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "test_bundle" in result.stdout
        assert "CoherenceEvaluator" in result.stdout


class TestDatasetValidateCLI:
    def test_valid(self, tmp_path: Path) -> None:
        ws = _write_full_workspace(tmp_path)
        result = runner.invoke(
            app,
            ["dataset", "validate", str(ws / "datasets" / "test_dataset.yaml")],
        )
        assert result.exit_code == 0
        assert "PASSED" in result.stdout


class TestDatasetDescribeCLI:
    def test_describes(self, tmp_path: Path) -> None:
        ws = _write_full_workspace(tmp_path)
        result = runner.invoke(
            app,
            ["dataset", "describe", str(ws / "datasets" / "test_dataset.yaml")],
        )
        assert result.exit_code == 0
        assert "test_dataset" in result.stdout
        assert "Rows: 2" in result.stdout
