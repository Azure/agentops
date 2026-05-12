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

import base64
import json
import os
import re
from importlib.resources import files as _pkg_files
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
    production = _build_production_section(telemetry)

    return {
        "workspace": str(workspace.resolve()),
        "telemetry": telemetry,
        "production": production,
        "eval": _build_eval_section(eval_runs),
        "metrics": _build_metrics_cards(eval_runs),
        "watchdog": _build_watchdog_section(records),
        "summary_counts": {
            "eval_runs": len(eval_runs),
            "analyses": len(records),
        },
    }


def _build_production_section(telemetry: Dict[str, Any]) -> Dict[str, Any]:
    """Pull live App Insights data when telemetry is wired up."""
    if not telemetry.get("enabled"):
        return {"has_data": False, "cards": [], "skip_reason": "telemetry off"}

    # Resolve the connection string once more to extract ApplicationId.
    # _telemetry_status() already validated the path is reachable; we re-run
    # the cheap env lookup / discovery (results are cached when discovery
    # ran already in this process).
    conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING") or os.getenv(
        "AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING"
    )
    if not conn and os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT"):
        try:
            from agentops.utils.foundry_discovery import (
                resolve_appinsights_connection_from_env,
            )
            conn = resolve_appinsights_connection_from_env()
        except Exception:  # noqa: BLE001
            conn = None

    from agentops.agent.production_telemetry import (
        collect_production_metrics,
        extract_application_id,
    )
    app_id = extract_application_id(conn)
    return collect_production_metrics(app_id)


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
            "labels": [_label_for_run(r) for r in eval_runs],
            "badge": {"label": _badge_runs(len(eval_runs)), "tone": "info"},
            "source": ".agentops/results/",
        },
        {
            "key": "pass_rate",
            "label": "Pass rate",
            "value": f"{int(pass_rate * 100)}%",
            "unit": "",
            "series": pass_series,
            "labels": [
                f"{_label_for_run(r)} · {'PASS' if r['passed'] else 'FAIL'}"
                for r in eval_runs
            ],
            "badge": _badge_pass_rate(pass_rate),
            "source": "results.json · summary.overall_passed",
        },
        {
            "key": "items",
            "label": "Items / run",
            "value": int(items_total_series[-1]) if items_total_series else 0,
            "unit": "rows",
            "series": items_total_series,
            "labels": [_label_for_run(r) for r in eval_runs],
            "badge": {"label": "latest", "tone": "muted"},
            "source": "results.json · summary.items_total",
        },
        {
            "key": "latest_run",
            "label": "Latest target",
            "value": latest["target"] or "—",
            "unit": "",
            "value_kind": "text",
            "series": pass_series[-6:],
            "labels": [
                f"{_label_for_run(r)} · {r.get('target') or '—'}"
                for r in eval_runs[-6:]
            ],
            "badge": {
                "label": "passed" if latest["passed"] else "failed",
                "tone": "ok" if latest["passed"] else "crit",
            },
            "meta": [
                latest["timestamp"] or "",
                f"duration: {latest['duration']:.1f}s" if latest["duration"] else "duration: —",
                f"execution: {latest['execution']}" if latest["execution"] else "execution: —",
            ],
            "source": "results.json · target.raw",
        },
    ]
    return {"has_runs": True, "cards": cards}


def _label_for_run(run: Dict[str, Any]) -> str:
    """Build a human label for a sparkline point on the eval cards."""
    ts = run.get("timestamp") or ""
    # Trim to minute precision for hover-tip readability.
    ts = ts[:16].replace("T", " ") if isinstance(ts, str) else str(ts)
    return ts or "—"


