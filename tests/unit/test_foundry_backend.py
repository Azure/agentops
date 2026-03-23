from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from agentops.backends.base import BackendRunContext
from agentops.backends.foundry_backend import FoundryBackend, FoundryEvaluatorRuntime
from agentops.core.models import BackendConfig
from agentops.utils.yaml import save_yaml


class _FakeHttpResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


def _dataset_yaml(tmp_path: Path) -> Path:
    dataset_file = tmp_path / "samples.jsonl"
    dataset_file.write_text(
        "\n".join(
            [
                json.dumps({"input": "2 + 2", "expected": "4"}),
                json.dumps({"input": "3 + 5", "expected": "8"}),
            ]
        ),
        encoding="utf-8",
    )

    config_path = tmp_path / "dataset.yaml"
    save_yaml(
        config_path,
        {
            "version": 1,
            "name": "smoke",
            "source": {"type": "file", "path": str(dataset_file)},
            "format": {"type": "jsonl", "input_field": "input", "expected_field": "expected"},
        },
    )
    return config_path


def _bundle_yaml(tmp_path: Path, *, similarity_source: str | None = None) -> Path:
    evaluators = [
        {"name": "exact_match", "source": "local", "enabled": True},
        {"name": "avg_latency_seconds", "source": "local", "enabled": True},
    ]
    thresholds = [
        {"evaluator": "exact_match", "criteria": "true"},
        {"evaluator": "avg_latency_seconds", "criteria": "<=", "value": 10.0},
    ]

    if similarity_source is not None:
        evaluators.insert(0, {"name": "SimilarityEvaluator", "source": similarity_source, "enabled": True})
        thresholds.insert(0, {"evaluator": "SimilarityEvaluator", "criteria": ">=", "value": 3})

    bundle_path = tmp_path / "bundle.yaml"
    save_yaml(
        bundle_path,
        {
            "version": 1,
            "name": "qa_similarity_baseline",
            "description": "Test bundle",
            "evaluators": evaluators,
            "thresholds": thresholds,
            "metadata": {"category": "test"},
        },
    )
    return bundle_path


def test_foundry_backend_uses_default_azure_credential(tmp_path: Path) -> None:
    """Verify the backend acquires a token via _acquire_token (DefaultAzureCredential)."""
    dataset_path = _dataset_yaml(tmp_path)
    bundle_path = _bundle_yaml(tmp_path)
    context = BackendRunContext(
        backend_config=BackendConfig(
            type="foundry",
            target="agent",
            project_endpoint="https://example.services.ai.azure.com/api/projects/proj-a",
            agent_id="asst_abc123",
        ),
        bundle_path=bundle_path,
        dataset_path=dataset_path,
        backend_output_dir=tmp_path / "out",
    )

    # When _acquire_token raises, the error should propagate clearly
    with patch("agentops.backends.foundry_backend._acquire_token", side_effect=RuntimeError("azure-identity not installed")):
        try:
            FoundryBackend().execute(context)
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "azure-identity" in str(exc)


def test_foundry_backend_agent_service_target(tmp_path: Path) -> None:
    dataset_path = _dataset_yaml(tmp_path)
    bundle_path = _bundle_yaml(tmp_path)
    context = BackendRunContext(
        backend_config=BackendConfig(
            type="foundry",
            target="agent",
            project_endpoint="https://example.services.ai.azure.com/api/projects/proj-a",
            agent_id="asst_abc123",
            api_version="2025-05-01",
            timeout_seconds=15,
            poll_interval_seconds=0.01,
            max_poll_attempts=5,
        ),
        bundle_path=bundle_path,
        dataset_path=dataset_path,
        backend_output_dir=tmp_path / "out-agent",
    )

    responses = [
        _FakeHttpResponse({"id": "thread_1"}),
        _FakeHttpResponse({"id": "msg_1"}),
        _FakeHttpResponse({"id": "run_1"}),
        _FakeHttpResponse({"status": "completed"}),
        _FakeHttpResponse({"data": [{"role": "assistant", "content": [{"text": {"value": "4"}}]}]}),
        _FakeHttpResponse({"id": "thread_2"}),
        _FakeHttpResponse({"id": "msg_2"}),
        _FakeHttpResponse({"id": "run_2"}),
        _FakeHttpResponse({"status": "completed"}),
        _FakeHttpResponse({"data": [{"role": "assistant", "content": [{"text": {"value": "8"}}]}]}),
    ]

    with patch("agentops.backends.foundry_backend._acquire_token", return_value="fake-agent-token"):
        with patch("agentops.backends.foundry_backend.urllib.request.urlopen", side_effect=responses):
            result = FoundryBackend().execute(context)

    assert result.backend == "foundry"
    assert result.exit_code == 0
    assert "foundry.agent_service" in result.command

    metrics_path = tmp_path / "out-agent" / "backend_metrics.json"
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics_by_name = {item["name"]: item["value"] for item in payload["metrics"]}

    assert metrics_by_name["exact_match"] == 1.0
    assert "SimilarityEvaluator" not in metrics_by_name
    assert "GroundednessEvaluator" not in metrics_by_name
    assert metrics_by_name["samples_evaluated"] == 2.0
    assert len(payload["row_metrics"]) == 2
    first_row_metrics = {item["name"]: item["value"] for item in payload["row_metrics"][0]["metrics"]}
    assert "GroundednessEvaluator" not in first_row_metrics
    assert first_row_metrics["exact_match"] == 1.0


