"""Local web dashboard for the AgentOps watchdog agent.

``agentops monitor`` boots a tiny FastAPI server that reads the
analysis history from ``.agentops/agent/history.jsonl`` **and** the
evaluation history from ``.agentops/results/*/results.json``, then
serves a single dashboard page in a FitBit-inspired dark theme. No
external frontend dependencies (sparklines are inline SVG); no Azure
resource required.

The server is intentionally read-only and bound to ``127.0.0.1`` by
default — it is a developer-tool surface, not a production service.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agentops.agent.history import AnalysisRecord, load_analysis_history


# ---------------------------------------------------------------------------
# Data shaping for the dashboard
# ---------------------------------------------------------------------------


_CATEGORY_LABELS = {
    "quality": "Quality",
    "performance": "Performance",
    "reliability": "Reliability",
    "security": "Security",
}

_BADGE_FOR_SEVERITY = {
    None: ("in range", "ok"),
    "info": ("info", "info"),
    "warning": ("warnings", "warn"),
    "critical": ("critical", "crit"),
}

# Quality-metric cards rendered when eval history is available.
# Ordered so the dashboard layout is stable across runs.
_QUALITY_METRICS: List[Tuple[str, str, str]] = [
    ("coherence", "Coherence", "/5"),
    ("fluency", "Fluency", "/5"),
    ("similarity", "Similarity", "/5"),
    ("f1_score", "F1 score", ""),
    ("groundedness", "Groundedness", "/5"),
    ("relevance", "Relevance", "/5"),
    ("avg_latency_seconds", "Latency", "s"),
]


def build_dashboard_payload(
    workspace: Path,
    *,
    history: Optional[List[AnalysisRecord]] = None,
) -> Dict[str, Any]:
    """Reduce raw history + eval runs into a dashboard-ready dict."""
    records = history if history is not None else load_analysis_history(workspace)
    eval_runs = _load_eval_runs(workspace, limit=24)
    telemetry = _telemetry_status()

    return {
        "workspace": str(workspace.resolve()),
        "telemetry": telemetry,
        "eval": _build_eval_section(eval_runs),
        "metrics": _build_metrics_cards(eval_runs),
        "watchdog": _build_watchdog_section(records),
        "summary_counts": {
            "eval_runs": len(eval_runs),
            "analyses": len(records),
        },
    }


def _build_eval_section(eval_runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not eval_runs:
        return {
            "has_runs": False,
            "cards": [],
        }
    pass_series = [1.0 if r["passed"] else 0.0 for r in eval_runs]
    pass_rate = sum(pass_series) / len(pass_series) if pass_series else 0.0
    latest = eval_runs[-1]
    items_total_series = [float(r.get("items_total") or 0) for r in eval_runs]

    cards: List[Dict[str, Any]] = [
        {
            "key": "total_runs",
            "label": "Eval runs",
            "value": len(eval_runs),
            "unit": "total",
            "series": [1.0] * len(eval_runs),  # constant — show as filled bar
            "badge": {"label": _badge_runs(len(eval_runs)), "tone": "info"},
        },
        {
            "key": "pass_rate",
            "label": "Pass rate",
            "value": f"{int(pass_rate * 100)}%",
            "unit": "",
            "series": pass_series,
            "badge": _badge_pass_rate(pass_rate),
        },
        {
            "key": "items",
            "label": "Items / run",
            "value": int(items_total_series[-1]) if items_total_series else 0,
            "unit": "rows",
            "series": items_total_series,
            "badge": {"label": "latest", "tone": "muted"},
        },
        {
            "key": "latest_run",
            "label": "Latest run",
            "value": latest["target"] or "—",
            "unit": "",
            "series": pass_series[-6:],
            "badge": {
                "label": "passed" if latest["passed"] else "failed",
                "tone": "ok" if latest["passed"] else "crit",
            },
            "meta": [
                latest["timestamp"] or "",
                f"duration: {latest['duration']:.1f}s" if latest["duration"] else "duration: —",
                f"execution: {latest['execution']}" if latest["execution"] else "execution: —",
            ],
        },
    ]
    return {"has_runs": True, "cards": cards}


def _build_metrics_cards(eval_runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not eval_runs:
        return []
    cards: List[Dict[str, Any]] = []
    for key, label, unit in _QUALITY_METRICS:
        series = [r["metrics"].get(key) for r in eval_runs]
        series = [float(v) for v in series if v is not None]
        if not series:
            continue
        latest = series[-1]
        # Latency is "lower is better"; quality metrics are "higher is better".
        is_latency = key == "avg_latency_seconds"
        badge = _metric_trend_badge(series, is_latency=is_latency)
        cards.append({
            "key": key,
            "label": label,
            "value": f"{latest:.2f}",
            "unit": unit,
            "series": series,
            "badge": badge,
        })
    return cards


def _build_watchdog_section(records: List[AnalysisRecord]) -> Dict[str, Any]:
    latest = records[-1] if records else None

    def _series(extractor) -> List[float]:
        return [float(extractor(r) or 0) for r in records]

    findings_series = _series(lambda r: r.findings_total)
    critical_series = _series(lambda r: r.findings_by_severity.get("critical", 0))

    category_cards: List[Dict[str, Any]] = []
    for key, label in _CATEGORY_LABELS.items():
        series = _series(lambda r, k=key: r.findings_by_category.get(k, 0))
        current = int(series[-1]) if series else 0
        category_cards.append({
            "key": key,
            "label": label,
            "value": current,
            "unit": "",
            "series": series,
            "badge": _category_badge(key, current, records),
        })

    latest_label, latest_badge = _latest_run_badge(latest)

    return {
        "has_history": bool(records),
        "history_count": len(records),
        "headline_cards": [
            {
                "key": "findings_total",
                "label": "Findings",
                "value": int(findings_series[-1]) if findings_series else 0,
                "unit": "total",
                "series": findings_series,
                "badge": _headline_badge_total(findings_series),
            },
            {
                "key": "critical",
                "label": "Critical",
                "value": int(critical_series[-1]) if critical_series else 0,
                "unit": "open",
                "series": critical_series,
                "badge": _headline_badge_critical(critical_series),
            },
            {
                "key": "last_analysis",
                "label": "Last analysis",
                "value": latest_label,
                "unit": "",
                "series": findings_series[-6:],
                "badge": latest_badge,
                "meta": _latest_run_meta(latest),
            },
        ],
        "category_cards": category_cards,
    }


# ---------------------------------------------------------------------------
# Eval run loading
# ---------------------------------------------------------------------------


def _load_eval_runs(workspace: Path, *, limit: int = 24) -> List[Dict[str, Any]]:
    """Scan ``.agentops/results/<timestamp>/results.json`` and project the
    fields the dashboard cares about. ``latest/`` is skipped because it is
    a mirror of the most recent timestamped run.
    """
    results_root = workspace / ".agentops" / "results"
    if not results_root.exists():
        return []

    candidates: List[Tuple[str, Path]] = []
    for entry in results_root.iterdir():
        if not entry.is_dir() or entry.name == "latest":
            continue
        results_file = entry / "results.json"
        if results_file.exists():
            candidates.append((entry.name, results_file))

    # Sort by directory name (timestamp prefix is sortable).
    candidates.sort(key=lambda kv: kv[0])
    candidates = candidates[-limit:]

    runs: List[Dict[str, Any]] = []
    for _, path in candidates:
        run = _project_run(path)
        if run is not None:
            runs.append(run)
    return runs


def _project_run(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    summary = data.get("summary") or {}
    target = data.get("target") or {}
    cfg = data.get("config") or {}
    return {
        "timestamp": data.get("started_at") or data.get("finished_at"),
        "duration": _safe_float(data.get("duration_seconds")),
        "target": target.get("raw") if isinstance(target, dict) else None,
        "passed": bool(summary.get("overall_passed")) if isinstance(summary, dict) else False,
        "items_total": summary.get("items_total") if isinstance(summary, dict) else None,
        "items_passed_all": summary.get("items_passed_all") if isinstance(summary, dict) else None,
        "metrics": data.get("aggregate_metrics") if isinstance(data.get("aggregate_metrics"), dict) else {},
        "execution": cfg.get("execution") if isinstance(cfg, dict) else None,
    }


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Telemetry status
# ---------------------------------------------------------------------------


def _telemetry_status() -> Dict[str, Any]:
    """Inspect env + Foundry discovery to tell the user whether eval/watchdog
    traces will reach an App Insights workspace. Pure read; no side effects.
    """
    explicit_conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING") or os.getenv(
        "AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING"
    )
    otlp = os.getenv("AGENTOPS_OTLP_ENDPOINT")
    project = os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")

    if explicit_conn:
        return {
            "enabled": True,
            "source": "env",
            "label": "App Insights (explicit)",
            "detail": "APPLICATIONINSIGHTS_CONNECTION_STRING is set.",
            "tone": "ok",
        }
    if otlp:
        return {
            "enabled": True,
            "source": "otlp",
            "label": "OTLP exporter",
            "detail": f"AGENTOPS_OTLP_ENDPOINT={otlp}",
            "tone": "ok",
        }
    if project:
        # Best-effort discovery; cached one-shot per dashboard request.
        try:
            from agentops.utils.foundry_discovery import (
                resolve_appinsights_connection_from_env,
            )
            conn = resolve_appinsights_connection_from_env()
        except Exception:  # noqa: BLE001
            conn = None
        if conn:
            return {
                "enabled": True,
                "source": "discovery",
                "label": "App Insights (auto-discovered)",
                "detail": "Resolved from the Foundry project endpoint.",
                "tone": "ok",
            }
        return {
            "enabled": False,
            "source": "discovery_failed",
            "label": "Telemetry not active",
            "detail": (
                "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT is set but no Application "
                "Insights resource was discovered on the project. Connect "
                "one in the Foundry portal or set "
                "APPLICATIONINSIGHTS_CONNECTION_STRING manually."
            ),
            "tone": "warn",
        }
    return {
        "enabled": False,
        "source": "off",
        "label": "Telemetry not active",
        "detail": (
            "Set AZURE_AI_FOUNDRY_PROJECT_ENDPOINT for auto-discovery, or "
            "APPLICATIONINSIGHTS_CONNECTION_STRING to route traces to a "
            "specific App Insights workspace."
        ),
        "tone": "muted",
    }


# ---------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------


def _headline_badge_total(series: List[float]) -> Dict[str, str]:
    if not series:
        return {"label": "no data", "tone": "muted"}
    last = series[-1]
    if last == 0:
        return {"label": "all clear", "tone": "ok"}
    if len(series) >= 2 and last > series[-2]:
        return {"label": "trending up", "tone": "warn"}
    return {"label": "open", "tone": "info"}


def _headline_badge_critical(series: List[float]) -> Dict[str, str]:
    if not series:
        return {"label": "no data", "tone": "muted"}
    last = series[-1]
    if last == 0:
        return {"label": "none", "tone": "ok"}
    return {"label": "above zero", "tone": "crit"}


def _category_badge(
    key: str, current: int, records: List[AnalysisRecord]
) -> Dict[str, str]:
    if not records:
        return {"label": "no data", "tone": "muted"}
    if current == 0:
        return {"label": "in range", "tone": "ok"}
    if len(records) >= 2:
        prev = records[-2].findings_by_category.get(key, 0)
        if prev == 0:
            return {"label": "new", "tone": "warn"}
        if current > prev:
            return {"label": "trending up", "tone": "warn"}
        if current < prev:
            return {"label": "trending down", "tone": "ok"}
    return {"label": "active", "tone": "info"}


def _latest_run_badge(record: Optional[AnalysisRecord]) -> tuple:
    if record is None:
        return ("never", {"label": "no data", "tone": "muted"})
    label, tone = _BADGE_FOR_SEVERITY[record.max_severity]
    return (
        f"{record.findings_total} finding(s)",
        {"label": label, "tone": tone},
    )


def _latest_run_meta(record: Optional[AnalysisRecord]) -> List[str]:
    if record is None:
        return []
    meta = [record.timestamp]
    if record.duration_seconds is not None:
        meta.append(f"duration: {record.duration_seconds:.1f}s")
    if record.sources_enabled:
        meta.append(f"sources: {', '.join(record.sources_enabled)}")
    return meta


def _badge_runs(count: int) -> str:
    if count >= 10:
        return "well sampled"
    if count >= 3:
        return "warming up"
    return "starting out"


def _badge_pass_rate(rate: float) -> Dict[str, str]:
    if rate >= 0.9:
        return {"label": "healthy", "tone": "ok"}
    if rate >= 0.7:
        return {"label": "mixed", "tone": "warn"}
    return {"label": "unhealthy", "tone": "crit"}


def _metric_trend_badge(series: List[float], *, is_latency: bool) -> Dict[str, str]:
    if len(series) < 2:
        return {"label": "baseline", "tone": "info"}
    last, prev = series[-1], series[-2]
    delta = last - prev
    if abs(delta) < 1e-3:
        return {"label": "stable", "tone": "muted"}
    improved = (delta < 0) if is_latency else (delta > 0)
    if improved:
        return {"label": "improved", "tone": "ok"}
    return {"label": "regressed", "tone": "warn"}


# ---------------------------------------------------------------------------
# HTML rendering — inline, zero JS deps
# ---------------------------------------------------------------------------


def render_dashboard_html(payload: Dict[str, Any]) -> str:
    """Render the dashboard from a payload built by
    :func:`build_dashboard_payload`. Returns a complete HTML document.
    """
    telemetry = payload["telemetry"]
    telemetry_card = _render_telemetry_card(telemetry)

    eval_section = ""
    if payload["eval"]["has_runs"]:
        cards_html = "".join(_render_card(c) for c in payload["eval"]["cards"])
        eval_section = (
            '<div class="section-title">Evaluation runs</div>'
            f'<div class="grid">{cards_html}{telemetry_card}</div>'
        )
    else:
        eval_section = (
            '<div class="section-title">Evaluation runs</div>'
            '<div class="empty-state">'
            "No eval runs yet under <code>.agentops/results/</code>. "
            "Run <code>agentops eval run</code> to populate this section."
            "</div>"
            f'<div class="grid">{telemetry_card}</div>'
        )

    metrics_section = ""
    if payload["metrics"]:
        metrics_html = "".join(_render_card(c) for c in payload["metrics"])
        metrics_section = (
            '<div class="section-title">Quality metrics</div>'
            f'<div class="grid">{metrics_html}</div>'
        )

    watchdog = payload["watchdog"]
    if watchdog["has_history"]:
        watchdog_headline = "".join(
            _render_card(c, hero=True) for c in watchdog["headline_cards"]
        )
        watchdog_categories = "".join(
            _render_card(c) for c in watchdog["category_cards"]
        )
        watchdog_section = (
            '<div class="section-title">Watchdog findings</div>'
            f'<div class="grid">{watchdog_headline}</div>'
            '<div class="section-title sub">By category</div>'
            f'<div class="grid">{watchdog_categories}</div>'
        )
    else:
        watchdog_section = (
            '<div class="section-title">Watchdog findings</div>'
            '<div class="empty-state">'
            "No analysis history yet. Run "
            "<code>agentops agent analyze</code> to populate this section."
            "</div>"
        )

    counts = payload["summary_counts"]
    return _DASHBOARD_TEMPLATE.format(
        eval_section=eval_section,
        metrics_section=metrics_section,
        watchdog_section=watchdog_section,
        eval_runs=counts["eval_runs"],
        analyses=counts["analyses"],
        workspace=payload["workspace"],
    )


def _render_card(card: Dict[str, Any], *, hero: bool = False) -> str:
    spark = _sparkline_svg(card.get("series", []))
    badge = card["badge"]
    css_class = "card hero" if hero else "card"
    value = card.get("value", 0)
    unit = card.get("unit", "")
    unit_html = f'<span class="card-unit"> {unit}</span>' if unit else ""

    meta_html = ""
    if card.get("meta"):
        meta_items = "".join(f"<span>{m}</span>" for m in card["meta"] if m)
        if meta_items:
            meta_html = f'<div class="card-meta">{meta_items}</div>'

    return (
        f'<div class="{css_class}">'
        f'<div class="card-label">{card["label"]}</div>'
        f'<div class="card-value">{value}{unit_html}</div>'
        f"{spark}"
        f"{meta_html}"
        f'<div class="badge tone-{badge["tone"]}">{badge["label"]}</div>'
        f"</div>"
    )


def _render_telemetry_card(telemetry: Dict[str, Any]) -> str:
    tone = telemetry["tone"]
    icon = "●" if telemetry["enabled"] else "○"
    return (
        f'<div class="card telemetry">'
        f'<div class="card-label">Telemetry</div>'
        f'<div class="card-value tone-{tone}-text">{icon} {telemetry["label"]}</div>'
        f'<div class="telemetry-detail">{telemetry["detail"]}</div>'
        f'<div class="badge tone-{tone}">{"on" if telemetry["enabled"] else "off"}</div>'
        f"</div>"
    )


def _sparkline_svg(series: List[float]) -> str:
    if not series:
        return ""
    window = series[-12:]
    if len(window) == 1:
        window = [window[0], window[0]]
    width = 240
    height = 56
    pad = 4
    max_v = max(window)
    min_v = min(window)
    span = max(max_v - min_v, 1.0)
    step = (width - 2 * pad) / (len(window) - 1) if len(window) > 1 else 0
    points = []
    for i, v in enumerate(window):
        x = pad + i * step
        y = height - pad - ((v - min_v) / span) * (height - 2 * pad)
        points.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(points)
    last_x = pad + (len(window) - 1) * step
    last_y = (
        height
        - pad
        - ((window[-1] - min_v) / span) * (height - 2 * pad)
    )
    return (
        f'<svg class="sparkline" viewBox="0 0 {width} {height}" preserveAspectRatio="none">'
        f'<polyline fill="none" stroke="currentColor" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" points="{polyline}"/>'
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3" fill="currentColor"/>'
        f"</svg>"
    )


_DASHBOARD_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>AgentOps Monitor</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta http-equiv="refresh" content="15" />
<style>
  :root {{
    --bg: #0a0a0a;
    --card: #161616;
    --border: #1f1f1f;
    --text: #f4f4f5;
    --text-dim: #a1a1aa;
    --ok: #4ade80;
    --info: #38bdf8;
    --warn: #fbbf24;
    --crit: #f87171;
    --muted: #52525b;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  header {{
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 24px;
  }}
  header h1 {{
    margin: 0; font-size: 20px; font-weight: 600; letter-spacing: 0.02em;
  }}
  header .subtitle {{ color: var(--text-dim); font-size: 13px; }}
  .section-title {{
    margin: 28px 0 12px; font-size: 15px; font-weight: 600;
    color: var(--text); letter-spacing: 0.01em;
  }}
  .section-title.sub {{
    margin-top: 16px; font-size: 13px; color: var(--text-dim);
    font-weight: 500;
  }}
  .grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 16px;
  }}
  .card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 20px;
    display: flex; flex-direction: column; gap: 8px;
    color: var(--text);
  }}
  .card.hero {{ background: linear-gradient(160deg, #181818 0%, #121212 100%); }}
  .card.telemetry {{ border-style: dashed; }}
  .card-label {{ color: var(--text-dim); font-size: 13px; font-weight: 500; }}
  .card-value {{
    font-size: 36px; font-weight: 600; line-height: 1.1;
    margin: 4px 0 0;
    word-break: break-word;
  }}
  .card-unit {{ color: var(--text-dim); font-size: 14px; font-weight: 500; margin-left: 6px; }}
  .card-meta {{
    display: flex; flex-direction: column; gap: 2px;
    color: var(--text-dim); font-size: 12px; margin-top: 4px;
  }}
  .telemetry-detail {{
    color: var(--text-dim); font-size: 12px; line-height: 1.4;
  }}
  .sparkline {{ width: 100%; height: 56px; color: var(--info); margin-top: 4px; }}
  .card.hero .sparkline {{ color: var(--info); }}
  .badge {{
    display: inline-flex; align-self: flex-start; align-items: center;
    padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600;
    text-transform: lowercase; margin-top: 6px;
  }}
  .tone-ok    {{ background: rgba(74, 222, 128, 0.12); color: var(--ok); }}
  .tone-info  {{ background: rgba(56, 189, 248, 0.12); color: var(--info); }}
  .tone-warn  {{ background: rgba(251, 191, 36, 0.12); color: var(--warn); }}
  .tone-crit  {{ background: rgba(248, 113, 113, 0.12); color: var(--crit); }}
  .tone-muted {{ background: rgba(82, 82, 91, 0.20); color: var(--muted); }}
  .tone-ok-text    {{ color: var(--ok); }}
  .tone-warn-text  {{ color: var(--warn); }}
  .tone-crit-text  {{ color: var(--crit); }}
  .tone-info-text  {{ color: var(--info); }}
  .tone-muted-text {{ color: var(--muted); }}
  .empty-state {{
    background: var(--card); border: 1px dashed var(--border);
    border-radius: 16px; padding: 24px;
    color: var(--text-dim); margin-bottom: 16px;
  }}
  .empty-state code {{
    background: #1f1f1f; padding: 2px 6px; border-radius: 6px;
    color: var(--text);
  }}
  footer {{
    margin-top: 32px; font-size: 12px; color: var(--text-dim);
    text-align: center;
  }}
</style>
</head>
<body>
<header>
  <div>
    <h1>AgentOps monitor</h1>
    <div class="subtitle">{workspace}</div>
  </div>
  <div class="subtitle">{eval_runs} eval(s) · {analyses} analysis run(s)</div>
</header>

{eval_section}
{metrics_section}
{watchdog_section}

<footer>Auto-refreshes every 15s · agentops monitor</footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


def create_app(workspace: Path):
    """Return a FastAPI app rooted at *workspace*."""
    try:
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse, JSONResponse
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "agentops monitor requires the [agent] extra. "
            "Install with: pip install 'agentops-toolkit[agent]'"
        ) from exc

    app = FastAPI(title="AgentOps Monitor", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def _index() -> HTMLResponse:
        payload = build_dashboard_payload(workspace)
        return HTMLResponse(render_dashboard_html(payload))

    @app.get("/api/history")
    def _api_history(limit: Optional[int] = None) -> JSONResponse:
        records = load_analysis_history(workspace, limit=limit)
        return JSONResponse([r.to_dict() for r in records])

    @app.get("/api/eval-runs")
    def _api_eval_runs(limit: int = 24) -> JSONResponse:
        return JSONResponse(_load_eval_runs(workspace, limit=limit))

    @app.get("/api/telemetry")
    def _api_telemetry() -> JSONResponse:
        return JSONResponse(_telemetry_status())

    @app.get("/healthz")
    def _healthz() -> Dict[str, str]:
        return {"status": "ok"}

    return app