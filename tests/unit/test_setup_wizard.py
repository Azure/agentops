"""Tests for :mod:`agentops.services.setup_wizard`."""

from __future__ import annotations

import builtins
import json
import os
import subprocess
from pathlib import Path

import pytest

import agentops.services.setup_wizard as setup_wizard
from agentops.services.setup_wizard import (
    WizardAnswers,
    apply_answers,
    collect_snapshot,
    discover_defaults,
    mask_secret,
    run_wizard,
    validate_agent,
    validate_dataset,
    validate_project_endpoint,
)
from agentops.utils.azd_env import parse_env_file


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validate_project_endpoint_accepts_https():
    assert validate_project_endpoint("https://acct.services.ai.azure.com/api/projects/p") is None


def test_validate_project_endpoint_rejects_garbage():
    err = validate_project_endpoint("not a url")
    assert err and "https://" in err


def test_validate_project_endpoint_blank_is_ok():
    assert validate_project_endpoint("") is None


def test_validate_agent_accepts_name_version():
    assert validate_agent("my-agent:7") is None


def test_validate_agent_accepts_model_deployment():
    assert validate_agent("model:gpt-4o") is None


def test_validate_agent_accepts_url():
    assert validate_agent("https://api.example.com/chat") is None


def test_validate_agent_rejects_bare_name():
    err = validate_agent("just-a-name")
    assert err and "<name>:<version>" in err


def test_validate_dataset_requires_existing_file(tmp_path: Path):
    err = validate_dataset(".agentops/data/missing.jsonl", tmp_path)
    assert err and "does not exist" in err

    (tmp_path / "data.jsonl").write_text("{}\n", encoding="utf-8")
    assert validate_dataset("data.jsonl", tmp_path) is None


# ---------------------------------------------------------------------------
# Defaults discovery
# ---------------------------------------------------------------------------


def _seed_azd_env(workspace: Path, env_name: str, lines: dict[str, str]) -> Path:
    env_dir = workspace / ".azure" / env_name
    env_dir.mkdir(parents=True)
    env_path = env_dir / ".env"
    env_path.write_text(
        "\n".join(f"{k}={v}" for k, v in lines.items()) + "\n",
        encoding="utf-8",
    )
    (workspace / ".azure" / "config.json").write_text(
        json.dumps({"version": 1, "defaultEnvironment": env_name}) + "\n",
        encoding="utf-8",
    )
    return env_path


def test_discover_defaults_reads_yaml_agent_and_dataset(tmp_path: Path):
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: my-bot:2\ndataset: ./qa.jsonl\n",
        encoding="utf-8",
    )
    defaults = discover_defaults(tmp_path)
    assert defaults.agent == "my-bot:2"
    assert defaults.dataset == "./qa.jsonl"


def test_discover_defaults_reads_project_endpoint_from_azd_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    _seed_azd_env(
        tmp_path,
        "dev",
        {
            "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT": (
                '"https://from-azd.services.ai.azure.com/api/projects/p"'
            ),
        },
    )
    defaults = discover_defaults(tmp_path)
    assert defaults.project_endpoint == (
        "https://from-azd.services.ai.azure.com/api/projects/p"
    )


def test_discover_defaults_reads_appinsights_from_azd_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    _seed_azd_env(
        tmp_path,
        "dev",
        {"APPLICATIONINSIGHTS_CONNECTION_STRING": '"InstrumentationKey=zzz"'},
    )
    defaults = discover_defaults(tmp_path)
    assert defaults.appinsights_connection_string == "InstrumentationKey=zzz"


def test_discover_defaults_falls_back_to_legacy_agentops_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Workspaces without .azure/ keep getting their old .agentops/.env values."""
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    (tmp_path / ".agentops").mkdir()
    (tmp_path / ".agentops" / ".env").write_text(
        'APPLICATIONINSIGHTS_CONNECTION_STRING="InstrumentationKey=legacy"\n',
        encoding="utf-8",
    )
    defaults = discover_defaults(tmp_path)
    assert defaults.appinsights_connection_string == "InstrumentationKey=legacy"


def test_discover_defaults_env_var_fallback_for_project_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """When no env file holds the value, the process environment is consulted."""
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://from-shell.services.ai.azure.com/api/projects/p",
    )
    defaults = discover_defaults(tmp_path)
    assert defaults.project_endpoint and "from-shell" in defaults.project_endpoint


def test_discover_defaults_tracks_endpoint_source_azd_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Endpoint read from .azure/<env>/.env reports source=azd-env-file."""
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    env_path = _seed_azd_env(
        tmp_path,
        "dev",
        {
            "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT": (
                '"https://from-azd.services.ai.azure.com/api/projects/p"'
            ),
        },
    )
    defaults = discover_defaults(tmp_path)
    assert defaults.project_endpoint_source == "azd-env-file"
    assert defaults.project_endpoint_source_path == env_path


