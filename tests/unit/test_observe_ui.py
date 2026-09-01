"""Tests for the hosted Observe frontend fragments in ``agentops.agent.observe.ui``.

Covers T050-T054, T058, and T062: markup/ARIA structure, draft/applied filter
separation, URL-persistence safety, refresh/staleness behavior, source
labels/last-seen/observed-usage wording, chart accessibility and non-color
distinction, protected-content loading, and coverage/troubleshooting
semantics (including zero-versus-missing rendering).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import inspect
from zoneinfo import ZoneInfo

import re

import pytest

from agentops.agent.observe import ui
from agentops.agent.observe.queries import MAX_ROWS_PER_QUERY
from agentops.agent import ui_theme
from agentops.core.cost import CostPeriodRef
from agentops.core.observe import CostEstimate, CoverageResult, GenerativeAIContent, ModelUsage
from fixtures.observe import make_run_usage_rows_at_scale


def _dt(hour: int = 0) -> datetime:
    return datetime(2024, 1, 1, hour, 0, 0, tzinfo=timezone.utc)


PROJECT = (
    "/subscriptions/11111111-1111-1111-1111-111111111111/"
    "resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/foundry/"
    "projects/project-a"
)


# ---------------------------------------------------------------------------
# Constants (T050/T051)
# ---------------------------------------------------------------------------


def test_default_range_and_refresh_constants() -> None:
    assert ui.DEFAULT_RANGE_HOURS == 7 * 24
    assert ui.AUTO_REFRESH_MS == 5 * 60 * 1000


def test_observe_views_and_labels_cover_all_required_surfaces() -> None:
    assert ui.OBSERVE_VIEWS == ("overview", "runs", "agents", "usage", "tools")
    for view in ui.OBSERVE_VIEWS:
        assert view in ui.OBSERVE_VIEW_LABELS


def test_observe_view_wire_names_map_internal_ids_to_openapi_view_enum() -> None:
    # contracts/observe-api.openapi.yaml spells the `ObserveQuery.view` enum
    # as [overview, agents, models, tools, runs]; the internal "usage" id (used
    # throughout DOM ids/CSS/labels) must be translated to "models" only when
    # building the outgoing wire payload.
    assert ui.OBSERVE_VIEW_WIRE_NAMES == {
        "overview": "overview",
        "runs": "runs",
        "agents": "agents",
        "usage": "models",
        "tools": "tools",
    }
    for view in ui.OBSERVE_VIEWS:
        assert view in ui.OBSERVE_VIEW_WIRE_NAMES


def test_filter_query_keys_exclude_raw_content_fields() -> None:
    raw_content_fields = {
        "input_messages",
        "output_messages",
        "system_instructions",
        "tool_content",
        "evaluation_explanation",
        "trace_id",
        "span_id",
    }
    assert set(ui.OBSERVE_FILTER_QUERY_KEYS).isdisjoint(raw_content_fields)
    assert ui.OBSERVE_FILTER_QUERY_KEYS == (
        "foundry_resource_id",
        "project_resource_id",
        "agent_id",
        "model",
        "tool_name",
        "run_key",
        "window_preset",
        "start",
        "end",
    )


# ---------------------------------------------------------------------------
# html_escape
# ---------------------------------------------------------------------------


def test_html_escape_escapes_all_special_characters() -> None:
    assert ui.html_escape("<a href=\"x\">&'</a>") == "&lt;a href=&quot;x&quot;&gt;&amp;'&lt;/a&gt;"


def test_html_escape_none_renders_empty_string() -> None:
    assert ui.html_escape(None) == ""


# ---------------------------------------------------------------------------
# Navigation (T050)
# ---------------------------------------------------------------------------


def test_render_observe_nav_marks_active_view_current() -> None:
    html = ui.render_observe_nav("agents")
    assert '<nav class="observe-nav" aria-label="Observe views">' in html
    assert 'data-observe-nav-link="agents" class="observe-nav-link" role="tab"' in html
    assert 'aria-controls="agents" aria-selected="true"' in html
    assert 'aria-controls="overview" aria-selected="false"' in html


def test_render_observe_nav_renders_every_view_once() -> None:
    html = ui.render_observe_nav()
    for view in ui.OBSERVE_VIEWS:
        assert html.count(f'data-observe-nav-link="{view}"') == 1
    assert 'data-observe-nav-link="cost"' not in html
    positions = [
        html.index(f'data-observe-nav-link="{view}"') for view in ui.OBSERVE_VIEWS
    ]
    assert positions == sorted(positions)


def test_render_observe_nav_only_adds_cost_when_enabled() -> None:
    html = ui.render_observe_nav("cost", cost_enabled=True)
    assert html.count('data-observe-nav-link="cost"') == 1
    assert (
        'aria-controls="cost" aria-selected="true"'
        in html
    )
    assert ">Cost<" in html


# ---------------------------------------------------------------------------
# Department attribution (issue #444 T018/T029)
# ---------------------------------------------------------------------------


def _department_data(*, metric: str = "usage", cost: object = None) -> dict[str, object]:
    usage = {
        "invocations": 12,
        "input_tokens": 120,
        "output_tokens": 60,
        "tool_invocations": 3,
        "active_session_seconds": "45.5",
    }
    row = {
        "kind": "department",
        "department_id": "engineering-internal",
        "department_label": 'Engineering <Core>',
        "filter_token": "dep1.g7.opaque_token-only",
        "member_count": 4,
        "usage": usage,
        "cost": cost,
        "mapping_state": "mapped",
    }
    if metric == "cost":
        summary: dict[str, object] = {
            "metric": "cost",
            "period_id": "august",
            "component_id": "ptu",
            "declared_total": "100.00",
            "attributed_amount": "75.00",
            "unattributed_amount": "20.00",
            "unallocated_amount": "5.00",
            "currency": "USD",
            "currency_minor_units": 2,
            "allocation_key": "weighted_tokens",
            "confidence": "high",
            "total_usage": usage,
            "attributed_usage": usage,
            "unattributed_usage": {**usage, "invocations": 2},
            "distinct_users": 5,
            "omitted_users": 0,
        }
    else:
        summary = {
            "metric": "usage",
            "total": usage,
            "attributed": usage,
            "unattributed": {**usage, "invocations": 2, "input_tokens": None},
            "distinct_users": 5,
            "omitted_users": 0,
        }
    return {
        "metric": metric,
        "group_by": "department",
        "access_boundary": "aggregate",
        "rows": [row],
        "summary": summary,
        "primary_measure": "allocated_amount" if metric == "cost" else "invocations",
        "calculated_at": "2026-08-25T12:00:00Z",
        "latest_observed_at": "2026-08-25T11:59:00Z",
    }


def _user_data(*, selected: bool = False, truncated: bool = False) -> dict[str, object]:
    usage = {
        "invocations": 8,
        "input_tokens": 80,
        "output_tokens": 40,
        "tool_invocations": 2,
        "active_session_seconds": "30",
    }
    rows: list[dict[str, object]] = [
        {
            "kind": "user",
            "user_key": "usr1.g7." + "a" * 64,
            "raw_identity": "synthetic-alex@example.test",
            "filter_token": "usr-token.opaque-only",
            "department_id": "engineering-internal",
            "department_label": "Engineering",
            "usage": usage,
            "cost": None,
            "mapping_state": "mapped",
        }
    ]
    if truncated:
        rows.append(
            {
                "kind": "other_users",
                "member_count": 2,
                "usage": {**usage, "invocations": 3},
                "cost": None,
                "mapping_state": "not_applicable",
            }
        )
    return {
        "metric": "usage",
        "group_by": "user",
        "access_boundary": "delegated",
        "rows": rows,
        "summary": {
            "metric": "usage",
            "total": {**usage, "invocations": 10},
            "attributed": {**usage, "invocations": 10},
            "unattributed": {**usage, "invocations": 0},
            "distinct_users": 3 if truncated else 1,
            "omitted_users": 2 if truncated else 0,
        },
        "primary_measure": "invocations",
        "calculated_at": "2026-08-25T12:00:00Z",
        "latest_observed_at": "2026-08-25T11:59:00Z",
    }


def test_department_surface_is_strictly_opt_in() -> None:
    default_html = ui.render_observe_page()
    assert 'data-observe-nav-link="departments"' not in default_html
    assert 'id="departments"' not in default_html
    assert 'id="observe-attribution-filter-form"' not in default_html

    enabled_html = ui.render_observe_page(
        attribution_enabled=True,
        department_attribution=_department_data(),
    )
    assert 'data-observe-nav-link="departments"' in enabled_html
    assert '<section id="departments"' in enabled_html
    assert 'data-observe-view-content="departments"' in enabled_html


def test_department_controls_offer_usage_and_available_cost_selectors() -> None:
    html = ui.render_attribution_controls(
        _department_data(),
        cost_available=True,
        period_options=["august"],
        component_options=["ptu"],
    )
    assert 'data-attribution-filter="metric"' in html
    assert '<option value="usage" selected>Usage</option>' in html
    assert '<option value="cost">Cost</option>' in html
    assert 'data-attribution-filter="cost_period_id"' in html
    assert 'data-attribution-filter="cost_component_id"' in html
    assert 'id="observe-apply-attribution-filters"' in html


def test_department_controls_explain_unavailable_cost() -> None:
    html = ui.render_attribution_controls(_department_data(), cost_available=False)
    assert '<option value="cost" disabled>Cost</option>' in html
    assert "Cost attribution is unavailable" in html
    assert "valid cost model and allocatable cost" in html


def test_department_usage_renders_opaque_link_and_unmapped_summary() -> None:
    html = ui.render_department_view(_department_data())
    assert "Engineering &lt;Core&gt;" in html
    assert "dep1.g7.opaque_token-only" in html
    assert "department_filter_token=dep1.g7.opaque_token-only" in html
    assert "engineering-internal" not in html
    assert "Unmapped usage" in html
    assert "2 invocations" in html
    assert "Not reported" in html


def test_department_cost_renders_reconciliation_and_unavailable_row_explanation() -> None:
    unavailable = _department_data(metric="cost", cost=None)
    html = ui.render_department_view(unavailable)
    assert "Declared total" in html
    assert "100.00 USD" in html
    assert "Unmapped cost" in html
    assert "20.00 USD" in html
    assert "Unallocated cost" in html
    assert "5.00 USD" in html
    assert "Cost unavailable for this department" in html


def test_department_script_has_endpoint_url_safety_and_server_parity() -> None:
    script = ui._OBSERVE_SCRIPT
    assert '"department_filter_token", "user_filter_token", "attribution_group_by"' in script
    assert 'fetch("/api/observe/attribution"' in script
    assert 'group_by: appliedFilters.attribution_group_by || "department"' in script
    assert "function renderDepartmentAttribution(data, diagnostics, coverage, partialFailures, bounds)" in script
    assert "function renderAttributionSummary(summary, groupBy)" in script
    assert "function renderAttributionControlsFromData(data)" in script
    assert "Cost attribution is unavailable" in script
    assert "Unmapped usage" in script
    assert "Unmapped cost" in script
    assert "innerHTML" not in script
    for forbidden in ("raw_identity", "user_key", "department_id", "department_label", "group_id"):
        url_block = script.split("function syncUrl() {")[1].split("\n  }\n")[0]
        assert forbidden not in url_block


def test_user_drilldown_renders_delegated_principal_ranking_and_opaque_selector() -> None:
    html = ui.render_department_view(_user_data(selected=True))
    assert 'aria-label="User attribution"' in html
    assert "Selected eligible principal" in html
    assert "synthetic-alex@example.test" in html
    assert "usr1.g7." + "a" * 64 in html
    assert "Rank 1" in html
    assert "ties are ordered by pseudonymous key" in html
    assert "user_filter_token=usr-token.opaque-only" in html
    assert "synthetic-alex%40example.test" not in html
    assert "engineering-internal" not in html


def test_user_drilldown_explains_bounds_other_users_and_partial_failures() -> None:
    html = ui.render_department_view(
        _user_data(truncated=True),
        bounds={"rows_shown": 2, "rows_total_in_scope": 3, "truncated": True},
        partial_failures=[
            {
                "source_id": "source-safe",
                "status": "timeout",
                "reason": "Identity query timed out.",
                "next_action": "Retry after restoring access.",
            }
        ],
    )
    assert "Other users" in html
    assert "Showing 2 of 3 rows in scope" in html
    assert "Results are truncated to the highest-ranked users plus Other users" in html
    assert "Partial source failures" in html
    assert "Successful source evidence remains visible" in html
    assert "Identity query timed out" in html


def test_user_selector_and_script_preserve_only_opaque_user_state() -> None:
    controls = ui.render_attribution_controls(_user_data())
    assert 'data-attribution-filter="group_by"' in controls
    assert '<option value="user" selected>Users in selected department</option>' in controls
    script = ui._OBSERVE_SCRIPT
    assert 'group_by: appliedFilters.attribution_group_by || "department"' in script
    assert (
        "filters.user_filter_token = appliedFilters.user_filter_token || null"
        in script
    )
    assert "localStorage.setItem" not in script
    assert "sessionStorage.setItem" not in script
    sync_url = script.split("function syncUrl() {")[1].split("\n  }\n")[0]
    for forbidden in ("raw_identity", "user_key", "department_label", "group_id"):
        assert forbidden not in sync_url


@pytest.mark.parametrize(
    ("state", "label"),
    [
        ("available", "Available"),
        ("partial", "Partial"),
        ("not_reported", "Not reported"),
        ("ambiguous", "Ambiguous"),
        ("inaccessible", "Inaccessible"),
        ("protected_or_unavailable", "Protected or unavailable"),
        ("error", "Error"),
    ],
)
def test_attribution_coverage_renders_actionable_states_and_missing_counts(
    state: str, label: str
) -> None:
    coverage = [
        {
            "source_id": f"source-{state}",
            "dimension": "user_attribution",
            "state": state,
            "reason": f"Reason for {state}.",
            "next_action": f"Action for {state}.",
            "metric": "cost",
            "attribution_level": "user",
            "component_id": "ptu-component",
            "eligible_records": None if state == "inaccessible" else 10,
            "identified_records": None if state == "inaccessible" else 8,
            "mapped_records": None if state == "inaccessible" else 7,
            "unattributed_records": None if state == "inaccessible" else 2,
            "ambiguous_records": None if state == "inaccessible" else 1,
            "returned_records": None if state == "inaccessible" else 8,
        }
    ]
    html = ui.render_department_view(_user_data(), coverage=coverage)
    assert label in html
    assert "Cost / ptu-component" in html
    assert f"Reason for {state}" in html
    assert f"Action for {state}" in html
    assert "is not zero usage" in html
    if state == "inaccessible":
        assert html.count("Not reported") >= 6


def test_attribution_missing_data_preserves_coverage_and_partial_failures() -> None:
    html = ui.render_department_view(
        None,
        coverage=[
            {
                "source_id": "source-protected",
                "state": "protected_or_unavailable",
                "metric": "usage",
                "reason": "Delegated access is unavailable.",
                "next_action": "Sign in again.",
            }
        ],
        partial_failures=[
            {
                "source_id": "source-timeout",
                "status": "timeout",
                "reason": "The source timed out.",
                "next_action": "Retry later.",
            }
        ],
    )
    assert "missing or protected evidence, not zero usage" in html
    assert "Protected or unavailable" in html
    assert "Delegated access is unavailable" in html
    assert "Partial source failures" in html
    assert "The source timed out" in html


def test_attribution_coverage_javascript_matches_server_fields_and_language() -> None:
    script = ui._OBSERVE_SCRIPT
    for field in (
        "component_id",
        "eligible_records",
        "identified_records",
        "mapped_records",
        "unattributed_records",
        "ambiguous_records",
        "returned_records",
    ):
        assert f"entry.{field}" in script
    assert '"Missing, inaccessible, ambiguous, or protected identity evidence is not zero usage."' in script
    assert "function renderAttributionPartialFailuresNode(partialFailures)" in script


# ---------------------------------------------------------------------------
# Filter bar (T050/T051)
# ---------------------------------------------------------------------------


def test_filter_bar_marks_every_filter_field_as_draft_only() -> None:
    html = ui.render_filter_bar()
    for key in ui.OBSERVE_FILTER_QUERY_KEYS:
        assert f'data-draft-filter="{key}"' in html


def test_filter_bar_has_explicit_apply_button_not_auto_apply() -> None:
    html = ui.render_filter_bar()
    assert 'id="observe-apply-filters"' in html
    assert "Apply filters" in html
    # No onchange/oninput inline auto-apply handlers anywhere in the form.
    assert "onchange" not in html
    assert "oninput" not in html


def test_filter_bar_has_manual_refresh_control() -> None:
    html = ui.render_filter_bar()
    assert 'id="observe-refresh-now"' in html
    assert "Refresh now" in html


def test_filter_bar_defaults_are_all_meaning_unfiltered() -> None:
    html = ui.render_filter_bar()
    assert html.count('placeholder="All') == 6  # foundry, project, agent, model, tool, run


def test_filter_bar_renders_optional_scope_label() -> None:
    html = ui.render_filter_bar(scope_label="project-a")
    assert "Scope:" in html
    assert "project-a" in html


def test_filter_bar_omits_scope_paragraph_when_absent() -> None:
    html = ui.render_filter_bar()
    assert '<p class="observe-scope">' not in html


# ---------------------------------------------------------------------------
# Faceted multi-select scope controls (T018 / spec 014)
# ---------------------------------------------------------------------------


SCOPE_DIMENSIONS = (
    "foundry_resource_id",
    "project_resource_id",
    "agent_id",
    "model",
    "tool_name",
    "run_key",
)


def test_scope_multiselect_changes_remain_draft_until_apply() -> None:
    html = ui.render_filter_bar()
    script = ui._OBSERVE_SCRIPT

    for dimension in SCOPE_DIMENSIONS:
        assert f'data-scope-dimension="{dimension}"' in html
    assert "draftFilters" in script
    assert "appliedFilters" in script
    assert 'document.getElementById("observe-filter-form")' in script
    assert re.search(
        r'addEventListener\("submit",[\s\S]+?appliedFilters\s*=\s*'
        r'(?:Object\.assign\([^;]*draftFilters|draftFilters)',
        script,
    )
    # Editing a scope checkbox may refresh downstream facets, but only Apply
    # may run the view query. Other existing selectors intentionally refresh.
    scope_renderer = script[
        script.index("function renderScopeOptions(") :
        script.index("function loadScopeOptions(")
    ]
    assert 'addEventListener("change"' in scope_renderer
    assert "fetchObserveData(" not in scope_renderer


def test_scope_multiselect_round_trips_repeated_values_through_allowlisted_url_keys() -> None:
    script = ui._OBSERVE_SCRIPT

    assert "FILTER_KEYS.forEach" in script
    assert "params.getAll(key)" in script
    assert "params.append(key," in script
    for dimension in SCOPE_DIMENSIONS:
        assert dimension in ui.OBSERVE_FILTER_QUERY_KEYS


def test_unopened_scope_picker_preserves_url_selected_values_on_apply() -> None:
    script = ui._OBSERVE_SCRIPT
    draft_reader = script[
        script.index("function readDraftFromForm(") :
        script.index("function populateFormFromApplied(")
    ]

    assert "selectedValues = selectedScopeValues(scope);" in draft_reader
    assert draft_reader.index("selectedScopeValues(scope)") < draft_reader.index(
        "delete draft[key]"
    )


def test_preset_requests_send_absolute_filters_and_relative_window_intent() -> None:
    script = ui._OBSERVE_SCRIPT
    scope_loader = script[
        script.index("function loadScopeOptions(") :
        script.index("function refreshScopeOptionsToTheRight(")
    ]
    query_fetch = script[
        script.index("function fetchObserveData(") :
        script.index("function scheduleAutoRefresh(")
    ]

    assert "start: bounds.start" in script
    assert "end: bounds.end" in script
    assert "filters: observeFiltersForRequest(appliedFilters)" in query_fetch
    assert "payload.window = windowSelectionForRequest(appliedFilters);" in query_fetch
    assert "var filters = observeFiltersForRequest(draft);" in scope_loader
    assert "window: windowSelectionForRequest(draft)" in scope_loader


def test_truncated_scope_option_set_states_shown_count_against_total_only_when_bounded() -> None:
    script = ui._OBSERVE_SCRIPT

    assert "optionSet.truncated" in script
    assert "optionSet.total_observed" in script
    assert re.search(
        r"optionSet\.options\.length[\s\S]{0,240}?optionSet\.total_observed",
        script,
    )
    # The ratio is conditional: complete/unbounded sets must not imply truncation.
    assert re.search(
        r"if\s*\(\s*optionSet\.truncated[\s\S]{0,500}?"
        r"(?:textContent|innerText)",
        script,
    )


def test_zero_scope_selections_leave_dimension_unrestricted() -> None:
    script = ui._OBSERVE_SCRIPT

    assert re.search(
        r"(?:checked|selectedValues)[\s\S]{0,300}?"
        r"(?:length|filter)[\s\S]{0,300}?"
        r"(?:delete|undefined|null|\[\])",
        script,
    )


def test_scope_options_failure_or_timeout_reveals_free_text_fallback() -> None:
    html = ui.render_filter_bar()
    script = ui._OBSERVE_SCRIPT

    assert "/api/observe/scope-options" in script
    assert "AbortController" in script
    assert re.search(r"(?:setTimeout|timeout)", script, re.IGNORECASE)
    assert "data-scope-fallback" in html
    assert re.search(
        r"(?:catch|response\.ok)[\s\S]{0,800}?scope[\s-]?fallback",
        script,
        re.IGNORECASE,
    )


def test_scope_cascade_preserves_reachable_values_and_announces_named_removals() -> None:
    html = ui.render_filter_bar()
    script = ui._OBSERVE_SCRIPT
    cascade = script[
        script.index("function refreshScopeOptionsToTheRight(") :
        script.index("function initializeScopeControls(")
    ]
    renderer = script[
        script.index("function renderScopeOptions(") :
        script.index("function loadScopeOptions(")
    ]

    assert 'id="observe-scope-status"' in html
    assert 'role="status" aria-live="polite"' in html
    assert "scope.dataset.selectedValues = \"[]\";" not in cascade
    assert "downstream.reduce" in cascade
    assert "optionSet.invalidated_selections" in renderer
    assert "availableValues.indexOf(scopeOptionKey(dimension, value)) >= 0" in renderer
    assert "Removed unavailable selections: " in script


def test_scope_multiselect_controls_are_keyboard_operable() -> None:
    html = ui.render_filter_bar()

    assert 'type="checkbox"' in html
    assert 'id="observe-apply-filters"' in html
    assert 'type="submit"' in html
    assert "onclick=" not in html
    assert re.search(r"<(?:fieldset|div)[^>]+data-scope-dimension=", html)
    assert re.search(r"<(?:legend|label)\b", html)


def test_scope_option_labels_and_selections_cannot_add_generative_url_fields() -> None:
    script = ui._OBSERVE_SCRIPT
    url_writer = script[script.index("function buildStateUrl()") : script.index(
        "function syncUrl()"
    )]
    generative_fields = (
        "input_messages",
        "output_messages",
        "system_instructions",
        "tool_content",
        "evaluation_explanation",
        "prompt",
        "response",
    )

    assert "FILTER_KEYS.forEach" in url_writer
    assert "label" not in url_writer
    for field in generative_fields:
        assert field not in url_writer
        assert field not in ui.OBSERVE_FILTER_QUERY_KEYS


# ---------------------------------------------------------------------------
# Source labels / refreshed-at / last-seen (T052)
# ---------------------------------------------------------------------------


def test_window_presets_default_round_trip_and_custom_validation() -> None:
    html = ui.render_filter_bar()
    script = ui._OBSERVE_SCRIPT

    assert '<option value="7d" selected>7 days</option>' in html
    assert '<option value="custom">Custom</option>' in html
    assert 'data-custom-window hidden' in html
    assert '"window_preset"' in script
    assert 'applied.window_preset = "7d"' in script
    assert 'params.append(key, value)' in script
    assert 'windowPreset.addEventListener("change"' in script
    assert "Custom window end must be after start." in script
    assert "if (!validateDraftWindow(form, draftFilters)) return;" in script
    assert 'id="observe-filter-window_preset"' in html


def test_render_source_label_from_plain_string() -> None:
    assert "Source: foundry" in ui.render_source_label("foundry")


def test_render_source_label_from_object_prefers_source_kind() -> None:
    coverage = CoverageResult(
        source_id="src-1",
        dimension="recent_traces",
        state="available",
        reason="ok",
        next_action="none",
        refreshed_at=_dt(),
    )
    html = ui.render_source_label(coverage)
    assert "Source:" in html
    assert "src-1" in html


def test_render_refreshed_at_with_datetime() -> None:
    html = ui.render_refreshed_at(_dt(5))
    assert "<time" in html
    assert "2024-01-01T05:00:00Z" in html
    assert "Refreshed: Jan 1, 2024, 05:00 +00:00" in html


def test_render_refreshed_at_missing() -> None:
    html = ui.render_refreshed_at(None)
    assert "not yet refreshed" in html


def test_render_last_seen_uses_compact_utc_timestamp() -> None:
    html = ui.render_last_seen(_dt(3))
    assert 'datetime="2024-01-01T03:00:00Z"' in html
    assert ">2024-01-01 03:00:00 +00:00<" in html
    assert "not agent lifecycle status" not in html


def test_time_presentation_defaults_local_and_is_one_accessible_page_control() -> None:
    html = ui.render_observe_page()

    assert html.split("<script>", 1)[0].count('data-observe-timezone-basis') == 1
    assert '<option value="local" selected>Local</option>' in html
    assert '<option value="utc">UTC</option>' in html
    assert 'for="observe-timezone-basis"' in html
    assert 'aria-describedby="observe-timezone-help"' in html
    assert 'presentationTimeBasis = "local"' in html
    assert 'timezoneBasis.addEventListener("change"' in html


def test_timezone_change_rerenders_every_time_without_persisting_selection() -> None:
    script = ui._OBSERVE_SCRIPT

    assert script.index("function inputValueToUtcIso") < script.index(
        "function rerenderTemporalSurface"
    )
    assert script.index("function utcIsoToInputValue") < script.index(
        "function rerenderTemporalSurface"
    )
    assert 'document.querySelectorAll("[data-observe-time]").forEach(updatePresentedTime)' in script
    assert "rerenderTemporalSurface();" in script
    assert '"data-observe-timezone-basis"' in script
    assert '"timezone"' not in script.split("var FILTER_KEYS =", 1)[1].split(";", 1)[0]
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "document.cookie" not in script


def test_refresh_status_follows_time_controls_and_starts_honestly() -> None:
    html = ui.render_filter_bar()

    assert html.index('id="observe-timezone-basis"') < html.index(
        'id="observe-refresh-status"'
    )
    assert ">Not yet refreshed</span>" in html
    assert ".observe-refresh-status {" in ui._OBSERVE_STYLES
    assert "margin-left: auto;" in ui._OBSERVE_STYLES
    refreshed = ui.render_refreshed_at(_dt(5))
    assert 'data-observe-time-style="compact"' in refreshed
    assert "Jan 1, 2024" in refreshed


def test_timezone_selection_does_not_enter_address_or_query_identity() -> None:
    script = ui._OBSERVE_SCRIPT
    filter_declaration = script.split("var FILTER_KEYS =", 1)[1].split(";", 1)[0]
    state_url = script.split("function buildStateUrl()", 1)[1].split(
        "function syncUrl()", 1
    )[0]
    query_payload = script.split("function windowSelectionForRequest(", 1)[1].split(
        "function observeFiltersForRequest(", 1
    )[0]

    assert "timezone" not in filter_declaration
    assert "timezone" not in state_url
    assert query_payload.count('timezone_label: "UTC"') == 2
    assert "presentationTimeBasis" not in query_payload
    assert "resolvedOptions" not in query_payload
    assert 'if (!isNaN(moment.getTime())) applied[key] = moment.toISOString();' in script


def test_python_and_javascript_time_formatters_share_fixed_utc_contract() -> None:
    assert ui._format_full_timestamp(_dt(5)) == "2024-01-01 05:00:00 +00:00"
    assert ui._format_compact_timestamp(_dt(5)) == "Jan 1, 2024, 05:00 +00:00"
    script = ui._OBSERVE_SCRIPT
    assert 'function formatPresentationTimestamp(value, style, timeZoneOverride)' in script
    assert 'var compact = style === "compact";' in script
    assert 'hourCycle: "h23"' in script
    assert 'timeZoneName: "shortOffset"' in script
    assert 'return timePart(parts, "year") + "-" + timePart(parts, "month")' in script


def test_time_formatter_observes_daylight_saving_boundaries() -> None:
    pacific = ZoneInfo("America/Los_Angeles")
    before = datetime(2024, 3, 10, 9, 59, tzinfo=timezone.utc)
    after = datetime(2024, 3, 10, 10, 1, tzinfo=timezone.utc)

    assert ui._format_full_timestamp(before, pacific) == "2024-03-10 01:59:00 -08:00"
    assert ui._format_full_timestamp(after, pacific) == "2024-03-10 03:01:00 -07:00"
    assert 'new Intl.DateTimeFormat("en-US", options)' in ui._OBSERVE_SCRIPT


def test_render_last_seen_missing_uses_metric_missing_class() -> None:
    html = ui.render_last_seen(None)
    assert "metric-missing" in html
    assert "\u2014" in html
    assert "not reported" not in html.lower()


# ---------------------------------------------------------------------------
# Zero-vs-missing semantics
# ---------------------------------------------------------------------------


def test_render_maybe_missing_zero_is_not_missing() -> None:
    html = ui._render_maybe_missing(0)
    assert "metric-zero" in html
    assert "metric-missing" not in html
    assert ">0<" in html


def test_render_maybe_missing_none_is_missing() -> None:
    html = ui._render_maybe_missing(None)
    assert "metric-missing" in html
    assert "Not reported" in html


def test_render_maybe_missing_nonzero_uses_value_class() -> None:
    html = ui._render_maybe_missing(42)
    assert "metric-value" in html
    assert "42" in html


def test_render_failure_rate_zero_failures_is_zero_percent_not_missing() -> None:
    html = ui._render_failure_rate(10, 0)
    assert "metric-zero" in html
    assert "0%" in html


def test_render_failure_rate_missing_invocations_is_missing() -> None:
    html = ui._render_failure_rate(None, None)
    assert "metric-missing" in html


def test_render_failure_rate_zero_invocations_is_no_invocations_not_divide_by_zero() -> None:
    html = ui._render_failure_rate(0, 0)
    assert "No invocations" in html


def test_render_token_totals_omits_repeated_usage_disclaimer() -> None:
    html = ui._render_token_totals(100, 0)
    assert "observed usage, not billing data" not in html
    assert "metric-zero" in html  # output tokens == 0 rendered distinctly from missing
    assert "100" in html


def test_render_token_totals_missing_tokens_render_as_missing() -> None:
    html = ui._render_token_totals(None, None)
    assert html.count("metric-missing") == 2


# ---------------------------------------------------------------------------
# Overview cards (T050)
# ---------------------------------------------------------------------------


def test_render_overview_cards_empty_shows_no_data_found() -> None:
    html = ui.render_overview_cards([])
    assert "No data found for the selected filters." in html


def test_render_overview_cards_renders_title_source_and_refreshed_at() -> None:
    html = ui.render_overview_cards(
        [
            {
                "title": "Total invocations",
                "value": 12,
                "source": "foundry",
                "refreshed_at": _dt(1),
            }
        ]
    )
    assert "Total invocations" in html
    assert "Source: foundry" in html
    assert "2024-01-01T01:00:00Z" in html
    assert "metric-value" in html


def test_render_overview_cards_zero_value_is_distinct_from_missing() -> None:
    html_zero = ui.render_overview_cards([{"title": "Failures", "value": 0}])
    html_missing = ui.render_overview_cards([{"title": "Failures", "value": None}])
    assert "metric-zero" in html_zero
    assert "metric-missing" in html_missing


def _overview_summaries() -> list[dict[str, object]]:
    return [
        {
            "entity_family": "Runs",
            "label": "Runs",
            "coverage_state": "available",
            "figures": [
                {"label": "Runs observed", "value": 3, "unit": None, "tone": "info"},
                {
                    "label": "Run tokens consumed",
                    "value": 0,
                    "unit": "tokens",
                    "tone": "info",
                },
            ],
        },
        {
            "entity_family": "Agents",
            "label": "Agents",
            "coverage_state": "available",
            "figures": [
                {"label": "Agents observed", "value": 2, "unit": None, "tone": "info"}
            ],
        },
        {
            "entity_family": "Models",
            "label": "Models",
            "coverage_state": "no_data",
            "figures": [
                {"label": "Models observed", "value": 0, "unit": None, "tone": "info"}
            ],
        },
        {
            "entity_family": "Tools",
            "label": "Tools",
            "coverage_state": "available",
            "figures": [
                {"label": "Tool failures", "value": None, "unit": None, "tone": "warn"}
            ],
        },
    ]


def test_render_overview_groups_entity_headlines_with_runs_first() -> None:
    html = ui.render_overview_cards(list(reversed(_overview_summaries())))
    positions = [html.index(f'data-entity-family="{family}"') for family in (
        "runs", "agents", "models", "tools"
    )]
    assert positions == sorted(positions)
    assert "Run tokens consumed" in html
    assert "0 tokens" in html
    assert 'role="list"' in html
    assert html.count('role="listitem"') == 4
    assert "@media (max-width: 760px)" in ui._OBSERVE_STYLES
    assert "@media (max-width: 420px)" in ui._OBSERVE_STYLES


def test_render_overview_family_empty_state_is_not_a_reported_zero() -> None:
    html = ui.render_overview_cards(_overview_summaries())
    models = html.split('data-entity-family="models"', 1)[1].split("</section>", 1)[0]
    tools = html.split('data-entity-family="tools"', 1)[1].split("</section>", 1)[0]
    assert "No models data found for the selected scope and window." in models
    assert "metric-zero" not in models
    assert "Not reported" in tools
    assert "metric-missing" in tools


def test_script_overview_renders_aggregate_response_as_kpi_cards() -> None:
    script = ui._OBSERVE_SCRIPT
    block = script.split("function overviewMetricsFrom(data) {")[1].split("\n  }\n")[0]
    for title in (
        "Run invocations",
        "Run failures",
        "Run success rate",
        "Average run latency",
        "p95 run latency",
    ):
        assert f'title: "{title}"' in block
    assert "data.invocations !== undefined" in block
    assert "Array.isArray(data.summaries)" in block
    assert 'var familyOrder = ["Runs", "Agents", "Models", "Tools"];' in script


# ---------------------------------------------------------------------------
# Agents table (T050/T052)
# ---------------------------------------------------------------------------


def _agent(**overrides: object) -> dict[str, object]:
    payload = dict(
        key="agent-a",
        source_id="source-a",
        agent_id="agent-a",
        agent_name="Agent A",
        project_resource_id=PROJECT,
        foundry_resource_id=None,
        source_kind="foundry_hosted",
        model="gpt-4o",
        last_seen=_dt(2),
        invocations=10,
        failures=1,
        p95_latency_ms=123.0,
        input_tokens=100,
        output_tokens=200,
    )
    payload.update(overrides)
    return payload


def test_render_agents_table_empty_shows_no_data_found() -> None:
    html = ui.render_agents_table([])
    assert "No data found for the selected filters." in html


def test_render_agents_table_renders_expected_columns() -> None:
    html = ui.render_agents_table([_agent()])
    for heading in (
        "Name",
        "Agent ID",
        "Source",
        "Model",
        "Last seen",
        "Invocations",
        "Failure rate",
        "p95 latency",
        "Input tokens",
        "Output tokens",
        "Total tokens",
    ):
        assert f'data-label="{heading}"' in html
    assert "Agent A" in html
    assert "gpt-4o" in html


def test_render_agents_table_uses_seconds_and_totals_reported_columns() -> None:
    html = ui.render_agents_table(
        [
            _agent(invocations=10, failures=1, p95_latency_ms=12_763),
            _agent(invocations=20, failures=2, input_tokens=50, output_tokens=75),
        ]
    )
    assert "12.763 s" in html
    assert "<tfoot>" in html
    assert ">30<" in html
    assert ">150<" in html
    assert ">275<" in html


def test_render_agents_table_separates_name_agent_id_and_plain_source_kind() -> None:
    html = ui.render_agents_table([_agent()])
    assert ">Name<" in html
    assert ">Agent ID" in html
    assert "Foundry Hosted" in html
    assert 'class="observe-source-kind"' in html
    assert "observe-source-kind-badge" not in html
    assert "Stable technical identifier reported by agent telemetry." in html
    assert "agent-a" in html
    assert "source-a" in html


def test_observe_badges_use_filled_high_contrast_treatment() -> None:
    css = ui._OBSERVE_STYLES
    assert "min-height: 24px;" in css
    assert "font-size: 12px;" in css
    assert "font-weight: 700;" in css
    assert ".observe-badge.observe-tone-ok {" in css
    assert (
        "background: color-mix(in srgb, var(--observe-ok) 16%, "
        "var(--observe-card-bg));"
    ) in css
    assert (
        "border-color: color-mix(in srgb, var(--observe-ok) 52%, "
        "var(--observe-border));"
    ) in css


def test_render_agents_table_missing_name_and_agent_id_uses_dashes() -> None:
    html = ui.render_agents_table([_agent(agent_id=None, agent_name=None)])
    assert "Agent ID reported" not in html
    assert "Agent ID missing" not in html
    assert html.count("metric-missing") >= 3


def test_render_agents_table_zero_invocations_distinct_from_missing_latency() -> None:
    html = ui.render_agents_table(
        [_agent(invocations=0, failures=0, p95_latency_ms=None, input_tokens=None, output_tokens=None)]
    )
    assert "metric-zero" in html  # invocations == 0
    assert "metric-missing" in html  # latency/tokens not reported


def test_render_agents_table_accepts_plain_mapping_too() -> None:
    html = ui.render_agents_table(
        [
            {
                "agent_name": "Mapping Agent",
                "agent_id": "mapping-agent",
                "source_kind": "external_registered",
                "source_id": "source-mapping",
                "model": "custom-model",
                "last_seen": _dt(4),
                "invocations": 5,
                "failures": 0,
                "p95_latency_ms": 10.0,
                "input_tokens": 1,
                "output_tokens": 2,
            }
        ]
    )
    assert "Mapping Agent" in html
    assert "External Registered" in html


def test_render_agents_table_omits_technical_diagnostics_banner() -> None:
    diagnostics = {
        "started_at": _dt(0),
        "completed_at": _dt(0),
        "duration_ms": 500,
        "source_count": 2,
        "successful_sources": 1,
        "partial_sources": 1,
        "failed_sources": 0,
        "cache_status": "miss",
    }
    html = ui.render_agents_table([_agent()], diagnostics=diagnostics)
    assert "observe-diagnostics-banner" not in html
    assert "Sources queried" not in html


def test_drilldown_controls_preserve_source_and_project_scope() -> None:
    script = ui._OBSERVE_SCRIPT
    for selector_field in ("source_id", "project_resource_id"):
        assert f"{selector_field}:" in script
    assert "body.complete === false" in script
    assert "Some activity sources could not be loaded." in script
    assert "No matching activity was found for this row." in script


def test_all_observe_table_columns_are_upgraded_to_sortable_headers() -> None:
    script = ui._OBSERVE_SCRIPT
    assert 'var headers = table.querySelectorAll("thead th")' in script
    assert 'makeEl("button", "observe-sort-button", label)' in script
    assert 'other.setAttribute("aria-sort", other === header ? direction : "none")' in script
    assert 'content: "\\2195"' in ui._OBSERVE_STYLES
    assert 'content: "\\2191"' in ui._OBSERVE_STYLES
    assert 'content: "\\2193"' in ui._OBSERVE_STYLES
    assert not any(ord(character) < 32 and character not in "\n\r\t" for character in ui._OBSERVE_STYLES)


# ---------------------------------------------------------------------------
# Cost allocation (spec 013 T013/T021)
# ---------------------------------------------------------------------------


def _cost_data(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "period": {
            "id": 'august<"period">',
            "starts_at": "2026-08-01T00:00:00Z",
            "ends_at": "2026-09-01T00:00:00Z",
        },
        "breakdown": "agents",
        "component_filter": None,
        "components": [
            {
                "component_id": 'ptu<"pool">',
                "component_type": "provisioned_throughput",
                "billing_boundary": {
                    "kind": "resource",
                    "value": "<resource>",
                    "label": 'Primary "PTU"',
                },
                "billed_source": 'operator<"declared">',
                "allocation_model": "commitment",
                "preferred_key": "weighted_tokens",
                "applied_key": "weighted_tokens",
                "declared_total": "1000.00",
                "attributed_amount": "900.00",
                "unattributed_amount": "50.00",
                "unallocated_amount": "50.00",
                "omitted_allocated_amount": "0.00",
                "currency": "USD",
                "currency_minor_units": 2,
                "rows_shown": 2,
                "rows_total": 2,
                "confidence": "high",
                "coverage_state": "available",
                "coverage_reason": "Complete period telemetry",
                "next_action": None,
            },
            {
                "component_id": "credits",
                "component_type": "credit_payg",
                "billing_boundary": {"kind": "account", "value": "acct"},
                "billed_source": "operator",
                "allocation_model": "metered",
                "preferred_key": "credits",
                "applied_key": "credit_events",
                "declared_total": "25.000",
                "attributed_amount": "25.000",
                "unattributed_amount": "0.000",
                "unallocated_amount": "0.000",
                "omitted_allocated_amount": "0.000",
                "currency": "EUR",
                "currency_minor_units": 3,
                "rows_shown": 1,
                "rows_total": 1,
                "confidence": "medium",
                "coverage_state": "partial",
                "coverage_reason": "Preferred usage was not reported",
                "next_action": "Enable direct credit telemetry",
            },
        ],
        "rows": [
            {
                "component_id": 'ptu<"pool">',
                "component_type": "provisioned_throughput",
                "billing_boundary": {"kind": "resource", "value": "<resource>"},
                "billed_source": 'operator<"declared">',
                "allocation_model": "commitment",
                "preferred_key": "weighted_tokens",
                "applied_key": "weighted_tokens",
                "fallback_used": False,
                "breakdown": "agents",
                "consumer_kind": "agent",
                "consumer_key": 'agent<"a">',
                "agent_key": 'agent<"a">',
                "amount": "600.00",
                "currency": "USD",
                "currency_minor_units": 2,
                "usage_numerator": "60",
                "usage_denominator": "100",
                "usage_unit": "weighted_tokens",
                "rounding_adjustment_minor_units": 0,
                "confidence": "high",
                "coverage_state": "available",
                "coverage_reason": None,
                "calculated_at": "2026-08-24T20:00:00Z",
                "latest_observed_at": "2026-08-24T19:59:00Z",
            },
            {
                "component_id": "credits",
                "component_type": "credit_payg",
                "billing_boundary": {"kind": "account", "value": "acct"},
                "billed_source": "operator",
                "allocation_model": "metered",
                "preferred_key": "credits",
                "applied_key": "credit_events",
                "fallback_used": True,
                "breakdown": "agents",
                "consumer_kind": "agent",
                "consumer_key": "agent-b",
                "agent_key": "agent-b",
                "amount": "25.000",
                "currency": "EUR",
                "currency_minor_units": 3,
                "usage_numerator": "1",
                "usage_denominator": "4",
                "usage_unit": "credit_events",
                "rounding_adjustment_minor_units": 0,
                "confidence": "medium",
                "coverage_state": "partial",
                "coverage_reason": "Used explicit fallback",
                "calculated_at": "2026-08-24T20:00:00Z",
                "latest_observed_at": "2026-08-24T19:58:00Z",
            },
        ],
        "currency_subtotals": [
            {
                "currency": "USD",
                "currency_minor_units": 2,
                "declared_total": "1000.00",
                "attributed_amount": "900.00",
                "unattributed_amount": "50.00",
                "unallocated_amount": "50.00",
            },
            {
                "currency": "EUR",
                "currency_minor_units": 3,
                "declared_total": "25.000",
                "attributed_amount": "25.000",
                "unattributed_amount": "0.000",
                "unallocated_amount": "0.000",
            },
        ],
        "calculated_at": "2026-08-24T20:00:00Z",
        "latest_observed_at": "2026-08-24T19:59:00Z",
        "disclaimer": (
            "Operational cost allocation from declared billed totals and observed usage; "
            "not an invoice or billing-accurate charge."
        ),
    }
    payload.update(overrides)
    return payload


def test_cost_controls_render_period_component_breakdown_and_agent_selectors() -> None:
    html = ui.render_cost_controls(
        _cost_data(),
        period_options=[
            {
                "id": "august",
                "label": 'August <"2026">',
                "component_ids": ("ptu", "search"),
            }
        ],
        component_options=[{"id": "ptu", "label": 'PTU <"pool">'}],
        agent_options=[{"key": "agent-a", "label": 'Agent <"A">'}],
    )
    for key in ui.COST_FILTER_QUERY_KEYS:
        assert f'data-cost-filter="{key}"' in html
    for label in ("Period", "Component", "Breakdown", "Agent"):
        assert f">{label}" in html
    assert "Agents" in html
    assert 'August &lt;&quot;2026&quot;&gt;' in html
    assert 'data-cost-component-ids="ptu,search"' in html
    assert 'PTU &lt;&quot;pool&quot;&gt;' in html
    assert 'Agent &lt;&quot;A&quot;&gt;' in html
    assert 'id="observe-apply-cost-filters"' in html


def test_render_cost_view_shows_exact_agent_allocations_and_usage_shares() -> None:
    html = ui.render_cost_view(_cost_data())
    for heading in ("Agent", "Component", "Amount", "Usage share", "Method", "Source"):
        assert f">{heading}<" in html
    assert 'agent&lt;&quot;a&quot;&gt;' in html
    assert 'ptu&lt;&quot;pool&quot;&gt;' in html
    assert "600.00 USD" in html
    assert "60 / 100 weighted tokens" in html
    assert "Observed usage: 60 / 100 weighted tokens" in html
    assert "25.000 EUR" in html
    assert "1 / 4 credit events" in html
    assert "Commitment" in html
    assert "Metered" in html
    assert "observe-cost-method-commitment" in html
    assert "observe-cost-method-metered" in html
    assert "observe-cost-confidence-high" in html
    assert "observe-cost-confidence-medium" in html
    assert 'operator&lt;&quot;declared&quot;&gt;' in html
    assert "<resource>" not in html


def test_render_cost_view_keeps_currency_precision_and_exact_component_summaries() -> None:
    html = ui.render_cost_view(_cost_data())
    assert html.count('class="observe-cost-subtotal-row"') == 2
    assert "1000.00 USD" in html
    assert "25.000 EUR" in html
    assert "Currency precision: 2 minor units" in html
    assert "Currency precision: 3 minor units" in html
    for heading in (
        "Declared",
        "Attributed",
        "Unattributed",
        "Unallocated",
        "Omitted allocated",
        "Rows",
    ):
        assert f">{heading}<" in html
    assert "900.00 USD" in html
    assert "50.00 USD" in html
    assert "2 / 2" in html
    assert "Preferred usage was not reported" in html
    assert "Enable direct credit telemetry" in html


def test_render_cost_view_includes_fixed_operational_allocation_disclaimer() -> None:
    html = ui.render_cost_view(_cost_data())
    assert ui.COST_DISCLAIMER in html
    assert "not an invoice or billing-accurate charge" in html


def test_cost_view_is_absent_by_default_and_rendered_when_enabled() -> None:
    default_html = ui.render_observe_page()
    assert 'data-observe-nav-link="cost"' not in default_html
    assert '<section id="cost"' not in default_html

    enabled_html = ui.render_observe_page(cost_enabled=True, cost=_cost_data())
    assert 'data-observe-nav-link="cost"' in enabled_html
    assert '<section id="cost"' in enabled_html
    assert 'id="cost-content" data-observe-view-content="cost"' in enabled_html
    assert "600.00 USD" in enabled_html


def test_script_cost_renderer_matches_server_fields_and_safe_dom_construction() -> None:
    script = ui._OBSERVE_SCRIPT
    for name in (
        "function renderCost(data, diagnostics, coverage, partialFailures, bounds)",
        "function renderCostControlsFromData(data)",
        "function renderCostAmountNode(amount, currency)",
        "function renderCostUsageShareNode(row)",
        "function buildCostPayload(manual)",
    ):
        assert name in script
    for field in (
        "currency_subtotals",
        "declared_total",
        "attributed_amount",
        "unattributed_amount",
        "unallocated_amount",
        "omitted_allocated_amount",
        "usage_numerator",
        "usage_denominator",
        "usage_unit",
    ):
        assert field in script
    assert ui.COST_DISCLAIMER in script
    assert "innerHTML" not in script


def test_script_cost_payload_includes_required_absolute_filter_envelope() -> None:
    script = ui._OBSERVE_SCRIPT
    block = script.split("function buildCostPayload(manual) {")[1].split("\n  }\n")[0]
    for key in ui.COST_FILTER_QUERY_KEYS:
        assert f"filters.{key} =" in block
    assert "var filters = observeFiltersForRequest(appliedFilters);" in block
    assert "window: windowSelectionForRequest(appliedFilters)" in block
    assert 'view: "cost"' in block
    assert "refresh: manual === true" in block
    fetch_block = script.split("function fetchObserveData(manual) {")[1].split(
        "\n  }\n\n  function scheduleAutoRefresh"
    )[0]
    assert 'currentView === "cost" ? buildCostPayload(manual)' in fetch_block


def test_script_cost_selectors_round_trip_url_without_changing_shared_keys() -> None:
    script = ui._OBSERVE_SCRIPT
    assert (
        'var FILTER_KEYS = ["foundry_resource_id", "project_resource_id", "agent_id", '
        '"model", "tool_name", "run_key", "window_preset", "start", "end"];'
    ) in script
    assert (
        'var COST_FILTER_KEYS = ["cost_period_id", "cost_component_id", '
        '"cost_breakdown", "cost_agent_key"];'
    ) in script
    assert 'document.getElementById("observe-cost-filter-form")' in script
    assert "readCostDraftFromForm(costForm)" in script
    assert "populateCostFormFromApplied(costForm)" in script
    assert "function replaceCostSelectOptions(select, values, allLabel)" in script
    assert "function resetCostSelectorsForPeriod(form)" in script
    assert 'costPeriodField.addEventListener("change"' in script
    reset_block = script.split("function resetCostSelectorsForPeriod(form) {")[1].split(
        "\n  }\n"
    )[0]
    assert 'data-cost-filter="cost_period_id"' in reset_block
    assert 'data-cost-filter="cost_component_id"' in reset_block
    assert 'appliedFilters.cost_component_id = ""' in reset_block
    assert "resetCostAgentSelector(form)" in reset_block
    agent_reset_block = script.split("function resetCostAgentSelector(form) {")[1].split(
        "\n  }\n"
    )[0]
    assert 'data-cost-filter="cost_agent_key"' in agent_reset_block
    assert 'appliedFilters.cost_agent_key = ""' in agent_reset_block
    shared_submit = script.split('form.addEventListener("submit"')[1].split(
        "\n      });"
    )[0]
    assert "preservedCostFilters" in shared_submit
    for key in ui.COST_FILTER_QUERY_KEYS:
        assert f"{key}: appliedFilters.{key}" in shared_submit


def test_clean_url_cost_uses_first_typed_server_period_for_initial_request() -> None:
    first_period = CostPeriodRef(
        id="period-first",
        starts_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        ends_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    second_period = CostPeriodRef(
        id="period-second",
        starts_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        ends_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    html = ui.render_observe_page(
        cost_enabled=True,
        cost_periods=[first_period, second_period],
    )
    assert '<option value="period-first" selected>period-first</option>' in html
    assert '<option value="period-second">period-second</option>' in html
    assert "2026-08-01T00:00:00" not in html
    assert "components&quot;" not in html

    script = ui._OBSERVE_SCRIPT
    initializer = script.split(
        "function initializeCostPeriodFromServer(form) {"
    )[1].split("\n  }\n")[0]
    assert 'appliedFilters.cost_period_id' in initializer
    assert '[data-cost-filter="cost_period_id"]' in initializer
    assert "field.options" in initializer
    assert "option.value" in initializer
    assert "FILTER_KEYS" not in initializer
    assert "JSON.stringify" not in initializer

    init_block = script.split("function init() {")[1].split(
        "\n    var refreshButton"
    )[0]
    assert init_block.index("initializeCostPeriodFromServer(costForm)") < init_block.index(
        "populateCostFormFromApplied(costForm)"
    )
    payload = script.split("function buildCostPayload(manual) {")[1].split(
        "\n  }\n"
    )[0]
    assert (
        "filters.cost_period_id = appliedFilters.cost_period_id || null" in payload
    )
    assert "observeFiltersForRequest(appliedFilters)" in payload


def test_cost_tool_and_run_grains_are_alternative_non_additive_reconciliations() -> None:
    tool_rows = [
        {
            **dict(_cost_data()["rows"][0]),
            "breakdown": "tools",
            "consumer_kind": "tool",
            "consumer_key": "agent-a::search_documents",
            "agent_key": "agent-a",
            "tool_name": "search_documents",
        },
        {
            **dict(_cost_data()["rows"][1]),
            "breakdown": "tools",
            "consumer_kind": "unattributed",
            "consumer_key": "__unattributed_tool__",
            "agent_key": None,
        },
    ]
    tool_html = ui.render_cost_view(_cost_data(breakdown="tools", rows=tool_rows))
    assert ">Tool<" in tool_html
    assert "search_documents" in tool_html
    assert "agent-a::search_documents" not in tool_html
    assert "Unattributed tool" in tool_html
    assert ui.COST_BREAKDOWN_WARNING in tool_html
    tool_controls = ui.render_cost_controls(_cost_data(breakdown="tools", rows=tool_rows))
    assert '<option value="agent-a">agent-a</option>' in tool_controls

    run_rows = [
        {
            **dict(_cost_data()["rows"][0]),
            "breakdown": "runs",
            "consumer_kind": "run",
            "consumer_key": "agent-a::run-123",
            "agent_key": "agent-a",
            "run_key": "run-123",
        },
        {
            **dict(_cost_data()["rows"][1]),
            "breakdown": "runs",
            "consumer_kind": "unattributed",
            "consumer_key": "__unattributed_run__",
            "agent_key": None,
        },
    ]
    run_html = ui.render_cost_view(_cost_data(breakdown="runs", rows=run_rows))
    assert ">Run<" in run_html
    assert "run-123" in run_html
    assert "agent-a::run-123" not in run_html
    assert "Unattributed run" in run_html
    assert ui.COST_BREAKDOWN_WARNING in run_html
    script = ui._OBSERVE_SCRIPT
    labeler = script.split("function costConsumerLabel(row, breakdown) {")[1].split(
        "\n  }\n"
    )[0]
    assert "tools: row.tool_name" in labeler
    assert "runs: row.run_key" in labeler


def test_cost_agent_rows_link_to_tool_and_run_drilldowns_with_cost_selectors_only() -> None:
    html = ui.render_cost_view(
        _cost_data(component_filter='ptu<"pool">'),
    )
    assert "View tools" in html
    assert "View runs" in html
    assert "cost_breakdown=tools" in html
    assert "cost_breakdown=runs" in html
    assert "cost_agent_key=agent%3C%22a%22%3E" in html
    assert "cost_period_id=august%3C%22period%22%3E" in html
    assert "cost_component_id=ptu%3C%22pool%22%3E" in html
    for shared in ui.OBSERVE_FILTER_QUERY_KEYS:
        assert f"{shared}=" not in html
    assert "operator%3C" not in html
    assert "%7B" not in html


def test_cost_rows_render_complete_auditable_provenance() -> None:
    row = {
        **dict(_cost_data()["rows"][0]),
        "period_id": "period-2026-08",
        "period_starts_at": "2026-08-01T00:00:00Z",
        "period_ends_at": "2026-09-01T00:00:00Z",
        "source_resource_id": '/subscriptions/sub/<source-"a">',
        "project_resource_id": '/projects/project-<"a">',
        "rounding_adjustment_minor_units": 1,
    }
    html = ui.render_cost_view(_cost_data(rows=[row]))
    for label in (
        "Period",
        "Observation window",
        "Billing boundary",
        "Source resource",
        "Project resource",
        "Preferred key",
        "Applied key",
        "Fallback",
        "Rounding adjustment",
        "Confidence",
        "Coverage",
        "Calculated at",
        "Latest observed",
    ):
        assert label in html
    assert "period-2026-08" in html
    assert "2026-08-01T00:00:00Z" in html
    assert "2026-09-01T00:00:00Z" in html
    assert "weighted tokens" in html
    assert "1 minor unit" in html
    assert "High" in html
    assert "Available" in html
    assert '&lt;source-&quot;a&quot;&gt;' in html
    assert '&lt;&quot;a&quot;&gt;' in html
    assert '<source-"a">' not in html


def test_cost_row_labels_missing_allocation_usage_without_fabricating_zero() -> None:
    row = {
        **dict(_cost_data()["rows"][0]),
        "usage_numerator": None,
        "usage_denominator": None,
        "usage_unit": None,
    }
    html = ui.render_cost_view(_cost_data(rows=[row]))
    assert "Observed usage: Not reported" in html
    assert "Observed usage: 0" not in html


def test_cost_view_distinguishes_missing_zero_unallocated_and_truncated_amounts() -> None:
    components = [
        {
            "component_id": "missing-total",
            "component_type": "payg",
            "billing_boundary": {"kind": "resource", "value": "resource-a"},
            "billed_source": "operator",
            "allocation_model": "metered",
            "preferred_key": "tokens",
            "applied_key": None,
            "declared_total": None,
            "attributed_amount": None,
            "unattributed_amount": None,
            "unallocated_amount": None,
            "omitted_allocated_amount": None,
            "currency": "USD",
            "currency_minor_units": 2,
            "rows_shown": 0,
            "rows_total": None,
            "confidence": "unavailable",
            "coverage_state": "not_configured",
            "coverage_reason": "No billed total was configured.",
            "next_action": "Configure an exact billed total.",
        },
        {
            **dict(_cost_data()["components"][0]),
            "component_id": "observed-zero",
            "declared_total": "0.00",
            "attributed_amount": "0.00",
            "unattributed_amount": "0.00",
            "unallocated_amount": "0.00",
            "omitted_allocated_amount": "0.00",
            "rows_shown": 100,
            "rows_total": 120,
            "coverage_state": "partial",
            "coverage_reason": "Twenty allocation rows were omitted.",
            "next_action": "Narrow the component or agent selector.",
        },
    ]
    coverage = [
        {
            "dimension": "cost_attribution",
            "state": "not_configured",
            "reason": "No billed total was configured.",
            "next_action": "Configure an exact billed total.",
            "source_id": "missing-total",
        }
    ]
    html = ui.render_cost_view(
        _cost_data(
            components=components,
            rows=[],
            currency_subtotals=[
                {
                    "currency": "USD",
                    "currency_minor_units": 2,
                    "declared_total": None,
                    "attributed_amount": None,
                    "unattributed_amount": None,
                    "unallocated_amount": None,
                }
            ],
        ),
        coverage=coverage,
        partial_failures=[
            {
                "source_id": 'workspace<"a">',
                "status": "partial",
                "reason": "The query returned partial data for this source.",
                "next_action": "Retry later.",
            }
        ],
        bounds={"rows_shown": 100, "rows_total_in_scope": 120, "truncated": True},
    )
    assert html.count("Missing configured billed total") >= 3
    assert "Not reported" in html
    assert "Observed zero" in html
    assert "0.00 USD" in html
    assert "Unallocated" in html
    assert "Omitted allocated" in html
    assert "Showing 100 of 120 rows in scope; results are truncated." in html
    assert "100 / 120 (20 omitted)" in html
    assert "Twenty allocation rows were omitted." in html
    assert "Narrow the component or agent selector." in html
    assert "Unavailable" in html
    assert "Partial source failures" in html
    assert 'workspace&lt;&quot;a&quot;&gt;' in html


def test_script_cost_renderer_has_tool_run_provenance_and_incomplete_state_parity() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "function renderCost(data, diagnostics, coverage, partialFailures, bounds)" in script
    for field in (
        "period_id",
        "period_starts_at",
        "period_ends_at",
        "source_resource_id",
        "project_resource_id",
        "billing_boundary",
        "preferred_key",
        "applied_key",
        "fallback_used",
        "rounding_adjustment_minor_units",
        "calculated_at",
        "latest_observed_at",
        "rows_total",
        "coverage_reason",
        "next_action",
        "partialFailures",
        "bounds",
    ):
        assert field in script
    assert ui.COST_BREAKDOWN_WARNING in script
    assert "Unattributed tool" in script
    assert "Unattributed run" in script
    assert "Missing configured billed total" in script
    assert "Observed zero" in script
    assert "innerHTML" not in script


# ---------------------------------------------------------------------------
# Tools and runs tables (T025/T026/T037/T042)
# ---------------------------------------------------------------------------


def _tool(**overrides: object) -> dict[str, object]:
    payload = dict(
        tool_name="search_documents",
        agent_key="agent-a",
        agent_id="agent-a",
        agent_name="Agent A",
        source_id="source-a",
        source_kind="foundry_prompt",
        last_seen=_dt(3),
        invocations=10,
        failures=1,
        p95_latency_ms=35.0,
    )
    payload.update(overrides)
    return payload


def _run(**overrides: object) -> dict[str, object]:
    payload = dict(
        run_key="conversation-123",
        run_key_kind="conversation",
        agent_key="agent-a",
        agent_id="agent-a",
        agent_name="Agent A",
        source_id="source-a",
        source_kind="foundry_prompt",
        started_at=_dt(1),
        last_activity_at=_dt(2),
        duration_ms=60_000.0,
        status="succeeded",
        turns=2,
        failed_turns=0,
        tool_invocations=1,
        tool_failures=0,
        input_tokens=100,
        output_tokens=200,
    )
    payload.update(overrides)
    return payload


def _estimated_cost(**overrides: object) -> CostEstimate:
    payload: dict[str, object] = {
        "amount": Decimal("0.00042"),
        "currency": "USD",
        "completeness": "complete",
        "price_reference_version": "test-v1",
        "price_reference_effective_date": date(2026, 8, 1),
        "is_stale": False,
        "reference_age_days": 30,
    }
    payload.update(overrides)
    return CostEstimate.model_validate(payload)


def test_render_tools_table_shows_source_latency_and_known_bounds() -> None:
    html = ui.render_tools_table(
        [_tool()],
        bounds={"rows_shown": 1, "rows_total_in_scope": 3, "truncated": True},
    )
    for heading in ("Tool", "Agent", "Source", "Runtime", "Last seen", "Invocations", "Failures", "p95 latency"):
        assert f'data-label="{heading}"' in html
    assert "search_documents" in html
    assert "source-a" in html
    assert "Foundry Prompt" in html
    assert "Showing 1 of 3 rows in scope." in html


def test_render_tools_table_marks_absent_latency_with_dash_and_escapes_values() -> None:
    html = ui.render_tools_table(
        [_tool(tool_name='<tool "name">', source_id="<source>", p95_latency_ms=None)]
    )
    assert "Not measured" not in html
    assert "\u2014" in html
    assert "metric-missing" in html
    assert "&lt;tool &quot;name&quot;&gt;" in html
    assert "&lt;source&gt;" in html


def test_render_tools_table_empty_is_explained_without_unknown_total() -> None:
    html = ui.render_tools_table([], bounds={"rows_shown": 0, "rows_total_in_scope": None})
    assert "Showing 0 rows." in html
    assert "unknown" not in html.lower()
    assert "No tool activity was found" in html
    assert "Tool attribution may not be reported" in html


def test_render_runs_table_shows_correlation_scope_source_and_tokens() -> None:
    html = ui.render_runs_table(
        [_run()],
        bounds={"rows_shown": 1, "rows_total_in_scope": 4, "truncated": True},
    )
    for heading in (
        "Run key",
        "Correlation",
        "Source",
        "Started",
        "Duration",
        "Turns",
        "Tool invocations",
        "Input tokens",
        "Output tokens",
        "Total tokens",
        "Estimated cost",
    ):
        assert f'data-label="{heading}"' in html
    assert "conversation-123" in html
    assert "conversation" in html
    assert "source-a" in html
    assert "activity within the selected range" in html
    assert "Showing 1 of 4 rows in scope." in html
    assert "60.000 s" in html
    assert "<tfoot>" in html


def test_estimated_cost_requires_status_disclaimer_currency_and_provenance() -> None:
    html = ui.render_runs_table([_run(estimated_cost=_estimated_cost())])

    assert "USD 0.00042" in html
    assert "Completeness: complete" in html
    assert "Price reference test-v1, effective 2026-08-01" in html
    assert ui.ESTIMATED_COST_DISCLAIMER in html
    assert 'data-completeness="complete"' in html
    assert "This result uses price reference test-v1, effective 2026-08-01." in html


def test_unpriced_is_not_zero_and_stale_estimate_remains_visible() -> None:
    unpriced = CostEstimate(
        completeness="not_priced",
        reason="No published model price.",
    )
    stale = _estimated_cost(
        amount=Decimal("0"),
        is_stale=True,
        reference_age_days=91,
    )
    html = ui.render_runs_table(
        [
            _run(run_key="unpriced", estimated_cost=unpriced),
            _run(run_key="stale", estimated_cost=stale),
        ],
        bounds={"rows_shown": 2, "rows_total_in_scope": 2, "truncated": False},
    )

    assert "Not priced" in html
    assert "No published model price." in html
    assert "USD 0" in html
    assert "Stale price reference (91 days old)" in html
    assert html.count(ui.ESTIMATED_COST_DISCLAIMER) >= 2


def test_agent_and_model_rollups_name_unpriced_runs_and_never_sum_billed_cost() -> None:
    rollup = _estimated_cost(
        completeness="partial",
        excluded_components=["unpriced model"],
        unpriced_run_count=2,
        covered_run_count=10,
        scope_run_count=10,
    )
    agent_html = ui.render_agents_table([_agent(estimated_cost=rollup)])
    model = ModelUsage(
        model="gpt-5-nano",
        requests=10,
        failures=0,
        estimated_cost=rollup,
        scope_run_count=10,
    )
    model_html = ui.render_models_usage_table([model])

    for html in (agent_html, model_html):
        assert "USD 0.00042" in html
        assert "10 of 10 runs covered; 2 runs not priced" in html
        assert "Completeness: partial" in html
        assert ui.ESTIMATED_COST_DISCLAIMER in html
    assert ui.ESTIMATED_COST_DISCLAIMER != ui.COST_DISCLAIMER
    script = ui._OBSERVE_SCRIPT
    assert "estimatedCostNode" in script
    assert "renderCostAmountNode" in script
    assert "estimated_cost +" not in script
    assert "allocated_amount +" not in script
    assert script.index("function estimatedCostNode(") < script.index(
        "function renderOverview("
    )
    assert script.index("function estimatedCostNode(") < script.index(
        "function renderAgents("
    )


def test_estimate_presentation_has_no_credential_commerce_or_outbound_dependency() -> None:
    import agentops.agent.knowledge.pricing as pricing_loader
    import agentops.core.observe_pricing as pricing_core

    source = inspect.getsource(pricing_core) + inspect.getsource(pricing_loader)
    for forbidden in (
        "DefaultAzureCredential",
        "azure.identity",
        "requests.",
        "httpx.",
        "billing",
        "commerce",
    ):
        assert forbidden not in source
    assert "fetch(" not in source


def test_render_runs_table_marks_absent_tokens_with_dash_and_escapes_values() -> None:
    html = ui.render_runs_table(
        [_run(run_key='<run "key">', source_id="<source>", input_tokens=None, output_tokens=None)]
    )
    assert "Not available" not in html
    assert "\u2014" in html
    assert "metric-missing" in html
    assert "&lt;run &quot;key&quot;&gt;" in html
    assert "&lt;source&gt;" in html


def test_render_runs_table_empty_is_explained_without_unknown_total() -> None:
    html = ui.render_runs_table([], bounds={"rows_shown": 0, "rows_total_in_scope": None})
    assert "Showing 0 rows." in html
    assert "unknown" not in html.lower()
    assert "No runs could be correlated" in html
    assert "Run correlation may not be reported" in html


@pytest.mark.parametrize(
    ("source_kind", "label"),
    [
        ("foundry_hosted", "Foundry Hosted"),
        ("foundry_prompt", "Foundry Prompt"),
        ("external_registered", "External Registered"),
        ("external_unregistered", "External Unregistered"),
        ("copilot_studio", "Copilot Studio"),
        ("unknown", "Unclassified"),
    ],
)
def test_source_kind_label_covers_all_refined_runtime_kinds(
    source_kind: str, label: str
) -> None:
    html = ui._render_source_kind_badge(source_kind)
    assert label in html
    assert 'class="observe-source-kind"' in html
    assert "observe-badge" not in html


def test_unclassified_source_kind_explains_missing_attribution() -> None:
    html = ui._render_source_kind_badge("unknown")
    assert "Unclassified" in html
    assert "could not be classified" in html
    assert ">Unknown<" not in html


# ---------------------------------------------------------------------------
# Runs table column declaration (T006/T007, FR-030)
# ---------------------------------------------------------------------------


def _js_array(name: str) -> tuple[str, ...]:
    match = re.search(rf"var {name} = \[(.*?)\];", ui._OBSERVE_SCRIPT, re.DOTALL)
    assert match, f"the embedded script must declare {name}"
    return tuple(re.findall(r'"([^"]+)"', match.group(1)))


def _js_string_map(name: str) -> dict[str, str]:
    match = re.search(rf"var {name} = \{{(.*?)\}};", ui._OBSERVE_SCRIPT, re.DOTALL)
    assert match, f"the embedded script must declare {name}"
    return dict(re.findall(r'([a-z_]+):\s*"([^"]+)"', match.group(1)))


def _js_label_tone_map(name: str) -> dict[str, dict[str, str]]:
    match = re.search(rf"var {name} = \{{(.*?)\n  \}};", ui._OBSERVE_SCRIPT, re.DOTALL)
    assert match, f"the embedded script must declare {name}"
    entries = re.findall(
        r'^\s*([a-z_]+): \{ label: "([^"]+)", tone: "([^"]+)" \},$',
        match.group(1),
        re.MULTILINE,
    )
    assert len(entries) == len({key for key, _label, _tone in entries}), (
        f"{name} must not repeat a key"
    )
    return {
        key: {"label": label, "tone": tone}
        for key, label, tone in entries
    }


def _js_runs_table_columns() -> list[tuple[str, str, str | None, str, int]]:
    block = re.search(
        r"var RUNS_TABLE_COLUMNS = \[(.*?)\n  \];", ui._OBSERVE_SCRIPT, re.DOTALL
    )
    assert block, "the embedded script must declare RUNS_TABLE_COLUMNS"

    columns: list[tuple[str, str, str | None, str, int]] = []
    for line in block.group(1).splitlines():
        if not line.strip():
            continue
        parsed = re.search(
            r'\{ id: "([^"]+)", label: "([^"]+)", sortKey: (?:"([^"]+)"|null)'
            r'(?:, help: (.*?))?(?:, priority: (\d+))? \}',
            line.strip().rstrip(","),
        )
        assert parsed, f"could not parse JS Runs column declaration: {line}"
        identifier, label, sort_key, help_expr, priority = parsed.groups()
        if not help_expr:
            help_text = ""
        elif help_expr == "RUNS_TOKEN_HELP":
            help_text = ui._RUNS_TOKEN_HELP
        elif help_expr == "ESTIMATED_COST_HELP":
            help_text = ui._ESTIMATED_COST_HELP
        elif " + RUNS_TOKEN_HELP" in help_expr:
            prefix = help_expr.split(" + RUNS_TOKEN_HELP", 1)[0].strip('"')
            help_text = prefix + ui._RUNS_TOKEN_HELP
        else:
            help_text = help_expr.strip('"')
        columns.append((identifier, label, sort_key, help_text, int(priority or 0)))
    return columns


def test_renaming_a_column_label_leaves_sorting_filtering_and_help_intact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reword one label and prove nothing that keys off the column moves.

    This is the whole point of the identifier/label split: the displayed prose
    is editorial, so US3 can reword any heading without a reviewer having to
    re-verify that sorting still works. The assertion is deliberately made
    against the rendered markup rather than against the declaration, because
    the markup is what the browser actually keys off.
    """
    original = ui.RUNS_TABLE_COLUMNS
    target = next(c for c in original if c.identifier == "duration_ms")
    assert target.help_text, "the column under test must carry help text"

    renamed = tuple(
        column.model_copy(update={"label": "Wall clock (renamed)"})
        if column.identifier == "duration_ms"
        else column
        for column in original
    )
    monkeypatch.setattr(ui, "RUNS_TABLE_COLUMNS", renamed)

    html = ui.render_runs_table([_run()])

    # Identity is unchanged, so the sort key and the script lookup are too.
    assert 'data-column-id="duration_ms"' in html
    # The new prose is displayed, and the old prose is gone from the header.
    assert 'data-label="Wall clock (renamed)"' in html
    assert 'data-label="Duration"' not in html
    # Help travels with the column, not with its old name.
    assert target.help_text in html
    # Every other column is untouched.
    rendered_ids = re.findall(r'data-column-id="([^"]+)"', html)
    assert rendered_ids == [column.identifier for column in original]


