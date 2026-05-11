"""Unit tests for the optional Foundry publisher."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

import pytest

from agentops.core.results import (
    RowMetric,
    RowResult,
    RunResult,
    RunSummary,
    TargetInfo,
)
from agentops.pipeline import publisher


def _build_run_result() -> RunResult:
    return RunResult(
        started_at="2026-04-27T14:00:00+00:00",
        finished_at="2026-04-27T14:00:01+00:00",
        duration_seconds=1.0,
        target=TargetInfo(kind="foundry_prompt", raw="my-agent:1"),
        dataset_path="dataset.jsonl",
        evaluators=["F1ScoreEvaluator"],
        rows=[
            RowResult(
                row_index=0,
                input="hi",
                expected="hello",
                response="hello",
                metrics=[RowMetric(name="f1_score", value=1.0)],
            ),
            RowResult(
                row_index=1,
                input="bye",
                expected="goodbye",
                response="goodbye",
                metrics=[RowMetric(name="f1_score", value=0.5)],
            ),
        ],
        aggregate_metrics={"f1_score": 0.75},
        summary=RunSummary(
            items_total=2,
            items_passed_all=2,
            items_pass_rate=1.0,
            thresholds_total=0,
            thresholds_passed=0,
            threshold_pass_rate=1.0,
            overall_passed=True,
        ),
    )


def test_build_instance_rows_projects_metrics():
    rows = publisher._build_instance_rows(_build_run_result())
    assert rows == [
        {
            "line_number": 0,
            "input": "hi",
            "response": "hello",
            "ground_truth": "hello",
            "f1_score": 1.0,
        },
        {
            "line_number": 1,
            "input": "bye",
            "response": "goodbye",
            "ground_truth": "goodbye",
            "f1_score": 0.5,
        },
    ]


def test_publish_requires_endpoint(monkeypatch):
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    with pytest.raises(ValueError, match="project_endpoint"):
        publisher.publish_to_foundry(_build_run_result())


def _install_fake_azure_modules(captured: Dict[str, Any]) -> None:
    """Inject lightweight stand-ins for azure-ai-evaluation and pandas."""

    fake_pandas = types.ModuleType("pandas")

    class _DataFrame:
        def __init__(self, rows: List[Dict[str, Any]]):
            self.rows = rows

    fake_pandas.DataFrame = _DataFrame  # type: ignore[attr-defined]
    sys.modules["pandas"] = fake_pandas

    fake_azure = types.ModuleType("azure")
    fake_evaluation = types.ModuleType("azure.ai.evaluation")
    fake_evaluate = types.ModuleType("azure.ai.evaluation._evaluate")
    fake_utils = types.ModuleType("azure.ai.evaluation._evaluate._utils")

    def _log_metrics_and_instance_results_onedp(**kwargs):
        captured.update(kwargs)
        return "https://ai.azure.com/projects/foo/evaluations/bar"

    fake_utils._log_metrics_and_instance_results_onedp = (  # type: ignore[attr-defined]
        _log_metrics_and_instance_results_onedp
    )
    fake_ai = types.ModuleType("azure.ai")

    sys.modules["azure"] = fake_azure
    sys.modules["azure.ai"] = fake_ai
    sys.modules["azure.ai.evaluation"] = fake_evaluation
    sys.modules["azure.ai.evaluation._evaluate"] = fake_evaluate
    sys.modules["azure.ai.evaluation._evaluate._utils"] = fake_utils


def test_publish_calls_onedp_with_expected_payload(monkeypatch):
    captured: Dict[str, Any] = {}
    _install_fake_azure_modules(captured)
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)

    result = publisher.publish_to_foundry(
        _build_run_result(),
        project_endpoint="https://contoso.services.ai.azure.com/api/projects/p",
        evaluation_name="agentops-eval-test",
    )

    assert result.studio_url.startswith("https://ai.azure.com/")
    assert result.evaluation_name == "agentops-eval-test"
    assert captured["project_url"].endswith("/projects/p")
    assert captured["evaluation_name"] == "agentops-eval-test"
    assert captured["metrics"] == {"f1_score": 0.75}
    assert captured["name_map"] == {"f1_score": "f1_score"}
    assert len(captured["instance_results"].rows) == 2


def test_publish_falls_back_to_env_var(monkeypatch):
    captured: Dict[str, Any] = {}
    _install_fake_azure_modules(captured)
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://contoso.services.ai.azure.com/api/projects/from-env",
    )

    publisher.publish_to_foundry(_build_run_result())
    assert captured["project_url"].endswith("/projects/from-env")


def test_orchestrator_skips_publish_when_disabled(tmp_path: Path):
    from agentops.core.agentops_config import AgentOpsConfig
    from agentops.pipeline import orchestrator

    config = AgentOpsConfig(
        version=1,
        agent="model:gpt-4o-mini",
        dataset=Path("dataset.jsonl"),
    )
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    result = _build_run_result()

    with mock.patch.object(publisher, "publish_to_foundry") as fake:
        orchestrator._publish_to_foundry_safely(result, config, output_dir)

    fake.assert_not_called()  # never reached because publish is None
    # The helper itself only runs when publish == "foundry"; we verify the
    # orchestrator branch by emulating that contract.


def test_orchestrator_swallows_publish_errors(tmp_path: Path):
    from agentops.core.agentops_config import AgentOpsConfig
    from agentops.pipeline import orchestrator

    config = AgentOpsConfig(
        version=1,
        agent="model:gpt-4o-mini",
        dataset=Path("dataset.jsonl"),
        publish="foundry",
    )
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    result = _build_run_result()

    with mock.patch.object(
        publisher, "publish_to_foundry", side_effect=ImportError("no SDK")
    ):
        # Must not raise.
        orchestrator._publish_to_foundry_safely(result, config, output_dir)

    assert not (output_dir / "cloud_evaluation.json").exists()


def test_orchestrator_writes_cloud_evaluation_metadata(tmp_path: Path):
    from agentops.core.agentops_config import AgentOpsConfig
    from agentops.pipeline import orchestrator

    config = AgentOpsConfig(
        version=1,
        agent="model:gpt-4o-mini",
        dataset=Path("dataset.jsonl"),
        publish="foundry",
        project_endpoint="https://contoso.services.ai.azure.com/api/projects/p",
    )
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    result = _build_run_result()

    fake_publish = publisher.PublishResult(
        studio_url="https://ai.azure.com/projects/p/evaluations/abc",
        evaluation_name="agentops-eval-abc",
    )
    with mock.patch.object(publisher, "publish_to_foundry", return_value=fake_publish):
        orchestrator._publish_to_foundry_safely(result, config, output_dir)

    meta_path = output_dir / "cloud_evaluation.json"
    assert meta_path.exists()
    import json
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    assert payload["report_url"].endswith("/abc")
    assert payload["evaluation_name"] == "agentops-eval-abc"


def test_orchestrator_dispatches_to_cloud_publisher_when_publish_is_foundry_cloud(
    tmp_path: Path,
):
    """publish='foundry_cloud' must invoke cloud_publisher (NOT the Classic
    publisher) and write a ``mode='cloud'`` cloud_evaluation.json."""
    from agentops.core.agentops_config import AgentOpsConfig
    from agentops.pipeline import cloud_publisher as _cp
    from agentops.pipeline import orchestrator

    dataset_path = tmp_path / "dataset.jsonl"
    dataset_path.write_text('{"input": "hi"}\n', encoding="utf-8")

    config = AgentOpsConfig(
        version=1,
        agent="support-bot:1",
        dataset=dataset_path,
        publish="foundry_cloud",
        project_endpoint="https://contoso.services.ai.azure.com/api/projects/p",
    )
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    fake_published = _cp.CloudPublishResult(
        eval_id="eval-1",
        run_id="run-1",
        status="completed",
        report_url="https://ai.azure.com/foundry/runs/run-1",
        evaluation_name="agentops-cloud-abc",
    )

    result = _build_run_result()
    result.target = TargetInfo(
        kind="foundry_prompt", raw="support-bot:1",
        name="support-bot", version="1",
    )

    with mock.patch.object(
        _cp, "publish_to_foundry_cloud", return_value=fake_published,
    ) as cloud_mock, mock.patch.object(
        publisher, "publish_to_foundry",
    ) as classic_mock:
        metadata = orchestrator._publish_to_foundry_cloud_safely(
            result, config, output_dir, dataset_path,
        )

    classic_mock.assert_not_called()
    cloud_mock.assert_called_once()

    meta_path = output_dir / "cloud_evaluation.json"
    assert meta_path.exists()
    import json
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "cloud"
    assert payload["eval_id"] == "eval-1"
    assert payload["run_id"] == "run-1"
    assert payload["report_url"].endswith("/run-1")
    assert metadata == payload


def test_orchestrator_swallows_cloud_publish_errors(tmp_path: Path):
    """A failure in cloud_publisher must not be fatal — local results
    remain the source of truth."""
    from agentops.core.agentops_config import AgentOpsConfig
    from agentops.pipeline import cloud_publisher as _cp
    from agentops.pipeline import orchestrator

    dataset_path = tmp_path / "dataset.jsonl"
    dataset_path.write_text('{"input": "hi"}\n', encoding="utf-8")

    config = AgentOpsConfig(
        version=1,
        agent="support-bot:1",
        dataset=dataset_path,
        publish="foundry_cloud",
        project_endpoint="https://x.example/api/projects/p",
    )
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    result = _build_run_result()
    result.target = TargetInfo(
        kind="foundry_prompt", raw="support-bot:1",
        name="support-bot", version="1",
    )

    notices: list = []
    with mock.patch.object(
        _cp, "publish_to_foundry_cloud", side_effect=RuntimeError("boom"),
    ):
        metadata = orchestrator._publish_to_foundry_cloud_safely(
            result, config, output_dir, dataset_path,
            progress=notices.append,
        )

    # No metadata file written on failure.
    assert not (output_dir / "cloud_evaluation.json").exists()
    assert metadata is not None
    assert metadata["status"] == "failed"
    assert metadata["error"] == "boom"
    # User saw a clear failure notice.
    assert any("foundry_cloud FAILED" in m for m in notices)


def test_orchestrator_cloud_publish_requires_project_endpoint(tmp_path: Path, monkeypatch):
    """Without project_endpoint or AZURE_AI_FOUNDRY_PROJECT_ENDPOINT, the
    cloud branch refuses to call out and emits a clear progress message."""
    from agentops.core.agentops_config import AgentOpsConfig
    from agentops.pipeline import cloud_publisher as _cp
    from agentops.pipeline import orchestrator

    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)

    dataset_path = tmp_path / "dataset.jsonl"
    dataset_path.write_text('{"input": "hi"}\n', encoding="utf-8")

    config = AgentOpsConfig(
        version=1,
        agent="support-bot:1",
        dataset=dataset_path,
        publish="foundry_cloud",
        project_endpoint=None,
    )
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    result = _build_run_result()
    notices: list = []
    with mock.patch.object(_cp, "publish_to_foundry_cloud") as cloud_mock:
        metadata = orchestrator._publish_to_foundry_cloud_safely(
            result, config, output_dir, dataset_path,
            progress=notices.append,
        )
    cloud_mock.assert_not_called()
    assert metadata is not None
    assert metadata["status"] == "failed"
    assert any("project_endpoint" in m for m in notices)
