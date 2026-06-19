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
project_endpoint: https://contoso.services.ai.azure.com/api/projects/travel
prompt_file: .agentops/prompts/travel.md
prompt_agent_bootstrap:
  model: gpt-4o-mini
  description: Helps plan short trips and explains tradeoffs.
""".lstrip(),
        encoding="utf-8",
    )


def _write_azd_env(workspace: Path, env_text: str = 'AZURE_RESOURCE_GROUP="rg-test"\n') -> Path:
    env_path = workspace / ".azure" / "sandbox" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text(env_text, encoding="utf-8")
    (workspace / ".azure" / "config.json").write_text(
        '{"defaultEnvironment":"sandbox"}',
        encoding="utf-8",
    )
    return env_path


def test_ensure_local_evaluator_model_env_discovers_single_chat_deployment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    env_path = _write_azd_env(tmp_path)
    selection = azd_eval_init.AzdEvaluatorSelection(
        names=("builtin.coherence",),
        source="test",
        signals=(),
    )

    def fake_run(command, **kwargs):
        az_args = command[1:]
        if az_args[:3] == ["cognitiveservices", "account", "list"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='[{"name":"aif-test","kind":"AIServices"}]',
                stderr="",
            )
        if az_args[:4] == ["cognitiveservices", "account", "deployment", "list"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "["
                    '{"name":"chat","properties":{"model":{"name":"gpt-5-nano"}}},'
                    '{"name":"text-embedding","properties":{"model":{"name":"text-embedding-3-large"}}}'
                    "]"
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = azd_eval_init.ensure_local_evaluator_model_env(
        workspace=tmp_path,
        selection=selection,
    )

    assert result.configured is True
    assert result.deployment == "chat"
    assert result.model == "gpt-5-nano"
    assert result.env_path == env_path
    assert set(result.changed_keys) == {
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_MODEL_NAME",
    }
    text = env_path.read_text(encoding="utf-8")
    assert "AZURE_OPENAI_DEPLOYMENT=chat" in text
    assert "AZURE_OPENAI_MODEL_NAME=gpt-5-nano" in text


def test_ensure_local_evaluator_model_env_does_not_guess_multiple_chat_deployments(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    env_path = _write_azd_env(tmp_path)
    selection = azd_eval_init.AzdEvaluatorSelection(
        names=("builtin.coherence",),
        source="test",
        signals=(),
    )

    def fake_run(command, **kwargs):
        az_args = command[1:]
        if az_args[:3] == ["cognitiveservices", "account", "list"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='[{"name":"aif-test","kind":"AIServices"}]',
                stderr="",
            )
        if az_args[:4] == ["cognitiveservices", "account", "deployment", "list"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "["
                    '{"name":"chat","properties":{"model":{"name":"gpt-5-nano"}}},'
                    '{"name":"judge","properties":{"model":{"name":"gpt-4o-mini"}}}'
                    "]"
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = azd_eval_init.ensure_local_evaluator_model_env(
        workspace=tmp_path,
        selection=selection,
    )

    assert result.configured is False
    assert result.changed_keys == ()
    assert "AZURE_OPENAI_DEPLOYMENT" not in env_path.read_text(encoding="utf-8")


def test_ensure_local_evaluator_model_env_reuses_existing_values(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    _write_azd_env(
        tmp_path,
        "\n".join(
            [
                'AZURE_RESOURCE_GROUP="rg-test"',
                'AZURE_OPENAI_DEPLOYMENT="judge"',
                'AZURE_OPENAI_MODEL_NAME="gpt-4o-mini"',
                "",
            ]
        ),
    )
    selection = azd_eval_init.AzdEvaluatorSelection(
        names=("builtin.coherence",),
        source="test",
        signals=(),
    )

    with mock.patch.object(subprocess, "run") as run_mock:
        result = azd_eval_init.ensure_local_evaluator_model_env(
            workspace=tmp_path,
            selection=selection,
        )

    assert result.configured is True
    assert result.deployment == "judge"
    assert result.model == "gpt-4o-mini"
    assert result.changed_keys == ()
    run_mock.assert_not_called()


def test_run_azd_eval_init_delegates_to_azd_and_persists_recipe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "agentops.yaml"
    _write_config(config_path)
    dataset = tmp_path / ".agentops" / "data" / "smoke.jsonl"
    dataset.parent.mkdir(parents=True)
    dataset.write_text('{"input":"hello","expected":"hi"}\n', encoding="utf-8")
    prompt_file = tmp_path / ".agentops" / "prompts" / "travel.md"
    prompt_file.parent.mkdir(parents=True)
    prompt_file.write_text("You are a travel planner.", encoding="utf-8")

    monkeypatch.setattr(azd_eval_init, "azd_available", lambda *, cwd=None: True)

    def fake_run(command, **kwargs):
        if command[:3] == ["az", "resource", "list"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '[{"id":"/subscriptions/sub-1/resourceGroups/rg-test/'
                    'providers/Microsoft.CognitiveServices/accounts/contoso/'
                    'projects/travel","resourceGroup":"rg-test","location":"eastus2"}]'
                ),
                stderr="",
            )
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        assert command == [
            "azd",
            "--no-prompt",
            "ai",
            "agent",
            "eval",
            "generate",
            "--project-endpoint",
            "https://contoso.services.ai.azure.com/api/projects/travel",
            "--agent",
            "travel-agent",
            "--eval-model",
            "gpt-4o-mini",
            "--dataset",
            str((tmp_path / ".agentops" / "azd" / "smoke.azd.jsonl").resolve()),
            "--evaluator",
            "builtin.coherence",
            "--evaluator",
            "builtin.fluency",
            "--evaluator",
            "builtin.text_similarity",
            "--evaluator",
            "builtin.response_completeness",
        ]
        recipe = Path(kwargs["cwd"]) / "src" / "travel-agent" / "eval.yaml"
        recipe.parent.mkdir(parents=True, exist_ok=True)
        recipe.write_text("name: travel-agent-eval\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="created", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = azd_eval_init.run_azd_eval_init(
        workspace=tmp_path,
        config_path=config_path,
    )

    assert result.command_ran is True
    assert result.evaluator_source == "AgentOps recommendation"
    assert "builtin.response_completeness" in result.evaluators
    assert result.config_updated is True
    converted = tmp_path / ".agentops" / "azd" / "smoke.azd.jsonl"
    assert '"query": "hello"' in converted.read_text(encoding="utf-8")
    assert (tmp_path / "azure.yaml").exists()
    agent_yaml = tmp_path / "src" / "travel-agent" / "agent.yaml"
    assert "Helps plan short trips" in agent_yaml.read_text(encoding="utf-8")
    env_text = (tmp_path / ".azure" / "sandbox" / ".env").read_text(encoding="utf-8")
    assert "AZURE_AI_PROJECT_ID=" in env_text
    assert "AZURE_RESOURCE_GROUP=rg-test" in env_text
    assert "FOUNDRY_PROJECT_ENDPOINT=" in env_text
    updated = config_path.read_text(encoding="utf-8")
    assert "eval_recipe: src/travel-agent/eval.yaml" in updated
    assert "execution: azd" in updated


def test_run_azd_eval_init_explicit_dataset_wins(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "agentops.yaml"
    _write_config(config_path)
    (tmp_path / "azure.yaml").write_text("name: travel-agent\n", encoding="utf-8")
    dataset = tmp_path / "golden.jsonl"
    dataset.write_text('{"input":"hello","expected":"hi"}\n', encoding="utf-8")
    prompt_file = tmp_path / ".agentops" / "prompts" / "travel.md"
    prompt_file.parent.mkdir(parents=True)
    prompt_file.write_text("You are a travel planner.", encoding="utf-8")

    monkeypatch.setattr(azd_eval_init, "azd_available", lambda *, cwd=None: True)

    def fake_run(command, **kwargs):
        if command[:3] == ["az", "resource", "list"]:
            return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")
        assert command == [
            "azd",
            "--no-prompt",
            "ai",
            "agent",
            "eval",
            "generate",
            "--project-endpoint",
            "https://contoso.services.ai.azure.com/api/projects/travel",
            "--agent",
            "travel-agent",
            "--eval-model",
            "gpt-4o-mini",
            "--dataset",
            str((tmp_path / ".agentops" / "azd" / "golden.azd.jsonl").resolve()),
            "--evaluator",
            "builtin.coherence",
            "--evaluator",
            "builtin.fluency",
            "--evaluator",
            "builtin.text_similarity",
            "--evaluator",
            "builtin.response_completeness",
        ]
        recipe = Path(kwargs["cwd"]) / "eval.yaml"
        recipe.write_text("name: travel-agent-eval\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="created", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = azd_eval_init.run_azd_eval_init(
        workspace=tmp_path,
        config_path=config_path,
        dataset=dataset,
    )

    assert result.command_ran is True


def test_run_azd_eval_init_passes_configured_rubric_evaluator(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "agentops.yaml"
    _write_config(config_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + """
