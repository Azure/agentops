"""CLI tests for `agentops doctor`."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentops.cli.app import app

runner = CliRunner()


def _seed_regression(workspace: Path) -> None:
    root = workspace / ".agentops" / "results"
    root.mkdir(parents=True, exist_ok=True)
    for run_id, ts, coh in [
        ("run-1", "2024-05-01T10:00:00Z", 4.5),
        ("run-2", "2024-05-02T10:00:00Z", 4.5),
        ("run-3", "2024-05-03T10:00:00Z", 2.5),
    ]:
        run_dir = root / run_id
        run_dir.mkdir(exist_ok=True)
        (run_dir / "results.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "timestamp": ts,
                    "metrics": {"coherence": coh},
                    "summary": {
                        "run_pass": coh >= 4.0,
                        "items_total": 3,
                        "items_passed_all": 3 if coh >= 4.0 else 1,
                    },
                }
            ),
            encoding="utf-8",
        )


def _agent_yaml(disable_remote: bool = True) -> str:
    return (
        "version: 1\n"
        "sources:\n"
        "  results_history:\n"
        "    enabled: true\n"
        "    path: .agentops/results\n"
        "  azure_monitor:\n"
        f"    enabled: {'false' if disable_remote else 'true'}\n"
        "  foundry_control:\n"
        f"    enabled: {'false' if disable_remote else 'true'}\n"
    )


def test_doctor_reports_regression_and_exits_two(tmp_path: Path) -> None:
    _seed_regression(tmp_path)
    (tmp_path / ".agentops" / "agent.yaml").write_text(
        _agent_yaml(), encoding="utf-8"
    )

    result = runner.invoke(
        app,
        [
            "doctor",
            "--workspace",
            str(tmp_path),
            "--severity-fail",
            "warning",
            "--no-preflight",
        ],
    )

    assert result.exit_code == 2, result.stdout
    report_path = tmp_path / ".agentops" / "agent" / "report.md"
    assert report_path.exists()
    body = report_path.read_text(encoding="utf-8")
    assert "regression.coherence" in body


def test_doctor_no_findings_exits_zero(tmp_path: Path) -> None:
    # Empty workspace -> no runs -> no findings.
    (tmp_path / ".agentops").mkdir()
    (tmp_path / ".agentops" / "agent.yaml").write_text(
        _agent_yaml(), encoding="utf-8"
    )
    result = runner.invoke(app, ["doctor", "--workspace", str(tmp_path), "--no-preflight"])
    assert result.exit_code == 0, result.stdout


def test_doctor_rejects_invalid_severity(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "doctor",
            "--workspace",
            str(tmp_path),
            "--severity-fail",
            "wat",
        ],
    )
    assert result.exit_code == 1


def test_doctor_can_write_release_evidence_pack(tmp_path: Path) -> None:
    (tmp_path / ".agentops").mkdir()
    (tmp_path / ".agentops" / "agent.yaml").write_text(
        _agent_yaml(), encoding="utf-8"
    )
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: support-agent:1\ndataset: .agentops/data/smoke.jsonl\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "doctor",
            "--workspace",
            str(tmp_path),
            "--no-preflight",
            "--evidence-pack",
        ],
    )

    assert result.exit_code == 0, result.stdout
    evidence_json = tmp_path / ".agentops" / "release" / "latest" / "evidence.json"
    evidence_md = tmp_path / ".agentops" / "release" / "latest" / "evidence.md"
    assert evidence_json.exists()
    assert evidence_md.exists()
    payload = json.loads(evidence_json.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["status"] == "blocked"


def test_agent_analyze_alias_is_gone(tmp_path: Path) -> None:
    """The legacy `agentops agent analyze` alias was removed in the
    rename — invoking it should fail with Typer's 'no such command' exit
    code (2), not run a hidden alias."""
    result = runner.invoke(
        app, ["agent", "analyze", "--workspace", str(tmp_path)]
    )
    assert result.exit_code != 0
