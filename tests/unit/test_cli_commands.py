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
    assert "analyze" in stripped
    assert "promote-traces" in stripped
    assert "run" in stripped
    assert "compare" not in stripped


def test_removed_command_groups_are_not_wired() -> None:
    """Retired and former stub command groups are absent.

    `cockpit` is now the real command that opens the local UI."""
    for group in ("monitor", "model", "dataset", "config", "telemetry"):
        result = runner.invoke(app, [group, "--help"])
        assert result.exit_code != 0, f"unexpected: 'agentops {group}' is still wired"


def test_cockpit_command_wired() -> None:
    """`agentops cockpit` exposes the local cockpit server."""
    result = runner.invoke(app, ["cockpit", "--help"])
    assert result.exit_code == 0
    stripped = _strip_ansi(result.stdout)
    assert "cockpit" in stripped.lower()
    assert "Reads ``" not in stripped
    assert "pip install agentops-accelerator" not in stripped


def test_workflow_command_exposes_analyze_and_generate() -> None:
    result = runner.invoke(app, ["workflow", "--help"])

    assert result.exit_code == 0
    stripped = _strip_ansi(result.stdout)
    assert "analyze" in stripped
    assert "generate" in stripped


def test_agent_command_group_removed() -> None:
    """The obsolete `agentops agent` group (serve/register) is gone.

    Issue #451 removed the GitHub App-based Copilot Extension server and the
    Entra Agent ID registration command with no compatibility alias.
    """
    group_help = runner.invoke(app, ["agent", "--help"])
    assert group_help.exit_code != 0

    serve = runner.invoke(app, ["agent", "serve"])
    assert serve.exit_code != 0

    register = runner.invoke(app, ["agent", "register"])
    assert register.exit_code != 0