def _build_metrics_cards(eval_runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not eval_runs:
        return []
    cards: List[Dict[str, Any]] = []
    for key, label, unit in _QUALITY_METRICS:
        # Build aligned series + labels: keep only runs that reported this metric.
        paired = [
            (run, run["metrics"].get(key))
            for run in eval_runs
            if run["metrics"].get(key) is not None
        ]
        if not paired:
            continue
        series = [float(v) for _r, v in paired]
        labels = [
            f"{_label_for_run(r)} · {float(v):.2f}"
            for r, v in paired
        ]
        latest = series[-1]
        is_latency = key == "avg_latency_seconds"
        badge = _metric_trend_badge(series, is_latency=is_latency)
        cards.append({
            "key": key,
            "label": label,
            "value": f"{latest:.2f}",
            "unit": unit,
            "series": series,
            "labels": labels,
            "badge": badge,
            "source": f"results.json · aggregate_metrics.{key}",
        })
    return cards


def _build_watchdog_section(records: List[AnalysisRecord]) -> Dict[str, Any]:
    latest = records[-1] if records else None

    def _series(extractor) -> List[float]:
        return [float(extractor(r) or 0) for r in records]

    findings_series = _series(lambda r: r.findings_total)
    critical_series = _series(lambda r: r.findings_by_severity.get("critical", 0))
    record_labels = [_label_for_record(r) for r in records]

    category_cards: List[Dict[str, Any]] = []
    for key, label in _CATEGORY_LABELS.items():
        series = _series(lambda r, k=key: r.findings_by_category.get(k, 0))
        current = int(series[-1]) if series else 0
        labels = [
            f"{_label_for_record(r)} · {int(r.findings_by_category.get(key, 0))} finding(s)"
            for r in records
        ]
        category_cards.append({
            "key": key,
            "label": label,
            "value": current,
            "unit": "",
            "series": series,
            "labels": labels,
            "badge": _category_badge(key, current, records),
            "source": f"history.jsonl · findings_by_category.{key}",
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
                "labels": [
                    f"{_label_for_record(r)} · {r.findings_total} finding(s)"
                    for r in records
                ],
                "badge": _headline_badge_total(findings_series),
                "source": "history.jsonl · findings_total",
            },
            {
                "key": "critical",
                "label": "Critical",
                "value": int(critical_series[-1]) if critical_series else 0,
                "unit": "open",
                "series": critical_series,
                "labels": [
                    f"{_label_for_record(r)} · {r.findings_by_severity.get('critical', 0)} critical"
                    for r in records
                ],
                "badge": _headline_badge_critical(critical_series),
                "source": "history.jsonl · findings_by_severity.critical",
            },
            {
                "key": "last_analysis",
                "label": "Last analysis",
                "value": latest_label,
                "unit": "",
                "value_kind": "text",
                "series": findings_series[-6:],
                "labels": record_labels[-6:],
                "badge": latest_badge,
                "meta": _latest_run_meta(latest),
                "source": "history.jsonl · latest record",
            },
        ],
        "category_cards": category_cards,
    }


def _label_for_record(record: AnalysisRecord) -> str:
    """Short timestamp label for a watchdog sparkline point."""
    ts = record.timestamp or ""
    return ts[:16].replace("T", " ") if isinstance(ts, str) else "—"


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
            "label": "App Insights",
            "detail": "Connected via APPLICATIONINSIGHTS_CONNECTION_STRING.",
            "portal_url": _appinsights_portal_url(explicit_conn),
            "tone": "ok",
        }
    if otlp:
        return {
            "enabled": True,
            "source": "otlp",
            "label": "OTLP exporter",
            "detail": f"AGENTOPS_OTLP_ENDPOINT={otlp}",
            "portal_url": None,
            "tone": "ok",
        }
    if project:
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
                "label": "App Insights",
                "detail": "Auto-discovered from the Foundry project endpoint.",
                "portal_url": _appinsights_portal_url(conn),
                "tone": "ok",
            }
        return {
            "enabled": False,
            "source": "discovery_failed",
            "label": "Telemetry off",
            "detail": (
                "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT is set but no App "
                "Insights was discovered. Connect one in Foundry or set "
                "APPLICATIONINSIGHTS_CONNECTION_STRING."
            ),
            "portal_url": None,
            "tone": "warn",
        }
    return {
        "enabled": False,
        "source": "off",
        "label": "Telemetry off",
        "detail": (
            "Set AZURE_AI_FOUNDRY_PROJECT_ENDPOINT for auto-discovery, "
            "or APPLICATIONINSIGHTS_CONNECTION_STRING to route traces."
        ),
        "portal_url": None,
        "tone": "muted",
    }


