"""Accessible, dependency-free HTML/CSS/JS fragments for the hosted Observe UI.

This module renders the Observe navigation surface described in
``specs/011-deploy-hosted-cockpit`` (User Story 2 "Explore Aggregated
Telemetry Without Manual Correlation" and User Story 3 "Diagnose Missing and
Partial Telemetry"): Overview cards, Agents, Models/usage, Tools, Runs, and
Telemetry coverage/troubleshooting views, plus agent/trace detail shells, all
wired to a single shared filter bar.

Design constraints carried over from the spec/plan (see FR-022..FR-053):

* No client-side dependencies. Every fragment is plain HTML/CSS/JS emitted as
  Python strings; there is no build step and no external script/style tag.
* Filters have a *draft* state (what is currently typed/selected in the form)
  and a separate *applied* state (the last committed selection actually used
  for queries and URL persistence). The two are only ever reconciled by an
  explicit "Apply filters" action -- nothing is auto-applied while typing.
* The URL only ever encodes the small, non-sensitive filter keys enumerated
  in :data:`OBSERVE_FILTER_QUERY_KEYS` (plus the active view). Raw
  generative-AI content (input/output messages, system instructions, tool
  content, evaluation explanations) is **never** written to the URL, to
  ``localStorage``/``sessionStorage``, or to cookies -- see
  :data:`_OBSERVE_SCRIPT` and the safety tests in ``test_observe_ui.py``.
* The default time range is the trailing 24 hours; refresh happens
  automatically every five minutes and can also be triggered manually. Every
  fetch is issued with an ``AbortController`` and a monotonically increasing
  request token so that a response for a superseded request is silently
  discarded even if it resolves after a newer request has started.
* Protected generative-AI content (trace/span input, output, system
  instructions, tool content, evaluation explanations) is never fetched
  automatically. The trace-detail shell always renders an explicit "Load
  protected content" action; only clicking it may reveal that content, and
  only for the fields the backend reports as ``available``.
* Coverage/troubleshooting rendering never hides evidence collected from
  other sources: every row is rendered independently of every other row's
  state, so one failing or partial source can never erase data that other
  sources returned successfully.
* "Zero" and "missing" are rendered distinctly everywhere a metric can be
  either: a reported value of literal ``0`` reads as ``0`` (with a
  ``metric-zero`` marker class), while a value that was not reported at all
  (``None``) reads as "Not reported" (with a ``metric-missing`` marker
  class). This applies to failure rates, latency, token totals, and
  coverage/troubleshooting rows alike.

Every public render function accepts either a :class:`~typing.Mapping` (e.g.
a plain ``dict`` built by a test or by ``service.py`` once implemented) or an
object with matching attributes (e.g. the Pydantic models defined in
``agentops.core.observe``). Only *rendering* logic lives here: nothing in
this module performs network I/O, reads environment variables, or imports
Azure SDKs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urlencode

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Views exposed by the Observe navigation, in display order.
OBSERVE_VIEWS: tuple[str, ...] = ("overview", "agents", "usage", "tools", "runs", "coverage")

#: Human-readable labels for each view, used by the nav and page title.
OBSERVE_VIEW_LABELS: dict[str, str] = {
    "overview": "Overview",
    "agents": "Agents",
    "usage": "Models and usage",
    "tools": "Tools",
    "runs": "Runs",
    "coverage": "Telemetry coverage",
}

#: Maps each internal ``OBSERVE_VIEWS`` identifier to the ``ObserveQuery.view``
#: wire value defined by ``contracts/observe-api.openapi.yaml``. The internal
#: name ``"usage"`` (used throughout this module's DOM ids/CSS classes) does
#: not match the OpenAPI enum, which spells the same view ``"models"``; this
#: mapping is applied only when building the JSON body sent to
#: ``POST /api/observe/query`` so the request stays contract-valid without
#: renaming every internal identifier.
OBSERVE_VIEW_WIRE_NAMES: dict[str, str] = {
    "overview": "overview",
    "agents": "agents",
    "usage": "models",
    "tools": "tools",
    "runs": "runs",
    "coverage": "coverage",
}

#: The *only* filter keys that may ever be written to the URL query string.
#: This list intentionally excludes every raw generative-AI content field
#: (input/output messages, system instructions, tool content, evaluation
#: explanations) -- those must never be persisted anywhere in the browser.
OBSERVE_FILTER_QUERY_KEYS: tuple[str, ...] = (
    "foundry_resource_id",
    "project_resource_id",
    "agent_id",
    "model",
    "tool_name",
    "run_key",
    "start",
    "end",
)

#: Cost selectors are intentionally separate from the shared Observe filters.
#: A cost request sends only these fields because its configured period is the
#: authoritative window and allocation is not narrowed by Observe identity
#: filters.
COST_FILTER_QUERY_KEYS: tuple[str, ...] = (
    "cost_period_id",
    "cost_component_id",
    "cost_breakdown",
    "cost_agent_key",
)

COST_DISCLAIMER = (
    "Operational cost allocation from declared billed totals and observed usage; "
    "not an invoice or billing-accurate charge."
)

COST_BREAKDOWN_WARNING = (
    "Agent, tool, and run breakdowns are alternative reconciliations of the same "
    "billed pools; do not add them together."
)

#: Default lookback window, in hours, applied when no range is in the URL.
DEFAULT_RANGE_HOURS: int = 24

#: Automatic refresh interval, in milliseconds (five minutes).
AUTO_REFRESH_MS: int = 5 * 60 * 1000

#: Maximum number of points ever drawn for a single chart series. Longer
#: series are downsampled (see :func:`_bound_points`) so that "bounded agent
#: trends" (T053) never render unbounded SVG markup for a long time range.
MAX_TREND_POINTS: int = 60

#: Copy shown for each of the eight ``CoverageState`` values understood by
#: ``agentops.core.observe.CoverageState``. ``"error"`` is not part of the
#: documented OpenAPI enum (which lists the other seven) but is handled here
#: as a safe fallback so an unexpected backend state still renders something
#: actionable instead of raising or rendering nothing.
COVERAGE_STATE_LABELS: dict[str, dict[str, str]] = {
    "available": {"label": "Available", "tone": "ok"},
    "partial": {"label": "Partial", "tone": "warn"},
    "no_data": {"label": "No data found", "tone": "muted"},
    "not_reported": {"label": "Not reported", "tone": "muted"},
    "not_configured": {"label": "Not configured", "tone": "muted"},
    "inaccessible": {"label": "Inaccessible", "tone": "crit"},
    "protected_or_unavailable": {"label": "Protected or unavailable", "tone": "warn"},
    "error": {"label": "Error", "tone": "crit"},
}

#: Human-readable labels for each ``CoverageResult.dimension`` value.
COVERAGE_DIMENSION_LABELS: dict[str, str] = {
    "resource_access": "Resource access",
    "telemetry_connection": "Telemetry connection",
    "recent_traces": "Recent traces",
    "agent_attribution": "Agent attribution",
    "model_attribution": "Model attribution",
    "token_usage": "Token usage",
    "tool_attribution": "Tool attribution",
    "run_correlation": "Run correlation",
    "trace_correlation": "Trace correlation",
    "protected_content": "Protected content",
    "cost_attribution": "Cost attribution",
}

#: Copy for each ``GenerativeAIContent.protection_state`` value.
PROTECTION_STATE_LABELS: dict[str, dict[str, str]] = {
    "available": {"label": "Available", "tone": "ok"},
    "protected_or_unavailable": {"label": "Protected or unavailable", "tone": "warn"},
    "not_configured": {"label": "Not configured", "tone": "muted"},
}

#: Best-effort, human-friendly labels for *documented* portal link keys.
#: Any key not listed here still renders (title-cased) rather than being
#: dropped -- this is the "best-effort labeling for undocumented portal
#: targets" half of T053.
_KNOWN_PORTAL_LABELS: dict[str, str] = {
    "foundry_resource": "Open Foundry resource",
    "foundry_project": "Open Foundry project",
    "foundry_trace": "Open trace in Foundry",
    "azure_monitor_resource": "Open Azure Monitor resource",
    "azure_monitor_transaction": "Open transaction in Azure Monitor",
}

#: Marker glyphs cycled across chart series so lines are distinguishable
#: without relying on color alone (FR-035). Order matters: it is asserted on
#: directly in tests.
_MARKER_GLYPHS: tuple[str, str, str, str] = ("\u25cf", "\u25a0", "\u25b2", "\u25c6")
_MARKER_SHAPE_NAMES: tuple[str, str, str, str] = ("circle", "square", "triangle", "diamond")


__all__ = [
    "OBSERVE_VIEWS",
    "OBSERVE_VIEW_LABELS",
    "OBSERVE_VIEW_WIRE_NAMES",
    "OBSERVE_FILTER_QUERY_KEYS",
    "COST_FILTER_QUERY_KEYS",
    "COST_DISCLAIMER",
    "COST_BREAKDOWN_WARNING",
    "DEFAULT_RANGE_HOURS",
    "AUTO_REFRESH_MS",
    "MAX_TREND_POINTS",
    "COVERAGE_STATE_LABELS",
    "COVERAGE_DIMENSION_LABELS",
    "PROTECTION_STATE_LABELS",
    "html_escape",
    "render_source_label",
    "render_refreshed_at",
    "render_last_seen",
    "render_trend_chart",
    "render_observe_nav",
    "render_filter_bar",
    "render_overview_cards",
    "render_agents_table",
    "render_cost_controls",
    "render_cost_view",
    "render_models_usage_table",
    "render_tools_table",
    "render_runs_table",
    "render_coverage_table",
    "render_diagnostics_banner",
    "render_coverage_view",
    "render_portal_links",
    "render_agent_detail_shell",
    "render_trace_detail_shell",
    "render_observe_page",
]


# ---------------------------------------------------------------------------
# Small generic helpers
# ---------------------------------------------------------------------------


def html_escape(value: Any) -> str:
    """Escape ``value`` for safe inclusion in HTML text/attribute content.

    Mirrors the minimal escaping convention already used by
    ``agentops.agent.cockpit`` (``&``, ``<``, ``>``, ``"``) so markup emitted
    by this module reads consistently with the rest of the local Cockpit.
    """
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _get(source: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from ``source``, whether it is a mapping or an object.

    This lets every render function accept either a plain ``dict`` (as
    constructed by tests, or by a future ``service.py``) or an instance of
    one of the Pydantic models in ``agentops.core.observe`` (e.g.
    ``ObservedAgent``), without every call site needing to branch.
    """
    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _format_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _format_number(value: Any) -> str:
    if value is None:
        return "\u2014"  # em dash
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value:,}"
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return html_escape(value)
    if as_float.is_integer():
        return f"{int(as_float):,}"
    return f"{as_float:,.2f}"


def _tone_class(tone: str) -> str:
    return f"observe-tone-{html_escape(tone)}"


def _render_badge(label: str, tone: str, *, extra_class: str = "") -> str:
    classes = f"observe-badge {_tone_class(tone)} {extra_class}".strip()
    return f'<span class="{classes}">{html_escape(label)}</span>'


def _bound_points(points: Sequence[Any], *, max_points: int = MAX_TREND_POINTS) -> list[Any]:
    """Downsample ``points`` so a chart never renders more than ``max_points``.

    Keeps the first and last point so the visible trend still spans the full
    requested range even after downsampling ("bounded agent trends", T053).
    """
    items = list(points)
    if len(items) <= max_points or max_points <= 1:
        return items
    step = (len(items) - 1) / (max_points - 1)
    bounded = [items[round(i * step)] for i in range(max_points)]
    bounded[-1] = items[-1]
    return bounded


# ---------------------------------------------------------------------------
# Source labels, refresh timestamps, and last-seen semantics (T052)
# ---------------------------------------------------------------------------


def render_source_label(source: Any, *, extra_class: str = "") -> str:
    """Render a small badge naming the telemetry source a value came from.

    ``source`` may be a plain string (a source id/kind) or any mapping/object
    exposing a ``source_kind`` and/or ``source_id`` attribute (e.g. a
    ``CoverageResult``). Every card/row that surfaces an aggregated value
    must show one of these so users can tell which source(s) contributed it.
    """
    if isinstance(source, str):
        label = source
    else:
        kind = _get(source, "source_kind")
        source_id = _get(source, "source_id")
        label = kind or source_id or "unknown source"
    classes = f"observe-source-label {extra_class}".strip()
    return f'<span class="{classes}">Source: {html_escape(label)}</span>'


def render_refreshed_at(value: Any, *, label: str = "Refreshed") -> str:
    """Render an explicit "Refreshed <timestamp>" marker for a card/table.

    Every metric card, table, and coverage row must show when its data was
    last refreshed. ``value`` may be a ``datetime``, an ISO-8601 string, or
    ``None`` (rendered as "not yet refreshed").
    """
    moment = _coerce_datetime(value)
    if moment is None:
        return (
            '<time class="observe-refreshed-at observe-refreshed-at-unknown">'
            f"{html_escape(label)}: not yet refreshed</time>"
        )
    iso = _format_iso(moment)
    return (
        f'<time class="observe-refreshed-at" datetime="{html_escape(iso)}">'
        f"{html_escape(label)}: {html_escape(iso)}</time>"
    )


def render_last_seen(value: Any) -> str:
    """Render a "Last seen" marker with an explicit non-lifecycle disclaimer.

    "Last seen" reflects only the most recent *observed telemetry*; it is
    explicitly not an agent lifecycle/registration status, so every use of
    it repeats that disclaimer (FR-034 / quickstart wording).
    """
    moment = _coerce_datetime(value)
    disclaimer = "Last seen reflects observed telemetry only, not agent lifecycle status."
    if moment is None:
        return (
            '<span class="observe-last-seen observe-last-seen-missing metric-missing">'
            f"Last seen: not reported"
            f'<span class="observe-hint"> ({html_escape(disclaimer)})</span></span>'
        )
    iso = _format_iso(moment)
    return (
        '<span class="observe-last-seen">'
        f'Last seen: <time datetime="{html_escape(iso)}">{html_escape(iso)}</time>'
        f'<span class="observe-hint"> ({html_escape(disclaimer)})</span></span>'
    )


def _render_maybe_missing(
    value: Any,
    *,
    formatter: Any = _format_number,
    missing_text: str = "Not reported",
    suffix: str = "",
) -> str:
    """Render ``value`` distinguishing a reported ``0`` from ``None``.

    A reported ``0`` renders as ``0`` (or ``0<suffix>``) tagged with
    ``metric-zero``; ``None`` (never reported) renders as *missing_text*
    tagged with ``metric-missing``. This distinction must hold everywhere a
    metric can legitimately be zero (failure counts, latency, tokens).
    """
    if value is None:
        return f'<span class="observe-metric metric-missing">{html_escape(missing_text)}</span>'
    text = f"{formatter(value)}{suffix}"
    zero = False
    try:
        zero = float(value) == 0
    except (TypeError, ValueError):
        zero = False
    marker = "metric-zero" if zero else "metric-value"
    return f'<span class="observe-metric {marker}">{html_escape(text)}</span>'


def _render_failure_rate(invocations: Any, failures: Any) -> str:
    if invocations is None or failures is None:
        return _render_maybe_missing(None)
    try:
        invocations_n = int(invocations)
        failures_n = int(failures)
    except (TypeError, ValueError):
        return _render_maybe_missing(None)
    if invocations_n <= 0:
        return _render_maybe_missing(None, missing_text="No invocations")
    rate = (failures_n / invocations_n) * 100
    return _render_maybe_missing(round(rate, 1), suffix="%")


def _render_token_totals(
    input_tokens: Any,
    output_tokens: Any,
    *,
    missing_text: str = "Not reported",
) -> str:
    """Render token totals with the required "observed usage" disclaimer.

    Token counts are observed usage signals, not billing data; every render
    of a token total must say so explicitly (FR-036).
    """
    input_html = _render_maybe_missing(input_tokens, missing_text=missing_text)
    output_html = _render_maybe_missing(output_tokens, missing_text=missing_text)
    return (
        '<span class="observe-token-totals">'
        f'<span class="observe-token-in">In: {input_html}</span> '
        f'<span class="observe-token-out">Out: {output_html}</span>'
        '<span class="observe-hint"> (observed usage, not billing data)</span>'
        "</span>"
    )


def _render_model_token_usage(entry: Any) -> str:
    classes = (
        ("Cache read", "cache_read_tokens"),
        ("Cache write", "cache_write_tokens"),
        ("Reasoning", "reasoning_tokens"),
    )
    class_html = "".join(
        '<span class="observe-token-class">'
        f'<span class="observe-token-class-label">{html_escape(label)}: </span>'
        f"{_render_maybe_missing(_get(entry, field))}"
        "</span>"
        for label, field in classes
    )
    partial = ""
    if _get(entry, "token_classes_partial", False):
        partial = (
            '<span class="observe-token-classes-partial">'
            "Partial class coverage"
            "</span>"
        )
    additional = _get(entry, "additional_token_classes", {}) or {}
    additional_html = "".join(
        '<span class="observe-token-class observe-token-class-additional">'
        f'<span class="observe-token-class-label">{html_escape(name)}: </span>'
        f"{_render_maybe_missing(value)}"
        "</span>"
        for name, value in additional.items()
    )
    truncated = ""
    if _get(entry, "additional_token_classes_truncated", False):
        truncated = (
            '<span class="observe-token-classes-truncated">'
            "Additional classes truncated"
            "</span>"
        )
    return (
        _render_token_totals(_get(entry, "input_tokens"), _get(entry, "output_tokens"))
        + '<span class="observe-token-classes">'
        + class_html
        + additional_html
        + partial
        + truncated
        + "</span>"
    )


# ---------------------------------------------------------------------------
# Chart rendering (T052 / FR-035)
# ---------------------------------------------------------------------------


