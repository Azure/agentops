"""Tests for ASSERT and Red Team runner services + CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.core.agentops_config import (
    AgentOpsConfig,
    AssertRunConfig,
    RedTeamRunConfig,
)
from agentops.services import assert_runner, redteam_runner


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


def test_assert_run_config_alias_in_yaml():
    cfg = AgentOpsConfig.model_validate(
        {
            "version": 1,
            "agent": "my-agent:1",
            "dataset": "data.jsonl",
            "assert": {"config": "assert/eval_config.yaml"},
        }
    )
    assert cfg.assert_run is not None
    assert isinstance(cfg.assert_run, AssertRunConfig)
    assert str(cfg.assert_run.config).endswith("eval_config.yaml")
    assert cfg.assert_run.fail_on_violations is True


def test_redteam_run_config_alias_in_yaml_with_defaults():
    cfg = AgentOpsConfig.model_validate(
        {
            "version": 1,
            "agent": "my-agent:1",
            "dataset": "data.jsonl",
            "redteam": {"target": {"model_deployment": "gpt-4o-mini"}},
        }
    )
    assert cfg.redteam_run is not None
    assert isinstance(cfg.redteam_run, RedTeamRunConfig)
    assert cfg.redteam_run.target == {"model_deployment": "gpt-4o-mini"}
    assert cfg.redteam_run.num_objectives == 10
    assert cfg.redteam_run.fail_on_attack_success_rate == 0.2
    assert "violence" in cfg.redteam_run.risk_categories
    assert "base64" in cfg.redteam_run.attack_strategies


def test_redteam_run_config_rejects_invalid_threshold():
    with pytest.raises(Exception):
        RedTeamRunConfig.model_validate(
            {"target": {"model_deployment": "x"}, "fail_on_attack_success_rate": 1.5}
        )


# ---------------------------------------------------------------------------
# ASSERT runner unit tests
# ---------------------------------------------------------------------------


def _write_assert_layout(
    base: Path,
    *,
    suite: str = "demo",
    run_id: str = "r1",
    metrics: dict | None = None,
    scores: list[dict] | None = None,
) -> Path:
    run_dir = base / "artifacts" / "results" / suite / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if metrics is not None:
        (run_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    if scores is not None:
        (run_dir / "scores.jsonl").write_text(
            "\n".join(json.dumps(s) for s in scores), encoding="utf-8"
        )
    return run_dir


def test_assert_locate_run_output_prefers_explicit_suite_and_run(tmp_path: Path):
    _write_assert_layout(tmp_path, suite="demo", run_id="r1", metrics={}, scores=[])
    found = assert_runner._locate_run_output(
        results_dir=(tmp_path / "artifacts" / "results").resolve(),
        suite="demo",
        run_id="r1",
    )
    assert found is not None
    assert found.name == "r1"


def test_assert_locate_run_output_falls_back_to_latest(tmp_path: Path):
    _write_assert_layout(tmp_path, suite="demo", run_id="older", metrics={}, scores=[])
    newer = _write_assert_layout(tmp_path, suite="demo", run_id="newer", metrics={}, scores=[])
    # Bump mtime of newer
    newer.touch()
    found = assert_runner._locate_run_output(
        results_dir=(tmp_path / "artifacts" / "results").resolve(),
        suite=None,
        run_id=None,
    )
    assert found is not None
    assert found.parent.name == "demo"


def test_assert_summarize_dimensions_counts_violations(tmp_path: Path):
    run_dir = _write_assert_layout(
        tmp_path,
        suite="demo",
        run_id="r1",
        metrics={},
        scores=[
            {"dimension": "pii_leak", "verdict": "pass"},
            {"dimension": "pii_leak", "verdict": "violation"},
            {"dimension": "jailbreak", "verdict": "fail"},
            {"dimension": "jailbreak", "verdict": "ok"},
            {"dimension": "jailbreak", "verdict": "unknown_state"},
        ],
    )
    summary = assert_runner._summarize_dimensions(run_dir)
    assert summary["pii_leak"]["total"] == 2
    assert summary["pii_leak"]["violations"] == 1
    assert summary["jailbreak"]["violations"] == 1
    assert summary["jailbreak"]["passes"] == 1
    assert summary["jailbreak"]["other"] == 1


def test_assert_aggregate_totals_uses_dimensions(tmp_path: Path):
    metrics: dict[str, Any] = {}
    dims = {
        "a": {"total": 5, "violations": 2, "passes": 3, "other": 0},
        "b": {"total": 5, "violations": 0, "passes": 5, "other": 0},
    }
    totals = assert_runner._aggregate_totals(metrics, dims)
    # ASSERT design: total = max across dimensions (each case is judged on
    # every dimension), failed = sum of violations.
    assert totals["total"] == 5
    assert totals["failed"] == 2
    assert totals["passed"] == 3
    assert totals["pass_rate"] == pytest.approx(3 / 5)


def test_run_assert_invokes_cli_and_writes_normalized(tmp_path: Path, monkeypatch):
    eval_cfg = tmp_path / "assert" / "eval_config.yaml"
    eval_cfg.parent.mkdir(parents=True)
    eval_cfg.write_text("suite_id: demo\nrun_id: r1\n", encoding="utf-8")
    _write_assert_layout(
        tmp_path,
        suite="demo",
        run_id="r1",
        metrics={"pass_rate": 0.8},
        scores=[{"dimension": "x", "verdict": "pass"}],
    )

    monkeypatch.setattr(assert_runner, "is_assert_installed", lambda executable="assert-ai": True)
    fake_completed = mock.Mock(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(assert_runner.subprocess, "run", mock.Mock(return_value=fake_completed))

    result = assert_runner.run_assert(
        workspace=tmp_path,
        config_path=eval_cfg,
        results_dir=tmp_path / "artifacts" / "results",
    )
    assert result.suite == "demo"
    assert result.run_id == "r1"
    assert result.total_cases == 1
    assert result.has_violations is False
    assert result.normalized_path is not None
    payload = json.loads(Path(result.normalized_path).read_text(encoding="utf-8"))
    assert payload["suite"] == "demo"


def test_run_assert_raises_when_cli_missing(tmp_path: Path, monkeypatch):
    eval_cfg = tmp_path / "ec.yaml"
    eval_cfg.write_text("suite: x\n", encoding="utf-8")
    monkeypatch.setattr(assert_runner, "is_assert_installed", lambda executable="assert-ai": False)
    with pytest.raises(assert_runner.AssertRunnerError):
        assert_runner.run_assert(
            workspace=tmp_path,
            config_path=eval_cfg,
        )


# ---------------------------------------------------------------------------
# Red Team runner unit tests
# ---------------------------------------------------------------------------


def test_redteam_aggregate_totals_zero_records():
    totals = redteam_runner._aggregate_totals([])
    assert totals == {"total": 0, "successful": 0, "attack_success_rate": 0.0}


def test_redteam_aggregate_totals_mixed():
    records = [
        {"risk_category": "a", "attack_strategy": "x", "successful": True},
        {"risk_category": "a", "attack_strategy": "x", "successful": False},
        {"risk_category": "b", "attack_strategy": "y", "successful": True},
        {"risk_category": "b", "attack_strategy": "y", "successful": True},
    ]
    totals = redteam_runner._aggregate_totals(records)
    assert totals["total"] == 4
    assert totals["successful"] == 3
    assert totals["attack_success_rate"] == 0.75


def test_redteam_summarize_by_axis_per_category():
    records = [
        {"risk_category": "violence", "attack_strategy": "base64", "successful": True},
        {"risk_category": "violence", "attack_strategy": "rot13", "successful": False},
        {"risk_category": "hate", "attack_strategy": "base64", "successful": False},
    ]
    by_cat = redteam_runner._summarize_by_axis(records, axis="risk_category")
    assert by_cat["violence"]["total"] == 2
    assert by_cat["violence"]["successful"] == 1
    assert by_cat["violence"]["attack_success_rate"] == 0.5
    assert by_cat["hate"]["successful"] == 0


def test_redteam_records_from_payload_attack_details():
    payload = {
        "attack_details": [
            {
                "risk_category": "violence",
                "attack_strategy": "base64",
                "attack_success": True,
            },
            {
                "category": "hate",
                "strategy": "rot13",
                "verdict": "pass",
            },
        ]
    }
    records = redteam_runner._records_from_payload(payload)
    assert len(records) == 2
    assert records[0]["risk_category"] == "violence"
    assert records[0]["successful"] is True
    assert records[1]["successful"] is False


def test_redteam_build_target_callback_requires_endpoint(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    with pytest.raises(redteam_runner.RedTeamRunnerError):
        redteam_runner._build_target_callback({"model_deployment": "x"})


def test_redteam_build_target_callback_with_endpoint(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com")
    callback = redteam_runner._build_target_callback({"model_deployment": "gpt-4o-mini"})
    assert callback["azure_deployment"] == "gpt-4o-mini"
    assert callback["azure_endpoint"].startswith("https://")


def test_redteam_build_target_callback_unsupported():
    with pytest.raises(redteam_runner.RedTeamRunnerError):
        redteam_runner._build_target_callback({"foo": "bar"})


def test_run_redteam_raises_when_sdk_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(redteam_runner, "is_redteam_installed", lambda: False)
    with pytest.raises(redteam_runner.RedTeamRunnerError):
        redteam_runner.run_redteam(
            workspace=tmp_path,
            target={"model_deployment": "gpt-4o-mini"},
            risk_categories=["violence"],
            attack_strategies=["base64"],
        )


def test_run_redteam_normalizes_and_gates(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(redteam_runner, "is_redteam_installed", lambda: True)

    records = [
        {"risk_category": "violence", "attack_strategy": "base64", "successful": True},
        {"risk_category": "violence", "attack_strategy": "base64", "successful": True},
        {"risk_category": "hate", "attack_strategy": "rot13", "successful": False},
    ]
    monkeypatch.setattr(
        redteam_runner,
        "_invoke_redteam_scan",
        lambda **_: (records, {"attack_details": records}),
    )

    result = redteam_runner.run_redteam(
        workspace=tmp_path,
        target={"model_deployment": "gpt-4o-mini"},
        risk_categories=["violence", "hate"],
        attack_strategies=["base64", "rot13"],
        num_objectives=3,
        fail_threshold=0.5,
    )
    assert result.total_attempts == 3
    assert result.successful_attacks == 2
    assert result.attack_success_rate == pytest.approx(2 / 3, abs=1e-3)
    assert result.has_violations is True
    # default normalized path lives under workspace
    assert Path(result.output_path).exists()
    payload = json.loads(Path(result.output_path).read_text(encoding="utf-8"))
    assert payload["successful_attacks"] == 2


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _seed_minimal_workspace(tmp_path: Path, *, extra: dict | None = None) -> Path:
    cfg_path = tmp_path / "agentops.yaml"
    base = {
        "version": 1,
        "agent": "my-agent:1",
        "dataset": "data.jsonl",
    }
    if extra:
        base.update(extra)
    import yaml as _yaml

    cfg_path.write_text(_yaml.safe_dump(base), encoding="utf-8")
    (tmp_path / "data.jsonl").write_text("", encoding="utf-8")
    return cfg_path


def test_cli_assert_run_missing_config_block(tmp_path: Path, runner: CliRunner, monkeypatch):
    cfg = _seed_minimal_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["assert", "run", "--config", str(cfg)])
    assert result.exit_code == 1
    assert "no ASSERT configuration found" in result.output


def test_cli_assert_run_missing_cli(tmp_path: Path, runner: CliRunner, monkeypatch):
    eval_cfg = tmp_path / "assert" / "eval_config.yaml"
    eval_cfg.parent.mkdir(parents=True)
    eval_cfg.write_text("suite: x\nrun: r\n", encoding="utf-8")
    cfg = _seed_minimal_workspace(
        tmp_path, extra={"assert": {"config": "assert/eval_config.yaml"}}
    )
    monkeypatch.chdir(tmp_path)
    from agentops.cli import app as app_module

    monkeypatch.setattr(
        app_module, "_resolve_eval_config_path", lambda c: cfg if c is None else c
    )
    monkeypatch.setattr(
        "agentops.services.assert_runner.is_assert_installed", lambda *a, **k: False
    )
    result = runner.invoke(app, ["assert", "run", "--config", str(cfg)])
    assert result.exit_code == 1
    assert "assert-ai" in result.output


def test_cli_redteam_run_missing_config_block(tmp_path: Path, runner: CliRunner, monkeypatch):
    cfg = _seed_minimal_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["redteam", "run", "--config", str(cfg)])
    assert result.exit_code == 1
    assert "no Red Team configuration found" in result.output


def test_cli_redteam_run_missing_sdk(tmp_path: Path, runner: CliRunner, monkeypatch):
    cfg = _seed_minimal_workspace(
        tmp_path,
        extra={"redteam": {"target": {"model_deployment": "gpt-4o-mini"}}},
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "agentops.services.redteam_runner.is_redteam_installed", lambda: False
    )
    result = runner.invoke(app, ["redteam", "run", "--config", str(cfg)])
    assert result.exit_code == 1
    assert "Red Team SDK" in result.output


def test_cli_assert_explain_runs(runner: CliRunner):
    result = runner.invoke(app, ["assert", "explain", "--no-pager"])
    assert result.exit_code == 0
    assert "ASSERT" in result.output


def test_cli_redteam_explain_runs(runner: CliRunner):
    result = runner.invoke(app, ["redteam", "explain", "--no-pager"])
    assert result.exit_code == 0
    assert "Red Team" in result.output