def test_discover_defaults_tracks_endpoint_source_agentops_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Endpoint read from .agentops/.env reports source=agentops-env-file."""
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    (tmp_path / ".agentops").mkdir()
    (tmp_path / ".agentops" / ".env").write_text(
        'AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="https://legacy.services.ai.azure.com/api/projects/p"\n',
        encoding="utf-8",
    )
    defaults = discover_defaults(tmp_path)
    assert defaults.project_endpoint_source == "agentops-env-file"
    assert defaults.project_endpoint_source_path is not None


def test_discover_defaults_tracks_endpoint_source_process_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Endpoint inherited from the shell reports source=process-env."""
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://from-shell.services.ai.azure.com/api/projects/p",
    )
    defaults = discover_defaults(tmp_path)
    assert defaults.project_endpoint_source == "process-env"
    assert defaults.project_endpoint_source_path is None


def test_discover_defaults_tracks_endpoint_source_yaml_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Endpoint from the legacy yaml field reports source=yaml-legacy."""
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\n"
        "agent: my-bot:1\n"
        "project_endpoint: https://from-yaml.services.ai.azure.com/api/projects/p\n",
        encoding="utf-8",
    )
    defaults = discover_defaults(tmp_path)
    assert defaults.project_endpoint_source == "yaml-legacy"


def test_discover_defaults_endpoint_source_none_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    defaults = discover_defaults(tmp_path)
    assert defaults.project_endpoint is None
    assert defaults.project_endpoint_source == "none"


def test_discover_defaults_suggests_foundry_project_from_azd_resource_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    env_path = _seed_azd_env(
        tmp_path,
        "dev",
        {
            "AZURE_RESOURCE_GROUP": "rg-dev",
            "AZURE_SUBSCRIPTION_ID": "11111111-1111-1111-1111-111111111111",
        },
    )
    endpoint = "https://acct.services.ai.azure.com/api/projects/proj"
    monkeypatch.setattr(setup_wizard, "_az_cli_executable", lambda: "az")

    def fake_run(args, **kwargs):  # noqa: ANN001
        assert args[:5] == [
            "az",
            "resource",
            "list",
            "-g",
            "rg-dev",
        ]
        assert "--subscription" in args
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=json.dumps(
                [
                    {
                        "properties": {
                            "isDefault": True,
                            "endpoints": {"AI Foundry API": endpoint},
                        }
                    }
                ]
            ),
            stderr="",
        )

    monkeypatch.setattr(setup_wizard.subprocess, "run", fake_run)

    defaults = discover_defaults(tmp_path)

    assert defaults.project_endpoint == endpoint
    assert defaults.project_endpoint_source == "azd-resource-discovery"
    assert defaults.project_endpoint_source_path == env_path


def test_discover_defaults_reads_foundry_project_properties_by_resource_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    _seed_azd_env(tmp_path, "dev", {"AZURE_RESOURCE_GROUP": "rg-dev"})
    endpoint = "https://acct.services.ai.azure.com/api/projects/proj"
    resource_id = (
        "/subscriptions/000/resourceGroups/rg-dev/providers/"
        "Microsoft.CognitiveServices/accounts/acct/projects/proj"
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(setup_wizard, "_az_cli_executable", lambda: "az")

    def fake_run(args, **kwargs):  # noqa: ANN001
        calls.append(args)
        if args[:3] == ["az", "resource", "list"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=json.dumps([{"id": resource_id, "properties": None}]),
                stderr="",
            )
        if args[:3] == ["az", "resource", "show"]:
            assert resource_id in args
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=json.dumps(
                    {
                        "properties": {
                            "isDefault": True,
                            "endpoints": {"AI Foundry API": endpoint},
                        }
                    }
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(setup_wizard.subprocess, "run", fake_run)

    defaults = discover_defaults(tmp_path)

    assert defaults.project_endpoint == endpoint
    assert [call[:3] for call in calls] == [
        ["az", "resource", "list"],
        ["az", "resource", "show"],
    ]


def test_discover_defaults_ignores_ambiguous_foundry_project_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    _seed_azd_env(tmp_path, "dev", {"AZURE_RESOURCE_GROUP": "rg-dev"})

    def fake_run(args, **kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=json.dumps(
                [
                    {
                        "properties": {
                            "isDefault": False,
                            "endpoints": {
                                "AI Foundry API": (
                                    "https://one.services.ai.azure.com/api/projects/p"
                                )
                            },
                        }
                    },
                    {
                        "properties": {
                            "isDefault": False,
                            "endpoints": {
                                "AI Foundry API": (
                                    "https://two.services.ai.azure.com/api/projects/p"
                                )
                            },
                        }
                    },
                ]
            ),
            stderr="",
        )

    monkeypatch.setattr(setup_wizard.subprocess, "run", fake_run)

    defaults = discover_defaults(tmp_path)

    assert defaults.project_endpoint is None
    assert defaults.project_endpoint_source == "none"


# ---------------------------------------------------------------------------
# Apply / persistence
# ---------------------------------------------------------------------------


def test_apply_answers_writes_agent_and_dataset_to_yaml(tmp_path: Path):
    answers = WizardAnswers(agent="my-bot:7", dataset=".agentops/data/smoke.jsonl")
    result = apply_answers(tmp_path, answers)
    assert result.yaml_updated is True
    text = (tmp_path / "agentops.yaml").read_text(encoding="utf-8")
    assert "version: 1" in text
    assert "agent: my-bot:7" in text
    assert "dataset: .agentops/data/smoke.jsonl" in text


def test_apply_answers_writes_yaml_without_pyyaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    real_import = builtins.__import__

    def _import(name, *args, **kwargs):  # noqa: ANN001
        if name == "yaml":
            raise ModuleNotFoundError("No module named 'yaml'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import)

    result = apply_answers(
        tmp_path,
        WizardAnswers(agent="my-bot:7", dataset=".agentops/data/smoke.jsonl"),
    )

    assert result.yaml_updated is True
    assert "agent: my-bot:7" in (tmp_path / "agentops.yaml").read_text(
        encoding="utf-8"
    )


def test_apply_answers_does_not_write_project_endpoint_to_yaml(tmp_path: Path):
    """Environment-specific endpoints stay out of agentops.yaml."""
    answers = WizardAnswers(
        project_endpoint="https://acct.services.ai.azure.com/api/projects/p"
    )
    apply_answers(tmp_path, answers)
    yaml_path = tmp_path / "agentops.yaml"
    if yaml_path.exists():
        assert "project_endpoint" not in yaml_path.read_text(encoding="utf-8")


def test_apply_answers_writes_endpoint_to_agentops_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    answers = WizardAnswers(
        project_endpoint="https://acct.services.ai.azure.com/api/projects/p",
    )
    result = apply_answers(tmp_path, answers)
    assert result.env_updated is True
    assert result.azd_env_created is False
    assert result.azd_env_name is None
    assert result.env_path == (tmp_path / ".agentops" / ".env").resolve()
    parsed = parse_env_file(result.env_path)
    assert parsed["AZURE_AI_FOUNDRY_PROJECT_ENDPOINT"] == (
        "https://acct.services.ai.azure.com/api/projects/p"
    )
    assert not (tmp_path / ".azure").exists()


def test_apply_answers_writes_appinsights_to_agentops_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    answers = WizardAnswers(
        appinsights_connection_string="InstrumentationKey=abc;IngestionEndpoint=https://x/"
    )
    result = apply_answers(tmp_path, answers)
    assert result.env_updated is True
    parsed = parse_env_file(result.env_path)
    assert (
        parsed["APPLICATIONINSIGHTS_CONNECTION_STRING"]
        == "InstrumentationKey=abc;IngestionEndpoint=https://x/"
    )


def test_apply_answers_creates_gitignore_for_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Writing .agentops/.env must drop a .gitignore so secrets cannot leak."""
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    apply_answers(
        tmp_path,
        WizardAnswers(appinsights_connection_string="InstrumentationKey=zzz"),
    )
    gitignore = tmp_path / ".agentops" / ".gitignore"
    assert gitignore.is_file()
    contents = gitignore.read_text(encoding="utf-8")
    assert ".env" in contents


