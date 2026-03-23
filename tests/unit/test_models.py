from agentops.core.models import (
    BackendConfig,
    BundleConfig,
    DatasetConfig,
    RowMetricsResult,
    ThresholdRule,
)


def test_bundle_config_parses() -> None:
    data = {
        "version": 1,
        "name": "rag_baseline",
        "description": "Baseline eval",
        "evaluators": [
            {"name": "GroundednessEvaluator", "source": "foundry", "enabled": True},
            {"name": "exact_match", "source": "local", "enabled": True},
        ],
        "thresholds": [
            {"evaluator": "exact_match", "criteria": ">=", "value": 0.8},
        ],
        "metadata": {"category": "rag"},
    }

    bundle = BundleConfig.model_validate(data)
    assert bundle.name == "rag_baseline"
    assert bundle.evaluators[0].source == "foundry"
    assert bundle.thresholds[0].criteria == ">="


def test_bundle_config_accepts_foundry_evaluator_config() -> None:
    data = {
        "version": 1,
        "name": "qa_similarity",
        "evaluators": [
            {
                "name": "SimilarityEvaluator",
                "source": "foundry",
                "enabled": True,
                "config": {
                    "kind": "builtin",
                    "class_name": "SimilarityEvaluator",
                    "input_mapping": {
                        "query": "$prompt",
                        "response": "$prediction",
                        "ground_truth": "$expected",
                    },
                    "score_keys": ["similarity"],
                },
            }
        ],
        "thresholds": [
            {"evaluator": "SimilarityEvaluator", "criteria": ">=", "value": 3},
        ],
        "metadata": {},
    }

    bundle = BundleConfig.model_validate(data)
    assert bundle.evaluators[0].config["kind"] == "builtin"


def test_threshold_legacy_metric_operator_is_supported() -> None:
    rule = ThresholdRule.model_validate(
        {"metric": "groundedness", "operator": ">=", "value": 0.8}
    )
    assert rule.evaluator == "groundedness"
    assert rule.criteria == ">="


def test_threshold_operator_validation() -> None:
    try:
        ThresholdRule.model_validate(
            {"evaluator": "groundedness", "criteria": "!=", "value": 0.8}
        )
        assert False, "expected validation error"
    except Exception as exc:
        assert "criteria" in str(exc)


def test_threshold_value_must_be_numeric() -> None:
    try:
        ThresholdRule.model_validate(
            {"evaluator": "groundedness", "criteria": ">=", "value": "0.8"}
        )
        assert False, "expected validation error"
    except Exception as exc:
        assert "numeric" in str(exc)


def test_boolean_criteria_must_not_have_value() -> None:
    try:
        ThresholdRule.model_validate(
            {"evaluator": "exact_match", "criteria": "true", "value": 1}
        )
        assert False, "expected validation error"
    except Exception as exc:
        assert "must be omitted" in str(exc)


def test_row_metrics_requires_positive_row_index() -> None:
    try:
        RowMetricsResult.model_validate(
            {
                "row_index": 0,
                "metrics": [{"name": "exact_match", "value": 1.0}],
            }
        )
        assert False, "expected validation error"
    except Exception as exc:
        assert "row_index" in str(exc)


def test_dataset_config_parses() -> None:
    data = {
        "version": 1,
        "name": "smoke",
        "description": "Small smoke dataset",
        "source": {"type": "file", "path": "./eval/datasets/smoke.jsonl"},
        "format": {
            "type": "jsonl",
            "input_field": "input",
            "expected_field": "expected",
        },
        "metadata": {"size_hint": 20},
    }

    dataset = DatasetConfig.model_validate(data)
    assert dataset.source.path.name == "smoke.jsonl"
    assert dataset.format.context_field is None


def test_dataset_config_parses_context_field() -> None:
    data = {
        "version": 1,
        "name": "smoke-rag",
        "source": {"type": "file", "path": "./data/smoke-rag.jsonl"},
        "format": {
            "type": "jsonl",
            "input_field": "input",
            "expected_field": "expected",
            "context_field": "context",
        },
    }

    dataset = DatasetConfig.model_validate(data)
    assert dataset.format.context_field == "context"


def test_backend_requires_command_and_args_for_subprocess() -> None:
    try:
        BackendConfig.model_validate({"type": "subprocess"})
        assert False, "expected validation error"
    except Exception as exc:
        assert "backend.command" in str(exc) or "backend.args" in str(exc)


def test_backend_requires_agent_id_for_foundry() -> None:
    try:
        BackendConfig.model_validate({"type": "foundry"})
        assert False, "expected validation error"
    except Exception as exc:
        assert "backend.agent_id" in str(exc)


def test_backend_accepts_foundry_with_agent_id() -> None:
    backend = BackendConfig.model_validate(
        {
            "type": "foundry",
            "agent_id": "asst_abc123",
        }
    )
    assert backend.type == "foundry"
    assert backend.target == "agent"
    assert backend.agent_id == "asst_abc123"


def test_backend_rejects_placeholder_model_name() -> None:
    try:
        BackendConfig.model_validate(
            {
                "type": "foundry",
                "target": "model",
                "model": "<replace-with-your-foundry-model-deployment-name>",
            }
        )
        assert False, "expected validation error"
    except Exception as exc:
        assert "backend.model" in str(exc) or "deployment name" in str(exc)


def test_backend_rejects_unsupported_type() -> None:
    try:
        BackendConfig.model_validate({"type": "unknown"})
        assert False, "expected validation error"
    except Exception as exc:
        assert "Unsupported backend type" in str(exc)


def test_foundry_agent_target_requires_agent_id() -> None:
    try:
        BackendConfig.model_validate(
            {
                "type": "foundry",
                "target": "agent",
            }
        )
        assert False, "expected validation error"
    except Exception as exc:
        assert "backend.agent_id" in str(exc)


def test_foundry_agent_target_accepts_agent_id() -> None:
    backend = BackendConfig.model_validate(
        {
            "type": "foundry",
            "target": "agent",
            "agent_id": "asst_abc123",
        }
    )
    assert backend.target == "agent"
    assert backend.agent_id == "asst_abc123"


def test_foundry_accepts_model_target() -> None:
    backend = BackendConfig.model_validate(
        {
            "type": "foundry",
            "target": "model",
        }
    )
    assert backend.target == "model"
    assert backend.agent_id is None


def test_foundry_model_target_ignores_agent_id() -> None:
    backend = BackendConfig.model_validate(
        {
            "type": "foundry",
            "target": "model",
            "agent_id": "asst_abc123",
        }
    )
    assert backend.target == "model"
    assert backend.agent_id == "asst_abc123"


def test_foundry_rejects_invalid_target() -> None:
    try:
        BackendConfig.model_validate(
            {
                "type": "foundry",
                "target": "unknown",
            }
        )
        assert False, "expected validation error"
    except Exception as exc:
        assert "backend.target" in str(exc)
