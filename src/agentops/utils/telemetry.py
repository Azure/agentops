"""Optional OpenTelemetry instrumentation for AgentOps evaluation runs.

All OpenTelemetry imports are **lazy** — they only happen when tracing is
enabled via the ``AGENTOPS_OTLP_ENDPOINT`` environment variable.  When the
variable is unset, every public function in this module is a no-op.

Schema design follows three OTel semantic convention layers:
https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/

* **CICD** (``cicd.pipeline.*``)  — the eval run as a pipeline
* **GenAI** (``gen_ai.*``)        — the agent/model invocation
* **AgentOps** (``agentops.eval.*``) — evaluation-specific (score, threshold)
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Generator, Optional

# ---------------------------------------------------------------------------
# Lazy globals — initialised on first call to ``init_tracing()``
# ---------------------------------------------------------------------------
_tracer: Any = None
_tracing_enabled: bool = False


def is_enabled() -> bool:
    """Return True when OTLP tracing has been initialised."""
    return _tracing_enabled


def init_tracing() -> None:
    """Initialise the OTLP exporter if ``AGENTOPS_OTLP_ENDPOINT`` is set.

    Safe to call multiple times; only the first call has an effect.
    """
    global _tracer, _tracing_enabled  # noqa: PLW0603

    if _tracing_enabled:
        return

    endpoint = os.getenv("AGENTOPS_OTLP_ENDPOINT")
    if not endpoint:
        return

    try:
        from opentelemetry import trace
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
        exporter = OTLPSpanExporter(endpoint=endpoint + "/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        _tracer = trace.get_tracer("agentops")
        _tracing_enabled = True
    except ImportError:
        # opentelemetry not installed — tracing stays disabled
        pass


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

    from opentelemetry.trace import SpanKind

    # Build a descriptive span name: "eval_item 1: What is the weather..."
    span_name = f"eval_item {row_index}"
    if input_text:
        truncated = input_text[:80] + ("..." if len(input_text) > 80 else "")
        span_name = f"eval_item {row_index}: {truncated}"

    with _tracer.start_as_current_span(
        span_name,
        kind=SpanKind.INTERNAL,
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

        yield span


def set_eval_item_result(span: Any, *, passed: bool) -> None:
    """Set final result on an eval item span."""
    if span is None:
        return
    span.set_attribute(
        "cicd.pipeline.task.run.result", "success" if passed else "failure"
    )
    span.set_attribute("agentops.eval.item.passed", passed)


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