def test_apply_answers_explicit_azd_env_sets_default_env_in_config_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    apply_answers(
        tmp_path,
        WizardAnswers(project_endpoint="https://acct.services.ai.azure.com/api/projects/p"),
        azd_env_name="dev",
    )
    config = json.loads((tmp_path / ".azure" / "config.json").read_text(encoding="utf-8"))
    assert config["defaultEnvironment"] == "dev"


def test_apply_answers_uses_existing_azd_env_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    _seed_azd_env(tmp_path, "prod", {"AZURE_AI_FOUNDRY_PROJECT_ENDPOINT": "https://old/"})
    result = apply_answers(
        tmp_path,
        WizardAnswers(project_endpoint="https://new.services.ai.azure.com/api/projects/p"),
    )
    assert result.azd_env_created is False
    assert result.azd_env_name == "prod"
    assert result.env_path == (tmp_path / ".azure" / "prod" / ".env").resolve()


def test_apply_answers_preserves_existing_yaml_fields(tmp_path: Path):
    """A second invocation must not blow away fields the user did not touch."""
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\n"
        "agent: keep-me:1\n"
        "dataset: ./data.jsonl\n"
        "thresholds:\n"
        "  coherence: '>=3'\n",
        encoding="utf-8",
    )
    apply_answers(
        tmp_path,
        WizardAnswers(project_endpoint="https://acct.services.ai.azure.com/api/projects/p"),
    )
    text = (tmp_path / "agentops.yaml").read_text(encoding="utf-8")
    assert "agent: keep-me:1" in text
    assert "thresholds" in text
    assert "coherence" in text


