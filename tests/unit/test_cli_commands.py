from typer.testing import CliRunner

from agentops.cli.app import app


runner = CliRunner()


def test_init_help_exposes_path_alias() -> None:
    result = runner.invoke(app, ["init", "--help"])

    assert result.exit_code == 0
    assert "--path" in result.stdout


def test_eval_compare_is_planned_stub() -> None:
    result = runner.invoke(app, ["eval", "compare", "--runs", "r1,r2"])

    assert result.exit_code == 1
    assert "planned but not implemented" in result.stdout.lower()


def test_trace_init_is_planned_stub() -> None:
    result = runner.invoke(app, ["trace", "init"])

    assert result.exit_code == 1
    assert "planned but not implemented" in result.stdout.lower()


def test_model_list_is_planned_stub() -> None:
    result = runner.invoke(app, ["model", "list"])

    assert result.exit_code == 1
    assert "planned but not implemented" in result.stdout.lower()


def test_report_help_exposes_available_and_planned_commands() -> None:
    result = runner.invoke(app, ["report", "--help"])

    assert result.exit_code == 0
    assert "--in" in result.stdout
    assert "show" in result.stdout
    assert "export" in result.stdout