def render_trend_chart(
    title: str,
    series: Sequence[Mapping[str, Any]],
    *,
    width: int = 600,
    height: int = 200,
    unit: str = "",
) -> str:
    """Render a responsive, accessible, dependency-free multi-series SVG chart.

    ``series`` is a sequence of mappings with:

    * ``label`` -- the series name shown in the legend and accessible table.
    * ``points`` -- a sequence of ``(x_label, value)`` pairs. ``value`` must
      be numeric; omit a point entirely to represent a gap rather than
      passing ``None`` (a numeric-only line keeps the SVG geometry simple).

    Series lines are always solid (never dashed): non-color distinction
    comes from a distinct marker glyph/shape per series plus a text legend,
    not from stroke patterns. Every point also carries an exact-value
    ``<title>`` tooltip, and a visually-hidden data table repeats the exact
    values for assistive technology and printed output.
    """
    padding = 28
    safe_series = [dict(s) for s in series]
    for entry in safe_series:
        entry["points"] = _bound_points(list(entry.get("points") or []))

    all_values = [
        float(value)
        for entry in safe_series
        for (_label, value) in entry["points"]
        if value is not None
    ]
    if not all_values:
        return (
            '<figure class="observe-chart observe-chart-empty">'
            f"<figcaption>{html_escape(title)}</figcaption>"
            '<p class="observe-empty">No data found for this chart.</p>'
            "</figure>"
        )

    min_val = min(all_values)
    max_val = max(all_values)
    if min_val == max_val:
        min_val -= 1
        max_val += 1
    plot_w = width - 2 * padding
    plot_h = height - 2 * padding

    def _x_for(index: int, count: int) -> float:
        if count <= 1:
            return padding + plot_w / 2
        return padding + (plot_w * index / (count - 1))

    def _y_for(value: float) -> float:
        ratio = (value - min_val) / (max_val - min_val)
        return padding + plot_h - (ratio * plot_h)

    defs_parts: list[str] = []
    body_parts: list[str] = []
    legend_parts: list[str] = []
    table_parts: list[str] = []

    # Subtle, decorative, aria-hidden horizontal gridlines.
    grid_lines = 4
    for i in range(grid_lines + 1):
        y = padding + (plot_h * i / grid_lines)
        body_parts.append(
            f'<line class="observe-chart-grid" x1="{padding}" y1="{y:.2f}" '
            f'x2="{width - padding}" y2="{y:.2f}" aria-hidden="true" />'
        )

    for series_index, entry in enumerate(safe_series):
        label = str(entry.get("label", f"Series {series_index + 1}"))
        points = entry["points"]
        glyph = _MARKER_GLYPHS[series_index % len(_MARKER_GLYPHS)]
        shape_name = _MARKER_SHAPE_NAMES[series_index % len(_MARKER_SHAPE_NAMES)]
        color_var = f"var(--observe-series-{(series_index % 4) + 1})"
        gradient_id = f"observe-gradient-{series_index}"

        coords: list[tuple[float, float]] = []
        for i, (_x_label, value) in enumerate(points):
            if value is None:
                continue
            coords.append((_x_for(i, len(points)), _y_for(float(value))))

        if coords:
            defs_parts.append(
                f'<linearGradient id="{gradient_id}" x1="0" y1="0" x2="0" y2="1">'
                f'<stop offset="0%" stop-color="{color_var}" stop-opacity="0.25" />'
                f'<stop offset="100%" stop-color="{color_var}" stop-opacity="0" />'
                "</linearGradient>"
            )
            poly_points = " ".join(f"{x:.2f},{y:.2f}" for x, y in coords)
            baseline_y = padding + plot_h
            area_points = (
                f"{coords[0][0]:.2f},{baseline_y:.2f} {poly_points} "
                f"{coords[-1][0]:.2f},{baseline_y:.2f}"
            )
            body_parts.append(
                f'<polygon class="observe-chart-area" points="{area_points}" '
                f'fill="url(#{gradient_id})" aria-hidden="true" />'
            )
            body_parts.append(
                f'<polyline class="observe-chart-line" points="{poly_points}" '
                f'fill="none" stroke="{color_var}" stroke-width="2" />'
            )

        for i, (x_label, value) in enumerate(points):
            if value is None:
                continue
            x, y = _x_for(i, len(points)), _y_for(float(value))
            tooltip = f"{label} \u2013 {html_escape(x_label)}: {_format_number(value)}{unit}"
            body_parts.append(
                '<g class="observe-chart-point">'
                f"<title>{tooltip}</title>"
                f'<text class="observe-chart-marker observe-chart-marker-{shape_name}" '
                f'x="{x:.2f}" y="{y:.2f}" fill="{color_var}" '
                'text-anchor="middle" dominant-baseline="central">'
                f"{glyph}</text></g>"
            )
            table_parts.append(
                f"<tr><th scope=\"row\">{html_escape(label)}</th>"
                f"<td>{html_escape(x_label)}</td><td>{_format_number(value)}{html_escape(unit)}</td></tr>"
            )

        legend_parts.append(
            '<li class="observe-chart-legend-item">'
            f'<span class="observe-chart-legend-marker observe-chart-legend-marker-{shape_name}" '
            f'style="color:{color_var}" aria-hidden="true">{glyph}</span> '
            f"{html_escape(label)}</li>"
        )

    svg = (
        f'<svg class="observe-chart-svg" viewBox="0 0 {width} {height}" '
        'preserveAspectRatio="xMidYMid meet" role="img" '
        f'aria-label="Trend chart: {html_escape(title)}">'
        f"<desc>Solid line per series; markers use distinct shapes ({', '.join(_MARKER_SHAPE_NAMES)}) "
        "so series remain distinguishable without color.</desc>"
        f"<defs>{''.join(defs_parts)}</defs>"
        f"{''.join(body_parts)}"
        "</svg>"
    )

    legend = f'<ul class="observe-chart-legend">{"".join(legend_parts)}</ul>' if legend_parts else ""
    accessible_table = (
        '<table class="observe-chart-data visually-hidden">'
        f"<caption>Exact values for {html_escape(title)}</caption>"
        "<thead><tr><th scope=\"col\">Series</th><th scope=\"col\">Point</th>"
        "<th scope=\"col\">Value</th></tr></thead>"
        f"<tbody>{''.join(table_parts)}</tbody></table>"
    )

    return (
        '<figure class="observe-chart">'
        f"<figcaption>{html_escape(title)}</figcaption>"
        f"{svg}{legend}{accessible_table}"
        "</figure>"
    )


# ---------------------------------------------------------------------------
# Navigation and filter bar (T050 / T051)
# ---------------------------------------------------------------------------


def render_observe_nav(active_view: str = "overview", *, cost_enabled: bool = False) -> str:
    """Render the Observe navigation as an accessible list of same-page links."""
    items = []
    views = OBSERVE_VIEWS + (("cost",) if cost_enabled else ())
    for view in views:
        label = "Cost" if view == "cost" else OBSERVE_VIEW_LABELS[view]
        current = ' aria-current="page"' if view == active_view else ""
        items.append(
            f'<li><a href="#{view}" data-observe-nav-link="{view}" class="observe-nav-link"'
            f'{current}>{html_escape(label)}</a></li>'
        )
    return (
        '<nav class="observe-nav" aria-label="Observe views">'
        f'<ul class="observe-nav-list">{"".join(items)}</ul>'
        "</nav>"
    )


def render_filter_bar(scope_label: Optional[str] = None) -> str:
    """Render the shared filter form (draft state) with an explicit Apply action.

    Every field is marked ``data-draft-filter`` so the behavior script can
    read the *draft* values only when the user explicitly submits the form;
    nothing here is auto-applied on change/input. Filters default to "All"
    (an empty value), and the date/time fields default (client-side) to the
    trailing 24 hours -- see :data:`_OBSERVE_SCRIPT`.
    """
    scope_html = (
        f'<p class="observe-scope"><span class="observe-hint">Scope:</span> '
        f"{html_escape(scope_label)}</p>"
        if scope_label
        else ""
    )
    return f"""
<form class="observe-filter-bar" id="observe-filter-form" aria-label="Observe filters">
  {scope_html}
  <div class="observe-filter-fields">
    <label for="observe-filter-foundry_resource_id">Foundry resource
      <input type="text" id="observe-filter-foundry_resource_id" name="foundry_resource_id"
             data-draft-filter="foundry_resource_id" placeholder="All" autocomplete="off" />
    </label>
    <label for="observe-filter-project_resource_id">Project
      <input type="text" id="observe-filter-project_resource_id" name="project_resource_id"
             data-draft-filter="project_resource_id" placeholder="All" autocomplete="off" />
    </label>
    <label for="observe-filter-agent_id">Agent
      <input type="text" id="observe-filter-agent_id" name="agent_id"
             data-draft-filter="agent_id" placeholder="All" autocomplete="off" />
    </label>
    <label for="observe-filter-model">Model
      <input type="text" id="observe-filter-model" name="model"
             data-draft-filter="model" placeholder="All" autocomplete="off" />
    </label>
    <label for="observe-filter-tool_name">Tool
      <input type="text" id="observe-filter-tool_name" name="tool_name"
             data-draft-filter="tool_name" placeholder="All" autocomplete="off" />
    </label>
    <label for="observe-filter-run_key">Run key
      <input type="text" id="observe-filter-run_key" name="run_key"
             data-draft-filter="run_key" placeholder="All" autocomplete="off" />
    </label>
    <label for="observe-filter-start">Start
      <input type="datetime-local" id="observe-filter-start" name="start"
             data-draft-filter="start" />
    </label>
    <label for="observe-filter-end">End
      <input type="datetime-local" id="observe-filter-end" name="end"
             data-draft-filter="end" />
    </label>
  </div>
  <div class="observe-filter-actions">
    <button type="submit" id="observe-apply-filters" class="observe-apply-button">Apply filters</button>
    <button type="button" id="observe-refresh-now" class="observe-refresh-button">Refresh now</button>
    <span id="observe-refresh-status" class="observe-refresh-status" role="status" aria-live="polite"></span>
  </div>
</form>
""".strip()


# ---------------------------------------------------------------------------
# Overview cards (T050)
# ---------------------------------------------------------------------------


def _render_metric_card(
    title: str,
    value_html: str,
    *,
    source: Any = None,
    refreshed_at: Any = None,
) -> str:
    source_html = render_source_label(source) if source is not None else ""
    refreshed_html = render_refreshed_at(refreshed_at) if refreshed_at is not None else ""
    return (
        '<div class="observe-card" role="group" '
        f'aria-label="{html_escape(title)}">'
        f'<h3 class="observe-card-title">{html_escape(title)}</h3>'
        f'<p class="observe-card-value">{value_html}</p>'
        f"{source_html}{refreshed_html}"
        "</div>"
    )


def render_overview_cards(
    metrics: Sequence[Mapping[str, Any]],
    *,
    diagnostics: Optional[Mapping[str, Any]] = None,
) -> str:
    """Render the Overview cards grid.

    Each entry in ``metrics`` is a mapping with ``title``, ``value``
    (rendered through the zero-vs-missing helper unless ``value_html`` is
    supplied directly), optional ``unit``, ``source``, and ``refreshed_at``.
    """
    banner = render_diagnostics_banner(diagnostics) if diagnostics is not None else ""
    if not metrics:
        return (
            f'{banner}<div class="observe-overview-cards observe-empty-state">'
            "<p class=\"observe-empty\">No data found for the selected filters.</p></div>"
        )
    cards = []
    for metric in metrics:
        title = str(metric.get("title", ""))
        if "value_html" in metric:
            value_html = metric["value_html"]
        else:
            value_html = _render_maybe_missing(metric.get("value"), suffix=str(metric.get("unit", "")))
        cards.append(
            _render_metric_card(
                title,
                value_html,
                source=metric.get("source"),
                refreshed_at=metric.get("refreshed_at"),
            )
        )
    return f'{banner}<div class="observe-overview-cards">{"".join(cards)}</div>'


# ---------------------------------------------------------------------------
# Agents table (T050)
# ---------------------------------------------------------------------------


def _render_source_kind_badge(source_kind: Any) -> str:
    kind = source_kind or "unknown"
    tone = {
        "foundry_hosted": "ok",
        "foundry_prompt": "ok",
        "external_registered": "warn",
        "external_unregistered": "warn",
        "copilot_studio": "warn",
        "unknown": "muted",
    }.get(kind, "muted")
    label = str(kind).replace("_", " ").title()
    return _render_badge(label, tone, extra_class="observe-source-kind-badge")


def _render_identity_availability(agent_id: Any) -> str:
    if agent_id:
        return _render_badge("Identity available", "ok", extra_class="observe-identity-badge")
    return _render_badge("Identity not reported", "muted", extra_class="observe-identity-badge")


def render_agents_table(
    agents: Sequence[Any],
    *,
    diagnostics: Optional[Mapping[str, Any]] = None,
) -> str:
    """Render the Agents table with zero-vs-missing metrics and source badges.

    Accepts a sequence of mappings or ``ObservedAgent``-shaped objects with
    ``agent_name``/``agent_id``, ``source_kind``, ``model``, ``last_seen``,
    ``invocations``, ``failures``, ``p95_latency_ms``, ``input_tokens``, and
    ``output_tokens``.
    """
    banner = render_diagnostics_banner(diagnostics) if diagnostics is not None else ""
    if not agents:
        return (
            f'{banner}<div class="observe-agents-view observe-empty-state">'
            '<p class="observe-empty">No data found for the selected filters.</p></div>'
        )
    rows = []
    for agent in agents:
        name = _get(agent, "agent_name") or _get(agent, "agent_id") or "Not reported"
        rows.append(
            "<tr>"
            f"<td>{html_escape(name)} {_render_source_kind_badge(_get(agent, 'source_kind'))} "
            f"{_render_identity_availability(_get(agent, 'agent_id'))}</td>"
            f"<td>{html_escape(_get(agent, 'source_id') or 'Not reported')}</td>"
            f"<td>{html_escape(_get(agent, 'model') or 'Not reported')}</td>"
            f"<td>{render_last_seen(_get(agent, 'last_seen'))}</td>"
            f"<td>{_render_maybe_missing(_get(agent, 'invocations'))}</td>"
            f"<td>{_render_failure_rate(_get(agent, 'invocations'), _get(agent, 'failures'))}</td>"
            f"<td>{_render_maybe_missing(_get(agent, 'p95_latency_ms'), suffix=' ms')}</td>"
            f"<td>{_render_token_totals(_get(agent, 'input_tokens'), _get(agent, 'output_tokens'))}</td>"
            "</tr>"
        )
    return f"""
{banner}
<table class="observe-agents-table" aria-label="Agents observed in the selected range">
  <caption class="visually-hidden">Agents observed in the selected range</caption>
  <thead>
    <tr>
      <th scope="col">Agent</th>
      <th scope="col">Source</th>
      <th scope="col">Model</th>
      <th scope="col">Last seen</th>
      <th scope="col">Invocations</th>
      <th scope="col">Failure rate</th>
      <th scope="col">p95 latency</th>
      <th scope="col">Tokens</th>
    </tr>
  </thead>
  <tbody>{"".join(rows)}</tbody>
</table>
""".strip()


# ---------------------------------------------------------------------------
# Cost allocation (spec 013 T021)
# ---------------------------------------------------------------------------


def _cost_label(value: Any) -> str:
    return str(value or "Not reported").replace("_", " ").strip().title()


def _render_cost_method_badge(value: Any) -> str:
    method = str(value or "unavailable")
    tone = {"metered": "info", "commitment": "warn"}.get(method, "muted")
    return _render_badge(
        _cost_label(value),
        tone,
        extra_class=f"observe-cost-method-{html_escape(method)}",
    )


def _render_cost_confidence_badge(value: Any) -> str:
    confidence = str(value or "unavailable")
    tone = {
        "high": "ok",
        "medium": "info",
        "low": "warn",
        "unavailable": "muted",
    }.get(confidence, "muted")
    return _render_badge(
        _cost_label(value),
        tone,
        extra_class=f"observe-cost-confidence-{html_escape(confidence)}",
    )


def _cost_amount(amount: Any, currency: Any) -> str:
    if amount is None:
        return '<span class="observe-metric metric-missing">Not reported</span>'
    try:
        is_zero = Decimal(str(amount)) == 0
    except (InvalidOperation, ValueError):
        is_zero = False
    zero = (
        ' <span class="observe-hint observe-cost-observed-zero">Observed zero</span>'
        if is_zero
        else ""
    )
    return (
        f'<span class="observe-cost-amount{" metric-zero" if is_zero else ""}">'
        f"{html_escape(amount)} {html_escape(currency or 'Not reported')}{zero}</span>"
    )


def _cost_declared_amount(amount: Any, currency: Any) -> str:
    if amount is None:
        return (
            '<span class="observe-metric metric-missing '
            'observe-cost-missing-total">Missing configured billed total</span>'
        )
    return _cost_amount(amount, currency)


def _cost_options(
    options: Sequence[Any],
    *,
    id_key: str,
    selected: Any,
    all_label: Optional[str] = None,
) -> str:
    rendered: list[str] = []
    if all_label is not None:
        selected_attr = " selected" if not selected else ""
        rendered.append(f'<option value=""{selected_attr}>{html_escape(all_label)}</option>')
    for option in options:
        value = option if isinstance(option, str) else _get(option, id_key)
        if value is None:
            continue
        label = value if isinstance(option, str) else (_get(option, "label") or value)
        selected_attr = " selected" if str(value) == str(selected) else ""
        rendered.append(
            f'<option value="{html_escape(value)}"{selected_attr}>{html_escape(label)}</option>'
        )
    return "".join(rendered)


def _cost_period_options(options: Sequence[Any], *, selected: Any) -> str:
    rendered: list[str] = []
    for option in options:
        value = option if isinstance(option, str) else _get(option, "id")
        if value is None:
            continue
        label = value if isinstance(option, str) else (_get(option, "label") or value)
        component_ids = None if isinstance(option, str) else _get(option, "component_ids")
        component_attr = ""
        if component_ids is not None:
            encoded_component_ids = ",".join(
                str(component_id) for component_id in component_ids
            )
            component_attr = (
                f' data-cost-component-ids="{html_escape(encoded_component_ids)}"'
            )
        selected_attr = " selected" if str(value) == str(selected) else ""
        rendered.append(
            f'<option value="{html_escape(value)}"{component_attr}'
            f"{selected_attr}>{html_escape(label)}</option>"
        )
    return "".join(rendered)


def render_cost_controls(
    cost: Any = None,
    *,
    period_options: Sequence[Any] = (),
    component_options: Sequence[Any] = (),
    agent_options: Sequence[Any] = (),
) -> str:
    """Render Cost-only selectors without reusing shared Observe filters."""
    period = _get(cost, "period", {}) or {}
    selected_period = _get(period, "id")
    selected_component = _get(cost, "component_filter")
    selected_breakdown = _get(cost, "breakdown") or "agents"
    selected_agent = _get(cost, "cost_agent_key")

    if not period_options and selected_period:
        period_options = ({"id": selected_period, "label": selected_period},)
    if not selected_period:
        selected_period = next(
            (
                value
                for option in period_options
                if (value := option if isinstance(option, str) else _get(option, "id"))
            ),
            None,
        )
    if not component_options:
        component_options = tuple(
            {
                "id": _get(component, "component_id"),
                "label": _get(component, "component_id"),
            }
            for component in (_get(cost, "components", ()) or ())
            if _get(component, "component_id")
        )
    if not agent_options:
        seen: set[str] = set()
        inferred_agents: list[dict[str, str]] = []
        for row in _get(cost, "rows", ()) or ():
            key = _get(row, "agent_key")
            if not key and _get(row, "consumer_kind") == "agent":
                key = _get(row, "consumer_key")
            if key and str(key) not in seen:
                seen.add(str(key))
                inferred_agents.append({"key": str(key), "label": str(key)})
        agent_options = tuple(inferred_agents)

    breakdown_options = "".join(
        f'<option value="{value}"{" selected" if value == selected_breakdown else ""}>'
        f"{html_escape(label)}</option>"
        for value, label in (("agents", "Agents"), ("tools", "Tools"), ("runs", "Runs"))
    )
    return f"""
<form class="observe-cost-filter-bar" id="observe-cost-filter-form" aria-label="Cost selectors">
  <p class="observe-hint">
    The configured cost period is authoritative. Shared Observe time and identity filters
    do not change these allocations.
  </p>
  <div class="observe-filter-fields observe-cost-filter-fields">
    <label for="observe-cost-period">Period
      <select id="observe-cost-period" name="cost_period_id"
              data-cost-filter="cost_period_id">
        {_cost_period_options(period_options, selected=selected_period)}
      </select>
    </label>
    <label for="observe-cost-component">Component
      <select id="observe-cost-component" name="cost_component_id"
              data-cost-filter="cost_component_id">
        {_cost_options(component_options, id_key="id", selected=selected_component, all_label="All components")}
      </select>
    </label>
    <label for="observe-cost-breakdown">Breakdown
      <select id="observe-cost-breakdown" name="cost_breakdown"
              data-cost-filter="cost_breakdown">{breakdown_options}</select>
    </label>
    <label for="observe-cost-agent">Agent
      <select id="observe-cost-agent" name="cost_agent_key"
              data-cost-filter="cost_agent_key">
        {_cost_options(agent_options, id_key="key", selected=selected_agent, all_label="All agents")}
      </select>
    </label>
  </div>
  <div class="observe-filter-actions">
    <button type="submit" id="observe-apply-cost-filters" class="observe-apply-button">
      Apply cost selectors
    </button>
  </div>
</form>
""".strip()