def test_python_and_embedded_js_column_declarations_agree_on_identifiers() -> None:
    """The two copies of the Runs table declaration must not drift.

    ``ui.py`` declares the columns twice on purpose -- once for the
    server-rendered first paint and once for the script that re-renders after a
    fetch. Nothing in the language stops those from diverging, so this test is
    the only thing that does. It compares identifiers, labels, sort keys, and
    help text so either renderer exposes the same table contract.
    """
    js_columns = _js_runs_table_columns()
    py_columns = [
        (c.identifier, c.label, c.sort_key, c.help_text or "", c.priority)
        for c in ui.RUNS_TABLE_COLUMNS
    ]
    assert js_columns == py_columns

    # Every declared sort key is the identifier itself, which is what lets the
    # script derive its lookup from the declaration instead of restating it.
    for column in ui.RUNS_TABLE_COLUMNS:
        assert column.sort_key is None or column.sort_key == column.identifier


def test_every_python_and_embedded_javascript_duplicate_agrees() -> None:
    """Pin every server/client constant that must remain duplicated in ``ui.py``."""
    assert tuple(_js_string_map("VIEW_WIRE_NAMES")) == ui.OBSERVE_VIEWS
    assert _js_string_map("VIEW_WIRE_NAMES") == ui.OBSERVE_VIEW_WIRE_NAMES
    assert _js_array("FILTER_KEYS") == ui.OBSERVE_FILTER_QUERY_KEYS
    assert _js_array("COST_FILTER_KEYS") == ui.COST_FILTER_QUERY_KEYS
    assert _js_array("ATTRIBUTION_FILTER_KEYS") == ui.ATTRIBUTION_FILTER_QUERY_KEYS

    refresh = re.search(r"var AUTO_REFRESH_MS = (\d+);", ui._OBSERVE_SCRIPT)
    default_days = re.search(
        r"var DEFAULT_RANGE_MS = (\d+) \* 24 \* 60 \* 60 \* 1000;",
        ui._OBSERVE_SCRIPT,
    )
    assert refresh and int(refresh.group(1)) == ui.AUTO_REFRESH_MS
    assert default_days
    assert int(default_days.group(1)) * 24 == ui.DEFAULT_RANGE_HOURS

    assert _js_label_tone_map("COVERAGE_STATE_LABELS") == ui.COVERAGE_STATE_LABELS
    assert _js_string_map("SOURCE_KIND_LABELS") == ui.SOURCE_KIND_LABELS
    assert _js_runs_table_columns() == [
        (column.identifier, column.label, column.sort_key, column.help_text or "", column.priority)
        for column in ui.RUNS_TABLE_COLUMNS
    ]


