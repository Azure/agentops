from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agentops.backends.base import BackendRunContext
from agentops.backends.eval_engine import (
    FoundryEvaluatorRuntime,
    _cloud_evaluator_data_mapping,
    _cloud_evaluator_needs_model,
    _default_foundry_input_mapping,
)
from agentops.backends.foundry_backend import (
    FoundryBackend,
)
from agentops.core.models import (
    BundleRef,
    DatasetRef,
    ExecutionConfig,
    OutputConfig,
    RunConfig,
    TargetConfig,
    TargetEndpointConfig,
)
from agentops.utils.yaml import save_yaml


def _foundry_context(
    *,
    bundle_path: Path,
    dataset_path: Path,
    output_dir: Path,
    target_type: str = "agent",
    agent_id: str | None = "asst_abc123",
    model: str | None = None,
    project_endpoint: str = "https://example.services.ai.azure.com/api/projects/proj-a",
    api_version: str | None = "2025-05-01",
    poll_interval_seconds: float | None = 0.01,
    max_poll_attempts: int | None = 5,
    timeout_seconds: int = 15,
) -> BackendRunContext:
    endpoint = TargetEndpointConfig(
        kind="foundry_agent",
        agent_id=agent_id,
        model=model,
        project_endpoint=project_endpoint,
        project_endpoint_env="AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        api_version=api_version,
        poll_interval_seconds=poll_interval_seconds,
        max_poll_attempts=max_poll_attempts,
    )
    run_config = RunConfig(
        version=2,
        target=TargetConfig(
            type=target_type,
            hosting="foundry",
            execution_mode="remote",
            endpoint=endpoint,
        ),
        bundle=BundleRef(path=bundle_path),
        dataset=DatasetRef(path=dataset_path),
        execution=ExecutionConfig(timeout_seconds=timeout_seconds),
        output=OutputConfig(),
    )
    return BackendRunContext(
        run_config=run_config,
        bundle_path=bundle_path,
        dataset_path=dataset_path,
        backend_output_dir=output_dir,
    )


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
            "format": {
                "type": "jsonl",
                "input_field": "input",
                "expected_field": "expected",
            },
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
        evaluators.insert(
            0,
            {
                "name": "SimilarityEvaluator",
                "source": similarity_source,
                "enabled": True,
            },
        )
        thresholds.insert(
            0, {"evaluator": "SimilarityEvaluator", "criteria": ">=", "value": 3}
        )

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
    context = _foundry_context(
        bundle_path=bundle_path,
        dataset_path=dataset_path,
        output_dir=tmp_path / "out",
    )

    # When _acquire_token raises, the error should propagate clearly
    with patch(
        "agentops.backends.foundry_backend._acquire_token",
        side_effect=RuntimeError("azure-identity not installed"),
    ):
        try:
            FoundryBackend().execute(context)
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "azure-identity" in str(exc)


def test_foundry_backend_agent_service_target(tmp_path: Path) -> None:
    dataset_path = _dataset_yaml(tmp_path)
    bundle_path = _bundle_yaml(tmp_path)
    context = _foundry_context(
        bundle_path=bundle_path,
        dataset_path=dataset_path,
        output_dir=tmp_path / "out-agent",
    )

    responses = [
        _FakeHttpResponse({"id": "thread_1"}),
        _FakeHttpResponse({"id": "msg_1"}),
        _FakeHttpResponse({"id": "run_1"}),
        _FakeHttpResponse({"status": "completed"}),
        _FakeHttpResponse(
            {"data": [{"role": "assistant", "content": [{"text": {"value": "4"}}]}]}
        ),
        _FakeHttpResponse({"id": "thread_2"}),
        _FakeHttpResponse({"id": "msg_2"}),
        _FakeHttpResponse({"id": "run_2"}),
        _FakeHttpResponse({"status": "completed"}),
        _FakeHttpResponse(
            {"data": [{"role": "assistant", "content": [{"text": {"value": "8"}}]}]}
        ),
    ]

    with (
        patch(
            "agentops.backends.foundry_backend._acquire_token",
            return_value="fake-agent-token",
        ),
        patch(
            "agentops.backends.foundry_backend.urllib.request.urlopen",
            side_effect=responses,
        ),
    ):
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
    first_row_metrics = {
        item["name"]: item["value"] for item in payload["row_metrics"][0]["metrics"]
    }
    assert "GroundednessEvaluator" not in first_row_metrics
    assert first_row_metrics["exact_match"] == 1.0