def _render_cost_usage_share(row: Any) -> str:
    numerator = _get(row, "usage_numerator")
    denominator = _get(row, "usage_denominator")
    if numerator is None or denominator is None:
        return (
            '<span class="observe-metric metric-missing">'
            "Observed usage: Not reported</span>"
        )
    unit = str(_get(row, "usage_unit") or "usage").replace("_", " ")
    return (
        '<span class="observe-cost-usage-share">'
        f"Observed usage: {html_escape(numerator)} / {html_escape(denominator)} "
        f"{html_escape(unit)}</span>"
    )


def _render_cost_period(cost: Any) -> str:
    period = _get(cost, "period", {}) or {}
    starts_at = _get(period, "starts_at") or "Not reported"
    ends_at = _get(period, "ends_at") or "Not reported"
    return (
        '<dl class="observe-cost-period">'
        f"<div><dt>Period</dt><dd>{html_escape(_get(period, 'id') or 'Not reported')}</dd></div>"
        f"<div><dt>Observation window</dt><dd>{html_escape(starts_at)} to {html_escape(ends_at)}</dd></div>"
        f"<div><dt>Calculated at</dt><dd>{html_escape(_get(cost, 'calculated_at') or 'Not reported')}</dd></div>"
        f"<div><dt>Latest observed</dt><dd>{html_escape(_get(cost, 'latest_observed_at') or 'Not reported')}</dd></div>"
        "</dl>"
    )


def _render_cost_subtotals(subtotals: Sequence[Any]) -> str:
    if not subtotals:
        return '<p class="observe-empty">No currency subtotals reported.</p>'
    rows = []
    for subtotal in subtotals:
        currency = _get(subtotal, "currency")
        precision = _get(subtotal, "currency_minor_units")
        rows.append(
            '<tr class="observe-cost-subtotal-row">'
            f"<td>{html_escape(currency or 'Not reported')}</td>"
            f"<td>{html_escape(precision if precision is not None else 'Not reported')}</td>"
            f"<td>{_cost_declared_amount(_get(subtotal, 'declared_total'), currency)}</td>"
            f"<td>{_cost_amount(_get(subtotal, 'attributed_amount'), currency)}</td>"
            f"<td>{_cost_amount(_get(subtotal, 'unattributed_amount'), currency)}</td>"
            f"<td>{_cost_amount(_get(subtotal, 'unallocated_amount'), currency)}</td>"
            "</tr>"
        )
    return f"""
<table class="observe-cost-subtotals-table" aria-label="Cost currency subtotals">
  <caption>Currency subtotals are separate and are never converted or added across currencies.</caption>
  <thead><tr>
    <th scope="col">Currency</th><th scope="col">Minor units</th>
    <th scope="col">Declared</th><th scope="col">Attributed</th>
    <th scope="col">Unattributed</th><th scope="col">Unallocated</th>
  </tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>
<ul class="observe-cost-precision-notes">
  {"".join(
      f"<li>Currency precision: {html_escape(_get(item, 'currency_minor_units'))} minor units "
      f"for {html_escape(_get(item, 'currency'))}</li>"
      for item in subtotals
  )}
</ul>
""".strip()


def _render_cost_components(components: Sequence[Any]) -> str:
    if not components:
        return '<p class="observe-empty">No configured component summaries reported.</p>'
    rows = []
    for component in components:
        currency = _get(component, "currency")
        boundary = _get(component, "billing_boundary", {}) or {}
        boundary_text = f"{_cost_label(_get(boundary, 'kind'))}: "
        boundary_text += str(_get(boundary, "label") or _get(boundary, "value") or "Not reported")
        if _get(boundary, "label") and _get(boundary, "value"):
            boundary_text += f" ({_get(boundary, 'value')})"
        applied_key = _get(component, "applied_key")
        preferred_key = _get(component, "preferred_key")
        fallback = bool(applied_key and preferred_key and applied_key != preferred_key)
        method = (
            f"{_cost_label(_get(component, 'allocation_model'))}; "
            f"Preferred key: {_cost_label(preferred_key)}; "
            f"Applied key: {_cost_label(applied_key)}; "
            f"Fallback: {'Yes' if fallback else 'No'}"
        )
        rows_shown = _get(component, "rows_shown")
        rows_total = _get(component, "rows_total")
        if rows_total is None:
            row_count = f"{rows_shown if rows_shown is not None else 'Not reported'} / total unknown"
        else:
            omitted = max(int(rows_total) - int(rows_shown or 0), 0)
            row_count = f"{rows_shown if rows_shown is not None else 'Not reported'} / {rows_total}"
            if omitted:
                row_count += f" ({omitted} omitted)"
        coverage_state = _cost_label(_get(component, "coverage_state"))
        coverage_reason = _get(component, "coverage_reason") or "No incomplete-coverage reason reported."
        next_action = _get(component, "next_action") or "No follow-up action required."
        rows.append(
            "<tr>"
            f"<td>{html_escape(_get(component, 'component_id') or 'Not reported')}<br />"
            f'<span class="observe-hint">{html_escape(_cost_label(_get(component, "component_type")))}</span></td>'
            f"<td>{html_escape(boundary_text)}</td>"
            f"<td>{html_escape(_get(component, 'billed_source') or 'Not reported')}</td>"
            f"<td>{_render_cost_method_badge(_get(component, 'allocation_model'))}<br />"
            f'<span class="observe-hint">{html_escape(method)}</span></td>'
            f"<td>{_cost_declared_amount(_get(component, 'declared_total'), currency)}</td>"
            f"<td>{_cost_amount(_get(component, 'attributed_amount'), currency)}</td>"
            f"<td>{_cost_amount(_get(component, 'unattributed_amount'), currency)}</td>"
            f"<td>{_cost_amount(_get(component, 'unallocated_amount'), currency)}</td>"
            f"<td>{_cost_amount(_get(component, 'omitted_allocated_amount'), currency)}</td>"
            f"<td>{html_escape(row_count)}</td>"
            f"<td><strong>Confidence:</strong> {_render_cost_confidence_badge(_get(component, 'confidence'))}<br />"
            f"<strong>Coverage:</strong> {html_escape(coverage_state)}<br />"
            f'<span class="observe-hint"><strong>Reason:</strong> {html_escape(coverage_reason)}<br />'
            f"<strong>Next action:</strong> {html_escape(next_action)}</span></td>"
            "</tr>"
        )
    return f"""
<table class="observe-cost-components-table" aria-label="Exact cost component reconciliation">
  <caption>Exact component summaries reconcile each declared billed pool independently.</caption>
  <thead><tr>
    <th scope="col">Component</th><th scope="col">Billing boundary</th>
    <th scope="col">Source</th><th scope="col">Method</th>
    <th scope="col">Declared</th><th scope="col">Attributed</th>
    <th scope="col">Unattributed</th><th scope="col">Unallocated</th>
    <th scope="col">Omitted allocated</th><th scope="col">Rows</th>
    <th scope="col">Confidence and coverage</th>
  </tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>
""".strip()


def _cost_breakdown_label(breakdown: Any) -> str:
    return {"agents": "Agent", "tools": "Tool", "runs": "Run"}.get(str(breakdown), "Consumer")


def _cost_consumer_label(row: Any, breakdown: Any) -> str:
    if _get(row, "consumer_kind") != "unattributed":
        identity = {
            "agents": _get(row, "agent_key"),
            "tools": _get(row, "tool_name"),
            "runs": _get(row, "run_key"),
        }.get(str(breakdown))
        return str(identity or _get(row, "consumer_key") or "Not reported")
    return {
        "agents": "Unattributed agent",
        "tools": "Unattributed tool",
        "runs": "Unattributed run",
    }.get(str(breakdown), "Unattributed")


def _cost_drilldown_href(cost: Any, row: Any, breakdown: str) -> str:
    period = _get(cost, "period", {}) or {}
    params = {
        "view": "cost",
        "cost_period_id": _get(period, "id"),
        "cost_component_id": _get(cost, "component_filter"),
        "cost_breakdown": breakdown,
        "cost_agent_key": _get(row, "agent_key") or _get(row, "consumer_key"),
    }
    return f"?{urlencode({key: value for key, value in params.items() if value is not None})}#cost"


def _render_cost_row_evidence(row: Any, cost: Any) -> str:
    period = _get(cost, "period", {}) or {}
    row_boundary = _get(row, "billing_boundary", {}) or {}
    boundary = (
        f"{_cost_label(_get(row_boundary, 'kind'))}: "
        f"{_get(row_boundary, 'label') or _get(row_boundary, 'value') or 'Not reported'}"
    )
    period_id = _get(row, "period_id") or _get(period, "id") or "Not reported"
    starts_at = _get(row, "period_starts_at") or _get(period, "starts_at") or "Not reported"
    ends_at = _get(row, "period_ends_at") or _get(period, "ends_at") or "Not reported"
    details = (
        ("Period", period_id),
        ("Observation window", f"{starts_at} to {ends_at}"),
        ("Billing boundary", boundary),
        ("Source resource", _get(row, "source_resource_id") or "Not reported"),
        ("Project resource", _get(row, "project_resource_id") or "Not reported"),
        ("Agent key", _get(row, "agent_key") or "Not reported"),
        ("Preferred key", _cost_label(_get(row, "preferred_key"))),
        ("Applied key", _cost_label(_get(row, "applied_key"))),
        ("Fallback", "Yes" if _get(row, "fallback_used") else "No"),
        (
            "Rounding adjustment",
            f"{_get(row, 'rounding_adjustment_minor_units')} minor unit"
            f"{'' if _get(row, 'rounding_adjustment_minor_units') == 1 else 's'}"
            if _get(row, "rounding_adjustment_minor_units") is not None
            else "Not reported",
        ),
        ("Confidence", _cost_label(_get(row, "confidence"))),
        ("Coverage", _cost_label(_get(row, "coverage_state"))),
        ("Coverage reason", _get(row, "coverage_reason") or "No incomplete-coverage reason reported."),
        ("Calculated at", _get(row, "calculated_at") or "Not reported"),
        ("Latest observed", _get(row, "latest_observed_at") or "Not reported"),
    )
    return '<dl class="observe-cost-row-evidence">' + "".join(
        f"<div><dt>{html_escape(label)}</dt><dd>{html_escape(value)}</dd></div>"
        for label, value in details
    ) + "</dl>"


def _render_cost_rows(rows: Sequence[Any], cost: Any) -> str:
    if not rows:
        return '<p class="observe-empty">No allocations reported for the selected cost selectors.</p>'
    breakdown = _get(cost, "breakdown") or "agents"
    consumer_heading = _cost_breakdown_label(breakdown)
    rendered = []
    for row in rows:
        consumer = _cost_consumer_label(row, breakdown)
        method = (
            f"{_cost_label(_get(row, 'allocation_model'))}; "
            f"Preferred key: {_cost_label(_get(row, 'preferred_key'))}; "
            f"Applied key: {_cost_label(_get(row, 'applied_key'))}; "
            f"Fallback: {'Yes' if _get(row, 'fallback_used') else 'No'}"
        )
        coverage = " — ".join(
            str(part)
            for part in (
                _cost_label(_get(row, "confidence")),
                _cost_label(_get(row, "coverage_state")),
                _get(row, "coverage_reason"),
            )
            if part
        )
        actions = ""
        if (
            breakdown == "agents"
            and _get(row, "consumer_kind") != "unattributed"
            and (_get(row, "agent_key") or _get(row, "consumer_key"))
        ):
            actions = (
                '<div class="observe-cost-drilldown-actions">'
                f'<a href="{html_escape(_cost_drilldown_href(cost, row, "tools"))}">View tools</a> '
                f'<a href="{html_escape(_cost_drilldown_href(cost, row, "runs"))}">View runs</a>'
                "</div>"
            )
        rendered.append(
            "<tr>"
            f"<td>{html_escape(consumer)}{actions}</td>"
            f"<td>{html_escape(_get(row, 'component_id') or 'Not reported')}</td>"
            f"<td>{_cost_amount(_get(row, 'amount'), _get(row, 'currency'))}</td>"
            f"<td>{_render_cost_usage_share(row)}</td>"
            f"<td>{_render_cost_method_badge(_get(row, 'allocation_model'))}<br />"
            f'<span class="observe-hint">{html_escape(method)}</span></td>'
            f"<td>{html_escape(_get(row, 'billed_source') or 'Not reported')}</td>"
            f"<td>{_render_cost_confidence_badge(_get(row, 'confidence'))}<br />"
            f'<span class="observe-hint">{html_escape(coverage)}</span></td>'
            f"<td>{_render_cost_row_evidence(row, cost)}</td>"
            "</tr>"
        )
    return f"""
<table class="observe-cost-allocations-table" aria-label="{html_escape(consumer_heading)} cost allocations">
  <caption>{html_escape(consumer_heading)} allocations from declared billed totals and observed usage.</caption>
  <thead><tr>
    <th scope="col">{html_escape(consumer_heading)}</th><th scope="col">Component</th>
    <th scope="col">Amount</th><th scope="col">Usage share</th>
    <th scope="col">Method</th><th scope="col">Source</th>
    <th scope="col">Confidence and coverage</th><th scope="col">Provenance and evidence</th>
  </tr></thead>
  <tbody>{"".join(rendered)}</tbody>
</table>
""".strip()


def _render_cost_coverage(coverage: Sequence[Any]) -> str:
    if not coverage:
        return ""
    rows = []
    for item in coverage:
        state = _get(item, "state") or "unknown"
        state_text = _cost_label(state)
        if state == "not_configured":
            state_text = "Missing configured billed total"
        rows.append(
            "<tr>"
            f"<td>{html_escape(_get(item, 'source_id') or 'Not reported')}</td>"
            f"<td>{html_escape(_cost_label(_get(item, 'dimension')))}</td>"
            f"<td>{html_escape(_get(item, 'allocation_key') or 'Not reported')}</td>"
            f"<td>{html_escape(state_text)}</td>"
            f"<td>{html_escape(_get(item, 'reason') or 'No reason reported.')}</td>"
            f"<td>{html_escape(_get(item, 'next_action') or 'No follow-up action reported.')}</td>"
            "</tr>"
        )
    return f"""
<section class="observe-cost-coverage" aria-labelledby="observe-cost-coverage-title">
  <h3 id="observe-cost-coverage-title">Cost attribution coverage</h3>
  <table aria-label="Cost attribution coverage">
    <thead><tr><th scope="col">Source or component</th><th scope="col">Dimension</th>
      <th scope="col">Allocation key</th><th scope="col">State</th>
      <th scope="col">Reason</th><th scope="col">Next action</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table>
</section>
""".strip()


def _render_cost_partial_failures(partial_failures: Sequence[Any]) -> str:
    if not partial_failures:
        return ""
    return (
        '<section class="observe-cost-partial-failures" '
        'aria-labelledby="observe-cost-partial-failures-title">'
        '<h3 id="observe-cost-partial-failures-title">Partial source failures</h3>'
        "<p>Readable components remain visible; failed sources may make allocations incomplete.</p>"
        "<ul>"
        + "".join(
            "<li>"
            f"<strong>{html_escape(_get(item, 'source_id') or 'Not reported')}</strong> "
            f"({html_escape(_cost_label(_get(item, 'status')))}) — "
            f"{html_escape(_get(item, 'reason') or 'No reason reported.')} "
            f"<strong>Next action:</strong> "
            f"{html_escape(_get(item, 'next_action') or 'No follow-up action reported.')}"
            "</li>"
            for item in partial_failures
        )
        + "</ul></section>"
    )


def _render_cost_bounds(bounds: Any) -> str:
    if bounds is None:
        return ""
    shown = _get(bounds, "rows_shown")
    total = _get(bounds, "rows_total_in_scope")
    if total is None:
        text = f"Showing {shown if shown is not None else 'an unknown number of'} rows; total unknown."
    else:
        text = f"Showing {shown if shown is not None else 'an unknown number of'} of {total} rows in scope"
        text += "; results are truncated." if _get(bounds, "truncated") else "."
    return f'<p class="observe-hint observe-cost-bounds-notice">{html_escape(text)}</p>'


def render_cost_view(
    cost: Any,
    *,
    diagnostics: Optional[Mapping[str, Any]] = None,
    coverage: Sequence[Any] = (),
    partial_failures: Sequence[Any] = (),
    bounds: Any = None,
) -> str:
    """Render exact, currency-safe billed-cost allocations and reconciliation."""
    banner = render_diagnostics_banner(diagnostics or {})
    supplemental = (
        f"{_render_cost_bounds(bounds)}"
        f"{_render_cost_coverage(coverage)}"
        f"{_render_cost_partial_failures(partial_failures)}"
    )
    if not cost:
        return (
            f'{banner}<div class="observe-cost-view observe-empty-state">'
            '<p class="observe-empty">No cost allocation data reported.</p>'
            f"{supplemental}"
            f'<p class="observe-cost-breakdown-warning">{html_escape(COST_BREAKDOWN_WARNING)}</p>'
            f'<p class="observe-cost-disclaimer">{html_escape(COST_DISCLAIMER)}</p></div>'
        )
    return (
        f'<div class="observe-cost-view">{banner}'
        f"{_render_cost_period(cost)}"
        f'<p class="observe-cost-breakdown-warning">{html_escape(COST_BREAKDOWN_WARNING)}</p>'
        f"<h3>Currency subtotals</h3>{_render_cost_subtotals(_get(cost, 'currency_subtotals', ()) or ())}"
        f"<h3>Component reconciliation</h3>{_render_cost_components(_get(cost, 'components', ()) or ())}"
        f"<h3>{html_escape(_cost_label(_get(cost, 'breakdown')))} allocation</h3>"
        f"{_render_cost_rows(_get(cost, 'rows', ()) or (), cost)}"
        f"{supplemental}"
        f'<p class="observe-cost-disclaimer">{html_escape(COST_DISCLAIMER)}</p>'
        "</div>"
    )


