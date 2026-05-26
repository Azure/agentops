"""Errors / failure rate check."""

from __future__ import annotations

from typing import List, Optional

from agentops.agent.config import ErrorsCheckConfig
from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.sources.azure_monitor import AzureMonitorPayload
from agentops.agent.sources.foundry_control import FoundryControlPayload


def run_errors_check(
    monitor: Optional[AzureMonitorPayload],
    foundry: Optional[FoundryControlPayload],
    config: ErrorsCheckConfig,
) -> List[Finding]:
    findings: List[Finding] = []

    if (
        monitor
        and monitor.error_rate is not None
        and monitor.error_rate > config.rate_threshold
    ):
        severity = (
            Severity.CRITICAL
            if monitor.error_rate >= config.rate_threshold * 2
            else Severity.WARNING
        )
        findings.append(
            Finding(
                id="errors.production_rate",
                severity=severity,
                category=Category.RELIABILITY,
                title="Production error rate above threshold",
                summary=(
                    f"App Insights reports {monitor.error_count} failed "
                    f"requests over {monitor.request_count} total "
                    f"({monitor.error_rate * 100:.2f}%), above the "
                    f"{config.rate_threshold * 100:.2f}% threshold."
                ),
                recommendation=(
                    "Open the App Insights resource, group failures by "
                    "operation, and inspect the most common exception "
                    "type."
                ),
                source="azure_monitor",
                evidence={
                    "error_count": monitor.error_count,
                    "request_count": monitor.request_count,
                    "error_rate": monitor.error_rate,
                    "threshold": config.rate_threshold,
                },
            )
        )

    if (
        foundry
        and foundry.failure_rate is not None
        and foundry.failure_rate > config.rate_threshold
    ):
        findings.append(
            Finding(
                id="errors.foundry_runs",
                severity=Severity.WARNING,
                category=Category.RELIABILITY,
                title="Foundry agent run failure rate elevated",
                summary=(
                    f"Foundry control plane reports "
                    f"{foundry.failed_runs}/{foundry.total_runs} failed "
                    f"runs ({foundry.failure_rate * 100:.2f}%)."
                ),
                recommendation=(
                    "Review recent Foundry runs, paying attention to "
                    "tool-call errors and rate limits."
                ),
                source="foundry_control",
                evidence={
                    "failed_runs": foundry.failed_runs,
                    "total_runs": foundry.total_runs,
                    "failure_rate": foundry.failure_rate,
                },
            )
        )

    findings.extend(_check_no_runtime_telemetry(monitor))
    findings.extend(_check_rate_limit_pressure(monitor, config))
    findings.extend(_check_no_token_telemetry(monitor))

    return findings


def _check_rate_limit_pressure(
    monitor: Optional[AzureMonitorPayload],
    config: ErrorsCheckConfig,
) -> List[Finding]:
    """AI.154 — surface HTTP 429 spikes from Azure OpenAI / AI Services.

    Rate-limit responses indicate the workload is exhausting its TPM /
    RPM quota or PTU capacity. Even when the overall error rate is
    healthy, 429s tell the team to raise quotas or add a backoff /
    gateway layer **before** users see degraded behaviour.
    """
    if monitor is None or not monitor.rate_limit_429_count:
        return []
    # Treat the same rate threshold as the error-rate check: if 429s
    # exceed ``rate_threshold`` of total requests, escalate. With no
    # request_count info, fall back to a hard floor of 10 hits.
    total = monitor.request_count
    threshold_hits = max(10, int(total * config.rate_threshold)) if total else 10
    if monitor.rate_limit_429_count < threshold_hits:
        return []
    severity = (
        Severity.CRITICAL
        if monitor.rate_limit_429_count >= threshold_hits * 2
        else Severity.WARNING
    )
    return [
        Finding(
            id="errors.rate_limit_pressure",
            severity=severity,
            category=Category.RELIABILITY,
            title="Azure OpenAI rate-limit responses (HTTP 429) above threshold",
            summary=(
                f"App Insights reports {monitor.rate_limit_429_count} HTTP "
                f"429 responses from Azure OpenAI / AI Services over the "
                "lookback window. The workload is hitting its TPM / RPM "
                "ceiling and clients are being throttled."
            ),
            recommendation=(
                "Raise the deployment's TPM / RPM quota, switch high-volume "
                "flows to a Provisioned-Throughput Unit (PTU) deployment, "
                "or add an APIM gateway with retry + backoff so clients "
                "do not see the 429s directly."
            ),
            source="azure_monitor",
            evidence={
                "rate_limit_429_count": monitor.rate_limit_429_count,
                "request_count": monitor.request_count,
                "threshold_hits": threshold_hits,
            },
        )
    ]


