from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.services.telemetry_import import TelemetryImportPreview


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
    assert "pip install agentops-accelerator" not in stripped


def test_workflow_command_exposes_analyze_and_generate() -> None:
    result = runner.invoke(app, ["workflow", "--help"])

    assert result.exit_code == 0
    stripped = _strip_ansi(result.stdout)
    assert "analyze" in stripped
    assert "generate" in stripped


def test_agent_command_group_wired() -> None:
    """`agentops agent` exposes the watchdog subcommands."""
    result = runner.invoke(app, ["agent", "--help"])
    assert result.exit_code == 0
    stripped = _strip_ansi(result.stdout)
    assert "analyze" in stripped
    assert "serve" in stripped


def test_telemetry_validate_uses_named_import(tmp_path, monkeypatch) -> None:
    config = tmp_path / "agentops.yaml"
    config.write_text(
        "\n".join(
            [
                "version: 1",
                "agent: support-agent:1",
                "dataset: .agentops/data/smoke.jsonl",
                "telemetry_imports:",
                "  - name: prod",
                "    target: log-analytics",
                "    workspace_id: workspace",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "agentops.services.telemetry_import.validate_telemetry_import",
        lambda _item: [],
    )

    result = runner.invoke(app, ["telemetry", "validate", "prod", "--config", str(config)])

    assert result.exit_code == 0, result.output
    assert "prod" in result.output
    assert "valid" in result.output


def test_telemetry_preview_prints_service_preview(tmp_path, monkeypatch) -> None:
    config = tmp_path / "agentops.yaml"
    config.write_text(
        "version: 1\n"
        "agent: support-agent:1\n"
        "dataset: .agentops/data/smoke.jsonl\n"
        "telemetry_imports:\n"
        "  - name: prod\n"
        "    target: log-analytics\n"
        "    workspace_id: workspace\n",
        encoding="utf-8",
    )

    def fake_preview(item, *, rows=None, apply=False):
        return TelemetryImportPreview(
            config=item,
            output_path=tmp_path / "prod.jsonl",
            manifest_path=tmp_path / "prod-manifest.json",
            rows=[{"input": "hello", "response": "world"}],
        )

    monkeypatch.setattr(
        "agentops.services.telemetry_import.preview_telemetry_import",
        fake_preview,
    )

    result = runner.invoke(
        app,
        ["telemetry", "preview", "prod", "--rows", "1", "--config", str(config)],
    )

    assert result.exit_code == 0, result.output
    assert "AgentOps telemetry import" in result.output
    assert "hello" in result.output


def test_telemetry_import_requires_apply_to_write(tmp_path, monkeypatch) -> None:
    config = tmp_path / "agentops.yaml"
    config.write_text(
        "version: 1\n"
        "agent: support-agent:1\n"
        "dataset: .agentops/data/smoke.jsonl\n"
        "telemetry_imports:\n"
        "  - name: prod\n"
        "    target: log-analytics\n"
        "    workspace_id: workspace\n",
        encoding="utf-8",
    )
    calls = []

    def fake_preview(item, *, rows=None, apply=False):
        calls.append(apply)
        return TelemetryImportPreview(
            config=item,
            output_path=tmp_path / "prod.jsonl",
            manifest_path=tmp_path / "prod-manifest.json",
            rows=[],
        )

    monkeypatch.setattr(
        "agentops.services.telemetry_import.preview_telemetry_import",
        fake_preview,
    )

    result = runner.invoke(app, ["telemetry", "import", "prod", "--config", str(config)])

    assert result.exit_code == 0, result.output
    assert calls == [False]
    assert "Dry run only" in result.output