# ---------------------------------------------------------------------------
# Tools and runs tables
# ---------------------------------------------------------------------------


def _render_bounds_notice(bounds: Any, *, rows_shown: int) -> str:
    """Render bounded-result scope without inventing an unavailable total."""
    total = _get(bounds, "rows_total_in_scope") if bounds is not None else None
    if total is None:
        text = f"Showing {rows_shown} rows; total unknown."
    else:
        text = f"Showing {rows_shown} of {total} rows in scope."
    return f'<p class="observe-hint observe-bounds-notice">{html_escape(text)}</p>'


def render_tools_table(
    tools: Sequence[Any],
    *,
    diagnostics: Optional[Mapping[str, Any]] = None,
    bounds: Any = None,
) -> str:
    """Render tool activity without attributing unavailable latency as zero."""
    banner = render_diagnostics_banner(diagnostics) if diagnostics is not None else ""
    notice = _render_bounds_notice(bounds, rows_shown=len(tools))
    if not tools:
        return (
            f'{banner}{notice}<div class="observe-tools-view observe-empty-state">'
            '<p class="observe-empty">No tool activity was found for the selected filters. '
            'Tool attribution may not be reported; check Telemetry coverage for details.</p></div>'
        )
    rows = []
    for tool in tools:
        agent = _get(tool, "agent_name") or _get(tool, "agent_id") or _get(tool, "agent_key") or "Not reported"
        rows.append(
            "<tr>"
            f"<td>{html_escape(_get(tool, 'tool_name') or 'Not reported')}</td>"
            f"<td>{html_escape(agent)}</td>"
            f"<td>{html_escape(_get(tool, 'source_id') or 'Not reported')}</td>"
            f"<td>{_render_source_kind_badge(_get(tool, 'source_kind'))}</td>"
            f"<td>{render_last_seen(_get(tool, 'last_seen'))}</td>"
            f"<td>{_render_maybe_missing(_get(tool, 'invocations'))}</td>"
            f"<td>{_render_maybe_missing(_get(tool, 'failures'))}</td>"
            f"<td>{_render_maybe_missing(_get(tool, 'p95_latency_ms'), suffix=' ms', missing_text='Not measured')}</td>"
            "</tr>"
        )
    return f"""
{banner}
{notice}
<table class="observe-tools-table" aria-label="Tools observed in the selected range">
  <caption class="visually-hidden">Tools observed in the selected range</caption>
  <thead>
    <tr>
      <th scope="col">Tool</th>
      <th scope="col">Agent</th>
      <th scope="col">Source</th>
      <th scope="col">Runtime</th>
      <th scope="col">Last seen</th>
      <th scope="col">Invocations</th>
      <th scope="col">Failures</th>
      <th scope="col">p95 latency</th>
    </tr>
  </thead>
  <tbody>{"".join(rows)}</tbody>
</table>
""".strip()


def render_runs_table(
    runs: Sequence[Any],
    *,
    diagnostics: Optional[Mapping[str, Any]] = None,
    bounds: Any = None,
) -> str:
    """Render range-scoped correlated executions and their observed token totals."""
    banner = render_diagnostics_banner(diagnostics) if diagnostics is not None else ""
    notice = _render_bounds_notice(bounds, rows_shown=len(runs))
    if not runs:
        return (
            f'{banner}{notice}<div class="observe-runs-view observe-empty-state">'
            '<p class="observe-empty">No runs could be correlated for the selected filters. '
            'Run correlation may not be reported; check Telemetry coverage for details.</p></div>'
        )
    rows = []
    for run in runs:
        agent = _get(run, "agent_name") or _get(run, "agent_id") or _get(run, "agent_key") or "Not reported"
        started_at = _coerce_datetime(_get(run, "started_at"))
        rows.append(
            "<tr>"
            f"<td>{html_escape(_get(run, 'run_key') or 'Not reported')}</td>"
            f"<td>{html_escape(_get(run, 'run_key_kind') or 'Not reported')}</td>"
            f"<td>{html_escape(agent)}</td>"
            f"<td>{html_escape(_get(run, 'source_id') or 'Not reported')}</td>"
            f"<td>{_render_source_kind_badge(_get(run, 'source_kind'))}</td>"
            f"<td>{html_escape(_format_iso(started_at) if started_at else 'Not reported')}</td>"
            f"<td>{_render_maybe_missing(_get(run, 'duration_ms'), suffix=' ms')}</td>"
            f"<td>{html_escape(_get(run, 'status') or 'Not reported')}</td>"
            f"<td>{_render_maybe_missing(_get(run, 'turns'))}</td>"
            f"<td>{_render_maybe_missing(_get(run, 'tool_invocations'))}</td>"
            f"<td>{_render_token_totals(_get(run, 'input_tokens'), _get(run, 'output_tokens'), missing_text='Not available')}</td>"
            "</tr>"
        )
    return f"""
{banner}
{notice}
<p class="observe-hint">Start, duration, and turns describe activity within the selected range.</p>
<table class="observe-runs-table" aria-label="Runs observed in the selected range">
  <caption class="visually-hidden">Runs observed in the selected range; start, duration, and turns are range-scoped.</caption>
  <thead>
    <tr>
      <th scope="col">Run key</th>
      <th scope="col">Correlation</th>
      <th scope="col">Agent</th>
      <th scope="col">Source</th>
      <th scope="col">Runtime</th>
      <th scope="col">Started in range</th>
      <th scope="col">Duration in range</th>
      <th scope="col">Status</th>
      <th scope="col">Turns in range</th>
      <th scope="col">Tool invocations</th>
      <th scope="col">Tokens</th>
    </tr>
  </thead>
  <tbody>{"".join(rows)}</tbody>
</table>
""".strip()


# ---------------------------------------------------------------------------
# Models / usage table (T050)
# ---------------------------------------------------------------------------


def render_models_usage_table(
    usage: Sequence[Any],
    *,
    diagnostics: Optional[Mapping[str, Any]] = None,
) -> str:
    """Render the Models/usage table with the required observed-usage wording.

    Accepts ``ModelUsage``-shaped objects/mappings with ``model``,
    ``deployment``, ``requests``, ``failures``, ``p95_latency_ms``,
    ``input_tokens``, ``output_tokens``, and ``last_seen``.
    """
    banner = render_diagnostics_banner(diagnostics) if diagnostics is not None else ""
    if not usage:
        return (
            f'{banner}<div class="observe-usage-view observe-empty-state">'
            '<p class="observe-empty">No data found for the selected filters.</p></div>'
        )
    rows = []
    for entry in usage:
        model = _get(entry, "model") or _get(entry, "deployment") or "Not reported"
        rows.append(
            "<tr>"
            f"<td>{html_escape(model)}</td>"
            f"<td>{html_escape(_get(entry, 'deployment') or 'Not reported')}</td>"
            f"<td>{_render_maybe_missing(_get(entry, 'requests'))}</td>"
            f"<td>{_render_failure_rate(_get(entry, 'requests'), _get(entry, 'failures'))}</td>"
            f"<td>{_render_maybe_missing(_get(entry, 'p95_latency_ms'), suffix=' ms')}</td>"
            f"<td>{_render_model_token_usage(entry)}</td>"
            f"<td>{render_last_seen(_get(entry, 'last_seen'))}</td>"
            "</tr>"
        )
    return f"""
{banner}
<table class="observe-usage-table" aria-label="Model usage observed in the selected range">
  <caption class="visually-hidden">
    Model usage observed in the selected range. Token counts are observed usage, not billing data.
  </caption>
  <thead>
    <tr>
      <th scope="col">Model</th>
      <th scope="col">Deployment</th>
      <th scope="col">Requests</th>
      <th scope="col">Failure rate</th>
      <th scope="col">p95 latency</th>
      <th scope="col">Tokens</th>
      <th scope="col">Last seen</th>
    </tr>
  </thead>
  <tbody>{"".join(rows)}</tbody>
</table>
""".strip()


# ---------------------------------------------------------------------------
# Coverage / troubleshooting (T058 / T062)
# ---------------------------------------------------------------------------


def render_diagnostics_banner(diagnostics: Mapping[str, Any]) -> str:
    """Render the query diagnostics banner (duration, source counts, cache, refresh).

    Renders a "partial results" notice when any source failed or was
    partial, but never as a substitute for the per-row coverage detail --
    it is purely a summary banner (T061 exposes the underlying counts;
    T062 renders them here alongside the row-level detail).
    """
    if not diagnostics:
        return ""
    source_count = _get(diagnostics, "source_count")
    successful = _get(diagnostics, "successful_sources")
    partial = _get(diagnostics, "partial_sources")
    failed = _get(diagnostics, "failed_sources")
    duration_ms = _get(diagnostics, "duration_ms")
    cache_status = _get(diagnostics, "cache_status")
    refreshed_at = _get(diagnostics, "completed_at")

    is_partial = bool((partial or 0) > 0 or (failed or 0) > 0)
    notice = (
        '<p class="observe-partial-notice" role="status">'
        "Partial results: some telemetry sources did not fully respond. "
        "Data from every source that did respond is still shown below."
        "</p>"
        if is_partial
        else ""
    )
    return f"""
<div class="observe-diagnostics-banner">
  {notice}
  <dl class="observe-diagnostics-list">
    <div><dt>Sources queried</dt><dd>{_render_maybe_missing(source_count)}</dd></div>
    <div><dt>Successful</dt><dd>{_render_maybe_missing(successful)}</dd></div>
    <div><dt>Partial</dt><dd>{_render_maybe_missing(partial)}</dd></div>
    <div><dt>Failed</dt><dd>{_render_maybe_missing(failed)}</dd></div>
    <div><dt>Query duration</dt><dd>{_render_maybe_missing(duration_ms, suffix=' ms')}</dd></div>
    <div><dt>Cache</dt><dd>{html_escape(cache_status or 'Not reported')}</dd></div>
  </dl>
  {render_refreshed_at(refreshed_at)}
</div>
""".strip()


def render_coverage_table(coverage: Sequence[Any]) -> str:
    """Render per-source/per-dimension coverage and troubleshooting detail.

    Every row is rendered independently regardless of its own or any other
    row's ``state`` -- a failed or protected row is never allowed to hide a
    row that is ``available`` (FR-046..FR-050, T062).
    """
    if not coverage:
        return '<p class="observe-empty">No coverage information reported.</p>'
    rows = []
    for entry in coverage:
        state = _get(entry, "state", "error")
        copy = COVERAGE_STATE_LABELS.get(state, COVERAGE_STATE_LABELS["error"])
        dimension = _get(entry, "dimension")
        dimension_label = COVERAGE_DIMENSION_LABELS.get(dimension, str(dimension or "Unknown dimension"))
        rows.append(
            "<tr>"
            f"<td>{html_escape(_get(entry, 'source_id') or 'Not reported')}</td>"
            f"<td>{html_escape(dimension_label)}</td>"
            f"<td>{_render_badge(copy['label'], copy['tone'], extra_class=f'observe-coverage-state-{html_escape(state)}')}</td>"
            f"<td>{html_escape(_get(entry, 'reason') or 'Not reported')}</td>"
            f"<td>{html_escape(_get(entry, 'next_action') or 'Not reported')}</td>"
            f"<td>{render_refreshed_at(_get(entry, 'refreshed_at'))}</td>"
            "</tr>"
        )
    return f"""
<table class="observe-coverage-table" aria-label="Telemetry coverage and troubleshooting detail">
  <caption class="visually-hidden">Telemetry coverage and troubleshooting detail</caption>
  <thead>
    <tr>
      <th scope="col">Source</th>
      <th scope="col">Dimension</th>
      <th scope="col">State</th>
      <th scope="col">Reason</th>
      <th scope="col">Next action</th>
      <th scope="col">Refreshed</th>
    </tr>
  </thead>
  <tbody>{"".join(rows)}</tbody>
</table>
""".strip()


def render_coverage_view(
    coverage: Sequence[Any],
    diagnostics: Optional[Mapping[str, Any]] = None,
) -> str:
    """Render the full "Telemetry coverage" view (T062).

    Combines the diagnostics/partial-results banner with the per-row
    coverage table. Coverage rows are always rendered in full regardless of
    any individual source's state, so a failed or partial source never
    hides evidence collected successfully from other sources.
    """
    banner = render_diagnostics_banner(diagnostics or {})
    table = render_coverage_table(coverage)
    return f'<div class="observe-coverage-view">{banner}{table}</div>'


# ---------------------------------------------------------------------------
# Portal links (T053)
# ---------------------------------------------------------------------------


def build_azure_resource_portal_url(resource_id: str) -> str:
    """Build a generic Azure portal "overview" link for an ARM resource id.

    Uses the same well-documented ``#resource<id>/overview`` deep-link
    pattern already used by ``agentops.agent.cockpit``.
    """
    return f"https://portal.azure.com/#resource{resource_id}/overview"


def render_portal_links(links: Mapping[str, str]) -> str:
    """Render best-effort labeled links to Foundry/Azure Monitor portal targets.

    ``links`` maps a portal-target key (e.g. ``"foundry_resource"``,
    ``"azure_monitor_transaction"``) to an absolute URL. Documented keys use
    a friendly, pre-defined label (:data:`_KNOWN_PORTAL_LABELS`); any other
    key still renders, with a best-effort label derived from the key name,
    so an undocumented portal target degrades gracefully instead of being
    dropped.
    """
    if not links:
        return ""
    items = []
    for key, url in links.items():
        label = _KNOWN_PORTAL_LABELS.get(key, key.replace("_", " ").strip().title())
        items.append(
            f'<li><a href="{html_escape(url)}" target="_blank" rel="noopener noreferrer" '
            f'data-observe-portal-link="{html_escape(key)}">{html_escape(label)}'
            '<span class="visually-hidden"> (opens in a new tab)</span></a></li>'
        )
    return f'<ul class="observe-portal-links">{"".join(items)}</ul>'


# ---------------------------------------------------------------------------
# Agent detail shell (T050 / T053)
# ---------------------------------------------------------------------------


def render_agent_detail_shell(
    agent: Any,
    *,
    trends: Sequence[Mapping[str, Any]] = (),
    portal_links: Optional[Mapping[str, str]] = None,
) -> str:
    """Render the agent-detail shell: identity, bounded trends, portal links."""
    name = _get(agent, "agent_name") or _get(agent, "agent_id") or "Unknown agent"
    charts = "".join(
        render_trend_chart(str(t.get("title", "")), t.get("series", []), unit=str(t.get("unit", "")))
        for t in trends
    )
    links_html = render_portal_links(portal_links or {})
    return f"""
<section class="observe-agent-detail" aria-label="Agent detail: {html_escape(name)}">
  <h2>{html_escape(name)}</h2>
  <p>{_render_source_kind_badge(_get(agent, 'source_kind'))} {_render_identity_availability(_get(agent, 'agent_id'))}</p>
  <p>{render_last_seen(_get(agent, 'last_seen'))}</p>
  <div class="observe-agent-detail-trends">{charts}</div>
  {links_html}
</section>
""".strip()


# ---------------------------------------------------------------------------
# Trace detail shell (T054)
# ---------------------------------------------------------------------------


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    import json

    try:
        return json.dumps(value, indent=2, default=str)
    except TypeError:
        return str(value)


def render_trace_detail_shell(
    trace_id: str,
    *,
    source_resource_id: Optional[str] = None,
    content: Optional[Any] = None,
) -> str:
    """Render the trace-detail shell with explicit protected-content loading.

    When ``content`` is ``None`` (nothing has been fetched yet, or the user
    has not clicked to load it), only an explicit "Load protected content"
    button is rendered -- protected content is never fetched automatically.

    ``source_resource_id`` identifies the Foundry/Azure Monitor resource the
    trace was observed on. Per ``contracts/observe-api.openapi.yaml``, the
    ``POST /api/observe/trace-content`` request body requires both
    ``source_resource_id`` and ``trace_id``; when it is supplied here it is
    rendered as a ``data-observe-source-resource-id`` attribute on the load
    button so the click handler can include it in that request. When it is
    omitted the attribute is not rendered at all, and the click handler
    refuses to issue an (invalid, contract-violating) request that is
    missing it.

    When ``content`` is supplied (a ``GenerativeAIContent``-shaped object or
    mapping), its ``protection_state`` selects exactly one of three
    mutually-exclusive renderings:

    * ``"available"`` -- the reported fields are shown in full.
    * ``"protected_or_unavailable"`` -- an explicit statement that no
      unprotected/legacy fallback is read; no field content is shown.
    * ``"not_configured"`` -- an explicit statement that delegated
      authorization is not configured for this content; no field content is
      shown.

    Any other/unknown ``protection_state`` is treated the same as
    ``"protected_or_unavailable"`` (fail closed: never show content unless
    the backend explicitly reports ``"available"``).
    """
    header = f'<h2>Trace {html_escape(trace_id)}</h2>'
    if content is None:
        source_attr = (
            f' data-observe-source-resource-id="{html_escape(source_resource_id)}"'
            if source_resource_id
            else ""
        )
        return f"""
<section class="observe-trace-detail" aria-label="Trace detail: {html_escape(trace_id)}"
         data-trace-id="{html_escape(trace_id)}">
  {header}
  <p class="observe-protected-notice">
    Generative AI content (inputs, outputs, system instructions, tool content, and evaluation
    explanations) is protected and is never loaded automatically.
  </p>
  <button type="button" id="observe-load-protected-content" class="observe-load-protected-button"
          data-observe-load-protected="{html_escape(trace_id)}"{source_attr}>
    Load protected content
  </button>
</section>
""".strip()

    state = _get(content, "protection_state", "protected_or_unavailable")
    copy = PROTECTION_STATE_LABELS.get(state, PROTECTION_STATE_LABELS["protected_or_unavailable"])
    badge = _render_badge(copy["label"], copy["tone"], extra_class="observe-protection-state-badge")

    if state == "not_configured":
        body = (
            '<p class="observe-protected-message">'
            "Delegated per-user authorization for protected content is not configured for this "
            "resource, so no protected fields can be shown here."
            "</p>"
        )
    elif state != "available":
        body = (
            '<p class="observe-protected-message">'
            "This content is protected or unavailable to you. No unprotected or legacy fallback "
            "content is read or shown."
            "</p>"
        )
    else:
        fields = (
            ("Input messages", _get(content, "input_messages")),
            ("Output messages", _get(content, "output_messages")),
            ("System instructions", _get(content, "system_instructions")),
            ("Tool content", _get(content, "tool_content")),
            ("Evaluation explanation", _get(content, "evaluation_explanation")),
        )
        blocks = []
        for label, value in fields:
            if value is None:
                blocks.append(
                    f'<div class="observe-protected-field">'
                    f"<h3>{html_escape(label)}</h3>"
                    '<p class="observe-empty metric-missing">Not reported</p></div>'
                )
            else:
                blocks.append(
                    f'<div class="observe-protected-field">'
                    f"<h3>{html_escape(label)}</h3>"
                    f"<pre class=\"observe-protected-content\">{html_escape(_stringify(value))}</pre></div>"
                )
        body = "".join(blocks)

    return f"""
<section class="observe-trace-detail" aria-label="Trace detail: {html_escape(trace_id)}"
         data-trace-id="{html_escape(trace_id)}">
  {header}
  <p>{badge}</p>
  {body}
</section>
""".strip()