def test_foundry_backend_uses_similarity_evaluator_when_source_is_foundry(tmp_path: Path) -> None:
    dataset_path = _dataset_yaml(tmp_path)
    bundle_path = _bundle_yaml(tmp_path, similarity_source="foundry")
    context = BackendRunContext(
        backend_config=BackendConfig(
            type="foundry",
            target="agent",
            project_endpoint="https://example.services.ai.azure.com/api/projects/proj-a",
            agent_id="asst_abc123",
            api_version="2025-05-01",
            timeout_seconds=15,
            poll_interval_seconds=0.01,
            max_poll_attempts=5,
        ),
        bundle_path=bundle_path,
        dataset_path=dataset_path,
        backend_output_dir=tmp_path / "out-agent-foundry-sim",
    )

    responses = [
        _FakeHttpResponse({"id": "thread_1"}),
        _FakeHttpResponse({"id": "msg_1"}),
        _FakeHttpResponse({"id": "run_1"}),
        _FakeHttpResponse({"status": "completed"}),
        _FakeHttpResponse({"data": [{"role": "assistant", "content": [{"text": {"value": "4"}}]}]}),
        _FakeHttpResponse({"id": "thread_2"}),
        _FakeHttpResponse({"id": "msg_2"}),
        _FakeHttpResponse({"id": "run_2"}),
        _FakeHttpResponse({"status": "completed"}),
        _FakeHttpResponse({"data": [{"role": "assistant", "content": [{"text": {"value": "8"}}]}]}),
    ]

    class _FakeSimilarityEvaluator:
        def __call__(self, **kwargs):
            assert "query" in kwargs
            assert "response" in kwargs
            assert "ground_truth" in kwargs
            return {"similarity": 4.0}

    with patch("agentops.backends.foundry_backend._acquire_token", return_value="fake-agent-token"):
        with patch(
            "agentops.backends.foundry_backend._build_foundry_evaluator_runtimes",
            return_value=[
                FoundryEvaluatorRuntime(
                    name="SimilarityEvaluator",
                    evaluator=_FakeSimilarityEvaluator(),
                    input_mapping={
                        "query": "$prompt",
                        "response": "$prediction",
                        "ground_truth": "$expected",
                    },
                    score_keys=["similarity"],
                )
            ],
        ):
            with patch("agentops.backends.foundry_backend.urllib.request.urlopen", side_effect=responses):
                result = FoundryBackend().execute(context)

    assert result.backend == "foundry"
    assert result.exit_code == 0

    metrics_path = tmp_path / "out-agent-foundry-sim" / "backend_metrics.json"
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics_by_name = {item["name"]: item["value"] for item in payload["metrics"]}
    assert metrics_by_name["SimilarityEvaluator"] == 4.0


def test_foundry_backend_rejects_unsupported_local_evaluator(tmp_path: Path) -> None:
    dataset_path = _dataset_yaml(tmp_path)
    bundle_path = _bundle_yaml(tmp_path, similarity_source="local")
    context = BackendRunContext(
        backend_config=BackendConfig(
            type="foundry",
            target="agent",
            project_endpoint="https://example.services.ai.azure.com/api/projects/proj-a",
            agent_id="asst_abc123",
            api_version="2025-05-01",
            timeout_seconds=15,
            poll_interval_seconds=0.01,
            max_poll_attempts=5,
        ),
        bundle_path=bundle_path,
        dataset_path=dataset_path,
        backend_output_dir=tmp_path / "out-agent-unsupported-local",
    )

    with patch("agentops.backends.foundry_backend._acquire_token", return_value="fake-agent-token"):
        try:
            FoundryBackend().execute(context)
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "Unsupported local evaluator(s): SimilarityEvaluator" in str(exc)