def test_runs_identifiers_are_abbreviated_and_full_values_remain_copyable() -> None:
    run_key = "1234567890abcdef"
    source_id = (
        "/subscriptions/sub/resourceGroups/rg/providers/"
        "Microsoft.MachineLearningServices/workspaces/production-workspace"
    )

    html = ui.render_runs_table(
        [_run(run_key=run_key, source_id=source_id)],
        bounds={"rows_shown": 1, "rows_total_in_scope": 2, "truncated": True},
    )

    assert "12345678\u2026" in html
    assert ">production-workspace<" in html
    assert f'data-copy-value="{run_key}"' in html
    assert f'data-copy-value="{source_id}"' in html
    assert f'value="{run_key}"' in html
    assert f'value="{source_id}"' in html
    assert 'aria-label="Copy full run key"' in html
    assert 'aria-label="Copy full source resource ID"' in html
    assert 'role="status" aria-live="polite"' in html

    short_html = ui.render_runs_table(
        [_run(run_key="run-1234")],
        bounds={"rows_shown": 1, "rows_total_in_scope": 2, "truncated": True},
    )
    assert "run-1234\u2026" not in short_html
    assert ">run-1234<" in short_html

    script = ui._OBSERVE_SCRIPT
    assert (
        f"var RUN_IDENTIFIER_VISIBLE_CHARS = {ui.RUN_IDENTIFIER_VISIBLE_CHARS};"
        in script
    )
    assert "abbreviateRunIdentifier(run.run_key)" in script
    assert "sourceWorkspaceName(run.source_id)" in script


