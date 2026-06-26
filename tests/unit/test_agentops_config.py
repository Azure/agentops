"""Tests for the flat ``agentops.yaml`` schema and agent classifier."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agentops.core.agentops_config import (
    AgentOpsConfig,
    DatasetSyncConfig,
    ObservabilityConfig,
    PromptAgentBootstrap,
    RubricConfig,
    RubricDimensionConfig,
    Threshold,
    classify_agent,
)


# ---------------------------------------------------------------------------
# classify_agent
# ---------------------------------------------------------------------------


class TestClassifyAgent:
    def test_foundry_prompt_name_version(self) -> None:
        result = classify_agent("my-rag:3")
        assert result.kind == "foundry_prompt"
        assert result.name == "my-rag"
        assert result.version == "3"
        assert result.protocol is None

    def test_foundry_prompt_rejects_empty_parts(self) -> None:
        with pytest.raises(ValueError, match="name:version"):
            classify_agent(":3")
        with pytest.raises(ValueError, match="name:version"):
            classify_agent("foo:")

    def test_model_direct(self) -> None:
        result = classify_agent("model:gpt-4o-mini")
        assert result.kind == "model_direct"
        assert result.deployment == "gpt-4o-mini"
        assert result.protocol is None

    def test_model_direct_rejects_empty_deployment(self) -> None:
        with pytest.raises(ValueError, match="deployment name"):
            classify_agent("model:")

    def test_foundry_hosted_default_protocol_responses(self) -> None:
        url = "https://my-project.services.ai.azure.com/agents/foo"
        result = classify_agent(url)
        assert result.kind == "foundry_hosted"
        assert result.protocol == "responses"
        assert result.url == url

    def test_foundry_hosted_invocations(self) -> None:
        url = "https://my-project.services.ai.azure.com/agents/foo"
        result = classify_agent(url, protocol="invocations")
        assert result.kind == "foundry_hosted"
        assert result.protocol == "invocations"

    def test_foundry_hosted_rejects_http_json_protocol(self) -> None:
        url = "https://my-project.services.ai.azure.com/agents/foo"
        with pytest.raises(ValueError, match="responses"):
            classify_agent(url, protocol="http-json")

    def test_http_json_default_protocol(self) -> None:
        url = "https://my-app.azurecontainerapps.io/chat"
        result = classify_agent(url)
        assert result.kind == "http_json"
        assert result.protocol == "http-json"

    def test_http_json_rejects_responses_protocol(self) -> None:
        url = "https://my-app.azurecontainerapps.io/chat"
        with pytest.raises(ValueError, match="http-json"):
            classify_agent(url, protocol="responses")

    def test_unrecognized_value(self) -> None:
        with pytest.raises(ValueError, match="unrecognized"):
            classify_agent("just-a-name")


# ---------------------------------------------------------------------------
# Threshold parser
# ---------------------------------------------------------------------------


class TestThresholdFromExpression:
    @pytest.mark.parametrize(
        "expression, expected_criteria, expected_value",
        [
            (">=3", ">=", 3.0),
            ("<=10", "<=", 10.0),
            (">2.5", ">", 2.5),
            ("<0.7", "<", 0.7),
            ("==1", "==", 1.0),
            (" >= 3 ", ">=", 3.0),
        ],
    )
    def test_comparison(
        self, expression: str, expected_criteria: str, expected_value: float
    ) -> None:
        threshold = Threshold.from_expression("metric", expression)
        assert threshold.criteria == expected_criteria
        assert threshold.value == expected_value

    def test_bool_true(self) -> None:
        threshold = Threshold.from_expression("metric", True)
        assert threshold.criteria == "true"
        assert threshold.value is None

    def test_bool_false_string(self) -> None:
        threshold = Threshold.from_expression("metric", "false")
        assert threshold.criteria == "false"

    def test_number_shorthand(self) -> None:
        # bare number defaults to >=
        threshold = Threshold.from_expression("metric", 3)
        assert threshold.criteria == ">="
        assert threshold.value == 3.0

    def test_invalid_expression(self) -> None:
        with pytest.raises(ValueError, match="expected"):
            Threshold.from_expression("metric", "approximately 3")

    def test_invalid_number(self) -> None:
        with pytest.raises(ValueError, match="cannot parse"):
            Threshold.from_expression("metric", ">=abc")


# ---------------------------------------------------------------------------
# AgentOpsConfig
# ---------------------------------------------------------------------------


class TestAgentOpsConfig:
    def test_minimal_config(self, tmp_path) -> None:
        cfg = AgentOpsConfig(version=1, agent="my-rag:3", dataset="./qa.jsonl")
        assert cfg.version == 1
        assert cfg.agent == "my-rag:3"
        assert cfg.thresholds == {}
        assert cfg.response_source == "agent"
        assert cfg.telemetry_imports == []

    def test_accepts_telemetry_import_config(self) -> None:
        cfg = AgentOpsConfig.model_validate(
            {
                "version": 1,
                "agent": "my-rag:3",
                "dataset": "./qa.jsonl",
                "response_source": "dataset",
                "telemetry_imports": [
                    {
                        "name": "prod",
                        "source": "azure-monitor",
                        "target": "application-insights",
                        "resource_id": "$APPINSIGHTS_RESOURCE_ID",
                        "time_range": {"lookback_days": 14},
                        "filters": {"customDimensions.agent": "support"},
                        "fields": {
                            "input": "customDimensions.question",
                            "response": "customDimensions.answer",
                        },
                        "privacy": {"redact_fields": ["token"], "max_field_length": 500},
                        "output": {
                            "path": ".agentops/data/prod.jsonl",
                            "label_mode": "pending",
                        },
                    }
                ],
            }
        )

        item = cfg.telemetry_imports[0]
        assert cfg.response_source == "dataset"
        assert item.name == "prod"
        assert item.source == "azure-monitor"
        assert item.target == "application-insights"
        assert item.resource_id == "$APPINSIGHTS_RESOURCE_ID"
        assert item.time_range.lookback_days == 14
        assert item.output.label_mode == "pending"

    def test_telemetry_import_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            AgentOpsConfig.model_validate(
                {
                    "version": 1,
                    "agent": "my-rag:3",
                    "dataset": "./qa.jsonl",
                    "telemetry_imports": [
                        {
                            "name": "prod",
                            "target": "log-analytics",
                            "workspace_id": "workspace",
                            "surprise": True,
                        }
                    ],
                }
            )

    def test_telemetry_import_time_range_requires_one_mode(self) -> None:
        with pytest.raises(ValidationError, match="cannot mix"):
            AgentOpsConfig.model_validate(
                {
                    "version": 1,
                    "agent": "my-rag:3",
                    "dataset": "./qa.jsonl",
                    "telemetry_imports": [
                        {
                            "name": "prod",
                            "target": "log-analytics",
                            "workspace_id": "workspace",
                            "time_range": {
                                "from": "2026-06-01T00:00:00Z",
                                "to": "2026-06-02T00:00:00Z",
                                "lookback_days": 7,
                            },
                        }
                    ],
                }
            )

    def test_telemetry_import_accepts_explicit_time_range(self) -> None:
        cfg = AgentOpsConfig.model_validate(
            {
                "version": 1,
                "agent": "my-rag:3",
                "dataset": "./qa.jsonl",
                "telemetry_imports": [
                    {
                        "name": "prod",
                        "target": "log-analytics",
                        "workspace_id": "workspace",
                        "time_range": {
                            "from": "2026-06-01T00:00:00Z",
                            "to": "2026-06-02T00:00:00Z",
                        },
                    }
                ],
            }
        )

        time_range = cfg.telemetry_imports[0].time_range
        assert time_range.from_ == "2026-06-01T00:00:00Z"
        assert time_range.to == "2026-06-02T00:00:00Z"
        assert time_range.lookback_days is None

    def test_resolved_target(self) -> None:
        cfg = AgentOpsConfig(version=1, agent="my-rag:3", dataset="./qa.jsonl")
        target = cfg.resolved_target()
        assert target.kind == "foundry_prompt"

    def test_accepts_prompt_file_for_prompt_agent_cicd(self) -> None:
        cfg = AgentOpsConfig(
            version=1,
            agent="my-rag:3",
            dataset="./qa.jsonl",
            prompt_file=".agentops/prompts/agent-instructions.md",
        )
        assert cfg.prompt_file == Path(".agentops/prompts/agent-instructions.md")

    def test_rejects_legacy_keys(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            AgentOpsConfig.model_validate(
                {
                    "version": 1,
                    "agent": "my-rag:3",
                    "dataset": "./qa.jsonl",
                    "scenario": "rag",
                }
            )
        assert "legacy" in str(exc_info.value).lower()

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            AgentOpsConfig.model_validate(
                {
                    "version": 1,
                    "agent": "my-rag:3",
                    "dataset": "./qa.jsonl",
                    "unknown_key": "x",
                }
            )

    def test_rejects_wrong_version(self) -> None:
        with pytest.raises(ValidationError, match="version must be 1"):
            AgentOpsConfig(version=2, agent="my-rag:3", dataset="./qa.jsonl")

    def test_thresholds_parsed(self) -> None:
        cfg = AgentOpsConfig(
            version=1,
            agent="my-rag:3",
            dataset="./qa.jsonl",
            thresholds={"groundedness": ">=3", "coherence": ">=3.5"},
        )
        parsed = {t.metric: t for t in cfg.parsed_thresholds()}
        assert parsed["groundedness"].criteria == ">="
        assert parsed["groundedness"].value == 3.0
        assert parsed["coherence"].value == 3.5

    def test_publish_true_with_default_execution(self) -> None:
        """publish: true defaults to execution: local → Classic Foundry."""
        cfg = AgentOpsConfig(
            version=1,
            agent="my-rag:3",
            dataset="./qa.jsonl",
            publish=True,
            project_endpoint="https://x.services.ai.azure.com/api/projects/p",
        )
        assert cfg.publish is True
        assert cfg.execution == "local"
        assert cfg.publish_target() == "foundry"
        assert cfg.project_endpoint.endswith("/projects/p")

    def test_publish_true_with_cloud_execution(self) -> None:
        """publish: true + execution: cloud → New Foundry."""
        cfg = AgentOpsConfig(
            version=1,
            agent="my-rag:3",
            dataset="./qa.jsonl",
            execution="cloud",
            publish=True,
        )
        assert cfg.publish_target() == "foundry_cloud"

    def test_cloud_execution_implies_publish(self) -> None:
        """execution: cloud auto-enables publish when not specified."""
        cfg = AgentOpsConfig(
            version=1,
            agent="my-rag:3",
            dataset="./qa.jsonl",
            execution="cloud",
        )
        assert cfg.publish is True
        assert cfg.publish_target() == "foundry_cloud"
        assert cfg.dataset_sync.mode == "auto"

    def test_dataset_sync_accepts_inline_mode(self) -> None:
        cfg = AgentOpsConfig(
            version=1,
            agent="my-rag:3",
            dataset="./qa.jsonl",
            dataset_sync=DatasetSyncConfig(
                mode="inline",
                name="agentops-qa",
                version="content-hash",
            ),
        )

        assert cfg.dataset_sync.mode == "inline"
        assert cfg.dataset_sync.name == "agentops-qa"

    def test_dataset_sync_rejects_empty_name(self) -> None:
        with pytest.raises(ValidationError, match="dataset_sync.name"):
            AgentOpsConfig.model_validate(
                {
                    "version": 1,
                    "agent": "my-rag:3",
                    "dataset": "./qa.jsonl",
                    "dataset_sync": {"mode": "inline", "name": " "},
                }
            )

    def test_accepts_build_2026_eval_metadata(self) -> None:
        cfg = AgentOpsConfig(
            version=1,
            agent="travel-agent:2",
            dataset=".agentops/data/conversations.jsonl",
            dataset_kind="multi-turn",
            rubrics=[
                RubricConfig(
                    name="travel-concierge-quality",
                    description="Travel planning behavior",
                    dimensions=[
                        RubricDimensionConfig(
                            name="task_success",
                            description="Completes the requested travel planning task.",
                            weight=0.5,
                        ),
                        RubricDimensionConfig(
                            name="tone",
                            description="Uses concise and helpful travel-advisor tone.",
                            weight=0.2,
                        ),
                    ],
                    evaluator="builtin.rubric",
                )
            ],
            observability=ObservabilityConfig(
                tracing_enabled=True,
                trace_sampling={"enabled": True, "mode": "foundry"},
                trace_replay_url="https://ai.azure.com/project/traces/trace-1",
            ),
            thresholds={"task_success": ">=4"},
        )

        assert cfg.dataset_kind == "multi-turn"
        assert cfg.rubrics[0].name == "travel-concierge-quality"
        assert cfg.rubrics[0].dimensions[0].weight == 0.5
        assert cfg.observability.tracing_enabled is True
        assert cfg.observability.trace_sampling.enabled is True

    def test_observability_rejects_non_url_links(self) -> None:
        with pytest.raises(ValidationError, match="observability URLs"):
            AgentOpsConfig.model_validate(
                {
                    "version": 1,
                    "agent": "travel-agent:2",
                    "dataset": ".agentops/data/smoke.jsonl",
                    "observability": {"trace_replay_url": "ai.azure.com/traces"},
                }
            )

    def test_rubric_rejects_empty_dimension(self) -> None:
        with pytest.raises(ValidationError, match="rubric dimension"):
            AgentOpsConfig.model_validate(
                {
                    "version": 1,
                    "agent": "travel-agent:2",
                    "dataset": ".agentops/data/smoke.jsonl",
                    "rubrics": [
                        {
                            "name": "travel",
                            "dimensions": [{"name": " ", "description": "score"}],
                        }
                    ],
                }
            )

    def test_prompt_agent_bootstrap_defaults_to_none(self) -> None:
        cfg = AgentOpsConfig(version=1, agent="my-rag:3", dataset="./qa.jsonl")
        assert cfg.prompt_agent_bootstrap is None

    def test_prompt_agent_bootstrap_accepts_model_only(self) -> None:
        cfg = AgentOpsConfig.model_validate(
            {
                "version": 1,
                "agent": "travel-agent:1",
                "dataset": "./qa.jsonl",
                "prompt_agent_bootstrap": {"model": "gpt-4o-mini"},
            }
        )
        assert isinstance(cfg.prompt_agent_bootstrap, PromptAgentBootstrap)
        assert cfg.prompt_agent_bootstrap.model == "gpt-4o-mini"
        assert cfg.prompt_agent_bootstrap.description is None
        assert cfg.prompt_agent_bootstrap.model_parameters is None
        assert cfg.prompt_agent_bootstrap.tools is None

    def test_prompt_agent_bootstrap_accepts_full_payload(self) -> None:
        cfg = AgentOpsConfig.model_validate(
            {
                "version": 1,
                "agent": "travel-agent:1",
                "dataset": "./qa.jsonl",
                "prompt_agent_bootstrap": {
                    "model": "gpt-4o-mini",
                    "description": "Plans short trips and explains tradeoffs.",
                    "model_parameters": {"temperature": 0.2, "top_p": 0.9},
                    "tools": [{"type": "function", "name": "lookup"}],
                },
            }
        )
        bootstrap = cfg.prompt_agent_bootstrap
        assert bootstrap is not None
        assert bootstrap.model_parameters == {"temperature": 0.2, "top_p": 0.9}
        assert bootstrap.tools == [{"type": "function", "name": "lookup"}]

    def test_prompt_agent_bootstrap_rejects_empty_model(self) -> None:
        with pytest.raises(ValidationError):
            AgentOpsConfig.model_validate(
                {
                    "version": 1,
                    "agent": "travel-agent:1",
                    "dataset": "./qa.jsonl",
                    "prompt_agent_bootstrap": {"model": "   "},
                }
            )

    def test_prompt_agent_bootstrap_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            AgentOpsConfig.model_validate(
                {
                    "version": 1,
                    "agent": "travel-agent:1",
                    "dataset": "./qa.jsonl",
                    "prompt_agent_bootstrap": {
                        "model": "gpt-4o-mini",
                        "totally_unknown": True,
                    },
                }
            )

    def test_cloud_execution_rejects_publish_false(self) -> None:
        """execution: cloud + publish: false is a contradiction."""
        with pytest.raises(ValidationError, match="always publishes"):
            AgentOpsConfig(
                version=1,
                agent="my-rag:3",
                dataset="./qa.jsonl",
                execution="cloud",
                publish=False,
            )

    def test_azd_execution_accepts_foundry_prompt_with_recipe(self) -> None:
        cfg = AgentOpsConfig(
            version=1,
            agent="my-rag:3",
            dataset="./qa.jsonl",
            execution="azd",
            eval_recipe="src/my-rag/eval.yaml",
        )
        assert cfg.execution == "azd"
        assert cfg.eval_recipe == Path("src/my-rag/eval.yaml")
        assert cfg.publish_target() is None

    def test_azd_execution_rejects_http_target(self) -> None:
        with pytest.raises(ValidationError, match="execution: azd"):
            AgentOpsConfig(
                version=1,
                agent="https://example.com/chat",
                dataset="./qa.jsonl",
                execution="azd",
            )

    def test_auto_execution_is_allowed_for_model_target(self) -> None:
        cfg = AgentOpsConfig(
            version=1,
            agent="model:gpt-4o",
            dataset="./qa.jsonl",
            execution="auto",
        )
        assert cfg.execution == "auto"

    def test_publish_defaults_to_false(self) -> None:
        cfg = AgentOpsConfig(version=1, agent="my-rag:3", dataset="./qa.jsonl")
        assert cfg.publish is False
        assert cfg.execution == "local"
        assert cfg.publish_target() is None
        assert cfg.project_endpoint is None

    def test_publish_rejects_string_values(self) -> None:
        """publish must be a boolean — no legacy string aliases."""
        with pytest.raises(ValidationError):
            AgentOpsConfig.model_validate(
                {
                    "version": 1,
                    "agent": "my-rag:3",
                    "dataset": "./qa.jsonl",
                    "publish": "foundry",
                }
            )

    def test_protocol_rejected_for_prompt_agent(self) -> None:
        with pytest.raises(ValidationError, match="prompt agent"):
            AgentOpsConfig(
                version=1,
                agent="my-rag:3",
                dataset="./qa.jsonl",
                protocol="responses",
            )

    def test_protocol_rejected_for_model_direct(self) -> None:
        with pytest.raises(ValidationError, match="protocol"):
            AgentOpsConfig(
                version=1,
                agent="model:gpt-4o",
                dataset="./qa.jsonl",
                protocol="http-json",
            )

    def test_http_fields_allowed_for_http_target(self) -> None:
        cfg = AgentOpsConfig(
            version=1,
            agent="https://my-app.azurecontainerapps.io/chat",
            dataset="./qa.jsonl",
            request_field="message",
            response_field="text",
            response_fields={"context": "retrieval.context"},
        )
        assert cfg.request_field == "message"
        assert cfg.response_fields == {"context": "retrieval.context"}

    def test_http_fields_rejected_for_prompt_agent(self) -> None:
        with pytest.raises(ValidationError, match="HTTP/JSON"):
            AgentOpsConfig(
                version=1,
                agent="my-rag:3",
                dataset="./qa.jsonl",
                response_fields={"context": "context"},
            )

    def test_streaming_fields_allowed_for_http_target(self) -> None:
        cfg = AgentOpsConfig(
            version=1,
            agent="https://app.example.com/orchestrator",
            dataset="./qa.jsonl",
            request_field="ask",
            response_mode="text",
            stream={"strip_leading_token": True},  # type: ignore[arg-type]
            auth_header_env="ORCH_KEY",
            auth_header_name="X-API-KEY",
            auth_value_template="{token}",
        )
        assert cfg.response_mode == "text"
        assert cfg.stream is not None
        assert cfg.stream.strip_leading_token is True
        assert cfg.auth_header_name == "X-API-KEY"
        assert cfg.auth_value_template == "{token}"

    def test_response_mode_defaults_to_json(self) -> None:
        cfg = AgentOpsConfig(
            version=1,
            agent="https://app.example.com/chat",
            dataset="./qa.jsonl",
        )
        assert cfg.response_mode == "json"
        assert cfg.stream is None

    def test_response_mode_rejected_for_prompt_agent(self) -> None:
        with pytest.raises(ValidationError, match="HTTP/JSON"):
            AgentOpsConfig(
                version=1,
                agent="my-rag:3",
                dataset="./qa.jsonl",
                response_mode="sse",
            )

    def test_stream_block_rejected_for_model_target(self) -> None:
        with pytest.raises(ValidationError, match="HTTP/JSON"):
            AgentOpsConfig(
                version=1,
                agent="model:gpt-4o",
                dataset="./qa.jsonl",
                stream={"done_marker": "[DONE]"},  # type: ignore[arg-type]
            )

    def test_auth_header_name_rejected_for_prompt_agent(self) -> None:
        with pytest.raises(ValidationError, match="HTTP/JSON"):
            AgentOpsConfig(
                version=1,
                agent="my-rag:3",
                dataset="./qa.jsonl",
                auth_header_name="X-API-KEY",
            )

    def test_evaluators_override(self) -> None:
        cfg = AgentOpsConfig(
            version=1,
            agent="my-rag:3",
            dataset="./qa.jsonl",
            evaluators=[{"name": "GroundednessEvaluator"}],  # type: ignore[list-item]
        )
        assert cfg.evaluators is not None
        assert cfg.evaluators[0].name == "GroundednessEvaluator"