def _appinsights_portal_url(connection_string: Optional[str]) -> Optional[str]:
    """Build a deep link to the Logs blade for the App Insights resource
    identified by the connection string. Returns ``None`` when the
    connection string lacks the ``ApplicationId`` segment (older format).
    """
    if not connection_string:
        return None
    m = re.search(r"ApplicationId=([0-9a-fA-F-]+)", connection_string)
    if not m:
        return None
    app_id = m.group(1)
    # ApplicationInsights-Extension landing query inspecting recent AgentOps spans.
    query = (
        "union dependencies, requests, traces"
        "\n| where timestamp > ago(1h)"
        "\n| where name has 'ANALYZE' or name has 'RUN ' or name has 'eval_item' "
        "or name has 'invoke_agent' or name has 'evaluator' or name has 'chat'"
        "\n| order by timestamp desc"
        "\n| take 100"
    )
    # The portal accepts an `appId` query param shortcut.
    return (
        "https://portal.azure.com/#blade/Microsoft_OperationsManagementSuite_Workspace/"
        f"AnalyticsBlade/initiator/AnalyticsShareLinkToQuery/isQueryEditorVisible/true/"
        f"sourceId/%2Fapps%2F{app_id}/scope/%7B%22resources%22%3A%5B%7B%22resourceId%22"
        f"%3A%22%2Fapps%2F{app_id}%22%7D%5D%7D/query/"
        + _url_quote(query)
    )


def _url_quote(text: str) -> str:
    from urllib.parse import quote
    return quote(text, safe="")


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


def _render_card(card: Dict[str, Any], *, hero: bool = False) -> str:
    series = card.get("series", [])
    labels = card.get("labels") or []
    spark = _sparkline_svg(series, labels=labels)
    badge = card["badge"]
    css_class = "card hero" if hero else "card"
    value = card.get("value", 0)
    unit = card.get("unit", "")
    unit_html = f'<span class="card-unit"> {unit}</span>' if unit else ""

    # Textual values (e.g. "agent-smoke:3") wrap awkwardly when rendered at
    # 36px. Detect and switch to a compact text style.
    value_kind = card.get("value_kind", "numeric")
    if value_kind == "numeric" and isinstance(value, str):
        if any(c.isalpha() and c != "." for c in value):
            value_kind = "text"
    value_css = "card-value card-value-text" if value_kind == "text" else "card-value"

    # value-num span is updated by JS on sparkline hover; data-orig holds the
    # original so leaving the card restores it.
    value_inner = (
        f'<span class="value-num" data-orig="{_html_escape(str(value))}">'
        f"{_html_escape(str(value))}</span>"
    )

    meta_html = ""
    if card.get("meta"):
        meta_items = "".join(
            f"<span>{_html_escape(m)}</span>" for m in card["meta"] if m
        )
        if meta_items:
            meta_html = f'<div class="card-meta">{meta_items}</div>'

    # Hover detail shows the sparkline point's timestamp/label when present.
    hover_html = '<div class="hover-detail" data-default="">&nbsp;</div>'

    footer_html = ""
    if card.get("source"):
        footer_html = (
            f'<div class="card-source" title="Data source">'
            f'<span class="source-icon">⌖</span>{_html_escape(card["source"])}</div>'
        )

    return (
        f'<div class="{css_class}">'
        f'<div class="card-label">{_html_escape(card["label"])}</div>'
        f'<div class="{value_css}">{value_inner}{unit_html}</div>'
        f"{spark}"
        f"{hover_html}"
        f"{meta_html}"
        f'<div class="badge tone-{badge["tone"]}">{_html_escape(badge["label"])}</div>'
        f"{footer_html}"
        f"</div>"
    )


def _html_escape(text: Any) -> str:
    """Minimal HTML attribute/text escaping."""
    if text is None:
        return ""
    s = str(text)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_telemetry_card(telemetry: Dict[str, Any]) -> str:
    tone = telemetry["tone"]
    dot = '<span class="dot dot-on"></span>' if telemetry["enabled"] else '<span class="dot dot-off"></span>'

    link_html = ""
    if telemetry.get("portal_url"):
        link_html = (
            f'<a class="card-link" href="{telemetry["portal_url"]}" '
            f'target="_blank" rel="noopener noreferrer">'
            f'View in App Insights →</a>'
        )

    source_html = ""
    src = telemetry.get("source")
    if src:
        src_label = {
            "env": "APPLICATIONINSIGHTS_CONNECTION_STRING",
            "otlp": "AGENTOPS_OTLP_ENDPOINT",
            "discovery": "Foundry project endpoint (auto)",
            "discovery_failed": "discovery failed",
            "off": "no env var set",
        }.get(src, src)
        source_html = (
            f'<div class="card-source"><span class="source-icon">⌖</span>{src_label}</div>'
        )

    return (
        f'<div class="card telemetry">'
        f'<div class="card-label">Telemetry</div>'
        f'<div class="card-value card-value-text tone-{tone}-text">{dot}{telemetry["label"]}</div>'
        f'<div class="telemetry-detail">{telemetry["detail"]}</div>'
        f'<div class="badge tone-{tone}">{"on" if telemetry["enabled"] else "off"}</div>'
        f"{link_html}"
        f"{source_html}"
        f"</div>"
    )