def test_foundry_backend_uses_similarity_evaluator_when_source_is_foundry(
    tmp_path: Path,
) -> None:
    dataset_path = _dataset_yaml(tmp_path)
    bundle_path = _bundle_yaml(tmp_path, similarity_source="foundry")
    context = _foundry_context(
        bundle_path=bundle_path,
        dataset_path=dataset_path,
        output_dir=tmp_path / "out-agent-foundry-sim",
    )

    responses = [
        _FakeHttpResponse({"id": "thread_1"}),
        _FakeHttpResponse({"id": "msg_1"}),
        _FakeHttpResponse({"id": "run_1"}),
        _FakeHttpResponse({"status": "completed"}),
        _FakeHttpResponse(
            {"data": [{"role": "assistant", "content": [{"text": {"value": "4"}}]}]}
        ),
        _FakeHttpResponse({"id": "thread_2"}),
        _FakeHttpResponse({"id": "msg_2"}),
        _FakeHttpResponse({"id": "run_2"}),
        _FakeHttpResponse({"status": "completed"}),
        _FakeHttpResponse(
            {"data": [{"role": "assistant", "content": [{"text": {"value": "8"}}]}]}
        ),
    ]

    class _FakeSimilarityEvaluator:
        def __call__(self, **kwargs):
            assert "query" in kwargs
            assert "response" in kwargs
            assert "ground_truth" in kwargs
            return {"similarity": 4.0}

    with (
        patch(
            "agentops.backends.foundry_backend._acquire_token",
            return_value="fake-agent-token",
        ),
        patch(
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
        ),
        patch(
            "agentops.backends.foundry_backend.urllib.request.urlopen",
            side_effect=responses,
        ),
    ):
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
    context = _foundry_context(
        bundle_path=bundle_path,
        dataset_path=dataset_path,
        output_dir=tmp_path / "out-agent-unsupported-local",
    )

    with patch(
        "agentops.backends.foundry_backend._acquire_token",
        return_value="fake-agent-token",
    ):
        try:
            FoundryBackend().execute(context)
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "Unsupported local evaluator(s): SimilarityEvaluator" in str(exc)


def test_foundry_backend_model_direct_target(tmp_path: Path) -> None:
    """Verify model-direct target calls the model via chat completions."""
    dataset_path = _dataset_yaml(tmp_path)
    bundle_path = _bundle_yaml(tmp_path)
    context = _foundry_context(
        bundle_path=bundle_path,
        dataset_path=dataset_path,
        output_dir=tmp_path / "out-model-direct",
        target_type="model",
        agent_id=None,
        model="gpt-5-mini",
    )

    def _fake_invoke_model_direct(self_backend, settings, prompt):
        if "2 + 2" in prompt:
            return "4"
        return "8"

    with (
        patch(
            "agentops.backends.foundry_backend._acquire_token",
            return_value="fake-token",
        ),
        patch.object(FoundryBackend, "_invoke_model_direct", _fake_invoke_model_direct),
    ):
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
    context = _foundry_context(
        bundle_path=bundle_path,
        dataset_path=dataset_path,
        output_dir=tmp_path / "out-model-missing",
        target_type="model",
        agent_id=None,
    )

    try:
        FoundryBackend().execute(context)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "model" in str(exc).lower()
        assert "endpoint.model" in str(exc) or "deployment" in str(exc)


