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
            "assert": {
                "config": "assert/eval_config.yaml",
                "env": {"AZURE_API_VERSION": "2024-12-01-preview"},
            },
        }
    )
    assert cfg.assert_run is not None
    assert isinstance(cfg.assert_run, AssertRunConfig)
    assert str(cfg.assert_run.config).endswith("eval_config.yaml")
    assert cfg.assert_run.fail_on_violations is True
    assert cfg.assert_run.env == {"AZURE_API_VERSION": "2024-12-01-preview"}


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
        "a": {"total": 5, "violations": 2, "passes": 3, "skipped": 0, "other": 0},
        "b": {"total": 5, "violations": 0, "passes": 5, "skipped": 0, "other": 0},
    }
    totals = assert_runner._aggregate_totals(metrics, dims)
    # assert-ai 0.1.x: each scores.jsonl row is one test case bucketed by its
    # risk_category / behavior, so total = sum across dimensions.
    assert totals["total"] == 10
    assert totals["failed"] == 2
    assert totals["passed"] == 8
    assert totals["skipped"] == 0
    assert totals["pass_rate"] == pytest.approx(8 / 10)


def test_assert_aggregate_totals_excludes_skipped_from_pass_rate(tmp_path: Path):
    dims = {
        "pii_leak": {
            "total": 4,
            "violations": 1,
            "passes": 2,
            "skipped": 1,
            "other": 0,
        },
    }
    totals = assert_runner._aggregate_totals({}, dims)
    assert totals["total"] == 4
    assert totals["skipped"] == 1
    assert totals["failed"] == 1
    # 4 total - 1 skipped = 3 scored; 3 - 1 failed = 2 passed; pass_rate over 3.
    assert totals["passed"] == 2
    assert totals["pass_rate"] == pytest.approx(2 / 3, abs=1e-3)