def _sparkline_svg(series: List[float], *, labels: Optional[List[str]] = None) -> str:
    if not series:
        return ""
    window = series[-12:]
    label_window = (labels or [])[-12:]
    # Align label count with the window.
    if len(label_window) < len(window):
        label_window = label_window + [""] * (len(window) - len(label_window))
    if len(window) == 1:
        window = [window[0], window[0]]
        label_window = [label_window[0] if label_window else "", label_window[0] if label_window else ""]
    width = 240
    height = 56
    pad = 4
    max_v = max(window)
    min_v = min(window)
    span = max(max_v - min_v, 1.0)
    step = (width - 2 * pad) / (len(window) - 1) if len(window) > 1 else 0
    points: List[Tuple[float, float]] = []
    for i, v in enumerate(window):
        x = pad + i * step
        y = height - pad - ((v - min_v) / span) * (height - 2 * pad)
        points.append((x, y))
    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    last_x, last_y = points[-1]
    area_points = (
        " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        + f" {last_x:.1f},{height - pad} {pad:.1f},{height - pad}"
    )

    # Render every point as an interactive dot. The .dot.is-last class
    # styles the rightmost (current) point more prominently.
    dots: List[str] = []
    for i, ((x, y), value) in enumerate(zip(points, window)):
        label = _html_escape(label_window[i] if i < len(label_window) else "")
        is_last = "is-last" if i == len(points) - 1 else ""
        formatted_value = (
            f"{value:.2f}" if isinstance(value, float) and not value.is_integer()
            else f"{int(value)}"
        )
        dots.append(
            f'<circle class="dot {is_last}" cx="{x:.1f}" cy="{y:.1f}" r="3.5" '
            f'fill="currentColor" data-v="{formatted_value}" data-l="{label}">'
            f'<title>{label}{" — " + formatted_value if label else formatted_value}</title>'
            f'</circle>'
        )
    dots_svg = "".join(dots)

    return (
        f'<svg class="sparkline" viewBox="0 0 {width} {height}" preserveAspectRatio="none">'
        f'<polygon fill="currentColor" fill-opacity="0.08" points="{area_points}"/>'
        f'<polyline fill="none" stroke="currentColor" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" points="{polyline}"/>'
        f"{dots_svg}"
        f"</svg>"
    )