# ---------------------------------------------------------------------------
# Unit tests for _cloud_evaluator_data_mapping and _default_foundry_input_mapping
# ---------------------------------------------------------------------------


def test_cloud_evaluator_data_mapping_similarity() -> None:
    mapping = _cloud_evaluator_data_mapping("similarity", "input", "expected")
    assert mapping["query"] == "{{item.input}}"
    assert mapping["response"] == "{{sample.output_text}}"
    assert mapping["ground_truth"] == "{{item.expected}}"
    assert "context" not in mapping


def test_cloud_evaluator_data_mapping_groundedness_uses_expected_when_no_context_field() -> (
    None
):
    mapping = _cloud_evaluator_data_mapping("groundedness", "input", "expected")
    assert mapping["context"] == "{{item.expected}}"


def test_cloud_evaluator_data_mapping_groundedness_uses_context_field_when_set() -> (
    None
):
    mapping = _cloud_evaluator_data_mapping(
        "groundedness", "input", "expected", context_field="context"
    )
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


# ---------------------------------------------------------------------------
# Extended evaluator coverage (issue #51)
# ---------------------------------------------------------------------------


def test_cloud_evaluator_data_mapping_response_completeness() -> None:
    mapping = _cloud_evaluator_data_mapping(
        "response_completeness", "input", "expected"
    )
    assert mapping["query"] == "{{item.input}}"
    assert mapping["response"] == "{{sample.output_text}}"
    assert mapping["ground_truth"] == "{{item.expected}}"


def test_cloud_evaluator_data_mapping_groundedness_pro() -> None:
    mapping = _cloud_evaluator_data_mapping(
        "groundedness_pro", "input", "expected", context_field="context"
    )
    assert mapping["context"] == "{{item.context}}"
    assert mapping["query"] == "{{item.input}}"
    assert "ground_truth" not in mapping


def test_cloud_evaluator_data_mapping_retrieval() -> None:
    mapping = _cloud_evaluator_data_mapping("retrieval", "input", "expected")
    assert mapping["context"] == "{{item.expected}}"
    assert mapping["query"] == "{{item.input}}"


def test_cloud_evaluator_data_mapping_tool_output_utilization() -> None:
    mapping = _cloud_evaluator_data_mapping(
        "tool_output_utilization", "input", "expected"
    )
    assert mapping["query"] == "{{item.input}}"
    assert mapping["tool_definitions"] == "{{item.tool_definitions}}"
    assert "tool_calls" not in mapping


def test_cloud_evaluator_data_mapping_tool_call_success() -> None:
    mapping = _cloud_evaluator_data_mapping("tool_call_success", "input", "expected")
    assert mapping["tool_definitions"] == "{{item.tool_definitions}}"
    assert "tool_calls" not in mapping


def test_cloud_evaluator_data_mapping_task_adherence_uses_output_items() -> None:
    mapping = _cloud_evaluator_data_mapping("task_adherence", "input", "expected")
    assert mapping["query"] == "{{item.input}}"
    assert mapping["response"] == "{{sample.output_items}}"
    assert "ground_truth" not in mapping


def test_cloud_evaluator_data_mapping_coherence_default_path() -> None:
    mapping = _cloud_evaluator_data_mapping("coherence", "input", "expected")
    assert mapping["query"] == "{{item.input}}"
    assert mapping["response"] == "{{sample.output_text}}"
    assert "ground_truth" not in mapping
    assert "context" not in mapping
    assert "tool_calls" not in mapping


def test_cloud_evaluator_data_mapping_violence_default_path() -> None:
    mapping = _cloud_evaluator_data_mapping("violence", "input", "expected")
    assert mapping["query"] == "{{item.input}}"
    assert mapping["response"] == "{{sample.output_text}}"
    assert "ground_truth" not in mapping