# ---------------------------------------------------------------------------
# Styles (T052: responsive light/dark, non-color distinction)
# ---------------------------------------------------------------------------

_OBSERVE_STYLES = """
:root {
  --observe-bg: #ffffff;
  --observe-fg: #14181f;
  --observe-muted: #5b6270;
  --observe-border: #d8dce3;
  --observe-card-bg: #f6f7fa;
  --observe-accent: #2f6fed;
  --observe-ok: #1a7f37;
  --observe-warn: #9a6700;
  --observe-crit: #cf222e;
  --observe-series-1: #2f6fed;
  --observe-series-2: #9a6700;
  --observe-series-3: #1a7f37;
  --observe-series-4: #8250df;
}

@media (prefers-color-scheme: dark) {
  :root {
    --observe-bg: #0d1117;
    --observe-fg: #e6edf3;
    --observe-muted: #9198a1;
    --observe-border: #30363d;
    --observe-card-bg: #161b22;
    --observe-accent: #6ea8fe;
    --observe-ok: #3fb950;
    --observe-warn: #d29922;
    --observe-crit: #f85149;
    --observe-series-1: #6ea8fe;
    --observe-series-2: #d29922;
    --observe-series-3: #3fb950;
    --observe-series-4: #bc8cff;
  }
}

.observe-root {
  background: var(--observe-bg);
  color: var(--observe-fg);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
}

.visually-hidden {
  position: absolute !important;
  width: 1px; height: 1px;
  padding: 0; margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.observe-nav-list { display: flex; gap: 1rem; list-style: none; padding: 0; }
.observe-nav-link[aria-current="page"] { font-weight: 700; text-decoration: underline; }

.observe-filter-bar { border: 1px solid var(--observe-border); border-radius: 8px; padding: 1rem; }
.observe-filter-fields { display: flex; flex-wrap: wrap; gap: 0.75rem; }
.observe-filter-fields label { display: flex; flex-direction: column; font-size: 0.85rem; gap: 0.25rem; }
.observe-filter-actions { display: flex; align-items: center; gap: 0.75rem; margin-top: 0.75rem; }
.observe-cost-filter-bar { border: 1px solid var(--observe-border); border-radius: 8px; padding: 1rem; margin-bottom: 1rem; }
.observe-cost-period, .observe-cost-precision-notes { display: flex; flex-wrap: wrap; gap: 0.75rem 1.5rem; }
.observe-cost-period div { display: flex; gap: 0.35rem; }
.observe-cost-period dt { font-weight: 600; }
.observe-cost-period dd { margin: 0; }
.observe-cost-disclaimer { border-left: 4px solid var(--observe-warn); padding: 0.75rem; color: var(--observe-muted); }
.observe-cost-view table { margin-bottom: 1rem; }

.observe-overview-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem; }
.observe-card { background: var(--observe-card-bg); border: 1px solid var(--observe-border); border-radius: 8px; padding: 1rem; }
.observe-card-value { font-size: 1.5rem; font-weight: 600; }

table { border-collapse: collapse; width: 100%; }
th, td { border-bottom: 1px solid var(--observe-border); padding: 0.5rem; text-align: left; }

.observe-badge { border-radius: 999px; padding: 0.15rem 0.6rem; font-size: 0.8rem; border: 1px solid currentColor; }
.observe-tone-ok { color: var(--observe-ok); }
.observe-tone-warn { color: var(--observe-warn); }
.observe-tone-crit { color: var(--observe-crit); }
.observe-tone-muted { color: var(--observe-muted); }

.metric-missing { color: var(--observe-muted); font-style: italic; }
.metric-zero { color: var(--observe-fg); }

.observe-chart-svg { width: 100%; height: auto; display: block; }
.observe-chart-grid { stroke: var(--observe-border); stroke-opacity: 0.5; }
.observe-chart-marker { font-size: 10px; }
.observe-chart-legend { display: flex; flex-wrap: wrap; gap: 0.75rem; list-style: none; padding: 0; font-size: 0.85rem; }

.observe-partial-notice { color: var(--observe-warn); font-weight: 600; }
.observe-protected-notice { color: var(--observe-muted); }
.observe-empty { color: var(--observe-muted); }

.observe-hint { color: var(--observe-muted); font-size: 0.8rem; }
""".strip()


# ---------------------------------------------------------------------------
# Behavior script (T051 / T053 / T054)
# ---------------------------------------------------------------------------
#
# Safety guarantee enforced by this script (and pinned by tests):
#   * Only OBSERVE_FILTER_QUERY_KEYS (foundry_resource_id, project_resource_id,
#     agent_id, model, tool_name, run_key, start, end) plus the active `view`
#     are ever read from
#     or written to the URL query string via history.replaceState.
#   * Raw generative-AI content fields (input_messages, output_messages,
#     system_instructions, tool_content, evaluation_explanation) are NEVER
#     placed in the URL, and this script never calls localStorage,
#     sessionStorage, or document.cookie for ANY purpose.
#   * Protected content is only ever requested after an explicit user click
#     on the "Load protected content" button; there is no automatic fetch of
#     that endpoint anywhere in this script.