def test_runs_copy_progressive_enhancement_has_clipboard_and_manual_fallback() -> None:
    script = ui._OBSERVE_SCRIPT
    block = script.split("function bindCopyControl(control) {")[1].split(
        "\n  }\n", 1
    )[0]
    assert "navigator.clipboard.writeText(value)" in block
    assert "input.select()" in block
    assert 'details.open = true' in block
    assert 'feedback.textContent = "Copied.";' in block
    assert '"Copy failed. Select the full value below."' in block
    assert "enhanceCopyControls(document);" in script


def test_runs_labels_drop_in_range_without_changing_stable_sort_keys() -> None:
    html = ui.render_runs_table([_run()])

    assert "in range" not in html.lower()
    for identifier, label in (
        ("started_at", "Started"),
        ("duration_ms", "Duration"),
        ("turns", "Turns"),
    ):
        assert f'data-column-id="{identifier}"' in html
        assert f'data-label="{label}"' in html
        column = next(c for c in ui.RUNS_TABLE_COLUMNS if c.identifier == identifier)
        assert column.sort_key == identifier


def test_runs_suppress_singleton_dimensions_only_for_proven_complete_scope() -> None:
    bounds = {"rows_shown": 2, "rows_total_in_scope": 2, "truncated": False}
    html = ui.render_runs_table(
        [_run(run_key="run-a"), _run(run_key="run-b")],
        bounds=bounds,
        diagnostics={"partial_sources": 0, "failed_sources": 0},
    )

    for identifier in ("run_key_kind", "agent_name", "source_id", "source_kind", "status"):
        assert f'data-column-id="{identifier}"' not in html
    assert 'aria-label="Values shared by every run in scope"' in html
    assert "<dt>Correlation</dt><dd>conversation</dd>" in html
    assert "<dt>Agent</dt><dd>Agent A</dd>" in html

    expected_cells = len(ui.RUNS_TABLE_COLUMNS) - 5
    thead = re.search(r"<thead>.*?</thead>", html, re.DOTALL)
    tbody_main = re.search(r'<tr data-observe-run-row="true".*?</tr>', html, re.DOTALL)
    tfoot = re.search(r"<tfoot>.*?</tfoot>", html, re.DOTALL)
    assert thead and tbody_main and tfoot
    assert thead.group(0).count("<th ") == expected_cells
    assert tbody_main.group(0).count("<td") == expected_cells
    assert (
        tfoot.group(0).count("<th ") + tfoot.group(0).count("<td")
        == expected_cells
    )
    assert f'colspan="{expected_cells}"' in html