def _check_no_token_telemetry(
    monitor: Optional[AzureMonitorPayload],
) -> List[Finding]:
    """AI.132 — warn when the runtime emits requests but no token telemetry.

    The OpenTelemetry GenAI semantic conventions
    (``gen_ai.usage.input_tokens`` / ``gen_ai.usage.output_tokens``)
    are the canonical signal for token-cost monitoring. When the agent
    runtime emits dependency spans but no token attributes, the team
    flies blind on cost and on AI.132's "Monitor token usage" guidance.
    """
    if monitor is None:
        return []
    if (monitor.diagnostics or {}).get("token_status") == "error":
        return []
    if monitor.request_count <= 0:
        return []  # absence of telemetry is covered by errors.no_runtime_telemetry
    in_t = monitor.input_token_count or 0
    out_t = monitor.output_token_count or 0
    if in_t > 0 or out_t > 0:
        return []
    return [
        Finding(
            id="opex.no_token_telemetry",
            severity=Severity.WARNING,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="Runtime emits requests but no token-usage telemetry",
            summary=(
                f"App Insights recorded {monitor.request_count} agent "
                "requests but reports zero input / output tokens. The "
                "OpenTelemetry GenAI conventions "
                "(`gen_ai.usage.input_tokens` / "
                "`gen_ai.usage.output_tokens`) are not being emitted, so "
                "token-cost monitoring and the Tokens card on the "
                "cockpit stay grey."
            ),
            recommendation=(
                "Wire the OpenAI instrumentor on the agent runtime "
                "(`opentelemetry-instrumentation-openai-v2` or the "
                "Azure SDK's built-in tracing). The instrumentor sets "
                "the token-usage attributes from the model response "
                "automatically."
            ),
            source="azure_monitor",
            evidence={
                "request_count": monitor.request_count,
                "input_token_count": in_t,
                "output_token_count": out_t,
            },
        )
    ]


def _check_no_runtime_telemetry(
    monitor: Optional[AzureMonitorPayload],
) -> List[Finding]:
    """Warn when Azure Monitor is not wired, or wired but silent.

    Two failure modes count, both blockers for production
    observability:

    * **Not configured.** The ``azure_monitor`` source is enabled but
      has no ``app_insights_resource_id`` / ``log_analytics_workspace_id``,
      so it reports ``status: skipped``. Doctor has no production
      observability at all.
    * **Configured but empty.** The source reports ``status: ok`` but
      ``request_count == 0`` over the lookback, so the App Insights
      workspace exists but the agent runtime is not emitting
      telemetry to it.

    The two cases share one finding because the user-facing
    remediation is identical: wire the OpenTelemetry exporter on the
    agent runtime side, and configure the resource id on the
    ``azure_monitor`` source in ``agent.yaml``. If the source is
    explicitly ``enabled: false`` we treat that as an opt-out and
    stay quiet.
    """
    if monitor is None:
        return []
    diag = monitor.diagnostics or {}
    status = diag.get("status")

    if status == "disabled":
        return []

    if status == "ok" and monitor.request_count <= 0:
        summary = (
            "Application Insights / Log Analytics is reachable but "
            "reports 0 requests over the lookback window. The "
            "agent runtime is not emitting telemetry, so the "
            "cockpit, latency, errors, and runtime-safety "
            "checks have nothing to grade."
        )
        evidence = {
            "request_count": monitor.request_count,
            "monitor_status": status,
            "mode": "configured_but_empty",
        }
    elif status == "skipped":
        summary = (
            "The `azure_monitor` source is not configured "
            f"({diag.get('reason') or 'unknown reason'}). Without "
            "App Insights wired up, Doctor has no production "
            "observability, so latency, errors, runtime safety, and "
            "telemetry-based reliability checks all stay grey."
        )
        evidence = {
            "monitor_status": status,
            "reason": diag.get("reason"),
            "mode": "not_configured",
        }
    else:
        return []

    return [
        Finding(
            id="errors.no_runtime_telemetry",
            severity=Severity.WARNING,
            category=Category.RELIABILITY,
            title="Production telemetry is not wired to the agent",
            summary=summary,
            recommendation=(
                "Configure `sources.azure_monitor.app_insights_resource_id` "
                "or set `APPLICATIONINSIGHTS_CONNECTION_STRING` with an "
                "`ApplicationId`, install the `[agent]` extra, and connect "
                "Azure Monitor OpenTelemetry on the agent runtime "
                "(call `configure_azure_monitor()` on startup). "
                "See `docs/tutorial-end-to-end.md` -> "
                "'Wire observability'."
            ),
            source="azure_monitor",
            evidence=evidence,
        )
    ]