def test_apply_answers_no_op_when_nothing_provided(tmp_path: Path):
    result = apply_answers(tmp_path, WizardAnswers())
    assert result.yaml_updated is False
    assert result.env_updated is False
    assert not (tmp_path / "agentops.yaml").exists()
    assert not (tmp_path / ".azure").exists()


def test_apply_answers_is_idempotent_when_values_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    answers = WizardAnswers(
        project_endpoint="https://acct.services.ai.azure.com/api/projects/p",
        agent="my-bot:7",
        dataset=".agentops/data/smoke.jsonl",
        appinsights_connection_string="InstrumentationKey=zzz",
    )
    apply_answers(tmp_path, answers)

    result = apply_answers(tmp_path, answers)
    assert result.yaml_updated is False
    assert result.env_updated is False
    assert result.yaml_fields == []
    assert result.env_keys == []


def test_apply_answers_preserves_env_file_comments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Line-preserving edits keep comments and untouched keys."""
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    env_path = _seed_azd_env(
        tmp_path,
        "dev",
        {"AZURE_AI_FOUNDRY_PROJECT_ENDPOINT": "https://old/"},
    )
    # Add comment + unrelated key by hand.
    env_path.write_text(
        "# managed by azd\n"
        '"AZURE_AI_FOUNDRY_PROJECT_ENDPOINT"=https://old/\n'.replace('"', "")
        + 'AZURE_SUBSCRIPTION_ID=11111111-1111-1111-1111-111111111111\n'
        + '# trailing comment\n',
        encoding="utf-8",
    )
    apply_answers(
        tmp_path,
        WizardAnswers(project_endpoint="https://new.services.ai.azure.com/api/projects/p"),
    )
    text = env_path.read_text(encoding="utf-8")
    assert "# managed by azd" in text
    assert "# trailing comment" in text
    assert "AZURE_SUBSCRIPTION_ID=11111111-1111-1111-1111-111111111111" in text
    assert "https://new.services.ai.azure.com" in text
    assert "https://old/" not in text


# ---------------------------------------------------------------------------
# Interactive loop (with injected prompt)
# ---------------------------------------------------------------------------


def _scripted_prompt(answers: list[str]):
    queue = list(answers)

    def _prompt(question: str, default):  # noqa: ANN001
        if not queue:
            raise AssertionError(f"Wizard asked extra question: {question}")
        return queue.pop(0)

    return _prompt


def test_run_wizard_collects_core_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Isolate from the developer shell so all interactive questions are asked.
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)

    (tmp_path / "data.jsonl").write_text("{}\n", encoding="utf-8")
    prompt = _scripted_prompt(
        [
            "https://acct.services.ai.azure.com/api/projects/p",
            "my-bot:9",
            "data.jsonl",
        ]
    )
    answers = run_wizard(tmp_path, prompt=prompt, echo=lambda _msg: None)
    assert answers.project_endpoint == "https://acct.services.ai.azure.com/api/projects/p"
    assert answers.agent == "my-bot:9"
    assert answers.dataset == "data.jsonl"
    assert answers.appinsights_connection_string is None


def test_run_wizard_does_not_prompt_for_appinsights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "InstrumentationKey=zzz")

    (tmp_path / "data.jsonl").write_text("{}\n", encoding="utf-8")
    prompt = _scripted_prompt(
        [
            "https://acct.services.ai.azure.com/api/projects/p",
            "my-bot:9",
            "data.jsonl",
        ]
    )
    messages: list[str] = []
    answers = run_wizard(tmp_path, prompt=prompt, echo=messages.append)
    assert answers.appinsights_connection_string is None
    assert "Application Insights" not in "\n".join(messages)


def test_run_wizard_prompts_with_discovered_foundry_endpoint_and_persists_enter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    _seed_azd_env(tmp_path, "dev", {"AZURE_RESOURCE_GROUP": "rg-dev"})
    endpoint = "https://acct.services.ai.azure.com/api/projects/proj"
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: keep:1\ndataset: keep.jsonl\n",
        encoding="utf-8",
    )
    (tmp_path / "keep.jsonl").write_text("{}\n", encoding="utf-8")

    def fake_run(args, **kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=json.dumps(
                [
                    {
                        "properties": {
                            "isDefault": True,
                            "endpoints": {"AI Foundry API": endpoint},
                        }
                    }
                ]
            ),
            stderr="",
        )

    monkeypatch.setattr(setup_wizard.subprocess, "run", fake_run)
    seen_defaults: list[str | None] = []
    persisted: list[tuple[str, str]] = []

    def prompt(_question: str, default):  # noqa: ANN001
        seen_defaults.append(default)
        return ""

    answers = run_wizard(
        tmp_path,
        prompt=prompt,
        echo=lambda _msg: None,
        on_answer=lambda field, value: persisted.append((field, value)),
    )

    assert seen_defaults == [endpoint]
    assert answers.project_endpoint == endpoint
    assert persisted == [("project_endpoint", endpoint)]


def test_run_wizard_empty_input_reprompts_required_missing_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)

    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: keep:1\ndataset: keep.jsonl\n",
        encoding="utf-8",
    )
    (tmp_path / "keep.jsonl").write_text("{}\n", encoding="utf-8")
    # With idempotent skip-on-default, agent/dataset are silently reused.
    # Only the still-empty project endpoint gets asked; App Insights is left
    # for runtime discovery or explicit non-interactive configuration.
    prompt = _scripted_prompt(["", "https://acct.services.ai.azure.com/api/projects/p"])
    messages: list[str] = []
    answers = run_wizard(
        tmp_path, prompt=prompt, echo=messages.append, reconfigure=False
    )
    assert answers.project_endpoint == "https://acct.services.ai.azure.com/api/projects/p"
    assert answers.agent is None
    assert answers.dataset is None
    assert answers.appinsights_connection_string is None
    assert "Foundry project endpoint is required." in "\n".join(messages)


def test_run_wizard_reprompts_blank_required_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)

    data_dir = tmp_path / ".agentops" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "smoke.jsonl").write_text("{}\n", encoding="utf-8")
    prompt = _scripted_prompt(
        [
            "",
            "https://acct.services.ai.azure.com/api/projects/p",
            "",
            "my-bot:9",
            "",
        ]
    )
    messages: list[str] = []

    answers = run_wizard(tmp_path, prompt=prompt, echo=messages.append)

    assert answers.project_endpoint == "https://acct.services.ai.azure.com/api/projects/p"
    assert answers.agent == "my-bot:9"
    assert answers.dataset == ".agentops/data/smoke.jsonl"
    output = "\n".join(messages)
    assert "Foundry project endpoint is required." in output
    assert "Agent is required." in output


def test_run_wizard_force_prompt_fields_reasks_seed_agent_and_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    _seed_azd_env(
        tmp_path,
        "dev",
        {
            "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT": (
                "https://acct.services.ai.azure.com/api/projects/p"
            ),
            "APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=zzz",
        },
    )
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: my-agent:1\ndataset: .agentops/data/smoke.jsonl\n",
        encoding="utf-8",
    )
    data_dir = tmp_path / ".agentops" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "smoke.jsonl").write_text("{}\n", encoding="utf-8")
    (data_dir / "travel-smoke.jsonl").write_text("{}\n", encoding="utf-8")

    replies = iter(["", "travel-agent:1", ".agentops/data/travel-smoke.jsonl"])
    prompt_calls: list[tuple[str, object]] = []

    def prompt(question: str, default):  # noqa: ANN001
        prompt_calls.append((question, default))
        return next(replies)

    answers = run_wizard(
        tmp_path,
        prompt=prompt,
        echo=lambda _msg: None,
        force_prompt_fields={"agent", "dataset"},
    )

    assert prompt_calls == [
        ("Foundry project endpoint", "https://acct.services.ai.azure.com/api/projects/p"),
        ("Agent / orchestrator endpoint", None),
        ("Dataset path", ".agentops/data/smoke.jsonl"),
    ]
    assert answers.project_endpoint is None
    assert answers.agent == "travel-agent:1"
    assert answers.dataset == ".agentops/data/travel-smoke.jsonl"
    assert answers.appinsights_connection_string is None


def test_run_wizard_reasks_placeholder_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    _seed_azd_env(
        tmp_path,
        "dev",
        {
            "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT": (
                "https://acct.services.ai.azure.com/api/projects/p"
            ),
        },
    )
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: my-agent:1\ndataset: .agentops/data/smoke.jsonl\n",
        encoding="utf-8",
    )
    data_dir = tmp_path / ".agentops" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "smoke.jsonl").write_text("{}\n", encoding="utf-8")

    prompt_calls: list[tuple[str, object]] = []

    def prompt(question: str, default):  # noqa: ANN001
        prompt_calls.append((question, default))
        return "" if question == "Foundry project endpoint" else "http://127.0.0.1:8000/chat"

    answers = run_wizard(tmp_path, prompt=prompt, echo=lambda _msg: None)

    assert prompt_calls == [
        ("Foundry project endpoint", "https://acct.services.ai.azure.com/api/projects/p"),
        ("Agent / orchestrator endpoint", None),
    ]
    assert answers.agent == "http://127.0.0.1:8000/chat"


def test_run_wizard_appinsights_is_not_interactive_even_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The wizard should not ask for App Insights just to leave it blank."""
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    _seed_azd_env(
        tmp_path,
        "dev",
        {
            "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT": (
                "https://acct.services.ai.azure.com/api/projects/p"
            ),
        },
    )
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: keep:1\ndataset: keep.jsonl\n",
        encoding="utf-8",
    )
    (tmp_path / "keep.jsonl").write_text("{}\n", encoding="utf-8")

    messages: list[str] = []
    prompt_calls: list[str] = []

    def prompt(question: str, _default):  # noqa: ANN001
        prompt_calls.append(question)
        return ""

    run_wizard(tmp_path, prompt=prompt, echo=messages.append)

    output = "\n".join(messages)
    assert "Application Insights" not in output
    assert "Application Insights connection string" not in prompt_calls


