"""Production telemetry queries for the local AgentOps monitor dashboard.

Pulls live signals (invocation count, error rate, p95 latency, token
spend) from the Application Insights resource that the Foundry project
endpoint resolves to, and reshapes them as dashboard cards.

All work is best-effort:

* If the App Insights connection string is not discoverable, the
  dashboard skips this section silently.
* If the API call fails (auth, network, resource not found, etc.), the
  module returns an empty payload — the rest of the dashboard keeps
  rendering.

The KQL hits ``https://api.applicationinsights.io/v1/apps/<appId>/query``
directly with a ``DefaultAzureCredential`` bearer token, which means no
extra Azure SDK dependency beyond what the ``[agent]`` extra already
installs (``azure-identity``).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# In-process cache so /api/production and the HTML render don't hammer the
# App Insights API on every dashboard refresh (default refresh: 15s).
_CACHE_TTL_SECONDS = 60.0
_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def extract_application_id(connection_string: Optional[str]) -> Optional[str]:
    """Pull the ``ApplicationId=<guid>`` segment out of an App Insights
    connection string. Returns ``None`` when absent (older format)."""
    if not connection_string:
        return None
    m = re.search(r"ApplicationId=([0-9a-fA-F-]+)", connection_string)
    return m.group(1) if m else None


def collect_production_metrics(
    application_id: Optional[str],
) -> Dict[str, Any]:
    """Return a dashboard-ready payload of live telemetry cards.

    Always returns a dict with ``has_data`` and ``cards`` keys; values
    populate when the App Insights query succeeds.
    """
    empty = {"has_data": False, "cards": [], "diagnostics": {}}
    if not application_id:
        empty["diagnostics"] = {"reason": "no ApplicationId in connection string"}
        return empty

    cached = _cache.get(application_id)
    now = time.time()
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    try:
        bearer = _acquire_token()
    except Exception as exc:  # noqa: BLE001
        log.debug("token acquisition failed: %s", exc)
        empty["diagnostics"] = {"reason": f"token: {exc}"}
        _cache[application_id] = (now, empty)
        return empty

    summary = _run_query(application_id, bearer, _KQL_SUMMARY)
    if summary is None:
        empty["diagnostics"] = {"reason": "summary query failed"}
        _cache[application_id] = (now, empty)
        return empty

    invocations_buckets = _run_query(application_id, bearer, _KQL_HOURLY_INVOCATIONS) or {}
    latency_buckets = _run_query(application_id, bearer, _KQL_HOURLY_LATENCY) or {}
    tokens = _run_query(application_id, bearer, _KQL_TOKENS) or {}

    cards = _build_cards(summary, invocations_buckets, latency_buckets, tokens)
    payload = {"has_data": bool(cards), "cards": cards, "diagnostics": {}}
    _cache[application_id] = (now, payload)
    return payload


# ---------------------------------------------------------------------------
# KQL queries
# ---------------------------------------------------------------------------


_KQL_SUMMARY = """
union dependencies, requests
| where timestamp > ago(24h)
| where name has "invoke_agent" or name has "chat " or name has "RUN "
| summarize
    invocations = count(),
    errors = countif(success == false),
    avg_ms = avg(duration),
    p95_ms = percentile(duration, 95)
"""

_KQL_HOURLY_INVOCATIONS = """
union dependencies, requests
| where timestamp > ago(24h)
| where name has "invoke_agent" or name has "chat "
| summarize count = count() by bin(timestamp, 1h)
| order by timestamp asc
"""

_KQL_HOURLY_LATENCY = """
dependencies
| where timestamp > ago(24h)
| where name has "invoke_agent" or name has "chat "
| summarize p95_ms = percentile(duration, 95) by bin(timestamp, 1h)
| order by timestamp asc
"""

_KQL_TOKENS = """
dependencies
| where timestamp > ago(24h)
| extend input_t = toint(coalesce(
    customDimensions["gen_ai.usage.input_tokens"],
    customDimensions["llm.usage.input_tokens"]
  ))
| extend output_t = toint(coalesce(
    customDimensions["gen_ai.usage.output_tokens"],
    customDimensions["llm.usage.output_tokens"]
  ))
| where isnotnull(input_t) or isnotnull(output_t)
| summarize
    input_tokens = sum(input_t),
    output_tokens = sum(output_t)
