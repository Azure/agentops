"""Tests for the azd eval init wrapper."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

from typer.testing import CliRunner

import agentops.cli.app as cli_app
from agentops.cli.app import app
from agentops.services import azd_eval_init


runner = CliRunner()


def _write_config(path: Path) -> None:
    path.write_text(
        """
version: 1
agent: travel-agent:1
dataset: .agentops/data/smoke.jsonl
""".lstrip(),
        encoding="utf-8",
    )


def test_run_azd_eval_init_delegates_to_azd_and_persists_recipe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "agentops.yaml"
    _write_config(config_path)

    monkeypatch.setattr(azd_eval_init, "azd_available", lambda *, cwd=None: True)

    def fake_run(command, *, cwd, text, capture_output, timeout, check):
        assert command == ["azd", "ai", "agent", "eval", "init"]
        recipe = Path(cwd) / "src" / "travel-agent" / "eval.yaml"
        recipe.parent.mkdir(parents=True, exist_ok=True)
        recipe.write_text("name: travel-agent-eval\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="created", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = azd_eval_init.run_azd_eval_init(
        workspace=tmp_path,
        config_path=config_path,
    )

    assert result.command_ran is True
    assert result.config_updated is True
    updated = config_path.read_text(encoding="utf-8")
    assert "eval_recipe: src\\travel-agent\\eval.yaml" in updated
    assert "execution: azd" in updated


def test_run_azd_eval_init_reuses_existing_recipe_without_running_azd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "agentops.yaml"
    _write_config(config_path)
    recipe = tmp_path / "eval.yaml"
    recipe.write_text("name: travel-agent-eval\n", encoding="utf-8")
    run_mock = mock.Mock()
    monkeypatch.setattr(subprocess, "run", run_mock)

    result = azd_eval_init.run_azd_eval_init(
        workspace=tmp_path,
        config_path=config_path,
    )

    assert result.command_ran is False
    assert result.recipe_path == recipe.resolve()
    run_mock.assert_not_called()


def test_cli_eval_init_prints_result(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "agentops.yaml"
    _write_config(config_path)
    recipe_path = tmp_path / "eval.yaml"

    def fake_init(**kwargs):
        return azd_eval_init.AzdEvalInitResult(
            recipe_path=recipe_path,
            config_path=config_path,
            config_updated=True,
            command_ran=True,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(azd_eval_init, "run_azd_eval_init", fake_init)

    result = runner.invoke(
        app,
        ["eval", "init", "--dir", str(tmp_path), "--config", str(config_path)],
    )

    assert result.exit_code == 0
    assert "azd eval init" in result.output
    assert "agentops eval run" in result.output


def test_cli_eval_init_uses_ascii_updated_marker_when_unicode_disabled(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "agentops.yaml"
    _write_config(config_path)
    recipe_path = tmp_path / "eval.yaml"

    def fake_init(**kwargs):
        return azd_eval_init.AzdEvalInitResult(
            recipe_path=recipe_path,
            config_path=config_path,
            config_updated=True,
            command_ran=False,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(azd_eval_init, "run_azd_eval_init", fake_init)
    monkeypatch.setattr(cli_app, "_terminal_unicode_enabled", lambda: False)

    result = runner.invoke(
        app,
        ["eval", "init", "--dir", str(tmp_path), "--config", str(config_path)],
    )

    assert result.exit_code == 0
    assert " * updated" in result.output
    assert "✓" not in result.output
