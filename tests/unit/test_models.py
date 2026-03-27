from agentops.core.models import (
    BundleConfig,
    BundleRef,
    DatasetConfig,
    DatasetRef,
    ExecutionConfig,
    LocalAdapterConfig,
    RunConfig,
    RowMetricsResult,
    TargetConfig,
    TargetEndpointConfig,
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


def test_endpoint_rejects_placeholder_model_name() -> None:
    try:
        TargetEndpointConfig.model_validate(
            {
                "kind": "foundry_agent",
                "model": "<replace-with-your-foundry-model-deployment-name>",
            }
        )
        assert False, "expected validation error"
    except Exception as exc:
        assert "deployment name" in str(exc)


def test_target_remote_requires_endpoint() -> None:
    try:
        TargetConfig.model_validate(
            {
                "type": "agent",
                "hosting": "foundry",
                "execution_mode": "remote",
            }
        )
        assert False, "expected validation error"
    except Exception as exc:
        assert "endpoint" in str(exc)


def test_target_local_requires_local_config() -> None:
    try:
        TargetConfig.model_validate(
            {
                "type": "model",
                "hosting": "local",
                "execution_mode": "local",
            }
        )
        assert False, "expected validation error"
    except Exception as exc:
        assert "local" in str(exc)


def test_target_agent_mode_only_for_foundry() -> None:
    try:
        TargetConfig.model_validate(
            {
                "type": "agent",
                "hosting": "local",
                "execution_mode": "local",
                "agent_mode": "hosted",
                "local": {"adapter": "python run.py"},
            }
        )
        assert False, "expected validation error"
    except Exception as exc:
        assert "agent_mode" in str(exc)


def test_target_framework_only_for_agent() -> None:
    try:
        TargetConfig.model_validate(
            {
                "type": "model",
                "hosting": "local",
                "execution_mode": "local",
                "framework": "langgraph",
                "local": {"adapter": "python run.py"},
            }
        )
        assert False, "expected validation error"
    except Exception as exc:
        assert "framework" in str(exc)


def test_foundry_agent_endpoint_parses() -> None:
    endpoint = TargetEndpointConfig.model_validate(
        {
            "kind": "foundry_agent",
            "agent_id": "my-agent:3",
            "model": "gpt-4o",
            "project_endpoint_env": "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        }
    )
    assert endpoint.kind == "foundry_agent"
    assert endpoint.agent_id == "my-agent:3"
    assert endpoint.model == "gpt-4o"


def test_http_endpoint_accepts_url() -> None:
    endpoint = TargetEndpointConfig.model_validate(
        {"kind": "http", "url": "http://localhost:8080/chat"}
    )
    assert endpoint.kind == "http"
    assert endpoint.url == "http://localhost:8080/chat"


def test_http_endpoint_accepts_url_env() -> None:
    endpoint = TargetEndpointConfig.model_validate(
        {"kind": "http", "url_env": "AGENT_HTTP_URL"}
    )
    assert endpoint.kind == "http"
    assert endpoint.url_env == "AGENT_HTTP_URL"


def test_http_endpoint_requires_url_or_url_env() -> None:
    try:
        TargetEndpointConfig.model_validate({"kind": "http"})
        assert False, "expected validation error"
    except Exception as exc:
        assert "url" in str(exc).lower()


def test_http_endpoint_accepts_all_optional_fields() -> None:
    endpoint = TargetEndpointConfig.model_validate(
        {
            "kind": "http",
            "url": "http://localhost/chat",
            "request_field": "query",
            "response_field": "output.text",
            "headers": {"X-Custom": "value"},
            "auth_header_env": "MY_TOKEN",
            "tool_calls_field": "metadata.tool_calls",
            "extra_fields": ["session_id", "user_id"],
        }
    )
    assert endpoint.request_field == "query"
    assert endpoint.response_field == "output.text"
    assert endpoint.tool_calls_field == "metadata.tool_calls"
    assert endpoint.extra_fields == ["session_id", "user_id"]
    assert endpoint.headers == {"X-Custom": "value"}


def test_target_remote_foundry_agent_parses() -> None:
    target = TargetConfig.model_validate(
        {
            "type": "agent",
            "hosting": "foundry",
            "execution_mode": "remote",
            "agent_mode": "hosted",
            "endpoint": {
                "kind": "foundry_agent",
                "agent_id": "my-agent:3",
                "model": "gpt-4o",
            },
        }
    )
    assert target.type == "agent"
    assert target.hosting == "foundry"
    assert target.execution_mode == "remote"
    assert target.agent_mode == "hosted"
    assert target.endpoint is not None
    assert target.endpoint.agent_id == "my-agent:3"


def test_target_remote_http_parses() -> None:
    target = TargetConfig.model_validate(
        {
            "type": "model",
            "hosting": "local",
            "execution_mode": "remote",
            "endpoint": {
                "kind": "http",
                "url": "http://localhost:8080/chat",
            },
        }
    )
    assert target.type == "model"
    assert target.endpoint is not None
    assert target.endpoint.kind == "http"


def test_target_local_adapter_parses() -> None:
    target = TargetConfig.model_validate(
        {
            "type": "model",
            "hosting": "local",
            "execution_mode": "local",
            "local": {"adapter": "python my_adapter.py"},
        }
    )
    assert target.type == "model"
    assert target.execution_mode == "local"
    assert target.local is not None
    assert target.local.adapter == "python my_adapter.py"


def test_bundle_ref_requires_name_or_path() -> None:
    try:
        BundleRef.model_validate({})
        assert False, "expected validation error"
    except Exception as exc:
        assert "name" in str(exc) or "path" in str(exc)


def test_bundle_ref_accepts_name() -> None:
    ref = BundleRef.model_validate({"name": "model_quality_baseline"})
    assert ref.name == "model_quality_baseline"
    assert ref.path is None


def test_bundle_ref_accepts_path() -> None:
    ref = BundleRef.model_validate({"path": "bundles/custom.yaml"})
    assert ref.path is not None
    assert ref.name is None


def test_dataset_ref_requires_name_or_path() -> None:
    try:
        DatasetRef.model_validate({})
        assert False, "expected validation error"
    except Exception as exc:
        assert "name" in str(exc) or "path" in str(exc)


def test_run_config_parses() -> None:
    data = {
        "version": 1,
        "target": {
            "type": "model",
            "hosting": "foundry",
            "execution_mode": "remote",
            "endpoint": {
                "kind": "foundry_agent",
                "model": "gpt-4o",
            },
        },
        "bundle": {"name": "model_quality_baseline"},
        "dataset": {"name": "smoke-model-direct"},
    }
    run_config = RunConfig.model_validate(data)
    assert run_config.version == 1
    assert run_config.target.type == "model"
    assert run_config.bundle.name == "model_quality_baseline"
    assert run_config.dataset.name == "smoke-model-direct"
    assert run_config.execution.timeout_seconds == 300
    assert run_config.output.write_report is True


def test_execution_config_defaults() -> None:
    cfg = ExecutionConfig.model_validate({})
    assert cfg.concurrency == 1
    assert cfg.timeout_seconds == 300


# ---- LocalAdapterConfig validation ----


def test_local_adapter_config_adapter_only() -> None:
    cfg = LocalAdapterConfig.model_validate({"adapter": "python run.py"})
    assert cfg.adapter == "python run.py"
    assert cfg.callable is None


def test_local_adapter_config_callable_only() -> None:
    cfg = LocalAdapterConfig.model_validate({"callable": "my_module:run_eval"})
    assert cfg.callable == "my_module:run_eval"
    assert cfg.adapter is None


def test_local_adapter_config_both_fails() -> None:
    try:
        LocalAdapterConfig.model_validate(
            {"adapter": "python run.py", "callable": "my_module:run_eval"}
        )
        assert False, "expected validation error"
    except Exception as exc:
        assert "not both" in str(exc)


def test_local_adapter_config_neither_fails() -> None:
    try:
        LocalAdapterConfig.model_validate({})
        assert False, "expected validation error"
    except Exception as exc:
        assert "adapter" in str(exc) or "callable" in str(exc)


def test_local_adapter_config_callable_bad_format() -> None:
    try:
        LocalAdapterConfig.model_validate({"callable": "no_colon_here"})
        assert False, "expected validation error"
    except Exception as exc:
        assert "module:function" in str(exc)


def test_local_adapter_config_callable_empty_parts() -> None:
    try:
        LocalAdapterConfig.model_validate({"callable": ":func"})
        assert False, "expected validation error"
    except Exception as exc:
        assert "module:function" in str(exc)


def test_local_adapter_config_callable_empty_string() -> None:
    try:
        LocalAdapterConfig.model_validate({"callable": "  "})
        assert False, "expected validation error"
    except Exception as exc:
        assert "non-empty" in str(exc)


def test_target_local_with_callable_parses() -> None:
    target = TargetConfig.model_validate(
        {
            "type": "model",
            "hosting": "local",
            "execution_mode": "local",
            "local": {"callable": "my_workflow:run_evaluation"},
        }
    )
    assert target.local is not None
    assert target.local.callable == "my_workflow:run_evaluation"
    assert target.local.adapter is None
