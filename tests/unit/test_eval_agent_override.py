"""`agentops eval run` must be able to retarget the agent version (#388).

``agentops.yaml`` pins a fully-qualified Foundry agent that includes a version
segment. Generated pipelines publish a new version and then evaluate, so
without an override the gate scores the previous version and a regression the
run just introduced cannot fail it. ``--agent`` and ``$AGENTOPS_AGENT`` give CI
a way to point the gate at the version it actually produced, without CI writing
to the tracked config.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.core.results import RunResult, RunSummary, TargetInfo

runner = CliRunner()

_HOSTED = (
    "https://acct.services.ai.azure.com/api/projects/proj/agents/helpdeskbot/versions/11"
)


def _passing_result() -> RunResult:
    return RunResult(
        started_at="2026-06-01T00:00:00+00:00",
        finished_at="2026-06-01T00:01:00+00:00",
        duration_seconds=60.0,
        target=TargetInfo(kind="foundry_hosted", raw=_HOSTED),
        dataset_path="dataset.jsonl",
        evaluators=[],
        rows=[],
        aggregate_metrics={},
        thresholds=[],
        summary=RunSummary(
            items_total=0,
            items_passed_all=0,
            items_pass_rate=1.0,
            thresholds_total=0,
            thresholds_passed=0,
            threshold_pass_rate=1.0,
            overall_passed=True,
        ),
    )


def _write_hosted_config(tmp_path: Path) -> Path:
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"input": "hi", "expected": "hi"}), encoding="utf-8")
    config = tmp_path / "agentops.yaml"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "agent": _HOSTED,
                "dataset": str(dataset),
                "protocol": "responses",
            }
        ),
        encoding="utf-8",
    )
    return config


def _invoke(tmp_path: Path, monkeypatch, extra_args: list[str]) -> tuple[object, list]:
    config = _write_hosted_config(tmp_path)
    output = tmp_path / "out"
    output.mkdir()

    seen: list = []

    import agentops.pipeline.orchestrator as orch

    def fake_run(cfg, options=None):
        seen.append(options)
        return _passing_result()

    monkeypatch.setattr(orch, "run_evaluation", fake_run)

    result = runner.invoke(
        app,
        ["eval", "run", "--config", str(config), "--output", str(output), *extra_args],
    )
    return result, seen


def test_agent_flag_replaces_pinned_version(tmp_path, monkeypatch) -> None:
    result, seen = _invoke(tmp_path, monkeypatch, ["--agent", "12"])

    assert result.exit_code == 0, result.output
    assert seen and seen[0].agent_override is not None
    assert seen[0].agent_override.endswith("/agents/helpdeskbot/versions/12")
    assert "Agent override" in result.output


def test_env_var_replaces_pinned_version(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTOPS_AGENT", "14")
    result, seen = _invoke(tmp_path, monkeypatch, [])

    assert result.exit_code == 0, result.output
    assert seen[0].agent_override.endswith("/agents/helpdeskbot/versions/14")


def test_flag_wins_over_env_var(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTOPS_AGENT", "14")
    result, seen = _invoke(tmp_path, monkeypatch, ["--agent", "12"])

    assert result.exit_code == 0, result.output
    assert seen[0].agent_override.endswith("/versions/12")


def test_no_override_leaves_the_pinned_agent_alone(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AGENTOPS_AGENT", raising=False)
    result, seen = _invoke(tmp_path, monkeypatch, [])

    assert result.exit_code == 0, result.output
    assert seen[0].agent_override is None
    assert "Agent override" not in result.output


def test_unexpanded_ado_variable_is_ignored(tmp_path, monkeypatch) -> None:
    """Azure DevOps leaves `$(NAME)` verbatim when the variable is undefined."""

    monkeypatch.setenv("AGENTOPS_AGENT", "$(AGENTOPS_AGENT)")
    result, seen = _invoke(tmp_path, monkeypatch, [])

    assert result.exit_code == 0, result.output
    assert seen[0].agent_override is None


def test_full_agent_reference_override_is_used_verbatim(tmp_path, monkeypatch) -> None:
    other = "https://acct.services.ai.azure.com/api/projects/other/agents/bot/versions/3"
    result, seen = _invoke(tmp_path, monkeypatch, ["--agent", other])

    assert result.exit_code == 0, result.output
    assert seen[0].agent_override == other


def test_unusable_override_fails_loudly(tmp_path, monkeypatch) -> None:
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"input": "hi", "expected": "hi"}), encoding="utf-8")
    config = tmp_path / "agentops.yaml"
    config.write_text(
        json.dumps({"version": 1, "agent": "model:gpt-4o", "dataset": str(dataset)}),
        encoding="utf-8",
    )
    output = tmp_path / "out"
    output.mkdir()

    import agentops.pipeline.orchestrator as orch

    monkeypatch.setattr(orch, "run_evaluation", lambda *a, **k: _passing_result())

    result = runner.invoke(
        app,
        [
            "eval",
            "run",
            "--config",
            str(config),
            "--output",
            str(output),
            "--agent",
            "12",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "no version segment" in result.output