rubrics:
  - name: travel-quality
    evaluator: travel-quality-rubric
    dimensions:
      - name: task_success
        description: Completes the requested travel planning task.
thresholds:
  task_success: ">=4"
""",
        encoding="utf-8",
    )
    dataset = tmp_path / ".agentops" / "data" / "smoke.jsonl"
    dataset.parent.mkdir(parents=True)
    dataset.write_text('{"input":"hello","expected":"hi"}\n', encoding="utf-8")
    prompt_file = tmp_path / ".agentops" / "prompts" / "travel.md"
    prompt_file.parent.mkdir(parents=True)
    prompt_file.write_text("You are a travel planner.", encoding="utf-8")

    monkeypatch.setattr(azd_eval_init, "azd_available", lambda *, cwd=None: True)

    def fake_run(command, **kwargs):
        if command[:3] == ["az", "resource", "list"]:
            return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")
        assert command[-2:] == ["--evaluator", "travel-quality-rubric"]
        assert command.count("--evaluator") == 5
        recipe = Path(kwargs["cwd"]) / "eval.yaml"
        recipe.write_text("name: travel-agent-eval\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="created", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = azd_eval_init.run_azd_eval_init(
        workspace=tmp_path,
        config_path=config_path,
    )

    assert result.command_ran is True


def test_run_azd_eval_init_bootstraps_before_azd_availability_check(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "agentops.yaml"
    _write_config(config_path)
    dataset = tmp_path / ".agentops" / "data" / "smoke.jsonl"
    dataset.parent.mkdir(parents=True)
    dataset.write_text('{"input":"hello","expected":"hi"}\n', encoding="utf-8")
    monkeypatch.setattr(azd_eval_init, "azd_available", lambda *, cwd=None: False)

    with mock.patch.object(subprocess, "run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess([], 0, stdout="[]", stderr="")
        try:
            azd_eval_init.run_azd_eval_init(
                workspace=tmp_path,
                config_path=config_path,
            )
        except azd_eval_init.AzdBackendError as exc:
            assert "azd AI agent evaluation is not available" in str(exc)
        else:  # pragma: no cover - assertion helper
            raise AssertionError("expected AzdBackendError")

    assert (tmp_path / "azure.yaml").exists()
    assert (tmp_path / "src" / "travel-agent" / "agent.yaml").exists()


def test_run_azd_eval_init_bootstraps_without_project_endpoint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "agentops.yaml"
    config_path.write_text(
        """