def test_run_wizard_reconfigure_does_not_echo_appinsights_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Reconfigure mode should not surface App Insights in the wizard."""
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    _seed_azd_env(
        tmp_path,
        "dev",
        {
            "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT": (
                "https://acct.services.ai.azure.com/api/projects/p"
            ),
            "APPLICATIONINSIGHTS_CONNECTION_STRING": (
                "InstrumentationKey=abc;ApplicationId=secret1234"
            ),
        },
    )
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: keep:1\ndataset: keep.jsonl\n",
        encoding="utf-8",
    )
    (tmp_path / "keep.jsonl").write_text("{}\n", encoding="utf-8")

    messages: list[str] = []
    prompt_calls: list[str] = []

    def prompt(question: str, _default):  # noqa: ANN001
        prompt_calls.append(question)
        return ""

    run_wizard(tmp_path, prompt=prompt, echo=messages.append, reconfigure=True)

    assert "Application Insights connection string" not in prompt_calls
    output = "\n".join(messages)
    assert "secret1234" not in output
    assert "Application Insights" not in output


def test_run_wizard_re_prompts_on_invalid_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)

    (tmp_path / "data.jsonl").write_text("{}\n", encoding="utf-8")
    prompt = _scripted_prompt(
        [
            "not a url",
            "https://acct.services.ai.azure.com/api/projects/p",
            "bare-name",
            "my-bot:2",
            "data.jsonl",
        ]
    )
    errors: list[str] = []
    answers = run_wizard(
        tmp_path,
        prompt=prompt,
        echo=lambda msg: errors.append(msg) if msg.lstrip().startswith("!") else None,
    )
    assert answers.project_endpoint == "https://acct.services.ai.azure.com/api/projects/p"
    assert answers.agent == "my-bot:2"
    assert len(errors) == 2


# ---------------------------------------------------------------------------
# Cross-environment endpoint protection (--azd-env <name>)
# ---------------------------------------------------------------------------


def test_run_wizard_suppresses_cross_env_endpoint_from_process_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A shell-exported endpoint must NOT pre-fill when targeting --azd-env dev.

    Reproduces the leak vector where ``$AZURE_AI_FOUNDRY_PROJECT_ENDPOINT``
    points at the user's sandbox project but they are running
    ``agentops init --azd-env dev`` against a freshly-created dev env.
    """
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://sandbox.services.ai.azure.com/api/projects/p",
    )
    _seed_azd_env(tmp_path, "dev", {})  # dev env exists but is empty
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: keep:1\ndataset: keep.jsonl\n",
        encoding="utf-8",
    )
    (tmp_path / "keep.jsonl").write_text("{}\n", encoding="utf-8")

    seen_defaults: list = []
    messages: list[str] = []

    def prompt(_question: str, default):  # noqa: ANN001
        seen_defaults.append(default)
        return "https://dev.services.ai.azure.com/api/projects/p"

    answers = run_wizard(
        tmp_path,
        prompt=prompt,
        echo=messages.append,
        target_env_name="dev",
    )

    assert answers.project_endpoint == (
        "https://dev.services.ai.azure.com/api/projects/p"
    )
    # The endpoint prompt was called with no default (None) — not pre-filled.
    assert seen_defaults and seen_defaults[0] is None
    output = "\n".join(messages)
    assert "sandbox.services.ai.azure.com" in output
    assert "shell environment" in output


