"""Production telemetry queries for the local AgentOps cockpit.

Pulls live signals (invocation count, error rate, p95 latency, token
spend) from the Application Insights resource that the Foundry project
endpoint resolves to, and reshapes them as cockpit cards.

All work is best-effort:

* If the App Insights connection string is not discoverable, the
  cockpit skips this section silently.
* If the API call fails (auth, network, resource not found, etc.), the
  module returns an empty payload - the rest of the cockpit keeps
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
# App Insights API on every cockpit refresh (default refresh: 15s).
_CACHE_TTL_SECONDS = 60.0
_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _humanize_token_error(exc: Exception) -> str:
    """Convert a verbose ``DefaultAzureCredential`` failure into a short,
    actionable message suitable for the cockpit's error tile.

    The Azure SDK concatenates the failure reason of every credential
    in the chain into a single multi-line string ("EnvironmentCredential:
    ... WorkloadIdentityCredential: ... AzureCliCredential: ..."). The
    full text is technically accurate but useless for a user staring at
    a cockpit. Detect the common failure shapes and surface a
    one-sentence remediation instead.
    """
    text = str(exc)
    lower = text.lower()
    # The Azure CLI sign-in is the path users on dev machines actually
    # take, so prioritize that hint when its sub-credential failed.
    cli_failed = (
        "azureclicredential: failed to invoke the azure cli" in lower
        or "no accounts were found in the cache" in lower
    )
    if cli_failed:
        return (
            "Not signed in to Azure. Run `az login` in the same shell "
            "you launched `agentops cockpit` from, then refresh."
        )
    if "defaultazurecredential failed to retrieve a token" in lower:
        return (
            "Azure authentication failed: DefaultAzureCredential could "
            "not acquire a token. On a dev machine the usual fix is "
            "`az login`. See cockpit logs for the full credential "
            "chain."
        )
    # Truncate generic exceptions so the tile stays readable.
    snippet = text.splitlines()[0].strip()
    if len(snippet) > 240:
        snippet = snippet[:237] + "..."
    return f"Token acquisition failed: {snippet}"


def extract_application_id(connection_string: Optional[str]) -> Optional[str]:
    """Pull the ``ApplicationId=<guid>`` segment out of an App Insights
    connection string. Returns ``None`` when absent (older format)."""
    if not connection_string:
        return None
    m = re.search(r"ApplicationId=([0-9a-fA-F-]+)", connection_string)
    return m.group(1) if m else None


def collect_production_metrics(
    application_id: Optional[str],
    *,
    lookback_hours: int = 24,
) -> Dict[str, Any]:
    """Return a cockpit-ready payload of live telemetry cards.

    Always returns a dict with ``has_data`` and ``cards`` keys; values
    populate when the App Insights query succeeds. The ``lookback_hours``
    parameter is substituted into the KQL templates so the cockpit
    time-range picker can drive how far back each card looks.
    """
    empty = {"has_data": False, "cards": [], "diagnostics": {}}
    if not application_id:
        empty["diagnostics"] = {"reason": "no ApplicationId in connection string"}
        return empty

    cache_key = f"{application_id}:{lookback_hours}"
    cached = _cache.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    try:
        bearer = _acquire_token()
    except Exception as exc:  # noqa: BLE001
        log.debug("token acquisition failed: %s", exc)
        empty["diagnostics"] = {"reason": _humanize_token_error(exc)}
        return empty

    # Pick a sensible bucket size: 1h for short windows, 6h for ~30d.
    bucket = "1h" if lookback_hours <= 48 else "6h"

    # Fire all four queries in parallel - sequential round-trips to App
    # Insights were the single biggest source of cockpit latency
    # (~4s vs ~1s after this change).
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        fut_summary = ex.submit(
            _run_query, application_id, bearer, _KQL_SUMMARY.format(hours=lookback_hours),
        )
        fut_invocations = ex.submit(
            _run_query, application_id, bearer,
            _KQL_HOURLY_INVOCATIONS.format(hours=lookback_hours, bucket=bucket),
        )
        fut_latency = ex.submit(
            _run_query, application_id, bearer,
            _KQL_HOURLY_LATENCY.format(hours=lookback_hours, bucket=bucket),
        )
        fut_tokens = ex.submit(
            _run_query, application_id, bearer,
            _KQL_TOKENS.format(hours=lookback_hours),
        )
        summary = fut_summary.result()
        invocations_buckets = fut_invocations.result() or {}
        latency_buckets = fut_latency.result() or {}
        tokens = fut_tokens.result() or {}

    if summary is None:
        empty["diagnostics"] = {
            "reason": "Application Insights query failed (auth, network, "
            "or KQL error). See `agentops cockpit` console logs for the "
            "exact message."
        }
        return empty

    cards = _build_cards(summary, invocations_buckets, latency_buckets, tokens, lookback_hours)
    payload = {"has_data": bool(cards), "cards": cards, "diagnostics": {}}
    # Only cache populated payloads. Caching empty results masks
    # transient failures (token expiry, App Insights 5xx, etc.) for up
    # to a minute — exactly the case the user notices as "the
    # cockpit suddenly stopped showing telemetry". A subsequent
    # refresh will retry the query immediately.
    if cards:
        _cache[cache_key] = (now, payload)
    else:
        payload["diagnostics"] = {
            "reason": "App Insights returned 0 invocations for the "
            "selected window. If you expect data here, widen the time "
            "range or verify that traces are being emitted."
        }
    return payload


# ---------------------------------------------------------------------------
# KQL queries
# ---------------------------------------------------------------------------


_KQL_SUMMARY = """
union dependencies, requests
| where timestamp > ago({hours}h)
| where name has "invoke_agent" or name has "chat " or name has "RUN "
| summarize
    invocations = count(),
    errors = countif(success == false),
    avg_ms = avg(duration),
    p95_ms = percentile(duration, 95)
"""

_KQL_HOURLY_INVOCATIONS = """
union dependencies, requests
| where timestamp > ago({hours}h)
| where name has "invoke_agent" or name has "chat "
| summarize count = count() by bin(timestamp, {bucket})
| order by timestamp asc
"""

_KQL_HOURLY_LATENCY = """
dependencies
| where timestamp > ago({hours}h)
| where name has "invoke_agent" or name has "chat "
| summarize p95_ms = percentile(duration, 95) by bin(timestamp, {bucket})
| order by timestamp asc
"""

_KQL_TOKENS = """
dependencies
| where timestamp > ago({hours}h)
| extend input_t = toint(coalesce(
    customDimensions["gen_ai.usage.input_tokens"],
    customDimensions["llm.usage.input_tokens"]
  ))
| extend output_t = toint(coalesce(
    customDimensions["gen_ai.usage.output_tokens"],
    customDimensions["llm.usage.output_tokens"]
  ))
| extend model_name = tostring(coalesce(
    customDimensions["gen_ai.request.model"],
    customDimensions["gen_ai.response.model"],
    customDimensions["llm.request.model"],
    customDimensions["llm.response.model"],
    "unknown"
  ))
| where isnotnull(input_t) or isnotnull(output_t)
| summarize
    input_tokens = sum(input_t),
    output_tokens = sum(output_t)
    by model_name
| order by (input_tokens + output_tokens) desc
"""


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _acquire_token() -> str:
    """Acquire an OAuth bearer token for the App Insights API.

    Cached in-process for 5 minutes to avoid re-running the expensive
    DefaultAzureCredential chain (IMDS timeouts on non-Azure boxes are
    the single biggest source of cockpit latency).
    """
    cached = _token_cache.get("bearer")
    now = time.time()
    if cached and now - cached[0] < _TOKEN_CACHE_TTL_SECONDS:
        return cached[1]

    from azure.identity import DefaultAzureCredential
    credential = DefaultAzureCredential(exclude_developer_cli_credential=True, process_timeout=30)
    token = credential.get_token("https://api.applicationinsights.io/.default")
    _token_cache["bearer"] = (now, token.token)
    return token.token


_TOKEN_CACHE_TTL_SECONDS = 5 * 60
_token_cache: Dict[str, Tuple[float, str]] = {}


def _run_query(app_id: str, bearer: str, kql: str) -> Optional[Dict[str, Any]]:
    """POST a KQL query to the App Insights REST endpoint.

    Returns ``None`` on any failure (HTTP error, network issue, or an
    Application Insights query error returned with HTTP 200). The
    caller treats ``None`` as a recoverable failure and surfaces the
    reason via diagnostics rather than caching a misleading empty
    result.
    """
    try:
        # urllib stays in stdlib - avoids dragging requests as a dep.
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
        # App Insights surfaces query failures with HTTP 200 and an
        # ``error`` object — surface those as failures so the caller
        # does not mistake them for "no data".
        if isinstance(parsed, dict) and parsed.get("error"):
            err = parsed["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            log.debug("app insights query reported error: %s", msg)
            return None
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
    lookback_hours: int = 24,
) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    rows = summary.get("rows") or []
    if not rows:
        return cards
    row = rows[0]

    invocations = int(row.get("invocations") or 0)
    errors = int(row.get("errors") or 0)
    error_rate = (errors / invocations) if invocations else 0.0
    p95_ms = row.get("p95_ms")
    p95_seconds = (float(p95_ms) / 1000.0) if p95_ms is not None else None

    inv_series, inv_labels = _hourly_series(invocations_buckets, "count")
    lat_series, lat_labels = _hourly_series(latency_buckets, "p95_ms", scale=1 / 1000.0)

    window_label = _window_label(lookback_hours)

    cards.append({
        "key": "prod_errors",
        "label": f"Error rate ({window_label})",
        "value": f"{int(error_rate * 100)}%",
        "unit": f"{errors} errors",
        "series": inv_series or [0.0],
        "labels": inv_labels,
        "badge": _error_rate_badge(error_rate),
        "help": (
            "Share of invocations whose dependency telemetry reported "
            "success = false."
            "\n\nBadge tiers:"
            "\n• 0% - healthy"
            "\n• under 5% - watch"
            "\n• 5% or more - unhealthy"
        ),
        "source": "Share of invocations that reported a failure status.",
    })

    cards.append({
        "key": "prod_p95",
        "label": f"P95 latency ({window_label})",
        "value": f"{p95_seconds:.2f}" if p95_seconds is not None else " - ",
        "unit": "s",
        "series": lat_series or ([p95_seconds] if p95_seconds is not None else [0.0]),
        "labels": lat_labels,
        "badge": _latency_badge(p95_seconds),
        "help": (
            "95th percentile end-to-end agent latency over the window. "
            "Includes invoke_agent spans (full agent turn with tool "
            "calls) and chat spans (direct model calls)."
            "\n\nBadge tiers:"
            "\n• under 2s - snappy"
            "\n• 2 to 5s - acceptable"
            "\n• over 5s - sluggish"
        ),
        "source": "95th-percentile end-to-end duration of agent and chat spans.",
    })

    return cards


def _window_label(hours: int) -> str:
    if hours <= 24:
        return "24h"
    if hours == 24 * 7:
        return "7d"
    if hours == 24 * 30:
        return "30d"
    days = hours // 24
    return f"{days}d" if days else f"{hours}h"


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