def test_runs_restore_dimension_when_second_value_appears() -> None:
    html = ui.render_runs_table(
        [
            _run(run_key="run-a", status="succeeded"),
            _run(run_key="run-b", status="failed"),
        ],
        bounds={"rows_shown": 2, "rows_total_in_scope": 2, "truncated": False},
        diagnostics={"partial_sources": 0, "failed_sources": 0},
    )

    assert 'data-column-id="status"' in html
    assert 'data-column-id="agent_name"' not in html


@pytest.mark.parametrize(
    ("bounds", "diagnostics"),
    [
        ({"rows_shown": 1, "rows_total_in_scope": 2, "truncated": True}, {}),
        (
            {
                "rows_shown": 1,
                "rows_total_in_scope": 2,
                "truncated": False,
                "has_next_page": True,
            },
            {},
        ),
        (
            {"rows_shown": 1, "rows_total_in_scope": 1, "truncated": False},
            {"partial_sources": 1, "failed_sources": 0},
        ),
        (
            {"rows_shown": 1, "rows_total_in_scope": 1, "truncated": False},
            {"partial_sources": 0, "failed_sources": 1},
        ),
        (None, {}),
    ],
)
def test_runs_never_suppress_dimensions_for_incomplete_scope(
    bounds: dict[str, object] | None,
    diagnostics: dict[str, object],
) -> None:
    html = ui.render_runs_table([_run()], bounds=bounds, diagnostics=diagnostics)

    for identifier in ("run_key_kind", "agent_name", "source_id", "source_kind", "status"):
        assert f'data-column-id="{identifier}"' in html
    assert "Hidden because every run" not in html


def test_client_runs_suppression_has_the_same_completeness_guards() -> None:
    script = ui._OBSERVE_SCRIPT
    complete = script.split("function runsScopeIsComplete(")[1].split(
        "\n  }\n", 1
    )[0]
    suppress = script.split("function suppressedRunDimensions(")[1].split(
        "\n  }\n", 1
    )[0]

    assert "bounds.truncated" in complete
    assert "bounds.has_previous_page" in complete
    assert "bounds.has_next_page" in complete
    assert "Number(bounds.rows_total_in_scope) !== runs.length" in complete
    assert "diagnostics.partial_sources" in complete
    assert "diagnostics.failed_sources" in complete
    assert "values.some(function (value) { return !value; })" in suppress
    assert "value.raw === first.raw" in suppress


def test_runs_do_not_suppress_an_unreported_dimension() -> None:
    html = ui.render_runs_table(
        [_run(run_key="run-a", source_id=None), _run(run_key="run-b", source_id=None)],
        bounds={"rows_shown": 2, "rows_total_in_scope": 2, "truncated": False},
        diagnostics={"partial_sources": 0, "failed_sources": 0},
    )

    assert 'data-column-id="source_id"' in html


def test_runs_help_and_details_are_keyboard_reachable_progressive_controls() -> None:
    html = ui.render_runs_table([_run()])

    assert 'class="observe-header-help"' in html
    assert 'class="observe-header-help-trigger"' in html
    assert 'aria-expanded="false"' in html
    assert 'aria-controls="observe-runs-help-run_key_kind"' in html
    assert 'id="observe-runs-help-run_key_kind"' in html
    assert '<details class="observe-run-detail"' in html
    assert "<summary>Run details for conversa\u2026</summary>" in html
    assert "<dt>Full run key</dt>" in html
    assert "<dt>Full source resource ID</dt>" in html

    script = ui._OBSERVE_SCRIPT
    assert 'event.key !== "Escape"' in script
    assert 'button.setAttribute("aria-expanded", "false")' in script
    assert '"Run details for " + abbreviateRunIdentifier(run.run_key)' in script
    assert '"Full source resource ID", copyValueNode(' in script


# ---------------------------------------------------------------------------
# Models / usage table (T050/T052)
# ---------------------------------------------------------------------------


def _usage(**overrides: object) -> ModelUsage:
    payload = dict(
        project_resource_id=PROJECT,
        agent_id="agent-a",
        deployment="gpt-4o-deploy",
        model="gpt-4o",
        requests=50,
        failures=5,
        p95_latency_ms=200.0,
        input_tokens=1000,
        output_tokens=2000,
        last_seen=_dt(6),
    )
    payload.update(overrides)
    return ModelUsage(**payload)


def test_render_models_usage_table_empty_shows_no_data_found() -> None:
    html = ui.render_models_usage_table([])
    assert "No data found for the selected filters." in html


def test_render_models_usage_table_includes_observed_usage_wording() -> None:
    html = ui.render_models_usage_table([_usage()])
    assert "observed usage, not billing data" in html
    assert "gpt-4o" in html
    assert "gpt-4o-deploy" in html


def test_render_models_usage_table_does_not_present_deployment_as_model() -> None:
    html = ui.render_models_usage_table(
        [_usage(model=None, deployment="deployment-only")]
    )
    assert "<tr><td>\u2014</td><td>deployment-only</td>" in html


def test_render_models_usage_table_last_seen_nullable_renders_missing() -> None:
    html = ui.render_models_usage_table([_usage(last_seen=None)])
    assert "metric-missing" in html
    assert "not agent lifecycle status" not in html


def test_render_models_usage_table_zero_failures_is_distinct_from_missing() -> None:
    html = ui.render_models_usage_table([_usage(failures=0)])
    assert "metric-zero" in html
    assert "0%" in html


def test_render_models_usage_table_renders_each_token_class_and_partial_state() -> None:
    html = ui.render_models_usage_table(
        [
            _usage(
                cache_read_tokens=0,
                cache_write_tokens=None,
                reasoning_tokens=12,
                token_classes_partial=True,
            )
        ]
    )
    assert "Cache read" in html
    assert "Cache write" in html
    assert "Reasoning" in html
    assert "Some telemetry records omitted one or more token-class attributes" in html
    assert "Partial class coverage" not in html
    assert "metric-zero" in html
    assert "\u2014" in html
    assert "observed usage, not billing data" in html


