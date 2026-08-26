"""Deterministic visual-regression coverage for the Observe redesign (#459).

Because Playwright / a real browser is not guaranteed in CI, "visual
regression" here is a *deterministic HTML/CSS snapshot* of the rendered Observe
document plus focused structural assertions. Rendering is pure (no clock, no
RNG, no network), so a fixed fixture always produces byte-identical HTML.

Regenerate the committed golden snapshots after an intentional design change::

    AGENTOPS_UPDATE_SNAPSHOTS=1 python -m pytest tests/unit/test_observe_ui_visual.py

The theme-parity, six-state, accessibility and privacy tests below guard the
invariants the snapshot alone cannot (token equality with ``ui_theme``, no
``prefers-color-scheme`` drift, every designed state renders accessibly, and no
PII / receiver addresses leak into the markup).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentops.agent import ui_theme
from agentops.agent.observe import ui


SNAPSHOT_DIR = Path(__file__).parent / "__snapshots__"
_UPDATE = os.environ.get("AGENTOPS_UPDATE_SNAPSHOTS") == "1"

# A single frozen instant drives every "refreshed_at" so the HTML is stable.
FROZEN = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Deterministic fixtures (inline, frozen)
# ---------------------------------------------------------------------------


def _overview_metrics() -> list[dict]:
    """Executive KPI cards with tone, delta chip, caption and sparkline."""
    spark = [
        {
            "label": "Invocations",
            "points": [("Mon", 120), ("Tue", 138), ("Wed", 150), ("Thu", 172)],
        }
    ]
    return [
        {
            "title": "Invocations",
            "value": 580,
            "tone": "info",
            "caption": "Last 24 hours",
            "delta": {"value": "+12%", "direction": "up", "tone": "ok", "label": "vs prior"},
            "series": spark,
        },
        {
            "title": "Failure rate",
            "value": 1.4,
            "unit": "%",
            "tone": "ok",
            "caption": "Errors / invocations",
            "delta": {"value": "-0.3pp", "direction": "down", "tone": "ok"},
        },
        {
            "title": "p95 latency",
            "value": 820,
            "unit": " ms",
            "tone": "warn",
            "caption": "95th percentile",
            "delta": {"value": "+40 ms", "direction": "up", "tone": "warn"},
        },
        {
            "title": "Tokens",
            "value": 1_240_000,
            "tone": "muted",
            "caption": "Input + output",
        },
    ]


def _overview_trends() -> list[dict]:
    """First-class operational trend charts below the KPI grid."""
    return [
        {
            "title": "Invocations & failures",
            "unit": "",
            "series": [
                {"label": "Invocations", "points": [("Mon", 120), ("Tue", 138), ("Wed", 150), ("Thu", 172)]},
                {"label": "Failures", "points": [("Mon", 3), ("Tue", 2), ("Wed", 4), ("Thu", 2)]},
            ],
        },
        {
            "title": "Latency (p95, ms)",
            "unit": " ms",
            "series": [
                {"label": "p95", "points": [("Mon", 760), ("Tue", 790), ("Wed", 810), ("Thu", 820)]},
            ],
        },
    ]


def _render_full_page() -> str:
    return ui.render_observe_page(
        active_view="overview",
        scope_label="Foundry project: project-a",
        overview_metrics=_overview_metrics(),
        overview_trends=_overview_trends(),
    )


# ---------------------------------------------------------------------------
# Snapshot helper
# ---------------------------------------------------------------------------


def _assert_snapshot(name: str, content: str) -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOT_DIR / name
    if _UPDATE or not path.exists():
        path.write_text(content, encoding="utf-8")
        if not _UPDATE:
            pytest.skip(f"golden snapshot created: {path.name} (re-run to compare)")
        return
    expected = path.read_text(encoding="utf-8")
    assert content == expected, (
        f"Rendered Observe HTML drifted from {path.name}. If this change is "
        f"intentional, regenerate with AGENTOPS_UPDATE_SNAPSHOTS=1."
    )


# ---------------------------------------------------------------------------
# 1. Deterministic full-page snapshot
# ---------------------------------------------------------------------------


def test_observe_overview_snapshot_is_stable() -> None:
    html = _render_full_page()
    # Sanity: the page carries the redesigned shell before we snapshot it.
    assert 'data-theme="dark"' in html
    assert 'class="aos-app observe-app"' in html
    _assert_snapshot("observe_overview.html", html)


def test_observe_overview_snapshot_is_reproducible() -> None:
    # Purity guarantee: two renders of the same fixture are byte-identical.
    assert _render_full_page() == _render_full_page()


def test_observe_styles_snapshot_is_stable() -> None:
    _assert_snapshot("observe_styles.css", ui._OBSERVE_STYLES)


# ---------------------------------------------------------------------------
# 2. Theme parity with the shared ui_theme module
# ---------------------------------------------------------------------------


def test_observe_emits_same_core_tokens_as_ui_theme() -> None:
    styles = ui._OBSERVE_STYLES
    # Every canonical token name/value pair from the dark theme is present
    # verbatim, so Observe and Cockpit share one palette.
    for name in ui_theme.TOKEN_NAMES:
        dark_value = ui_theme.DARK_TOKENS[name]
        assert f"{name}: {dark_value};" in styles, f"missing dark token {name}"
    # The explicit light theme block is also emitted (deliberate, not OS-driven).
    assert '[data-theme="light"]' in styles
    for name in ui_theme.TOKEN_NAMES:
        light_value = ui_theme.LIGHT_TOKENS[name]
        assert f"{name}: {light_value};" in styles, f"missing light token {name}"


def test_observe_has_no_prefers_color_scheme_drift() -> None:
    # The whole point of the redesign: no independent OS-preference block that
    # could render Observe light while the Cockpit is dark.
    assert "prefers-color-scheme" not in ui._OBSERVE_STYLES
    assert "prefers-color-scheme" not in ui.render_observe_page()


def test_observe_declares_explicit_default_theme_on_html() -> None:
    html = ui.render_observe_page()
    assert '<html lang="en" data-theme="dark">' in html
    # color-scheme is pinned in CSS so form controls / scrollbars match.
    assert "color-scheme: dark" in ui._OBSERVE_STYLES


# ---------------------------------------------------------------------------
# 3. Every designed non-happy state renders accessibly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ui.OBSERVE_STATE_KINDS)
def test_state_panel_renders_every_designed_state(kind: str) -> None:
    html = ui.render_state_panel(kind, f"{kind} message", detail="secondary detail")
    assert f'data-observe-state="{kind}"' in html
    assert f"observe-state-{kind}" in html
    # Announced to assistive tech: error => alert, others => status.
    expected_role = "alert" if kind == "error" else "status"
    assert f'role="{expected_role}"' in html
    # A non-color glyph marker keeps the state legible without color.
    assert 'class="observe-state-icon" aria-hidden="true"' in html
    assert "secondary detail" in html


def test_loading_state_is_marked_busy() -> None:
    html = ui.render_state_panel("loading", "Loading data")
    assert 'aria-busy="true"' in html


def test_unknown_state_kind_is_rejected() -> None:
    with pytest.raises(ValueError):
        ui.render_state_panel("bogus", "nope")


def test_all_six_states_are_covered() -> None:
    assert ui.OBSERVE_STATE_KINDS == (
        "loading",
        "empty",
        "partial",
        "permission-denied",
        "disconnected",
        "error",
    )


# ---------------------------------------------------------------------------
# 4. Accessibility invariants for the new chart primitives
# ---------------------------------------------------------------------------


def test_trend_chart_is_accessible() -> None:
    chart = ui.render_trend_chart(
        "Invocations",
        [{"label": "count", "points": [("Mon", 1), ("Tue", 2), ("Wed", 3)]}],
    )
    assert 'role="img"' in chart
    assert "aria-label=" in chart
    assert "<title" in chart  # hover tooltip / accessible name
    assert "<desc" in chart
    # A visually-hidden data table backs the SVG so the data is not image-only.
    assert "<table" in chart


def test_overview_page_charts_expose_accessible_names() -> None:
    html = _render_full_page()
    # The trends section is labelled and its charts carry role="img".
    assert 'class="observe-overview-trends"' in html
    assert 'aria-label="Operational trends"' in html
    assert html.count('role="img"') >= len(_overview_trends())


def test_kpi_cards_are_grouped_and_labelled() -> None:
    html = _render_full_page()
    assert 'class="observe-card observe-metric-card' in html
    assert 'role="group"' in html
    # Delta chips are not color-only: they carry a direction glyph + value.
    assert "observe-card-delta" in html
    assert 'data-direction="up"' in html


# ---------------------------------------------------------------------------
# 5. Privacy invariants preserved (no PII / receiver addresses / secrets)
# ---------------------------------------------------------------------------


def test_rendered_page_has_no_pii_or_receiver_addresses() -> None:
    html = _render_full_page()
    lowered = html.lower()
    # No email-shaped receiver addresses anywhere in the markup.
    import re

    assert not re.search(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", lowered)
    for needle in ("mailto:", "smtp", "@example", "receiver", "recipient", "bearer "):
        assert needle not in lowered, f"unexpected sensitive token: {needle!r}"


def test_rendered_page_makes_no_external_requests() -> None:
    html = _render_full_page()
    # Self-contained: no CDN, no <link>, no external script src.
    assert "cdn." not in html
    assert "<link " not in html
    assert 'src="http' not in html
    assert "https://fonts" not in html


def test_theme_toggle_uses_no_persistent_storage() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "setupThemeToggle" in script
    for banned in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
        assert banned not in script


# ---------------------------------------------------------------------------
# 6. URL-driven filters still round-trip
# ---------------------------------------------------------------------------


def test_filter_query_keys_round_trip_in_markup() -> None:
    html = _render_full_page()
    for key in ui.OBSERVE_FILTER_QUERY_KEYS:
        # Each allow-listed filter key is bound to a draft-filter control.
        assert f'data-draft-filter="{key}"' in html


def test_no_filter_keys_outside_allow_list() -> None:
    html = _render_full_page()
    import re

    # Only inspect static markup, not the client script that assembles
    # ``data-draft-filter`` attributes from a template literal at runtime.
    without_script = re.sub(r"<script\b.*?</script>", "", html, flags=re.DOTALL)
    bound = set(re.findall(r'data-draft-filter="([^"]+)"', without_script))
    assert bound
    assert bound <= set(ui.OBSERVE_FILTER_QUERY_KEYS)
