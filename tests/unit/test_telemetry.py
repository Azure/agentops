"""Tests for OTLP telemetry instrumentation."""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentops.agent.config import AzureMonitorSourceConfig
from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.sources import azure_monitor
from agentops.core.agentops_config import AgentOpsConfig
from agentops.pipeline.orchestrator import RunOptions, run_evaluation
from agentops.utils import telemetry
from agentops.utils.telemetry import (
    eval_item_span,
    eval_run_span,
    init_tracing,
    is_enabled,
    record_evaluator_span,
    record_agent_finding_span,
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

    def test_record_agent_finding_span_noop(self) -> None:
        finding = Finding(
            id="quality-regression",
            severity=Severity.WARNING,
            title="Quality regressed",
            summary="Score dropped.",
            recommendation="Review the failing eval rows.",
            source="results_history",
            category=Category.QUALITY,
        )

        record_agent_finding_span(finding)


class TestInitTracingWithoutEndpoint:
    def test_no_init_without_env_var(self) -> None:
        # Ensure the env var is not set
        env = os.environ.copy()
        env.pop("AGENTOPS_OTLP_ENDPOINT", None)
        env.pop("APPLICATIONINSIGHTS_CONNECTION_STRING", None)
        env.pop("AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING", None)
        env.pop("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", None)
        with patch.dict(os.environ, env, clear=True):
            # Reset module state
            import agentops.utils.telemetry as tel

            tel._tracing_enabled = False
            tel._tracer = None

            init_tracing()
            assert is_enabled() is False

    def test_placeholder_app_insights_env_var_is_ignored(self, monkeypatch) -> None:
        import agentops.utils.telemetry as tel

        tel._tracing_enabled = False
        tel._tracer = None

        monkeypatch.setenv(
            "APPLICATIONINSIGHTS_CONNECTION_STRING",
            "$(APPLICATIONINSIGHTS_CONNECTION_STRING)",
        )
        monkeypatch.delenv(
            "AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING",
            raising=False,
        )
        monkeypatch.delenv("AGENTOPS_OTLP_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)

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

        calls = {
            call.args[0]: call.args[1]
            for call in self.mock_span.set_attribute.call_args_list
        }
        assert calls["cicd.pipeline.task.name"] == "eval_item"
        assert calls["cicd.pipeline.task.run.id"] == "3"
        assert calls["agentops.eval.item.index"] == 3
        assert calls["agentops.eval.item.input"] == "What is 2+2?"
        assert calls["agentops.eval.item.expected"] == "4"

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

    def test_record_agent_finding_span(self) -> None:
        finding = Finding(
            id="quality-regression",
            severity=Severity.WARNING,
            title="Quality regressed",
            summary="Score dropped.",
            recommendation="Review the failing eval rows.",
            source="results_history",
            category=Category.QUALITY,
        )

        record_agent_finding_span(finding)

        self.mock_tracer.start_as_current_span.assert_called_with(
            "doctor finding quality-regression",
            kind=pytest.importorskip("opentelemetry.trace").SpanKind.INTERNAL,
        )

        calls = {
            call.args[0]: call.args[1]
            for call in self.mock_span.set_attribute.call_args_list
        }
        assert calls["agentops.agent.finding.id"] == "quality-regression"
        assert calls["agentops.agent.finding.severity"] == "warning"
        assert calls["agentops.agent.finding.category"] == "quality"
        assert calls["agentops.agent.finding.title"] == "Quality regressed"
        assert calls["agentops.agent.finding.recommendation"] == "Review the failing eval rows."

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


def test_application_insights_connection_string_initializes_azure_monitor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    trace_module = types.ModuleType("opentelemetry.trace")
    trace_module.get_tracer = lambda name: ("tracer", name)  # type: ignore[attr-defined]

    opentelemetry_module = types.ModuleType("opentelemetry")
    opentelemetry_module.trace = trace_module  # type: ignore[attr-defined]
    opentelemetry_sdk_module = types.ModuleType("opentelemetry.sdk")
    resources_module = types.ModuleType("opentelemetry.sdk.resources")

    class Resource:
        @classmethod
        def create(cls, attributes: dict[str, str]) -> tuple[str, dict[str, str]]:
            calls["resource_attributes"] = attributes
            return ("resource", attributes)

    setattr(resources_module, "Resource", Resource)

    azure_module = types.ModuleType("azure")
    azure_monitor_module = types.ModuleType("azure.monitor")
    azure_monitor_otel_module = types.ModuleType("azure.monitor.opentelemetry")

    def configure_azure_monitor(**kwargs: object) -> None:
        connection_string = kwargs["connection_string"]
        calls["connection_string"] = connection_string
        calls["resource"] = kwargs.get("resource")

    setattr(
        azure_monitor_otel_module,
        "configure_azure_monitor",
        configure_azure_monitor,
    )

    monkeypatch.setitem(sys.modules, "opentelemetry", opentelemetry_module)
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", trace_module)
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk", opentelemetry_sdk_module)
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk.resources", resources_module)
    monkeypatch.setitem(sys.modules, "azure", azure_module)
    monkeypatch.setitem(sys.modules, "azure.monitor", azure_monitor_module)
    monkeypatch.setitem(
        sys.modules, "azure.monitor.opentelemetry", azure_monitor_otel_module
    )
    monkeypatch.setattr(telemetry, "_tracer", None)
    monkeypatch.setattr(telemetry, "_tracing_enabled", False)
    monkeypatch.setenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "InstrumentationKey=00000000-0000-0000-0000-000000000000",
    )
    monkeypatch.delenv("AGENTOPS_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)

    init_tracing()

    assert calls["connection_string"] == (
        "InstrumentationKey=00000000-0000-0000-0000-000000000000"
    )
    assert calls["resource"] is not None
    resource_attributes = calls["resource_attributes"]
    assert isinstance(resource_attributes, dict)
    assert resource_attributes["service.name"] == "agentops"
    assert "service.version" in resource_attributes
    assert is_enabled() is True
    # Default-on for GenAI tracing so the Azure SDK warning ("Set
    # AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true …") is silenced and
    # prompts/responses are captured as span attributes.
    assert os.environ.get("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING") == "true"
    assert os.environ.get("OTEL_SERVICE_NAME") == "agentops"


def test_genai_tracing_env_var_not_overwritten_if_user_set_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User-provided AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING wins."""
    trace_module = types.ModuleType("opentelemetry.trace")
    trace_module.get_tracer = lambda name: ("tracer", name)  # type: ignore[attr-defined]
    opentelemetry_module = types.ModuleType("opentelemetry")
    opentelemetry_module.trace = trace_module  # type: ignore[attr-defined]

    azure_module = types.ModuleType("azure")
    azure_monitor_module = types.ModuleType("azure.monitor")
    azure_monitor_otel_module = types.ModuleType("azure.monitor.opentelemetry")
    setattr(
        azure_monitor_otel_module,
        "configure_azure_monitor",
        lambda **_: None,
    )

    monkeypatch.setitem(sys.modules, "opentelemetry", opentelemetry_module)
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", trace_module)
    monkeypatch.setitem(sys.modules, "azure", azure_module)
    monkeypatch.setitem(sys.modules, "azure.monitor", azure_monitor_module)
    monkeypatch.setitem(
        sys.modules, "azure.monitor.opentelemetry", azure_monitor_otel_module
    )
    monkeypatch.setattr(telemetry, "_tracer", None)
    monkeypatch.setattr(telemetry, "_tracing_enabled", False)
    monkeypatch.setenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "InstrumentationKey=test",
    )
    monkeypatch.setenv("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "false")
    monkeypatch.delenv("AGENTOPS_OTLP_ENDPOINT", raising=False)

    init_tracing()

    # User opt-out preserved.
    assert os.environ["AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING"] == "false"


def test_azure_monitor_queries_requests_and_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {"queries": []}

    azure_module = types.ModuleType("azure")
    identity_module = types.ModuleType("azure.identity")
    monitor_module = types.ModuleType("azure.monitor")
    query_module = types.ModuleType("azure.monitor.query")

    class DefaultAzureCredential:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class LogsQueryStatus:
        FAILURE = "Failure"

    class Column:
        def __init__(self, name: str) -> None:
            self.name = name

    class Table:
        columns = [
            Column("request_count"),
            Column("error_count"),
            Column("avg_duration_ms"),
            Column("p95_duration_ms"),
        ]
        rows = [[2, 1, 1000.0, 2500.0]]

    class Response:
        status = "Success"
        tables = [Table()]

    class LogsQueryClient:
        def __init__(self, _credential: object) -> None:
            pass

        def query_resource(
            self,
            *,
            resource_id: str,
            query: str,
            timespan: object,
        ) -> Response:
            captured["resource_id"] = resource_id
            captured["queries"].append(query)  # type: ignore[union-attr]
            captured["timespan"] = str(timespan)
            return Response()

    identity_module.DefaultAzureCredential = DefaultAzureCredential  # type: ignore[attr-defined]
    query_module.LogsQueryClient = LogsQueryClient  # type: ignore[attr-defined]
    query_module.LogsQueryStatus = LogsQueryStatus  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "azure", azure_module)
    monkeypatch.setitem(sys.modules, "azure.identity", identity_module)
    monkeypatch.setitem(sys.modules, "azure.monitor", monitor_module)
    monkeypatch.setitem(sys.modules, "azure.monitor.query", query_module)

    payload = azure_monitor.collect_azure_monitor(
        AzureMonitorSourceConfig(
            enabled=True,
            app_insights_resource_id=(
                "/subscriptions/000/resourceGroups/rg/providers/"
                "Microsoft.Insights/components/appi"
            ),
        ),
        lookback_days=7,
    )

    assert any(
        "union isfuzzy=true requests, dependencies" in str(q)
        for q in captured["queries"]  # type: ignore[union-attr]
    )
    assert payload.diagnostics["status"] == "ok"
    assert payload.request_count == 2
    assert payload.error_count == 1
    assert payload.error_rate == 0.5
    assert payload.avg_duration_seconds == 1.0
    assert payload.p95_duration_seconds == 2.5


def test_azure_monitor_uses_connection_string_application_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[str] = []

    def fake_query(application_id: str, bearer: str, query: str) -> dict[str, object]:
        assert application_id == "app-from-env"
        assert bearer == "fake-bearer"
        queries.append(query)
        if "request_count" in query:
            return _app_insights_result(
                {
                    "request_count": 4,
                    "error_count": 1,
                    "avg_duration_ms": 1500.0,
                    "p95_duration_ms": 3200.0,
                }
            )
        if "content_filter" in query:
            return _app_insights_result({"hits": 2})
        if "input_tokens" in query:
            return _app_insights_result(
                {"input_tokens": 120, "output_tokens": 45}
            )
        return _app_insights_result({"hits": 3})

    monkeypatch.setenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "InstrumentationKey=ikey;ApplicationId=app-from-env",
    )
    monkeypatch.setattr(
        azure_monitor,
        "_acquire_application_insights_token",
        lambda: "fake-bearer",
    )
    monkeypatch.setattr(
        azure_monitor,
        "_query_application_insights",
        fake_query,
    )

    payload = azure_monitor.collect_azure_monitor(
        AzureMonitorSourceConfig(enabled=True),
        lookback_days=7,
    )

    assert payload.diagnostics["status"] == "ok"
    assert payload.diagnostics["target_kind"] == "application_id"
    assert payload.diagnostics["target_source"] == "APPLICATIONINSIGHTS_CONNECTION_STRING"
    assert payload.request_count == 4
    assert payload.error_count == 1
    assert payload.error_rate == 0.25
    assert payload.avg_duration_seconds == 1.5
    assert payload.p95_duration_seconds == 3.2
    assert payload.safety_violations == [{"signal": "content_filter", "hits": 2}]
    assert payload.input_token_count == 120
    assert payload.output_token_count == 45
    assert payload.rate_limit_429_count == 3
    assert any("union isfuzzy=true requests, dependencies" in q for q in queries)


def test_azure_monitor_uses_foundry_discovered_application_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentops.utils import foundry_discovery

    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.setattr(
        foundry_discovery,
        "resolve_appinsights_connection_from_env_with_reason",
        lambda: ("InstrumentationKey=ikey;ApplicationId=app-from-foundry", None),
    )
    monkeypatch.setattr(
        azure_monitor,
        "_acquire_application_insights_token",
        lambda: "fake-bearer",
    )
    monkeypatch.setattr(
        azure_monitor,
        "_query_application_insights",
        lambda *_args: _app_insights_result(
            {
                "request_count": 1,
                "error_count": 0,
                "avg_duration_ms": 100.0,
                "p95_duration_ms": 100.0,
            }
        ),
    )

    payload = azure_monitor.collect_azure_monitor(
        AzureMonitorSourceConfig(enabled=True),
        lookback_days=7,
    )

    assert payload.diagnostics["status"] == "ok"
    assert payload.diagnostics["target"] == "app-from-foundry"
    assert payload.diagnostics["target_source"] == "foundry_project_telemetry"
    assert payload.request_count == 1


def test_azure_monitor_skipped_when_connection_string_lacks_application_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentops.utils import foundry_discovery

    monkeypatch.setenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "InstrumentationKey=ikey",
    )
    monkeypatch.setattr(
        foundry_discovery,
        "resolve_appinsights_connection_from_env_with_reason",
        lambda: (None, "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT is not set"),
    )

    payload = azure_monitor.collect_azure_monitor(
        AzureMonitorSourceConfig(enabled=True),
        lookback_days=7,
    )

    assert payload.diagnostics["status"] == "skipped"
    assert "no App Insights ApplicationId" in payload.diagnostics["reason"]
    assert payload.diagnostics["discovery_reason"]


def _app_insights_result(row: dict[str, object]) -> dict[str, object]:
    return {
        "tables": [
            {
                "columns": [{"name": name} for name in row],
                "rows": [list(row.values())],
            }
        ]
    }


def test_run_evaluation_flushes_telemetry_on_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(telemetry, "init_tracing", lambda: events.append("init"))
    monkeypatch.setattr(telemetry, "shutdown", lambda: events.append("shutdown"))

    config = AgentOpsConfig(
        version=1,
        agent="model:gpt-4o-mini",
        dataset=tmp_path / "missing.jsonl",
    )
    options = RunOptions(
        config_path=tmp_path / "agentops.yaml",
        output_dir=tmp_path / "out",
    )

    with pytest.raises(FileNotFoundError):
        run_evaluation(config, options=options)

    assert events == ["init", "shutdown"]