def test_render_models_usage_table_marks_intermittent_class_reporting() -> None:
    html = ui.render_models_usage_table(
        [
            _usage(
                cache_read_tokens=8,
                cache_write_tokens=4,
                reasoning_tokens=2,
                partially_reported_token_classes=("cache-read",),
                token_classes_partial=True,
            )
        ]
    )

    assert "Cache read" in html
    assert ">8<" in html
    assert "Some telemetry records omitted one or more token-class attributes" in html
    assert "Partial class coverage" not in html


def test_script_models_renderer_mirrors_token_class_fields_and_labels() -> None:
    script = ui._OBSERVE_SCRIPT
    for field in ("cache_read_tokens", "cache_write_tokens", "reasoning_tokens"):
        assert f"entry.{field}" in script
    for label in ("Cache read", "Cache write", "Reasoning"):
        assert label in script
    assert "Some telemetry records omitted one or more token-class attributes" in script
    assert "Partial class coverage" not in script


def test_models_renderers_show_additional_classes_and_truncation() -> None:
    html = ui.render_models_usage_table(
        [
            _usage(
                additional_token_classes={"gen_ai.usage.audio_tokens": 7},
                additional_token_classes_truncated=True,
            )
        ]
    )
    assert "gen_ai.usage.audio_tokens" in html
    assert ">7<" in html
    assert "Additional classes truncated" in html

    script = ui._OBSERVE_SCRIPT
    assert "entry.additional_token_classes" in script
    assert "entry.additional_token_classes_truncated" in script
    assert "Additional classes truncated" in script


def test_token_totals_are_rendered_in_separate_columns() -> None:
    agents_html = ui.render_agents_table([_agent(input_tokens=1000, output_tokens=2000)])
    models_html = ui.render_models_usage_table([_usage(input_tokens=1000, output_tokens=2000)])
    for html in (agents_html, models_html):
        assert 'data-label="Input tokens"' in html
        assert 'data-label="Output tokens"' in html
        assert 'data-label="Total tokens"' in html
        assert ">1,000<" in html
        assert ">2,000<" in html
        assert ">3,000<" in html


def test_models_row_with_only_totals_adds_no_partial_indicator() -> None:
    html = ui.render_models_usage_table(
        [
            _usage(
                input_tokens=1000,
                output_tokens=2000,
                cache_read_tokens=None,
                cache_write_tokens=None,
                reasoning_tokens=None,
                token_classes_partial=False,
            )
        ]
    )
    assert 'data-label="Input tokens"' in html
    assert 'data-label="Output tokens"' in html
    assert 'data-label="Total tokens"' in html
    assert "Not reported" not in html
    assert "Partial class coverage" not in html
    assert "observed usage, not billing data" in html


# ---------------------------------------------------------------------------
# Trend chart (T052/T053: bounded, non-color distinction, exact tooltips)
# ---------------------------------------------------------------------------


def test_render_trend_chart_empty_series_shows_no_data_found() -> None:
    html = ui.render_trend_chart("Invocations", [])
    assert "No data found for this chart." in html


def test_render_trend_chart_uses_solid_lines_only() -> None:
    html = ui.render_trend_chart(
        "Invocations",
        [{"label": "agent-a", "points": [("t1", 1), ("t2", 2)]}],
    )
    assert "observe-chart-line" in html
    assert "stroke-dasharray" not in html


def test_render_trend_chart_uses_distinct_marker_shapes_per_series() -> None:
    html = ui.render_trend_chart(
        "Invocations",
        [
            {"label": "series-1", "points": [("t1", 1)]},
            {"label": "series-2", "points": [("t1", 2)]},
        ],
    )
    assert "observe-chart-marker-circle" in html
    assert "observe-chart-marker-square" in html
    assert "\u25cf" in html
    assert "\u25a0" in html


def test_render_trend_chart_tooltip_has_exact_value() -> None:
    html = ui.render_trend_chart(
        "Latency",
        [{"label": "agent-a", "points": [("2024-01-01T00:00:00Z", 123.456)]}],
        unit=" ms",
    )
    assert 'data-observe-time="2024-01-01T00:00:00Z"' in html
    assert 'data-observe-time-prefix="agent-a \u2013 "' in html
    assert 'data-observe-time-suffix=": 0.123 s"' in html


def test_render_trend_chart_is_responsive_via_viewbox_not_fixed_size() -> None:
    html = ui.render_trend_chart("Invocations", [{"label": "a", "points": [("t1", 1)]}])
    assert 'viewBox="0 0 600 200"' in html
    assert 'width="600"' not in html
    assert 'height="200"' not in html


def test_render_trend_chart_includes_accessible_data_table_with_exact_values() -> None:
    html = ui.render_trend_chart(
        "Invocations",
        [{"label": "agent-a", "points": [("t1", 7)]}],
    )
    assert "observe-chart-data visually-hidden" in html
    assert "<td>7</td>" in html or ">7<" in html


def test_render_trend_chart_gridlines_and_gradient_are_aria_hidden_decoration() -> None:
    html = ui.render_trend_chart("Invocations", [{"label": "a", "points": [("t1", 1), ("t2", 2)]}])
    assert 'class="observe-chart-grid" x1=' in html
    assert 'aria-hidden="true"' in html
    assert "linearGradient" in html


def test_bound_points_downsamples_but_keeps_first_and_last() -> None:
    points = [(f"t{i}", i) for i in range(500)]
    bounded = ui._bound_points(points, max_points=60)
    assert len(bounded) == 60
    assert bounded[0] == points[0]
    assert bounded[-1] == points[-1]


def test_bound_points_leaves_short_series_untouched() -> None:
    points = [("t1", 1), ("t2", 2)]
    assert ui._bound_points(points, max_points=60) == points


def test_render_trend_chart_bounds_long_series_by_default() -> None:
    points = [(f"t{i}", i) for i in range(500)]
    html = ui.render_trend_chart("Invocations", [{"label": "agent-a", "points": points}])
    assert html.count("observe-chart-point") <= ui.MAX_TREND_POINTS


# ---------------------------------------------------------------------------
# Coverage / troubleshooting (T058/T062)
# ---------------------------------------------------------------------------


def _coverage(state: str, **overrides: object) -> CoverageResult:
    payload = dict(
        source_id="src-1",
        dimension="recent_traces",
        state=state,
        reason=f"reason for {state}",
        next_action=f"action for {state}",
        refreshed_at=_dt(),
    )
    payload.update(overrides)
    return CoverageResult(**payload)


@pytest.mark.parametrize(
    "state",
    [
        "available",
        "inaccessible",
        "not_configured",
        "no_data",
        "not_reported",
        "partial",
        "protected_or_unavailable",
    ],
)
def test_coverage_state_renders_distinct_label_and_reason(state: str) -> None:
    html = ui.render_coverage_table([_coverage(state)])
    assert ui.COVERAGE_STATE_LABELS[state]["label"] in html
    assert f"reason for {state}" in html
    assert f"action for {state}" in html


def test_coverage_state_error_fallback_renders_something_actionable() -> None:
    # "error" is not part of the documented seven-state OpenAPI enum but must
    # still render safely as a fallback rather than raising or vanishing.
    html = ui.render_coverage_table([{"source_id": "src-x", "dimension": "recent_traces",
                                       "state": "error", "reason": "boom", "next_action": "retry",
                                       "refreshed_at": _dt()}])
    assert "Error" in html
    assert "boom" in html
    assert "retry" in html


def test_coverage_table_all_states_have_labels() -> None:
    for state, copy in ui.COVERAGE_STATE_LABELS.items():
        assert copy["label"]
        assert copy["tone"] in {"ok", "warn", "crit", "muted"}


def test_coverage_table_all_dimensions_have_labels() -> None:
    dimensions = [
        "resource_access",
        "telemetry_connection",
        "recent_traces",
        "agent_attribution",
        "model_attribution",
        "token_usage",
        "tool_attribution",
        "run_correlation",
        "trace_correlation",
        "protected_content",
    ]
    for dimension in dimensions:
        assert dimension in ui.COVERAGE_DIMENSION_LABELS


def test_render_coverage_table_empty_reports_no_coverage_information() -> None:
    html = ui.render_coverage_table([])
    assert "No coverage information reported." in html


def test_render_coverage_view_preserves_other_sources_when_one_fails() -> None:
    """T062: a failed/partial source must never hide another source's data."""
    rows = [
        _coverage("available", source_id="src-ok", dimension="recent_traces"),
        _coverage("inaccessible", source_id="src-broken", dimension="protected_content"),
        _coverage("partial", source_id="src-partial", dimension="token_usage"),
    ]
    html = ui.render_coverage_view(rows)
    assert "src-ok" in html
    assert "src-broken" in html
    assert "src-partial" in html
    assert ui.COVERAGE_STATE_LABELS["available"]["label"] in html
    assert ui.COVERAGE_STATE_LABELS["inaccessible"]["label"] in html
    assert ui.COVERAGE_STATE_LABELS["partial"]["label"] in html


def test_render_diagnostics_banner_empty_diagnostics_renders_nothing() -> None:
    assert ui.render_diagnostics_banner({}) == ""


def test_render_diagnostics_banner_partial_results_notice_when_any_source_failed_or_partial() -> None:
    diagnostics = {
        "started_at": _dt(),
        "completed_at": _dt(),
        "duration_ms": 100,
        "source_count": 3,
        "successful_sources": 2,
        "partial_sources": 0,
        "failed_sources": 1,
        "cache_status": "hit",
    }
    html = ui.render_diagnostics_banner(diagnostics)
    assert "Partial results" in html
    assert "still shown below" in html


def test_render_diagnostics_banner_no_notice_when_all_sources_succeeded() -> None:
    diagnostics = {
        "started_at": _dt(),
        "completed_at": _dt(),
        "duration_ms": 100,
        "source_count": 2,
        "successful_sources": 2,
        "partial_sources": 0,
        "failed_sources": 0,
        "cache_status": "hit",
    }
    html = ui.render_diagnostics_banner(diagnostics)
    assert "Partial results" not in html


def test_render_diagnostics_banner_zero_failed_sources_is_not_missing() -> None:
    diagnostics = {
        "started_at": _dt(),
        "completed_at": _dt(),
        "duration_ms": 0,
        "source_count": 1,
        "successful_sources": 1,
        "partial_sources": 0,
        "failed_sources": 0,
        "cache_status": "hit",
    }
    html = ui.render_diagnostics_banner(diagnostics)
    assert "metric-zero" in html  # duration_ms == 0 and failed_sources == 0 both render as zero


# ---------------------------------------------------------------------------
# Portal links (T053)
# ---------------------------------------------------------------------------


def test_render_portal_links_uses_known_labels_for_documented_keys() -> None:
    html = ui.render_portal_links({"foundry_resource": "https://portal.azure.com/#resource/x/overview"})
    assert "Open Foundry resource" in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html


def test_render_portal_links_falls_back_to_title_case_for_unknown_keys() -> None:
    html = ui.render_portal_links({"some_new_undocumented_target": "https://example.com/x"})
    assert "Some New Undocumented Target" in html


def test_render_portal_links_empty_renders_nothing() -> None:
    assert ui.render_portal_links({}) == ""


def test_build_azure_resource_portal_url_matches_documented_pattern() -> None:
    url = ui.build_azure_resource_portal_url(PROJECT)
    assert url == f"https://portal.azure.com/#resource{PROJECT}/overview"


# ---------------------------------------------------------------------------
# Agent detail shell (T050/T053)
# ---------------------------------------------------------------------------


def test_render_agent_detail_shell_includes_bounded_trends_and_portal_links() -> None:
    html = ui.render_agent_detail_shell(
        _agent(),
        trends=[{"title": "Invocations", "series": [{"label": "agent-a", "points": [("t1", 1)]}]}],
        portal_links={"foundry_resource": "https://portal.azure.com/#resource/x/overview"},
    )
    assert "Agent A" in html
    assert "observe-chart" in html
    assert "Open Foundry resource" in html


# ---------------------------------------------------------------------------
# Trace detail shell + protected content (T054)
# ---------------------------------------------------------------------------


def test_render_trace_detail_shell_without_content_only_shows_load_button() -> None:
    html = ui.render_trace_detail_shell("trace-1")
    assert 'data-observe-load-protected="trace-1"' in html
    assert "Load protected content" in html
    assert "input_messages" not in html
    assert "system_instructions" not in html


def test_render_trace_detail_shell_available_content_renders_fields() -> None:
    content = GenerativeAIContent(
        trace_id="trace-1",
        span_id="span-1",
        source_resource_id=PROJECT,
        protection_state="available",
        input_messages=[{"role": "user", "content": "hi"}],
        output_messages=[{"role": "assistant", "content": "hello"}],
        system_instructions="be nice",
        tool_content=None,
        evaluation_explanation=None,
    )
    html = ui.render_trace_detail_shell("trace-1", content=content)
    assert "Available" in html
    assert "be nice" in html
    assert "Input messages" in html
    assert "Not reported" in html  # tool_content / evaluation_explanation omitted


def test_render_trace_detail_shell_protected_or_unavailable_shows_no_fallback_statement() -> None:
    content = GenerativeAIContent(
        trace_id="trace-1",
        source_resource_id=PROJECT,
        protection_state="protected_or_unavailable",
    )
    html = ui.render_trace_detail_shell("trace-1", content=content)
    assert "Protected or unavailable" in html
    assert "No unprotected or legacy fallback" in html
    assert "input_messages" not in html


def test_render_trace_detail_shell_not_configured_explains_authorization_gap() -> None:
    content = GenerativeAIContent(
        trace_id="trace-1",
        source_resource_id=PROJECT,
        protection_state="not_configured",
    )
    html = ui.render_trace_detail_shell("trace-1", content=content)
    assert "Not configured" in html
    assert "not configured for this resource" in html


def test_render_trace_detail_shell_unknown_protection_state_fails_closed() -> None:
    html = ui.render_trace_detail_shell("trace-1", content={"protection_state": "made_up_state"})
    assert "No unprotected or legacy fallback" in html
    assert "made_up_state" not in html.split("data-trace-id")[0]


def test_render_trace_detail_shell_carries_source_resource_id_for_protected_fetch() -> None:
    # TraceContentRequest requires both `source_resource_id` and `trace_id`
    # (contracts/observe-api.openapi.yaml); the "Load protected content"
    # button must carry both so the click handler can build a valid request.
    html = ui.render_trace_detail_shell("trace-1", source_resource_id=PROJECT)
    assert 'data-observe-load-protected="trace-1"' in html
    assert f'data-observe-source-resource-id="{PROJECT}"' in html


def test_render_trace_detail_shell_omits_source_resource_id_attribute_when_not_supplied() -> None:
    html = ui.render_trace_detail_shell("trace-1")
    assert "data-observe-source-resource-id" not in html


def test_render_trace_detail_shell_html_escapes_source_resource_id() -> None:
    html = ui.render_trace_detail_shell("trace-1", source_resource_id='"><script>alert(1)</script>')
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# Full page assembly
# ---------------------------------------------------------------------------


def test_render_observe_page_includes_noscript_fallback() -> None:
    html = ui.render_observe_page()
    assert "<noscript>" in html
    assert "requires JavaScript" in html


def test_render_observe_page_has_no_external_asset_references() -> None:
    html = ui.render_observe_page()
    assert "<link " not in html
    assert 'src="http' not in html
    assert "cdn." not in html


def test_render_observe_page_includes_all_operator_views() -> None:
    html = ui.render_observe_page(
        overview_metrics=[{"title": "Invocations", "value": 5}],
        agents=[_agent()],
        usage=[_usage()],
        coverage=[_coverage("available")],
    )
    for section_id in ("overview", "agents", "usage", "tools", "runs"):
        assert f'<section id="{section_id}"' in html
    assert '<section id="coverage"' not in html


# ---------------------------------------------------------------------------
# Safety: raw content and browser persistence (T054 / URL persistence)
# ---------------------------------------------------------------------------


def test_script_never_uses_local_or_session_storage_or_cookies() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "document.cookie" not in script


def test_script_only_persists_allow_listed_filter_keys_in_url() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "FILTER_KEYS" in script
    for key in ui.OBSERVE_FILTER_QUERY_KEYS:
        assert f'"{key}"' in script
    raw_content_fields = (
        "input_messages",
        "output_messages",
        "system_instructions",
        "tool_content",
        "evaluation_explanation",
    )
    for field in raw_content_fields:
        assert field not in script


def test_script_uses_abort_controller_and_request_token_for_stale_suppression() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "AbortController" in script
    assert "signal" in script
    assert "requestToken" in script
    assert "myToken !== requestToken" in script


def test_script_auto_refreshes_every_five_minutes() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "window.setInterval(function () {" in script
    assert "fetchObserveData(false);" in script
    assert "}, AUTO_REFRESH_MS)" in script
    assert "AUTO_REFRESH_MS = 300000" in script


def test_script_computes_default_seven_day_range_when_missing_from_url() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "DEFAULT_RANGE_MS = 7 * 24 * 60 * 60 * 1000" in script
    assert 'return local.toISOString().slice(0, 16);' in script
    assert 'return isNaN(moment.getTime()) ? "" : moment.toISOString();' in script


def test_missing_overview_metric_is_visually_subordinate_to_reported_values() -> None:
    styles = ui._OBSERVE_STYLES
    assert ".observe-card-value {" in styles
    assert "font-size: 30px;" in styles
    missing_rule = styles.split(".observe-card-value .metric-missing {", 1)[1].split(
        "}", 1
    )[0]
    assert "font-size: 17px;" in missing_rule
    assert "font-weight: 600;" in missing_rule


