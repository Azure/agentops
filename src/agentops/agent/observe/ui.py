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

from agentops.agent import ui_theme

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Views exposed by the Observe navigation, in display order.
OBSERVE_VIEWS: tuple[str, ...] = ("overview", "agents", "usage", "tools", "runs")

#: Human-readable labels for each view, used by the nav and page title.
OBSERVE_VIEW_LABELS: dict[str, str] = {
    "overview": "Overview",
    "agents": "Agents",
    "usage": "Models and usage",
    "tools": "Tools",
    "runs": "Runs",
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

#: URL state for the opt-in department surface.  The department value is an
#: opaque server-issued token; labels, IDs, user keys, and mapping values are
#: deliberately not accepted as URL state.
ATTRIBUTION_FILTER_QUERY_KEYS: tuple[str, ...] = (
    "department_filter_token",
    "user_filter_token",
    "attribution_group_by",
    "attribution_metric",
    "attribution_cost_period_id",
    "attribution_cost_component_id",
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
    "ambiguous": {"label": "Ambiguous", "tone": "warn"},
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
    "user_attribution": "User attribution",
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
    "ATTRIBUTION_FILTER_QUERY_KEYS",
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
    "render_attribution_controls",
    "render_department_view",
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


def _format_compact_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_seconds(milliseconds: Any) -> str:
    return f"{float(milliseconds) / 1000:,.3f} s"


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


def _render_info_icon(help_text: str, *, extra_class: str = "") -> str:
    classes = f"observe-info-icon {extra_class}".strip()
    escaped = html_escape(help_text)
    return (
        f'<span class="{classes}" role="img" tabindex="0" '
        f'aria-label="{escaped}" title="{escaped}">i</span>'
    )


def _render_header_cell(label: str, help_text: str | None = None) -> str:
    icon = f" {_render_info_icon(help_text)}" if help_text else ""
    return (
        f'<th scope="col" data-label="{html_escape(label)}">'
        f"{html_escape(label)}{icon}</th>"
    )


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
        label = kind or source_id or "source unavailable"
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
    compact = _format_compact_timestamp(moment)
    return (
        f'<time class="observe-refreshed-at" datetime="{html_escape(iso)}" '
        f'title="{html_escape(iso)}">'
        f"{html_escape(label)}: {html_escape(compact)}</time>"
    )


def render_last_seen(value: Any) -> str:
    """Render the most recent observed-telemetry timestamp compactly."""
    moment = _coerce_datetime(value)
    if moment is None:
        return (
            '<span class="observe-last-seen observe-last-seen-missing metric-missing">'
            "\u2014</span>"
        )
    iso = _format_iso(moment)
    compact = _format_compact_timestamp(moment)
    return (
        '<span class="observe-last-seen">'
        f'<time datetime="{html_escape(iso)}" title="{html_escape(iso)}">'
        f"{html_escape(compact)}</time></span>"
    )


def _render_timestamp(value: Any) -> str:
    moment = _coerce_datetime(value)
    if moment is None:
        return '<span class="observe-metric metric-missing">\u2014</span>'
    iso = _format_iso(moment)
    compact = _format_compact_timestamp(moment)
    return (
        f'<time datetime="{html_escape(iso)}" title="{html_escape(iso)}">'
        f"{html_escape(compact)}</time>"
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
    """Render compact input/output token totals."""
    input_html = _render_maybe_missing(input_tokens, missing_text=missing_text)
    output_html = _render_maybe_missing(output_tokens, missing_text=missing_text)
    return (
        '<span class="observe-token-totals">'
        f'<span class="observe-token-in">In: {input_html}</span> '
        f'<span class="observe-token-out">Out: {output_html}</span>'
        "</span>"
    )


def _observed_token_total(input_tokens: Any, output_tokens: Any) -> int | float | None:
    if input_tokens is None and output_tokens is None:
        return None
    return (input_tokens or 0) + (output_tokens or 0)


def _sum_reported(rows: Sequence[Any], field: str) -> int | float | None:
    values = [_get(row, field) for row in rows if _get(row, field) is not None]
    return sum(values) if values else None


def _render_seconds(value: Any, *, missing_text: str = "\u2014") -> str:
    return _render_maybe_missing(
        value,
        formatter=_format_seconds,
        missing_text=missing_text,
    )


def _render_totals_footer(cells: Sequence[str]) -> str:
    if not cells:
        return ""
    return (
        '<tfoot><tr class="observe-totals-row">'
        f'<th scope="row">{cells[0]}</th>'
        f"{''.join(f'<td>{cell}</td>' for cell in cells[1:])}"
        "</tr></tfoot>"
    )


def _render_additional_token_classes(entry: Any) -> str:
    additional = _get(entry, "additional_token_classes", {}) or {}
    values = [
        '<span class="observe-token-class observe-token-class-additional">'
        f'<span class="observe-token-class-label">{html_escape(name)}: </span>'
        f"{_render_maybe_missing(value)}</span>"
        for name, value in additional.items()
    ]
    if _get(entry, "additional_token_classes_truncated", False):
        values.append(
            '<span class="observe-token-classes-truncated">Additional classes truncated</span>'
        )
    if _get(entry, "token_classes_partial", False):
        values.append(
            _render_info_icon(
                "Some telemetry records omitted one or more token-class attributes; "
                "totals include the values that were reported.",
                extra_class="observe-token-classes-partial",
            )
        )
    return "".join(values) if values else _render_maybe_missing(None, missing_text="\u2014")


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
        partial = _render_info_icon(
            "Some telemetry records omitted one or more token-class attributes; "
            "totals include the values that were reported.",
            extra_class="observe-token-classes-partial",
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
# Intentional states (issue #459): loading / empty / partial /
# permission-denied / disconnected / error
# ---------------------------------------------------------------------------

#: The six deliberately designed non-happy-path states an Observe surface can
#: present. Each maps to an accessible, theme-consistent panel via
#: :func:`render_state_panel`. The tone drives the accent color; the glyph is a
#: text marker (never color-only) so the state is legible without color.
OBSERVE_STATE_KINDS: tuple[str, ...] = (
    "loading",
    "empty",
    "partial",
    "permission-denied",
    "disconnected",
    "error",
)

_OBSERVE_STATE_META: dict[str, dict[str, str]] = {
    "loading": {"tone": "info", "glyph": "\u2026", "title": "Loading"},
    "empty": {"tone": "muted", "glyph": "\u2205", "title": "No data"},
    "partial": {"tone": "warn", "glyph": "\u25d1", "title": "Partial data"},
    "permission-denied": {"tone": "warn", "glyph": "\u26bf", "title": "Access needed"},
    "disconnected": {"tone": "muted", "glyph": "\u2205", "title": "Disconnected"},
    "error": {"tone": "crit", "glyph": "\u26a0", "title": "Something went wrong"},
}


def render_state_panel(
    kind: str,
    message: str,
    *,
    title: Optional[str] = None,
    detail: Optional[str] = None,
    actions_html: str = "",
    busy: bool = False,
) -> str:
    """Render one of the six deliberately-designed Observe states.

    ``kind`` must be one of :data:`OBSERVE_STATE_KINDS`. The panel is an
    accessible, theme-consistent surface: it carries ``role="status"`` (with
    ``aria-busy`` for the loading state) so assistive technology announces the
    state, a non-color glyph marker, a heading, the primary ``message``, and an
    optional secondary ``detail`` plus optional ``actions_html``.
    """
    if kind not in _OBSERVE_STATE_META:
        raise ValueError(f"unknown state kind: {kind!r}")
    meta = _OBSERVE_STATE_META[kind]
    tone = meta["tone"]
    heading = title if title is not None else meta["title"]
    role = "alert" if kind == "error" else "status"
    busy_attr = ' aria-busy="true"' if (busy or kind == "loading") else ""
    detail_html = (
        f'<p class="observe-state-detail">{html_escape(detail)}</p>' if detail else ""
    )
    actions = (
        f'<div class="observe-state-actions">{actions_html}</div>' if actions_html else ""
    )
    return (
        f'<div class="observe-state observe-state-{html_escape(kind)} '
        f'observe-tone-{tone}" role="{role}"{busy_attr} '
        f'data-observe-state="{html_escape(kind)}">'
        f'<span class="observe-state-icon" aria-hidden="true">{meta["glyph"]}</span>'
        f'<div class="observe-state-body">'
        f'<p class="observe-state-title">{html_escape(heading)}</p>'
        f'<p class="observe-state-message">{html_escape(message)}</p>'
        f"{detail_html}{actions}"
        "</div>"
        "</div>"
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


def render_observe_nav(
    active_view: str = "overview",
    *,
    cost_enabled: bool = False,
    attribution_enabled: bool = False,
) -> str:
    """Render the Observe navigation as an accessible tab list."""
    items = []
    views = OBSERVE_VIEWS
    if attribution_enabled:
        views += ("departments",)
    if cost_enabled:
        views += ("cost",)
    for view in views:
        label = {"cost": "Cost", "departments": "Departments"}.get(
            view, OBSERVE_VIEW_LABELS.get(view, view.title())
        )
        selected = "true" if view == active_view else "false"
        items.append(
            f'<li role="presentation"><a href="?view={view}" id="observe-tab-{view}" '
            f'data-observe-nav-link="{view}" class="observe-nav-link" role="tab" '
            f'aria-controls="{view}" aria-selected="{selected}">{html_escape(label)}</a></li>'
        )
    return (
        '<nav class="observe-nav" aria-label="Observe views">'
        f'<ul class="observe-nav-list" role="tablist">{"".join(items)}</ul>'
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
             data-draft-filter="foundry_resource_id" placeholder="All in current scope" autocomplete="off" />
    </label>
    <label for="observe-filter-project_resource_id">Project
      <input type="text" id="observe-filter-project_resource_id" name="project_resource_id"
             data-draft-filter="project_resource_id" placeholder="All in current scope" autocomplete="off" />
    </label>
    <label for="observe-filter-agent_id">Agent
      <input type="text" id="observe-filter-agent_id" name="agent_id"
             data-draft-filter="agent_id" placeholder="All agents" autocomplete="off" />
    </label>
    <label for="observe-filter-model">Model
      <input type="text" id="observe-filter-model" name="model"
             data-draft-filter="model" placeholder="All models" autocomplete="off" />
    </label>
    <label for="observe-filter-tool_name">Tool
      <input type="text" id="observe-filter-tool_name" name="tool_name"
             data-draft-filter="tool_name" placeholder="All tools" autocomplete="off" />
    </label>
    <label for="observe-filter-run_key">Run key
      <input type="text" id="observe-filter-run_key" name="run_key"
             data-draft-filter="run_key" placeholder="All runs" autocomplete="off" />
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


def _render_metric_delta(delta: Any) -> str:
    """Render an optional delta chip for a KPI card.

    ``delta`` is a mapping with ``value`` (the display text, already formatted),
    an optional ``direction`` (``"up"``/``"down"``/``"flat"``), and an optional
    ``tone`` (``"ok"``/``"warn"``/``"crit"``/``"muted"``). Direction only sets a
    non-color glyph; tone sets the accent. Nothing here is color-only.
    """
    if not delta:
        return ""
    value = str(_get(delta, "value", ""))
    if not value:
        return ""
    direction = str(_get(delta, "direction", "flat") or "flat")
    tone = str(_get(delta, "tone", "muted") or "muted")
    glyph = {"up": "\u2191", "down": "\u2193", "flat": "\u2192"}.get(direction, "\u2192")
    label = str(_get(delta, "label", "") or "")
    label_html = f' <span class="observe-card-delta-label">{html_escape(label)}</span>' if label else ""
    return (
        f'<span class="observe-card-delta observe-tone-{html_escape(tone)}" '
        f'data-direction="{html_escape(direction)}">'
        f'<span class="observe-card-delta-glyph" aria-hidden="true">{glyph}</span> '
        f"{html_escape(value)}{label_html}</span>"
    )


def _render_metric_card(
    title: str,
    value_html: str,
    *,
    source: Any = None,
    refreshed_at: Any = None,
    tone: Optional[str] = None,
    delta: Any = None,
    caption: Optional[str] = None,
    series: Optional[Sequence[Mapping[str, Any]]] = None,
    unit: str = "",
) -> str:
    source_html = render_source_label(source) if source is not None else ""
    refreshed_html = render_refreshed_at(refreshed_at) if refreshed_at is not None else ""
    tone_class = f" observe-tone-{html_escape(tone)}" if tone else ""
    delta_html = _render_metric_delta(delta)
    caption_html = (
        f'<p class="observe-card-caption">{html_escape(caption)}</p>' if caption else ""
    )
    spark_html = ""
    if series:
        # A compact, inline sparkline makes the trend first-class on the card
        # itself. render_trend_chart already emits an accessible <svg role="img">
        # plus a visually-hidden data table, so the sparkline is not color-only.
        spark_html = (
            '<div class="observe-card-spark">'
            + render_trend_chart(f"{title} trend", series, width=240, height=64, unit=unit)
            + "</div>"
        )
    footer = (
        f'<div class="observe-card-footer">{source_html}{refreshed_html}</div>'
        if (source_html or refreshed_html)
        else ""
    )
    return (
        f'<div class="observe-card observe-metric-card{tone_class}" role="group" '
        f'aria-label="{html_escape(title)}">'
        f'<h3 class="observe-card-title">{html_escape(title)}</h3>'
        f'<p class="observe-card-value">{value_html}</p>'
        f"{delta_html}{caption_html}{spark_html}{footer}"
        "</div>"
    )


def render_overview_cards(
    metrics: Sequence[Mapping[str, Any]],
    *,
    diagnostics: Optional[Mapping[str, Any]] = None,
    trends: Optional[Sequence[Mapping[str, Any]]] = None,
) -> str:
    """Render the Overview executive dashboard: KPI cards plus trend charts.

    Each entry in ``metrics`` is a mapping with ``title``, ``value``
    (rendered through the zero-vs-missing helper unless ``value_html`` is
    supplied directly), optional ``unit``, ``source``, ``refreshed_at`` and the
    new, purely-additive KPI keys ``tone`` (accent), ``delta`` (a delta chip
    mapping), ``caption`` (supporting text), and ``series`` (a compact inline
    sparkline).

    ``trends`` is an optional sequence of first-class trend charts rendered
    below the KPI grid. Each entry is ``{"title", "series", optional "unit"}``
    matching :func:`render_trend_chart`. Invocation, failure, latency, token and
    coverage trends are surfaced here.
    """
    if not metrics and not trends:
        return (
            '<div class="observe-overview-cards observe-empty-state">'
            "<p class=\"observe-empty\">No data found for the selected filters.</p></div>"
        )
    cards = []
    for metric in metrics:
        title = str(metric.get("title", ""))
        unit = str(metric.get("unit", ""))
        if "value_html" in metric:
            value_html = metric["value_html"]
        else:
            value_html = _render_maybe_missing(metric.get("value"), suffix=unit)
        cards.append(
            _render_metric_card(
                title,
                value_html,
                source=metric.get("source"),
                refreshed_at=metric.get("refreshed_at"),
                tone=metric.get("tone"),
                delta=metric.get("delta"),
                caption=metric.get("caption"),
                series=metric.get("series"),
                unit=unit,
            )
        )
    cards_html = (
        f'<div class="observe-overview-cards">{"".join(cards)}</div>' if cards else ""
    )
    trends_html = ""
    if trends:
        charts = []
        for trend in trends:
            charts.append(
                '<div class="observe-trend-tile">'
                + render_trend_chart(
                    str(trend.get("title", "")),
                    trend.get("series", ()),
                    unit=str(trend.get("unit", "")),
                )
                + "</div>"
            )
        trends_html = (
            '<section class="observe-overview-trends" '
            'aria-label="Operational trends">'
            '<h3 class="observe-overview-trends-title aos-section-title">Trends</h3>'
            f'<div class="observe-trend-grid">{"".join(charts)}</div>'
            "</section>"
        )
    return f"{cards_html}{trends_html}"


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
    label = "Unclassified" if kind == "unknown" else str(kind).replace("_", " ").title()
    badge = _render_badge(label, tone, extra_class="observe-source-kind-badge")
    if kind != "unknown":
        return badge
    return (
        '<span class="observe-inline-help" '
        'title="Source kind could not be classified from the available telemetry attributes.">'
        f"{badge}</span>"
    )


def _render_identity_availability(agent_id: Any) -> str:
    if agent_id:
        return _render_badge("Identity available", "ok", extra_class="observe-identity-badge")
    return _render_badge("Identity unavailable", "muted", extra_class="observe-identity-badge")


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
    if not agents:
        return (
            '<div class="observe-agents-view observe-empty-state">'
            '<p class="observe-empty">No data found for the selected filters.</p></div>'
        )
    rows = []
    for agent in agents:
        name = _get(agent, "agent_name") or _get(agent, "agent_id") or "\u2014"
        input_tokens = _get(agent, "input_tokens")
        output_tokens = _get(agent, "output_tokens")
        rows.append(
            "<tr>"
            f"<td>{html_escape(name)} "
            f"{_render_identity_availability(_get(agent, 'agent_id'))}</td>"
            f'<td title="{html_escape(_get(agent, "source_id") or "")}">'
            f"{_render_source_kind_badge(_get(agent, 'source_kind'))}</td>"
            f"<td>{html_escape(_get(agent, 'model') or '—')}</td>"
            f"<td>{render_last_seen(_get(agent, 'last_seen'))}</td>"
            f"<td>{_render_maybe_missing(_get(agent, 'invocations'), missing_text='—')}</td>"
            f"<td>{_render_failure_rate(_get(agent, 'invocations'), _get(agent, 'failures'))}</td>"
            f"<td>{_render_seconds(_get(agent, 'p95_latency_ms'))}</td>"
            f"<td>{_render_maybe_missing(input_tokens, missing_text='—')}</td>"
            f"<td>{_render_maybe_missing(output_tokens, missing_text='—')}</td>"
            f"<td>{_render_maybe_missing(_observed_token_total(input_tokens, output_tokens), missing_text='—')}</td>"
            f"<td>{_render_maybe_missing(_get(agent, 'cache_read_tokens'), missing_text='—')}</td>"
            f"<td>{_render_maybe_missing(_get(agent, 'cache_write_tokens'), missing_text='—')}</td>"
            f"<td>{_render_maybe_missing(_get(agent, 'reasoning_tokens'), missing_text='—')}</td>"
            "</tr>"
        )
    total_invocations = _sum_reported(agents, "invocations")
    total_failures = _sum_reported(agents, "failures")
    total_input = _sum_reported(agents, "input_tokens")
    total_output = _sum_reported(agents, "output_tokens")
    footer = _render_totals_footer(
        (
            f'Totals {_render_info_icon("Totals cover the rows currently displayed.")}',
            "\u2014",
            "\u2014",
            "\u2014",
            _render_maybe_missing(total_invocations, missing_text="\u2014"),
            _render_failure_rate(total_invocations, total_failures),
            "\u2014",
            _render_maybe_missing(total_input, missing_text="\u2014"),
            _render_maybe_missing(total_output, missing_text="\u2014"),
            _render_maybe_missing(
                _observed_token_total(total_input, total_output), missing_text="\u2014"
            ),
            _render_maybe_missing(
                _sum_reported(agents, "cache_read_tokens"), missing_text="\u2014"
            ),
            _render_maybe_missing(
                _sum_reported(agents, "cache_write_tokens"), missing_text="\u2014"
            ),
            _render_maybe_missing(
                _sum_reported(agents, "reasoning_tokens"), missing_text="\u2014"
            ),
        )
    )
    token_help = "Observed token usage from telemetry; this is not billing data."
    return f"""
<table class="observe-agents-table" aria-label="Agents observed in the selected range">
  <caption class="visually-hidden">Agents observed in the selected range</caption>
  <thead>
    <tr>
      {_render_header_cell("Agent")}
      {_render_header_cell("Source")}
      {_render_header_cell("Model", "Model identifier reported by response telemetry.")}
      {_render_header_cell("Last seen", "Most recent telemetry in the selected range; not agent lifecycle status.")}
      {_render_header_cell("Invocations")}
      {_render_header_cell("Failure rate", "Failures divided by invocations.")}
      {_render_header_cell("p95 latency", "95% of observed invocations completed in this time or less.")}
      {_render_header_cell("Input tokens", token_help)}
      {_render_header_cell("Output tokens", token_help)}
      {_render_header_cell("Total tokens", token_help)}
      {_render_header_cell("Cache read", token_help)}
      {_render_header_cell("Cache write", token_help)}
      {_render_header_cell("Reasoning", token_help)}
    </tr>
  </thead>
  <tbody>{"".join(rows)}</tbody>
  {footer}
</table>
""".strip()


# ---------------------------------------------------------------------------
# Department attribution (issue #444 T029)
# ---------------------------------------------------------------------------


_ATTRIBUTION_COST_UNAVAILABLE = (
    "Cost attribution is unavailable. Configure a valid cost model and allocatable cost "
    "before selecting Cost."
)


def render_attribution_controls(
    attribution: Any = None,
    *,
    cost_available: bool = False,
    period_options: Sequence[Any] = (),
    component_options: Sequence[Any] = (),
) -> str:
    """Render opt-in department and delegated-user attribution selectors.

    These controls are emitted only by :func:`render_observe_page` when
    attribution is explicitly enabled. Department selection itself is carried
    only by a server-issued opaque URL token.
    """

    selected_metric = str(_get(attribution, "metric") or "usage")
    selected_group = str(_get(attribution, "group_by") or "department")
    summary = _get(attribution, "summary", {}) or {}
    selected_period = _get(summary, "period_id")
    selected_component = _get(summary, "component_id")
    if not period_options and selected_period:
        period_options = ({"id": selected_period, "label": selected_period},)
    if not component_options and selected_component:
        component_options = ({"id": selected_component, "label": selected_component},)
    cost_option = (
        '<option value="cost" selected>Cost</option>'
        if selected_metric == "cost" and cost_available
        else '<option value="cost">Cost</option>'
        if cost_available
        else '<option value="cost" disabled>Cost</option>'
    )
    usage_selected = " selected" if selected_metric != "cost" else ""
    cost_fields = ""
    if cost_available:
        cost_fields = f"""
    <label for="observe-attribution-cost-period">Cost period
      <select id="observe-attribution-cost-period" data-attribution-filter="cost_period_id">
        {_cost_options(period_options, id_key="id", selected=selected_period, all_label="Select period")}
      </select>
    </label>
    <label for="observe-attribution-cost-component">Cost component
      <select id="observe-attribution-cost-component" data-attribution-filter="cost_component_id">
        {_cost_options(component_options, id_key="id", selected=selected_component, all_label="Select component")}
      </select>
    </label>"""
    unavailable = (
        ""
        if cost_available
        else f'<p class="observe-hint observe-attribution-cost-unavailable">{_ATTRIBUTION_COST_UNAVAILABLE}</p>'
    )
    return f"""
<form class="observe-attribution-filter-bar" id="observe-attribution-filter-form"
      aria-label="Department attribution selectors">
  <div class="observe-filter-fields">
    <label for="observe-attribution-metric">Measure
      <select id="observe-attribution-metric" data-attribution-filter="metric">
        <option value="usage"{usage_selected}>Usage</option>
        {cost_option}
      </select>
    </label>
    <label for="observe-attribution-group">Attribution level
      <select id="observe-attribution-group" data-attribution-filter="group_by">
        <option value="department"{" selected" if selected_group != "user" else ""}>Departments</option>
        <option value="user"{" selected" if selected_group == "user" else ""}>Users in selected department</option>
      </select>
    </label>
    {cost_fields}
  </div>
  {unavailable}
  <div class="observe-filter-actions">
    <button type="submit" id="observe-apply-attribution-filters" class="observe-apply-button">
      Apply attribution selectors
    </button>
  </div>
</form>
""".strip()


def _render_attribution_usage(usage: Any) -> str:
    return (
        '<dl class="observe-attribution-usage">'
        f"<div><dt>Invocations</dt><dd>{_render_maybe_missing(_get(usage, 'invocations'))}</dd></div>"
        f"<div><dt>Input tokens</dt><dd>{_render_maybe_missing(_get(usage, 'input_tokens'))}</dd></div>"
        f"<div><dt>Output tokens</dt><dd>{_render_maybe_missing(_get(usage, 'output_tokens'))}</dd></div>"
        f"<div><dt>Tool invocations</dt><dd>{_render_maybe_missing(_get(usage, 'tool_invocations'))}</dd></div>"
        f"<div><dt>Active session</dt><dd>{_render_maybe_missing(_get(usage, 'active_session_seconds'), suffix=' s')}</dd></div>"
        "</dl>"
    )


def _render_attribution_summary(summary: Any, *, group_by: str = "department") -> str:
    metric = str(_get(summary, "metric") or "usage")
    users = _render_maybe_missing(_get(summary, "distinct_users"))
    omitted = _render_maybe_missing(_get(summary, "omitted_users"))
    level = "User" if group_by == "user" else "Department"
    if metric == "cost":
        currency = _get(summary, "currency")
        return f"""
<section class="observe-attribution-summary" aria-labelledby="department-summary-heading">
  <h3 id="department-summary-heading">{level} cost summary</h3>
  <dl>
    <div><dt>Declared total</dt><dd>{_cost_declared_amount(_get(summary, "declared_total"), currency)}</dd></div>
    <div><dt>Attributed cost</dt><dd>{_cost_amount(_get(summary, "attributed_amount"), currency)}</dd></div>
    <div><dt>Unmapped cost</dt><dd>{_cost_amount(_get(summary, "unattributed_amount"), currency)}</dd></div>
    <div><dt>Unallocated cost</dt><dd>{_cost_amount(_get(summary, "unallocated_amount"), currency)}</dd></div>
    <div><dt>Distinct users</dt><dd>{users}</dd></div>
    <div><dt>Omitted users</dt><dd>{omitted}</dd></div>
  </dl>
  <h4>Unmapped usage</h4>{_render_attribution_usage(_get(summary, "unattributed_usage", {}) or {})}
</section>""".strip()
    unattributed = _get(summary, "unattributed", {}) or {}
    return f"""
<section class="observe-attribution-summary" aria-labelledby="department-summary-heading">
  <h3 id="department-summary-heading">{level} usage summary</h3>
  <div class="observe-attribution-summary-columns">
    <div><h4>Total usage</h4>{_render_attribution_usage(_get(summary, "total", {}) or {})}</div>
    <div><h4>Attributed usage</h4>{_render_attribution_usage(_get(summary, "attributed", {}) or {})}</div>
    <div><h4>Unmapped usage</h4>{_render_attribution_usage(unattributed)}</div>
  </div>
  <p><strong>Distinct users:</strong> {users} <strong>Omitted users:</strong> {omitted}</p>
</section>""".strip()


def _render_attribution_partial_failures(partial_failures: Sequence[Any]) -> str:
    if not partial_failures:
        return ""
    items = "".join(
        "<li>"
        f"<strong>{html_escape(_get(item, 'source_id') or 'Not reported')}</strong> "
        f"({html_escape(_cost_label(_get(item, 'status')))}) — "
        f"{html_escape(_get(item, 'reason') or 'No reason reported.')} "
        f"<strong>Next action:</strong> "
        f"{html_escape(_get(item, 'next_action') or 'No follow-up action reported.')}"
        "</li>"
        for item in partial_failures
    )
    return (
        '<section class="observe-attribution-partial-failures" '
        'aria-labelledby="observe-attribution-partial-failures-title">'
        '<h3 id="observe-attribution-partial-failures-title">Partial source failures</h3>'
        "<p>Successful source evidence remains visible; totals may be incomplete.</p>"
        f"<ul>{items}</ul></section>"
    )


def _render_attribution_coverage(
    coverage: Sequence[Any], *, group_by: str
) -> str:
    if not coverage:
        return ""
    rendered_rows = []
    for entry in coverage:
        state = str(_get(entry, "state") or "error")
        copy = COVERAGE_STATE_LABELS.get(state, COVERAGE_STATE_LABELS["error"])
        metric = str(_get(entry, "metric") or "usage").title()
        component = _get(entry, "component_id")
        measure = f"{metric} / {component}" if component else metric
        counts = (
            ("Eligible", _get(entry, "eligible_records")),
            ("Identified", _get(entry, "identified_records")),
            ("Mapped", _get(entry, "mapped_records")),
            ("Unattributed", _get(entry, "unattributed_records")),
            ("Ambiguous", _get(entry, "ambiguous_records")),
            ("Returned", _get(entry, "returned_records")),
        )
        count_text = "; ".join(
            f"{label}: {_render_maybe_missing(value)}" for label, value in counts
        )
        rendered_rows.append(
            "<tr>"
            f"<td>{html_escape(_get(entry, 'source_id') or 'Not reported')}</td>"
            f"<td>{html_escape(measure)}</td>"
            f"<td>{_render_badge(copy['label'], copy['tone'], extra_class=f'observe-coverage-state-{html_escape(state)}')}</td>"
            f"<td>{count_text}</td>"
            f"<td>{html_escape(_get(entry, 'reason') or 'Not reported')}</td>"
            f"<td>{html_escape(_get(entry, 'next_action') or 'Not reported')}</td>"
            "</tr>"
        )
    level = "User" if group_by == "user" else "Department"
    return (
        '<section class="observe-attribution-coverage" '
        'aria-labelledby="observe-attribution-coverage-title">'
        f'<h3 id="observe-attribution-coverage-title">{level} attribution coverage</h3>'
        "<p>Missing, inaccessible, ambiguous, or protected identity evidence is not zero usage.</p>"
        '<table aria-label="Attribution coverage by source and measure">'
        "<thead><tr><th>Source</th><th>Measure / component</th><th>State</th>"
        "<th>Record counts</th><th>Reason</th><th>Next action</th></tr></thead>"
        f"<tbody>{''.join(rendered_rows)}</tbody></table></section>"
    )


def render_department_view(
    attribution: Any,
    *,
    diagnostics: Optional[Mapping[str, Any]] = None,
    coverage: Sequence[Any] = (),
    partial_failures: Sequence[Any] = (),
    bounds: Any = None,
) -> str:
    """Render department or protected user attribution and exact reconciliation."""

    if attribution is None:
        return (
            '<div class="observe-department-view">'
            '<p class="observe-empty">Attribution data was not returned. '
            "This is missing or protected evidence, not zero usage.</p>"
            f"{_render_attribution_coverage(coverage, group_by='department')}"
            f"{_render_attribution_partial_failures(partial_failures)}"
            "</div>"
        )
    metric = str(_get(attribution, "metric") or "usage")
    group_by = str(_get(attribution, "group_by") or "department")
    rows = _get(attribution, "rows", ()) or ()
    rendered_rows: list[str] = []
    user_rank = 0
    for row in rows:
        kind = str(_get(row, "kind") or "")
        if group_by == "department" and kind != "department":
            continue
        if group_by == "user" and kind not in {"user", "other_users"}:
            continue
        if kind == "user":
            user_rank += 1
        token = _get(row, "filter_token")
        if kind == "user":
            raw_identity = html_escape(_get(row, "raw_identity") or "Identity not reported")
            user_key = html_escape(_get(row, "user_key") or "Pseudonym not reported")
            label = f"{raw_identity}<br><code>{user_key}</code>"
        elif kind == "other_users":
            label = "Other users"
        else:
            label = html_escape(_get(row, "department_label") or "Department")
        if token:
            query = {
                "view": "departments",
                "attribution_metric": metric,
                "attribution_group_by": "user",
            }
            if kind == "user":
                query["user_filter_token"] = str(token)
            else:
                query["department_filter_token"] = str(token)
            summary = _get(attribution, "summary", {}) or {}
            if metric == "cost":
                if _get(summary, "period_id"):
                    query["attribution_cost_period_id"] = str(_get(summary, "period_id"))
                if _get(summary, "component_id"):
                    query["attribution_cost_component_id"] = str(
                        _get(summary, "component_id")
                    )
            href = "?" + urlencode(query)
            label = f'<a class="observe-attribution-link" href="{html_escape(href)}">{label}</a>'
        usage = _get(row, "usage", {}) or {}
        if metric == "cost":
            cost = _get(row, "cost")
            measure = (
                _cost_amount(_get(cost, "amount"), _get(cost, "currency"))
                if cost is not None
                else f'<span class="metric-missing">Cost unavailable for this {kind.replace("_", " ")}</span>'
            )
        else:
            measure = _render_maybe_missing(_get(usage, "invocations"), suffix=" invocations")
        rendered_rows.append(
            "<tr>"
            f"<th scope=\"row\">{label}</th>"
            f"<td>{html_escape(f'Rank {user_rank}' if kind == 'user' else _get(row, 'member_count') or 'Omitted users')}</td>"
            f"<td>{measure}</td>"
            f"<td>{_render_maybe_missing(_get(usage, 'input_tokens'))}</td>"
            f"<td>{_render_maybe_missing(_get(usage, 'output_tokens'))}</td>"
            "</tr>"
        )
    table = (
        f'<p class="observe-empty">No {group_by} attribution data found. This is not reported usage, not zero usage.</p>'
        if not rendered_rows
        else (
            f'<table class="observe-department-table" aria-label="{html_escape(group_by.title())} attribution">'
            f"<thead><tr><th scope=\"col\">{'Eligible principal' if group_by == 'user' else 'Department'}</th>"
            f"<th scope=\"col\">{'Rank context' if group_by == 'user' else 'Members'}</th>"
            f"<th scope=\"col\">{'Allocated cost' if metric == 'cost' else 'Usage'}</th>"
            "<th scope=\"col\">Input tokens</th><th scope=\"col\">Output tokens</th></tr></thead>"
            f"<tbody>{''.join(rendered_rows)}</tbody></table>"
        )
    )
    summary = _render_attribution_summary(
        _get(attribution, "summary", {}) or {}, group_by=group_by
    )
    coverage_html = _render_attribution_coverage(coverage, group_by=group_by)
    failures_html = _render_attribution_partial_failures(partial_failures)
    bounds_html = _render_bounds_notice(bounds, rows_shown=len(rendered_rows)) if bounds else ""
    if bounds and bool(_get(bounds, "truncated")):
        bounds_html += (
            '<p class="observe-hint observe-attribution-truncated">'
            "Results are truncated to the highest-ranked users plus Other users."
            "</p>"
        )
    ranking_html = (
        '<p class="observe-hint observe-attribution-ranking">'
        f"Users are ranked by {'allocated cost' if metric == 'cost' else 'invocations'}; "
        "ties are ordered by pseudonymous key. Other users preserves omitted totals."
        "</p>"
        if group_by == "user"
        else ""
    )
    selected_html = (
        '<p class="observe-protected-context"><strong>Selected eligible principal:</strong> '
        "This delegated, private view contains only the selected principal context.</p>"
        if group_by == "user"
        and str(_get(attribution, "access_boundary") or "") == "delegated"
        and len([row for row in rows if _get(row, "kind") == "user"]) == 1
        else ""
    )
    return (
        '<div class="observe-department-view">'
        f"{selected_html}{summary}{ranking_html}{table}{bounds_html}{coverage_html}{failures_html}"
        "</div>"
    )


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
            row_count = str(rows_shown) if rows_shown is not None else "\u2014"
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
        text = f"Showing {shown} rows." if shown is not None else "Showing available rows."
    else:
        text = (
            f"Showing {shown} of {total} rows in scope"
            if shown is not None
            else f"{total} rows in scope"
        )
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
    supplemental = (
        f"{_render_cost_bounds(bounds)}"
        f"{_render_cost_coverage(coverage)}"
        f"{_render_cost_partial_failures(partial_failures)}"
    )
    if not cost:
        return (
            '<div class="observe-cost-view observe-empty-state">'
            '<p class="observe-empty">No cost allocation data reported.</p>'
            f"{supplemental}"
            f'<p class="observe-cost-breakdown-warning">{html_escape(COST_BREAKDOWN_WARNING)}</p>'
            f'<p class="observe-cost-disclaimer">{html_escape(COST_DISCLAIMER)}</p></div>'
        )
    return (
        '<div class="observe-cost-view">'
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
        text = f"Showing {rows_shown} rows."
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
    notice = _render_bounds_notice(bounds, rows_shown=len(tools))
    if not tools:
        return (
            f'{notice}<div class="observe-tools-view observe-empty-state">'
            '<p class="observe-empty">No tool activity was found for the selected filters. '
            'Tool attribution may not be reported for this selection.</p></div>'
        )
    rows = []
    for tool in tools:
        agent = (
            _get(tool, "agent_name")
            or _get(tool, "agent_id")
            or _get(tool, "agent_key")
            or "\u2014"
        )
        rows.append(
            "<tr>"
            f"<td>{html_escape(_get(tool, 'tool_name') or '—')}</td>"
            f"<td>{html_escape(agent)}</td>"
            f"<td>{html_escape(_get(tool, 'source_id') or '—')}</td>"
            f"<td>{_render_source_kind_badge(_get(tool, 'source_kind'))}</td>"
            f"<td>{render_last_seen(_get(tool, 'last_seen'))}</td>"
            f"<td>{_render_maybe_missing(_get(tool, 'invocations'), missing_text='—')}</td>"
            f"<td>{_render_maybe_missing(_get(tool, 'failures'), missing_text='—')}</td>"
            f"<td>{_render_seconds(_get(tool, 'p95_latency_ms'))}</td>"
            "</tr>"
        )
    total_invocations = _sum_reported(tools, "invocations")
    total_failures = _sum_reported(tools, "failures")
    footer = _render_totals_footer(
        (
            f'Totals {_render_info_icon("Totals cover the rows currently displayed.")}',
            "\u2014",
            "\u2014",
            "\u2014",
            "\u2014",
            _render_maybe_missing(total_invocations, missing_text="\u2014"),
            _render_maybe_missing(total_failures, missing_text="\u2014"),
            "\u2014",
        )
    )
    return f"""
{notice}
<table class="observe-tools-table" aria-label="Tools observed in the selected range">
  <caption class="visually-hidden">Tools observed in the selected range</caption>
  <thead>
    <tr>
      {_render_header_cell("Tool")}
      {_render_header_cell("Agent")}
      {_render_header_cell("Source")}
      {_render_header_cell("Runtime")}
      {_render_header_cell("Last seen", "Most recent telemetry in the selected range.")}
      {_render_header_cell("Invocations")}
      {_render_header_cell("Failures")}
      {_render_header_cell("p95 latency", "95% of observed tool invocations completed in this time or less.")}
    </tr>
  </thead>
  <tbody>{"".join(rows)}</tbody>
  {footer}
</table>
""".strip()


def render_runs_table(
    runs: Sequence[Any],
    *,
    diagnostics: Optional[Mapping[str, Any]] = None,
    bounds: Any = None,
) -> str:
    """Render range-scoped correlated executions and their observed token totals."""
    notice = _render_bounds_notice(bounds, rows_shown=len(runs))
    if not runs:
        return (
            f'{notice}<div class="observe-runs-view observe-empty-state">'
            '<p class="observe-empty">No runs could be correlated for the selected filters. '
            'Run correlation may not be reported for this selection.</p></div>'
        )
    rows = []
    for run in runs:
        agent = (
            _get(run, "agent_name")
            or _get(run, "agent_id")
            or _get(run, "agent_key")
            or "\u2014"
        )
        input_tokens = _get(run, "input_tokens")
        output_tokens = _get(run, "output_tokens")
        rows.append(
            "<tr>"
            f"<td>{html_escape(_get(run, 'run_key') or '—')}</td>"
            f"<td>{html_escape(_get(run, 'run_key_kind') or '—')}</td>"
            f"<td>{html_escape(agent)}</td>"
            f"<td>{html_escape(_get(run, 'source_id') or '—')}</td>"
            f"<td>{_render_source_kind_badge(_get(run, 'source_kind'))}</td>"
            f"<td>{_render_timestamp(_get(run, 'started_at'))}</td>"
            f"<td>{_render_seconds(_get(run, 'duration_ms'))}</td>"
            f"<td>{html_escape(_get(run, 'status') or '—')}</td>"
            f"<td>{_render_maybe_missing(_get(run, 'turns'), missing_text='—')}</td>"
            f"<td>{_render_maybe_missing(_get(run, 'tool_invocations'), missing_text='—')}</td>"
            f"<td>{_render_maybe_missing(input_tokens, missing_text='—')}</td>"
            f"<td>{_render_maybe_missing(output_tokens, missing_text='—')}</td>"
            f"<td>{_render_maybe_missing(_observed_token_total(input_tokens, output_tokens), missing_text='—')}</td>"
            f"<td>{_render_maybe_missing(_get(run, 'cache_read_tokens'), missing_text='—')}</td>"
            f"<td>{_render_maybe_missing(_get(run, 'cache_write_tokens'), missing_text='—')}</td>"
            f"<td>{_render_maybe_missing(_get(run, 'reasoning_tokens'), missing_text='—')}</td>"
            "</tr>"
        )
    total_input = _sum_reported(runs, "input_tokens")
    total_output = _sum_reported(runs, "output_tokens")
    footer = _render_totals_footer(
        (
            f'Totals {_render_info_icon("Totals cover the rows currently displayed.")}',
            "\u2014",
            "\u2014",
            "\u2014",
            "\u2014",
            "\u2014",
            _render_seconds(_sum_reported(runs, "duration_ms")),
            "\u2014",
            _render_maybe_missing(_sum_reported(runs, "turns"), missing_text="\u2014"),
            _render_maybe_missing(
                _sum_reported(runs, "tool_invocations"), missing_text="\u2014"
            ),
            _render_maybe_missing(total_input, missing_text="\u2014"),
            _render_maybe_missing(total_output, missing_text="\u2014"),
            _render_maybe_missing(
                _observed_token_total(total_input, total_output), missing_text="\u2014"
            ),
            _render_maybe_missing(
                _sum_reported(runs, "cache_read_tokens"), missing_text="\u2014"
            ),
            _render_maybe_missing(
                _sum_reported(runs, "cache_write_tokens"), missing_text="\u2014"
            ),
            _render_maybe_missing(
                _sum_reported(runs, "reasoning_tokens"), missing_text="\u2014"
            ),
        )
    )
    token_help = "Observed token usage from telemetry; this is not billing data."
    return f"""
{notice}
<table class="observe-runs-table" aria-label="Runs observed in the selected range">
  <caption class="visually-hidden">Runs observed in the selected range; start, duration, and turns are range-scoped.</caption>
  <thead>
    <tr>
      {_render_header_cell("Run key")}
      {_render_header_cell("Correlation", "Telemetry key used to group this run.")}
      {_render_header_cell("Agent")}
      {_render_header_cell("Source")}
      {_render_header_cell("Runtime")}
      {_render_header_cell("Started in range", "First observed activity within the selected range.")}
      {_render_header_cell("Duration in range", "Elapsed time between first and last observed activity in the selected range.")}
      {_render_header_cell("Status")}
      {_render_header_cell("Turns in range", "Turns observed within the selected range.")}
      {_render_header_cell("Tool invocations")}
      {_render_header_cell("Input tokens", token_help)}
      {_render_header_cell("Output tokens", token_help)}
      {_render_header_cell("Total tokens", token_help)}
      {_render_header_cell("Cache read", token_help)}
      {_render_header_cell("Cache write", token_help)}
      {_render_header_cell("Reasoning", token_help)}
    </tr>
  </thead>
  <tbody>{"".join(rows)}</tbody>
  {footer}
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
    if not usage:
        return (
            '<div class="observe-usage-view observe-empty-state">'
            '<p class="observe-empty">No data found for the selected filters.</p></div>'
        )
    rows = []
    for entry in usage:
        model = _get(entry, "model") or "\u2014"
        input_tokens = _get(entry, "input_tokens")
        output_tokens = _get(entry, "output_tokens")
        rows.append(
            "<tr>"
            f"<td>{html_escape(model)}</td>"
            f"<td>{html_escape(_get(entry, 'deployment') or '—')}</td>"
            f"<td>{_render_maybe_missing(_get(entry, 'requests'), missing_text='—')}</td>"
            f"<td>{_render_failure_rate(_get(entry, 'requests'), _get(entry, 'failures'))}</td>"
            f"<td>{_render_seconds(_get(entry, 'p95_latency_ms'))}</td>"
            f"<td>{_render_maybe_missing(input_tokens, missing_text='—')}</td>"
            f"<td>{_render_maybe_missing(output_tokens, missing_text='—')}</td>"
            f"<td>{_render_maybe_missing(_observed_token_total(input_tokens, output_tokens), missing_text='—')}</td>"
            f"<td>{_render_maybe_missing(_get(entry, 'cache_read_tokens'), missing_text='—')}</td>"
            f"<td>{_render_maybe_missing(_get(entry, 'cache_write_tokens'), missing_text='—')}</td>"
            f"<td>{_render_maybe_missing(_get(entry, 'reasoning_tokens'), missing_text='—')}</td>"
            f"<td>{_render_additional_token_classes(entry)}</td>"
            f"<td>{render_last_seen(_get(entry, 'last_seen'))}</td>"
            "</tr>"
        )
    total_requests = _sum_reported(usage, "requests")
    total_failures = _sum_reported(usage, "failures")
    total_input = _sum_reported(usage, "input_tokens")
    total_output = _sum_reported(usage, "output_tokens")
    footer = _render_totals_footer(
        (
            f'Totals {_render_info_icon("Totals cover the rows currently displayed.")}',
            "\u2014",
            _render_maybe_missing(total_requests, missing_text="\u2014"),
            _render_failure_rate(total_requests, total_failures),
            "\u2014",
            _render_maybe_missing(total_input, missing_text="\u2014"),
            _render_maybe_missing(total_output, missing_text="\u2014"),
            _render_maybe_missing(
                _observed_token_total(total_input, total_output), missing_text="\u2014"
            ),
            _render_maybe_missing(
                _sum_reported(usage, "cache_read_tokens"), missing_text="\u2014"
            ),
            _render_maybe_missing(
                _sum_reported(usage, "cache_write_tokens"), missing_text="\u2014"
            ),
            _render_maybe_missing(
                _sum_reported(usage, "reasoning_tokens"), missing_text="\u2014"
            ),
            "\u2014",
            "\u2014",
        )
    )
    token_help = "Observed token usage from telemetry; this is not billing data."
    return f"""
<table class="observe-usage-table" aria-label="Model usage observed in the selected range">
  <caption class="visually-hidden">
    Model usage observed in the selected range. Token counts are observed usage, not billing data.
  </caption>
  <thead>
    <tr>
      {_render_header_cell("Model", "Model identifier reported by response telemetry.")}
      {_render_header_cell("Deployment", "Requested Azure OpenAI deployment reported by telemetry.")}
      {_render_header_cell("Requests")}
      {_render_header_cell("Failure rate", "Failures divided by requests.")}
      {_render_header_cell("p95 latency", "95% of observed model requests completed in this time or less.")}
      {_render_header_cell("Input tokens", token_help)}
      {_render_header_cell("Output tokens", token_help)}
      {_render_header_cell("Total tokens", token_help)}
      {_render_header_cell("Cache read", f"Tokens served from the prompt cache. {token_help}")}
      {_render_header_cell("Cache write", f"Tokens written to the prompt cache. {token_help}")}
      {_render_header_cell("Reasoning", f"Reasoning tokens reported by the model provider. {token_help}")}
      {_render_header_cell("Other token classes", "Additional gen_ai.usage.* classes. A row information icon means some telemetry records omitted token-class attributes.")}
      {_render_header_cell("Last seen", "Most recent telemetry in the selected range.")}
    </tr>
  </thead>
  <tbody>{"".join(rows)}</tbody>
  {footer}
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
    <div><dt>Query duration</dt><dd>{_render_seconds(duration_ms)}</dd></div>
    <div><dt>Cache</dt><dd>{html_escape(cache_status or '—')}</dd></div>
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

_OBSERVE_COMPONENT_CSS = """
/*
 * Observe component styles. All theming flows from the canonical AgentOps
 * tokens (see agentops.agent.ui_theme). The legacy per-component
 * ``--observe-*`` custom properties are mapped onto those tokens on
 * ``.observe-root`` so every existing rule themes correctly in both the dark
 * (default) and explicit light themes -- there is deliberately no
 * OS-preference media query that could drift from the Cockpit.
 */
.observe-root {
  margin: 0;
  min-height: 100vh;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
  --observe-bg: var(--bg);
  --observe-fg: var(--text);
  --observe-muted: var(--text-dim);
  --observe-faint: var(--text-faint);
  --observe-border: var(--border);
  --observe-border-strong: var(--border-strong);
  --observe-card-bg: var(--card);
  --observe-card-hi: var(--card-hi);
  --observe-accent: var(--info);
  --observe-ok: var(--ok);
  --observe-warn: var(--warn);
  --observe-crit: var(--crit);
  --observe-series-1: var(--info);
  --observe-series-2: var(--warn);
  --observe-series-3: var(--ok);
  --observe-series-4: #bc8cff;
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

/* --- Navigation ---------------------------------------------------------- */
.observe-nav {
  margin: 4px 0 22px;
  border-bottom: 1px solid var(--observe-border);
}
.observe-nav-list {
  display: flex;
  flex-wrap: wrap;
  gap: 2px;
  list-style: none;
  padding: 0;
  margin: 0;
}
.observe-nav-link {
  display: inline-block;
  padding: 8px 14px;
  color: var(--observe-muted);
  text-decoration: none;
  font-size: 13px;
  font-weight: 600;
  border-bottom: 2px solid transparent;
  transition: color 0.15s ease, border-color 0.15s ease;
}
.observe-nav-link:hover { color: var(--observe-fg); }
.observe-nav-link[aria-selected="true"] {
  color: var(--observe-fg);
  border-bottom-color: var(--observe-accent);
  text-decoration: none;
}
.observe-nav-link:focus-visible {
  outline: 2px solid var(--observe-accent);
  outline-offset: 2px;
  border-radius: 4px;
}

/* --- Filters (compact, subordinate to the summary) ----------------------- */
.observe-filter-bar,
.observe-cost-filter-bar,
.observe-attribution-filter-bar {
  background: var(--observe-card-bg);
  border: 1px solid var(--observe-border);
  border-radius: 12px;
  padding: 12px 14px;
  margin-bottom: 16px;
}
.observe-filter-fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px 12px;
}
.observe-filter-fields label {
  display: flex;
  flex-direction: column;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
  text-transform: uppercase;
  color: var(--observe-faint);
  gap: 4px;
}
.observe-filter-fields input {
  font: inherit;
  font-size: 13px;
  text-transform: none;
  letter-spacing: normal;
  font-weight: 400;
  color: var(--observe-fg);
  background: var(--bg);
  border: 1px solid var(--observe-border);
  border-radius: 8px;
  padding: 6px 9px;
}
.observe-filter-fields input:focus-visible {
  outline: 2px solid var(--observe-accent);
  outline-offset: 1px;
  border-color: var(--observe-accent);
}
.observe-filter-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 12px;
}
.observe-apply-button,
.observe-refresh-button {
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  border-radius: 8px;
  padding: 6px 14px;
  border: 1px solid var(--observe-border);
  background: var(--observe-card-bg);
  color: var(--observe-fg);
  transition: background 0.15s ease, border-color 0.15s ease;
}
.observe-apply-button {
  background: color-mix(in srgb, var(--observe-accent) 16%, transparent);
  border-color: color-mix(in srgb, var(--observe-accent) 42%, transparent);
  color: var(--observe-accent);
}
.observe-apply-button:hover {
  background: color-mix(in srgb, var(--observe-accent) 26%, transparent);
}
.observe-refresh-button:hover { border-color: var(--observe-border-strong); }
.observe-refresh-button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}
.observe-apply-button:focus-visible,
.observe-refresh-button:focus-visible {
  outline: 2px solid var(--observe-accent);
  outline-offset: 2px;
}
.observe-refresh-status { color: var(--observe-muted); font-size: 12px; }
.observe-page-controls {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0 0 8px;
}
.observe-page-controls input,
.observe-page-controls select {
  box-sizing: border-box;
  min-height: 32px;
  border: 1px solid var(--observe-border);
  border-radius: 8px;
  background: var(--observe-card-bg);
  color: var(--observe-fg);
  font: inherit;
  font-size: 13px;
  padding: 5px 9px;
}
.observe-page-controls input { min-width: 220px; }
.observe-page-status {
  color: var(--observe-muted);
  font-size: 12px;
  min-width: 48px;
  text-align: center;
}
.observe-scope { margin: 0 0 10px; font-size: 12px; color: var(--observe-muted); }

.observe-attribution-summary {
  background: var(--observe-card-bg);
  border: 1px solid var(--observe-border);
  border-radius: 12px;
  padding: 14px 16px;
  margin-bottom: 16px;
}
.observe-attribution-summary-columns, .observe-attribution-usage { display: flex; flex-wrap: wrap; gap: 0.75rem 1.5rem; }
.observe-attribution-usage div { min-width: 8rem; }
.observe-attribution-usage dt { font-weight: 600; color: var(--observe-faint); font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }
.observe-attribution-usage dd { margin: 0; }
.observe-cost-period, .observe-cost-precision-notes { display: flex; flex-wrap: wrap; gap: 0.75rem 1.5rem; }
.observe-cost-period div { display: flex; gap: 0.35rem; }
.observe-cost-period dt { font-weight: 600; }
.observe-cost-period dd { margin: 0; }
.observe-cost-disclaimer { border-left: 3px solid var(--observe-warn); padding: 0.75rem; color: var(--observe-muted); background: color-mix(in srgb, var(--observe-warn) 8%, transparent); border-radius: 0 8px 8px 0; }
.observe-cost-view table { margin-bottom: 1rem; }

/* --- Overview KPI cards -------------------------------------------------- */
.observe-overview-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 14px;
  margin-bottom: 8px;
}
.observe-card {
  background: var(--observe-card-bg);
  border: 1px solid var(--observe-border);
  border-radius: 14px;
  padding: 16px 18px;
  transition: border-color 0.15s ease, transform 0.15s ease;
}
.observe-card:hover { border-color: var(--observe-border-strong); }
.observe-metric-card { display: flex; flex-direction: column; gap: 6px; position: relative; }
.observe-card-title {
  margin: 0;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--observe-faint);
}
.observe-card-value {
  margin: 0;
  font-size: 30px;
  font-weight: 700;
  line-height: 1.05;
  letter-spacing: -0.02em;
  color: var(--observe-fg);
}
.observe-metric-card.observe-tone-ok .observe-card-value { color: var(--observe-ok); }
.observe-metric-card.observe-tone-warn .observe-card-value { color: var(--observe-warn); }
.observe-metric-card.observe-tone-crit .observe-card-value { color: var(--observe-crit); }
.observe-card-delta {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  align-self: flex-start;
  font-size: 12px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid currentColor;
}
.observe-card-delta-label { color: var(--observe-muted); font-weight: 500; }
.observe-card-caption { margin: 0; font-size: 12px; color: var(--observe-muted); }
.observe-card-spark { margin-top: 4px; }
.observe-card-spark .observe-chart { margin: 0; }
.observe-card-spark figcaption { display: none; }
.observe-card-spark .observe-chart-legend { display: none; }
.observe-card-footer {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  margin-top: 4px;
  font-size: 11px;
  color: var(--observe-faint);
}

/* --- Trends ------------------------------------------------------------- */
.observe-overview-trends { margin-top: 26px; }
.observe-trend-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 16px;
}
.observe-trend-tile {
  background: var(--observe-card-bg);
  border: 1px solid var(--observe-border);
  border-radius: 14px;
  padding: 14px 16px;
}

/* --- Tables (drill-down views) ------------------------------------------ */
table { border-collapse: collapse; width: 100%; font-size: 13px; }
caption { text-align: left; }
th, td {
  border-bottom: 1px solid var(--observe-border);
  padding: 9px 10px;
  text-align: left;
  vertical-align: top;
}
thead th {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--observe-faint);
  border-bottom: 1px solid var(--observe-border-strong);
  position: sticky;
  top: 0;
  background: var(--observe-bg);
}
.observe-sort-button {
  align-items: center;
  appearance: none;
  background: transparent;
  border: 0;
  color: inherit;
  cursor: pointer;
  display: inline-flex;
  font: inherit;
  gap: 6px;
  letter-spacing: inherit;
  padding: 0;
  text-align: left;
  text-transform: inherit;
}
.observe-sort-button::after {
  color: var(--observe-muted);
  content: "\\2195";
  font-size: 12px;
  line-height: 1;
}
th[aria-sort="ascending"] .observe-sort-button::after {
  color: var(--observe-accent);
  content: "\\2191";
}
th[aria-sort="descending"] .observe-sort-button::after {
  color: var(--observe-accent);
  content: "\\2193";
}
.observe-sort-button:focus-visible {
  border-radius: 3px;
  outline: 2px solid var(--observe-accent);
  outline-offset: 3px;
}
.observe-info-icon {
  align-items: center;
  border: 1px solid currentColor;
  border-radius: 50%;
  color: var(--observe-muted);
  cursor: help;
  display: inline-flex;
  font-size: 9px;
  font-style: normal;
  font-weight: 800;
  height: 14px;
  justify-content: center;
  line-height: 1;
  margin-left: 5px;
  text-transform: none;
  vertical-align: middle;
  width: 14px;
}
.observe-info-icon:focus-visible {
  color: var(--observe-accent);
  outline: 2px solid var(--observe-accent);
  outline-offset: 2px;
}
tbody tr:hover td { background: color-mix(in srgb, var(--observe-fg) 4%, transparent); }
tfoot th,
tfoot td {
  background: color-mix(in srgb, var(--observe-card-bg) 92%, var(--observe-accent));
  border-top: 1px solid var(--observe-border-strong);
  border-bottom: 0;
  font-weight: 700;
}
.observe-drilldown-button {
  appearance: none;
  background: transparent;
  border: 0;
  color: var(--observe-accent);
  cursor: pointer;
  font: inherit;
  font-weight: 650;
  padding: 0;
  text-decoration: underline;
  text-decoration-color: color-mix(in srgb, var(--observe-accent) 45%, transparent);
  text-underline-offset: 3px;
}
.observe-drilldown-button:hover { text-decoration-color: currentColor; }
.observe-drilldown-button:focus-visible {
  border-radius: 3px;
  outline: 2px solid var(--observe-accent);
  outline-offset: 3px;
}
.observe-drilldown-row > td {
  background: color-mix(in srgb, var(--observe-accent) 4%, var(--observe-surface));
  padding: 14px;
}
.observe-drilldown-panel {
  border-left: 3px solid var(--observe-accent);
  padding-left: 12px;
}
.observe-drilldown-panel > p { margin: 0 0 10px; }
.observe-drilldown-table {
  background: var(--observe-bg);
  border: 1px solid var(--observe-border);
}
.observe-drilldown-table td { word-break: break-word; }

/* --- Badges & tones ----------------------------------------------------- */
.observe-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 24px;
  box-sizing: border-box;
  border-radius: 999px;
  padding: 3px 9px;
  font-size: 12px;
  font-weight: 700;
  line-height: 1.25;
  letter-spacing: 0.01em;
  white-space: nowrap;
  border: 1px solid var(--observe-border-strong);
  background: var(--observe-card-hi);
}
.observe-badge.observe-tone-ok {
  border-color: color-mix(in srgb, var(--observe-ok) 52%, var(--observe-border));
  background: color-mix(in srgb, var(--observe-ok) 16%, var(--observe-card-bg));
  color: var(--observe-ok);
}
.observe-badge.observe-tone-warn {
  border-color: color-mix(in srgb, var(--observe-warn) 52%, var(--observe-border));
  background: color-mix(in srgb, var(--observe-warn) 14%, var(--observe-card-bg));
  color: var(--observe-warn);
}
.observe-badge.observe-tone-crit {
  border-color: color-mix(in srgb, var(--observe-crit) 52%, var(--observe-border));
  background: color-mix(in srgb, var(--observe-crit) 14%, var(--observe-card-bg));
  color: var(--observe-crit);
}
.observe-badge.observe-tone-info {
  border-color: color-mix(in srgb, var(--observe-accent) 52%, var(--observe-border));
  background: color-mix(in srgb, var(--observe-accent) 14%, var(--observe-card-bg));
  color: var(--observe-accent);
}
.observe-badge.observe-tone-muted {
  border-color: var(--observe-border-strong);
  background: color-mix(in srgb, var(--observe-muted) 10%, var(--observe-card-bg));
  color: var(--observe-muted);
}
.observe-tone-ok { color: var(--observe-ok); }
.observe-tone-warn { color: var(--observe-warn); }
.observe-tone-crit { color: var(--observe-crit); }
.observe-tone-info { color: var(--observe-accent); }
.observe-tone-muted { color: var(--observe-muted); }

.metric-missing { color: var(--observe-muted); font-style: italic; }
.metric-zero { color: var(--observe-fg); }

/* --- Charts ------------------------------------------------------------- */
.observe-chart { margin: 0 0 8px; }
.observe-chart figcaption {
  font-size: 12px;
  font-weight: 600;
  color: var(--observe-faint);
  margin-bottom: 6px;
}
.observe-chart-svg { width: 100%; height: auto; display: block; }
.observe-chart-grid { stroke: var(--observe-border); stroke-opacity: 0.6; stroke-width: 1; }
.observe-chart-line { stroke-linejoin: round; stroke-linecap: round; }
.observe-chart-marker { font-size: 9px; }
.observe-chart-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  list-style: none;
  padding: 0;
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--observe-muted);
}
.observe-chart-legend-item { display: inline-flex; align-items: center; gap: 5px; }
.observe-chart-legend-marker { font-size: 11px; }
.observe-chart-empty { color: var(--observe-muted); }

/* --- Notices ------------------------------------------------------------ */
.observe-partial-notice {
  color: var(--observe-warn);
  font-weight: 600;
  border: 1px solid color-mix(in srgb, var(--observe-warn) 40%, transparent);
  background: color-mix(in srgb, var(--observe-warn) 8%, transparent);
  border-radius: 10px;
  padding: 10px 12px;
  margin-bottom: 12px;
}
.observe-protected-notice { color: var(--observe-muted); }
.observe-empty { color: var(--observe-muted); }
.observe-empty-state {
  border: 1px dashed var(--observe-border-strong);
  border-radius: 14px;
  padding: 28px 20px;
  text-align: center;
}
.observe-hint { color: var(--observe-muted); font-size: 12px; }

/* --- Intentional states ------------------------------------------------- */
.observe-state {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  background: var(--observe-card-bg);
  border: 1px solid var(--observe-border);
  border-left-width: 3px;
  border-left-color: var(--observe-border-strong);
  border-radius: 12px;
  padding: 16px 18px;
  margin: 8px 0 16px;
}
.observe-state.observe-tone-info { border-left-color: var(--observe-accent); }
.observe-state.observe-tone-warn { border-left-color: var(--observe-warn); }
.observe-state.observe-tone-crit { border-left-color: var(--observe-crit); }
.observe-state.observe-tone-muted { border-left-color: var(--observe-border-strong); }
.observe-state-icon { font-size: 20px; line-height: 1.2; color: currentColor; }
.observe-state.observe-tone-info .observe-state-icon { color: var(--observe-accent); }
.observe-state.observe-tone-warn .observe-state-icon { color: var(--observe-warn); }
.observe-state.observe-tone-crit .observe-state-icon { color: var(--observe-crit); }
.observe-state.observe-tone-muted .observe-state-icon { color: var(--observe-muted); }
.observe-state-body { flex: 1; min-width: 0; }
.observe-state-title { margin: 0 0 4px; font-size: 14px; font-weight: 700; color: var(--observe-fg); }
.observe-state-message { margin: 0; font-size: 13px; color: var(--observe-muted); }
.observe-state-detail { margin: 6px 0 0; font-size: 12px; color: var(--observe-faint); }
.observe-state-actions { margin-top: 10px; display: flex; flex-wrap: wrap; gap: 8px; }
.observe-state-loading .observe-state-icon { animation: observe-pulse 1.4s ease-in-out infinite; }

@keyframes observe-pulse {
  0%, 100% { opacity: 0.35; }
  50% { opacity: 1; }
}

@media (prefers-reduced-motion: reduce) {
  .observe-card, .observe-nav-link, .observe-apply-button, .observe-refresh-button { transition: none; }
  .observe-state-loading .observe-state-icon { animation: none; }
}
""".strip()


_OBSERVE_STYLES = "\n\n".join(
    (
        ui_theme.render_theme_variables(default_theme="dark"),
        ui_theme.SHARED_SHELL_CSS.strip(),
        _OBSERVE_COMPONENT_CSS,
    )
).strip()


# ---------------------------------------------------------------------------
# Behavior script (T051 / T053 / T054)
# ---------------------------------------------------------------------------
#
# Safety guarantee enforced by this script (and pinned by tests):
#   * Only OBSERVE_FILTER_QUERY_KEYS (foundry_resource_id, project_resource_id,
#     agent_id, model, tool_name, run_key, start, end), the active `view`, and
#     the non-sensitive `theme` preference are read from or written to the URL
#     query string via history.replaceState.
#   * Raw generative-AI content fields (input_messages, output_messages,
#     system_instructions, tool_content, evaluation_explanation) are NEVER
#     placed in the URL, and this script never calls localStorage,
#     sessionStorage, or document.cookie for ANY purpose.
#   * Protected content is only ever requested after an explicit user click
#     on the "Load protected content" button; there is no automatic fetch of
#     that endpoint anywhere in this script.

_OBSERVE_SCRIPT = ui_theme.THEME_TOGGLE_SCRIPT + """
(function () {
  "use strict";

  var FILTER_KEYS = ["foundry_resource_id", "project_resource_id", "agent_id", "model", "tool_name", "run_key", "start", "end"];
  var COST_FILTER_KEYS = ["cost_period_id", "cost_component_id", "cost_breakdown", "cost_agent_key"];
  var ATTRIBUTION_FILTER_KEYS = ["department_filter_token", "user_filter_token", "attribution_group_by", "attribution_metric", "attribution_cost_period_id", "attribution_cost_component_id"];
  var COST_DISCLAIMER = "Operational cost allocation from declared billed totals and observed usage; not an invoice or billing-accurate charge.";
  var COST_BREAKDOWN_WARNING = "Agent, tool, and run breakdowns are alternative reconciliations of the same billed pools; do not add them together.";
  var ATTRIBUTION_COST_UNAVAILABLE = "Cost attribution is unavailable. Configure a valid cost model and allocatable cost before selecting Cost.";
  var AUTO_REFRESH_MS = 300000; // five minutes
  var DEFAULT_RANGE_MS = 24 * 60 * 60 * 1000; // trailing 24 hours
  var CACHE_WINDOW_MS = 60 * 1000; // align default windows across browser sessions
  // Mirrors MAX_TREND_POINTS in ui.py: even though the backend is expected
  // to already bound each trend series (T053), the client re-bounds
  // defensively so a chart never renders unbounded markup regardless of
  // what a given backend implementation actually sends.
  var MAX_TREND_POINTS = 60;
  // Maps each internal view identifier to the `ObserveQuery.view` wire value
  // from contracts/observe-api.openapi.yaml (mirrors OBSERVE_VIEW_WIRE_NAMES
  // in ui.py -- the internal "usage" id is spelled "models" on the wire).
  var VIEW_WIRE_NAMES = { overview: "overview", agents: "agents", usage: "models", tools: "tools", runs: "runs" };
  var SERVER_SORT_KEYS = {
    "observe-agents-table": {
      "Agent": "agent_name", "Source": "source_kind", "Model": "model",
      "Last seen": "last_seen", "Invocations": "invocations",
      "Failure rate": "failure_rate", "p95 latency": "p95_latency_ms",
      "Input tokens": "input_tokens", "Output tokens": "output_tokens",
      "Total tokens": "total_tokens", "Cache read": "cache_read_tokens",
      "Cache write": "cache_write_tokens", "Reasoning": "reasoning_tokens"
    },
    "observe-usage-table": {
      "Model": "model", "Deployment": "deployment", "Requests": "requests",
      "Failure rate": "failure_rate", "p95 latency": "p95_latency_ms",
      "Input tokens": "input_tokens", "Output tokens": "output_tokens",
      "Total tokens": "total_tokens", "Cache read": "cache_read_tokens",
      "Cache write": "cache_write_tokens", "Reasoning": "reasoning_tokens",
      "Last seen": "last_seen"
    },
    "observe-tools-table": {
      "Tool": "tool_name", "Agent": "agent_name", "Source": "source_id",
      "Runtime": "source_kind", "Last seen": "last_seen",
      "Invocations": "invocations", "Failures": "failures",
      "p95 latency": "p95_latency_ms"
    },
    "observe-runs-table": {
      "Run key": "run_key", "Correlation": "run_key_kind", "Agent": "agent_name",
      "Source": "source_id", "Runtime": "source_kind", "Started in range": "started_at",
      "Duration in range": "duration_ms", "Status": "status", "Turns in range": "turns",
      "Tool invocations": "tool_invocations", "Input tokens": "input_tokens",
      "Output tokens": "output_tokens", "Total tokens": "total_tokens",
      "Cache read": "cache_read_tokens",
      "Cache write": "cache_write_tokens", "Reasoning": "reasoning_tokens"
    }
  };
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
  var currentPage = 1;
  var currentPageSize = 50;
  var currentSearch = "";
  var currentSortBy = "";
  var currentSortDirection = "desc";
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
    if (document.getElementById("departments")) {
      ATTRIBUTION_FILTER_KEYS.forEach(function (key) {
        var value = params.get(key);
        if (value) {
          applied[key] = value;
        }
      });
      applied.attribution_metric = applied.attribution_metric || "usage";
      applied.attribution_group_by = applied.attribution_group_by || "department";
    }
    currentView = params.get("view") || "overview";
    if (currentView === "cost" && !document.getElementById("cost")) {
      currentView = "overview";
    }
    currentPage = Math.max(1, parseInt(params.get("page") || "1", 10) || 1);
    currentPageSize = [25, 50, 100].indexOf(parseInt(params.get("page_size") || "50", 10)) >= 0
      ? parseInt(params.get("page_size") || "50", 10)
      : 50;
    currentSearch = String(params.get("search") || "").slice(0, 200);
    currentSortBy = String(params.get("sort_by") || "").slice(0, 64);
    currentSortDirection = params.get("sort_direction") === "asc" ? "asc" : "desc";
    if (!applied.start || !applied.end) {
      var end = applied.end
        ? new Date(applied.end)
        : new Date(Math.floor(Date.now() / CACHE_WINDOW_MS) * CACHE_WINDOW_MS);
      var start = applied.start
        ? new Date(applied.start)
        : new Date(end.getTime() - DEFAULT_RANGE_MS);
      applied.start = start.toISOString();
      applied.end = end.toISOString();
    }
    return applied;
  }

  function buildStateUrl() {
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
    if (document.getElementById("departments")) {
      ATTRIBUTION_FILTER_KEYS.forEach(function (key) {
        if (appliedFilters[key]) {
          params.set(key, appliedFilters[key]);
        }
      });
    }
    params.set("view", currentView);
    if (isPagedView(currentView)) {
      params.set("page", String(currentPage));
      params.set("page_size", String(currentPageSize));
      if (currentSearch) params.set("search", currentSearch);
      if (currentSortBy) params.set("sort_by", currentSortBy);
      params.set("sort_direction", currentSortDirection);
    }
    params.set("theme", document.documentElement.getAttribute("data-theme") || "dark");
    return window.location.pathname + "?" + params.toString();
  }

  function syncUrl() {
    window.history.replaceState(null, "", buildStateUrl());
  }

  function pushUrl() {
    window.history.pushState(null, "", buildStateUrl());
  }

  function activateView(view) {
    var panel = document.getElementById(view);
    if (!panel || !panel.hasAttribute("data-observe-panel")) {
      view = "overview";
    }
    currentView = view;
    document.querySelectorAll("[data-observe-panel]").forEach(function (candidate) {
      candidate.hidden = candidate.id !== currentView;
    });
    document.querySelectorAll("[data-observe-nav-link]").forEach(function (link) {
      link.setAttribute(
        "aria-selected",
        link.getAttribute("data-observe-nav-link") === currentView ? "true" : "false"
      );
    });
  }

  function readDraftFromForm(form) {
    var draft = {};
    FILTER_KEYS.forEach(function (key) {
      var field = form.querySelector('[data-draft-filter="' + key + '"]');
      var value = field && field.value ? field.value : "";
      if ((key === "start" || key === "end") && value) {
        var moment = new Date(value);
        value = isNaN(moment.getTime()) ? "" : moment.toISOString();
      }
      draft[key] = value;
    });
    return draft;
  }

  function populateFormFromApplied(form) {
    FILTER_KEYS.forEach(function (key) {
      var field = form.querySelector('[data-draft-filter="' + key + '"]');
      if (field) {
        var value = appliedFilters[key] || "";
        if ((key === "start" || key === "end") && value) {
          var moment = new Date(value);
          if (!isNaN(moment.getTime())) {
            var local = new Date(moment.getTime() - moment.getTimezoneOffset() * 60000);
            value = local.toISOString().slice(0, 16);
          }
        }
        field.value = value;
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
    ambiguous: { label: "Ambiguous", tone: "warn" },
    no_data: { label: "No data found", tone: "muted" },
    not_reported: { label: "Not reported", tone: "muted" },
    not_configured: { label: "Not configured", tone: "muted" },
    inaccessible: { label: "Inaccessible", tone: "crit" },
    protected_or_unavailable: { label: "Protected or unavailable", tone: "warn" },
    ambiguous: { label: "Ambiguous", tone: "warn" },
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
    user_attribution: "User attribution",
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

  function formatNumberJs(value, minimumFractionDigits, maximumFractionDigits) {
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
    if (
      Number.isInteger(num) &&
      minimumFractionDigits === undefined &&
      maximumFractionDigits === undefined
    ) {
      return num.toLocaleString("en-US");
    }
    return num.toLocaleString("en-US", {
      minimumFractionDigits: minimumFractionDigits === undefined ? 2 : minimumFractionDigits,
      maximumFractionDigits: maximumFractionDigits === undefined ? 2 : maximumFractionDigits
    });
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
    return makeEl(
      "span",
      "observe-metric " + (isZero ? "metric-zero" : "metric-value"),
      formatNumberJs(value, opts.minimumFractionDigits, opts.maximumFractionDigits) + suffix
    );
  }

  function renderMillisecondsAsSeconds(value, missingText) {
    if (value === null || value === undefined) {
      return renderMaybeMissing(null, { missingText: missingText || "\u2014" });
    }
    return renderMaybeMissing(Number(value) / 1000, {
      suffix: " s",
      minimumFractionDigits: 3,
      maximumFractionDigits: 3
    });
  }

  function compactTimestamp(value) {
    if (!value) return "";
    var moment = new Date(value);
    if (isNaN(moment.getTime())) return String(value);
    function pad(part) { return String(part).padStart(2, "0"); }
    return moment.getUTCFullYear() + "-" + pad(moment.getUTCMonth() + 1) + "-" +
      pad(moment.getUTCDate()) + " " + pad(moment.getUTCHours()) + ":" +
      pad(moment.getUTCMinutes()) + ":" + pad(moment.getUTCSeconds()) + " UTC";
  }

  function renderTimestampJs(value) {
    if (!value) {
      return renderMaybeMissing(null, { missingText: "\u2014" });
    }
    var time = document.createElement("time");
    time.setAttribute("datetime", value);
    time.title = String(value);
    time.textContent = compactTimestamp(value);
    return time;
  }

  function sumReported(rows, field) {
    var found = false;
    var total = (rows || []).reduce(function (sum, row) {
      var value = row && row[field];
      if (value === null || value === undefined || isNaN(Number(value))) return sum;
      found = true;
      return sum + Number(value);
    }, 0);
    return found ? total : null;
  }

  function infoIcon(helpText, extraClass) {
    var icon = makeEl("span", "observe-info-icon" + (extraClass ? " " + extraClass : ""), "i");
    icon.setAttribute("role", "img");
    icon.setAttribute("tabindex", "0");
    icon.setAttribute("aria-label", helpText);
    icon.title = helpText;
    return icon;
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

  function renderTokenTotals(inputTokens, outputTokens, missingText) {
    var wrap = makeEl("span", "observe-token-totals");
    var inSpan = makeEl("span", "observe-token-in", "In: ");
    inSpan.appendChild(renderMaybeMissing(inputTokens, { missingText: missingText || "Not reported" }));
    var outSpan = makeEl("span", "observe-token-out", "Out: ");
    outSpan.appendChild(renderMaybeMissing(outputTokens, { missingText: missingText || "Not reported" }));
    wrap.appendChild(inSpan);
    wrap.appendChild(document.createTextNode(" "));
    wrap.appendChild(outSpan);
    return wrap;
  }

  function observedTokenTotal(inputTokens, outputTokens) {
    if (inputTokens === null && outputTokens === null) {
      return null;
    }
    if (inputTokens === undefined && outputTokens === undefined) {
      return null;
    }
    return Number(inputTokens || 0) + Number(outputTokens || 0);
  }

  function renderAdditionalTokenClasses(entry) {
    entry = entry || {};
    var wrap = makeEl("span", "observe-additional-token-classes");
    Object.keys(entry.additional_token_classes || {}).forEach(function (name) {
      wrap.appendChild(makeEl(
        "span",
        "observe-token-class observe-token-class-additional",
        name + ": " + formatNumberJs(entry.additional_token_classes[name])
      ));
    });
    if (entry.additional_token_classes_truncated) {
      wrap.appendChild(infoIcon(
        "More additional token classes were reported than can be displayed.",
        "observe-token-classes-truncated"
      ));
    }
    if (entry.token_classes_partial) {
      wrap.appendChild(infoIcon(
        "Some telemetry records omitted one or more token-class attributes; totals include the values that were reported.",
        "observe-token-classes-partial"
      ));
    }
    return wrap.childNodes.length ? wrap : renderMaybeMissing(null, { missingText: "\u2014" });
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
      classes.appendChild(infoIcon(
        "Some telemetry records omitted one or more token-class attributes; totals include the values that were reported.",
        "observe-token-classes-partial"
      ));
    }
    if (entry.additional_token_classes_truncated) {
      classes.appendChild(
        makeEl("span", "observe-token-classes-truncated", "Additional classes truncated")
      );
    }
    wrap.appendChild(classes);
    return wrap;
  }

  function renderLastSeenJs(value) {
    if (!value) {
      return makeEl("span", "observe-last-seen observe-last-seen-missing metric-missing", "\u2014");
    }
    var span = makeEl("span", "observe-last-seen");
    span.appendChild(renderTimestampJs(value));
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
    time.title = String(value);
    time.textContent = label + ": " + compactTimestamp(value);
    return time;
  }

  // Mirrors `render_source_label`.
  function renderSourceLabelJs(source) {
    var label = "source unavailable";
    if (typeof source === "string" && source) {
      label = source;
    } else if (source && typeof source === "object") {
      label = source.source_kind || source.source_id || "source unavailable";
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
    if (kind !== "unknown") {
      return renderBadgeJs(label, tone, "observe-source-kind-badge");
    }
    var help = makeEl("span", "observe-inline-help");
    help.title = "Source kind could not be classified from the available telemetry attributes.";
    help.appendChild(renderBadgeJs("Unclassified", tone, "observe-source-kind-badge"));
    return help;
  }

  function renderIdentityAvailabilityBadge(agentId) {
    if (agentId) {
      return renderBadgeJs("Identity available", "ok", "observe-identity-badge");
    }
    return renderBadgeJs("Identity unavailable", "muted", "observe-identity-badge");
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
    enhanceSortableTables(container);
  }

  function sortableCellValue(cell) {
    var text = String(cell.getAttribute("data-sort-value") || cell.textContent || "").trim();
    if (!text || /^(not reported|not measured|not available)$/i.test(text)) {
      return { missing: true, type: "text", value: "" };
    }
    var iso = text.match(/\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?Z?/);
    if (iso) {
      var timestamp = Date.parse(iso[0]);
      if (!Number.isNaN(timestamp)) {
        return { missing: false, type: "number", value: timestamp };
      }
    }
    var compact = text.replace(/,/g, "");
    var numeric = compact.match(/-?\\d+(?:\\.\\d+)?/);
    if (numeric && (/^-?[\\d,.]+\\s*(?:%|ms|s)?$/i.test(text) || /^(in:|last seen:)/i.test(text))) {
      return { missing: false, type: "number", value: Number(numeric[0]) };
    }
    return { missing: false, type: "text", value: text.toLocaleLowerCase() };
  }

  function enhanceSortableTable(table) {
    if (!table || table.dataset.observeSortable === "true") {
      return;
    }
    var body = table.tBodies && table.tBodies[0];
    var headers = table.querySelectorAll("thead th");
    if (!body || !headers.length) {
      return;
    }
    table.dataset.observeSortable = "true";
    headers.forEach(function (header, columnIndex) {
      var label = header.dataset.label || String(header.textContent || "").trim() ||
        "Column " + (columnIndex + 1);
      var helpText = header.dataset.help || "";
      var serverSortKey = header.dataset.serverSortKey || "";
      clearChildren(header);
      var activeDirection = serverSortKey && currentSortBy === serverSortKey
        ? (currentSortDirection === "asc" ? "ascending" : "descending")
        : "none";
      header.setAttribute("aria-sort", activeDirection);
      var button = makeEl("button", "observe-sort-button", label);
      button.type = "button";
      button.title = "Sort by " + label;
      button.setAttribute("aria-label", "Sort by " + label);
      button.addEventListener("click", function () {
        if (serverSortKey) {
          currentSortDirection = currentSortBy === serverSortKey && currentSortDirection === "asc"
            ? "desc"
            : "asc";
          currentSortBy = serverSortKey;
          currentPage = 1;
          syncUrl();
          fetchObserveData(false);
          return;
        }
        body.querySelectorAll("[data-observe-drilldown-row]").forEach(function (detailRow) {
          detailRow.remove();
        });
        body.querySelectorAll(".observe-drilldown-button").forEach(function (detailButton) {
          detailButton.setAttribute("aria-expanded", "false");
        });
        var direction = header.getAttribute("aria-sort") === "ascending"
          ? "descending"
          : "ascending";
        headers.forEach(function (other) {
          other.setAttribute("aria-sort", other === header ? direction : "none");
        });
        var rows = Array.prototype.slice.call(body.rows)
          .filter(function (row) {
            return row.dataset.observeDrilldownRow !== "true";
          })
          .map(function (row, index) {
          return { row: row, index: index, value: sortableCellValue(row.cells[columnIndex] || row) };
        });
        rows.sort(function (left, right) {
          if (left.value.missing !== right.value.missing) {
            return left.value.missing ? 1 : -1;
          }
          var comparison = 0;
          if (left.value.type === "number" && right.value.type === "number") {
            comparison = left.value.value - right.value.value;
          } else {
            comparison = String(left.value.value).localeCompare(String(right.value.value));
          }
          if (comparison === 0) {
            comparison = left.index - right.index;
          }
          return direction === "ascending" ? comparison : -comparison;
        });
        rows.forEach(function (entry) {
          body.appendChild(entry.row);
        });
        button.setAttribute(
          "aria-label",
          "Sort by " + label + (direction === "ascending" ? " descending" : " ascending")
        );
      });
      header.appendChild(button);
      if (helpText) {
        header.appendChild(infoIcon(helpText));
      }
    });
  }

  function enhanceSortableTables(root) {
    (root || document).querySelectorAll("table").forEach(enhanceSortableTable);
  }

  function appendCellContent(cell, content) {
    if (Array.isArray(content)) {
      content.forEach(function (part) {
        appendCellContent(cell, part);
      });
    } else if (content instanceof Node) {
      cell.appendChild(content);
    } else {
      cell.textContent = content === undefined || content === null ? "" : String(content);
    }
  }

  function buildDataTable(className, ariaLabel, columns, rows, footerCells) {
    var table = makeEl("table", className);
    table.setAttribute("aria-label", ariaLabel);
    var thead = document.createElement("thead");
    var headRow = document.createElement("tr");
    var sortKeys = SERVER_SORT_KEYS[className] || {};
    columns.forEach(function (column) {
      var definition = typeof column === "string" ? { label: column } : column;
      var th = makeEl("th", null, definition.label);
      th.setAttribute("scope", "col");
      th.dataset.label = definition.label;
      if (definition.help) {
        th.dataset.help = definition.help;
      }
      if (sortKeys[definition.label]) {
        th.dataset.serverSortKey = sortKeys[definition.label];
      }
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    rows.forEach(function (cells) {
      var tr = document.createElement("tr");
      cells.forEach(function (cell) {
        var td = document.createElement("td");
        appendCellContent(td, cell);
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    if (footerCells && footerCells.length) {
      var tfoot = document.createElement("tfoot");
      var footerRow = makeEl("tr", "observe-totals-row");
      footerCells.forEach(function (cell, index) {
        var footerCell = document.createElement(index === 0 ? "th" : "td");
        if (index === 0) footerCell.setAttribute("scope", "row");
        appendCellContent(footerCell, cell);
        footerRow.appendChild(footerCell);
      });
      tfoot.appendChild(footerRow);
      table.appendChild(tfoot);
    }
    enhanceSortableTable(table);
    return table;
  }

  function drilldownFilters() {
    return {
      foundry_resource_id: appliedFilters.foundry_resource_id || null,
      project_resource_id: appliedFilters.project_resource_id || null,
      agent_id: appliedFilters.agent_id || null,
      model: appliedFilters.model || null,
      start: appliedFilters.start,
      end: appliedFilters.end,
    };
  }

  function renderDrilldownRows(body) {
    var rows = body && Array.isArray(body.data) ? body.data : [];
    if (!rows.length) {
      if (body && body.complete === false) {
        return emptyStateNode("Activity details could not be loaded completely. Refresh and try again.");
      }
      return emptyStateNode("No matching activity was found for this row.");
    }
    var tableRows = rows.map(function (row) {
      row = row || {};
      var status = row.success === true
        ? "Succeeded"
        : (row.success === false ? "Failed" : "\u2014");
      return [
        renderTimestampJs(row.timestamp),
        row.telemetry_type || "\u2014",
        row.operation_name || "\u2014",
        row.agent_name || row.agent_id || "\u2014",
        row.model || "\u2014",
        row.deployment || "\u2014",
        row.tool_name || "\u2014",
        status,
        renderMillisecondsAsSeconds(row.duration_ms),
        row.trace_id || "\u2014",
      ];
    });
    return buildDataTable(
      "observe-drilldown-table",
      "Metadata-only activity for the selected aggregate",
      ["Time", "Type", "Operation", "Agent", "Model", "Deployment", "Tool", "Status", "Duration", "Trace ID"],
      tableRows,
      [
        [document.createTextNode("Totals "), infoIcon("Totals cover the rows currently displayed.")],
        "\u2014",
        "\u2014",
        "\u2014",
        "\u2014",
        "\u2014",
        "\u2014",
        "\u2014",
        renderMillisecondsAsSeconds(sumReported(rows, "duration_ms")),
        "\u2014"
      ]
    );
  }

  function toggleDrilldown(button, view, selector) {
    var parentRow = button.closest("tr");
    if (!parentRow || !parentRow.parentNode) {
      return;
    }
    var existing = parentRow.nextElementSibling;
    if (existing && existing.dataset.observeDrilldownRow === "true") {
      existing.remove();
      button.setAttribute("aria-expanded", "false");
      return;
    }

    parentRow.parentNode.querySelectorAll("[data-observe-drilldown-row]").forEach(function (row) {
      row.remove();
    });
    parentRow.parentNode.querySelectorAll(".observe-drilldown-button").forEach(function (other) {
      other.setAttribute("aria-expanded", "false");
    });

    var detailRow = document.createElement("tr");
    detailRow.className = "observe-drilldown-row";
    detailRow.dataset.observeDrilldownRow = "true";
    var detailCell = document.createElement("td");
    detailCell.colSpan = parentRow.cells.length;
    var panel = makeEl("div", "observe-drilldown-panel");
    panel.appendChild(makeEl("p", "observe-hint", "Loading activity metadata\u2026"));
    detailCell.appendChild(panel);
    detailRow.appendChild(detailCell);
    parentRow.parentNode.insertBefore(detailRow, parentRow.nextSibling);
    button.setAttribute("aria-expanded", "true");

    fetch("/api/observe/drilldown", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        view: view,
        filters: drilldownFilters(),
        selector: selector,
        limit: 50,
      }),
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("drill-through request failed");
        }
        return response.json();
      })
      .then(function (body) {
        if (!detailRow.isConnected) {
          return;
        }
        clearChildren(panel);
        panel.appendChild(
          makeEl(
            "p",
            "observe-hint",
            body.complete === false
              ? "Some activity sources could not be loaded. The records shown may be incomplete."
              : body.truncated
              ? "Showing the 50 most recent metadata records."
              : "Metadata only. Prompts, responses, and tool payloads are not loaded."
          )
        );
        panel.appendChild(renderDrilldownRows(body));
      })
      .catch(function () {
        if (!detailRow.isConnected) {
          return;
        }
        clearChildren(panel);
        panel.appendChild(
          emptyStateNode("Activity details could not be loaded. Refresh and try again.")
        );
      });
  }

  function buildDrilldownButton(view, selector, value, label) {
    var button = makeEl(
      "button",
      "observe-drilldown-button",
      value === undefined || value === null ? "View" : String(value)
    );
    button.type = "button";
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-label", label || "View activity details");
    button.addEventListener("click", function () {
      toggleDrilldown(button, view, selector);
    });
    return button;
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
    if (data && typeof data === "object" && data.invocations !== undefined) {
      var invocations = Number(data.invocations || 0);
      var failures = Number(data.failures || 0);
      return [
        { title: "Invocations", value: invocations },
        { title: "Failures", value: failures },
        {
          title: "Success rate",
          value: invocations > 0 ? Math.round(((invocations - failures) / invocations) * 1000) / 10 : null,
          unit: "%",
        },
        {
          title: "Average latency",
          value: data.avg_latency_ms === null ? null : Number(data.avg_latency_ms) / 1000,
          unit: " s",
          minimumFractionDigits: 3,
          maximumFractionDigits: 3
        },
        {
          title: "p95 latency",
          value: data.p95_latency_ms === null ? null : Number(data.p95_latency_ms) / 1000,
          unit: " s",
          minimumFractionDigits: 3,
          maximumFractionDigits: 3
        },
      ];
    }
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
    var page = bounds && bounds.page ? bounds.page : currentPage;
    var pageSize = bounds && bounds.page_size ? bounds.page_size : currentPageSize;
    var first = rowsShown ? ((page - 1) * pageSize) + 1 : 0;
    var last = rowsShown ? first + rowsShown - 1 : 0;
    var text;
    if (!rowsShown && page > 1) {
      text = "No rows are available on page " + page + ".";
    } else if (bounds && bounds.truncated) {
      text = "Showing rows " + first + "\u2013" + last + " from the highest-ranked results";
      text += total === null || total === undefined
        ? "."
        : " (" + total + " rows in scope).";
    } else {
      text = total === null || total === undefined
        ? "Showing rows " + first + "\u2013" + last + "."
        : "Showing rows " + first + "\u2013" + last + " of " + total + ".";
    }
    return makeEl("p", "observe-hint observe-bounds-notice", text);
  }

  function isPagedView(view) {
    return ["agents", "usage", "tools", "runs"].indexOf(view) >= 0;
  }

  function resetPaging(resetQuery) {
    currentPage = 1;
    if (resetQuery) {
      currentSearch = "";
      currentSortBy = "";
      currentSortDirection = "desc";
    }
  }

  function paginationToolbar(bounds) {
    var toolbar = makeEl("form", "observe-page-controls");
    toolbar.setAttribute("role", "search");
    toolbar.addEventListener("submit", function (event) {
      event.preventDefault();
      currentSearch = String(search.value || "").trim().slice(0, 200);
      currentPageSize = parseInt(pageSize.value, 10) || 50;
      currentPage = 1;
      syncUrl();
      fetchObserveData(false);
    });

    var search = document.createElement("input");
    search.type = "search";
    search.value = currentSearch;
    search.placeholder = "Search this view";
    search.setAttribute("aria-label", "Search this view");
    toolbar.appendChild(search);

    var pageSize = document.createElement("select");
    pageSize.setAttribute("aria-label", "Rows per page");
    [25, 50, 100].forEach(function (size) {
      var option = document.createElement("option");
      option.value = String(size);
      option.textContent = size + " rows";
      option.selected = size === currentPageSize;
      pageSize.appendChild(option);
    });
    toolbar.appendChild(pageSize);
    toolbar.appendChild(makeEl("button", "observe-refresh-button", "Search"));
    var clear = makeEl("button", "observe-refresh-button", "Clear");
    clear.type = "button";
    clear.disabled = !currentSearch;
    clear.addEventListener("click", function () {
      currentSearch = "";
      currentPage = 1;
      syncUrl();
      fetchObserveData(false);
    });
    toolbar.appendChild(clear);

    var previous = makeEl("button", "observe-refresh-button", "Previous");
    previous.type = "button";
    previous.disabled = !(bounds && bounds.has_previous_page);
    previous.addEventListener("click", function () {
      currentPage = Math.max(1, currentPage - 1);
      syncUrl();
      fetchObserveData(false);
    });
    toolbar.appendChild(previous);

    var status = makeEl(
      "span",
      "observe-page-status",
      "Page " + String((bounds && bounds.page) || currentPage)
    );
    status.setAttribute("aria-live", "polite");
    toolbar.appendChild(status);

    var next = makeEl("button", "observe-refresh-button", "Next");
    next.type = "button";
    next.disabled = !(bounds && bounds.has_next_page);
    next.addEventListener("click", function () {
      currentPage += 1;
      syncUrl();
      fetchObserveData(false);
    });
    toolbar.appendChild(next);
    return toolbar;
  }

  function renderOverview(data, diagnostics) {
    var metrics = overviewMetricsFrom(data);
    if (!metrics.length) {
      setViewContent("overview", [emptyStateNode("No data found for the selected filters.")]);
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
      valueEl.appendChild(renderMaybeMissing(metric.value, {
        suffix: metric.unit || "",
        minimumFractionDigits: metric.minimumFractionDigits,
        maximumFractionDigits: metric.maximumFractionDigits
      }));
      card.appendChild(valueEl);
      if (metric.source) {
        card.appendChild(renderSourceLabelJs(metric.source));
      }
      if (metric.refreshed_at) {
        card.appendChild(renderRefreshedAtJs(metric.refreshed_at));
      }
      grid.appendChild(card);
    });
    setViewContent("overview", [grid]);
  }

  function renderAgents(data, diagnostics, bounds) {
    var agents = agentsFrom(data);
    var notice = boundsNoticeNode(bounds, agents.length);
    var controls = paginationToolbar(bounds);
    if (!agents.length) {
      setViewContent("agents", [controls, notice, emptyStateNode("No data found for the selected filters.")]);
      return;
    }
    var rows = agents.map(function (agent) {
      agent = agent || {};
      var nameCell = [
        document.createTextNode((agent.agent_name || agent.agent_id || "\u2014") + " "),
        renderIdentityAvailabilityBadge(agent.agent_id),
      ];
      var sourceCell = renderSourceKindBadge(agent.source_kind);
      sourceCell.title = agent.source_id || "";
      return [
        nameCell,
        sourceCell,
        agent.model || "\u2014",
        renderLastSeenJs(agent.last_seen),
        buildDrilldownButton(
          "agents",
          {
            source_id: agent.source_id,
            project_resource_id: agent.project_resource_id || null,
            agent_key: agentKeyFor(agent),
          },
          agent.invocations,
          "View " + String(agent.invocations || 0) + " invocations for " + (agent.agent_name || agent.agent_id || "this agent")
        ),
        renderFailureRate(agent.invocations, agent.failures),
        renderMillisecondsAsSeconds(agent.p95_latency_ms),
        renderMaybeMissing(agent.input_tokens, { missingText: "\u2014" }),
        renderMaybeMissing(agent.output_tokens, { missingText: "\u2014" }),
        renderMaybeMissing(observedTokenTotal(agent.input_tokens, agent.output_tokens), { missingText: "\u2014" }),
        renderMaybeMissing(agent.cache_read_tokens, { missingText: "\u2014" }),
        renderMaybeMissing(agent.cache_write_tokens, { missingText: "\u2014" }),
        renderMaybeMissing(agent.reasoning_tokens, { missingText: "\u2014" }),
        buildAgentDetailButton(agent),
      ];
    });
    var totalInvocations = sumReported(agents, "invocations");
    var totalFailures = sumReported(agents, "failures");
    var totalInput = sumReported(agents, "input_tokens");
    var totalOutput = sumReported(agents, "output_tokens");
    var tokenHelp = "Observed token usage from telemetry; this is not billing data.";
    var table = buildDataTable(
      "observe-agents-table",
      "Agents observed in the selected range",
      [
        "Agent",
        "Source",
        { label: "Model", help: "Model identifier reported by response telemetry." },
        { label: "Last seen", help: "Most recent telemetry in the selected range; not agent lifecycle status." },
        "Invocations",
        { label: "Failure rate", help: "Failures divided by invocations." },
        { label: "p95 latency", help: "95% of observed invocations completed in this time or less." },
        { label: "Input tokens", help: tokenHelp },
        { label: "Output tokens", help: tokenHelp },
        { label: "Total tokens", help: tokenHelp },
        { label: "Cache read", help: "Tokens served from the prompt cache. " + tokenHelp },
        { label: "Cache write", help: "Tokens written to the prompt cache. " + tokenHelp },
        { label: "Reasoning", help: "Reasoning tokens reported by the model provider. " + tokenHelp },
        "Details"
      ],
      rows,
      [
        [document.createTextNode("Totals "), infoIcon("Totals cover the rows currently displayed.")],
        "\u2014",
        "\u2014",
        "\u2014",
        renderMaybeMissing(totalInvocations, { missingText: "\u2014" }),
        renderFailureRate(totalInvocations, totalFailures),
        "\u2014",
        renderMaybeMissing(totalInput, { missingText: "\u2014" }),
        renderMaybeMissing(totalOutput, { missingText: "\u2014" }),
        renderMaybeMissing(observedTokenTotal(totalInput, totalOutput), { missingText: "\u2014" }),
        renderMaybeMissing(sumReported(agents, "cache_read_tokens"), { missingText: "\u2014" }),
        renderMaybeMissing(sumReported(agents, "cache_write_tokens"), { missingText: "\u2014" }),
        renderMaybeMissing(sumReported(agents, "reasoning_tokens"), { missingText: "\u2014" }),
        "\u2014"
      ]
    );
    setViewContent("agents", [controls, notice, table]);
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

  function renderUsage(data, diagnostics, bounds) {
    var usage = modelsFrom(data);
    var notice = boundsNoticeNode(bounds, usage.length);
    var controls = paginationToolbar(bounds);
    if (!usage.length) {
      setViewContent("usage", [controls, notice, emptyStateNode("No data found for the selected filters.")]);
      return;
    }
    var rows = usage.map(function (entry) {
      entry = entry || {};
      return [
        entry.model || "\u2014",
        entry.deployment || "\u2014",
        buildDrilldownButton(
          "models",
          {
            source_id: entry.source_id,
            project_resource_id: entry.project_resource_id || null,
            model: entry.model || null,
            deployment: entry.deployment || null,
          },
          entry.requests,
          "View " + String(entry.requests || 0) + " requests for " + (entry.model || entry.deployment || "this model")
        ),
        renderFailureRate(entry.requests, entry.failures),
        renderMillisecondsAsSeconds(entry.p95_latency_ms),
        renderMaybeMissing(entry.input_tokens, { missingText: "\u2014" }),
        renderMaybeMissing(entry.output_tokens, { missingText: "\u2014" }),
        renderMaybeMissing(observedTokenTotal(entry.input_tokens, entry.output_tokens), { missingText: "\u2014" }),
        renderMaybeMissing(entry.cache_read_tokens, { missingText: "\u2014" }),
        renderMaybeMissing(entry.cache_write_tokens, { missingText: "\u2014" }),
        renderMaybeMissing(entry.reasoning_tokens, { missingText: "\u2014" }),
        renderAdditionalTokenClasses(entry),
        renderLastSeenJs(entry.last_seen),
      ];
    });
    var totalRequests = sumReported(usage, "requests");
    var totalFailures = sumReported(usage, "failures");
    var totalInput = sumReported(usage, "input_tokens");
    var totalOutput = sumReported(usage, "output_tokens");
    var tokenHelp = "Observed token usage from telemetry; this is not billing data.";
    var table = buildDataTable(
      "observe-usage-table",
      "Model usage observed in the selected range",
      [
        { label: "Model", help: "Model identifier reported by response telemetry." },
        { label: "Deployment", help: "Requested Azure OpenAI deployment reported by telemetry." },
        "Requests",
        { label: "Failure rate", help: "Failures divided by requests." },
        { label: "p95 latency", help: "95% of observed model requests completed in this time or less." },
        { label: "Input tokens", help: tokenHelp },
        { label: "Output tokens", help: tokenHelp },
        { label: "Total tokens", help: tokenHelp },
        { label: "Cache read", help: "Tokens served from the prompt cache. " + tokenHelp },
        { label: "Cache write", help: "Tokens written to the prompt cache. " + tokenHelp },
        { label: "Reasoning", help: "Reasoning tokens reported by the model provider. " + tokenHelp },
        {
          label: "Other token classes",
          help: "Additional gen_ai.usage.* classes. A row information icon means some telemetry records omitted token-class attributes."
        },
        { label: "Last seen", help: "Most recent telemetry in the selected range." }
      ],
      rows,
      [
        [document.createTextNode("Totals "), infoIcon("Totals cover the rows currently displayed.")],
        "\u2014",
        renderMaybeMissing(totalRequests, { missingText: "\u2014" }),
        renderFailureRate(totalRequests, totalFailures),
        "\u2014",
        renderMaybeMissing(totalInput, { missingText: "\u2014" }),
        renderMaybeMissing(totalOutput, { missingText: "\u2014" }),
        renderMaybeMissing(observedTokenTotal(totalInput, totalOutput), { missingText: "\u2014" }),
        renderMaybeMissing(sumReported(usage, "cache_read_tokens"), { missingText: "\u2014" }),
        renderMaybeMissing(sumReported(usage, "cache_write_tokens"), { missingText: "\u2014" }),
        renderMaybeMissing(sumReported(usage, "reasoning_tokens"), { missingText: "\u2014" }),
        "\u2014",
        "\u2014"
      ]
    );
    setViewContent("usage", [controls, notice, table]);
  }

  function renderTools(data, diagnostics, bounds) {
    var tools = toolsFrom(data);
    var notice = boundsNoticeNode(bounds, tools.length);
    var controls = paginationToolbar(bounds);
    if (!tools.length) {
      setViewContent("tools", [
        controls,
        notice,
        emptyStateNode("No tool activity was found for the selected filters. Tool attribution may not be reported for this selection."),
      ]);
      return;
    }
    var rows = tools.map(function (tool) {
      tool = tool || {};
      return [
        tool.tool_name || "\u2014",
        tool.agent_name || tool.agent_id || tool.agent_key || "\u2014",
        tool.source_id || "\u2014",
        renderSourceKindBadge(tool.source_kind),
        renderLastSeenJs(tool.last_seen),
        buildDrilldownButton(
          "tools",
          {
            source_id: tool.source_id,
            project_resource_id: tool.project_resource_id || null,
            tool_name: tool.tool_name,
            agent_key: tool.agent_key || null,
          },
          tool.invocations,
          "View " + String(tool.invocations || 0) + " invocations for " + (tool.tool_name || "this tool")
        ),
        renderMaybeMissing(tool.failures, { missingText: "\u2014" }),
        renderMillisecondsAsSeconds(tool.p95_latency_ms),
      ];
    });
    var totalInvocations = sumReported(tools, "invocations");
    var totalFailures = sumReported(tools, "failures");
    var table = buildDataTable(
      "observe-tools-table",
      "Tools observed in the selected range",
      [
        "Tool",
        "Agent",
        "Source",
        "Runtime",
        { label: "Last seen", help: "Most recent telemetry in the selected range." },
        "Invocations",
        "Failures",
        { label: "p95 latency", help: "95% of observed tool invocations completed in this time or less." }
      ],
      rows,
      [
        [document.createTextNode("Totals "), infoIcon("Totals cover the rows currently displayed.")],
        "\u2014",
        "\u2014",
        "\u2014",
        "\u2014",
        renderMaybeMissing(totalInvocations, { missingText: "\u2014" }),
        renderMaybeMissing(totalFailures, { missingText: "\u2014" }),
        "\u2014"
      ]
    );
    setViewContent("tools", [controls, notice, table]);
  }

  function renderRuns(data, diagnostics, bounds) {
    var runs = runsFrom(data);
    var notice = boundsNoticeNode(bounds, runs.length);
    var controls = paginationToolbar(bounds);
    if (!runs.length) {
      setViewContent("runs", [
        controls,
        notice,
        emptyStateNode("No runs could be correlated for the selected filters. Run correlation may not be reported for this selection."),
      ]);
      return;
    }
    var rows = runs.map(function (run) {
      run = run || {};
      return [
        run.run_key || "\u2014",
        run.run_key_kind || "\u2014",
        run.agent_name || run.agent_id || run.agent_key || "\u2014",
        run.source_id || "\u2014",
        renderSourceKindBadge(run.source_kind),
        renderTimestampJs(run.started_at),
        renderMillisecondsAsSeconds(run.duration_ms),
        run.status || "\u2014",
        buildDrilldownButton(
          "runs",
          {
            source_id: run.source_id,
            project_resource_id: run.project_resource_id || null,
            run_key: run.run_key,
            agent_key: run.agent_key || null,
          },
          run.turns,
          "View activity for run " + (run.run_key || "")
        ),
        renderMaybeMissing(run.tool_invocations, { missingText: "\u2014" }),
        renderMaybeMissing(run.input_tokens, { missingText: "\u2014" }),
        renderMaybeMissing(run.output_tokens, { missingText: "\u2014" }),
        renderMaybeMissing(observedTokenTotal(run.input_tokens, run.output_tokens), { missingText: "\u2014" }),
        renderMaybeMissing(run.cache_read_tokens, { missingText: "\u2014" }),
        renderMaybeMissing(run.cache_write_tokens, { missingText: "\u2014" }),
        renderMaybeMissing(run.reasoning_tokens, { missingText: "\u2014" }),
      ];
    });
    var totalInput = sumReported(runs, "input_tokens");
    var totalOutput = sumReported(runs, "output_tokens");
    var tokenHelp = "Observed token usage from telemetry; this is not billing data.";
    var table = buildDataTable(
      "observe-runs-table",
      "Runs observed in the selected range",
      [
        "Run key",
        { label: "Correlation", help: "Telemetry key used to group this run." },
        "Agent",
        "Source",
        "Runtime",
        { label: "Started in range", help: "First observed activity within the selected range." },
        { label: "Duration in range", help: "Elapsed time between first and last observed activity in the selected range." },
        "Status",
        { label: "Turns in range", help: "Turns observed within the selected range." },
        "Tool invocations",
        { label: "Input tokens", help: tokenHelp },
        { label: "Output tokens", help: tokenHelp },
        { label: "Total tokens", help: tokenHelp },
        { label: "Cache read", help: "Tokens served from the prompt cache. " + tokenHelp },
        { label: "Cache write", help: "Tokens written to the prompt cache. " + tokenHelp },
        { label: "Reasoning", help: "Reasoning tokens reported by the model provider. " + tokenHelp }
      ],
      rows,
      [
        [document.createTextNode("Totals "), infoIcon("Totals cover the rows currently displayed.")],
        "\u2014",
        "\u2014",
        "\u2014",
        "\u2014",
        "\u2014",
        renderMillisecondsAsSeconds(sumReported(runs, "duration_ms")),
        "\u2014",
        renderMaybeMissing(sumReported(runs, "turns"), { missingText: "\u2014" }),
        renderMaybeMissing(sumReported(runs, "tool_invocations"), { missingText: "\u2014" }),
        renderMaybeMissing(totalInput, { missingText: "\u2014" }),
        renderMaybeMissing(totalOutput, { missingText: "\u2014" }),
        renderMaybeMissing(observedTokenTotal(totalInput, totalOutput), { missingText: "\u2014" }),
        renderMaybeMissing(sumReported(runs, "cache_read_tokens"), { missingText: "\u2014" }),
        renderMaybeMissing(sumReported(runs, "cache_write_tokens"), { missingText: "\u2014" }),
        renderMaybeMissing(sumReported(runs, "reasoning_tokens"), { missingText: "\u2014" })
      ]
    );
    setViewContent("runs", [controls, notice, table]);
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
      var rowCount = String(shown === null || shown === undefined ? "\u2014" : shown);
      if (total !== null && total !== undefined) {
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
      ? (shown === null || shown === undefined
        ? "Showing available rows."
        : "Showing " + String(shown) + " rows.")
      : (shown === null || shown === undefined
        ? String(total) + " rows in scope"
        : "Showing " + String(shown) + " of " + String(total) + " rows in scope") +
        (bounds.truncated ? "; results are truncated." : ".");
    return makeEl("p", "observe-hint observe-cost-bounds-notice", text);
  }

  function renderCost(data, diagnostics, coverage, partialFailures, bounds) {
    if (!data || typeof data !== "object") {
      var emptyNodes = [
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
    var nodes = [renderCostPeriodNode(data)];
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

  function renderAttributionControlsFromData(data) {
    var form = document.getElementById("observe-attribution-filter-form");
    if (!form || !data) return;
    var metric = form.querySelector('[data-attribution-filter="metric"]');
    if (metric) metric.value = data.metric || appliedFilters.attribution_metric || "usage";
    var group = form.querySelector('[data-attribution-filter="group_by"]');
    if (group) group.value = data.group_by || appliedFilters.attribution_group_by || "department";
    var summary = data.summary || {};
    var period = form.querySelector('[data-attribution-filter="cost_period_id"]');
    var component = form.querySelector('[data-attribution-filter="cost_component_id"]');
    if (period && summary.period_id) {
      ensureSelectOption(period, String(summary.period_id), String(summary.period_id));
      period.value = String(summary.period_id);
    }
    if (component && summary.component_id) {
      ensureSelectOption(component, String(summary.component_id), String(summary.component_id));
      component.value = String(summary.component_id);
    }
  }

  function attributionUsageNode(title, usage) {
    usage = usage || {};
    var section = makeEl("div", "observe-attribution-usage-group");
    section.appendChild(makeEl("h4", null, title));
    var dl = makeEl("dl", "observe-attribution-usage");
    [
      ["Invocations", usage.invocations, ""],
      ["Input tokens", usage.input_tokens, ""],
      ["Output tokens", usage.output_tokens, ""],
      ["Tool invocations", usage.tool_invocations, ""],
      ["Active session", usage.active_session_seconds, " s"],
    ].forEach(function (item) {
      var div = document.createElement("div");
      div.appendChild(makeEl("dt", null, item[0]));
      var dd = document.createElement("dd");
      dd.appendChild(renderMaybeMissing(item[1], { suffix: item[2] }));
      div.appendChild(dd);
      dl.appendChild(div);
    });
    section.appendChild(dl);
    return section;
  }

  function renderAttributionSummary(summary, groupBy) {
    summary = summary || {};
    var section = makeEl("section", "observe-attribution-summary");
    section.appendChild(makeEl(
      "h3", null,
      (groupBy === "user" ? "User" : "Department") +
        (summary.metric === "cost" ? " cost summary" : " usage summary")
    ));
    if (summary.metric === "cost") {
      var values = [
        ["Declared total", summary.declared_total],
        ["Attributed cost", summary.attributed_amount],
        ["Unmapped cost", summary.unattributed_amount],
        ["Unallocated cost", summary.unallocated_amount],
      ];
      var dl = document.createElement("dl");
      values.forEach(function (item) {
        var div = document.createElement("div");
        div.appendChild(makeEl("dt", null, item[0]));
        var dd = document.createElement("dd");
        dd.appendChild(renderCostAmountNode(item[1], summary.currency));
        div.appendChild(dd);
        dl.appendChild(div);
      });
      section.appendChild(dl);
      section.appendChild(attributionUsageNode("Unmapped usage", summary.unattributed_usage));
    } else {
      var columns = makeEl("div", "observe-attribution-summary-columns");
      columns.appendChild(attributionUsageNode("Total usage", summary.total));
      columns.appendChild(attributionUsageNode("Attributed usage", summary.attributed));
      columns.appendChild(attributionUsageNode("Unmapped usage", summary.unattributed));
      section.appendChild(columns);
    }
    var counts = makeEl("p");
    counts.appendChild(makeEl("strong", null, "Distinct users: "));
    counts.appendChild(renderMaybeMissing(summary.distinct_users));
    counts.appendChild(document.createTextNode(" "));
    counts.appendChild(makeEl("strong", null, "Omitted users: "));
    counts.appendChild(renderMaybeMissing(summary.omitted_users));
    section.appendChild(counts);
    return section;
  }

  function renderAttributionCoverage(coverage, groupBy) {
    coverage = coverage || [];
    if (!coverage.length) return null;
    var rows = coverage.map(function (entry) {
      var state = entry.state || "error";
      var copy = COVERAGE_STATE_LABELS[state] || COVERAGE_STATE_LABELS.error;
      var measure = String(entry.metric || "usage");
      if (entry.component_id) measure += " / " + String(entry.component_id);
      var counts = [
        ["Eligible", entry.eligible_records],
        ["Identified", entry.identified_records],
        ["Mapped", entry.mapped_records],
        ["Unattributed", entry.unattributed_records],
        ["Ambiguous", entry.ambiguous_records],
        ["Returned", entry.returned_records],
      ].map(function (item) {
        return item[0] + ": " +
          (item[1] === null || item[1] === undefined ? "Not reported" : String(item[1]));
      }).join("; ");
      return [
        entry.source_id || "Not reported",
        measure,
        renderBadgeJs(copy.label, copy.tone, "observe-coverage-state-" + state),
        counts,
        entry.reason || "Not reported",
        entry.next_action || "Not reported",
      ];
    });
    var section = makeEl("section", "observe-attribution-coverage");
    section.appendChild(makeEl(
      "h3",
      null,
      (groupBy === "user" ? "User" : "Department") + " attribution coverage"
    ));
    section.appendChild(makeEl(
      "p",
      null,
      "Missing, inaccessible, ambiguous, or protected identity evidence is not zero usage."
    ));
    section.appendChild(buildDataTable(
      "observe-attribution-coverage",
      "Attribution coverage by source and measure",
      ["Source", "Measure / component", "State", "Record counts", "Reason", "Next action"],
      rows
    ));
    return section;
  }

  function renderAttributionPartialFailuresNode(partialFailures) {
    partialFailures = Array.isArray(partialFailures) ? partialFailures : [];
    if (!partialFailures.length) return null;
    var section = makeEl("section", "observe-attribution-partial-failures");
    section.appendChild(makeEl("h3", null, "Partial source failures"));
    section.appendChild(makeEl(
      "p",
      null,
      "Successful source evidence remains visible; totals may be incomplete."
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

  function renderDepartmentAttribution(data, diagnostics, coverage, partialFailures, bounds) {
    data = data || {};
    renderAttributionControlsFromData(data);
    var metric = data.metric || "usage";
    var groupBy = data.group_by || "department";
    var rows = (data.rows || []).filter(function (row) {
      return groupBy === "user"
        ? row.kind === "user" || row.kind === "other_users"
        : row.kind === "department";
    });
    var userRank = 0;
    var tableRows = rows.map(function (row) {
      var label;
      if (row.kind === "user") userRank += 1;
      if (row.filter_token) {
        label = makeEl(
          "a",
          "observe-attribution-link",
          row.kind === "user" ? (row.raw_identity || "Identity not reported") : (row.department_label || "Department")
        );
        if (row.kind === "user") {
          label.appendChild(document.createElement("br"));
          label.appendChild(makeEl("code", null, row.user_key || "Pseudonym not reported"));
        }
        var params = new URLSearchParams();
        FILTER_KEYS.forEach(function (key) {
          if (appliedFilters[key]) params.set(key, appliedFilters[key]);
        });
        params.set("view", "departments");
        params.set("attribution_metric", metric);
        params.set("attribution_group_by", "user");
        if (row.kind === "user") {
          params.set("user_filter_token", String(row.filter_token));
          if (appliedFilters.department_filter_token) {
            params.set("department_filter_token", appliedFilters.department_filter_token);
          }
        } else {
          params.set("department_filter_token", String(row.filter_token));
        }
        if (metric === "cost") {
          var summary = data.summary || {};
          if (summary.period_id) params.set("attribution_cost_period_id", String(summary.period_id));
          if (summary.component_id) params.set("attribution_cost_component_id", String(summary.component_id));
        }
        label.href = window.location.pathname + "?" + params.toString();
      } else {
        label = makeEl(
          "span",
          null,
          row.kind === "other_users" ? "Other users" : (row.department_label || "Department")
        );
      }
      var usage = row.usage || {};
      var measure;
      if (metric === "cost") {
        measure = row.cost
          ? renderCostAmountNode(row.cost.amount, row.cost.currency)
          : makeEl("span", "metric-missing", "Cost unavailable for this " + String(row.kind || "group").replace("_", " "));
      } else {
        measure = renderMaybeMissing(usage.invocations, { suffix: " invocations" });
      }
      return [
        label,
        row.kind === "user"
          ? makeEl("span", null, "Rank " + String(userRank))
          : renderMaybeMissing(row.member_count),
        measure,
        renderMaybeMissing(usage.input_tokens),
        renderMaybeMissing(usage.output_tokens),
      ];
    });
    var table = tableRows.length ? buildDataTable(
      "observe-department-table",
      groupBy === "user" ? "User attribution" : "Department attribution",
      [groupBy === "user" ? "Eligible principal" : "Department", groupBy === "user" ? "Rank context" : "Members", metric === "cost" ? "Allocated cost" : "Usage", "Input tokens", "Output tokens"],
      tableRows
    ) : emptyStateNode("No " + groupBy + " attribution data found. This is not reported usage, not zero usage.");
    var content = [
      renderDiagnosticsJs(diagnostics || {}),
    ];
    if (groupBy === "user" && data.access_boundary === "delegated" &&
        rows.filter(function (row) { return row.kind === "user"; }).length === 1) {
      content.push(makeEl(
        "p",
        "observe-protected-context",
        "Selected eligible principal: This delegated, private view contains only the selected principal context."
      ));
    }
    content.push(
      renderAttributionSummary(data.summary, groupBy),
      groupBy === "user" ? makeEl(
        "p",
        "observe-hint observe-attribution-ranking",
        "Users are ranked by " + (metric === "cost" ? "allocated cost" : "invocations") +
          "; ties are ordered by pseudonymous key. Other users preserves omitted totals."
      ) : null,
      table,
      renderCostBoundsNode(bounds),
      renderAttributionCoverage(coverage, groupBy),
      renderAttributionPartialFailuresNode(partialFailures || []),
    );
    setViewContent("departments", content);
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
      renderAgents(body.data, body.diagnostics, body.bounds);
    } else if (view === "usage") {
      renderUsage(body.data, body.diagnostics, body.bounds);
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
    } else if (view === "departments") {
      renderDepartmentAttribution(
        body.data,
        body.diagnostics,
        body.coverage,
        body.partial_failures,
        body.bounds
      );
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

  function buildAttributionPayload(manual) {
    var metric = appliedFilters.attribution_metric || "usage";
    return {
      metric: metric,
      group_by: appliedFilters.attribution_group_by || "department",
      filters: {
        foundry_resource_id: appliedFilters.foundry_resource_id || null,
        project_resource_id: appliedFilters.project_resource_id || null,
        agent_id: appliedFilters.agent_id || null,
        model: appliedFilters.model || null,
        tool_name: appliedFilters.tool_name || null,
        run_key: appliedFilters.run_key || null,
        start: appliedFilters.start,
        end: appliedFilters.end,
        department_filter_token: appliedFilters.department_filter_token || null,
        user_filter_token: appliedFilters.user_filter_token || null,
        cost_period_id: metric === "cost" ? (appliedFilters.attribution_cost_period_id || null) : null,
        cost_component_id: metric === "cost" ? (appliedFilters.attribution_cost_component_id || null) : null,
      },
      refresh: manual === true,
    };
  }

  function fetchAttributionData(payload, controller, myToken) {
    return fetch("/api/observe/attribution", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    })
      .then(function (response) {
        if (myToken !== requestToken) return null;
        if (!response.ok) {
          setRefreshStatus("Refresh failed");
          return null;
        }
        return response.json().then(function (body) {
          if (myToken !== requestToken) return null;
          renderObserveResponse(body);
          setRefreshStatus("Refreshed " + compactTimestamp(new Date().toISOString()));
          return body;
        });
      })
      .catch(function (error) {
        if (error && error.name === "AbortError") return null;
        if (myToken === requestToken) setRefreshStatus("Refresh failed");
        return null;
      });
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
      page: currentPage,
      page_size: currentPageSize,
      search: currentSearch || null,
      sort_by: currentSortBy || null,
      sort_direction: currentSortDirection,
    };
    payload = currentView === "cost" ? buildCostPayload(manual) : payload;
    if (currentView === "departments") {
      return fetchAttributionData(buildAttributionPayload(manual), controller, myToken);
    }

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
          setRefreshStatus("Refreshed " + compactTimestamp(new Date().toISOString()));
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
        var preservedAttributionFilters = document.getElementById("observe-attribution-filter-form") ? {
          department_filter_token: appliedFilters.department_filter_token || "",
          user_filter_token: appliedFilters.user_filter_token || "",
          attribution_group_by: appliedFilters.attribution_group_by || "department",
          attribution_metric: appliedFilters.attribution_metric || "usage",
          attribution_cost_period_id: appliedFilters.attribution_cost_period_id || "",
          attribution_cost_component_id: appliedFilters.attribution_cost_component_id || "",
        } : null;
        appliedFilters = draftFilters;
        if (preservedCostFilters) {
          appliedFilters.cost_period_id = preservedCostFilters.cost_period_id;
          appliedFilters.cost_component_id = preservedCostFilters.cost_component_id;
          appliedFilters.cost_breakdown = preservedCostFilters.cost_breakdown;
          appliedFilters.cost_agent_key = preservedCostFilters.cost_agent_key;
        }
        if (preservedAttributionFilters) {
          appliedFilters.department_filter_token = preservedAttributionFilters.department_filter_token;
          appliedFilters.user_filter_token = preservedAttributionFilters.user_filter_token;
          appliedFilters.attribution_group_by = preservedAttributionFilters.attribution_group_by;
          appliedFilters.attribution_metric = preservedAttributionFilters.attribution_metric;
          appliedFilters.attribution_cost_period_id = preservedAttributionFilters.attribution_cost_period_id;
          appliedFilters.attribution_cost_component_id = preservedAttributionFilters.attribution_cost_component_id;
        }
        resetPaging(false);
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
    var attributionForm = document.getElementById("observe-attribution-filter-form");
    if (attributionForm) {
      var attributionFields = {
        attribution_metric: attributionForm.querySelector('[data-attribution-filter="metric"]'),
        attribution_group_by: attributionForm.querySelector('[data-attribution-filter="group_by"]'),
        attribution_cost_period_id: attributionForm.querySelector('[data-attribution-filter="cost_period_id"]'),
        attribution_cost_component_id: attributionForm.querySelector('[data-attribution-filter="cost_component_id"]'),
      };
      Object.keys(attributionFields).forEach(function (key) {
        var field = attributionFields[key];
        if (field && appliedFilters[key]) field.value = appliedFilters[key];
      });
      attributionForm.addEventListener("submit", function (event) {
        event.preventDefault();
        Object.keys(attributionFields).forEach(function (key) {
          var field = attributionFields[key];
          appliedFilters[key] = field && field.value ? field.value : "";
        });
        if (appliedFilters.attribution_group_by !== "user") {
          appliedFilters.user_filter_token = "";
          appliedFilters.department_filter_token = "";
        }
        currentView = "departments";
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
      link.addEventListener("click", function (event) {
        event.preventDefault();
        var nextView = link.getAttribute("data-observe-nav-link");
        if (nextView !== currentView) resetPaging(true);
        activateView(nextView);
        pushUrl();
        // Switching views queries a different `ObserveQuery.view`, so the
        // newly active view must be fetched -- otherwise it would only ever
        // show its initial server-rendered snapshot.
        fetchObserveData(false);
      });
    });
    window.addEventListener("popstate", function () {
      appliedFilters = readAppliedFromUrl();
      populateFormFromApplied(form);
      activateView(currentView);
      fetchObserveData(false);
    });
    activateView(currentView);
    setupAgentOpsThemeToggle();
    enhanceSortableTables(document);
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
    attribution_enabled: bool = False,
    department_attribution: Any = None,
    attribution_cost_available: bool = False,
    attribution_cost_periods: Sequence[Any] = (),
    attribution_cost_components: Sequence[Any] = (),
    attribution_coverage: Sequence[Any] = (),
    attribution_partial_failures: Sequence[Any] = (),
    attribution_bounds: Any = None,
    overview_trends: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Assemble the full Observe HTML document.

    Cost and attribution are independently additive and opt-in. Their
    navigation, controls, and sections are absent unless explicitly enabled.
    This preserves the existing operational views by default.
    """
    effective_active_view = active_view
    if active_view == "cost" and not cost_enabled:
        effective_active_view = "overview"
    if active_view == "departments" and not attribution_enabled:
        effective_active_view = "overview"
    nav = render_observe_nav(
        effective_active_view,
        cost_enabled=cost_enabled,
        attribution_enabled=attribution_enabled,
    )
    filters = render_filter_bar(scope_label)
    overview = render_overview_cards(
        overview_metrics, diagnostics=diagnostics, trends=overview_trends
    )
    agents_html = render_agents_table(agents, diagnostics=diagnostics)
    usage_html = render_models_usage_table(usage, diagnostics=diagnostics)
    tools_html = render_tools_table(tools, diagnostics=diagnostics, bounds=tools_bounds)
    runs_html = render_runs_table(runs, diagnostics=diagnostics, bounds=runs_bounds)
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
  <section id="cost" data-observe-panel role="tabpanel"
           aria-labelledby="observe-tab-cost" hidden>
    <h2 id="cost-heading">Cost</h2>
    {cost_controls}
    <div id="cost-content" data-observe-view-content="cost">{cost_html}</div>
  </section>"""
    attribution_section = ""
    if attribution_enabled:
        cost_available = bool(
            attribution_cost_available
            or attribution_cost_periods
            or attribution_cost_components
            or _get(department_attribution, "metric") == "cost"
        )
        attribution_controls = render_attribution_controls(
            department_attribution,
            cost_available=cost_available,
            period_options=attribution_cost_periods,
            component_options=attribution_cost_components,
        )
        attribution_html = render_department_view(
            department_attribution,
            diagnostics=diagnostics,
            coverage=attribution_coverage,
            partial_failures=attribution_partial_failures,
            bounds=attribution_bounds,
        )
        attribution_section = f"""
  <section id="departments" data-observe-panel role="tabpanel"
           aria-labelledby="observe-tab-departments" hidden>
    <h2 id="departments-heading">Departments</h2>
    {attribution_controls}
    <div id="departments-content" data-observe-view-content="departments">{attribution_html}</div>
  </section>"""

    scope_subtitle = html_escape(scope_label) if scope_label else "Runtime observability"
    theme_toggle = ui_theme.render_theme_toggle(
        control_id="observe-theme-toggle", extra_class="observe-theme-toggle"
    )
    header_html = f"""  <header class="aos-header observe-header">
    <div class="aos-brand-block">
      <h1 class="aos-brand observe-brand">AgentOps Observe</h1>
      <p class="aos-subtitle observe-subtitle">{scope_subtitle}</p>
    </div>
    <div class="aos-header-actions observe-header-actions">
      <a class="aos-link observe-cockpit-link" href="/" data-theme-link>&#8592; Cockpit</a>
      {theme_toggle}
    </div>
  </header>"""

    return f"""<!doctype html>
<html lang="en" data-theme="dark">
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
<main id="observe-app" class="aos-app observe-app" data-observe-active-view="{html_escape(effective_active_view)}">
{header_html}
  {nav}
  {filters}
  <section id="overview" data-observe-panel role="tabpanel"
           aria-labelledby="observe-tab-overview">
    <h2 id="overview-heading">Overview</h2>
    <div id="overview-content" data-observe-view-content="overview">{overview}</div>
  </section>
  <section id="agents" data-observe-panel role="tabpanel"
           aria-labelledby="observe-tab-agents" hidden>
    <h2 id="agents-heading">Agents</h2>
    <div id="agents-content" data-observe-view-content="agents">{agents_html}</div>
    <div id="agent-detail-content" aria-live="polite" data-observe-agent-detail-content></div>
  </section>
  <section id="usage" data-observe-panel role="tabpanel"
           aria-labelledby="observe-tab-usage" hidden>
    <h2 id="usage-heading">Models and usage</h2>
    <div id="usage-content" data-observe-view-content="usage">{usage_html}</div>
  </section>
  <section id="tools" data-observe-panel role="tabpanel"
           aria-labelledby="observe-tab-tools" hidden>
    <h2 id="tools-heading">Tools</h2>
    <div id="tools-content" data-observe-view-content="tools">{tools_html}</div>
  </section>
  <section id="runs" data-observe-panel role="tabpanel"
           aria-labelledby="observe-tab-runs" hidden>
    <h2 id="runs-heading">Runs</h2>
    <div id="runs-content" data-observe-view-content="runs">{runs_html}</div>
  </section>
  {attribution_section}
  {cost_section}
</main>
<script>{_OBSERVE_SCRIPT}</script>
</body>
</html>
"""