def test_run_wizard_suppresses_cross_env_endpoint_from_yaml_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A legacy yaml ``project_endpoint`` must NOT pre-fill an explicit env."""
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\n"
        "agent: keep:1\n"
        "dataset: keep.jsonl\n"
        "project_endpoint: https://from-yaml.services.ai.azure.com/api/projects/p\n",
        encoding="utf-8",
    )
    (tmp_path / "keep.jsonl").write_text("{}\n", encoding="utf-8")
    _seed_azd_env(tmp_path, "dev", {})

    seen_defaults: list = []
    messages: list[str] = []

    def prompt(_question: str, default):  # noqa: ANN001
        seen_defaults.append(default)
        return "https://dev.services.ai.azure.com/api/projects/p"

    run_wizard(
        tmp_path,
        prompt=prompt,
        echo=messages.append,
        target_env_name="dev",
    )

    assert seen_defaults and seen_defaults[0] is None
    output = "\n".join(messages)
    assert "from-yaml.services.ai.azure.com" in output
    assert "agentops.yaml" in output


def test_run_wizard_suppresses_cross_env_endpoint_from_other_azd_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Endpoint sitting in .azure/sandbox/.env must not pre-fill --azd-env dev."""
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    # sandbox holds an endpoint and is the *active* azd env...
    _seed_azd_env(
        tmp_path,
        "sandbox",
        {
            "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT": (
                '"https://sandbox.services.ai.azure.com/api/projects/p"'
            ),
        },
    )
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: keep:1\ndataset: keep.jsonl\n",
        encoding="utf-8",
    )
    (tmp_path / "keep.jsonl").write_text("{}\n", encoding="utf-8")

    seen_defaults: list = []
    messages: list[str] = []

    def prompt(_question: str, default):  # noqa: ANN001
        seen_defaults.append(default)
        return "https://dev.services.ai.azure.com/api/projects/p"

    # ...but the user is explicitly running --azd-env dev.
    run_wizard(
        tmp_path,
        prompt=prompt,
        echo=messages.append,
        target_env_name="dev",
    )

    assert seen_defaults and seen_defaults[0] is None
    output = "\n".join(messages)
    assert "sandbox.services.ai.azure.com" in output