def test_apply_pushes_filter_state_into_browser_history() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "history.replaceState" in script
    assert "history.pushState" in script
    submit_block = script.split('form.addEventListener("submit"', 1)[1].split(
        'var costForm = document.getElementById("observe-cost-filter-form")', 1
    )[0]
    assert "pushUrl();" in submit_block
    assert "syncUrl();" not in submit_block


def test_script_protected_content_is_only_loaded_on_explicit_click() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "loadProtectedContent" in script
    assert "addEventListener(\"click\"" in script
    # The click handler is the only call site for loadProtectedContent.
    assert script.count("loadProtectedContent(") == 2  # definition-adjacent call + listener wiring


def test_script_apply_button_is_the_only_place_draft_becomes_applied() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "appliedFilters = draftFilters;" in script
    # Ensure this assignment only happens inside the submit handler block.
    submit_block = script.split('form.addEventListener("submit"')[1]
    assert "appliedFilters = draftFilters;" in submit_block


# ---------------------------------------------------------------------------
# API contract reconciliation: POST JSON, not GET query-string
# (contracts/observe-api.openapi.yaml -- /api/observe/query and
# /api/observe/trace-content)
# ---------------------------------------------------------------------------


def test_script_queries_observe_query_endpoint_via_post_json_not_get_query_string() -> None:
    script = ui._OBSERVE_SCRIPT
    assert 'fetch("/api/observe/query", {' in script
    assert '"/api/observe/query?"' not in script
    assert '"/api/observe/query" + ' not in script
    query_block = script.split('fetch("/api/observe/query"')[1].split(");")[0]
    assert 'method: "POST"' in query_block
    assert '"Content-Type": "application/json"' in query_block
    assert "JSON.stringify(payload)" in query_block
    assert "signal: controller.signal" in query_block


def test_script_observe_query_payload_matches_observe_query_schema() -> None:
    # ObserveQuery (contracts/observe-api.openapi.yaml): {view, filters:
    # {foundry_resource_id?, project_resource_id?, agent_id?, model?, tool_name?, run_key?,
    # start (required), end (required)}, refresh?}.
    script = ui._OBSERVE_SCRIPT
    payload_block = script.split("var payload = {")[1].split("};")[0]
    filter_builder = script.split("function observeFiltersForRequest(", 1)[1].split(
        "function rerenderTemporalSurface(", 1
    )[0]
    assert "view: VIEW_WIRE_NAMES[currentView] || currentView" in payload_block
    assert "filters: observeFiltersForRequest(appliedFilters)" in payload_block
    for key in (
        "foundry_resource_id",
        "project_resource_id",
        "agent_id",
        "model",
        "tool_name",
        "run_key",
    ):
        assert f"{key}: filters.{key} || null" in filter_builder
    assert "start: bounds.start" in filter_builder
    assert "end: bounds.end" in filter_builder
    assert "refresh: manual === true" in payload_block
    assert "page: currentPage" in payload_block
    assert "page_size: currentPageSize" in payload_block
    assert "search: currentSearch || null" in payload_block
    assert "sort_by: currentSortBy || null" in payload_block
    assert "sort_direction: currentSortDirection" in payload_block


def test_script_pages_large_views_and_round_trips_safe_state_in_url() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "function paginationToolbar(bounds)" in script
    assert "No rows are available on page " in script
    assert " from the highest-ranked results" in script
    assert '"Search this view"' in script
    assert '"Previous"' in script
    assert '"Next"' in script
    assert 'params.set("page", String(currentPage))' in script
    assert 'params.set("page_size", String(currentPageSize))' in script
    assert 'params.set("search", currentSearch)' in script
    assert 'params.set("sort_by", currentSortBy)' in script
    assert "SERVER_SORT_KEYS" in script
    assert '"p95 latency": "p95_latency_ms"' in script


def test_script_manual_refresh_sets_refresh_true_and_auto_refresh_sets_refresh_false() -> None:
    script = ui._OBSERVE_SCRIPT
    # "Refresh now" button and the Apply submit handler are explicit user
    # actions and both request refresh: true (cache bypass); the periodic
    # timer tick requests refresh: false.
    assert "fetchObserveData(true);" in script
    assert "fetchObserveData(false);" in script
    refresh_button_block = script.split('getElementById("observe-refresh-now")')[1].split("}")[0]
    assert "fetchObserveData(true);" in refresh_button_block
    submit_block = script.split('form.addEventListener("submit"')[1].split("});")[0]
    assert "fetchObserveData(true);" in submit_block


def test_script_loads_protected_content_via_post_json_not_get_query_string() -> None:
    script = ui._OBSERVE_SCRIPT
    assert 'fetch("/api/observe/trace-content", {' in script
    assert '"/api/observe/trace-content?"' not in script
    assert '"/api/observe/trace-content" + ' not in script
    trace_block = script.split('fetch("/api/observe/trace-content"')[1].split(");")[0]
    assert 'method: "POST"' in trace_block
    assert '"Content-Type": "application/json"' in trace_block
    assert "source_resource_id: sourceResourceId" in trace_block
    assert "trace_id: traceId" in trace_block
    assert "signal: controller.signal" in trace_block


def test_script_load_protected_content_refuses_request_missing_source_resource_id() -> None:
    # TraceContentRequest requires both `source_resource_id` and `trace_id`;
    # the click handler must fail closed rather than send an invalid body.
    script = ui._OBSERVE_SCRIPT
    handler_block = script.split("function loadProtectedContent(button) {")[1].split(
        "\n  }\n"
    )[0]
    assert 'button.getAttribute("data-observe-source-resource-id")' in handler_block
    assert "if (!traceId || !sourceResourceId)" in handler_block
    assert "return null;" in handler_block


def test_script_view_wire_names_translate_internal_usage_id_to_models() -> None:
    # Mirrors OBSERVE_VIEW_WIRE_NAMES: the wire `view` enum spells the
    # internal "usage" view as "models".
    script = ui._OBSERVE_SCRIPT
    assert (
        'var VIEW_WIRE_NAMES = { overview: "overview", runs: "runs", agents: "agents", '
        'usage: "models", tools: "tools" };'
    ) in script


def test_regression_usage_view_id_and_label_unchanged_while_wire_payload_uses_models() -> None:
    # Regression guard for the "usage" vs "models" API mismatch:
    # contracts/observe-api.openapi.yaml's `ObserveQuery.view` enum only
    # knows [overview, agents, models, coverage] -- it has no "usage" value.
    # The fix must translate "usage" -> "models" *only* in the outgoing
    # query payload, while every user-facing/DOM-facing surface (internal
    # view id, visible label, section id/heading) keeps spelling it "usage"
    # so CSS/DOM/test selectors and copy remain stable.
    assert "usage" in ui.OBSERVE_VIEWS
    assert "models" not in ui.OBSERVE_VIEWS
    assert ui.OBSERVE_VIEW_LABELS["usage"] == "Models and usage"
    assert ui.OBSERVE_VIEW_WIRE_NAMES["usage"] == "models"

    html = ui.render_observe_page(
        overview_metrics=[{"title": "Invocations", "value": 5}],
        agents=[_agent()],
        usage=[_usage()],
        coverage=[_coverage("available")],
    )
    # DOM ids/headings stay "usage" -- they are never renamed to "models".
    assert 'id="usage"' in html
    assert 'id="usage-heading"' in html
    assert "Models and usage" in html
    assert 'id="models"' not in html

    script = ui._OBSERVE_SCRIPT
    # The client only substitutes "usage" -> "models" when building the
    # `/api/observe/query` request body, via the VIEW_WIRE_NAMES lookup.
    payload_block = script.split("var payload = {")[1].split("};")[0]
    assert "view: VIEW_WIRE_NAMES[currentView] || currentView" in payload_block
    assert 'usage: "models"' in script


def test_script_runtime_kind_labels_are_plain_text() -> None:
    script = ui._OBSERVE_SCRIPT
    fn_block = script.split("function renderSourceKindBadge(kind) {")[1].split(
        "\n  }\n", 1
    )[0]
    assert 'makeEl("span", "observe-source-kind", label)' in fn_block
    assert 'source.textContent = "Unclassified";' in fn_block
    assert "renderBadgeJs" not in fn_block
    assert "observe-tone-" not in fn_block


def test_script_tools_and_runs_render_sources_bounds_and_explained_empty_states() -> None:
    script = ui._OBSERVE_SCRIPT
    agents_block = script.split("function renderAgents(data, diagnostics, bounds) {")[1].split(
        "\n  }\n"
    )[0]
    assert 'sourceCell.title = agent.source_id || ""' in agents_block
    for function_name, source_field, empty_copy in (
        (
            "function renderTools(data, diagnostics, bounds)",
            "tool.source_id || \"\u2014\"",
            "No tool activity was found for the selected filters.",
        ),
        (
            "function renderRuns(data, diagnostics, bounds)",
            "sourceWorkspaceName(run.source_id)",
            "No runs could be correlated for the selected filters.",
        ),
    ):
        block = script.split(function_name)[1].split("\n  }\n")[0]
        assert source_field in block
        assert "boundsNoticeNode(bounds" in block
        assert empty_copy in block
    assert "renderMillisecondsAsSeconds(tool.p95_latency_ms)" in script
    assert "observedTokenTotal(run.input_tokens, run.output_tokens)" in script
    assert "run.run_key_kind || \"\u2014\"" in script
    assert "Elapsed time between first and last observed activity" in script


# ---------------------------------------------------------------------------
# Functional page: parse fetch responses and render/update the active view
# (overview cards, agents, models/usage, tools, runs,
# refreshed timestamp) instead of only updating the refresh-status text.
# ---------------------------------------------------------------------------


def test_render_observe_page_wraps_each_view_fragment_in_a_client_updatable_container() -> None:
    # Each server-rendered fragment must live inside a container the client
    # script can look up by id (`getElementById(view + "-content")`) and
    # replace wholesale once a fetch response is parsed.
    html = ui.render_observe_page(
        overview_metrics=[{"title": "Invocations", "value": 5}],
        agents=[_agent()],
        usage=[_usage()],
        coverage=[_coverage("available")],
    )
    for view in ui.OBSERVE_VIEWS:
        assert f'id="{view}-content"' in html
        assert f'data-observe-view-content="{view}"' in html


def test_script_defines_response_parsing_and_render_dispatch_functions() -> None:
    script = ui._OBSERVE_SCRIPT
    for name in (
        "function renderObserveResponse(body)",
        "function renderOverview(data, diagnostics)",
        "function renderAgents(data, diagnostics, bounds)",
        "function renderUsage(data, diagnostics, bounds)",
        "function renderTools(data, diagnostics, bounds)",
        "function renderRuns(data, diagnostics, bounds)",
        "function internalViewFromWire(view)",
        "function setViewContent(view, nodes)",
    ):
        assert name in script


def test_script_fetch_success_parses_json_and_dispatches_to_render_before_updating_status() -> None:
    script = ui._OBSERVE_SCRIPT
    then_block = script.split("return fetch(\"/api/observe/query\"")[1].split(".catch(")[0]
    assert "response.json().then(function (body)" in then_block
    assert "renderObserveResponse(body);" in then_block
    # renderObserveResponse must run before the refresh-status text is
    # updated, so the page reflects the new data by the time the status
    # announces a refresh.
    render_index = then_block.index("renderObserveResponse(body);")
    status_index = then_block.index(
        'setRefreshStatus("Refreshed", new Date().toISOString());'
    )
    assert render_index < status_index


def test_script_fetch_still_reports_failure_status_for_non_ok_responses() -> None:
    script = ui._OBSERVE_SCRIPT
    then_block = script.split("return fetch(\"/api/observe/query\"")[1].split(".catch(")[0]
    assert "if (!response.ok) {" in then_block
    not_ok_block = then_block.split("if (!response.ok) {")[1].split("}")[0]
    assert 'setRefreshStatus("Refresh failed");' in not_ok_block
    assert "return null;" in not_ok_block


def test_script_suppresses_stale_response_both_before_and_after_json_parsing() -> None:
    # A newer request may start while an older one is still in flight, or
    # while its (async) `.json()` parse is still pending -- both must be
    # checked so a slow, superseded response can never overwrite the view
    # that a later request already rendered.
    script = ui._OBSERVE_SCRIPT
    then_block = script.split("return fetch(\"/api/observe/query\"")[1].split(".catch(")[0]
    assert then_block.count("myToken !== requestToken") == 2


def test_script_render_dispatch_routes_each_view_to_its_own_renderer_and_field() -> None:
    script = ui._OBSERVE_SCRIPT
    dispatch_block = script.split("function renderObserveResponse(body) {")[1].split(
        "\n  }\n"
    )[0]
    assert "var view = internalViewFromWire(body.view) || currentView;" in dispatch_block
    assert "renderOverview(body.data, body.diagnostics);" in dispatch_block
    assert "renderAgents(body.data, body.diagnostics, body.bounds);" in dispatch_block
    assert "renderUsage(body.data, body.diagnostics, body.bounds);" in dispatch_block
    assert "renderTools(body.data, body.diagnostics, body.bounds);" in dispatch_block
    assert "renderRuns(body.data, body.diagnostics, body.bounds);" in dispatch_block
    assert "renderCoverage(" not in dispatch_block


def test_script_internal_view_from_wire_maps_models_back_to_usage_only() -> None:
    script = ui._OBSERVE_SCRIPT
    fn_block = script.split("function internalViewFromWire(view) {")[1].split("\n  }\n")[0]
    assert 'if (view === "models") {' in fn_block
    assert 'return "usage";' in fn_block
    assert "return view;" in fn_block


def test_script_uses_safe_dom_construction_not_raw_markup_concatenation() -> None:
    # No branch of the render/dispatch code may build markup via raw string
    # concatenation; every node is created via `document.createElement`/a
    # `makeEl` helper and text is always assigned via `.textContent`.
    script = ui._OBSERVE_SCRIPT
    assert "innerHTML" not in script
    assert "outerHTML" not in script
    assert "document.write(" not in script
    assert script.count("document.createElement(") >= 8
    assert "function makeEl(tag, className, text) {" in script
    assert "node.textContent = text;" in script


def test_script_view_content_helper_targets_the_generated_wrapper_divs() -> None:
    script = ui._OBSERVE_SCRIPT
    fn_block = script.split("function setViewContent(view, nodes) {")[1].split("\n  }\n")[0]
    assert 'document.getElementById(view + "-content")' in fn_block
    assert "clearChildren(container);" in fn_block


def test_script_omits_internal_query_diagnostics_from_operator_views() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "function renderDiagnosticsBannerNode(" not in script
    assert '"Sources queried"' not in script
    assert '"Query duration"' not in script


def test_script_moves_repeated_disclaimers_to_header_help() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "Last seen reflects observed telemetry only, not agent lifecycle status." not in script
    assert "Token columns show observed usage, not billing data." not in script
    assert "Observed token usage from telemetry; this is not billing data." in script
    assert "Most recent telemetry in the selected range" in script


def test_script_zero_vs_missing_distinction_uses_distinct_classes() -> None:
    script = ui._OBSERVE_SCRIPT
    fn_block = script.split("function renderMaybeMissing(value, opts) {")[1].split(
        "\n  }\n"
    )[0]
    assert "metric-missing" in fn_block
    assert "metric-zero" in fn_block
    assert "metric-value" in fn_block


def test_script_seconds_formatter_preserves_three_decimals_for_whole_seconds() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "minimumFractionDigits: 3" in script
    assert "maximumFractionDigits: 3" in script
    assert "minimumFractionDigits === undefined" in script
    assert "maximumFractionDigits === undefined" in script
    assert 'entry.model || "\\u2014"' not in script
    assert "primaryCellWithAction(" in script
    assert "entry.model," in script


def test_script_coverage_state_and_dimension_labels_mirror_python_constants() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "var COVERAGE_STATE_LABELS = {" in script
    assert "var COVERAGE_DIMENSION_LABELS = {" in script
    state_block = script.split("var COVERAGE_STATE_LABELS = {")[1].split("};")[0]
    for state, copy in ui.COVERAGE_STATE_LABELS.items():
        assert f"{state}:" in state_block
        assert f'"{copy["label"]}"' in state_block
        assert f'"{copy["tone"]}"' in state_block
    dimension_block = script.split("var COVERAGE_DIMENSION_LABELS = {")[1].split("};")[0]
    for dimension, label in ui.COVERAGE_DIMENSION_LABELS.items():
        assert f"{dimension}:" in dimension_block
        assert f'"{label}"' in dimension_block


def test_script_does_not_render_removed_coverage_view() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "function renderCoverage(" not in script


def test_script_nav_link_click_fetches_the_newly_selected_view() -> None:
    # Regression: switching the active view via a nav link must trigger a
    # live fetch for that view, not only update the URL and leave the page
    # showing the initial server-rendered snapshot of a different view.
    script = ui._OBSERVE_SCRIPT
    nav_block = script.split(
        'document.querySelectorAll("[data-observe-nav-link]").forEach(function (link) {'
    )[2].split("    });", 1)[0]
    assert 'var nextView = link.getAttribute("data-observe-nav-link");' in nav_block
    assert "activateView(nextView);" in nav_block
    assert "event.preventDefault();" in script
    assert "pushUrl();" in nav_block
    assert "fetchObserveData(false);" in nav_block


