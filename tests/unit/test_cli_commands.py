from typer.testing import CliRunner

from agentops.cli.app import app


runner = CliRunner()


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences for reliable text matching."""
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_init_help_exposes_path_alias() -> None:
    result = runner.invoke(app, ["init", "--help"])

    assert result.exit_code == 0
    assert "--path" in _strip_ansi(result.stdout)


def test_eval_compare_rejects_wrong_run_count() -> None:
    result = runner.invoke(app, ["eval", "compare", "--runs", "only_one"])

    assert result.exit_code == 1
    assert (
        "at least two" in result.stdout.lower()
        or "at least two" in (result.stderr or "").lower()
    )


def test_model_list_is_planned_stub() -> None:
    result = runner.invoke(app, ["model", "list"])

    assert result.exit_code == 1
    assert "planned but not implemented" in result.stdout.lower()


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "agentops" in result.stdout.lower()


def test_report_help_exposes_available_and_planned_commands() -> None:
    result = runner.invoke(app, ["report", "--help"])

    assert result.exit_code == 0
    stripped = _strip_ansi(result.stdout)
    assert "generate" in stripped
    assert "show" in stripped
    assert "export" in stripped


def test_config_validate_accepts_valid_run_yaml(tmp_path) -> None:
    cfg = tmp_path / "run.yaml"
    cfg.write_text(
        """\
version: 1
target:
  type: agent
  hosting: local
  execution_mode: local
  local:
    callable: my_module:my_fn
bundle:
  name: conversational_agent_baseline
dataset:
  name: smoke-conversational
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["config", "validate", "-c", str(cfg)])

    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    assert "is valid" in result.stdout


def test_config_validate_reports_missing_file() -> None:
    result = runner.invoke(app, ["config", "validate", "-c", "/no/such/file.yaml"])

    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "not found" in combined.lower()


def test_config_validate_reports_invalid_schema(tmp_path) -> None:
    cfg = tmp_path / "run.yaml"
    cfg.write_text("version: 1\nbackend: legacy\n", encoding="utf-8")

    result = runner.invoke(app, ["config", "validate", "-c", str(cfg)])

    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "invalid" in combined.lower() or "backend" in combined.lower()
