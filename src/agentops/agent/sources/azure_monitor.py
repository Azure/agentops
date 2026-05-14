"""Azure Monitor / Application Insights source.

Lazy-imports ``azure.monitor.query`` at call time so the base CLI does
not require the SDK. When the source is not configured or the SDK is
not installed, returns an empty payload with a diagnostic note.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agentops.agent.config import AzureMonitorSourceConfig

log = logging.getLogger(__name__)


@dataclass
class AzureMonitorPayload:
    request_count: int = 0
    error_count: int = 0
    p95_duration_seconds: Optional[float] = None
    avg_duration_seconds: Optional[float] = None
    error_rate: Optional[float] = None
    safety_violations: List[Dict[str, Any]] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)


_REQUESTS_KQL = """
union isfuzzy=true requests, dependencies
| where timestamp > ago({lookback_days}d)
| summarize
    request_count = count(),
    error_count = countif(success == false),
    avg_duration_ms = avg(duration),
    p95_duration_ms = percentile(duration, 95)
"""


# Detects OpenAI / Foundry content-filter triggers in App Insights /
# Log Analytics. We intentionally stay conservative: the only attribute
# guaranteed by the OTel Gen AI semantic conventions is
# ``gen_ai.response.finish_reasons`` (plural), which contains
# ``content_filter`` when the model refused to complete a response. Any
# other category/severity breakdown is vendor-specific and not relied
# upon here.
_SAFETY_KQL = """
union isfuzzy=true requests, dependencies, traces, AppTraces, AppDependencies, AppRequests
| where timestamp > ago({lookback_days}d)
| extend cd = tostring(customDimensions)
| where cd has "content_filter"
| summarize hits = count()
"""


def collect_azure_monitor(
    config: AzureMonitorSourceConfig,
    lookback_days: int,
) -> AzureMonitorPayload:
    """Run KQL queries against Application Insights for the lookback window."""
    diagnostics: Dict[str, Any] = {"enabled": config.enabled}

    if not config.enabled:
        diagnostics["status"] = "disabled"
        return AzureMonitorPayload(diagnostics=diagnostics)

    if not config.app_insights_resource_id and not config.log_analytics_workspace_id:
        diagnostics["status"] = "skipped"
        diagnostics["reason"] = (
            "neither app_insights_resource_id nor log_analytics_workspace_id "
            "is configured"
        )
        return AzureMonitorPayload(diagnostics=diagnostics)

    try:
        from azure.identity import DefaultAzureCredential
        from azure.monitor.query import LogsQueryClient, LogsQueryStatus
    except ImportError as exc:
        diagnostics["status"] = "skipped"
        diagnostics["reason"] = (
            "azure-monitor-query / azure-identity not installed "
            "(install agentops-toolkit[agent])"
        )
        log.info("azure-monitor-query unavailable: %s", exc)
        return AzureMonitorPayload(diagnostics=diagnostics)

    workspace_or_resource = (
        config.log_analytics_workspace_id or config.app_insights_resource_id
    )
    diagnostics["target"] = workspace_or_resource

    try:
        credential = DefaultAzureCredential(exclude_developer_cli_credential=True)
        client = LogsQueryClient(credential)
        kql = _REQUESTS_KQL.format(lookback_days=int(lookback_days))
        if config.log_analytics_workspace_id:
            response = client.query_workspace(
                workspace_id=config.log_analytics_workspace_id,
                query=kql,
                timespan=None,
            )
        else:
            # query_resource is available on newer SDKs.
            query_resource = getattr(client, "query_resource", None)
            if query_resource is None:
                diagnostics["status"] = "skipped"
                diagnostics["reason"] = (
                    "Installed azure-monitor-query does not support "
                    "query_resource; upgrade to >=1.3.0 or use "
                    "log_analytics_workspace_id."
                )
                return AzureMonitorPayload(diagnostics=diagnostics)
            response = query_resource(
                resource_id=config.app_insights_resource_id,
                query=kql,
                timespan=None,
            )
    except Exception as exc:  # pragma: no cover - network / auth errors
        diagnostics["status"] = "error"
        diagnostics["reason"] = str(exc)
        log.warning("Azure Monitor query failed: %s", exc)
        return AzureMonitorPayload(diagnostics=diagnostics)

    if getattr(response, "status", None) == LogsQueryStatus.FAILURE:
        diagnostics["status"] = "error"
        diagnostics["reason"] = "query failed"
        return AzureMonitorPayload(diagnostics=diagnostics)

    payload = AzureMonitorPayload(diagnostics=diagnostics)
    diagnostics["status"] = "ok"

    tables = getattr(response, "tables", []) or []
    if tables:
        rows = list(tables[0].rows)
        if rows:
            row = rows[0]
            columns = [c.name if hasattr(c, "name") else str(c) for c in tables[0].columns]
            data = dict(zip(columns, row))
            payload.request_count = int(data.get("request_count", 0) or 0)
            payload.error_count = int(data.get("error_count", 0) or 0)
            avg_ms = data.get("avg_duration_ms")
            p95_ms = data.get("p95_duration_ms")
            if avg_ms is not None:
                payload.avg_duration_seconds = float(avg_ms) / 1000.0
            if p95_ms is not None:
                payload.p95_duration_seconds = float(p95_ms) / 1000.0
            if payload.request_count > 0:
                payload.error_rate = payload.error_count / payload.request_count

    # Best-effort second pass: content-filter / safety triggers.
    # Failures here are isolated from the primary metrics above.
    try:
        safety_kql = _SAFETY_KQL.format(lookback_days=int(lookback_days))
        if config.log_analytics_workspace_id:
            safety_response = client.query_workspace(
                workspace_id=config.log_analytics_workspace_id,
                query=safety_kql,
                timespan=None,
            )
        else:
            safety_response = client.query_resource(  # type: ignore[union-attr]
                resource_id=config.app_insights_resource_id,
                query=safety_kql,
                timespan=None,
            )
        if getattr(safety_response, "status", None) == LogsQueryStatus.FAILURE:
            diagnostics["safety_status"] = "error"
            diagnostics["safety_reason"] = "query failed"
        else:
            safety_tables = getattr(safety_response, "tables", []) or []
            hits = 0
            if safety_tables:
                safety_rows = list(safety_tables[0].rows)
                if safety_rows:
                    cols = [
                        c.name if hasattr(c, "name") else str(c)
                        for c in safety_tables[0].columns
                    ]
                    data = dict(zip(cols, safety_rows[0]))
                    hits = int(data.get("hits", 0) or 0)
            diagnostics["safety_status"] = "ok"
            diagnostics["safety_hits"] = hits
            if hits > 0:
                payload.safety_violations.append(
                    {"signal": "content_filter", "hits": hits}
                )
    except Exception as exc:  # pragma: no cover - best effort
        diagnostics["safety_status"] = "error"
        diagnostics["safety_reason"] = str(exc)
        log.info("Safety KQL probe failed (non-fatal): %s", exc)

    return payload