def test_cloud_evaluator_data_mapping_intent_resolution_default_path() -> None:
    mapping = _cloud_evaluator_data_mapping("intent_resolution", "input", "expected")
    assert mapping["query"] == "{{item.input}}"
    assert mapping["response"] == "{{sample.output_text}}"


def test_default_foundry_input_mapping_coherence() -> None:
    mapping = _default_foundry_input_mapping("CoherenceEvaluator")
    assert mapping["query"] == "$prompt"
    assert mapping["response"] == "$prediction"
    assert "ground_truth" not in mapping
    assert "context" not in mapping


def test_default_foundry_input_mapping_fluency() -> None:
    mapping = _default_foundry_input_mapping("FluencyEvaluator")
    assert mapping["query"] == "$prompt"
    assert mapping["response"] == "$prediction"


def test_default_foundry_input_mapping_f1_score() -> None:
    mapping = _default_foundry_input_mapping("F1ScoreEvaluator")
    assert mapping["response"] == "$prediction"
    assert mapping["ground_truth"] == "$expected"
    assert "query" not in mapping


def test_default_foundry_input_mapping_relevance() -> None:
    mapping = _default_foundry_input_mapping("RelevanceEvaluator")
    assert mapping["query"] == "$prompt"
    assert mapping["response"] == "$prediction"
    assert mapping["context"] == "$row.context"


def test_default_foundry_input_mapping_retrieval() -> None:
    mapping = _default_foundry_input_mapping("RetrievalEvaluator")
    assert mapping["query"] == "$prompt"
    assert mapping["response"] == "$prediction"
    assert mapping["context"] == "$row.context"


def test_default_foundry_input_mapping_response_completeness() -> None:
    mapping = _default_foundry_input_mapping("ResponseCompletenessEvaluator")
    assert mapping["response"] == "$prediction"
    assert mapping["ground_truth"] == "$expected"
    assert "query" not in mapping


def test_default_foundry_input_mapping_intent_resolution() -> None:
    mapping = _default_foundry_input_mapping("IntentResolutionEvaluator")
    assert mapping["query"] == "$prompt"
    assert mapping["response"] == "$prediction"
    assert "tool_calls" not in mapping


def test_default_foundry_input_mapping_task_adherence() -> None:
    mapping = _default_foundry_input_mapping("TaskAdherenceEvaluator")
    assert mapping["query"] == "$prompt"
    assert mapping["response"] == "$prediction"


def test_default_foundry_input_mapping_tool_selection() -> None:
    mapping = _default_foundry_input_mapping("ToolSelectionEvaluator")
    assert mapping["query"] == "$prompt"
    assert mapping["response"] == "$prediction"
    assert mapping["tool_calls"] == "$row.tool_calls"
    assert mapping["tool_definitions"] == "$row.tool_definitions"


def test_default_foundry_input_mapping_tool_input_accuracy() -> None:
    mapping = _default_foundry_input_mapping("ToolInputAccuracyEvaluator")
    assert mapping["query"] == "$prompt"
    assert mapping["response"] == "$prediction"
    assert mapping["tool_definitions"] == "$row.tool_definitions"
    assert "tool_calls" not in mapping


def test_cloud_evaluator_data_mapping_relevance_uses_context() -> None:
    mapping = _cloud_evaluator_data_mapping(
        "relevance", "input", "expected", context_field="context"
    )
    assert mapping["query"] == "{{item.input}}"
    assert mapping["response"] == "{{sample.output_text}}"
    assert mapping["context"] == "{{item.context}}"
    assert "ground_truth" not in mapping


def test_cloud_evaluator_data_mapping_retrieval_uses_context() -> None:
    mapping = _cloud_evaluator_data_mapping(
        "retrieval", "input", "expected", context_field="context"
    )
    assert mapping["context"] == "{{item.context}}"