_OBSERVE_SCRIPT = """
(function () {
  "use strict";

  var FILTER_KEYS = ["foundry_resource_id", "project_resource_id", "agent_id", "model", "tool_name", "run_key", "start", "end"];
  var COST_FILTER_KEYS = ["cost_period_id", "cost_component_id", "cost_breakdown", "cost_agent_key"];
  var COST_DISCLAIMER = "Operational cost allocation from declared billed totals and observed usage; not an invoice or billing-accurate charge.";
  var COST_BREAKDOWN_WARNING = "Agent, tool, and run breakdowns are alternative reconciliations of the same billed pools; do not add them together.";
  var AUTO_REFRESH_MS = 300000; // five minutes
  var DEFAULT_RANGE_MS = 24 * 60 * 60 * 1000; // trailing 24 hours
  // Mirrors MAX_TREND_POINTS in ui.py: even though the backend is expected
  // to already bound each trend series (T053), the client re-bounds
  // defensively so a chart never renders unbounded markup regardless of
  // what a given backend implementation actually sends.
  var MAX_TREND_POINTS = 60;
  // Maps each internal view identifier to the `ObserveQuery.view` wire value
  // from contracts/observe-api.openapi.yaml (mirrors OBSERVE_VIEW_WIRE_NAMES
  // in ui.py -- the internal "usage" id is spelled "models" on the wire).
  var VIEW_WIRE_NAMES = { overview: "overview", agents: "agents", usage: "models", tools: "tools", runs: "runs", coverage: "coverage" };
  // Best-effort, human-friendly labels for *documented* portal link keys
  // (mirrors _KNOWN_PORTAL_LABELS in ui.py). Any key not listed here still
  // renders (title-cased) rather than being dropped -- the "best-effort
  // labeling for undocumented portal targets" half of T053.
  var KNOWN_PORTAL_LABELS = {
    foundry_resource: "Open Foundry resource",
    foundry_project: "Open Foundry project",
    foundry_trace: "Open trace in Foundry",
    azure_monitor_resource: "Open Azure Monitor resource",
    azure_monitor_transaction: "Open transaction in Azure Monitor",
  };

  // `draftFilters` mirrors whatever is currently typed/selected in the form.
  // `appliedFilters` is only ever updated by an explicit Apply submit and is
  // the single source of truth for both the URL and outgoing fetches.
  var draftFilters = {};
  var appliedFilters = {};
  var currentView = "overview";
  var requestToken = 0;
  var activeController = null;
  var refreshTimer = null;
  // Independent request-lifecycle state for the agent-detail panel (T053).
  // Kept separate from `requestToken`/`activeController` so opening an
  // agent's details never aborts (or is aborted by) the main view's
  // auto-refresh/manual-refresh fetches, and vice versa.
  var agentDetailToken = 0;
  var agentDetailController = null;

  function readAppliedFromUrl() {
    var params = new URLSearchParams(window.location.search);
    var applied = {};
    FILTER_KEYS.forEach(function (key) {
      var value = params.get(key);
      if (value) {
        applied[key] = value;
      }
    });
    if (document.getElementById("cost")) {
      COST_FILTER_KEYS.forEach(function (key) {
        var value = params.get(key);
        if (value) {
          applied[key] = value;
        }
      });
      applied.cost_breakdown = applied.cost_breakdown || "agents";
    }
    currentView = params.get("view") || "overview";
    if (currentView === "cost" && !document.getElementById("cost")) {
      currentView = "overview";
    }
    if (!applied.start || !applied.end) {
      var end = new Date();
      var start = new Date(end.getTime() - DEFAULT_RANGE_MS);
      applied.start = applied.start || start.toISOString();
      applied.end = applied.end || end.toISOString();
    }
    return applied;
  }

  function syncUrl() {
    var params = new URLSearchParams();
    FILTER_KEYS.forEach(function (key) {
      if (appliedFilters[key]) {
        params.set(key, appliedFilters[key]);
      }
    });
    if (document.getElementById("cost")) {
      COST_FILTER_KEYS.forEach(function (key) {
        if (appliedFilters[key]) {
          params.set(key, appliedFilters[key]);
        }
      });
    }
    params.set("view", currentView);
    var next = window.location.pathname + "?" + params.toString();
    window.history.replaceState(null, "", next);
  }

  function readDraftFromForm(form) {
    var draft = {};
    FILTER_KEYS.forEach(function (key) {
      var field = form.querySelector('[data-draft-filter="' + key + '"]');
      draft[key] = field && field.value ? field.value : "";
    });
    return draft;
  }

  function populateFormFromApplied(form) {
    FILTER_KEYS.forEach(function (key) {
      var field = form.querySelector('[data-draft-filter="' + key + '"]');
      if (field) {
        field.value = appliedFilters[key] || "";
      }
    });
  }

  function readCostDraftFromForm(form) {
    var draft = {};
    COST_FILTER_KEYS.forEach(function (key) {
      var field = form.querySelector('[data-cost-filter="' + key + '"]');
      draft[key] = field && field.value ? field.value : "";
    });
    return draft;
  }

  function initializeCostPeriodFromServer(form) {
    if (appliedFilters.cost_period_id) {
      return;
    }
    var field = form.querySelector('[data-cost-filter="cost_period_id"]');
    if (!field) {
      return;
    }
    for (var index = 0; index < field.options.length; index += 1) {
      var option = field.options[index];
      if (option && option.value) {
        appliedFilters.cost_period_id = String(option.value);
        field.value = String(option.value);
        return;
      }
    }
  }

  function populateCostFormFromApplied(form) {
    COST_FILTER_KEYS.forEach(function (key) {
      var field = form.querySelector('[data-cost-filter="' + key + '"]');
      if (field) {
        var value = appliedFilters[key] || (key === "cost_breakdown" ? "agents" : "");
        ensureSelectOption(field, value, value);
        field.value = value;
      }
    });
  }

  function setRefreshStatus(text) {
    var status = document.getElementById("observe-refresh-status");
    if (status) {
      status.textContent = text;
    }
  }

  // -------------------------------------------------------------------
  // Safe DOM rendering for parsed `ObserveResponse` bodies (mirrors the
  // zero-vs-missing / disclaimer / coverage conventions implemented in
  // ui.py's Python renderers). Every value that originates from the fetch
  // response is written through `.textContent`/DOM node properties only --
  // markup is always built with `document.createElement`, never with raw
  // string-concatenated markup -- so a backend string can never be
  // interpreted as markup, even though it is not otherwise escaped here.
  // -------------------------------------------------------------------

  // Mirrors COVERAGE_STATE_LABELS in ui.py.
  var COVERAGE_STATE_LABELS = {
    available: { label: "Available", tone: "ok" },
    partial: { label: "Partial", tone: "warn" },
    no_data: { label: "No data found", tone: "muted" },
    not_reported: { label: "Not reported", tone: "muted" },
    not_configured: { label: "Not configured", tone: "muted" },
    inaccessible: { label: "Inaccessible", tone: "crit" },
    protected_or_unavailable: { label: "Protected or unavailable", tone: "warn" },
    error: { label: "Error", tone: "crit" },
  };

  // Mirrors COVERAGE_DIMENSION_LABELS in ui.py.
  var COVERAGE_DIMENSION_LABELS = {
    resource_access: "Resource access",
    telemetry_connection: "Telemetry connection",
    recent_traces: "Recent traces",
    agent_attribution: "Agent attribution",
    model_attribution: "Model attribution",
    token_usage: "Token usage",
    tool_attribution: "Tool attribution",
    run_correlation: "Run correlation",
    trace_correlation: "Trace correlation",
    protected_content: "Protected content",
    cost_attribution: "Cost attribution",
  };

  function clearChildren(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function makeEl(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = text;
    }
    return node;
  }

  function formatNumberJs(value) {
    if (value === null || value === undefined) {
      return "\u2014";
    }
    if (typeof value === "boolean") {
      return value ? "true" : "false";
    }
    var num = Number(value);
    if (isNaN(num)) {
      return String(value);
    }
    if (Number.isInteger(num)) {
      return num.toLocaleString("en-US");
    }
    return num.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  // Mirrors `_render_maybe_missing`: a reported `0` renders as "0" tagged
  // `metric-zero`; `null`/`undefined` renders as `missingText` tagged
  // `metric-missing`.
  function renderMaybeMissing(value, opts) {
    opts = opts || {};
    var suffix = opts.suffix || "";
    var missingText = opts.missingText || "Not reported";
    if (value === null || value === undefined) {
      return makeEl("span", "observe-metric metric-missing", missingText);
    }
    var numeric = Number(value);
    var isZero = !isNaN(numeric) && numeric === 0;
    return makeEl("span", "observe-metric " + (isZero ? "metric-zero" : "metric-value"), formatNumberJs(value) + suffix);
  }

  // Mirrors `_render_failure_rate`.
  function renderFailureRate(invocations, failures) {
    if (invocations === null || invocations === undefined || failures === null || failures === undefined) {
      return renderMaybeMissing(null);
    }
    var invocationsN = Number(invocations);
    var failuresN = Number(failures);
    if (isNaN(invocationsN) || isNaN(failuresN)) {
      return renderMaybeMissing(null);
    }
    if (invocationsN <= 0) {
      return renderMaybeMissing(null, { missingText: "No invocations" });
    }
    var rate = Math.round((failuresN / invocationsN) * 1000) / 10;
    return renderMaybeMissing(rate, { suffix: "%" });
  }

  // Mirrors `_render_token_totals`: always appends the "observed usage, not
  // billing data" disclaimer (FR-036).
  function renderTokenTotals(inputTokens, outputTokens, missingText) {
    var wrap = makeEl("span", "observe-token-totals");
    var inSpan = makeEl("span", "observe-token-in", "In: ");
    inSpan.appendChild(renderMaybeMissing(inputTokens, { missingText: missingText || "Not reported" }));
    var outSpan = makeEl("span", "observe-token-out", "Out: ");
    outSpan.appendChild(renderMaybeMissing(outputTokens, { missingText: missingText || "Not reported" }));
    wrap.appendChild(inSpan);
    wrap.appendChild(document.createTextNode(" "));
    wrap.appendChild(outSpan);
    wrap.appendChild(makeEl("span", "observe-hint", " (observed usage, not billing data)"));
    return wrap;
  }

  function renderModelTokenUsage(entry) {
    entry = entry || {};
    var wrap = makeEl("span", "observe-model-token-usage");
    wrap.appendChild(renderTokenTotals(entry.input_tokens, entry.output_tokens));
    var classes = makeEl("span", "observe-token-classes");
    [
      ["Cache read", entry.cache_read_tokens],
      ["Cache write", entry.cache_write_tokens],
      ["Reasoning", entry.reasoning_tokens]
    ].forEach(function (item) {
      var tokenClass = makeEl("span", "observe-token-class");
      tokenClass.appendChild(makeEl("span", "observe-token-class-label", item[0] + ": "));
      tokenClass.appendChild(renderMaybeMissing(item[1]));
      classes.appendChild(tokenClass);
    });
    Object.keys(entry.additional_token_classes || {}).forEach(function (name) {
      var tokenClass = makeEl("span", "observe-token-class observe-token-class-additional");
      tokenClass.appendChild(makeEl("span", "observe-token-class-label", name + ": "));
      tokenClass.appendChild(renderMaybeMissing(entry.additional_token_classes[name]));
      classes.appendChild(tokenClass);
    });
    if (entry.token_classes_partial) {
      classes.appendChild(
        makeEl("span", "observe-token-classes-partial", "Partial class coverage")
      );
    }
    if (entry.additional_token_classes_truncated) {
      classes.appendChild(
        makeEl("span", "observe-token-classes-truncated", "Additional classes truncated")
      );
    }
    wrap.appendChild(classes);
    return wrap;
  }

  // Mirrors `render_last_seen`: always appends the observed-telemetry-only
  // disclaimer (FR-034).
  function renderLastSeenJs(value) {
    var disclaimer = "Last seen reflects observed telemetry only, not agent lifecycle status.";
    if (!value) {
      var missing = makeEl(
        "span",
        "observe-last-seen observe-last-seen-missing metric-missing",
        "Last seen: not reported"
      );
      missing.appendChild(makeEl("span", "observe-hint", " (" + disclaimer + ")"));
      return missing;
    }
    var span = makeEl("span", "observe-last-seen");
    span.appendChild(document.createTextNode("Last seen: "));
    var time = document.createElement("time");
    time.setAttribute("datetime", value);
    time.textContent = value;
    span.appendChild(time);
    span.appendChild(makeEl("span", "observe-hint", " (" + disclaimer + ")"));
    return span;
  }

  // Mirrors `render_refreshed_at`.
  function renderRefreshedAtJs(value, label) {
    label = label || "Refreshed";
    var time = document.createElement("time");
    if (!value) {
      time.className = "observe-refreshed-at observe-refreshed-at-unknown";
      time.textContent = label + ": not yet refreshed";
      return time;
    }
    time.className = "observe-refreshed-at";
    time.setAttribute("datetime", value);
    time.textContent = label + ": " + value;
    return time;
  }

  // Mirrors `render_source_label`.
  function renderSourceLabelJs(source) {
    var label = "unknown source";
    if (typeof source === "string" && source) {
      label = source;
    } else if (source && typeof source === "object") {
      label = source.source_kind || source.source_id || "unknown source";
    }
    return makeEl("span", "observe-source-label", "Source: " + label);
  }

  function renderBadgeJs(label, tone, extraClass) {
    var classes = "observe-badge observe-tone-" + tone + (extraClass ? " " + extraClass : "");
    return makeEl("span", classes, label);
  }

  function renderSourceKindBadge(kind) {
    kind = kind || "unknown";
    var tones = {
      foundry_hosted: "ok",
      foundry_prompt: "ok",
      external_registered: "warn",
      external_unregistered: "warn",
      copilot_studio: "warn",
      unknown: "muted",
    };
    var tone = tones[kind] || "muted";
    var label = String(kind).split("_").map(function (part) {
      return part.charAt(0).toUpperCase() + part.slice(1);
    }).join(" ");
    return renderBadgeJs(label, tone, "observe-source-kind-badge");
  }

  function renderIdentityAvailabilityBadge(agentId) {
    if (agentId) {
      return renderBadgeJs("Identity available", "ok", "observe-identity-badge");
    }
    return renderBadgeJs("Identity not reported", "muted", "observe-identity-badge");
  }

  function renderDiagnosticsBannerNode(diagnostics) {
    if (!diagnostics) {
      return null;
    }
    var banner = makeEl("div", "observe-diagnostics-banner");
    var partial = diagnostics.partial_sources || 0;
    var failed = diagnostics.failed_sources || 0;
    if (partial > 0 || failed > 0) {
      var notice = makeEl(
        "p",
        "observe-partial-notice",
        "Partial results: some telemetry sources did not fully respond. " +
          "Data from every source that did respond is still shown below."
      );
      notice.setAttribute("role", "status");
      banner.appendChild(notice);
    }
    var dl = makeEl("dl", "observe-diagnostics-list");
    var rows = [
      ["Sources queried", diagnostics.source_count, {}],
      ["Successful", diagnostics.successful_sources, {}],
      ["Partial", diagnostics.partial_sources, {}],
      ["Failed", diagnostics.failed_sources, {}],
      ["Query duration", diagnostics.duration_ms, { suffix: " ms" }],
    ];
    rows.forEach(function (row) {
      var div = document.createElement("div");
      div.appendChild(makeEl("dt", null, row[0]));
      var dd = document.createElement("dd");
      dd.appendChild(renderMaybeMissing(row[1], row[2]));
      div.appendChild(dd);
      dl.appendChild(div);
    });
    var cacheDiv = document.createElement("div");
    cacheDiv.appendChild(makeEl("dt", null, "Cache"));
    cacheDiv.appendChild(makeEl("dd", null, diagnostics.cache_status || "Not reported"));
    dl.appendChild(cacheDiv);
    banner.appendChild(dl);
    banner.appendChild(renderRefreshedAtJs(diagnostics.completed_at));
    return banner;
  }

  function emptyStateNode(message) {
    var wrap = makeEl("div", "observe-empty-state");
    wrap.appendChild(makeEl("p", "observe-empty", message));
    return wrap;
  }

  function setViewContent(view, nodes) {
    var container = document.getElementById(view + "-content");
    if (!container) {
      return;
    }
    clearChildren(container);
    nodes.forEach(function (node) {
      if (node) {
        container.appendChild(node);
      }
    });
  }

  function buildDataTable(className, ariaLabel, columns, rows) {
    var table = makeEl("table", className);
    table.setAttribute("aria-label", ariaLabel);
    var thead = document.createElement("thead");
    var headRow = document.createElement("tr");
    columns.forEach(function (column) {
      var th = makeEl("th", null, column);
      th.setAttribute("scope", "col");
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    rows.forEach(function (cells) {
      var tr = document.createElement("tr");
      cells.forEach(function (cell) {
        var td = document.createElement("td");
        if (Array.isArray(cell)) {
          cell.forEach(function (part) {
            td.appendChild(part);
          });
        } else if (cell instanceof Node) {
          td.appendChild(cell);
        } else {
          td.textContent = cell === undefined || cell === null ? "" : String(cell);
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    return table;
  }

  // The exact key(s) each per-view renderer reads from `data` are a
  // defensive convention (no backend `service.py` implementation exists yet
  // to confirm the wire shape against): each helper accepts either a bare
  // array, or an object exposing the documented primary key, so a real
  // backend response is rendered correctly under either shape.
  function overviewMetricsFrom(data) {
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.metrics)) return data.metrics;
    if (data && Array.isArray(data.cards)) return data.cards;
    return [];
  }

  function agentsFrom(data) {
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.agents)) return data.agents;
    return [];
  }

  function modelsFrom(data) {
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.models)) return data.models;
    if (data && Array.isArray(data.usage)) return data.usage;
    return [];
  }

  function toolsFrom(data) {
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.tools)) return data.tools;
    return [];
  }

  function runsFrom(data) {
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.runs)) return data.runs;
    return [];
  }

  function boundsNoticeNode(bounds, rowsShown) {
    var total = bounds && bounds.rows_total_in_scope;
    var text = total === null || total === undefined
      ? "Showing " + rowsShown + " rows; total unknown."
      : "Showing " + rowsShown + " of " + total + " rows in scope.";
    return makeEl("p", "observe-hint observe-bounds-notice", text);
  }

  function renderOverview(data, diagnostics) {
    var banner = renderDiagnosticsBannerNode(diagnostics);
    var metrics = overviewMetricsFrom(data);
    if (!metrics.length) {
      setViewContent("overview", [banner, emptyStateNode("No data found for the selected filters.")]);
      return;
    }
    var grid = makeEl("div", "observe-overview-cards");
    metrics.forEach(function (metric) {
      metric = metric || {};
      var card = makeEl("div", "observe-card");
      card.setAttribute("role", "group");
      var title = metric.title || "";
      card.setAttribute("aria-label", title);
      card.appendChild(makeEl("h3", "observe-card-title", title));
      var valueEl = makeEl("p", "observe-card-value");
      valueEl.appendChild(renderMaybeMissing(metric.value, { suffix: metric.unit || "" }));
      card.appendChild(valueEl);
      if (metric.source) {
        card.appendChild(renderSourceLabelJs(metric.source));
      }
      if (metric.refreshed_at) {
        card.appendChild(renderRefreshedAtJs(metric.refreshed_at));
      }
      grid.appendChild(card);
    });
    setViewContent("overview", [banner, grid]);
  }

  function renderAgents(data, diagnostics) {
    var banner = renderDiagnosticsBannerNode(diagnostics);
    var agents = agentsFrom(data);
    if (!agents.length) {
      setViewContent("agents", [banner, emptyStateNode("No data found for the selected filters.")]);
      return;
    }
    var rows = agents.map(function (agent) {
      agent = agent || {};
      var nameCell = [
        document.createTextNode((agent.agent_name || agent.agent_id || "Not reported") + " "),
        renderSourceKindBadge(agent.source_kind),
        document.createTextNode(" "),
        renderIdentityAvailabilityBadge(agent.agent_id),
      ];
      return [
        nameCell,
        agent.source_id || "Not reported",
        agent.model || "Not reported",
        renderLastSeenJs(agent.last_seen),
        renderMaybeMissing(agent.invocations),
        renderFailureRate(agent.invocations, agent.failures),
        renderMaybeMissing(agent.p95_latency_ms, { suffix: " ms" }),
        renderTokenTotals(agent.input_tokens, agent.output_tokens),
        buildAgentDetailButton(agent),
      ];
    });
    var table = buildDataTable(
      "observe-agents-table",
      "Agents observed in the selected range",
      ["Agent", "Source", "Model", "Last seen", "Invocations", "Failure rate", "p95 latency", "Tokens", "Details"],
      rows
    );
    setViewContent("agents", [banner, table]);
  }

  // ---------------------------------------------------------------------
  // Agent detail panel (T053): explicit click -> POST /api/observe/agent-detail
  // ---------------------------------------------------------------------

  // `ObservedAgent.key` (data-model.md) is the stable identifier the
  // agent-detail endpoint expects as `agent_key`. Fall back to `agent_id`/
  // `agent_name` defensively (no live backend response has been observed
  // yet to confirm every row always carries `.key`), but never fabricate an
  // identifier: a row with none of the three renders a disabled button
  // rather than issuing an ambiguous request.
  function agentKeyFor(agent) {
    agent = agent || {};
    return agent.key || agent.agent_id || agent.agent_name || "";
  }

  function buildAgentDetailButton(agent) {
    var key = agentKeyFor(agent);
    var button = makeEl("button", "observe-agent-detail-button", "View details");
    button.type = "button";
    var label = agent && (agent.agent_name || agent.agent_id);
    button.setAttribute("aria-label", "View details for " + (label || "this agent"));
    if (key) {
      button.setAttribute("data-observe-agent-key", key);
      button.addEventListener("click", function () {
        fetchAgentDetail(key, false);
      });
    } else {
      button.disabled = true;
    }
    return button;
  }

  function agentDetailPanel() {
    return document.getElementById("agent-detail-content");
  }

  function setAgentDetailStatus(text) {
    var panel = agentDetailPanel();
    if (!panel) {
      return;
    }
    clearChildren(panel);
    panel.appendChild(makeEl("p", "observe-agent-detail-status", text));
  }

  // Mirrors `_bound_points` in ui.py so a trend series never renders more
  // than `MAX_TREND_POINTS` markers/table rows, keeping the first and last
  // point so the visible trend still spans the full requested range.
  function boundTrendPoints(points) {
    var items = Array.isArray(points) ? points : [];
    if (items.length <= MAX_TREND_POINTS || MAX_TREND_POINTS <= 1) {
      return items;
    }
    var bounded = [];
    var step = (items.length - 1) / (MAX_TREND_POINTS - 1);
    for (var i = 0; i < MAX_TREND_POINTS; i++) {
      bounded.push(items[Math.round(i * step)]);
    }
    bounded[bounded.length - 1] = items[items.length - 1];
    return bounded;
  }

  // Renders each bounded trend series as an accessible data table (the same
  // exact-value semantics as the visually-hidden table in
  // `render_trend_chart`) rather than building a new SVG chart engine in
  // JS: this reuses existing safe-DOM helpers only, and every value stays
  // exact (no color-only distinction is introduced since rows are labeled
  // by series name).
  function renderAgentTrendsNode(trends) {
    var wrap = makeEl("div", "observe-agent-detail-trends");
    (Array.isArray(trends) ? trends : []).forEach(function (trend) {
      trend = trend || {};
      var unit = trend.unit || "";
      var figure = makeEl("div", "observe-agent-trend");
      figure.appendChild(makeEl("h4", "observe-agent-trend-title", trend.title || "Trend"));
      var rows = [];
      (Array.isArray(trend.series) ? trend.series : []).forEach(function (series) {
        series = series || {};
        var label = series.label || "Series";
        boundTrendPoints(series.points).forEach(function (point) {
          var xLabel = Array.isArray(point) ? point[0] : "";
          var value = Array.isArray(point) ? point[1] : null;
          rows.push([
            label,
            xLabel === undefined || xLabel === null ? "" : String(xLabel),
            renderMaybeMissing(value, { suffix: unit }),
          ]);
        });
      });
      if (rows.length) {
        figure.appendChild(
          buildDataTable(
            "observe-chart-data",
            "Exact values for " + (trend.title || "trend"),
            ["Series", "Point", "Value"],
            rows
          )
        );
      } else {
        figure.appendChild(makeEl("p", "observe-empty", "No data found for this chart."));
      }
      wrap.appendChild(figure);
    });
    return wrap;
  }

  function titleCaseFromKey(key) {
    return String(key || "")
      .split("_")
      .filter(function (part) {
        return part.length > 0;
      })
      .map(function (part) {
        return part.charAt(0).toUpperCase() + part.slice(1);
      })
      .join(" ");
  }

  // Only ever renders http(s) links: this is the one place arbitrary
  // backend-supplied URLs reach an `<a href>`, so anything else (including
  // a `javascript:` URL) is dropped rather than assigned.
  function isSafePortalUrl(url) {
    return typeof url === "string" && new RegExp("^https?://", "i").test(url);
  }

  // Mirrors `render_portal_links` in ui.py: an `<a target="_blank"
  // rel="noopener noreferrer">` per documented/undocumented portal link,
  // with a visually-hidden "(opens in a new tab)" suffix for assistive
  // technology.
  function renderPortalLinksNode(links) {
    links = links && typeof links === "object" ? links : {};
    var keys = Object.keys(links);
    if (!keys.length) {
      return null;
    }
    var list = makeEl("ul", "observe-portal-links");
    var rendered = 0;
    keys.forEach(function (key) {
      var url = links[key];
      if (!isSafePortalUrl(url)) {
        return;
      }
      var item = document.createElement("li");
      var anchor = document.createElement("a");
      anchor.href = url;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      anchor.setAttribute("data-observe-portal-link", key);
      anchor.textContent = KNOWN_PORTAL_LABELS[key] || titleCaseFromKey(key);
      anchor.appendChild(makeEl("span", "visually-hidden", " (opens in a new tab)"));
      item.appendChild(anchor);
      list.appendChild(item);
      rendered += 1;
    });
    return rendered ? list : null;
  }

  // Defensive extraction, same convention as `agentsFrom`/`modelsFrom`: no
  // live backend response has been observed yet to confirm the exact
  // `/api/observe/agent-detail` response shape, so each field is read from
  // the documented primary key with a couple of reasonable fallbacks.
  function agentDetailFrom(body) {
    body = body && typeof body === "object" ? body : {};
    return {
      agent: body.agent || body.data || {},
      trends: Array.isArray(body.trends) ? body.trends : [],
      portalLinks: body.portal_links || body.portalLinks || body.links || {},
    };
  }

  function renderAgentDetail(agentKey, body) {
    var panel = agentDetailPanel();
    if (!panel) {
      return;
    }
    var parsed = agentDetailFrom(body);
    var agent = parsed.agent || {};
    clearChildren(panel);
    var section = makeEl("section", "observe-agent-detail");
    section.setAttribute("aria-label", "Agent detail: " + (agent.agent_name || agent.agent_id || agentKey || "agent"));
    section.appendChild(makeEl("h3", null, agent.agent_name || agent.agent_id || "Unknown agent"));
    var identityLine = makeEl("p", "observe-agent-detail-identity");
    identityLine.appendChild(renderSourceKindBadge(agent.source_kind));
    identityLine.appendChild(document.createTextNode(" "));
    identityLine.appendChild(renderIdentityAvailabilityBadge(agent.agent_id));
    section.appendChild(identityLine);
    var lastSeenLine = makeEl("p", "observe-agent-detail-last-seen");
    lastSeenLine.appendChild(renderLastSeenJs(agent.last_seen));
    section.appendChild(lastSeenLine);
    section.appendChild(renderAgentTrendsNode(parsed.trends));
    var linksNode = renderPortalLinksNode(parsed.portalLinks);
    if (linksNode) {
      section.appendChild(linksNode);
    }
    panel.appendChild(section);
  }

  function fetchAgentDetail(agentKey, manual) {
    // Explicit, click-triggered only: this function is only ever invoked
    // from a "click" listener on an agent row's details button (T053/FR-038
    // -- agent details load only when the user opens/selects them, never
    // automatically alongside the main query).
    if (!agentKey) {
      return null;
    }
    var myToken = ++agentDetailToken;
    if (agentDetailController) {
      agentDetailController.abort();
    }
    var controller = new AbortController();
    agentDetailController = controller;
    setAgentDetailStatus("Loading agent detail\u2026");

    // POST /api/observe/agent-detail with `{agent_key, filters, refresh}`
    // per contracts/observe-api.openapi.yaml. `filters` reuses the same
    // applied scope/time-range filters as the main query so returned trends
    // match whatever the user is currently looking at. Only the stable
    // `agent_key` identifier is ever sent -- no raw protected content.
    var agentDetailPayload = {
      agent_key: agentKey,
      filters: {
        foundry_resource_id: appliedFilters.foundry_resource_id || null,
        project_resource_id: appliedFilters.project_resource_id || null,
        agent_id: appliedFilters.agent_id || null,
        model: appliedFilters.model || null,
        start: appliedFilters.start,
        end: appliedFilters.end,
      },
      refresh: manual === true,
    };

    return fetch("/api/observe/agent-detail", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(agentDetailPayload),
      signal: controller.signal,
    })
      .then(function (response) {
        // Stale-response suppression, same convention as fetchObserveData:
        // discard this result if a newer agent-detail request has started.
        if (myToken !== agentDetailToken) {
          return null;
        }
        if (response.status === 404) {
          setAgentDetailStatus("Agent not found for the selected filters.");
          return null;
        }
        if (!response.ok) {
          setAgentDetailStatus("Agent detail failed to load.");
          return null;
        }
        return response.json().then(function (body) {
          if (myToken !== agentDetailToken) {
            return null;
          }
          renderAgentDetail(agentKey, body);
          return body;
        });
      })
      .catch(function (error) {
        if (error && error.name === "AbortError") {
          return null;
        }
        if (myToken === agentDetailToken) {
          setAgentDetailStatus("Agent detail failed to load.");
        }
        return null;
      });
  }

  function renderUsage(data, diagnostics) {
    var banner = renderDiagnosticsBannerNode(diagnostics);
    var usage = modelsFrom(data);
    if (!usage.length) {
      setViewContent("usage", [banner, emptyStateNode("No data found for the selected filters.")]);
      return;
    }
    var rows = usage.map(function (entry) {
      entry = entry || {};
      return [
        entry.model || entry.deployment || "Not reported",
        entry.deployment || "Not reported",
        renderMaybeMissing(entry.requests),
        renderFailureRate(entry.requests, entry.failures),
        renderMaybeMissing(entry.p95_latency_ms, { suffix: " ms" }),
        renderModelTokenUsage(entry),
        renderLastSeenJs(entry.last_seen),
      ];
    });
    var table = buildDataTable(
      "observe-usage-table",
      "Model usage observed in the selected range",
      ["Model", "Deployment", "Requests", "Failure rate", "p95 latency", "Tokens", "Last seen"],
      rows
    );
    setViewContent("usage", [banner, table]);
  }

  function renderTools(data, diagnostics, bounds) {
    var banner = renderDiagnosticsBannerNode(diagnostics);
    var tools = toolsFrom(data);
    var notice = boundsNoticeNode(bounds, tools.length);
    if (!tools.length) {
      setViewContent("tools", [
        banner,
        notice,
        emptyStateNode("No tool activity was found for the selected filters. Tool attribution may not be reported; check Telemetry coverage for details."),
      ]);
      return;
    }
    var rows = tools.map(function (tool) {
      tool = tool || {};
      return [
        tool.tool_name || "Not reported",
        tool.agent_name || tool.agent_id || tool.agent_key || "Not reported",
        tool.source_id || "Not reported",
        renderSourceKindBadge(tool.source_kind),
        renderLastSeenJs(tool.last_seen),
        renderMaybeMissing(tool.invocations),
        renderMaybeMissing(tool.failures),
        renderMaybeMissing(tool.p95_latency_ms, { suffix: " ms", missingText: "Not measured" }),
      ];
    });
    var table = buildDataTable(
      "observe-tools-table",
      "Tools observed in the selected range",
      ["Tool", "Agent", "Source", "Runtime", "Last seen", "Invocations", "Failures", "p95 latency"],
      rows
    );
    setViewContent("tools", [banner, notice, table]);
  }

  function renderRuns(data, diagnostics, bounds) {
    var banner = renderDiagnosticsBannerNode(diagnostics);
    var runs = runsFrom(data);
    var notice = boundsNoticeNode(bounds, runs.length);
    if (!runs.length) {
      setViewContent("runs", [
        banner,
        notice,
        emptyStateNode("No runs could be correlated for the selected filters. Run correlation may not be reported; check Telemetry coverage for details."),
      ]);
      return;
    }
    var rows = runs.map(function (run) {
      run = run || {};
      return [
        run.run_key || "Not reported",
        run.run_key_kind || "Not reported",
        run.agent_name || run.agent_id || run.agent_key || "Not reported",
        run.source_id || "Not reported",
        renderSourceKindBadge(run.source_kind),
        run.started_at || "Not reported",
        renderMaybeMissing(run.duration_ms, { suffix: " ms" }),
        run.status || "Not reported",
        renderMaybeMissing(run.turns),
        renderMaybeMissing(run.tool_invocations),
        renderTokenTotals(run.input_tokens, run.output_tokens, "Not available"),
      ];
    });
    var hint = makeEl("p", "observe-hint", "Start, duration, and turns describe activity within the selected range.");
    var table = buildDataTable(
      "observe-runs-table",
      "Runs observed in the selected range",
      ["Run key", "Correlation", "Agent", "Source", "Runtime", "Started in range", "Duration in range", "Status", "Turns in range", "Tool invocations", "Tokens"],
      rows
    );
    setViewContent("runs", [banner, notice, hint, table]);
  }

  function costLabel(value) {
    if (value === null || value === undefined || value === "") {
      return "Not reported";
    }
    return String(value).split("_").map(function (part) {
      return part.charAt(0).toUpperCase() + part.slice(1);
    }).join(" ");
  }

  function renderCostMethodBadgeNode(value) {
    var method = value || "unavailable";
    var tone = { metered: "info", commitment: "warn" }[method] || "muted";
    return renderBadgeJs(
      costLabel(value),
      tone,
      "observe-cost-method-" + String(method)
    );
  }

  function renderCostConfidenceBadgeNode(value) {
    var confidence = value || "unavailable";
    var tone = { high: "ok", medium: "info", low: "warn", unavailable: "muted" }[
      confidence
    ] || "muted";
    return renderBadgeJs(
      costLabel(value),
      tone,
      "observe-cost-confidence-" + String(confidence)
    );
  }

  function renderCostAmountNode(amount, currency) {
    if (amount === null || amount === undefined) {
      return makeEl("span", "observe-metric metric-missing", "Not reported");
    }
    var isZero = Number(amount) === 0;
    var node = makeEl(
      "span",
      "observe-cost-amount" + (isZero ? " metric-zero" : ""),
      String(amount) + " " + (currency || "Not reported")
    );
    if (isZero) {
      node.appendChild(document.createTextNode(" "));
      node.appendChild(makeEl("span", "observe-hint observe-cost-observed-zero", "Observed zero"));
    }
    return node;
  }

  function renderCostDeclaredAmountNode(amount, currency) {
    if (amount === null || amount === undefined) {
      return makeEl(
        "span",
        "observe-metric metric-missing observe-cost-missing-total",
        "Missing configured billed total"
      );
    }
    return renderCostAmountNode(amount, currency);
  }

  function renderCostUsageShareNode(row) {
    row = row || {};
    if (
      row.usage_numerator === null || row.usage_numerator === undefined ||
      row.usage_denominator === null || row.usage_denominator === undefined
    ) {
      return makeEl(
        "span",
        "observe-metric metric-missing",
        "Observed usage: Not reported"
      );
    }
    var unit = String(row.usage_unit || "usage").split("_").join(" ");
    return makeEl(
      "span",
      "observe-cost-usage-share",
      "Observed usage: " + String(row.usage_numerator) + " / " +
        String(row.usage_denominator) + " " + unit
    );
  }

  function ensureSelectOption(select, value, label) {
    if (!select || value === null || value === undefined || value === "") {
      return;
    }
    var textValue = String(value);
    var found = false;
    Array.prototype.forEach.call(select.options, function (option) {
      if (option.value === textValue) {
        found = true;
      }
    });
    if (!found) {
      var option = document.createElement("option");
      option.value = textValue;
      option.textContent = label === null || label === undefined ? textValue : String(label);
      select.appendChild(option);
    }
  }

  function replaceCostSelectOptions(select, values, allLabel) {
    if (!select) {
      return;
    }
    while (select.firstChild) {
      select.removeChild(select.firstChild);
    }
    if (allLabel !== null && allLabel !== undefined) {
      var allOption = document.createElement("option");
      allOption.value = "";
      allOption.textContent = String(allLabel);
      select.appendChild(allOption);
    }
    (Array.isArray(values) ? values : []).forEach(function (value) {
      ensureSelectOption(select, value, value);
    });
  }

  function selectedPeriodComponentIds(periodSelect) {
    if (!periodSelect || periodSelect.selectedIndex < 0) {
      return [];
    }
    var selectedOption = periodSelect.options[periodSelect.selectedIndex];
    var encoded = selectedOption
      ? selectedOption.getAttribute("data-cost-component-ids")
      : "";
    return encoded
      ? encoded.split(",").filter(function (value) { return value !== ""; })
      : [];
  }

  function resetCostAgentSelector(form) {
    var agentSelect = form.querySelector('[data-cost-filter="cost_agent_key"]');
    replaceCostSelectOptions(agentSelect, [], "All agents");
    appliedFilters.cost_agent_key = "";
  }

  function resetCostSelectorsForPeriod(form) {
    var periodSelect = form.querySelector('[data-cost-filter="cost_period_id"]');
    var componentSelect = form.querySelector('[data-cost-filter="cost_component_id"]');
    replaceCostSelectOptions(
      componentSelect,
      selectedPeriodComponentIds(periodSelect),
      "All components"
    );
    appliedFilters.cost_component_id = "";
    resetCostAgentSelector(form);
  }

  function renderCostControlsFromData(data) {
    data = data && typeof data === "object" ? data : {};
    var period = data.period || {};
    var periodSelect = document.getElementById("observe-cost-period");
    ensureSelectOption(periodSelect, period.id, period.id);
    if (period.id) {
      appliedFilters.cost_period_id = String(period.id);
      if (periodSelect) periodSelect.value = String(period.id);
    }

    var componentSelect = document.getElementById("observe-cost-component");
    var configuredComponentIds = selectedPeriodComponentIds(periodSelect);
    if (!configuredComponentIds.length && !data.component_filter) {
      configuredComponentIds = (Array.isArray(data.components) ? data.components : [])
        .map(function (component) {
          return component && component.component_id ? String(component.component_id) : "";
        })
        .filter(function (value) { return value !== ""; });
      if (periodSelect && periodSelect.selectedIndex >= 0) {
        periodSelect.options[periodSelect.selectedIndex].setAttribute(
          "data-cost-component-ids",
          configuredComponentIds.join(",")
        );
      }
    }
    if (configuredComponentIds.length) {
      replaceCostSelectOptions(componentSelect, configuredComponentIds, "All components");
    }
    (Array.isArray(data.components) ? data.components : []).forEach(function (component) {
      component = component || {};
      ensureSelectOption(componentSelect, component.component_id, component.component_id);
    });
    if (data.component_filter) {
      ensureSelectOption(componentSelect, data.component_filter, data.component_filter);
      appliedFilters.cost_component_id = String(data.component_filter);
      if (componentSelect) componentSelect.value = String(data.component_filter);
    } else {
      appliedFilters.cost_component_id = "";
      if (componentSelect) componentSelect.value = "";
    }

    var breakdownSelect = document.getElementById("observe-cost-breakdown");
    if (data.breakdown) {
      appliedFilters.cost_breakdown = String(data.breakdown);
      if (breakdownSelect) breakdownSelect.value = String(data.breakdown);
    }

    var agentSelect = document.getElementById("observe-cost-agent");
    var selectedAgent = appliedFilters.cost_agent_key || "";
    var agentKeys = [];
    (Array.isArray(data.rows) ? data.rows : []).forEach(function (row) {
      row = row || {};
      var key = row.agent_key ||
        (row.consumer_kind === "agent" ? row.consumer_key : null);
      if (key !== null && key !== undefined && key !== "" &&
          agentKeys.indexOf(String(key)) === -1) {
        agentKeys.push(String(key));
      }
    });
    replaceCostSelectOptions(agentSelect, agentKeys, "All agents");
    ensureSelectOption(agentSelect, selectedAgent, selectedAgent);
    if (agentSelect) agentSelect.value = selectedAgent;
    syncUrl();
  }

  function renderCostPeriodNode(data) {
    data = data || {};
    var period = data.period || {};
    var dl = makeEl("dl", "observe-cost-period");
    [
      ["Period", period.id],
      [
        "Observation window",
        String(period.starts_at || "Not reported") + " to " +
          String(period.ends_at || "Not reported"),
      ],
      ["Calculated at", data.calculated_at],
      ["Latest observed", data.latest_observed_at],
    ].forEach(function (item) {
      var div = document.createElement("div");
      div.appendChild(makeEl("dt", null, item[0]));
      div.appendChild(makeEl("dd", null, item[1] || "Not reported"));
      dl.appendChild(div);
    });
    return dl;
  }

  function renderCostSubtotalsNode(subtotals) {
    subtotals = Array.isArray(subtotals) ? subtotals : [];
    if (!subtotals.length) {
      return emptyStateNode("No currency subtotals reported.");
    }
    var rows = subtotals.map(function (subtotal) {
      subtotal = subtotal || {};
      return [
        subtotal.currency || "Not reported",
        subtotal.currency_minor_units === null || subtotal.currency_minor_units === undefined
          ? "Not reported"
          : String(subtotal.currency_minor_units),
        renderCostDeclaredAmountNode(subtotal.declared_total, subtotal.currency),
        renderCostAmountNode(subtotal.attributed_amount, subtotal.currency),
        renderCostAmountNode(subtotal.unattributed_amount, subtotal.currency),
        renderCostAmountNode(subtotal.unallocated_amount, subtotal.currency),
      ];
    });
    var wrap = makeEl("div", "observe-cost-subtotals");
    wrap.appendChild(
      buildDataTable(
        "observe-cost-subtotals-table",
        "Cost currency subtotals",
        ["Currency", "Minor units", "Declared", "Attributed", "Unattributed", "Unallocated"],
        rows
      )
    );
    var notes = makeEl("ul", "observe-cost-precision-notes");
    subtotals.forEach(function (subtotal) {
      subtotal = subtotal || {};
      notes.appendChild(
        makeEl(
          "li",
          null,
          "Currency precision: " + String(subtotal.currency_minor_units) +
            " minor units for " + String(subtotal.currency || "Not reported")
        )
      );
    });
    wrap.appendChild(notes);
    return wrap;
  }

  function renderCostComponentsNode(components) {
    components = Array.isArray(components) ? components : [];
    if (!components.length) {
      return emptyStateNode("No configured component summaries reported.");
    }
    var rows = components.map(function (component) {
      component = component || {};
      var boundary = component.billing_boundary || {};
      var boundaryText = costLabel(boundary.kind) + ": " +
        String(boundary.label || boundary.value || "Not reported");
      if (boundary.label && boundary.value) {
        boundaryText += " (" + String(boundary.value) + ")";
      }
      var fallback = !!(
        component.applied_key && component.preferred_key &&
        component.applied_key !== component.preferred_key
      );
      var method = costLabel(component.allocation_model) +
        "; Preferred key: " + costLabel(component.preferred_key) +
        "; Applied key: " + costLabel(component.applied_key) +
        "; Fallback: " + (fallback ? "Yes" : "No");
      var shown = component.rows_shown;
      var total = component.rows_total;
      var rowCount = String(shown === null || shown === undefined ? "Not reported" : shown);
      if (total === null || total === undefined) {
        rowCount += " / total unknown";
      } else {
        var omitted = Math.max(Number(total) - Number(shown || 0), 0);
        rowCount += " / " + String(total);
        if (omitted) rowCount += " (" + String(omitted) + " omitted)";
      }
      var coverage = [
        makeEl("strong", null, "Confidence:"),
        document.createTextNode(" " + costLabel(component.confidence)),
        document.createElement("br"),
        makeEl("strong", null, "Coverage:"),
        document.createTextNode(" " + costLabel(component.coverage_state)),
        document.createElement("br"),
        makeEl("strong", null, "Reason:"),
        document.createTextNode(
          " " + String(component.coverage_reason || "No incomplete-coverage reason reported.")
        ),
        document.createElement("br"),
        makeEl("strong", null, "Next action:"),
        document.createTextNode(
          " " + String(component.next_action || "No follow-up action required.")
        ),
      ];
      return [
        [
          document.createTextNode(component.component_id || "Not reported"),
          document.createElement("br"),
          makeEl("span", "observe-hint", costLabel(component.component_type)),
        ],
        boundaryText,
        component.billed_source || "Not reported",
        [
          renderCostMethodBadgeNode(component.allocation_model),
          document.createElement("br"),
          makeEl("span", "observe-hint", method),
        ],
        renderCostDeclaredAmountNode(component.declared_total, component.currency),
        renderCostAmountNode(component.attributed_amount, component.currency),
        renderCostAmountNode(component.unattributed_amount, component.currency),
        renderCostAmountNode(component.unallocated_amount, component.currency),
        renderCostAmountNode(component.omitted_allocated_amount, component.currency),
        rowCount,
        [
          renderCostConfidenceBadgeNode(component.confidence),
          document.createElement("br"),
        ].concat(coverage),
      ];
    });
    return buildDataTable(
      "observe-cost-components-table",
      "Exact cost component reconciliation",
      [
        "Component", "Billing boundary", "Source", "Method", "Declared", "Attributed",
        "Unattributed", "Unallocated", "Omitted allocated", "Rows", "Confidence and coverage",
      ],
      rows
    );
  }

  function costBreakdownLabel(breakdown) {
    return { agents: "Agent", tools: "Tool", runs: "Run" }[breakdown] || "Consumer";
  }

  function costConsumerLabel(row, breakdown) {
    if (row.consumer_kind !== "unattributed") {
      var identity = {
        agents: row.agent_key,
        tools: row.tool_name,
        runs: row.run_key,
      }[breakdown];
      return identity || row.consumer_key || "Not reported";
    }
    return {
      agents: "Unattributed agent",
      tools: "Unattributed tool",
      runs: "Unattributed run",
    }[breakdown] || "Unattributed";
  }

  function costDrilldownHref(data, row, breakdown) {
    var params = new URLSearchParams();
    var period = data.period || {};
    params.set("view", "cost");
    if (period.id) params.set("cost_period_id", String(period.id));
    if (data.component_filter) {
      params.set("cost_component_id", String(data.component_filter));
    }
    params.set("cost_breakdown", breakdown);
    var agentKey = row.agent_key || row.consumer_key;
    if (agentKey) params.set("cost_agent_key", String(agentKey));
    return "?" + params.toString() + "#cost";
  }

  function costRowEvidenceNode(row, data) {
    var period = data.period || {};
    var boundary = row.billing_boundary || {};
    var dl = makeEl("dl", "observe-cost-row-evidence");
    var adjustment = row.rounding_adjustment_minor_units;
    var details = [
      ["Period", row.period_id || period.id],
      [
        "Observation window",
        String(row.period_starts_at || period.starts_at || "Not reported") + " to " +
          String(row.period_ends_at || period.ends_at || "Not reported"),
      ],
      [
        "Billing boundary",
        costLabel(boundary.kind) + ": " +
          String(boundary.label || boundary.value || "Not reported"),
      ],
      ["Source resource", row.source_resource_id],
      ["Project resource", row.project_resource_id],
      ["Agent key", row.agent_key],
      ["Preferred key", costLabel(row.preferred_key)],
      ["Applied key", costLabel(row.applied_key)],
      ["Fallback", row.fallback_used ? "Yes" : "No"],
      [
        "Rounding adjustment",
        adjustment === null || adjustment === undefined
          ? "Not reported"
          : String(adjustment) + " minor unit" + (Number(adjustment) === 1 ? "" : "s"),
      ],
      ["Confidence", costLabel(row.confidence)],
      ["Coverage", costLabel(row.coverage_state)],
      ["Coverage reason", row.coverage_reason || "No incomplete-coverage reason reported."],
      ["Calculated at", row.calculated_at],
      ["Latest observed", row.latest_observed_at],
    ];
    details.forEach(function (detail) {
      var div = document.createElement("div");
      div.appendChild(makeEl("dt", null, detail[0]));
      div.appendChild(makeEl("dd", null, detail[1] || "Not reported"));
      dl.appendChild(div);
    });
    return dl;
  }

  function renderCostRowsNode(rows, data) {
    rows = Array.isArray(rows) ? rows : [];
    if (!rows.length) {
      return emptyStateNode("No allocations reported for the selected cost selectors.");
    }
    data = data || {};
    var breakdown = data.breakdown || "agents";
    var consumerHeading = costBreakdownLabel(breakdown);
    var renderedRows = rows.map(function (row) {
      row = row || {};
      var method = costLabel(row.allocation_model) +
        "; Preferred key: " + costLabel(row.preferred_key) +
        "; Applied key: " + costLabel(row.applied_key) +
        "; Fallback: " + (row.fallback_used ? "Yes" : "No");
      var consumerCell = [document.createTextNode(costConsumerLabel(row, breakdown))];
      if (
        breakdown === "agents" && row.consumer_kind !== "unattributed" &&
        (row.agent_key || row.consumer_key)
      ) {
        var actions = makeEl("div", "observe-cost-drilldown-actions");
        var toolsLink = makeEl("a", null, "View tools");
        toolsLink.setAttribute("href", costDrilldownHref(data, row, "tools"));
        var runsLink = makeEl("a", null, "View runs");
        runsLink.setAttribute("href", costDrilldownHref(data, row, "runs"));
        actions.appendChild(toolsLink);
        actions.appendChild(document.createTextNode(" "));
        actions.appendChild(runsLink);
        consumerCell.push(actions);
      }
      var coverage = [
        costLabel(row.confidence),
        costLabel(row.coverage_state),
        row.coverage_reason,
      ].filter(function (part) { return !!part; }).join(" \u2014 ");
      return [
        consumerCell,
        row.component_id || "Not reported",
        renderCostAmountNode(row.amount, row.currency),
        renderCostUsageShareNode(row),
        [
          renderCostMethodBadgeNode(row.allocation_model),
          document.createElement("br"),
          makeEl("span", "observe-hint", method),
        ],
        row.billed_source || "Not reported",
        [
          renderCostConfidenceBadgeNode(row.confidence),
          document.createElement("br"),
          makeEl("span", "observe-hint", coverage),
        ],
        costRowEvidenceNode(row, data),
      ];
    });
    return buildDataTable(
      "observe-cost-allocations-table",
      consumerHeading + " cost allocations",
      [
        consumerHeading, "Component", "Amount", "Usage share", "Method", "Source",
        "Confidence and coverage", "Provenance and evidence",
      ],
      renderedRows
    );
  }

  function renderCostCoverageNode(coverage) {
    coverage = Array.isArray(coverage) ? coverage : [];
    if (!coverage.length) return null;
    var rows = coverage.map(function (entry) {
      entry = entry || {};
      var state = entry.state === "not_configured"
        ? "Missing configured billed total"
        : costLabel(entry.state);
      return [
        entry.source_id || "Not reported",
        costLabel(entry.dimension),
        entry.allocation_key || "Not reported",
        state,
        entry.reason || "No reason reported.",
        entry.next_action || "No follow-up action reported.",
      ];
    });
    return buildDataTable(
      "observe-cost-coverage-table",
      "Cost attribution coverage",
      ["Source or component", "Dimension", "Allocation key", "State", "Reason", "Next action"],
      rows
    );
  }

  function renderCostPartialFailuresNode(partialFailures) {
    partialFailures = Array.isArray(partialFailures) ? partialFailures : [];
    if (!partialFailures.length) return null;
    var section = makeEl("section", "observe-cost-partial-failures");
    section.appendChild(makeEl("h3", null, "Partial source failures"));
    section.appendChild(makeEl(
      "p",
      null,
      "Readable components remain visible; failed sources may make allocations incomplete."
    ));
    var list = document.createElement("ul");
    partialFailures.forEach(function (failure) {
      failure = failure || {};
      list.appendChild(makeEl(
        "li",
        null,
        String(failure.source_id || "Not reported") + " (" + costLabel(failure.status) +
          ") \u2014 " + String(failure.reason || "No reason reported.") +
          " Next action: " + String(failure.next_action || "No follow-up action reported.")
      ));
    });
    section.appendChild(list);
    return section;
  }

  function renderCostBoundsNode(bounds) {
    if (!bounds) return null;
    var shown = bounds.rows_shown;
    var total = bounds.rows_total_in_scope;
    var text = total === null || total === undefined
      ? "Showing " + String(shown === null || shown === undefined ? "an unknown number of" : shown) +
        " rows; total unknown."
      : "Showing " + String(shown === null || shown === undefined ? "an unknown number of" : shown) +
        " of " + String(total) + " rows in scope" +
        (bounds.truncated ? "; results are truncated." : ".");
    return makeEl("p", "observe-hint observe-cost-bounds-notice", text);
  }

  function renderCost(data, diagnostics, coverage, partialFailures, bounds) {
    var banner = renderDiagnosticsBannerNode(diagnostics);
    if (!data || typeof data !== "object") {
      var emptyNodes = [
        banner,
        emptyStateNode("No cost allocation data reported."),
        makeEl("p", "observe-cost-breakdown-warning", COST_BREAKDOWN_WARNING),
        makeEl("p", "observe-cost-disclaimer", COST_DISCLAIMER),
      ];
      var emptyCoverage = renderCostCoverageNode(coverage);
      var emptyFailures = renderCostPartialFailuresNode(partialFailures);
      var emptyBounds = renderCostBoundsNode(bounds);
      if (emptyBounds) emptyNodes.splice(1, 0, emptyBounds);
      if (emptyCoverage) emptyNodes.splice(emptyNodes.length - 1, 0, emptyCoverage);
      if (emptyFailures) emptyNodes.splice(emptyNodes.length - 1, 0, emptyFailures);
      setViewContent("cost", emptyNodes);
      return;
    }
    renderCostControlsFromData(data);
    var nodes = [banner, renderCostPeriodNode(data)];
    nodes.push(makeEl("p", "observe-cost-breakdown-warning", COST_BREAKDOWN_WARNING));
    nodes.push(makeEl("h3", null, "Currency subtotals"));
    nodes.push(renderCostSubtotalsNode(data.currency_subtotals));
    nodes.push(makeEl("h3", null, "Component reconciliation"));
    nodes.push(renderCostComponentsNode(data.components));
    nodes.push(makeEl("h3", null, costLabel(data.breakdown) + " allocation"));
    nodes.push(renderCostRowsNode(data.rows, data));
    var boundsNode = renderCostBoundsNode(bounds);
    var coverageNode = renderCostCoverageNode(coverage);
    var failuresNode = renderCostPartialFailuresNode(partialFailures);
    if (boundsNode) nodes.push(boundsNode);
    if (coverageNode) nodes.push(coverageNode);
    if (failuresNode) nodes.push(failuresNode);
    nodes.push(makeEl("p", "observe-cost-disclaimer", COST_DISCLAIMER));
    setViewContent("cost", nodes);
  }

  function renderCoverage(coverage, diagnostics) {
    var banner = renderDiagnosticsBannerNode(diagnostics);
    coverage = Array.isArray(coverage) ? coverage : [];
    if (!coverage.length) {
      setViewContent("coverage", [banner, emptyStateNode("No coverage information reported.")]);
      return;
    }
    var rows = coverage.map(function (entry) {
      entry = entry || {};
      var state = entry.state || "error";
      var copy = COVERAGE_STATE_LABELS[state] || COVERAGE_STATE_LABELS.error;
      var dimensionLabel = COVERAGE_DIMENSION_LABELS[entry.dimension] || (entry.dimension || "Unknown dimension");
      return [
        entry.source_id || "Not reported",
        dimensionLabel,
        renderBadgeJs(copy.label, copy.tone, "observe-coverage-state-" + state),
        entry.reason || "Not reported",
        entry.next_action || "Not reported",
        renderRefreshedAtJs(entry.refreshed_at),
      ];
    });
    var table = buildDataTable(
      "observe-coverage-table",
      "Telemetry coverage and troubleshooting detail",
      ["Source", "Dimension", "State", "Reason", "Next action", "Refreshed"],
      rows
    );
    setViewContent("coverage", [banner, table]);
  }

  // Maps a wire `view` value (contracts/observe-api.openapi.yaml) back to
  // the internal view id used for DOM ids/CSS classes (the inverse of
  // VIEW_WIRE_NAMES; only "models" differs from its internal id "usage").
  function internalViewFromWire(view) {
    if (view === "models") {
      return "usage";
    }
    return view;
  }

  // Dispatches a successfully parsed `ObserveResponse` body to the render
  // function for whichever view was queried. `diagnostics` and
  // `coverage`/`data` are top-level `ObserveResponse` fields present on
  // every response regardless of the queried view (contracts/observe-api
  // .openapi.yaml), so the active view's diagnostics banner is always
  // rebuilt from the latest response even when the queried view itself is
  // "coverage" (whose row detail comes from the top-level `coverage` array,
  // not from `data`).
  function renderObserveResponse(body) {
    if (!body || typeof body !== "object") {
      return;
    }
    var view = internalViewFromWire(body.view) || currentView;
    if (view === "overview") {
      renderOverview(body.data, body.diagnostics);
    } else if (view === "agents") {
      renderAgents(body.data, body.diagnostics);
    } else if (view === "usage") {
      renderUsage(body.data, body.diagnostics);
    } else if (view === "tools") {
      renderTools(body.data, body.diagnostics, body.bounds);
    } else if (view === "runs") {
      renderRuns(body.data, body.diagnostics, body.bounds);
    } else if (view === "cost") {
      renderCost(
        body.data,
        body.diagnostics,
        body.coverage,
        body.partial_failures,
        body.bounds
      );
    } else if (view === "coverage") {
      renderCoverage(body.coverage, body.diagnostics);
    }
  }

  function buildCostPayload(manual) {
    return {
      view: "cost",
      filters: {
        cost_period_id: appliedFilters.cost_period_id || null,
        cost_component_id: appliedFilters.cost_component_id || null,
        cost_breakdown: appliedFilters.cost_breakdown || "agents",
        cost_agent_key: appliedFilters.cost_agent_key || null,
      },
      refresh: manual === true,
    };
  }

  function fetchObserveData(manual) {
    var myToken = ++requestToken;
    if (activeController) {
      activeController.abort();
    }
    var controller = new AbortController();
    activeController = controller;
    setRefreshStatus("Refreshing\\u2026");

    // POST /api/observe/query with a JSON `ObserveQuery` body, per
    // contracts/observe-api.openapi.yaml -- this endpoint is not a GET with
    // query-string filters. `filters.start`/`filters.end` are required;
    // every other filter is sent as `null` (never omitted-as-empty-string)
    // when unset. `refresh: true` is sent only for an explicit "Refresh now"
    // click, requesting the backend bypass any cache for this fetch.
    var payload = {
      view: VIEW_WIRE_NAMES[currentView] || currentView,
      filters: {
        foundry_resource_id: appliedFilters.foundry_resource_id || null,
        project_resource_id: appliedFilters.project_resource_id || null,
        agent_id: appliedFilters.agent_id || null,
        model: appliedFilters.model || null,
        tool_name: appliedFilters.tool_name || null,
        run_key: appliedFilters.run_key || null,
        start: appliedFilters.start,
        end: appliedFilters.end,
      },
      refresh: manual === true,
    };
    payload = currentView === "cost" ? buildCostPayload(manual) : payload;

    return fetch("/api/observe/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    })
      .then(function (response) {
        // Stale-response suppression: if a newer request has started since
        // this one was issued, silently discard this result even though the
        // fetch itself resolved successfully.
        if (myToken !== requestToken) {
          return null;
        }
        if (!response.ok) {
          setRefreshStatus("Refresh failed");
          return null;
        }
        // Parse and render the body so the page actually reflects the
        // fetched telemetry (not only the refresh-status text). A second
        // stale-response check runs after the (async) JSON parse completes,
        // in case an even newer request started while this one was
        // in-flight/parsing.
        return response.json().then(function (body) {
          if (myToken !== requestToken) {
            return null;
          }
          renderObserveResponse(body);
          setRefreshStatus("Refreshed " + new Date().toISOString());
          return body;
        });
      })
      .catch(function (error) {
        if (error && error.name === "AbortError") {
          return null;
        }
        if (myToken === requestToken) {
          setRefreshStatus("Refresh failed");
        }
        return null;
      });
  }

  function scheduleAutoRefresh() {
    if (refreshTimer) {
      window.clearInterval(refreshTimer);
    }
    refreshTimer = window.setInterval(function () {
      fetchObserveData(false);
    }, AUTO_REFRESH_MS);
  }

  function loadProtectedContent(button) {
    // Explicit, click-triggered only: this function is never called except
    // from a "click" listener on a `[data-observe-load-protected]` button.
    var traceId = button.getAttribute("data-observe-load-protected");
    var sourceResourceId = button.getAttribute("data-observe-source-resource-id");
    // TraceContentRequest requires both `source_resource_id` and `trace_id`
    // (contracts/observe-api.openapi.yaml). Refuse to issue a request that
    // would omit either rather than send an invalid/ambiguous payload.
    if (!traceId || !sourceResourceId) {
      return null;
    }
    var controller = new AbortController();
    return fetch("/api/observe/trace-content", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_resource_id: sourceResourceId, trace_id: traceId }),
      signal: controller.signal,
    });
  }

  function init() {
    appliedFilters = readAppliedFromUrl();
    var form = document.getElementById("observe-filter-form");
    if (form) {
      populateFormFromApplied(form);
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        draftFilters = readDraftFromForm(form);
        var preservedCostFilters = document.getElementById("observe-cost-filter-form") ? {
          cost_period_id: appliedFilters.cost_period_id || "",
          cost_component_id: appliedFilters.cost_component_id || "",
          cost_breakdown: appliedFilters.cost_breakdown || "",
          cost_agent_key: appliedFilters.cost_agent_key || "",
        } : null;
        appliedFilters = draftFilters;
        if (preservedCostFilters) {
          appliedFilters.cost_period_id = preservedCostFilters.cost_period_id;
          appliedFilters.cost_component_id = preservedCostFilters.cost_component_id;
          appliedFilters.cost_breakdown = preservedCostFilters.cost_breakdown;
          appliedFilters.cost_agent_key = preservedCostFilters.cost_agent_key;
        }
        syncUrl();
        fetchObserveData(true);
      });
    }
    var costForm = document.getElementById("observe-cost-filter-form");
    if (costForm) {
      initializeCostPeriodFromServer(costForm);
      populateCostFormFromApplied(costForm);
      var costPeriodField = costForm.querySelector(
        '[data-cost-filter="cost_period_id"]'
      );
      if (costPeriodField) {
        costPeriodField.addEventListener("change", function () {
          resetCostSelectorsForPeriod(costForm);
        });
      }
      ["cost_component_id", "cost_breakdown"].forEach(function (key) {
        var field = costForm.querySelector('[data-cost-filter="' + key + '"]');
        if (field) {
          field.addEventListener("change", function () {
            resetCostAgentSelector(costForm);
          });
        }
      });
      costForm.addEventListener("submit", function (event) {
        event.preventDefault();
        var costDraft = readCostDraftFromForm(costForm);
        COST_FILTER_KEYS.forEach(function (key) {
          appliedFilters[key] = costDraft[key];
        });
        currentView = "cost";
        syncUrl();
        fetchObserveData(true);
      });
    }
    var refreshButton = document.getElementById("observe-refresh-now");
    if (refreshButton) {
      refreshButton.addEventListener("click", function () {
        fetchObserveData(true);
      });
    }
    document.querySelectorAll("[data-observe-load-protected]").forEach(function (button) {
      button.addEventListener("click", function () {
        loadProtectedContent(button);
      });
    });
    document.querySelectorAll("[data-observe-nav-link]").forEach(function (link) {
      link.addEventListener("click", function () {
        currentView = link.getAttribute("data-observe-nav-link");
        syncUrl();
        // Switching views queries a different `ObserveQuery.view`, so the
        // newly active view must be fetched -- otherwise it would only ever
        // show its initial server-rendered snapshot.
        fetchObserveData(false);
      });
    });
    syncUrl();
    scheduleAutoRefresh();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
""".strip()