def test_run_wizard_keeps_endpoint_default_when_target_env_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """When .azure/dev/.env already holds the endpoint, reuse it silently."""
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    _seed_azd_env(
        tmp_path,
        "dev",
        {
            "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT": (
                '"https://dev.services.ai.azure.com/api/projects/p"'
            ),
        },
    )
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: keep:1\ndataset: keep.jsonl\n",
        encoding="utf-8",
    )
    (tmp_path / "keep.jsonl").write_text("{}\n", encoding="utf-8")

    messages: list[str] = []
    prompt_calls: list[str] = []

    def prompt(question: str, _default):  # noqa: ANN001
        prompt_calls.append(question)
        return ""

    run_wizard(
        tmp_path,
        prompt=prompt,
        echo=messages.append,
        target_env_name="dev",
    )

    output = "\n".join(messages)
    # Confirmed without re-prompting + no cross-env warning text leaked.
    assert "Foundry project endpoint" in output
    assert "shell environment" not in output
    assert "may belong to a different environment" not in output
    assert "Foundry project endpoint" not in prompt_calls


def test_run_wizard_without_target_env_keeps_legacy_default_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Bare ``agentops init`` (no --azd-env) still pre-fills from the shell."""
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://from-shell.services.ai.azure.com/api/projects/p",
    )
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: keep:1\ndataset: keep.jsonl\n",
        encoding="utf-8",
    )
    (tmp_path / "keep.jsonl").write_text("{}\n", encoding="utf-8")

    prompt_calls: list[str] = []
    messages: list[str] = []

    def prompt(question: str, _default):  # noqa: ANN001
        prompt_calls.append(question)
        return ""

    run_wizard(tmp_path, prompt=prompt, echo=messages.append)

    output = "\n".join(messages)
    assert "may belong to a different environment" not in output
    # And the endpoint is silently reused (not re-prompted) on the bare path.
    assert "Foundry project endpoint" not in prompt_calls


# ---------------------------------------------------------------------------
# Round trip with auto-loader
# ---------------------------------------------------------------------------


def test_apply_then_load_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The wizard writes into the AgentOps env and the auto-loader picks it up."""
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    apply_answers(
        tmp_path,
        WizardAnswers(appinsights_connection_string="InstrumentationKey=zzz"),
    )

    from agentops.utils.dotenv_loader import load_workspace_dotenv

    loaded = load_workspace_dotenv(tmp_path)
    assert loaded is not None
    path, count = loaded
    assert path == (tmp_path / ".agentops" / ".env").resolve()
    assert count >= 1
    assert os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"] == "InstrumentationKey=zzz"