def test_assert_summarize_dimensions_handles_0_1_x_schema(tmp_path: Path):
    """assert-ai 0.1.x emits structured verdict + dimensions blocks per row."""

    run_dir = _write_assert_layout(
        tmp_path,
        suite="demo",
        run_id="r1",
        metrics={},
        scores=[
            {
                "judge_status": "ok",
                "verdict": {"dimensions": {"policy_violation": False, "overrefusal": False}},
                "dimensions": {"risk_category": "pii_leak", "behavior": "noop"},
            },
            {
                "judge_status": "ok",
                "verdict": {"dimensions": {"policy_violation": True}},
                "dimensions": {"risk_category": "pii_leak"},
            },
            {
                "judge_status": "scoring_skipped",
                "verdict": {},
                "dimensions": {"risk_category": "jailbreak"},
            },
        ],
    )
    summary = assert_runner._summarize_dimensions(run_dir)
    assert summary["pii_leak"]["total"] == 2
    assert summary["pii_leak"]["violations"] == 1
    assert summary["pii_leak"]["passes"] == 1
    assert summary["jailbreak"]["skipped"] == 1
    assert summary["jailbreak"]["violations"] == 0


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

    monkeypatch.setattr(
        assert_runner,
        "_resolve_assert_executable",
        lambda executable="assert-ai": "assert-ai",
    )
    fake_completed = mock.Mock(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(assert_runner.subprocess, "run", mock.Mock(return_value=fake_completed))

    result = assert_runner.run_assert(
        workspace=tmp_path,
        config_path=eval_cfg,
        results_dir=tmp_path / "artifacts" / "results",
        env={"AZURE_API_VERSION": "2024-12-01-preview"},
    )
    assert result.suite == "demo"
    assert result.run_id == "r1"
    assert result.total_cases == 1
    assert result.has_violations is False
    assert result.normalized_path is not None
    payload = json.loads(Path(result.normalized_path).read_text(encoding="utf-8"))
    assert payload["suite"] == "demo"
    assert assert_runner.subprocess.run.call_args.kwargs["env"]["AZURE_API_VERSION"] == "2024-12-01-preview"


def test_run_assert_raises_when_cli_missing(tmp_path: Path, monkeypatch):
    eval_cfg = tmp_path / "ec.yaml"
    eval_cfg.write_text("suite: x\n", encoding="utf-8")
    monkeypatch.setattr(
        assert_runner,
        "_resolve_assert_executable",
        lambda executable="assert-ai": None,
    )
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


def test_redteam_records_from_payload_openai_evals_shape():
    """Current azure-ai-evaluation RedTeam SDK writes OpenAI Evals-shaped output."""
    payload = {
        "result_counts": {"total": 3, "passed": 2, "failed": 1},
        "output_items": {
            "object": "list",
            "data": [
                {
                    "object": "eval.run.output_item",
                    "status": "completed",
                    "results": {
                        "name": "hate_unfairness",
                        "metric": "hate_unfairness",
                        "passed": False,
                        "label": "fail",
                        "properties": {
                            "attack_technique": "base64",
                            "attack_complexity": "easy",
                            "attack_success": True,
                        },
                    },
                },
                {
                    "object": "eval.run.output_item",
                    "status": "completed",
                    "results": {
                        "name": "violence",
                        "passed": True,
                        "label": "pass",
                        "properties": {
                            "attack_technique": "base64",
                            "attack_success": False,
                        },
                    },
                },
                {
                    "object": "eval.run.output_item",
                    "status": "completed",
                    "results": {
                        "name": "hate_unfairness",
                        "passed": True,
                        "label": "pass",
                        "properties": {"attack_technique": "base64"},
                    },
                },
            ],
        },
    }
    records = redteam_runner._records_from_payload(payload)
    assert len(records) == 3
    assert records[0]["risk_category"] == "hate_unfairness"
    assert records[0]["attack_strategy"] == "base64"
    assert records[0]["successful"] is True
    assert records[1]["successful"] is False
    assert records[2]["successful"] is False


def test_redteam_records_from_payload_unwraps_redteam_result_object():
    """The SDK now returns a RedTeamResult object; we unwrap via scan_result."""

    class _FakeRedTeamResult:
        def __init__(self, data: dict) -> None:
            self.scan_result = data

    payload = _FakeRedTeamResult(
        {
            "output_items": {
                "data": [
                    {
                        "results": {
                            "name": "violence",
                            "label": "fail",
                            "properties": {
                                "attack_technique": "rot13",
                                "attack_success": True,
                            },
                        }
                    }
                ]
            }
        }
    )
    records = redteam_runner._records_from_payload(payload)
    assert len(records) == 1
    assert records[0]["risk_category"] == "violence"
    assert records[0]["attack_strategy"] == "rot13"
    assert records[0]["successful"] is True


def test_redteam_records_from_payload_unwraps_via_to_dict():
    class _FakeRedTeamResult:
        def to_dict(self) -> dict:
            return {
                "attack_details": [
                    {
                        "risk_category": "self_harm",
                        "attack_strategy": "morse",
                        "attack_success": False,
                    }
                ]
            }

    records = redteam_runner._records_from_payload(_FakeRedTeamResult())
    assert len(records) == 1
    assert records[0]["risk_category"] == "self_harm"
    assert records[0]["successful"] is False


def test_redteam_load_results_from_output_dir_reads_results_json(tmp_path: Path):
    results_dir = tmp_path / "raw_redteam_output.json"
    results_dir.mkdir()
    payload = {
        "output_items": {
            "data": [
                {
                    "results": {
                        "name": "hate_unfairness",
                        "label": "fail",
                        "properties": {
                            "attack_technique": "base64",
                            "attack_success": True,
                        },
                    }
                }
            ]
        }
    }
    (results_dir / "results.json").write_text(json.dumps(payload), encoding="utf-8")

    loaded = redteam_runner._load_results_from_output_dir(tmp_path)
    assert loaded == payload


def test_redteam_load_results_from_output_dir_falls_back_to_file(tmp_path: Path):
    """Older SDK versions wrote a single file at the output_path."""
    payload = {"attack_details": [{"risk_category": "violence", "attack_success": True}]}
    (tmp_path / "raw_redteam_output.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    loaded = redteam_runner._load_results_from_output_dir(tmp_path)
    assert loaded == payload


def test_redteam_load_results_from_output_dir_returns_none_when_missing(tmp_path: Path):
    assert redteam_runner._load_results_from_output_dir(tmp_path) is None


def test_redteam_build_target_callback_requires_endpoint(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    with pytest.raises(redteam_runner.RedTeamRunnerError):
        redteam_runner._build_target_callback({"model_deployment": "x"})


def test_redteam_build_target_callback_with_endpoint(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com")
    callback = redteam_runner._build_target_callback({"model_deployment": "gpt-4o-mini"})
    assert callback["azure_deployment"] == "gpt-4o-mini"
    assert callback["azure_endpoint"].startswith("https://")


def test_redteam_build_target_callback_for_http_endpoint_uses_agentops_mapping(
    monkeypatch,
):
    from agentops.pipeline import invocations

    seen: dict[str, Any] = {}

    def fake_stream(**kwargs: Any) -> str:
        seen.update(kwargs)
        return "conv-123 answer"

    monkeypatch.setattr(invocations, "_http_request_stream", fake_stream)
    callback = redteam_runner._build_target_callback(
        {
            "endpoint": "https://example.test/orchestrator",
            "request_field": "ask",
            "response_mode": "text",
            "stream": {"strip_leading_token": True},
        }
    )

    assert callable(callback)
    assert callback(query="Can you help?") == "answer"
    assert seen["url"] == "https://example.test/orchestrator"
    assert seen["body"] == {"ask": "Can you help?"}


def test_redteam_build_target_callback_unsupported():
    with pytest.raises(redteam_runner.RedTeamRunnerError):
        redteam_runner._build_target_callback({"foo": "bar"})


def test_redteam_project_from_env_returns_onedp_endpoint_string(monkeypatch):
    """New (hub-less) Foundry projects use the endpoint as a string."""

    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://acct.services.ai.azure.com/api/projects/my-project/",
    )
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-1234")
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg-foundry")
    monkeypatch.setenv("AZURE_AI_PROJECT_NAME", "my-project")
    project = redteam_runner._project_from_env()
    # SDK detects OneDP via isinstance(project, str); endpoint must win even
    # when the legacy triplet is also present so we skip AML discovery.
    assert isinstance(project, str)
    assert project == "https://acct.services.ai.azure.com/api/projects/my-project"


def test_redteam_project_from_env_returns_triplet_for_hub_based(monkeypatch):
    """Hub-based projects (no OneDP endpoint) use the legacy triplet."""

    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-1234")
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg-foundry")
    monkeypatch.setenv("AZURE_AI_PROJECT_NAME", "my-project")
    project = redteam_runner._project_from_env()
    assert project == {
        "subscription_id": "sub-1234",
        "resource_group_name": "rg-foundry",
        "project_name": "my-project",
    }


def test_redteam_project_from_env_returns_none_when_incomplete(monkeypatch):
    """Without an endpoint or full triplet we must return None."""

    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg-foundry")
    monkeypatch.setenv("AZURE_AI_PROJECT_NAME", "my-project")
    assert redteam_runner._project_from_env() is None


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