# ---------------------------------------------------------------------------
# Full page assembly
# ---------------------------------------------------------------------------


def render_observe_page(
    *,
    active_view: str = "overview",
    scope_label: Optional[str] = None,
    overview_metrics: Sequence[Mapping[str, Any]] = (),
    agents: Sequence[Any] = (),
    usage: Sequence[Any] = (),
    tools: Sequence[Any] = (),
    runs: Sequence[Any] = (),
    coverage: Sequence[Any] = (),
    diagnostics: Optional[Mapping[str, Any]] = None,
    tools_bounds: Any = None,
    runs_bounds: Any = None,
    cost_enabled: bool = False,
    cost: Any = None,
    cost_periods: Sequence[Any] = (),
    cost_components: Sequence[Any] = (),
    cost_agent_keys: Sequence[Any] = (),
    cost_coverage: Sequence[Any] = (),
    cost_partial_failures: Sequence[Any] = (),
    cost_bounds: Any = None,
) -> str:
    """Assemble the full Observe HTML document.

    Cost is additive and opt-in: when ``cost_enabled`` is false its navigation,
    controls, and section are omitted. This preserves the existing six-view
    surface for absent cost configuration.
    """
    effective_active_view = (
        active_view if cost_enabled or active_view != "cost" else "overview"
    )
    nav = render_observe_nav(effective_active_view, cost_enabled=cost_enabled)
    filters = render_filter_bar(scope_label)
    overview = render_overview_cards(overview_metrics, diagnostics=diagnostics)
    agents_html = render_agents_table(agents, diagnostics=diagnostics)
    usage_html = render_models_usage_table(usage, diagnostics=diagnostics)
    tools_html = render_tools_table(tools, diagnostics=diagnostics, bounds=tools_bounds)
    runs_html = render_runs_table(runs, diagnostics=diagnostics, bounds=runs_bounds)
    coverage_html = render_coverage_view(coverage, diagnostics)
    cost_section = ""
    if cost_enabled:
        cost_controls = render_cost_controls(
            cost,
            period_options=cost_periods,
            component_options=cost_components,
            agent_options=cost_agent_keys,
        )
        cost_html = render_cost_view(
            cost,
            diagnostics=diagnostics,
            coverage=cost_coverage,
            partial_failures=cost_partial_failures,
            bounds=cost_bounds,
        )
        cost_section = f"""
  <section id="cost" aria-labelledby="cost-heading">
    <h2 id="cost-heading">Cost</h2>
    {cost_controls}
    <div id="cost-content" data-observe-view-content="cost">{cost_html}</div>
  </section>"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>AgentOps Observe</title>
<style>{_OBSERVE_STYLES}</style>
</head>
<body class="observe-root">
<noscript>
  <p class="observe-noscript-banner">
    AgentOps Observe requires JavaScript for filtering, refresh, and protected-content loading.
    Static data below reflects the last server-rendered snapshot.
  </p>
</noscript>
<main id="observe-app" data-observe-active-view="{html_escape(effective_active_view)}">
  <h1>AgentOps Observe</h1>
  {nav}
  {filters}
  <section id="overview" aria-labelledby="overview-heading">
    <h2 id="overview-heading">Overview</h2>
    <div id="overview-content" data-observe-view-content="overview">{overview}</div>
  </section>
  <section id="agents" aria-labelledby="agents-heading">
    <h2 id="agents-heading">Agents</h2>
    <div id="agents-content" data-observe-view-content="agents">{agents_html}</div>
    <div id="agent-detail-content" aria-live="polite" data-observe-agent-detail-content></div>
  </section>
  <section id="usage" aria-labelledby="usage-heading">
    <h2 id="usage-heading">Models and usage</h2>
    <div id="usage-content" data-observe-view-content="usage">{usage_html}</div>
  </section>
  <section id="tools" aria-labelledby="tools-heading">
    <h2 id="tools-heading">Tools</h2>
    <div id="tools-content" data-observe-view-content="tools">{tools_html}</div>
  </section>
  <section id="runs" aria-labelledby="runs-heading">
    <h2 id="runs-heading">Runs</h2>
    <div id="runs-content" data-observe-view-content="runs">{runs_html}</div>
  </section>
  {cost_section}
  <section id="coverage" aria-labelledby="coverage-heading">
    <h2 id="coverage-heading">Telemetry coverage</h2>
    <div id="coverage-content" data-observe-view-content="coverage">{coverage_html}</div>
  </section>
</main>
<script>{_OBSERVE_SCRIPT}</script>
</body>
</html>
"""