def test_foundry_backend_model_direct_target(tmp_path: Path) -> None:
    """Verify model-direct target calls the model via chat completions."""
    dataset_path = _dataset_yaml(tmp_path)
    bundle_path = _bundle_yaml(tmp_path)
    context = BackendRunContext(
        backend_config=BackendConfig(
            type="foundry",
            target="model",
            project_endpoint="https://example.services.ai.azure.com/api/projects/proj-a",
            model="gpt-5-mini",
            api_version="2025-05-01",
            timeout_seconds=15,
            poll_interval_seconds=0.01,
            max_poll_attempts=5,
        ),
        bundle_path=bundle_path,
        dataset_path=dataset_path,
        backend_output_dir=tmp_path / "out-model-direct",
    )

    def _fake_invoke_model_direct(self_backend, settings, prompt):
        if "2 + 2" in prompt:
            return "4"
        return "8"

    with patch("agentops.backends.foundry_backend._acquire_token", return_value="fake-token"):
        with patch.object(FoundryBackend, "_invoke_model_direct", _fake_invoke_model_direct):
            result = FoundryBackend().execute(context)

    assert result.backend == "foundry"
    assert result.exit_code == 0
    assert "model_direct" in result.command

    metrics_path = tmp_path / "out-model-direct" / "backend_metrics.json"
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics_by_name = {item["name"]: item["value"] for item in payload["metrics"]}
    assert metrics_by_name["exact_match"] == 1.0
    assert metrics_by_name["samples_evaluated"] == 2.0


def test_foundry_backend_model_target_requires_explicit_model(tmp_path: Path) -> None:
    dataset_path = _dataset_yaml(tmp_path)
    bundle_path = _bundle_yaml(tmp_path)
    context = BackendRunContext(
        backend_config=BackendConfig(
            type="foundry",
            target="model",
            project_endpoint="https://example.services.ai.azure.com/api/projects/proj-a",
            api_version="2025-05-01",
            timeout_seconds=15,
            poll_interval_seconds=0.01,
            max_poll_attempts=5,
        ),
        bundle_path=bundle_path,
        dataset_path=dataset_path,
        backend_output_dir=tmp_path / "out-model-missing",
    )

    try:
        FoundryBackend().execute(context)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "target=model" in str(exc)
        assert "backend.model" in str(exc)


# ---------------------------------------------------------------------------
# Unit tests for _cloud_evaluator_data_mapping and _default_foundry_input_mapping
# ---------------------------------------------------------------------------

from agentops.backends.foundry_backend import (
    _cloud_evaluator_data_mapping,
    _default_foundry_input_mapping,
)


def test_cloud_evaluator_data_mapping_similarity() -> None:
    mapping = _cloud_evaluator_data_mapping("similarity", "input", "expected")
    assert mapping["query"] == "{{item.input}}"
    assert mapping["response"] == "{{sample.output_text}}"
    assert mapping["ground_truth"] == "{{item.expected}}"
    assert "context" not in mapping


def test_cloud_evaluator_data_mapping_groundedness_uses_expected_when_no_context_field() -> None:
    mapping = _cloud_evaluator_data_mapping("groundedness", "input", "expected")
    assert mapping["context"] == "{{item.expected}}"


def test_cloud_evaluator_data_mapping_groundedness_uses_context_field_when_set() -> None:
    mapping = _cloud_evaluator_data_mapping("groundedness", "input", "expected", context_field="context")
    assert mapping["context"] == "{{item.context}}"
    assert "ground_truth" not in mapping


def test_cloud_evaluator_data_mapping_task_completion() -> None:
    mapping = _cloud_evaluator_data_mapping("task_completion", "input", "expected")
    assert mapping["query"] == "{{item.input}}"
    assert mapping["response"] == "{{sample.output_text}}"
    assert "ground_truth" not in mapping
    assert "context" not in mapping
    assert "tool_calls" not in mapping


def test_cloud_evaluator_data_mapping_tool_call_accuracy() -> None:
    mapping = _cloud_evaluator_data_mapping("tool_call_accuracy", "input", "expected")
    assert mapping["query"] == "{{item.input}}"
    assert mapping["response"] == "{{sample.output_text}}"
    assert mapping["tool_calls"] == "{{sample.tool_calls}}"
    assert mapping["tool_definitions"] == "{{item.tool_definitions}}"


def test_default_foundry_input_mapping_groundedness_uses_row_context() -> None:
    mapping = _default_foundry_input_mapping("GroundednessEvaluator")
    assert mapping["context"] == "$row.context"
    assert mapping["query"] == "$prompt"
    assert mapping["response"] == "$prediction"


def test_default_foundry_input_mapping_task_completion() -> None:
    mapping = _default_foundry_input_mapping("TaskCompletionEvaluator")
    assert mapping["query"] == "$prompt"
    assert mapping["response"] == "$prediction"
    assert "ground_truth" not in mapping
    assert "context" not in mapping


def test_default_foundry_input_mapping_tool_call_accuracy() -> None:
    mapping = _default_foundry_input_mapping("ToolCallAccuracyEvaluator")
    assert mapping["query"] == "$prompt"
    assert mapping["response"] == "$prediction"
    assert mapping["tool_calls"] == "$row.tool_calls"
    assert mapping["tool_definitions"] == "$row.tool_definitions"
