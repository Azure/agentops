from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentops.core.agentops_config import AgentOpsConfig
from agentops.core.results import RowMetric
from agentops.pipeline import orchestrator
from agentops.services import dataset_source as dataset_source_service


def test_remote_dataset_is_resolved_once_and_provenance_survives_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_uri = (
        "https://examplestorage.blob.core.windows.net/evals/regression.jsonl"
    )
    materialized = tmp_path / "private-snapshot.jsonl"
    materialized.write_text(
        json.dumps(
            {
                "input": "hello",
                "expected": "hi",
                "response": "hi",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    state = {"resolutions": 0, "cleaned": False}

    @contextmanager
    def fake_resolve(value, **kwargs):
        state["resolutions"] += 1
        yield SimpleNamespace(
            local_path=materialized,
            provenance=source_uri,
            display_name="regression.jsonl",
            temporary=True,
        )
        materialized.unlink()
        state["cleaned"] = True

    span_arguments: dict[str, object] = {}

    @contextmanager
    def fake_span(**kwargs):
        span_arguments.update(kwargs)
        yield None

    monkeypatch.setattr(orchestrator, "resolve_dataset_source", fake_resolve)
    monkeypatch.setattr(orchestrator, "detect_dataset_shape", lambda _path: set())
    monkeypatch.setattr(orchestrator, "select_evaluators", lambda *args, **kwargs: [])
    monkeypatch.setattr(orchestrator.runtime, "load_evaluators", lambda _presets: {})
    monkeypatch.setattr(orchestrator.telemetry, "eval_run_span", fake_span)
    monkeypatch.setattr(orchestrator.telemetry, "init_tracing", lambda: None)
    monkeypatch.setattr(orchestrator.telemetry, "shutdown", lambda: None)

    progress: list[str] = []
    result = orchestrator.run_evaluation(
        AgentOpsConfig(
            version=1,
            agent="https://example.test/chat",
            dataset=source_uri,
            response_source="dataset",
        ),
        options=orchestrator.RunOptions(
            config_path=tmp_path / "agentops.yaml",
            output_dir=tmp_path / "out",
            progress=progress.append,
        ),
    )

    assert state == {"resolutions": 1, "cleaned": True}
    assert result.dataset_path == source_uri
    assert result.rows[0].response == "hi"
    assert span_arguments["dataset_name"] == source_uri
    assert source_uri in "\n".join(progress)
    assert "private-snapshot" not in "\n".join(progress)
    assert materialized.as_posix() not in (tmp_path / "out" / "results.json").read_text(
        encoding="utf-8"
    )


def test_azd_execution_does_not_resolve_agentops_dataset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = AgentOpsConfig(
        version=1,
        agent="support-agent:1",
        dataset="https://examplestorage.blob.core.windows.net/evals/data.jsonl",
        execution="azd",
        eval_recipe=Path("eval.yaml"),
    )
    monkeypatch.setattr(
        orchestrator,
        "resolve_dataset_source",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("azd must keep recipe-owned dataset behavior")
        ),
    )
    monkeypatch.setattr(
        orchestrator,
        "_run_evaluation_azd",
        lambda config, *, options: "azd-result",
    )

    assert (
        orchestrator._run_evaluation(
            config,
            options=orchestrator.RunOptions(
                config_path=tmp_path / "agentops.yaml",
                output_dir=tmp_path / "out",
            ),
        )
        == "azd-result"
    )


class _ParityDatasetDownloader:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def chunks(self):
        midpoint = len(self._payload) // 2
        yield self._payload[:midpoint]
        yield self._payload[midpoint:]


class _ParityDatasetClient:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def get_properties(self):
        return SimpleNamespace(size=len(self._payload), etag=None, last_modified=None)

    def download(self, **_kwargs):
        return _ParityDatasetDownloader(self._payload)


@pytest.mark.parametrize(
    ("source_kind", "dataset_value"),
    [
        ("local", "parity.jsonl"),
        (
            "blob",
            "https://account.blob.core.windows.net/evaluations/parity.jsonl",
        ),
        (
            "adls",
            "https://account.dfs.core.windows.net/evaluations/parity.jsonl",
        ),
    ],
    ids=["local", "blob", "adls"],
)
def test_run_evaluation_preserves_dataset_semantics_across_sources(
    tmp_path: Path,
    monkeypatch,
    source_kind: str,
    dataset_value: str,
) -> None:
    payload = (
        b'{"input":"first","expected":"alpha","response":"alpha"}\n'
        b'{"input":"second","expected":"beta","response":"beta"}\n'
    )
    local_dataset = tmp_path / "parity.jsonl"
    local_dataset.write_bytes(payload)

    if source_kind != "local":
        client = _ParityDatasetClient(payload)
        monkeypatch.setattr(
            dataset_source_service,
            "_create_storage_client",
            lambda _reference: client,
        )

    monkeypatch.setattr(
        orchestrator.runtime,
        "load_evaluators",
        lambda presets: [SimpleNamespace(preset=preset) for preset in presets],
    )
    monkeypatch.setattr(
        orchestrator.runtime,
        "run_evaluator",
        lambda evaluator, **_kwargs: RowMetric(
            name=evaluator.preset.score_key,
            value=(
                0.01
                if evaluator.preset.score_key == "avg_latency_seconds"
                else 5.0
            ),
        ),
    )
    monkeypatch.setattr(orchestrator.telemetry, "init_tracing", lambda: None)
    monkeypatch.setattr(orchestrator.telemetry, "shutdown", lambda: None)

    result = orchestrator.run_evaluation(
        AgentOpsConfig(
            version=1,
            agent="https://example.test/chat",
            dataset=dataset_value,
            protocol="http-json",
            response_source="dataset",
        ),
        options=orchestrator.RunOptions(
            config_path=tmp_path / "agentops.yaml",
            output_dir=tmp_path / "out",
        ),
    )

    expected_evaluators = [
        "CoherenceEvaluator",
        "FluencyEvaluator",
        "SimilarityEvaluator",
        "ResponseCompletenessEvaluator",
        "avg_latency_seconds",
    ]
    expected_metrics = {
        "coherence": 5.0,
        "fluency": 5.0,
        "similarity": 5.0,
        "response_completeness": 5.0,
        "avg_latency_seconds": 0.01,
    }

    assert result.dataset_path == (
        str(local_dataset.resolve()) if source_kind == "local" else dataset_value
    )
    assert result.evaluators == expected_evaluators
    assert [
        (
            row.row_index,
            row.input,
            row.expected,
            row.response,
            row.error,
            {metric.name: metric.value for metric in row.metrics},
        )
        for row in result.rows
    ] == [
        (0, "first", "alpha", "alpha", None, expected_metrics),
        (1, "second", "beta", "beta", None, expected_metrics),
    ]
    assert result.aggregate_metrics == expected_metrics
    assert all(threshold.passed for threshold in result.thresholds)
    assert result.summary.model_dump() == {
        "items_total": 2,
        "items_passed_all": 2,
        "items_pass_rate": 1.0,
        "thresholds_total": 5,
        "thresholds_passed": 5,
        "threshold_pass_rate": 1.0,
        "overall_passed": True,
    }
    assert not list((tmp_path / ".agentops" / ".resolved").glob("*.jsonl"))
