"""Regression guards proving legacy azd workspaces are not opted into the new surface."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from agentops.cli.app import app  # noqa: E402
from agentops.pipeline import azd_runner  # noqa: E402
from fixtures.azd_stub import AzdStub, extension_list_json  # noqa: E402


runner = CliRunner()

LEGACY_EXTENSION = "azure.ai.agents"


def _write_legacy_workspace(workspace: Path) -> Path:
    (workspace / "dataset.jsonl").write_text(
        json.dumps({"input": "hello", "expected": "hi"}) + "\n",
        encoding="utf-8",
    )
    (workspace / "agentops.yaml").write_text(
        """
version: 1
agent: travel-agent:1
dataset: dataset.jsonl
execution: azd
""".lstrip(),
        encoding="utf-8",
    )
    (workspace / "eval.yaml").write_text(
        """
name: travel-agent-eval
agent:
  name: travel-agent
  kind: prompt-agent
  version: "1"
dataset_reference:
  name: smoke
  version: "1"
  local_uri: dataset.jsonl
evaluators:
  - name: builtin.coherence
""".lstrip(),
        encoding="utf-8",
    )
    return workspace / "agentops.yaml"


def _script_legacy_success(stub: AzdStub) -> AzdStub:
    stub.expect("azd", "version")
    stub.expect(
        "extension",
        "list",
        "--installed",
        stdout=extension_list_json(LEGACY_EXTENSION),
    )
    stub.expect(
        "ai",
        "agent",
        "eval",
        "run",
        stdout="Eval:       eval-1\nRun:        run-1\nStatus:     Completed\n",
    )
    stub.expect_output_file(
        "ai",
        "agent",
        "eval",
        "show",
        flag="--out-file",
        payload={
            "id": "run-1",
            "eval_id": "eval-1",
            "status": "completed",
            "result_counts": {"total": 1},
            "metrics": [{"name": "builtin.coherence", "score": 4.0}],
        },
        stdout="exported",
    )
    return stub


def _install_legacy_stub(stub: AzdStub, monkeypatch) -> AzdStub:
    stub.install(monkeypatch)

    def _fake_run_command(
        command,
        *,
        cwd,
        timeout_seconds,
        progress=None,
        progress_label="command",
    ):
        return stub(command)

    monkeypatch.setattr(azd_runner, "_run_command", _fake_run_command)
    return stub


def _is_legacy_eval_command(argv: list[str]) -> bool:
    return argv[:5] == ["azd", "--no-prompt", "ai", "agent", "eval"]


def _is_current_surface_eval_command(argv: list[str]) -> bool:
    return argv[:4] == ["azd", "--no-prompt", "ai", "eval"]


def test_legacy_workspace_without_evals_runs_with_unchanged_normalization(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = _write_legacy_workspace(tmp_path)
    assert not (tmp_path / "evals").exists()
    out = tmp_path / "out"
    stub = _install_legacy_stub(_script_legacy_success(AzdStub()), monkeypatch)

    result = runner.invoke(
        app,
        ["eval", "run", "--config", str(config_path), "--output", str(out)],
    )

    assert result.exit_code == 0, result.output
    stub.assert_exhausted()
    payload = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert payload["config"]["result_granularity"] == "aggregate"
    assert "surface" not in payload["config"]["azd_evaluation"]
    assert payload["rows"] == []
    assert payload["aggregate_metrics"] == {"builtin.coherence": 4.0}
    assert "Warning" not in result.output
    assert "Error" not in result.output
    assert "azd ai eval" not in result.output
    assert "current azd evaluation surface" not in result.output


def test_legacy_workspace_analyze_does_not_mention_current_surface(tmp_path: Path) -> None:
    _write_legacy_workspace(tmp_path)
    assert not (tmp_path / "evals").exists()

    result = runner.invoke(app, ["eval", "analyze", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "Warnings" not in result.output
    assert "Warning" not in result.output
    assert "Error" not in result.output
    assert "azd ai eval" not in result.output
    assert "azure.ai.evaluations" not in result.output
    assert "evals/azure.eval.yaml" not in result.output
    assert "current azd evaluation surface" not in result.output


def test_legacy_workspace_dispatches_only_to_legacy_azd_commands(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = _write_legacy_workspace(tmp_path)
    stub = _install_legacy_stub(_script_legacy_success(AzdStub()), monkeypatch)

    result = runner.invoke(
        app,
        ["eval", "run", "--config", str(config_path), "--output", str(tmp_path / "out")],
    )

    assert result.exit_code == 0, result.output
    eval_commands = [argv for argv in stub.calls if argv and argv[0] == "azd"]
    assert [argv for argv in eval_commands if _is_legacy_eval_command(argv)] == [
        [
            "azd",
            "--no-prompt",
            "ai",
            "agent",
            "eval",
            "run",
            "--config",
            str(tmp_path / "eval.yaml"),
            "--output",
            "json",
        ],
        [
            "azd",
            "--no-prompt",
            "ai",
            "agent",
            "eval",
            "show",
            "eval-1",
            "--eval-run-id",
            "run-1",
            "--out-file",
            next(
                argv[argv.index("--out-file") + 1]
                for argv in eval_commands
                if _is_legacy_eval_command(argv) and "show" in argv
            ),
        ],
    ]
    assert not [argv for argv in eval_commands if _is_current_surface_eval_command(argv)]