def _icon_data_uri() -> str:
    """Read the bundled icon.png and return a base64 data URI.

    Falls back to a tiny inline SVG glyph when the asset is missing
    (older installs) so the dashboard still renders.
    """
    try:
        data = _pkg_files("agentops.templates").joinpath("icon.png").read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception:  # noqa: BLE001
        # Fallback SVG dot.
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#38bdf8">'
            '<circle cx="12" cy="12" r="10"/></svg>'
        )
        return "data:image/svg+xml;utf8," + svg


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

    production = payload.get("production") or {}
    production_section = ""
    if production.get("has_data") and production.get("cards"):
        prod_html = "".join(_render_card(c, hero=True) for c in production["cards"])
        production_section = (
            '<div class="section-title">Production telemetry'
            ' <span class="live-pill">live · App Insights</span></div>'
            f'<div class="grid">{prod_html}</div>'
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
    workspace_display = _shorten_workspace(payload["workspace"])
    return _DASHBOARD_TEMPLATE.format(
        eval_section=eval_section,
        metrics_section=metrics_section,
        production_section=production_section,
        watchdog_section=watchdog_section,
        eval_runs=counts["eval_runs"],
        analyses=counts["analyses"],
        workspace_display=workspace_display,
        workspace=payload["workspace"],
        icon_uri=_icon_data_uri(),
    )


def _shorten_workspace(path: str) -> str:
    """Show only the folder name + parent for compact heading display."""
    p = Path(path)
    parts = p.parts
    if len(parts) <= 2:
        return path
    return str(Path(*parts[-2:]))


_DASHBOARD_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>AgentOps Monitor</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta http-equiv="refresh" content="15" />
<style>
  :root {{
    --bg: #08090b;
    --bg-grad: radial-gradient(1200px 600px at 80% -10%, rgba(56, 189, 248, 0.06), transparent 60%);
    --card: #161618;
    --card-hi: #1c1c1f;
    --border: rgba(255, 255, 255, 0.06);
    --border-strong: rgba(255, 255, 255, 0.12);
    --text: #fafafa;
    --text-dim: #a1a1aa;
    --text-faint: #71717a;
    --ok: #4ade80;
    --info: #38bdf8;
    --warn: #fbbf24;
    --crit: #f87171;
    --muted: #71717a;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; background: var(--bg); color: var(--text); }}
  body {{
    padding: 28px 32px 48px;
    background: var(--bg) var(--bg-grad) no-repeat;
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", system-ui, sans-serif;
    -webkit-font-smoothing: antialiased;
    max-width: 1400px; margin: 0 auto;
  }}
  header {{
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 28px; padding-bottom: 20px;
    border-bottom: 1px solid var(--border);
  }}
  header .brand {{ display: flex; align-items: center; gap: 14px; }}
  header .brand img {{
    width: 40px; height: 40px; border-radius: 10px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.4);
  }}
  header h1 {{
    margin: 0; font-size: 22px; font-weight: 700; letter-spacing: -0.01em;
  }}
  header .subtitle {{
    color: var(--text-dim); font-size: 12px; font-weight: 500;
    font-family: "SF Mono", "Cascadia Code", Consolas, monospace;
    margin-top: 2px;
  }}
  header .stats {{
    display: flex; align-items: center; gap: 18px;
    color: var(--text-dim); font-size: 12px; font-weight: 500;
  }}
  header .stat-num {{ color: var(--text); font-size: 18px; font-weight: 600; }}
  .section-title {{
    margin: 32px 0 14px; font-size: 11px; font-weight: 700;
    color: var(--text-faint); letter-spacing: 0.12em;
    text-transform: uppercase;
  }}
  .section-title.sub {{
    margin-top: 18px; font-size: 11px;
  }}
  .live-pill {{
    display: inline-block; margin-left: 8px;
    padding: 2px 8px; border-radius: 999px;
    background: rgba(74, 222, 128, 0.12); color: var(--ok);
    font-size: 10px; font-weight: 700; letter-spacing: 0.05em;
    text-transform: uppercase; vertical-align: middle;
    animation: live-pulse 2s ease-in-out infinite;
  }}
  @keyframes live-pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.6; }}
  }}
  .grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 14px;
  }}
  .card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 18px;
    padding: 18px 20px;
    display: flex; flex-direction: column; gap: 6px;
    color: var(--text);
    transition: border-color 0.15s ease, transform 0.15s ease;
    position: relative; overflow: hidden;
  }}
  .card:hover {{ border-color: var(--border-strong); }}
  .card.hero {{
    background: linear-gradient(165deg, var(--card-hi) 0%, var(--card) 100%);
  }}
  .card.telemetry {{
    border-style: dashed; border-color: var(--border-strong);
    background: linear-gradient(165deg, #181a1f 0%, var(--card) 100%);
  }}
  .card-label {{
    color: var(--text-dim); font-size: 12px; font-weight: 600;
    letter-spacing: 0.02em;
  }}
  .card-value {{
    font-size: 36px; font-weight: 700; line-height: 1.05;
    margin: 2px 0 0; letter-spacing: -0.02em;
  }}
  .card-value-text {{
    font-size: 20px; line-height: 1.25; font-weight: 600;
    word-break: break-word;
  }}
  .card-unit {{
    color: var(--text-dim); font-size: 13px; font-weight: 500;
    margin-left: 5px;
  }}
  .card-meta {{
    display: flex; flex-direction: column; gap: 2px;
    color: var(--text-faint); font-size: 11px; margin-top: 4px;
    font-family: "SF Mono", "Cascadia Code", Consolas, monospace;
  }}
  .telemetry-detail {{
    color: var(--text-dim); font-size: 12px; line-height: 1.45;
  }}
  .card-link {{
    color: var(--info); text-decoration: none; font-size: 12px;
    font-weight: 600; margin-top: 4px;
  }}
  .card-link:hover {{ text-decoration: underline; }}
  .card-source {{
    color: var(--text-faint); font-size: 10.5px;
    font-family: "SF Mono", "Cascadia Code", Consolas, monospace;
    margin-top: 6px; padding-top: 10px;
    border-top: 1px solid var(--border);
    display: flex; align-items: center; gap: 6px;
  }}
  .source-icon {{ opacity: 0.6; }}
  .sparkline {{
    width: 100%; height: 56px; color: var(--info); margin-top: 6px;
    cursor: crosshair;
  }}
  .sparkline .dot {{
    opacity: 0; transition: opacity 0.12s ease, r 0.12s ease;
  }}
  .sparkline:hover .dot {{ opacity: 0.55; }}
  .sparkline .dot.is-last {{ opacity: 1; }}
  .sparkline .dot:hover {{ opacity: 1; r: 5; }}
  .card.hero .sparkline {{ color: var(--info); }}
  .hover-detail {{
    color: var(--text-dim); font-size: 11px; font-weight: 500;
    font-family: "SF Mono", "Cascadia Code", Consolas, monospace;
    margin-top: 2px; min-height: 14px;
    transition: color 0.12s ease;
  }}
  .hover-detail.active {{ color: var(--info); }}
  .dot {{
    display: inline-block; width: 9px; height: 9px; border-radius: 50%;
    margin-right: 8px; vertical-align: middle;
  }}
  .dot-on  {{ background: var(--ok); box-shadow: 0 0 8px rgba(74, 222, 128, 0.6); }}
  .dot-off {{ background: var(--muted); }}
  .badge {{
    display: inline-flex; align-self: flex-start; align-items: center;
    padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 600;
    text-transform: lowercase; margin-top: 4px; letter-spacing: 0.01em;
  }}
  .tone-ok    {{ background: rgba(74, 222, 128, 0.12); color: var(--ok); }}
  .tone-info  {{ background: rgba(56, 189, 248, 0.12); color: var(--info); }}
  .tone-warn  {{ background: rgba(251, 191, 36, 0.13); color: var(--warn); }}
  .tone-crit  {{ background: rgba(248, 113, 113, 0.13); color: var(--crit); }}
  .tone-muted {{ background: rgba(113, 113, 122, 0.18); color: var(--text-dim); }}
  .tone-ok-text    {{ color: var(--ok); }}
  .tone-warn-text  {{ color: var(--warn); }}
  .tone-crit-text  {{ color: var(--crit); }}
  .tone-info-text  {{ color: var(--info); }}
  .tone-muted-text {{ color: var(--text-dim); }}
  .empty-state {{
    background: var(--card); border: 1px dashed var(--border-strong);
    border-radius: 16px; padding: 22px;
    color: var(--text-dim); margin-bottom: 14px; font-size: 13px;
  }}
  .empty-state code {{
    background: rgba(255, 255, 255, 0.04); padding: 2px 7px; border-radius: 6px;
    color: var(--text); font-size: 12px;
    font-family: "SF Mono", "Cascadia Code", Consolas, monospace;
  }}
  footer {{
    margin-top: 40px; font-size: 11px; color: var(--text-faint);
    text-align: center; font-family: "SF Mono", monospace;
  }}
</style>
</head>
<body>
<header>
  <div class="brand">
    <img src="{icon_uri}" alt="AgentOps" />
    <div>
      <h1>AgentOps monitor</h1>
      <div class="subtitle" title="{workspace}">{workspace_display}</div>
    </div>
  </div>
  <div class="stats">
    <div><span class="stat-num">{eval_runs}</span> eval(s)</div>
    <div><span class="stat-num">{analyses}</span> analysis run(s)</div>
  </div>
</header>

{eval_section}
{production_section}
{metrics_section}
{watchdog_section}

<footer>Auto-refreshes every 15s · <code>agentops monitor</code></footer>

<script>
// Interactive sparkline hover: highlight the hovered point and swap the
// card's headline value to that point's value, then restore on leave.
(function() {{
  document.querySelectorAll('.card').forEach(function(card) {{
    const valueNum = card.querySelector('.value-num');
    const hoverDetail = card.querySelector('.hover-detail');
    if (!valueNum) return;
    const origValue = valueNum.dataset.orig || valueNum.textContent;
    card.querySelectorAll('svg.sparkline .dot').forEach(function(dot) {{
      dot.addEventListener('mouseenter', function() {{
        const v = dot.getAttribute('data-v');
        const l = dot.getAttribute('data-l');
        if (v) valueNum.textContent = v;
        if (hoverDetail) {{
          hoverDetail.textContent = l || '';
          hoverDetail.classList.add('active');
        }}
      }});
    }});
    card.addEventListener('mouseleave', function() {{
      valueNum.textContent = origValue;
      if (hoverDetail) {{
        hoverDetail.textContent = '';
        hoverDetail.classList.remove('active');
      }}
    }});
  }});
}})();
</script>
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

    @app.get("/api/production")
    def _api_production() -> JSONResponse:
        return JSONResponse(_build_production_section(_telemetry_status()))

    @app.get("/healthz")
    def _healthz() -> Dict[str, str]:
        return {"status": "ok"}

    return app