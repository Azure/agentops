"""Tests for deterministic billed-cost allocation at every supported grain."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from agentops.agent.observe.cost_allocation import allocate_cost_period
from agentops.core.cost import CostPeriod, CostUsageObservation


SOURCE = (
    "/subscriptions/11111111-1111-1111-1111-111111111111/"
    "resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/ws"
)
PROJECT = (
    "/subscriptions/11111111-1111-1111-1111-111111111111/"
    "resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/foundry/"
    "projects/project-a"
)
CALCULATED_AT = datetime(2026, 9, 2, tzinfo=timezone.utc)


def _component(**changes: object) -> dict[str, object]:
    component: dict[str, object] = {
        "id": "model-prod",
        "type": "standard_model",
        "billing_boundary": {"kind": "resource", "value": PROJECT},
        "billed_source": "August model billed total",
        "billed_total": "100.00",
        "currency": "USD",
        "currency_minor_units": 2,
        "allocation_model": "metered",
        "allocation_key": "weighted_tokens",
        "fallback_key": "total_tokens",
        "token_weights": {
            "input_tokens": "1",
            "output_tokens": "4",
            "cache_read_tokens": "0.25",
        },
        "usage_match": {
            "source_resource_ids": [SOURCE],
            "deployments": ["gpt-prod"],
        },
    }
    component.update(changes)
    return component


def _period(*components: dict[str, object]) -> CostPeriod:
    return CostPeriod.model_validate(
        {
            "id": "2026-08",
            "starts_at": "2026-08-01T00:00:00Z",
            "ends_at": "2026-09-01T00:00:00Z",
            "components": list(components) or [_component()],
        }
    )


def _observation(agent_key: str | None, **changes: object) -> CostUsageObservation:
    observation: dict[str, object] = {
        "source_resource_id": SOURCE,
        "project_resource_id": PROJECT,
        "agent_key": agent_key,
        "runtime_kind": "foundry_hosted",
        "deployment": "gpt-prod",
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "latest_observed_at": "2026-08-24T12:00:00Z",
        "coverage_complete": True,
    }
    observation.update(changes)
    return CostUsageObservation.model_validate(observation)


def _amounts(result: object) -> dict[str, Decimal]:
    return {row.consumer_key: row.amount for row in result.rows}


def test_weighted_tokens_allocate_by_agent_and_usage_match_narrows() -> None:
    observations = [
        _observation("agent-a", input_tokens=100, output_tokens=0),
        _observation("agent-b", input_tokens=0, output_tokens=100),
        _observation(
            "outside-component",
            deployment="other-deployment",
            input_tokens=100_000,
            output_tokens=100_000,
        ),
    ]

    result = allocate_cost_period(
        _period(),
        observations,
        calculated_at=CALCULATED_AT,
    )

    assert _amounts(result) == {
        "agent-b": Decimal("80.00"),
        "agent-a": Decimal("20.00"),
    }
    assert [row.usage_numerator for row in result.rows] == [
        Decimal("400"),
        Decimal("100"),
    ]
    assert all(row.usage_denominator == Decimal("500") for row in result.rows)
    assert all(row.applied_key == "weighted_tokens" for row in result.rows)
    assert all(row.confidence == "high" for row in result.rows)
    assert result.components[0].declared_total == Decimal("100.00")
    assert result.components[0].attributed_amount == Decimal("100.00")
    assert result.components[0].unallocated_amount == Decimal("0.00")
    assert result.latest_observed_at == datetime(
        2026, 8, 24, 12, tzinfo=timezone.utc
    )


def test_total_tokens_and_largest_remainder_use_consumer_key_tie_break() -> None:
    component = _component(
        billed_total="0.01",
        allocation_key="total_tokens",
        fallback_key=None,
        token_weights=None,
    )
    observations = [
        _observation("agent-c", input_tokens=1),
        _observation("agent-a", input_tokens=1),
        _observation("agent-b", input_tokens=1),
    ]

    first = allocate_cost_period(
        _period(component), observations, calculated_at=CALCULATED_AT
    )
    second = allocate_cost_period(
        _period(component),
        list(reversed(observations)),
        calculated_at=CALCULATED_AT,
    )

    assert _amounts(first) == {
        "agent-a": Decimal("0.01"),
        "agent-b": Decimal("0.00"),
        "agent-c": Decimal("0.00"),
    }
    assert _amounts(second) == _amounts(first)
    adjustment = {row.consumer_key: row.rounding_adjustment_minor_units for row in first.rows}
    assert adjustment == {"agent-a": 1, "agent-b": 0, "agent-c": 0}
    assert sum((row.amount for row in first.rows), Decimal(0)) == Decimal("0.01")


@pytest.mark.parametrize(
    ("component", "observations", "expected"),
    [
        (
            _component(
                id="search-prod",
                type="search",
                billed_total="30.00",
                allocation_key="tool_invocations",
                fallback_key=None,
                token_weights=None,
                usage_match={"tool_names": ["product_search"]},
            ),
            [
                _observation(
                    "agent-a", tool_name="product_search", tool_invocations=1
                ),
                _observation(
                    "agent-b", tool_name="product_search", tool_invocations=2
                ),
            ],
            {"agent-a": Decimal("10.00"), "agent-b": Decimal("20.00")},
        ),
        (
            _component(
                id="hosted-compute",
                type="hosted_compute",
                billed_total="45.00",
                allocation_key="active_session_seconds",
                fallback_key=None,
                token_weights=None,
                usage_match={"runtime_kinds": ["foundry_hosted"]},
            ),
            [
                _observation("agent-a", active_session_seconds="30"),
                _observation("agent-b", active_session_seconds="60"),
            ],
            {"agent-a": Decimal("15.00"), "agent-b": Decimal("30.00")},
        ),
    ],
)
def test_invocations_and_duration_allocate_by_declared_observed_key(
    component: dict[str, object],
    observations: list[CostUsageObservation],
    expected: dict[str, Decimal],
) -> None:
    result = allocate_cost_period(
        _period(component), observations, calculated_at=CALCULATED_AT
    )
    assert _amounts(result) == expected
    assert result.components[0].confidence == "high"


def test_direct_credits_take_precedence_over_explicit_event_fallback() -> None:
    component = _component(
        id="credit-pool",
        type="credit_prepaid",
        billed_total="90.00",
        allocation_model="commitment",
        allocation_key="credits",
        fallback_key="credit_events",
        token_weights=None,
        usage_match={
            "runtime_kinds": ["copilot_studio"],
            "credit_event_operations": ["InvokeAgent"],
        },
    )
    observations = [
        _observation(
            "agent-a",
            runtime_kind="copilot_studio",
            credits="1",
            credit_events=100,
            operation_name="InvokeAgent",
        ),
        _observation(
            "agent-b",
            runtime_kind="copilot_studio",
            credits="2",
            credit_events=1,
            operation_name="InvokeAgent",
        ),
    ]

    result = allocate_cost_period(
        _period(component), observations, calculated_at=CALCULATED_AT
    )

    assert _amounts(result) == {
        "agent-b": Decimal("60.00"),
        "agent-a": Decimal("30.00"),
    }
    assert all(row.applied_key == "credits" for row in result.rows)
    assert all(not row.fallback_used for row in result.rows)
    assert result.components[0].confidence == "high"


def test_credit_event_fallback_is_explicit_and_operation_scoped() -> None:
    component = _component(
        id="credit-pool",
        type="credit_prepaid",
        billed_total="40.00",
        allocation_model="commitment",
        allocation_key="credits",
        fallback_key="credit_events",
        token_weights=None,
        usage_match={
            "runtime_kinds": ["copilot_studio"],
            "credit_event_operations": ["InvokeAgent"],
        },
    )
    observations = [
        _observation(
            "agent-a",
            runtime_kind="copilot_studio",
            credits=None,
            credit_events=1,
            operation_name="InvokeAgent",
        ),
        _observation(
            "agent-b",
            runtime_kind="copilot_studio",
            credits=None,
            credit_events=3,
            operation_name="InvokeAgent",
        ),
        _observation(
            "agent-c",
            runtime_kind="copilot_studio",
            credits=None,
            credit_events=100,
            operation_name="ExecuteTool",
        ),
    ]

    result = allocate_cost_period(
        _period(component), observations, calculated_at=CALCULATED_AT
    )

    assert _amounts(result) == {
        "agent-b": Decimal("30.00"),
        "agent-a": Decimal("10.00"),
    }
    assert all(row.applied_key == "credit_events" for row in result.rows)
    assert all(row.fallback_used for row in result.rows)
    assert all(row.confidence == "medium" for row in result.rows)
    assert result.components[0].confidence == "medium"


def test_weighted_tokens_fall_back_only_when_required_dimension_is_missing() -> None:
    observations = [
        _observation(
            "agent-a",
            input_tokens=1,
            output_tokens=3,
            cache_read_tokens=None,
        ),
        _observation(
            "agent-b",
            input_tokens=3,
            output_tokens=1,
            cache_read_tokens=None,
        ),
    ]

    result = allocate_cost_period(
        _period(), observations, calculated_at=CALCULATED_AT
    )

    assert _amounts(result) == {
        "agent-a": Decimal("50.00"),
        "agent-b": Decimal("50.00"),
    }
    assert all(row.applied_key == "total_tokens" for row in result.rows)
    assert all(row.fallback_used for row in result.rows)
    assert result.components[0].confidence == "medium"


def test_zero_total_is_allocated_but_zero_denominator_remains_unallocated() -> None:
    zero_total = _component(
        billed_total="0.00",
        allocation_key="total_tokens",
        fallback_key=None,
        token_weights=None,
    )
    allocated = allocate_cost_period(
        _period(zero_total),
        [_observation("agent-a", input_tokens=1)],
        calculated_at=CALCULATED_AT,
    )
    assert _amounts(allocated) == {"agent-a": Decimal("0.00")}
    assert allocated.components[0].confidence == "high"
    assert allocated.components[0].unallocated_amount == Decimal("0.00")

    zero_usage = allocate_cost_period(
        _period(
            _component(
                allocation_key="total_tokens",
                fallback_key=None,
                token_weights=None,
            )
        ),
        [_observation("agent-a", input_tokens=0, output_tokens=0)],
        calculated_at=CALCULATED_AT,
    )
    assert zero_usage.rows == []
    assert zero_usage.components[0].confidence == "unavailable"
    assert zero_usage.components[0].coverage_state == "no_data"
    assert zero_usage.components[0].unallocated_amount == Decimal("100.00")

    missing_usage = allocate_cost_period(
        _period(
            _component(
                allocation_key="total_tokens",
                fallback_key=None,
                token_weights=None,
            )
        ),
        [_observation("agent-a", input_tokens=None, output_tokens=None)],
        calculated_at=CALCULATED_AT,
    )
    assert missing_usage.components[0].coverage_state == "not_reported"


def test_mixed_currencies_reconcile_separately() -> None:
    usd = _component(
        id="model-usd",
        billed_total="12.00",
        allocation_key="total_tokens",
        fallback_key=None,
        token_weights=None,
    )
    eur = deepcopy(usd)
    eur.update(
        {
            "id": "model-eur",
            "billed_total": "7.50",
            "currency": "EUR",
            "billing_boundary": {"kind": "pool", "value": "eu-model-pool"},
        }
    )

    result = allocate_cost_period(
        _period(usd, eur),
        [_observation("agent-a", input_tokens=1)],
        calculated_at=CALCULATED_AT,
    )

    assert {(item.currency, item.declared_total) for item in result.currency_subtotals} == {
        ("USD", Decimal("12.00")),
        ("EUR", Decimal("7.50")),
    }
    assert len(result.components) == 2
    assert sum(
        (summary.declared_total for summary in result.components if summary.currency == "USD"),
        Decimal(0),
    ) == Decimal("12.00")


def test_unattributed_agent_share_is_preserved_and_lowers_confidence() -> None:
    component = _component(
        allocation_key="total_tokens",
        fallback_key=None,
        token_weights=None,
    )
    result = allocate_cost_period(
        _period(component),
        [
            _observation("agent-a", input_tokens=1),
            _observation(None, input_tokens=1),
        ],
        calculated_at=CALCULATED_AT,
    )

    assert _amounts(result) == {
        "__unattributed_agent__": Decimal("50.00"),
        "agent-a": Decimal("50.00"),
    }
    summary = result.components[0]
    assert summary.attributed_amount == Decimal("50.00")
    assert summary.unattributed_amount == Decimal("50.00")
    assert summary.confidence == "low"
    assert summary.coverage_state == "partial"


def test_component_and_agent_filters_apply_after_full_allocation() -> None:
    component = _component(
        allocation_key="total_tokens",
        fallback_key=None,
        token_weights=None,
    )
    result = allocate_cost_period(
        _period(component),
        [
            _observation("agent-a", input_tokens=1),
            _observation("agent-b", input_tokens=3),
        ],
        calculated_at=CALCULATED_AT,
        component_id="model-prod",
        cost_agent_key="agent-a",
    )

    assert _amounts(result) == {"agent-a": Decimal("25.00")}
    summary = result.components[0]
    assert summary.attributed_amount == Decimal("100.00")
    assert summary.omitted_allocated_amount == Decimal("75.00")
    assert summary.rows_total == 2
    assert summary.rows_shown == 1
    with pytest.raises(ValueError, match="Unknown cost component"):
        allocate_cost_period(
            _period(component),
            [],
            calculated_at=CALCULATED_AT,
            component_id="missing",
        )


def test_tool_and_run_breakdowns_are_alternative_views_of_the_same_pool() -> None:
    component = _component(
        allocation_key="total_tokens",
        fallback_key=None,
        token_weights=None,
    )
    observations = [
        _observation(
            "agent-a",
            tool_name="search",
            run_key="run-a",
            input_tokens=1,
        ),
        _observation(
            "agent-a",
            tool_name="grounding",
            run_key="run-b",
            input_tokens=3,
        ),
    ]

    agents = allocate_cost_period(
        _period(component),
        observations,
        breakdown="agents",
        calculated_at=CALCULATED_AT,
    )
    tools = allocate_cost_period(
        _period(component),
        observations,
        breakdown="tools",
        calculated_at=CALCULATED_AT,
    )
    runs = allocate_cost_period(
        _period(component),
        observations,
        breakdown="runs",
        calculated_at=CALCULATED_AT,
    )

    assert _amounts(agents) == {"agent-a": Decimal("100.00")}
    assert _amounts(tools) == {
        "grounding": Decimal("75.00"),
        "search": Decimal("25.00"),
    }
    assert _amounts(runs) == {
        "run-b": Decimal("75.00"),
        "run-a": Decimal("25.00"),
    }
    for view, breakdown in (
        (agents, "agents"),
        (tools, "tools"),
        (runs, "runs"),
    ):
        assert view.breakdown == breakdown
        assert view.components[0].declared_total == Decimal("100.00")
        assert sum((row.amount for row in view.rows), Decimal(0)) == Decimal(
            "100.00"
        )
        assert view.currency_subtotals[0].declared_total == Decimal("100.00")
    assert "not an invoice" in agents.disclaimer


def test_tool_invocation_breakdown_groups_tools_and_preserves_unattributed() -> None:
    component = _component(
        id="search-prod",
        type="search",
        billed_total="40.00",
        allocation_key="tool_invocations",
        fallback_key=None,
        token_weights=None,
        usage_match={"runtime_kinds": ["foundry_hosted"]},
    )
    result = allocate_cost_period(
        _period(component),
        [
            _observation(
                "agent-a",
                tool_name="search",
                run_key="run-a",
                tool_invocations=1,
            ),
            _observation(
                "agent-a",
                tool_name="grounding",
                run_key="run-a",
                tool_invocations=2,
            ),
            _observation(
                "agent-a",
                tool_name=None,
                run_key="run-a",
                tool_invocations=1,
            ),
        ],
        breakdown="tools",
        calculated_at=CALCULATED_AT,
    )

    assert _amounts(result) == {
        "grounding": Decimal("20.00"),
        "__unattributed_tool__": Decimal("10.00"),
        "search": Decimal("10.00"),
    }
    unattributed = next(
        row for row in result.rows if row.consumer_kind == "unattributed"
    )
    assert unattributed.tool_name is None
    assert unattributed.agent_key == "agent-a"
    assert unattributed.run_key == "run-a"
    assert result.components[0].unattributed_amount == Decimal("10.00")
    assert result.components[0].confidence == "low"


@pytest.mark.parametrize(
    ("allocation_key", "usage", "expected"),
    [
        ("total_tokens", {"input_tokens": (1, 3)}, ("25.00", "75.00")),
        ("tool_invocations", {"tool_invocations": (1, 3)}, ("25.00", "75.00")),
        (
            "active_session_seconds",
            {"active_session_seconds": ("1", "3")},
            ("25.00", "75.00"),
        ),
        ("credits", {"credits": ("1", "3")}, ("25.00", "75.00")),
    ],
)
def test_run_breakdown_supports_each_run_attributable_usage_key(
    allocation_key: str,
    usage: dict[str, tuple[object, object]],
    expected: tuple[str, str],
) -> None:
    if allocation_key == "tool_invocations":
        component = _component(
            id="search-prod",
            type="search",
            allocation_key=allocation_key,
            fallback_key=None,
            token_weights=None,
            usage_match={"runtime_kinds": ["foundry_hosted"]},
        )
    elif allocation_key == "active_session_seconds":
        component = _component(
            id="compute-prod",
            type="hosted_compute",
            allocation_key=allocation_key,
            fallback_key=None,
            token_weights=None,
            usage_match={"runtime_kinds": ["foundry_hosted"]},
        )
    elif allocation_key == "credits":
        component = _component(
            id="credit-prod",
            type="credit_payg",
            allocation_key=allocation_key,
            fallback_key=None,
            token_weights=None,
            usage_match={"runtime_kinds": ["foundry_hosted"]},
        )
    else:
        component = _component(
            allocation_key=allocation_key,
            fallback_key=None,
            token_weights=None,
        )
    field, values = next(iter(usage.items()))
    result = allocate_cost_period(
        _period(component),
        [
            _observation(
                "agent-a",
                run_key="run-a",
                **{field: values[0]},
            ),
            _observation(
                "agent-a",
                run_key="run-b",
                **{field: values[1]},
            ),
        ],
        breakdown="runs",
        calculated_at=CALCULATED_AT,
    )

    assert _amounts(result) == {
        "run-b": Decimal(expected[1]),
        "run-a": Decimal(expected[0]),
    }
    assert all(row.consumer_kind == "run" for row in result.rows)
    assert all(row.agent_key == "agent-a" for row in result.rows)


def test_run_breakdown_preserves_uncorrelated_usage_in_reserved_bucket() -> None:
    component = _component(
        allocation_key="total_tokens",
        fallback_key=None,
        token_weights=None,
    )
    result = allocate_cost_period(
        _period(component),
        [
            _observation("agent-a", run_key="run-a", input_tokens=3),
            _observation("agent-a", run_key=None, input_tokens=1),
        ],
        breakdown="runs",
        calculated_at=CALCULATED_AT,
    )

    assert _amounts(result) == {
        "run-a": Decimal("75.00"),
        "__unattributed_run__": Decimal("25.00"),
    }
    unattributed = next(
        row for row in result.rows if row.consumer_kind == "unattributed"
    )
    assert unattributed.agent_key == "agent-a"
    assert unattributed.run_key is None
    assert result.components[0].unattributed_amount == Decimal("25.00")


@pytest.mark.parametrize(
    ("breakdown", "identity_field"),
    [("tools", "tool_name"), ("runs", "run_key")],
)
def test_alternative_rows_retain_agent_grain_for_post_allocation_filtering(
    breakdown: str,
    identity_field: str,
) -> None:
    component = _component(
        id="search-prod",
        type="search",
        allocation_key="tool_invocations",
        fallback_key=None,
        token_weights=None,
        usage_match={"runtime_kinds": ["foundry_hosted"]},
    )
    observations = [
        _observation(
            "agent-a",
            tool_name="search",
            run_key="shared-run",
            tool_invocations=1,
        ),
        _observation(
            "agent-b",
            tool_name="search",
            run_key="shared-run",
            tool_invocations=3,
        ),
    ]

    full = allocate_cost_period(
        _period(component),
        observations,
        breakdown=breakdown,
        calculated_at=CALCULATED_AT,
    )
    filtered = allocate_cost_period(
        _period(component),
        observations,
        breakdown=breakdown,
        calculated_at=CALCULATED_AT,
        cost_agent_key="agent-a",
    )

    assert len(full.rows) == 2
    assert {row.agent_key for row in full.rows} == {"agent-a", "agent-b"}
    assert {
        getattr(row, identity_field)
        for row in full.rows
    } == {"search" if breakdown == "tools" else "shared-run"}
    assert len(filtered.rows) == 1
    assert filtered.rows[0].agent_key == "agent-a"
    assert filtered.rows[0].amount == Decimal("25.00")
    summary = filtered.components[0]
    assert summary.attributed_amount == Decimal("100.00")
    assert summary.omitted_allocated_amount == Decimal("75.00")


def test_rows_preserve_complete_provenance_methods_and_freshness() -> None:
    commitment = _component(
        id="ptu-prod",
        type="provisioned_throughput",
        allocation_model="commitment",
        billed_source="August PTU commitment",
        billed_total="60.00",
    )
    metered = _component(
        id="compute-prod",
        type="hosted_compute",
        allocation_model="metered",
        allocation_key="active_session_seconds",
        fallback_key=None,
        token_weights=None,
        billed_source="August hosted compute bill",
        billed_total="30.00",
        usage_match={"runtime_kinds": ["foundry_hosted"]},
    )
    latest = datetime(2026, 8, 31, 23, tzinfo=timezone.utc)
    result = allocate_cost_period(
        _period(commitment, metered),
        [
            _observation(
                "agent-a",
                input_tokens=2,
                output_tokens=1,
                cache_read_tokens=4,
                active_session_seconds="5",
                latest_observed_at=latest,
            )
        ],
        calculated_at=CALCULATED_AT,
    )

    assert {row.allocation_model for row in result.rows} == {
        "metered",
        "commitment",
    }
    for row in result.rows:
        assert row.period_id == "2026-08"
        assert row.starts_at == datetime(2026, 8, 1, tzinfo=timezone.utc)
        assert row.ends_at == datetime(2026, 9, 1, tzinfo=timezone.utc)
        assert row.billing_boundary.value == PROJECT.lower()
        assert row.billed_source
        assert row.preferred_key == row.applied_key
        assert row.fallback_used is False
        assert row.usage_numerator > 0
        assert row.usage_denominator >= row.usage_numerator
        assert row.source_resource_id == SOURCE.lower()
        assert row.project_resource_id == PROJECT.lower()
        assert row.agent_key == "agent-a"
        assert row.confidence == "high"
        assert row.coverage_state == "available"
        assert row.coverage_reason
        assert row.calculated_at == CALCULATED_AT
        assert row.latest_observed_at == latest
    assert result.latest_observed_at == latest


def test_fallback_and_partial_coverage_evidence_use_confidence_precedence() -> None:
    fallback = allocate_cost_period(
        _period(),
        [
            _observation(
                "agent-a",
                input_tokens=1,
                output_tokens=1,
                cache_read_tokens=None,
            )
        ],
        calculated_at=CALCULATED_AT,
    )
    row = fallback.rows[0]
    assert row.preferred_key == "weighted_tokens"
    assert row.applied_key == "total_tokens"
    assert row.usage_unit == "total_tokens"
    assert row.fallback_used is True
    assert row.confidence == "medium"
    assert "fallback" in row.coverage_reason

    partial = allocate_cost_period(
        _period(),
        [
            _observation(
                "agent-a",
                input_tokens=1,
                output_tokens=1,
                cache_read_tokens=None,
                coverage_complete=False,
            )
        ],
        calculated_at=CALCULATED_AT,
    )
    partial_row = partial.rows[0]
    assert partial_row.fallback_used is True
    assert partial_row.confidence == "low"
    assert partial_row.coverage_state == "partial"
    assert "partial" in partial_row.coverage_reason
    assert partial.components[0].next_action


def test_mixed_telemetry_availability_allocates_only_observed_usage_with_low_confidence() -> None:
    component = _component(
        allocation_key="total_tokens",
        fallback_key=None,
        token_weights=None,
    )
    result = allocate_cost_period(
        _period(component),
        [
            _observation("agent-a", input_tokens=2, output_tokens=0),
            _observation(
                "agent-b",
                input_tokens=None,
                output_tokens=None,
                coverage_complete=False,
            ),
        ],
        calculated_at=CALCULATED_AT,
    )

    assert _amounts(result) == {"agent-a": Decimal("100.00")}
    assert result.rows[0].confidence == "low"
    assert result.rows[0].coverage_state == "partial"
    assert result.components[0].unallocated_amount == Decimal("0.00")
    assert result.components[0].next_action


def test_partial_direct_credits_precede_complete_event_counts() -> None:
    component = _component(
        id="credit-pool",
        type="credit_prepaid",
        allocation_model="commitment",
        allocation_key="credits",
        fallback_key="credit_events",
        token_weights=None,
        usage_match={
            "runtime_kinds": ["copilot_studio"],
            "credit_event_operations": ["InvokeAgent"],
        },
    )
    result = allocate_cost_period(
        _period(component),
        [
            _observation(
                "agent-a",
                runtime_kind="copilot_studio",
                credits="2",
                credit_events=1,
                operation_name="InvokeAgent",
            ),
            _observation(
                "agent-b",
                runtime_kind="copilot_studio",
                credits=None,
                credit_events=100,
                operation_name="InvokeAgent",
            ),
        ],
        calculated_at=CALCULATED_AT,
    )

    assert _amounts(result) == {"agent-a": Decimal("100.00")}
    assert result.rows[0].applied_key == "credits"
    assert result.rows[0].fallback_used is False
    assert result.rows[0].confidence == "low"


def test_fallback_exhaustion_keeps_full_total_unallocated_with_action() -> None:
    component = _component(
        id="credit-pool",
        type="credit_prepaid",
        allocation_model="commitment",
        allocation_key="credits",
        fallback_key="credit_events",
        token_weights=None,
        usage_match={
            "runtime_kinds": ["copilot_studio"],
            "credit_event_operations": ["InvokeAgent"],
        },
    )
    result = allocate_cost_period(
        _period(component),
        [
            _observation(
                "agent-a",
                runtime_kind="copilot_studio",
                credits=None,
                credit_events=None,
                operation_name="InvokeAgent",
            )
        ],
        calculated_at=CALCULATED_AT,
    )

    assert result.rows == []
    summary = result.components[0]
    assert summary.preferred_key == "credits"
    assert summary.applied_key == "credit_events"
    assert summary.fallback_used is True
    assert summary.confidence == "unavailable"
    assert summary.coverage_state == "not_reported"
    assert summary.attributed_amount == Decimal("0.00")
    assert summary.unattributed_amount == Decimal("0.00")
    assert summary.unallocated_amount == Decimal("100.00")
    assert summary.next_action


def test_row_bound_preserves_omitted_allocated_amount_and_reconciliation() -> None:
    component = _component(
        billed_total="501.00",
        allocation_key="total_tokens",
        fallback_key=None,
        token_weights=None,
    )
    observations = [
        _observation(f"agent-{index:03d}", input_tokens=1)
        for index in range(501)
    ]

    result = allocate_cost_period(
        _period(component),
        observations,
        calculated_at=CALCULATED_AT,
    )

    assert len(result.rows) == 500
    summary = result.components[0]
    assert summary.rows_total == 501
    assert summary.rows_shown == 500
    assert summary.attributed_amount == Decimal("501.00")
    assert summary.omitted_allocated_amount == Decimal("1.00")
    shown = sum((row.amount for row in result.rows), Decimal(0))
    assert shown + summary.omitted_allocated_amount == summary.attributed_amount
    assert (
        summary.attributed_amount
        + summary.unattributed_amount
        + summary.unallocated_amount
        == summary.declared_total
    )