"""


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _acquire_token() -> str:
    """Acquire an OAuth bearer token for the App Insights API."""
    from azure.identity import DefaultAzureCredential
    credential = DefaultAzureCredential(exclude_developer_cli_credential=True)
    token = credential.get_token("https://api.applicationinsights.io/.default")
    return token.token


def _run_query(app_id: str, bearer: str, kql: str) -> Optional[Dict[str, Any]]:
    """POST a KQL query to the App Insights REST endpoint."""
    try:
        # urllib stays in stdlib — avoids dragging requests as a dep.
        import json as _json
        from urllib import error, request

        body = _json.dumps({"query": kql}).encode("utf-8")
        req = request.Request(
            url=f"https://api.applicationinsights.io/v1/apps/{app_id}/query",
            data=body,
            headers={
                "Authorization": f"Bearer {bearer}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with request.urlopen(req, timeout=10) as resp:  # noqa: S310
            data = resp.read()
        parsed = _json.loads(data)
        return _flatten_first_table(parsed)
    except (error.URLError, ValueError, KeyError) as exc:
        log.debug("app insights query failed: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        log.debug("app insights query failed unexpectedly: %s", exc)
        return None


def _flatten_first_table(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce the Kusto REST response into a list of column->value rows."""
    tables = parsed.get("tables") or []
    if not tables:
        return {"rows": []}
    table = tables[0]
    columns = [c.get("name") for c in (table.get("columns") or [])]
    rows = []
    for raw in table.get("rows") or []:
        rows.append(dict(zip(columns, raw)))
    return {"columns": columns, "rows": rows}


# ---------------------------------------------------------------------------
# Card builders
# ---------------------------------------------------------------------------


def _build_cards(
    summary: Dict[str, Any],
    invocations_buckets: Dict[str, Any],
    latency_buckets: Dict[str, Any],
    tokens: Dict[str, Any],
) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    rows = summary.get("rows") or []
    if not rows:
        # No data at all — nothing to render.
        return cards
    row = rows[0]

    invocations = int(row.get("invocations") or 0)
    errors = int(row.get("errors") or 0)
    error_rate = (errors / invocations) if invocations else 0.0
    p95_ms = row.get("p95_ms")
    p95_seconds = (float(p95_ms) / 1000.0) if p95_ms is not None else None

    inv_series, inv_labels = _hourly_series(invocations_buckets, "count")
    lat_series, lat_labels = _hourly_series(latency_buckets, "p95_ms", scale=1 / 1000.0)

    cards.append({
        "key": "prod_invocations",
        "label": "Invocations (24h)",
        "value": invocations,
        "unit": "calls",
        "series": inv_series or [float(invocations)],
        "labels": inv_labels,
        "badge": {"label": "live", "tone": "info"},
        "source": "App Insights · KQL: count of invoke_agent + chat spans",
    })

    cards.append({
        "key": "prod_errors",
        "label": "Error rate (24h)",
        "value": f"{int(error_rate * 100)}%",
        "unit": f"{errors} errors",
        "series": inv_series or [0.0],  # reuse invocation buckets to give context
        "labels": inv_labels,
        "badge": _error_rate_badge(error_rate),
        "source": "App Insights · KQL: countif(success == false)",
    })

    cards.append({
        "key": "prod_p95",
        "label": "P95 latency (24h)",
        "value": f"{p95_seconds:.2f}" if p95_seconds is not None else "—",
        "unit": "s",
        "series": lat_series or ([p95_seconds] if p95_seconds is not None else [0.0]),
        "labels": lat_labels,
        "badge": _latency_badge(p95_seconds),
        "source": "App Insights · KQL: percentile(duration, 95)",
    })

    token_rows = (tokens or {}).get("rows") or []
    if token_rows:
        trow = token_rows[0]
        input_t = int(trow.get("input_tokens") or 0)
        output_t = int(trow.get("output_tokens") or 0)
        total = input_t + output_t
        cards.append({
            "key": "prod_tokens",
            "label": "Tokens (24h)",
            "value": _format_tokens(total),
            "unit": f"{_format_tokens(input_t)} in / {_format_tokens(output_t)} out",
            "value_kind": "text",
            "series": [float(total)],
            "labels": [f"input: {input_t} · output: {output_t}"],
            "badge": {"label": "live", "tone": "info"},
            "source": "App Insights · KQL: sum(gen_ai.usage.*_tokens)",
        })

    return cards


def _hourly_series(buckets: Dict[str, Any], value_key: str, *, scale: float = 1.0) -> Tuple[List[float], List[str]]:
    rows = (buckets or {}).get("rows") or []
    series: List[float] = []
    labels: List[str] = []
    for r in rows:
        v = r.get(value_key)
        if v is None:
            continue
        series.append(float(v) * scale)
        ts = r.get("timestamp") or ""
        labels.append(str(ts)[:16].replace("T", " "))
    return series, labels


def _error_rate_badge(rate: float) -> Dict[str, str]:
    if rate <= 0.01:
        return {"label": "healthy", "tone": "ok"}
    if rate <= 0.05:
        return {"label": "watch", "tone": "warn"}
    return {"label": "elevated", "tone": "crit"}


def _latency_badge(seconds: Optional[float]) -> Dict[str, str]:
    if seconds is None:
        return {"label": "no data", "tone": "muted"}
    if seconds <= 5:
        return {"label": "snappy", "tone": "ok"}
    if seconds <= 15:
        return {"label": "ok", "tone": "info"}
    return {"label": "slow", "tone": "warn"}


def _format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)
