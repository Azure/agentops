"""Optional OpenTelemetry instrumentation for AgentOps evaluation runs.

All OpenTelemetry imports are **lazy** - they only happen when tracing is
enabled via ``APPLICATIONINSIGHTS_CONNECTION_STRING`` (Azure Monitor) or
the ``AGENTOPS_OTLP_ENDPOINT`` environment variable. When neither variable
is set, every public function in this module is a no-op.

Schema design follows three OTel semantic convention layers:
https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/

* **CICD** (``cicd.pipeline.*``)  - the eval run as a pipeline
* **GenAI** (``gen_ai.*``)        - the agent/model invocation
* **AgentOps** (``agentops.eval.*``) - evaluation-specific (score, threshold)
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Generator, Optional
from urllib.parse import urlparse
from uuid import UUID

from agentops.utils.logging import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Lazy globals - initialised on first call to ``init_tracing()``
# ---------------------------------------------------------------------------
_tracer: Any = None
_tracing_enabled: bool = False


def is_enabled() -> bool:
    """Return True when tracing has been initialised."""
    return _tracing_enabled


def init_tracing() -> None:
    """Initialise tracing when Azure Monitor or OTLP export is configured.

    Resolution order for the App Insights connection string:

    1. ``APPLICATIONINSIGHTS_CONNECTION_STRING`` (or the AgentOps-prefixed
       variant) - explicit user configuration always wins.
    2. ``AGENTOPS_OTLP_ENDPOINT`` - use a generic OTLP/HTTP exporter.
    3. **Auto-discovery**: when neither of the above is set but
       ``AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`` is, ask the Foundry project
       (via the ``azure-ai-projects`` SDK) for the connection string of
       the Application Insights resource attached to it. This lets
       eval runs and watchdog analyses emit traces into the same App
       Insights the Foundry project already uses, without any extra
       configuration.

    Safe to call multiple times; only the first call has an effect.
    """
    global _tracer, _tracing_enabled  # noqa: PLW0603

    if _tracing_enabled:
        return

    appinsights_connection_string = os.getenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING"
    ) or os.getenv("AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING")
    if appinsights_connection_string and not is_appinsights_connection_string(
        appinsights_connection_string
    ):
        appinsights_connection_string = None
    otlp_endpoint = os.getenv("AGENTOPS_OTLP_ENDPOINT")

    if not appinsights_connection_string and not otlp_endpoint:
        # Fallback: ask the Foundry project for the App Insights it owns.
        try:
            from agentops.utils.foundry_discovery import (
                resolve_appinsights_connection_from_env,
            )
            appinsights_connection_string = resolve_appinsights_connection_from_env()
        except Exception:  # noqa: BLE001
            # Discovery is best-effort - never raise into init_tracing.
            appinsights_connection_string = None

    if not appinsights_connection_string and not otlp_endpoint:
        return

    # Opt into Azure's "experimental" GenAI tracing flag by default. This
    # tells the OTel instrumentation to capture prompt + response content
    # as span attributes (not just metadata), which is exactly what an
    # eval / watchdog workflow needs to inspect a failing row in the
    # Foundry portal. The flag is "experimental" only in the sense that
    # Azure may change the underlying schema - not that it is unsafe.
    # Users who want to opt out can set the env var to "false" explicitly.
    os.environ.setdefault("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "true")
    os.environ.setdefault("OTEL_SERVICE_NAME", "agentops")

    try:
        from opentelemetry import trace
    except ImportError:
        # opentelemetry not installed - tracing stays disabled
        return

    if appinsights_connection_string:
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor

            kwargs = {"connection_string": appinsights_connection_string}
            resource = _agentops_resource()
            if resource is not None:
                kwargs["resource"] = resource
            configure_azure_monitor(**kwargs)
            _tracer = trace.get_tracer("agentops")
            _tracing_enabled = True
            return
        except ImportError:
            # Azure Monitor exporter not installed - try OTLP below if configured.
            pass
        except Exception as exc:  # noqa: BLE001
            log.warning("Azure Monitor tracing disabled: %s", exc)
            appinsights_connection_string = None

    if not otlp_endpoint:
        return

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        import agentops

        resource = Resource(
            attributes={
                "service.name": "agentops",
                "service.version": getattr(agentops, "__version__", "0.0.0"),
            }
        )

        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint + "/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        _tracer = trace.get_tracer("agentops")
        _tracing_enabled = True
    except ImportError:
        # OTLP exporter not installed - tracing stays disabled
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("OTLP tracing disabled: %s", exc)


def is_appinsights_connection_string(value: str) -> bool:
    """Return True for real App Insights connection strings.

    CI systems can leave undefined variables as literal placeholders such
    as ``$(APPLICATIONINSIGHTS_CONNECTION_STRING)``. Treat those as absent
    so Foundry auto-discovery still has a chance to configure telemetry.
    """
    parts = _appinsights_connection_string_parts(value)
    instrumentation_key = parts.get("instrumentationkey")
    if not instrumentation_key:
        return False
    try:
        UUID(instrumentation_key)
    except ValueError:
        return False
    ingestion_endpoint = parts.get("ingestionendpoint")
    if ingestion_endpoint:
        parsed = urlparse(ingestion_endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
    return True


def _is_appinsights_connection_string(value: str) -> bool:
    return is_appinsights_connection_string(value)


def _appinsights_connection_string_parts(value: str) -> dict[str, str]:
    value = value.strip()
    if not value or "$(" in value or "${{" in value:
        return {}
    parts: dict[str, str] = {}
    for segment in value.split(";"):
        segment = segment.strip()
        if not segment:
            continue
        if "=" not in segment:
            return {}
        key, part_value = segment.split("=", 1)
        key = key.strip().lower()
        if not key:
            return {}
        parts[key] = part_value.strip()
    return parts


def _agentops_resource() -> Optional[Any]:
    try:
        from opentelemetry.sdk.resources import Resource
        import agentops
    except Exception:  # noqa: BLE001
        return None
    return Resource.create(
        {
            "service.name": "agentops",
            "service.version": getattr(agentops, "__version__", "0.0.0"),
        }
    )


def shutdown() -> None:
    """Flush and shut down the tracer provider."""
    if not _tracing_enabled:
        return
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        if hasattr(provider, "shutdown"):
            provider.shutdown()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Span context managers
# ---------------------------------------------------------------------------


@contextmanager
def eval_run_span(
    *,
    bundle_name: str,
    dataset_name: str,
    backend_type: str,
    target: str,
    model: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Generator[Optional[Any], None, None]:
    """Root span for an evaluation run (CICD pipeline run)."""
    if not _tracing_enabled or _tracer is None:
        yield None
        return

    from opentelemetry.trace import SpanKind, StatusCode

    with _tracer.start_as_current_span(
        f"RUN {bundle_name}",
        kind=SpanKind.SERVER,
    ) as span:
        # CICD semconv
        span.set_attribute("cicd.pipeline.name", bundle_name)
        span.set_attribute("cicd.pipeline.action.name", "RUN")

        # AgentOps evaluation attributes
        span.set_attribute("agentops.eval.dataset", dataset_name)
        span.set_attribute("agentops.eval.backend", backend_type)
        span.set_attribute("agentops.eval.target", target)
        if model:
            span.set_attribute("agentops.eval.model", model)
        if agent_id:
            span.set_attribute("agentops.eval.agent_id", agent_id)

        try:
            yield span
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise


def set_eval_run_result(
    span: Any,
    *,
    passed: bool,
    items_total: int,
    items_passed: int,
) -> None:
    """Set final result attributes on the root eval run span."""
    if span is None:
        return

    from opentelemetry.trace import StatusCode

    span.set_attribute("cicd.pipeline.result", "success" if passed else "failure")
    span.set_attribute("agentops.eval.items_total", items_total)
    span.set_attribute("agentops.eval.items_passed", items_passed)
    if items_total > 0:
        span.set_attribute("agentops.eval.pass_rate", items_passed / items_total)

    if passed:
        span.set_status(StatusCode.OK)
    else:
        span.set_status(StatusCode.ERROR, "Threshold failure")


@contextmanager
def eval_item_span(
    *,
    row_index: int,
    input_text: Optional[str] = None,
    expected_text: Optional[str] = None,
) -> Generator[Optional[Any], None, None]:
    """Span for a single evaluation item (CICD task run)."""
    if not _tracing_enabled or _tracer is None:
        yield None
        return

    from opentelemetry.trace import SpanKind, StatusCode

    _label = f"eval_item {row_index}"
    if input_text:
        _snippet = input_text[:60].replace("\n", " ")
        if len(input_text) > 60:
            _snippet += "\u2026"
        _label = f"{_label} - '{_snippet}'"

    with _tracer.start_as_current_span(
        _label,
        kind=SpanKind.SERVER,
    ) as span:
        # CICD task attributes
        span.set_attribute("cicd.pipeline.task.name", "eval_item")
        span.set_attribute("cicd.pipeline.task.run.id", str(row_index))

        # AgentOps item attributes
        span.set_attribute("agentops.eval.item.index", row_index)
        if input_text:
            span.set_attribute("agentops.eval.item.input", input_text)
        if expected_text:
            span.set_attribute("agentops.eval.item.expected", expected_text)

        try:
            yield span
        except Exception as exc:
            span.set_attribute("cicd.pipeline.task.run.result", "failure")
            span.set_attribute("agentops.eval.item.passed", False)
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise


def set_eval_item_result(span: Any, *, passed: bool) -> None:
    """Set final result on an eval item span."""
    if span is None:
        return
    from opentelemetry.trace import StatusCode

    span.set_attribute(
        "cicd.pipeline.task.run.result", "success" if passed else "failure"
    )
    span.set_attribute("agentops.eval.item.passed", passed)
    span.set_status(StatusCode.OK if passed else StatusCode.ERROR)


@contextmanager
def agent_invoke_span(
    *,
    target: str,
    model: Optional[str] = None,
    agent_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    agent_version: Optional[str] = None,
    provider: str = "azure.ai.inference",
) -> Generator[Optional[Any], None, None]:
    """Span for agent/model invocation (GenAI semconv)."""
    if not _tracing_enabled or _tracer is None:
        yield None
        return

    from opentelemetry.trace import SpanKind

    operation = "invoke_agent" if target == "agent" else "chat"
    span_name = f"{operation} {agent_name or model or 'unknown'}"

    with _tracer.start_as_current_span(
        span_name,
        kind=SpanKind.CLIENT,
    ) as span:
        # GenAI semconv
        span.set_attribute("gen_ai.operation.name", operation)
        span.set_attribute("gen_ai.provider.name", provider)
        if model:
            span.set_attribute("gen_ai.request.model", model)
        if agent_id:
            span.set_attribute("gen_ai.agent.id", agent_id)
        if agent_name:
            span.set_attribute("gen_ai.agent.name", agent_name)
        if agent_version:
            span.set_attribute("gen_ai.agent.version", agent_version)

        yield span


def set_agent_invoke_result(
    span: Any,
    *,
    response_model: Optional[str] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
) -> None:
    """Set GenAI response attributes on an agent invoke span."""
    if span is None:
        return
    if response_model:
        span.set_attribute("gen_ai.response.model", response_model)
    if input_tokens is not None:
        span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    if output_tokens is not None:
        span.set_attribute("gen_ai.usage.output_tokens", output_tokens)


def record_evaluator_span(
    *,
    evaluator_name: str,
    builtin_name: str,
    source: str,
    score: float,
    threshold: Optional[float] = None,
    criteria: Optional[str] = None,
    passed: Optional[bool] = None,
) -> None:
    """Create a child span for a single evaluator result."""
    if not _tracing_enabled or _tracer is None:
        return

    from opentelemetry.trace import SpanKind

    with _tracer.start_as_current_span(
        f"evaluator {builtin_name}",
        kind=SpanKind.INTERNAL,
    ) as span:
        span.set_attribute("agentops.eval.evaluator.name", evaluator_name)
        span.set_attribute("agentops.eval.evaluator.builtin", builtin_name)
        span.set_attribute("agentops.eval.evaluator.source", source)
        span.set_attribute("agentops.eval.evaluator.score", score)
        if threshold is not None:
            span.set_attribute("agentops.eval.evaluator.threshold", threshold)
        if criteria is not None:
            span.set_attribute("agentops.eval.evaluator.criteria", criteria)
        if passed is not None:
            span.set_attribute("agentops.eval.evaluator.passed", passed)


# ---------------------------------------------------------------------------
# Doctor finding spans
# ---------------------------------------------------------------------------


def record_agent_finding_span(finding: Any) -> None:
    """Create a queryable child span for a single ``agentops doctor`` finding."""
    if not _tracing_enabled or _tracer is None:
        return

    from opentelemetry.trace import SpanKind, StatusCode

    finding_id = str(getattr(finding, "id", "") or "unknown")
    severity = getattr(finding, "severity", None)
    category = getattr(finding, "category", None)
    severity_value = str(getattr(severity, "value", severity) or "")
    category_value = str(getattr(category, "value", category) or "")

    with _tracer.start_as_current_span(
        f"doctor finding {finding_id}",
        kind=SpanKind.INTERNAL,
    ) as span:
        span.set_attribute("agentops.agent.finding.id", finding_id)
        span.set_attribute("agentops.agent.finding.severity", severity_value)
        span.set_attribute("agentops.agent.finding.category", category_value)
        span.set_attribute("agentops.agent.finding.title", str(getattr(finding, "title", "") or ""))
        span.set_attribute("agentops.agent.finding.summary", str(getattr(finding, "summary", "") or ""))
        span.set_attribute(
            "agentops.agent.finding.recommendation",
            str(getattr(finding, "recommendation", "") or ""),
        )
        span.set_attribute("agentops.agent.finding.source", str(getattr(finding, "source", "") or ""))
        span.set_status(StatusCode.OK)


# ---------------------------------------------------------------------------
# Watchdog agent spans
# ---------------------------------------------------------------------------


@contextmanager
def agent_analyze_span(
    *,
    workspace: str,
    lookback_days: Optional[int] = None,
) -> Generator[Optional[Any], None, None]:
    """Root span for a watchdog ``agentops doctor`` run.

    Mirrors :func:`eval_run_span` for the watchdog: when telemetry is
    enabled (``APPLICATIONINSIGHTS_CONNECTION_STRING`` or
    ``AGENTOPS_OTLP_ENDPOINT`` set) the span carries source-collection
    and finding-distribution attributes so analyses are queryable
    alongside the evaluation runs they observe.
    """
    if not _tracing_enabled or _tracer is None:
        yield None
        return

    from opentelemetry.trace import SpanKind, StatusCode

    with _tracer.start_as_current_span(
        "ANALYZE watchdog",
        kind=SpanKind.SERVER,
    ) as span:
        span.set_attribute("cicd.pipeline.name", "agentops.agent.analyze")
        span.set_attribute("cicd.pipeline.action.name", "ANALYZE")
        span.set_attribute("agentops.agent.workspace", workspace)
        if lookback_days is not None:
            span.set_attribute("agentops.agent.lookback_days", lookback_days)

        try:
            yield span
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise


def set_agent_analyze_result(
    span: Any,
    *,
    findings_total: int,
    by_severity: dict,
    by_category: dict,
    max_severity: Optional[str],
    sources_enabled: list,
) -> None:
    """Set final attributes on a watchdog analyze span."""
    if span is None:
        return

    from opentelemetry.trace import StatusCode

    span.set_attribute("agentops.agent.findings_total", findings_total)
    for severity, count in by_severity.items():
        span.set_attribute(f"agentops.agent.findings.severity.{severity}", count)
    for category, count in by_category.items():
        span.set_attribute(f"agentops.agent.findings.category.{category}", count)
    if max_severity is not None:
        span.set_attribute("agentops.agent.max_severity", max_severity)
    span.set_attribute(
        "agentops.agent.sources_enabled", ",".join(sorted(sources_enabled))
    )
    # The watchdog itself completes successfully even when findings exist  -
    # finding severity is observability, not pipeline failure.
    span.set_status(StatusCode.OK)