def test_cloud_evaluator_data_mapping_tool_selection() -> None:
    mapping = _cloud_evaluator_data_mapping("tool_selection", "input", "expected")
    assert mapping["tool_calls"] == "{{sample.tool_calls}}"
    assert mapping["tool_definitions"] == "{{item.tool_definitions}}"


def test_cloud_evaluator_data_mapping_tool_input_accuracy() -> None:
    mapping = _cloud_evaluator_data_mapping("tool_input_accuracy", "input", "expected")
    assert mapping["query"] == "{{item.input}}"
    assert mapping["tool_definitions"] == "{{item.tool_definitions}}"
    assert "tool_calls" not in mapping


# ---------------------------------------------------------------------------
# Safety evaluator tests
# ---------------------------------------------------------------------------


def test_cloud_evaluator_data_mapping_violence() -> None:
    mapping = _cloud_evaluator_data_mapping("violence", "input", "expected")
    assert mapping["query"] == "{{item.input}}"
    assert mapping["response"] == "{{sample.output_text}}"
    assert "ground_truth" not in mapping
    assert "context" not in mapping
    assert "tool_calls" not in mapping


def test_cloud_evaluator_data_mapping_sexual() -> None:
    mapping = _cloud_evaluator_data_mapping("sexual", "input", "expected")
    assert mapping["query"] == "{{item.input}}"
    assert mapping["response"] == "{{sample.output_text}}"
    assert len(mapping) == 2


def test_cloud_evaluator_data_mapping_self_harm() -> None:
    mapping = _cloud_evaluator_data_mapping("self_harm", "input", "expected")
    assert mapping["query"] == "{{item.input}}"
    assert mapping["response"] == "{{sample.output_text}}"
    assert len(mapping) == 2


def test_cloud_evaluator_data_mapping_hate_unfairness() -> None:
    mapping = _cloud_evaluator_data_mapping("hate_unfairness", "input", "expected")
    assert mapping["query"] == "{{item.input}}"
    assert mapping["response"] == "{{sample.output_text}}"
    assert len(mapping) == 2


def test_cloud_evaluator_data_mapping_protected_material() -> None:
    mapping = _cloud_evaluator_data_mapping("protected_material", "input", "expected")
    assert mapping["query"] == "{{item.input}}"
    assert mapping["response"] == "{{sample.output_text}}"
    assert len(mapping) == 2


def test_cloud_evaluator_data_mapping_content_safety() -> None:
    mapping = _cloud_evaluator_data_mapping("content_safety", "input", "expected")
    assert mapping["query"] == "{{item.input}}"
    assert mapping["response"] == "{{sample.output_text}}"
    assert len(mapping) == 2


def test_cloud_evaluator_needs_model_safety_evaluators() -> None:
    """Safety evaluators use azure_ai_project, not a judge model."""
    safety_builtins = [
        "violence",
        "sexual",
        "self_harm",
        "hate_unfairness",
        "content_safety",
        "protected_material",
        "code_vulnerability",
        "ungrounded_attributes",
        "indirect_attack",
    ]
    for name in safety_builtins:
        assert not _cloud_evaluator_needs_model(name), f"{name} should not need a model"


def test_cloud_evaluator_needs_model_quality_evaluators() -> None:
    """Quality evaluators still need a model."""
    quality_builtins = ["similarity", "coherence", "fluency", "groundedness"]
    for name in quality_builtins:
        assert _cloud_evaluator_needs_model(name), f"{name} should need a model"


def test_cloud_evaluator_needs_model_nlp_evaluators() -> None:
    """NLP evaluators do not need a model."""
    nlp_builtins = [
        "f1_score",
        "bleu_score",
        "rouge_score",
        "meteor_score",
        "gleu_score",
    ]
    for name in nlp_builtins:
        assert not _cloud_evaluator_needs_model(name), f"{name} should not need a model"


def test_default_foundry_input_mapping_violence() -> None:
    mapping = _default_foundry_input_mapping("ViolenceEvaluator")
    assert mapping["query"] == "$prompt"
    assert mapping["response"] == "$prediction"
    assert "ground_truth" not in mapping
    assert "context" not in mapping