version: 1
agent: travel-agent:1
dataset: .agentops/data/smoke.jsonl
prompt_agent_bootstrap:
  model: gpt-4o-mini
""".lstrip(),
        encoding="utf-8",
    )
    dataset = tmp_path / ".agentops" / "data" / "smoke.jsonl"
    dataset.parent.mkdir(parents=True)
    dataset.write_text('{"input":"hello","expected":"hi"}\n', encoding="utf-8")
    monkeypatch.setattr(azd_eval_init, "azd_available", lambda *, cwd=None: True)

    def fake_run(command, **kwargs):
        assert "--project-endpoint" not in command
        recipe = Path(kwargs["cwd"]) / "eval.yaml"
        recipe.write_text("name: travel-agent-eval\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="created", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = azd_eval_init.run_azd_eval_init(
        workspace=tmp_path,
        config_path=config_path,
    )

    assert result.command_ran is True


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
    assert "azd eval generate" in result.output
    assert "agentops eval run" in result.output


def test_cli_eval_init_http_target_skips_azd(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "agentops.yaml"
    config_path.write_text(
        """
version: 1
agent: https://orchestrator.example.com/orchestrator
protocol: http-json
dataset: .agentops/data/vw-smoke.jsonl
request_field: question
response_field: answer
""".lstrip(),
        encoding="utf-8",
    )
    dataset = tmp_path / ".agentops" / "data" / "vw-smoke.jsonl"
    dataset.parent.mkdir(parents=True)
    dataset.write_text(
        '{"input":"hello","expected":"hi"}\n',
        encoding="utf-8",
    )
    run_mock = mock.Mock()
    monkeypatch.setattr(azd_eval_init, "run_azd_eval_init", run_mock)

    result = runner.invoke(
        app,
        ["eval", "init", "--dir", str(tmp_path), "--config", str(config_path)],
    )

    assert result.exit_code == 0, result.output
    assert "local HTTP/model target detected" in result.output
    assert "azd eval assets are not required" in result.output
    assert "Evaluator recommendation" in result.output
    assert "agentops eval run" in result.output
    run_mock.assert_not_called()


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