def test_page_renders_real_tab_panels_instead_of_same_page_anchors() -> None:
    html = ui.render_observe_page()
    assert 'role="tablist"' in html
    assert 'href="#agents"' not in html
    assert '<section id="overview" data-observe-panel role="tabpanel"' in html
    assert '<section id="agents" data-observe-panel role="tabpanel"' in html
    assert 'aria-labelledby="observe-tab-agents" hidden' in html
    assert "function activateView(view)" in html


def test_generated_html_never_contains_raw_content_field_names_outside_trace_detail() -> None:
    html = ui.render_observe_page(
        overview_metrics=[{"title": "Invocations", "value": 5}],
        agents=[_agent()],
        usage=[_usage()],
        coverage=[_coverage("available")],
    )
    # None of the raw generative-AI content field names should ever leak into
    # the standard page (they only ever appear inside an explicitly-loaded
    # trace detail shell, which render_observe_page does not include).
    for field in (
        "input_messages",
        "output_messages",
        "system_instructions",
        "tool_content",
        "evaluation_explanation",
    ):
        assert field not in html


def test_console_address_builder_never_serializes_generative_content() -> None:
    script = ui._OBSERVE_SCRIPT
    address_builder = script.split("function buildStateUrl() {")[1].split(
        "\n  }\n", 1
    )[0]
    generative_fields = {
        "input_messages",
        "output_messages",
        "system_instructions",
        "tool_content",
        "evaluation_explanation",
        "prompt",
        "response",
    }

    assert "FILTER_KEYS.forEach(function (key)" in address_builder
    assert "params.append(key, value)" in address_builder
    assert "data-copy-value" not in address_builder
    assert "scopeSearch" not in address_builder
    assert set(ui.OBSERVE_FILTER_QUERY_KEYS).isdisjoint(generative_fields)
    for field in generative_fields:
        assert field not in address_builder


def test_styles_use_explicit_themes_not_prefers_color_scheme() -> None:
    """Observe must theme explicitly (via ``data-theme``) so it never drifts
    from the Cockpit through an independent OS-preference media query."""
    styles = ui._OBSERVE_STYLES
    # No bare OS-preference block that could diverge from Cockpit's dark theme.
    assert "prefers-color-scheme" not in styles
    # Explicit, deliberate light + dark themes keyed off ``data-theme``.
    assert '[data-theme="light"]' in styles
    assert "color-scheme: dark" in styles
    # Canonical shared design tokens are present (theme parity with ui_theme).
    for token in ("--bg", "--card", "--text", "--border", "--info", "--ok", "--warn", "--crit"):
        assert f"{token}:" in styles
    # Observe series palette still exposed for the trend charts.
    assert "--observe-series-1" in styles


def test_new_controls_use_shared_tokens_in_both_themes_without_new_color_literals() -> None:
    styles = ui._OBSERVE_STYLES
    for tokens in (ui_theme.DARK_TOKENS, ui_theme.LIGHT_TOKENS):
        assert set(tokens) == set(ui_theme.TOKEN_NAMES)
        for name, value in tokens.items():
            assert f"{name}: {value};" in styles

    themed_selectors = (
        ".observe-scope-trigger",
        ".observe-scope-panel",
        ".observe-window-filter select",
        ".observe-header-help-trigger",
        ".observe-header-help-panel",
        ".observe-copy-fallback[open] label",
        ".observe-run-detail-row td",
    )
    for selector in themed_selectors:
        rule = re.search(rf"{re.escape(selector)}[^{{]*\{{([^}}]+)\}}", styles)
        assert rule, f"missing themed rule for {selector}"
        assert "var(--observe" in rule.group(1) or "color-mix(" in rule.group(1)

    # The fourth legacy chart-series value predates these controls. New control
    # styles must consume theme tokens instead of extending this literal set.
    assert set(re.findall(r"#[0-9a-fA-F]{3,8}", ui._OBSERVE_COMPONENT_CSS)) == {
        "#bc8cff"
    }


def test_page_uses_shared_url_theme_control_without_browser_storage() -> None:
    html = ui.render_observe_page()
    assert 'data-aos-theme-toggle' in html
    assert 'data-theme-link' in html
    assert 'next.set("theme", theme)' in html
    assert "setupAgentOpsThemeToggle();" in html
    assert "localStorage" not in html
    assert "sessionStorage" not in html
    assert "document.cookie" not in html


def test_runs_table_structural_budgets_hold_at_standard_and_maximum_scale() -> None:
    standard_rows = make_run_usage_rows_at_scale(4)
    standard = ui.render_runs_table(
        standard_rows,
        bounds={
            "rows_shown": len(standard_rows),
            "rows_total_in_scope": len(standard_rows),
            "truncated": False,
        },
        diagnostics={"partial_sources": 0, "failed_sources": 0},
    )
    header = re.search(r"<thead>.*?</thead>", standard, re.DOTALL)
    assert header
    assert header.group(0).count("<th ") <= ui.RUNS_TABLE_COLUMN_BUDGET
    assert ui.RUNS_TABLE_COLUMN_BUDGET * 72 <= ui.RUNS_TABLE_STANDARD_VIEWPORT_PX
    assert ".observe-runs-table { table-layout: fixed; }" in ui._OBSERVE_COMPONENT_CSS
    assert "table { border-collapse: collapse; width: 100%;" in ui._OBSERVE_COMPONENT_CSS
    assert "overflow-x" not in ui._OBSERVE_COMPONENT_CSS

    bounded_rows = make_run_usage_rows_at_scale(MAX_ROWS_PER_QUERY)
    bounded = ui.render_runs_table(
        bounded_rows,
        bounds={
            "rows_shown": MAX_ROWS_PER_QUERY,
            "rows_total_in_scope": MAX_ROWS_PER_QUERY + 1,
            "truncated": True,
        },
    )
    assert bounded.count('data-observe-run-row="true"') == MAX_ROWS_PER_QUERY
    assert bounded.count('data-observe-run-detail-row="true"') == MAX_ROWS_PER_QUERY
    assert f"Showing {MAX_ROWS_PER_QUERY} of {MAX_ROWS_PER_QUERY + 1} rows in scope." in bounded
    assert "1 row is not displayed." in bounded
    assert len(bounded) / MAX_ROWS_PER_QUERY < 4_200


def test_get_accessor_supports_both_mapping_and_object() -> None:
    assert ui._get({"a": 1}, "a") == 1
    assert ui._get({"a": 1}, "b", "default") == "default"
    assert ui._get(_agent(), "agent_name") == "Agent A"
    assert ui._get(None, "anything", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# Agent detail panel wiring (T053): explicit click -> POST
# /api/observe/agent-detail with bounded trend rendering and portal links.
# ---------------------------------------------------------------------------


def test_render_observe_page_includes_agent_detail_mount_point() -> None:
    html = ui.render_observe_page(agents=[_agent()])
    assert 'id="agent-detail-content"' in html
    assert "data-observe-agent-detail-content" in html
    assert 'aria-live="polite"' in html
    # The mount point must be a sibling of the regenerated agents table
    # wrapper, not nested inside it -- otherwise re-rendering the agents
    # table on refresh would wipe an already-open detail panel.
    agents_section = html.split('<section id="agents"')[1].split("</section>")[0]
    assert 'id="agents-content"' in agents_section
    assert 'id="agent-detail-content"' in agents_section
    assert agents_section.index('id="agents-content"') < agents_section.index(
        'id="agent-detail-content"'
    )


def test_script_defines_agent_detail_functions() -> None:
    script = ui._OBSERVE_SCRIPT
    for name in (
        "function agentKeyFor(agent)",
        "function buildAgentDetailButton(agent)",
        "function agentDetailPanel()",
        "function setAgentDetailStatus(text)",
        "function boundTrendPoints(points)",
        "function renderAgentTrendsNode(trends)",
        "function titleCaseFromKey(key)",
        "function isSafePortalUrl(url)",
        "function renderPortalLinksNode(links)",
        "function agentDetailFrom(body)",
        "function renderAgentDetail(agentKey, body)",
        "function fetchAgentDetail(agentKey, sourceId, projectResourceId, manual)",
    ):
        assert name in script


def test_script_renders_agent_details_action_below_name_without_extra_column() -> None:
    script = ui._OBSERVE_SCRIPT
    fn_block = script.split("function renderAgents(data, diagnostics, bounds) {")[1].split(
        "\n  }\n"
    )[0]
    assert '"Details"' not in fn_block
    assert "primaryCellWithAction(agent.agent_name, [" in fn_block
    assert "buildAgentDetailButton(agent)" in fn_block
    assert 'buildDrilldownButton(\n            "agents"' in fn_block
    assert '"View activity"' in fn_block


def test_script_agent_key_for_prefers_key_then_falls_back() -> None:
    script = ui._OBSERVE_SCRIPT
    fn_block = script.split("function agentKeyFor(agent) {")[1].split("\n  }\n")[0]
    assert "agent.key || agent.agent_id || agent.agent_name" in fn_block


def test_script_build_agent_detail_button_disables_when_no_key_resolves() -> None:
    script = ui._OBSERVE_SCRIPT
    fn_block = script.split("function buildAgentDetailButton(agent) {")[1].split(
        "\n  }\n"
    )[0]
    assert 'button.setAttribute("data-observe-agent-key", key);' in fn_block
    assert 'button.setAttribute("data-observe-source-id", agent.source_id || "");' in fn_block
    assert (
        'button.setAttribute("data-observe-project-resource-id", '
        'agent.project_resource_id || "");'
    ) in fn_block
    assert "button.disabled = true;" in fn_block
    assert "agent.project_resource_id || \"\"" in fn_block


def test_script_agent_detail_uses_independent_abort_state_from_main_query() -> None:
    # The agent-detail panel must never abort (or be aborted by) the main
    # view's fetchObserveData -- each has its own token/controller pair.
    script = ui._OBSERVE_SCRIPT
    assert "var agentDetailToken = 0;" in script
    assert "var agentDetailController = null;" in script
    fn_block = script.split(
        "function fetchAgentDetail(agentKey, sourceId, projectResourceId, manual) {"
    )[1].split(
        "\n  }\n"
    )[0]
    assert "requestToken" not in fn_block
    assert "activeController" not in fn_block
    assert "var myToken = ++agentDetailToken;" in fn_block
    assert "if (agentDetailController) {" in fn_block
    assert "agentDetailController.abort();" in fn_block
    assert "var controller = new AbortController();" in fn_block
    assert "agentDetailController = controller;" in fn_block
    assert "signal: controller.signal," in fn_block


def test_script_fetch_agent_detail_posts_json_with_agent_key_filters_and_refresh() -> None:
    script = ui._OBSERVE_SCRIPT
    fn_block = script.split(
        "function fetchAgentDetail(agentKey, sourceId, projectResourceId, manual) {"
    )[1].split(
        "\n  }\n"
    )[0]
    assert 'fetch("/api/observe/agent-detail", {' in fn_block
    assert 'method: "POST",' in fn_block
    assert '"Content-Type": "application/json"' in fn_block
    assert "body: JSON.stringify(agentDetailPayload)," in fn_block
    assert "agent_key: agentKey," in fn_block
    assert "source_id: sourceId || null," in fn_block
    assert "project_resource_id: projectResourceId || null," in fn_block
    assert "refresh: manual === true," in fn_block
    # The request must only ever carry the stable identifier and the
    # currently-applied filters -- never a raw-content field.
    for forbidden in ("input_messages", "output_messages", "system_instructions"):
        assert forbidden not in fn_block


def test_script_fetch_agent_detail_suppresses_stale_response_and_handles_not_found() -> None:
    script = ui._OBSERVE_SCRIPT
    fn_block = script.split(
        "function fetchAgentDetail(agentKey, sourceId, projectResourceId, manual) {"
    )[1].split(
        "\n  }\n\n  function renderUsage"
    )[0]
    assert fn_block.count("myToken !== agentDetailToken") == 2
    assert "if (response.status === 404) {" in fn_block
    assert 'setAgentDetailStatus("Agent not found for the selected filters.");' in fn_block
    assert "if (!response.ok) {" in fn_block
    assert 'setAgentDetailStatus("Agent detail failed to load.");' in fn_block
    assert 'error.name === "AbortError"' in fn_block
    assert "renderAgentDetail(agentKey, body);" in fn_block


def test_script_bound_trend_points_caps_length_and_keeps_first_and_last() -> None:
    script = ui._OBSERVE_SCRIPT
    fn_block = script.split("function boundTrendPoints(points) {")[1].split("\n  }\n")[0]
    assert "items.length <= MAX_TREND_POINTS" in fn_block
    assert "bounded[bounded.length - 1] = items[items.length - 1];" in fn_block


def test_script_render_agent_trends_uses_data_table_not_svg_and_shows_exact_values() -> None:
    # Trend series render as an accessible data table via the existing
    # buildDataTable helper -- no separate SVG chart engine is introduced
    # for the agent-detail panel.
    script = ui._OBSERVE_SCRIPT
    fn_block = script.split("function renderAgentTrendsNode(trends) {")[1].split(
        "\n  }\n"
    )[0]
    assert 'buildDataTable(\n            "observe-chart-data"' in fn_block or (
        "buildDataTable(" in fn_block
    )
    assert '["Series", "Point", "Value"]' in fn_block
    assert "boundTrendPoints(series.points)" in fn_block
    assert "No data found for this chart." in fn_block


def test_script_portal_links_only_render_http_s_urls_and_open_in_new_tab_safely() -> None:
    script = ui._OBSERVE_SCRIPT
    safe_fn = script.split("function isSafePortalUrl(url) {")[1].split("\n  }\n")[0]
    assert '"^https?://"' in safe_fn
    fn_block = script.split("function renderPortalLinksNode(links) {")[1].split(
        "\n  }\n"
    )[0]
    assert "isSafePortalUrl(url)" in fn_block
    assert 'anchor.target = "_blank";' in fn_block
    assert 'anchor.rel = "noopener noreferrer";' in fn_block
    assert 'anchor.setAttribute("data-observe-portal-link", key);' in fn_block
    assert "(opens in a new tab)" in fn_block
    assert "return rendered ? list : null;" in fn_block


def test_script_known_portal_labels_mirror_python_labels() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "var KNOWN_PORTAL_LABELS = {" in script
    labels_block = script.split("var KNOWN_PORTAL_LABELS = {")[1].split("};")[0]
    for key, label in ui._KNOWN_PORTAL_LABELS.items():
        assert f"{key}:" in labels_block
        assert f'"{label}"' in labels_block


def test_script_max_trend_points_matches_python_constant() -> None:
    script = ui._OBSERVE_SCRIPT
    assert f"var MAX_TREND_POINTS = {ui.MAX_TREND_POINTS};" in script


def test_script_agent_detail_never_touches_url_sync_or_filter_keys() -> None:
    # Agent-detail state (the selected agent key, trends, portal links) must
    # never be persisted to the URL or browser storage -- it lives purely in
    # memory/DOM for the lifetime of the page.
    script = ui._OBSERVE_SCRIPT
    sync_fn = script.split("function syncUrl() {")[1].split("\n  }\n")[0]
    assert "agent" not in sync_fn.lower()
    filter_keys_line = [line for line in script.splitlines() if "var FILTER_KEYS" in line][0]
    assert "agent_key" not in filter_keys_line
    assert "localStorage" not in script.split("function fetchAgentDetail")[1].split(
        "function renderUsage"
    )[0]
    assert "sessionStorage" not in script.split("function fetchAgentDetail")[1].split(
        "function renderUsage"
    )[0]


def test_script_render_agent_detail_uses_safe_dom_construction_only() -> None:
    script = ui._OBSERVE_SCRIPT
    fn_block = script.split("function renderAgentDetail(agentKey, body) {")[1].split(
        "\n  }\n"
    )[0]
    assert "clearChildren(panel);" in fn_block
    assert "makeEl(" in fn_block
    assert "innerHTML" not in fn_block
    assert "renderAgentSummaryNode(agent)" in fn_block

    summary_block = script.split("function renderAgentSummaryNode(agent) {")[1].split(
        "\n  }\n"
    )[0]
    assert "renderLastSeenJs(agent.last_seen)" in summary_block
    assert "renderSourceKindBadge(agent.source_kind)" in summary_block
    assert "renderMillisecondsAsSeconds(agent.p95_latency_ms)" in summary_block


def test_script_agent_detail_extracts_first_agent_from_serialized_result_data() -> None:
    script = ui._OBSERVE_SCRIPT
    fn_block = script.split("function agentDetailFrom(body) {")[1].split("\n  }\n")[0]
    assert "Array.isArray(body.data) ? body.data[0] : body.data" in fn_block
    assert "agent: body.agent || dataAgent || {}" in fn_block


def test_script_places_view_details_below_primary_identifier_in_every_table() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "function primaryCellWithAction(value, action)" in script
    assert "primaryCellWithAction(agent.agent_name, [" in script
    assert "buildAgentDetailButton(agent)" in script
    for function_name, primary_value in (
        ("renderUsage", "entry.model"),
        ("renderTools", "tool.tool_name"),
    ):
        fn_block = script.split(f"function {function_name}(")[1].split("\n  }\n")[0]
        assert "primaryCellWithAction(" in fn_block
        assert primary_value in fn_block
    runs_block = script.split("function renderRuns(")[1].split("\n  }\n")[0]
    assert "detail: runDetailNode(run)" in runs_block
    assert "abbreviateRunIdentifier(run.run_key)" in runs_block
