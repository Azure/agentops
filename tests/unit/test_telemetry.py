"""Tests for OTLP telemetry instrumentation."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from agentops.utils.telemetry import (
    eval_item_span,
    eval_run_span,
    init_tracing,
    is_enabled,
    record_evaluator_span,
    set_eval_item_result,
    set_eval_run_result,
)


class TestTracingDisabledByDefault:
    """When AGENTOPS_OTLP_ENDPOINT is unset, all functions are no-ops."""

    def setup_method(self) -> None:
        import agentops.utils.telemetry as tel

        tel._tracing_enabled = False
        tel._tracer = None

    def test_is_enabled_returns_false(self) -> None:
        assert is_enabled() is False

    def test_eval_run_span_yields_none(self) -> None:
        with eval_run_span(
            bundle_name="test",
            dataset_name="test",
            backend_type="foundry",
            target="model",
        ) as span:
            assert span is None

    def test_eval_item_span_yields_none(self) -> None:
        with eval_item_span(row_index=1) as span:
            assert span is None

    def test_set_eval_run_result_noop(self) -> None:
        # Should not raise
        set_eval_run_result(None, passed=True, items_total=5, items_passed=5)

    def test_set_eval_item_result_noop(self) -> None:
        set_eval_item_result(None, passed=True)

    def test_record_evaluator_span_noop(self) -> None:
        # Should not raise
        record_evaluator_span(
            evaluator_name="SimilarityEvaluator",
            builtin_name="similarity",
            source="foundry",
            score=4.0,
            threshold=3.0,
            criteria=">=",
            passed=True,
        )


class TestInitTracingWithoutEndpoint:
    def test_no_init_without_env_var(self) -> None:
        # Ensure the env var is not set
        env = os.environ.copy()
        env.pop("AGENTOPS_OTLP_ENDPOINT", None)
        with patch.dict(os.environ, env, clear=True):
            # Reset module state
            import agentops.utils.telemetry as tel

            tel._tracing_enabled = False
            tel._tracer = None

            init_tracing()
            assert is_enabled() is False


class TestInitTracingWithoutOtelInstalled:
    def test_graceful_when_otel_missing(self) -> None:
        import agentops.utils.telemetry as tel

        tel._tracing_enabled = False
        tel._tracer = None

        with patch.dict(
            os.environ, {"AGENTOPS_OTLP_ENDPOINT": "http://localhost:4318"}
        ):
            # Simulate opentelemetry not installed
            with patch.dict("sys.modules", {"opentelemetry": None}):
                init_tracing()
                assert is_enabled() is False


class TestSpanAttributesWhenEnabled:
    """Test that span context managers set correct attributes when tracing is enabled.

    These tests require opentelemetry to be installed because the code paths
    import SpanKind/StatusCode when tracing is enabled.
    """

    otel = pytest.importorskip("opentelemetry")

    def setup_method(self) -> None:
        """Mock the tracing module to simulate enabled state."""
        import agentops.utils.telemetry as tel

        self.mock_span = MagicMock()
        self.mock_span.__enter__ = MagicMock(return_value=self.mock_span)
        self.mock_span.__exit__ = MagicMock(return_value=False)

        self.mock_tracer = MagicMock()
        self.mock_tracer.start_as_current_span.return_value = self.mock_span

        tel._tracing_enabled = True
        tel._tracer = self.mock_tracer

    def teardown_method(self) -> None:
        import agentops.utils.telemetry as tel

        tel._tracing_enabled = False
        tel._tracer = None

    def test_eval_run_span_sets_cicd_attributes(self) -> None:
        with eval_run_span(
            bundle_name="model_direct",
            dataset_name="smoke",
            backend_type="foundry",
            target="model",
            model="gpt-4.1",
        ) as span:
            assert span is self.mock_span

        # Verify CICD semconv attributes
        calls = {
            call.args[0]: call.args[1]
            for call in self.mock_span.set_attribute.call_args_list
        }
        assert calls["cicd.pipeline.name"] == "model_direct"
        assert calls["cicd.pipeline.action.name"] == "RUN"
        assert calls["agentops.eval.dataset"] == "smoke"
        assert calls["agentops.eval.backend"] == "foundry"
        assert calls["agentops.eval.target"] == "model"
        assert calls["agentops.eval.model"] == "gpt-4.1"

    def test_eval_run_span_sets_agent_id(self) -> None:
        with eval_run_span(
            bundle_name="agent_test",
            dataset_name="smoke",
            backend_type="foundry",
            target="agent",
            agent_id="my-agent:3",
        ):
            pass

        calls = {
            call.args[0]: call.args[1]
            for call in self.mock_span.set_attribute.call_args_list
        }
        assert calls["agentops.eval.agent_id"] == "my-agent:3"
        assert calls["agentops.eval.target"] == "agent"

    def test_eval_item_span_sets_task_attributes(self) -> None:
        with eval_item_span(
            row_index=3,
            input_text="What is 2+2?",
            expected_text="4",
        ) as span:
            assert span is self.mock_span

        # Verify span name includes input text
        span_name = self.mock_tracer.start_as_current_span.call_args.args[0]
        assert span_name == "eval_item 3: What is 2+2?"

        calls = {
            call.args[0]: call.args[1]
            for call in self.mock_span.set_attribute.call_args_list
        }
        assert calls["cicd.pipeline.task.name"] == "eval_item"
        assert calls["cicd.pipeline.task.run.id"] == "3"
        assert calls["agentops.eval.item.index"] == 3
        assert calls["agentops.eval.item.input"] == "What is 2+2?"
        assert calls["agentops.eval.item.expected"] == "4"

    def test_eval_item_span_name_without_input(self) -> None:
        with eval_item_span(row_index=5) as span:
            assert span is self.mock_span

        span_name = self.mock_tracer.start_as_current_span.call_args.args[0]
        assert span_name == "eval_item 5"

    def test_set_eval_run_result_pass(self) -> None:
        set_eval_run_result(
            self.mock_span,
            passed=True,
            items_total=5,
            items_passed=5,
        )

        calls = {
            call.args[0]: call.args[1]
            for call in self.mock_span.set_attribute.call_args_list
        }
        assert calls["cicd.pipeline.result"] == "success"
        assert calls["agentops.eval.items_total"] == 5
        assert calls["agentops.eval.items_passed"] == 5
        assert calls["agentops.eval.pass_rate"] == 1.0

    def test_set_eval_run_result_fail(self) -> None:
        set_eval_run_result(
            self.mock_span,
            passed=False,
            items_total=5,
            items_passed=3,
        )

        calls = {
            call.args[0]: call.args[1]
            for call in self.mock_span.set_attribute.call_args_list
        }
        assert calls["cicd.pipeline.result"] == "failure"
        assert calls["agentops.eval.items_passed"] == 3
        assert calls["agentops.eval.pass_rate"] == 0.6

    def test_set_eval_item_result(self) -> None:
        set_eval_item_result(self.mock_span, passed=False)

        calls = {
            call.args[0]: call.args[1]
            for call in self.mock_span.set_attribute.call_args_list
        }
        assert calls["cicd.pipeline.task.run.result"] == "failure"
        assert calls["agentops.eval.item.passed"] is False

    def test_record_evaluator_span(self) -> None:
        record_evaluator_span(
            evaluator_name="SimilarityEvaluator",
            builtin_name="similarity",
            source="foundry",
            score=4.0,
            threshold=3.0,
            criteria=">=",
            passed=True,
        )

        # Verify a child span was created
        self.mock_tracer.start_as_current_span.assert_called_with(
            "evaluator similarity",
            kind=pytest.importorskip("opentelemetry.trace").SpanKind.INTERNAL,
        )

        calls = {
            call.args[0]: call.args[1]
            for call in self.mock_span.set_attribute.call_args_list
        }
        assert calls["agentops.eval.evaluator.name"] == "SimilarityEvaluator"
        assert calls["agentops.eval.evaluator.builtin"] == "similarity"
        assert calls["agentops.eval.evaluator.source"] == "foundry"
        assert calls["agentops.eval.evaluator.score"] == 4.0
        assert calls["agentops.eval.evaluator.threshold"] == 3.0
        assert calls["agentops.eval.evaluator.criteria"] == ">="
        assert calls["agentops.eval.evaluator.passed"] is True

    def test_eval_run_span_name(self) -> None:
        with eval_run_span(
            bundle_name="my_bundle",
            dataset_name="smoke",
            backend_type="foundry",
            target="model",
        ):
            pass

        self.mock_tracer.start_as_current_span.assert_called_once()
        span_name = self.mock_tracer.start_as_current_span.call_args.args[0]
        assert span_name == "RUN my_bundle"
