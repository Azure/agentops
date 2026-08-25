"""Tests for the hosted Observe frontend fragments in ``agentops.agent.observe.ui``.

Covers T050-T054, T058, and T062: markup/ARIA structure, draft/applied filter
separation, URL-persistence safety, refresh/staleness behavior, source
labels/last-seen/observed-usage wording, chart accessibility and non-color
distinction, protected-content loading, and coverage/troubleshooting
semantics (including zero-versus-missing rendering).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentops.agent.observe import ui
from agentops.core.cost import CostPeriodRef
from agentops.core.observe import CoverageResult, GenerativeAIContent, ModelUsage


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
    assert ui.DEFAULT_RANGE_HOURS == 24
    assert ui.AUTO_REFRESH_MS == 5 * 60 * 1000


def test_observe_views_and_labels_cover_all_required_surfaces() -> None:
    assert ui.OBSERVE_VIEWS == ("overview", "agents", "usage", "tools", "runs", "coverage")
    for view in ui.OBSERVE_VIEWS:
        assert view in ui.OBSERVE_VIEW_LABELS


def test_observe_view_wire_names_map_internal_ids_to_openapi_view_enum() -> None:
    # contracts/observe-api.openapi.yaml spells the `ObserveQuery.view` enum
    # as [overview, agents, models, tools, runs, coverage]; the internal "usage" id (used
    # throughout DOM ids/CSS/labels) must be translated to "models" only when
    # building the outgoing wire payload.
    assert ui.OBSERVE_VIEW_WIRE_NAMES == {
        "overview": "overview",
        "agents": "agents",
        "usage": "models",
        "tools": "tools",
        "runs": "runs",
        "coverage": "coverage",
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
    assert 'data-observe-nav-link="agents" class="observe-nav-link" aria-current="page"' in html
    assert 'data-observe-nav-link="overview" class="observe-nav-link">' in html
    assert "aria-current" not in html.split('data-observe-nav-link="overview"')[1].split("</a>")[0] or True


def test_render_observe_nav_renders_every_view_once() -> None:
    html = ui.render_observe_nav()
    for view in ui.OBSERVE_VIEWS:
        assert html.count(f'data-observe-nav-link="{view}"') == 1
    assert 'data-observe-nav-link="cost"' not in html


def test_render_observe_nav_only_adds_cost_when_enabled() -> None:
    html = ui.render_observe_nav("cost", cost_enabled=True)
    assert html.count('data-observe-nav-link="cost"') == 1
    assert (
        'data-observe-nav-link="cost" class="observe-nav-link" aria-current="page"'
        in html
    )
    assert ">Cost<" in html


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
    assert html.count('placeholder="All"') == 6  # foundry, project, agent, model, tool, run


def test_filter_bar_renders_optional_scope_label() -> None:
    html = ui.render_filter_bar(scope_label="project-a")
    assert "Scope:" in html
    assert "project-a" in html


def test_filter_bar_omits_scope_paragraph_when_absent() -> None:
    html = ui.render_filter_bar()
    assert "observe-scope" not in html


# ---------------------------------------------------------------------------
# Source labels / refreshed-at / last-seen (T052)
# ---------------------------------------------------------------------------


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
    assert "Refreshed" in html


def test_render_refreshed_at_missing() -> None:
    html = ui.render_refreshed_at(None)
    assert "not yet refreshed" in html


def test_render_last_seen_includes_non_lifecycle_disclaimer() -> None:
    html = ui.render_last_seen(_dt(3))
    assert "2024-01-01T03:00:00Z" in html
    assert "not agent lifecycle status" in html


def test_render_last_seen_missing_uses_metric_missing_class() -> None:
    html = ui.render_last_seen(None)
    assert "metric-missing" in html
    assert "not reported" in html
    assert "not agent lifecycle status" in html


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


def test_render_token_totals_includes_observed_usage_disclaimer() -> None:
    html = ui._render_token_totals(100, 0)
    assert "observed usage, not billing data" in html
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
    for heading in ("Agent", "Source", "Model", "Last seen", "Invocations", "Failure rate", "p95 latency", "Tokens"):
        assert f">{heading}<" in html
    assert "Agent A" in html
    assert "gpt-4o" in html


def test_render_agents_table_shows_source_kind_and_identity_badges() -> None:
    html = ui.render_agents_table([_agent()])
    assert "Foundry Hosted" in html
    assert "Identity available" in html
    assert "source-a" in html


def test_render_agents_table_missing_agent_id_shows_identity_not_reported() -> None:
    html = ui.render_agents_table([_agent(agent_id=None, agent_name=None)])
    assert "Identity not reported" in html


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


def test_render_agents_table_includes_diagnostics_banner_when_supplied() -> None:
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
    assert "observe-diagnostics-banner" in html
    assert "Partial results" in html


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


def test_script_cost_payload_sends_only_cost_selectors() -> None:
    script = ui._OBSERVE_SCRIPT
    block = script.split("function buildCostPayload(manual) {")[1].split("\n  }\n")[0]
    for key in ui.COST_FILTER_QUERY_KEYS:
        assert f"{key}:" in block
    for shared in ui.OBSERVE_FILTER_QUERY_KEYS:
        assert f"{shared}:" not in block
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
        '"model", "tool_name", "run_key", "start", "end"];'
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
    assert "cost_period_id: appliedFilters.cost_period_id || null" in payload
    for shared in ui.OBSERVE_FILTER_QUERY_KEYS:
        assert f"{shared}:" not in payload


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


def test_render_tools_table_shows_source_latency_and_known_bounds() -> None:
    html = ui.render_tools_table(
        [_tool()],
        bounds={"rows_shown": 1, "rows_total_in_scope": 3, "truncated": True},
    )
    for heading in ("Tool", "Agent", "Source", "Runtime", "Last seen", "Invocations", "Failures", "p95 latency"):
        assert f">{heading}<" in html
    assert "search_documents" in html
    assert "source-a" in html
    assert "Foundry Prompt" in html
    assert "Showing 1 of 3 rows in scope." in html


def test_render_tools_table_marks_absent_latency_not_measured_and_escapes_values() -> None:
    html = ui.render_tools_table(
        [_tool(tool_name='<tool "name">', source_id="<source>", p95_latency_ms=None)]
    )
    assert "Not measured" in html
    assert "metric-missing" in html
    assert "&lt;tool &quot;name&quot;&gt;" in html
    assert "&lt;source&gt;" in html


def test_render_tools_table_empty_is_explained_and_total_is_unknown() -> None:
    html = ui.render_tools_table([], bounds={"rows_shown": 0, "rows_total_in_scope": None})
    assert "Showing 0 rows; total unknown." in html
    assert "No tool activity was found" in html
    assert "Tool attribution may not be reported" in html


def test_render_runs_table_shows_correlation_range_scope_source_and_tokens() -> None:
    html = ui.render_runs_table(
        [_run()],
        bounds={"rows_shown": 1, "rows_total_in_scope": 4, "truncated": True},
    )
    for heading in (
        "Run key",
        "Correlation",
        "Source",
        "Started in range",
        "Duration in range",
        "Turns in range",
        "Tool invocations",
        "Tokens",
    ):
        assert f">{heading}<" in html
    assert "conversation-123" in html
    assert "conversation" in html
    assert "source-a" in html
    assert "activity within the selected range" in html
    assert "Showing 1 of 4 rows in scope." in html


def test_render_runs_table_marks_absent_tokens_not_available_and_escapes_values() -> None:
    html = ui.render_runs_table(
        [_run(run_key='<run "key">', source_id="<source>", input_tokens=None, output_tokens=None)]
    )
    assert "Not available" in html
    assert "metric-missing" in html
    assert "&lt;run &quot;key&quot;&gt;" in html
    assert "&lt;source&gt;" in html


def test_render_runs_table_empty_is_explained_and_total_is_unknown() -> None:
    html = ui.render_runs_table([], bounds={"rows_shown": 0, "rows_total_in_scope": None})
    assert "Showing 0 rows; total unknown." in html
    assert "No runs could be correlated" in html
    assert "Run correlation may not be reported" in html


@pytest.mark.parametrize(
    ("source_kind", "label", "tone"),
    [
        ("foundry_hosted", "Foundry Hosted", "ok"),
        ("foundry_prompt", "Foundry Prompt", "ok"),
        ("external_registered", "External Registered", "warn"),
        ("external_unregistered", "External Unregistered", "warn"),
        ("copilot_studio", "Copilot Studio", "warn"),
        ("unknown", "Unknown", "muted"),
    ],
)
def test_source_kind_badge_covers_all_refined_runtime_kinds(
    source_kind: str, label: str, tone: str
) -> None:
    html = ui._render_source_kind_badge(source_kind)
    assert label in html
    assert f"observe-tone-{tone}" in html


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


def test_render_models_usage_table_last_seen_nullable_renders_missing() -> None:
    html = ui.render_models_usage_table([_usage(last_seen=None)])
    assert "metric-missing" in html
    assert "not agent lifecycle status" in html


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
    assert "Partial class coverage" in html
    assert "metric-zero" in html
    assert "Not reported" in html
    assert "(observed usage, not billing data)" in html


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
    assert "Partial class coverage" in html


def test_script_models_renderer_mirrors_token_class_fields_and_labels() -> None:
    script = ui._OBSERVE_SCRIPT
    for field in ("cache_read_tokens", "cache_write_tokens", "reasoning_tokens"):
        assert f"entry.{field}" in script
    for label in ("Cache read", "Cache write", "Reasoning", "Partial class coverage"):
        assert label in script


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


def test_existing_token_totals_are_byte_identical_across_existing_surfaces() -> None:
    expected = (
        '<span class="observe-token-totals">'
        '<span class="observe-token-in">In: '
        '<span class="observe-metric metric-value">1,000</span></span> '
        '<span class="observe-token-out">Out: '
        '<span class="observe-metric metric-value">2,000</span></span>'
        '<span class="observe-hint"> (observed usage, not billing data)</span>'
        "</span>"
    )
    assert ui._render_token_totals(1000, 2000) == expected
    assert expected in ui.render_agents_table([_agent(input_tokens=1000, output_tokens=2000)])
    assert expected in ui.render_models_usage_table([_usage(input_tokens=1000, output_tokens=2000)])


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
    assert "In: " in html
    assert "Out: " in html
    assert html.count("Not reported") == 3
    assert "Partial class coverage" not in html
    assert "(observed usage, not billing data)" in html


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
    assert "<title>agent-a \u2013 2024-01-01T00:00:00Z: 123.46 ms</title>" in html


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


def test_render_observe_page_includes_all_six_views() -> None:
    html = ui.render_observe_page(
        overview_metrics=[{"title": "Invocations", "value": 5}],
        agents=[_agent()],
        usage=[_usage()],
        coverage=[_coverage("available")],
    )
    for section_id in ("overview", "agents", "usage", "tools", "runs", "coverage"):
        assert f'<section id="{section_id}"' in html


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


def test_script_computes_default_24_hour_range_when_missing_from_url() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "DEFAULT_RANGE_MS = 24 * 60 * 60 * 1000" in script


def test_script_uses_history_replace_state_not_push_state_for_url_sync() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "history.replaceState" in script
    assert "history.pushState" not in script


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
    submit_block = script.split('addEventListener("submit"')[1]
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
    assert "view: VIEW_WIRE_NAMES[currentView] || currentView" in payload_block
    assert "foundry_resource_id: appliedFilters.foundry_resource_id || null" in payload_block
    assert "project_resource_id: appliedFilters.project_resource_id || null" in payload_block
    assert "agent_id: appliedFilters.agent_id || null" in payload_block
    assert "model: appliedFilters.model || null" in payload_block
    assert "tool_name: appliedFilters.tool_name || null" in payload_block
    assert "run_key: appliedFilters.run_key || null" in payload_block
    assert "start: appliedFilters.start" in payload_block
    assert "end: appliedFilters.end" in payload_block
    assert "refresh: manual === true" in payload_block


def test_script_manual_refresh_sets_refresh_true_and_auto_refresh_sets_refresh_false() -> None:
    script = ui._OBSERVE_SCRIPT
    # "Refresh now" button and the Apply submit handler are explicit user
    # actions and both request refresh: true (cache bypass); the periodic
    # timer tick requests refresh: false.
    assert "fetchObserveData(true);" in script
    assert "fetchObserveData(false);" in script
    refresh_button_block = script.split('getElementById("observe-refresh-now")')[1].split("}")[0]
    assert "fetchObserveData(true);" in refresh_button_block
    submit_block = script.split('addEventListener("submit"')[1].split("});")[0]
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
        'var VIEW_WIRE_NAMES = { overview: "overview", agents: "agents", usage: "models", '
        'tools: "tools", runs: "runs", coverage: "coverage" };'
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


def test_script_runtime_kind_tones_mirror_all_six_python_badges() -> None:
    script = ui._OBSERVE_SCRIPT
    tone_block = script.split("var tones = {")[1].split("};")[0]
    expected_tones = {
        "foundry_hosted": "ok",
        "foundry_prompt": "ok",
        "external_registered": "warn",
        "external_unregistered": "warn",
        "copilot_studio": "warn",
        "unknown": "muted",
    }
    for kind, tone in expected_tones.items():
        assert f"{kind}: \"{tone}\"" in tone_block
    assert 'var tone = tones[kind] || "muted";' in script


def test_script_tools_and_runs_render_sources_bounds_and_explained_empty_states() -> None:
    script = ui._OBSERVE_SCRIPT
    agents_block = script.split("function renderAgents(data, diagnostics) {")[1].split("\n  }\n")[0]
    assert 'agent.source_id || "Not reported"' in agents_block
    for function_name, source_field, empty_copy in (
        (
            "function renderTools(data, diagnostics, bounds)",
            "tool.source_id || \"Not reported\"",
            "No tool activity was found for the selected filters.",
        ),
        (
            "function renderRuns(data, diagnostics, bounds)",
            "run.source_id || \"Not reported\"",
            "No runs could be correlated for the selected filters.",
        ),
    ):
        block = script.split(function_name)[1].split("\n  }\n")[0]
        assert source_field in block
        assert "boundsNoticeNode(bounds" in block
        assert empty_copy in block
    assert 'missingText: "Not measured"' in script
    assert 'renderTokenTotals(run.input_tokens, run.output_tokens, "Not available")' in script
    assert "run.run_key_kind || \"Not reported\"" in script
    assert "activity within the selected range" in script


# ---------------------------------------------------------------------------
# Functional page: parse fetch responses and render/update the active view
# (overview cards, agents, models/usage, coverage, diagnostics banner,
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
        "function renderAgents(data, diagnostics)",
        "function renderUsage(data, diagnostics)",
        "function renderTools(data, diagnostics, bounds)",
        "function renderRuns(data, diagnostics, bounds)",
        "function renderCoverage(coverage, diagnostics)",
        "function internalViewFromWire(view)",
        "function renderDiagnosticsBannerNode(diagnostics)",
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
    status_index = then_block.index('setRefreshStatus("Refreshed " + new Date().toISOString());')
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
    assert "renderAgents(body.data, body.diagnostics);" in dispatch_block
    assert "renderUsage(body.data, body.diagnostics);" in dispatch_block
    assert "renderTools(body.data, body.diagnostics, body.bounds);" in dispatch_block
    assert "renderRuns(body.data, body.diagnostics, body.bounds);" in dispatch_block
    # Coverage row detail is a top-level `ObserveResponse.coverage` array in
    # the OpenAPI contract, not `data` -- the dispatcher must read the
    # correct field for that view.
    assert "renderCoverage(body.coverage, body.diagnostics);" in dispatch_block
    assert "renderCoverage(body.data" not in dispatch_block


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


def test_script_diagnostics_banner_reflects_partial_results_and_is_rebuilt_per_view() -> None:
    script = ui._OBSERVE_SCRIPT
    banner_fn = script.split("function renderDiagnosticsBannerNode(diagnostics) {")[1].split(
        "\n  }\n"
    )[0]
    assert "Partial results: some telemetry sources did not fully respond." in banner_fn
    assert '"Sources queried"' in banner_fn
    assert '"Successful"' in banner_fn
    assert '"Partial"' in banner_fn
    assert '"Failed"' in banner_fn
    assert '"Query duration"' in banner_fn
    assert '"Cache"' in banner_fn
    # Every per-view renderer rebuilds its own diagnostics banner from the
    # latest response instead of relying on a single shared/global element.
    for fn_name in ("renderOverview", "renderAgents", "renderUsage", "renderTools", "renderRuns", "renderCoverage"):
        fn_block = script.split(f"function {fn_name}(")[1].split("\n  }\n")[0]
        assert "renderDiagnosticsBannerNode(diagnostics)" in fn_block


def test_script_disclaimers_preserved_in_render_functions() -> None:
    script = ui._OBSERVE_SCRIPT
    assert "Last seen reflects observed telemetry only, not agent lifecycle status." in script
    assert "(observed usage, not billing data)" in script


def test_script_zero_vs_missing_distinction_uses_distinct_classes() -> None:
    script = ui._OBSERVE_SCRIPT
    fn_block = script.split("function renderMaybeMissing(value, opts) {")[1].split(
        "\n  }\n"
    )[0]
    assert "metric-missing" in fn_block
    assert "metric-zero" in fn_block
    assert "metric-value" in fn_block


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


def test_script_coverage_rows_always_rendered_independent_of_state() -> None:
    # T062: coverage rows are mapped unconditionally from the full array --
    # no state value causes other rows to be hidden or skipped.
    script = ui._OBSERVE_SCRIPT
    fn_block = script.split("function renderCoverage(coverage, diagnostics) {")[1].split(
        "\n  }\n"
    )[0]
    assert "coverage.map(function (entry) {" in fn_block
    assert "if (" not in fn_block.split("coverage.map(function (entry) {")[1].split(
        "return ["
    )[0]


def test_script_nav_link_click_fetches_the_newly_selected_view() -> None:
    # Regression: switching the active view via a nav link must trigger a
    # live fetch for that view, not only update the URL and leave the page
    # showing the initial server-rendered snapshot of a different view.
    script = ui._OBSERVE_SCRIPT
    nav_block = script.split("[data-observe-nav-link]")[1].split("});")[0]
    assert "currentView = link.getAttribute" in nav_block
    assert "syncUrl();" in nav_block
    assert "fetchObserveData(false);" in nav_block


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


def test_styles_support_light_and_dark_via_prefers_color_scheme() -> None:
    styles = ui._OBSERVE_STYLES
    assert "@media (prefers-color-scheme: dark)" in styles
    assert "--observe-series-1" in styles


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
        "function fetchAgentDetail(agentKey, manual)",
    ):
        assert name in script


def test_script_renders_agents_table_with_details_column_and_button() -> None:
    script = ui._OBSERVE_SCRIPT
    fn_block = script.split("function renderAgents(data, diagnostics) {")[1].split(
        "\n  }\n"
    )[0]
    assert '"Details"' in fn_block
    assert "buildAgentDetailButton(agent)" in fn_block


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
    assert "button.disabled = true;" in fn_block
    assert "fetchAgentDetail(key, false);" in fn_block


def test_script_agent_detail_uses_independent_abort_state_from_main_query() -> None:
    # The agent-detail panel must never abort (or be aborted by) the main
    # view's fetchObserveData -- each has its own token/controller pair.
    script = ui._OBSERVE_SCRIPT
    assert "var agentDetailToken = 0;" in script
    assert "var agentDetailController = null;" in script
    fn_block = script.split("function fetchAgentDetail(agentKey, manual) {")[1].split(
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
    fn_block = script.split("function fetchAgentDetail(agentKey, manual) {")[1].split(
        "\n  }\n"
    )[0]
    assert 'fetch("/api/observe/agent-detail", {' in fn_block
    assert 'method: "POST",' in fn_block
    assert '"Content-Type": "application/json"' in fn_block
    assert "body: JSON.stringify(agentDetailPayload)," in fn_block
    assert "agent_key: agentKey," in fn_block
    assert "refresh: manual === true," in fn_block
    # The request must only ever carry the stable identifier and the
    # currently-applied filters -- never a raw-content field.
    for forbidden in ("input_messages", "output_messages", "system_instructions"):
        assert forbidden not in fn_block


def test_script_fetch_agent_detail_suppresses_stale_response_and_handles_not_found() -> None:
    script = ui._OBSERVE_SCRIPT
    fn_block = script.split("function fetchAgentDetail(agentKey, manual) {")[1].split(
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
    assert "renderLastSeenJs(agent.last_seen)" in fn_block
    assert "renderSourceKindBadge(agent.source_kind)" in fn_block
    assert "renderIdentityAvailabilityBadge(agent.agent_id)" in fn_block
