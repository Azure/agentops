"""Integration tests for the consolidated `agentops init` command.

These tests cover the behaviours that landed when `agentops setup` was
merged into `agentops init`:

* scaffold the AgentOps workspace without requiring or creating azd state
* scripted mode persists values to `agentops.yaml` + `.agentops/.env` by default
* `agentops init show` mirrors the old `setup show`
* `agentops init explain` emits the long-form manual
* no `agentops setup` command exists anymore
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from agentops.cli.app import app

runner = CliRunner()


def _strip_ansi(text: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)


# ---------------------------------------------------------------------------
# init scaffolds the workspace without bootstrapping azd
# ---------------------------------------------------------------------------


def test_init_scaffolds_workspace_without_bootstrapping_azure(tmp_path: Path) -> None:
    """`agentops init` with no flags (non-TTY) scaffolds AgentOps files only."""
    result = runner.invoke(app, ["init", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.stdout
    assert "Initialized AgentOps workspace" in result.stdout

    # Workspace scaffolded
    assert (tmp_path / "agentops.yaml").exists()
    assert (tmp_path / ".agentops" / ".gitignore").exists()
    assert (tmp_path / ".agentops" / "data" / "smoke.jsonl").exists()

    # Fresh AgentOps-only workspaces should not get azd files until requested.
    assert not (tmp_path / ".azure").exists()

    # .agentops/.gitignore protects the local env file
    gitignore_text = (tmp_path / ".agentops" / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore_text


def test_init_no_prompt_skips_wizard(tmp_path: Path) -> None:
    """`--no-prompt` produces a clean scaffold-only output."""
    result = runner.invoke(app, ["init", "--dir", str(tmp_path), "--no-prompt"])

    assert result.exit_code == 0
    assert "Initialized AgentOps workspace" in result.stdout
    assert "Workspace ready" in result.stdout
    assert "agentops skills install" in result.stdout
    # Should NOT print the TTY tip when --no-prompt was explicit
    assert "stdin is not a TTY" not in result.stdout


def test_init_scripted_mode_persists_endpoint_to_agentops_env(tmp_path: Path) -> None:
    """Scripted mode writes project endpoint to .agentops/.env by default."""
    endpoint = "https://acct.services.ai.azure.com/api/projects/proj-default"
    smoke = tmp_path / ".agentops" / "data" / "smoke.jsonl"

    # First scaffold (so dataset path validation passes).
    runner.invoke(app, ["init", "--dir", str(tmp_path), "--no-prompt"])
    assert smoke.exists()

    result = runner.invoke(
        app,
        [
            "init",
            "--dir",
            str(tmp_path),
            "--project-endpoint",
            endpoint,
            "--agent",
            "my-agent:1",
            "--dataset",
            ".agentops/data/smoke.jsonl",
        ],
    )

    assert result.exit_code == 0, result.stdout

    # agentops.yaml has agent/dataset
    yaml_text = (tmp_path / "agentops.yaml").read_text(encoding="utf-8")
    assert "my-agent:1" in yaml_text
    assert "smoke.jsonl" in yaml_text

    # .agentops/.env has the endpoint
    env_text = (tmp_path / ".agentops" / ".env").read_text(encoding="utf-8")
    assert "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT" in env_text
    assert endpoint in env_text
    assert not (tmp_path / ".azure").exists()


def test_init_scripted_validates_project_endpoint(tmp_path: Path) -> None:
    """Bogus endpoint flag is rejected with exit code 1."""
    result = runner.invoke(
        app,
        [
            "init",
            "--dir",
            str(tmp_path),
            "--project-endpoint",
            "not-a-url",
        ],
    )

    assert result.exit_code == 1
    assert "Project endpoint" in result.output


def test_init_scripted_blank_required_value_is_friendly(tmp_path: Path) -> None:
    """Blank scripted values fail with a concise message, not a traceback."""
    result = runner.invoke(app, ["init", "--dir", str(tmp_path), "--agent", ""])

    assert result.exit_code == 1
    assert "--agent is required" in result.output
    assert "AgentOps needs a Foundry project endpoint" in result.output
    assert "Traceback" not in result.output


def test_init_scripted_with_custom_azd_env(tmp_path: Path) -> None:
    """`--azd-env qa` writes to the explicit azd env instead of the local env."""
    runner.invoke(app, ["init", "--dir", str(tmp_path), "--no-prompt"])

    result = runner.invoke(
        app,
        [
            "init",
            "--dir",
            str(tmp_path),
            "--azd-env",
            "qa",
            "--project-endpoint",
            "https://acct.services.ai.azure.com/api/projects/p",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert (tmp_path / ".azure" / "qa" / ".env").exists()
    env_text = (tmp_path / ".azure" / "qa" / ".env").read_text(encoding="utf-8")
    assert "https://acct.services.ai.azure.com/api/projects/p" in env_text
    assert not (tmp_path / ".agentops" / ".env").exists()


def test_init_scripted_uses_existing_azd_env_when_present(tmp_path: Path) -> None:
    """Existing azd workspaces keep writing to the active azd env."""
    azure_env = tmp_path / ".azure" / "dev" / ".env"
    azure_env.parent.mkdir(parents=True, exist_ok=True)
    azure_env.write_text("# existing azd env\n", encoding="utf-8")
    (tmp_path / ".azure" / "config.json").write_text(
        '{"version": 1, "defaultEnvironment": "dev"}\n',
        encoding="utf-8",
    )
    runner.invoke(app, ["init", "--dir", str(tmp_path), "--no-prompt"])

    result = runner.invoke(
        app,
        [
            "init",
            "--dir",
            str(tmp_path),
            "--project-endpoint",
            "https://acct.services.ai.azure.com/api/projects/p",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "https://acct.services.ai.azure.com/api/projects/p" in azure_env.read_text(
        encoding="utf-8"
    )
    assert not (tmp_path / ".agentops" / ".env").exists()


def test_init_is_idempotent(tmp_path: Path) -> None:
    """Re-running init scripted with the same values does not error."""
    runner.invoke(app, ["init", "--dir", str(tmp_path), "--no-prompt"])

    args = [
        "init",
        "--dir",
        str(tmp_path),
        "--agent",
        "my-agent:1",
    ]
    first = runner.invoke(app, args)
    second = runner.invoke(app, args)
    assert first.exit_code == 0
    assert second.exit_code == 0


# ---------------------------------------------------------------------------
# init show
# ---------------------------------------------------------------------------


def test_init_show_reports_missing_when_empty(tmp_path: Path, monkeypatch) -> None:
    """`init show` on a fresh workspace reports missing required vars."""
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    result = runner.invoke(app, ["init", "show", "--dir", str(tmp_path)])

    # Exit code 1 because the required project endpoint is unset.
    assert result.exit_code == 1
    assert "AgentOps configuration" in result.stdout
    assert "Missing required values" in result.output


def test_init_show_after_scripted_init_succeeds(tmp_path: Path, monkeypatch) -> None:
    """After scripted init, `init show` reports values and exits 0."""
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    runner.invoke(app, ["init", "--dir", str(tmp_path), "--no-prompt"])
    runner.invoke(
        app,
        [
            "init",
            "--dir",
            str(tmp_path),
            "--project-endpoint",
            "https://acct.services.ai.azure.com/api/projects/p",
            "--agent",
            "my-agent:1",
        ],
    )

    result = runner.invoke(app, ["init", "show", "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    assert "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT" in result.stdout
    assert "my-agent:1" in result.stdout


def test_init_show_masks_secrets_by_default(tmp_path: Path, monkeypatch) -> None:
    """`init show` masks secret values; `--reveal-secrets` opts out."""
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    secret = "InstrumentationKey=00000000-0000-0000-0000-aaaaaaaaaaaa"
    runner.invoke(app, ["init", "--dir", str(tmp_path), "--no-prompt"])
    runner.invoke(
        app,
        [
            "init",
            "--dir",
            str(tmp_path),
            "--project-endpoint",
            "https://acct.services.ai.azure.com/api/projects/p",
            "--appinsights-connection-string",
            secret,
        ],
    )

    masked = runner.invoke(app, ["init", "show", "--dir", str(tmp_path)])
    assert masked.exit_code == 0
    assert secret not in masked.stdout

    revealed = runner.invoke(
        app, ["init", "show", "--dir", str(tmp_path), "--reveal-secrets"]
    )
    assert revealed.exit_code == 0
    assert secret in revealed.stdout


# ---------------------------------------------------------------------------
# init explain
# ---------------------------------------------------------------------------


def test_init_explain_emits_manual() -> None:
    """`init explain --no-pager` writes the long-form manual to stdout."""
    result = runner.invoke(app, ["init", "explain", "--no-pager"])
    assert result.exit_code == 0
    text = _strip_ansi(result.stdout)
    assert "Initialize workspace and configure endpoints" in text
    assert "agentops init" in text


# ---------------------------------------------------------------------------
# brand banner — startup splash shared with explain pages
# ---------------------------------------------------------------------------


def test_init_prints_brand_banner(tmp_path: Path, monkeypatch) -> None:
    """`agentops init` greets the user with the AgentOps brand banner."""
    # Force ASCII + no-color so the assertions are deterministic regardless of
    # the terminal that ran the test.
    monkeypatch.setenv("AGENTOPS_NO_UNICODE_BANNER", "1")
    monkeypatch.setenv("AGENTOPS_NO_COLOR", "1")
    monkeypatch.setenv("NO_COLOR", "1")

    result = runner.invoke(app, ["init", "--dir", str(tmp_path), "--no-prompt"])
    assert result.exit_code == 0
    text = _strip_ansi(result.stdout)
    # ASCII letterforms from _AGENTOPS_PLAIN_BANNER (figlet "Standard").
    assert "____ _____ _   _ _____" in text
    # The catchphrase, ASCII fallback variant.
    assert "Evaluate :: Ship :: Observe :: Own -- every Foundry agent." in text


def test_brand_tagline_is_used_by_explain_pages(monkeypatch) -> None:
    """The same tagline must appear on every explain page banner."""
    monkeypatch.setenv("AGENTOPS_NO_UNICODE_BANNER", "1")
    monkeypatch.setenv("AGENTOPS_NO_COLOR", "1")
    monkeypatch.setenv("NO_COLOR", "1")

    result = runner.invoke(app, ["init", "explain", "--no-pager"])
    assert result.exit_code == 0
    text = _strip_ansi(result.stdout)
    assert "Evaluate :: Ship :: Observe :: Own -- every Foundry agent." in text


# ---------------------------------------------------------------------------
# setup command no longer exists
# ---------------------------------------------------------------------------


def test_setup_command_is_gone() -> None:
    """`agentops setup` was merged into `agentops init` and no longer exists."""
    result = runner.invoke(app, ["setup", "--help"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# run_wizard on_answer callback
# ---------------------------------------------------------------------------


def test_run_wizard_calls_on_answer_for_each_validated_input(
    tmp_path: Path,
    monkeypatch,  # noqa: ANN001
) -> None:
    """`on_answer` is invoked once per validated, non-empty, changed field."""
    from agentops.services.setup_wizard import (
        ENV_KEY_APPINSIGHTS,
        ENV_KEY_PROJECT_ENDPOINT,
        run_wizard,
    )

    # Isolate from the developer shell so no value resolves from process env
    # and the wizard genuinely asks every question.
    monkeypatch.delenv(ENV_KEY_PROJECT_ENDPOINT, raising=False)
    monkeypatch.delenv(ENV_KEY_APPINSIGHTS, raising=False)

    smoke = tmp_path / ".agentops" / "data" / "smoke.jsonl"
    smoke.parent.mkdir(parents=True, exist_ok=True)
    smoke.write_text('{"input":"hi","expected":"hello"}\n', encoding="utf-8")

    replies = iter(
        [
            "https://acct.services.ai.azure.com/api/projects/p",  # project_endpoint
            "my-agent:1",  # agent
            ".agentops/data/smoke.jsonl",  # dataset
        ]
    )

    captured: list[tuple[str, str]] = []

    def _prompt(_question: str, _default):  # noqa: ANN001
        return next(replies)

    answers = run_wizard(
        tmp_path,
        prompt=_prompt,
        echo=lambda _msg: None,
        on_answer=lambda field, value: captured.append((field, value)),
    )

    assert answers.project_endpoint is not None
    assert captured == [
        ("project_endpoint", "https://acct.services.ai.azure.com/api/projects/p"),
        ("agent", "my-agent:1"),
        ("dataset", ".agentops/data/smoke.jsonl"),
    ]


def test_run_wizard_skips_questions_when_defaults_present(
    tmp_path: Path,
    monkeypatch,  # noqa: ANN001
) -> None:
    """By default the wizard MUST NOT prompt for values that are already set.

    This is the headless-friendly behaviour: a CI re-run should be a no-op
    (just confirmations), not a fresh round of prompts.
    """
    from agentops.services.setup_wizard import (
        ENV_KEY_APPINSIGHTS,
        ENV_KEY_PROJECT_ENDPOINT,
        run_wizard,
    )

    # Resolve defaults exclusively from persisted workspace files, not from the
    # developer's shell env.
    monkeypatch.delenv(ENV_KEY_PROJECT_ENDPOINT, raising=False)
    monkeypatch.delenv(ENV_KEY_APPINSIGHTS, raising=False)

    # Pre-populate agentops.yaml and the active azd env so all interactive
    # values resolve. App Insights may exist, but the wizard no longer manages
    # it interactively.
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: configured-agent:1\ndataset: .agentops/data/smoke.jsonl\n",
        encoding="utf-8",
    )
    smoke = tmp_path / ".agentops" / "data" / "smoke.jsonl"
    smoke.parent.mkdir(parents=True, exist_ok=True)
    smoke.write_text('{"input":"hi","expected":"hello"}\n', encoding="utf-8")
    azure_env = tmp_path / ".azure" / "dev" / ".env"
    azure_env.parent.mkdir(parents=True, exist_ok=True)
    azure_env.write_text(
        'AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="https://acct.services.ai.azure.com/api/projects/p"\n'
        'APPLICATIONINSIGHTS_CONNECTION_STRING="InstrumentationKey=abc;IngestionEndpoint=https://x"\n',
        encoding="utf-8",
    )
    (tmp_path / ".azure" / "config.json").write_text(
        '{"version": 1, "defaultEnvironment": "dev"}\n',
        encoding="utf-8",
    )

    prompt_calls: list[str] = []
    echo_lines: list[str] = []

    def _prompt(question: str, _default):  # noqa: ANN001
        prompt_calls.append(question)
        return ""

    run_wizard(tmp_path, prompt=_prompt, echo=echo_lines.append)

    # Zero questions asked — every default was satisfied.
    assert prompt_calls == [], (
        f"Wizard should not prompt when all defaults are present, asked: {prompt_calls}"
    )
    # All three interactive confirmation lines emitted, plus the closing hint.
    # The leading glyph is "✓" on UTF-8 stdouts and "*" on cp1252; accept either.
    confirmations = [
        line for line in echo_lines if line.startswith(("  ✓ ", "  * "))
    ]
    assert len(confirmations) == 3, echo_lines
    assert any("--reconfigure" in line for line in echo_lines), echo_lines
    assert "Application Insights" not in "\n".join(echo_lines)


def test_run_wizard_reconfigure_forces_questions_even_when_defaults_present(
    tmp_path: Path,
    monkeypatch,  # noqa: ANN001
) -> None:
    """`reconfigure=True` MUST prompt every question regardless of defaults."""
    from agentops.services.setup_wizard import (
        ENV_KEY_APPINSIGHTS,
        ENV_KEY_PROJECT_ENDPOINT,
        run_wizard,
    )

    monkeypatch.delenv(ENV_KEY_PROJECT_ENDPOINT, raising=False)
    monkeypatch.delenv(ENV_KEY_APPINSIGHTS, raising=False)

    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: configured-agent:1\ndataset: .agentops/data/smoke.jsonl\n",
        encoding="utf-8",
    )
    smoke = tmp_path / ".agentops" / "data" / "smoke.jsonl"
    smoke.parent.mkdir(parents=True, exist_ok=True)
    smoke.write_text('{"input":"hi","expected":"hello"}\n', encoding="utf-8")
    azure_env = tmp_path / ".azure" / "dev" / ".env"
    azure_env.parent.mkdir(parents=True, exist_ok=True)
    azure_env.write_text(
        'AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="https://acct.services.ai.azure.com/api/projects/p"\n'
        'APPLICATIONINSIGHTS_CONNECTION_STRING="InstrumentationKey=abc;IngestionEndpoint=https://x"\n',
        encoding="utf-8",
    )
    (tmp_path / ".azure" / "config.json").write_text(
        '{"version": 1, "defaultEnvironment": "dev"}\n',
        encoding="utf-8",
    )

    prompt_calls: list[str] = []

    def _prompt(question: str, _default):  # noqa: ANN001
        prompt_calls.append(question)
        return ""  # accept default

    run_wizard(
        tmp_path,
        prompt=_prompt,
        echo=lambda _msg: None,
        reconfigure=True,
    )

    assert prompt_calls == [
        "Foundry project endpoint",
        "Agent / orchestrator endpoint",
        "Dataset path",
    ]