# ---------------------------------------------------------------------------
# Snapshot / setup show
# ---------------------------------------------------------------------------


def test_collect_snapshot_reports_missing_when_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    monkeypatch.delenv("AGENTOPS_FOUNDRY_MODE", raising=False)
    snapshot = collect_snapshot(tmp_path)
    assert snapshot.azd_env_name is None
    assert snapshot.azd_status == "not_found"
    assert snapshot.yaml_present is False
    assert "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT" in snapshot.missing_required


def test_collect_snapshot_reads_azd_env_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    _seed_azd_env(
        tmp_path,
        "dev",
        {"AZURE_AI_FOUNDRY_PROJECT_ENDPOINT": "https://x.services.ai.azure.com/api/projects/p"},
    )
    snapshot = collect_snapshot(tmp_path)
    assert snapshot.azd_env_name == "dev"
    assert snapshot.azd_status == "ok"
    proj_var = next(
        v for v in snapshot.variables if v.key == "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT"
    )
    assert proj_var.value == "https://x.services.ai.azure.com/api/projects/p"
    assert proj_var.source == "azd-env"
    assert snapshot.missing_required == []


def test_collect_snapshot_attributes_process_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://shell.services.ai.azure.com/api/projects/p",
    )
    snapshot = collect_snapshot(tmp_path)
    proj_var = next(
        v for v in snapshot.variables if v.key == "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT"
    )
    assert proj_var.source == "process-env"


def test_collect_snapshot_reports_agentops_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    (tmp_path / ".agentops").mkdir()
    (tmp_path / ".agentops" / ".env").write_text(
        "APPLICATIONINSIGHTS_CONNECTION_STRING=InstrumentationKey=legacy\n",
        encoding="utf-8",
    )
    snapshot = collect_snapshot(tmp_path)
    assert snapshot.agentops_env_path is not None
    appinsights_var = next(
        v for v in snapshot.variables if v.key == "APPLICATIONINSIGHTS_CONNECTION_STRING"
    )
    assert appinsights_var.source == "agentops-env"


def test_mask_secret_handles_short_and_long_values():
    assert mask_secret(None) == "(not set)"
    assert mask_secret("") == "(not set)"
    assert mask_secret("short") == "***"
    masked = mask_secret("AAAAmiddleBBBB")
    assert masked.startswith("AAAA") and masked.endswith("BBBB") and "***" in masked