def test_default_foundry_input_mapping_sexual() -> None:
    mapping = _default_foundry_input_mapping("SexualEvaluator")
    assert mapping["query"] == "$prompt"
    assert mapping["response"] == "$prediction"
    assert len(mapping) == 2


def test_default_foundry_input_mapping_self_harm() -> None:
    mapping = _default_foundry_input_mapping("SelfHarmEvaluator")
    assert mapping == {"query": "$prompt", "response": "$prediction"}


def test_default_foundry_input_mapping_hate_unfairness() -> None:
    mapping = _default_foundry_input_mapping("HateUnfairnessEvaluator")
    assert mapping == {"query": "$prompt", "response": "$prediction"}


def test_default_foundry_input_mapping_protected_material() -> None:
    mapping = _default_foundry_input_mapping("ProtectedMaterialEvaluator")
    assert mapping == {"query": "$prompt", "response": "$prediction"}


def test_default_foundry_input_mapping_content_safety() -> None:
    mapping = _default_foundry_input_mapping("ContentSafetyEvaluator")
    assert mapping == {"query": "$prompt", "response": "$prediction"}


def test_default_foundry_input_mapping_groundedness_pro() -> None:
    mapping = _default_foundry_input_mapping("GroundednessProEvaluator")
    assert mapping == {"query": "$prompt", "response": "$prediction"}


# ---------------------------------------------------------------------------
# model_config auto-injection tests
# ---------------------------------------------------------------------------


def test_model_config_injected_for_all_ai_assisted_evaluators() -> None:
    """Verify model_config is auto-injected for ALL AI-assisted evaluators, not just 2."""
    import importlib as _real_importlib

    from agentops.backends.eval_engine import (
        _AI_ASSISTED_EVALUATORS,
        _load_foundry_evaluator_callable,
    )

    # Capture a direct reference to the real import_module BEFORE patching
    _orig_import_module = _real_importlib.import_module

    # Create a fake evaluator class that captures its kwargs
    class FakeEvaluator:
        def __init__(self, **kwargs):
            self.init_kwargs = kwargs

        def __call__(self, **kwargs):
            return {}

    # Create a fake module with all AI-assisted evaluator classes
    fake_module = SimpleNamespace(
        **{name: type(name, (FakeEvaluator,), {}) for name in _AI_ASSISTED_EVALUATORS}
    )

    # Only intercept "azure.ai.evaluation" imports, let everything else through
    def _selective_import(name, *args, **kwargs):
        if name == "azure.ai.evaluation":
            return fake_module
        return _orig_import_module(name, *args, **kwargs)

    for evaluator_name in _AI_ASSISTED_EVALUATORS:
        with (
            patch.dict(
                "os.environ",
                {
                    "AZURE_OPENAI_ENDPOINT": "https://test.openai.azure.com/",
                    "AZURE_OPENAI_DEPLOYMENT": "gpt-4o-mini",
                },
            ),
            patch(
                "agentops.backends.eval_engine.importlib.import_module",
                side_effect=_selective_import,
            ),
            patch(
                "agentops.backends.eval_engine._default_credential",
                return_value="fake-cred",
            ),
        ):
            evaluator = _load_foundry_evaluator_callable(
                evaluator_name=evaluator_name,
                evaluator_config={"kind": "builtin", "class_name": evaluator_name},
            )
            assert hasattr(evaluator, "init_kwargs"), (
                f"{evaluator_name}: expected FakeEvaluator instance"
            )
            assert "model_config" in evaluator.init_kwargs, (
                f"{evaluator_name}: model_config was NOT auto-injected"
            )
            mc = evaluator.init_kwargs["model_config"]
            assert mc["azure_endpoint"] == "https://test.openai.azure.com/"
            assert mc["azure_deployment"] == "gpt-4o-mini"
