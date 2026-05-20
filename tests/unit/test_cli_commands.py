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


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "agentops" in result.stdout.lower()


def test_report_help_only_exposes_generate() -> None:
    result = runner.invoke(app, ["report", "--help"])

    assert result.exit_code == 0
    stripped = _strip_ansi(result.stdout)
    assert "generate" in stripped
    assert "show" not in stripped
    assert "export" not in stripped


def test_eval_help_does_not_expose_compare_subcommand() -> None:
    result = runner.invoke(app, ["eval", "--help"])

    assert result.exit_code == 0
    stripped = _strip_ansi(result.stdout)
    assert "compare" not in stripped


def test_planned_command_groups_removed() -> None:
    """Stub command groups (monitor/model/dataset/config) are gone in 1.0.

    `cockpit` is now the real command that opens the local UI."""
    for group in ("monitor", "model", "dataset", "config"):
        result = runner.invoke(app, [group, "--help"])
        assert result.exit_code != 0, f"unexpected: 'agentops {group}' is still wired"


def test_cockpit_command_wired() -> None:
    """`agentops cockpit` exposes the local cockpit server."""
    result = runner.invoke(app, ["cockpit", "--help"])
    assert result.exit_code == 0
    stripped = _strip_ansi(result.stdout)
    assert "cockpit" in stripped.lower()
    assert "Reads ``" not in stripped
    assert "pip install agentops-toolkit" not in stripped


def test_agent_command_group_wired() -> None:
    """`agentops agent` exposes the watchdog subcommands."""
    result = runner.invoke(app, ["agent", "--help"])
    assert result.exit_code == 0
    stripped = _strip_ansi(result.stdout)
    assert "analyze" in stripped
    assert "serve" in stripped
