"""Focused tests for pure billed-cost contracts and loading."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parents[1]))

from agentops.core.cost import (
    BillingBoundary,
    CostAllocationRow,
    CostComponent,
    CostComponentSummary,
    CostModel,
    CostModelLoadResult,
    CostPeriodRef,
    CostUsageObservation,
    CostViewData,
    CurrencySubtotal,
    TokenWeights,
    canonical_cost_model_json,
    cost_model_fingerprint,
    load_cost_model,
)
from fixtures.cost import (
    FOUNDRY_RESOURCE_ID,
    fallback_cost_model_payload,
    invalid_cost_model_payload,
    mixed_currency_cost_model_payload,
    overlapping_cost_model_payload,
    secret_shaped_cost_model_payload,
    valid_cost_model_payload,
)


def _component(payload: dict) -> dict:
    return payload["periods"][0]["components"][0]


def test_valid_model_normalizes_selectors_and_arm_ids() -> None:
    model = CostModel.model_validate(valid_cost_model_payload())
    component = model.periods[0].components[0]

    assert model.version == 1
    assert component.billing_boundary.value == FOUNDRY_RESOURCE_ID.lower()
    assert component.usage_match.source_resource_ids == [
        FOUNDRY_RESOURCE_ID.lower()
    ]
    assert component.usage_match.deployments == ["gpt-prod"]
    assert component.billed_total == Decimal("12000.00")
    assert component.token_weights is not None
    assert component.token_weights.cache_read_tokens == Decimal("0.25")


@pytest.mark.parametrize("version", [0, 2, "1", True])
def test_model_requires_exact_integer_version_one(version: object) -> None:
    payload = valid_cost_model_payload()
    payload["version"] = version
    with pytest.raises(ValidationError):
        CostModel.model_validate(payload)


def test_loader_enforces_utf8_size_before_parsing() -> None:
    oversized = "{" + ("é" * 16_384) + "}"
    result = load_cost_model(oversized)

    assert result.state == "invalid"
    assert result.error_code == "cost_model_too_large"
    assert result.model is None
    assert "32 KiB" in (result.message or "")


@pytest.mark.parametrize(
    ("field", "count"),
    [("periods", 25), ("components", 51)],
)
def test_model_enforces_period_and_component_cardinality(
    field: str, count: int
) -> None:
    payload = valid_cost_model_payload()
    if field == "periods":
        base = payload["periods"][0]
        payload["periods"] = [
            deepcopy(base) | {"id": f"period-{index}"}
            for index in range(count)
        ]
    else:
        base = _component(payload)
        payload["periods"][0]["components"] = [
            deepcopy(base) | {"id": f"component-{index}"}
            for index in range(count)
        ]
    with pytest.raises(ValidationError):
        CostModel.model_validate(payload)


@pytest.mark.parametrize(
    "value",
    ["01", "1.", ".5", "+1", "-1", "1e2", "NaN", "Infinity", 1, 1.0],
)
def test_billed_total_requires_a_canonical_non_negative_decimal_string(
    value: object,
) -> None:
    payload = valid_cost_model_payload()
    _component(payload)["billed_total"] = value
    with pytest.raises(ValidationError):
        CostModel.model_validate(payload)


def test_billed_total_must_fit_declared_minor_units() -> None:
    payload = valid_cost_model_payload()
    _component(payload)["billed_total"] = "1.001"
    with pytest.raises(ValidationError, match="currency_minor_units"):
        CostModel.model_validate(payload)


@pytest.mark.parametrize("value", ["0", "0.0", "-1", "01", "1e2", 1])
def test_token_weights_are_canonical_positive_decimal_strings(value: object) -> None:
    payload = valid_cost_model_payload()
    _component(payload)["token_weights"] = {"input_tokens": value}
    with pytest.raises(ValidationError):
        CostModel.model_validate(payload)


def test_periods_require_ordered_utc_boundaries() -> None:
    payload = valid_cost_model_payload()
    payload["periods"][0]["starts_at"] = "2026-08-01T00:00:00"
    with pytest.raises(ValidationError, match="UTC"):
        CostModel.model_validate(payload)

    payload = valid_cost_model_payload()
    payload["periods"][0]["ends_at"] = payload["periods"][0]["starts_at"]
    with pytest.raises(ValidationError, match="later"):
        CostModel.model_validate(payload)


def test_period_and_component_ids_must_be_unique() -> None:
    payload = valid_cost_model_payload()
    payload["periods"].append(deepcopy(payload["periods"][0]))
    payload["periods"][1]["starts_at"] = "2026-09-01T00:00:00Z"
    payload["periods"][1]["ends_at"] = "2026-10-01T00:00:00Z"
    with pytest.raises(ValidationError, match="period IDs"):
        CostModel.model_validate(payload)

    payload = valid_cost_model_payload()
    payload["periods"][0]["components"].append(
        deepcopy(payload["periods"][0]["components"][0])
    )
    with pytest.raises(ValidationError, match="component IDs"):
        CostModel.model_validate(payload)


def test_same_component_and_boundary_periods_cannot_overlap() -> None:
    with pytest.raises(ValidationError, match="overlap"):
        CostModel.model_validate(overlapping_cost_model_payload())

    payload = overlapping_cost_model_payload()
    payload["periods"][1]["starts_at"] = "2026-09-01T00:00:00Z"
    CostModel.model_validate(payload)


@pytest.mark.parametrize(
    ("component_type", "allocation_model", "allocation_key", "fallback_key"),
    [
        ("provisioned_throughput", "metered", "total_tokens", None),
        ("standard_model", "metered", "tool_invocations", None),
        ("search", "commitment", "tool_invocations", None),
        ("hosted_compute", "metered", "total_tokens", None),
        ("customer_compute", "commitment", "active_session_seconds", "total_tokens"),
        ("credit_payg", "commitment", "credits", None),
        ("credit_prepaid", "commitment", "credits", "total_tokens"),
    ],
)
def test_component_compatibility_matrix_is_closed(
    component_type: str,
    allocation_model: str,
    allocation_key: str,
    fallback_key: str | None,
) -> None:
    payload = valid_cost_model_payload()
    component = _component(payload)
    component.update(
        {
            "type": component_type,
            "allocation_model": allocation_model,
            "allocation_key": allocation_key,
            "fallback_key": fallback_key,
            "token_weights": None,
        }
    )
    with pytest.raises(ValidationError, match="compatible"):
        CostModel.model_validate(payload)


def test_weighted_tokens_require_weights_and_non_weighted_forbids_them() -> None:
    payload = valid_cost_model_payload()
    _component(payload)["token_weights"] = None
    with pytest.raises(ValidationError, match="token_weights"):
        CostModel.model_validate(payload)

    payload = valid_cost_model_payload()
    component = _component(payload)
    component.update(
        {"allocation_key": "total_tokens", "fallback_key": None}
    )
    with pytest.raises(ValidationError, match="token_weights"):
        CostModel.model_validate(payload)


def test_usage_match_requires_a_narrowing_selector() -> None:
    payload = valid_cost_model_payload()
    _component(payload)["usage_match"] = {
        "credit_event_operations": ["InvokeAgent"]
    }
    with pytest.raises(ValidationError, match="narrowing selector"):
        CostModel.model_validate(payload)


def test_credit_event_operations_are_required_iff_credit_events_are_used() -> None:
    payload = fallback_cost_model_payload()
    model = CostModel.model_validate(payload)
    assert model.periods[0].components[0].usage_match.credit_event_operations == [
        "InvokeAgent"
    ]

    missing = fallback_cost_model_payload()
    _component(missing)["usage_match"]["credit_event_operations"] = []
    with pytest.raises(ValidationError, match="credit_event_operations"):
        CostModel.model_validate(missing)

    forbidden = valid_cost_model_payload()
    _component(forbidden)["usage_match"]["credit_event_operations"] = ["InvokeAgent"]
    with pytest.raises(ValidationError, match="credit_event_operations"):
        CostModel.model_validate(forbidden)


def test_strict_models_reject_unknown_and_secret_shaped_fields() -> None:
    with pytest.raises(ValidationError):
        CostModel.model_validate(secret_shaped_cost_model_payload())
    with pytest.raises(ValidationError):
        BillingBoundary(kind="custom", value="pool-a", unexpected=True)

    result = load_cost_model(json.dumps(secret_shaped_cost_model_payload()))
    assert result.state == "invalid"
    assert result.error_code == "cost_model_secret_field"
    assert "do-not-echo" not in (result.message or "")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (
            "billed_source",
            "DefaultEndpointsProtocol=https;AccountKey=do-not-echo",
        ),
        (
            "boundary",
            "@Microsoft.KeyVault(SecretUri=https://vault.example/secrets/do-not-echo)",
        ),
    ],
)
def test_loader_rejects_credential_bearing_values_without_echoing(
    field: str, value: str
) -> None:
    payload = valid_cost_model_payload()
    component = _component(payload)
    if field == "boundary":
        component["billing_boundary"] = {"kind": "custom", "value": value}
    else:
        component[field] = value

    result = load_cost_model(json.dumps(payload))

    assert result.state == "invalid"
    assert result.error_code == "cost_model_secret_field"
    assert result.model is None
    assert "do-not-echo" not in (result.message or "")


def test_loader_returns_absent_valid_and_invalid_states() -> None:
    absent = load_cost_model(None)
    valid = load_cost_model(json.dumps(valid_cost_model_payload()))
    invalid = load_cost_model(json.dumps(invalid_cost_model_payload()))

    assert absent == CostModelLoadResult(state="absent")
    assert valid.state == "valid"
    assert valid.model is not None
    assert valid.fingerprint == cost_model_fingerprint(valid.model)
    assert invalid.state == "invalid"
    assert invalid.model is None
    assert invalid.fingerprint is None
    assert invalid.error_code == "cost_model_validation_error"
    assert "billed_total" in (invalid.message or "")
    assert "-1" not in (invalid.message or "")


def test_loader_rejects_malformed_json_without_echoing_input() -> None:
    raw = '{"client_secret":"do-not-echo"'
    result = load_cost_model(raw)
    assert result.state == "invalid"
    assert result.error_code == "cost_model_invalid_json"
    assert "do-not-echo" not in (result.message or "")


def test_canonical_serialization_and_fingerprint_are_deterministic() -> None:
    payload = mixed_currency_cost_model_payload()
    reordered = json.loads(json.dumps(payload))
    reordered = {"periods": reordered["periods"], "version": reordered["version"]}
    first = CostModel.model_validate(payload)
    second = CostModel.model_validate(reordered)

    assert canonical_cost_model_json(first) == canonical_cost_model_json(second)
    assert cost_model_fingerprint(first) == cost_model_fingerprint(second)
    assert len(cost_model_fingerprint(first)) == 64
    assert '": ' not in canonical_cost_model_json(first)


def test_load_result_state_invariants_are_strict() -> None:
    model = CostModel.model_validate(valid_cost_model_payload())
    with pytest.raises(ValidationError):
        CostModelLoadResult(state="valid", model=model)
    with pytest.raises(ValidationError):
        CostModelLoadResult(state="absent", message="unexpected")
    with pytest.raises(ValidationError):
        CostModelLoadResult(
            state="invalid",
            error_code="cost_model_validation_error",
        )


def test_usage_and_response_contracts_are_strict_and_decimal_safe() -> None:
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    observation = CostUsageObservation(
        source_resource_id=FOUNDRY_RESOURCE_ID,
        runtime_kind="foundry_hosted",
        input_tokens=0,
        active_session_seconds="1.25",
        credits=None,
        coverage_complete=True,
    )
    boundary = BillingBoundary(
        kind="resource",
        value=FOUNDRY_RESOURCE_ID,
        label="Foundry resource pool",
    )
    row = CostAllocationRow(
        period_id="2026-08",
        starts_at="2026-08-01T00:00:00Z",
        ends_at="2026-09-01T00:00:00Z",
        component_id="compute",
        component_type="hosted_compute",
        billing_boundary=boundary,
        billed_source="August compute total",
        allocation_model="metered",
        preferred_key="active_session_seconds",
        applied_key="active_session_seconds",
        fallback_used=False,
        breakdown="agents",
        consumer_kind="agent",
        consumer_key="agent-a",
        agent_key="agent-a",
        amount="1.25",
        currency="USD",
        currency_minor_units=2,
        usage_numerator="1.25",
        usage_denominator="1.25",
        usage_unit="active_session_seconds",
        rounding_adjustment_minor_units=0,
        confidence="high",
        coverage_state="available",
        coverage_reason="Complete observed duration.",
        calculated_at=now,
        latest_observed_at=now,
    )
    summary = CostComponentSummary(
        period_id="2026-08",
        starts_at="2026-08-01T00:00:00Z",
        ends_at="2026-09-01T00:00:00Z",
        component_id="compute",
        component_type="hosted_compute",
        billing_boundary=boundary,
        billed_source="August compute total",
        allocation_model="metered",
        preferred_key="active_session_seconds",
        applied_key="active_session_seconds",
        fallback_used=False,
        breakdown="agents",
        currency="USD",
        currency_minor_units=2,
        declared_total="1.25",
        attributed_amount="1.25",
        unattributed_amount="0",
        unallocated_amount="0",
        omitted_allocated_amount="0",
        rows_shown=1,
        rows_total=1,
        confidence="high",
        coverage_state="available",
        coverage_reason="Complete observed duration.",
        next_action=None,
    )
    view = CostViewData(
        period=CostPeriodRef(
            id="2026-08",
            starts_at="2026-08-01T00:00:00Z",
            ends_at="2026-09-01T00:00:00Z",
        ),
        breakdown="agents",
        components=[summary],
        rows=[row],
        currency_subtotals=[
            CurrencySubtotal(
                currency="USD",
                currency_minor_units=2,
                declared_total="1.25",
                attributed_amount="1.25",
                unattributed_amount="0",
                unallocated_amount="0",
            )
        ],
        calculated_at=now,
        latest_observed_at=now,
    )

    assert observation.source_resource_id == FOUNDRY_RESOURCE_ID.lower()
    assert observation.input_tokens == 0
    assert observation.credits is None
    serialized = json.loads(view.model_dump_json())
    serialized_row = serialized["rows"][0]
    serialized_summary = serialized["components"][0]
    assert serialized_row["amount"] == "1.25"
    assert serialized_row["period_id"] == "2026-08"
    assert serialized_row["starts_at"] == "2026-08-01T00:00:00Z"
    assert serialized_row["ends_at"] == "2026-09-01T00:00:00Z"
    assert serialized_row["billing_boundary"] == {
        "kind": "resource",
        "value": FOUNDRY_RESOURCE_ID.lower(),
        "label": "Foundry resource pool",
    }
    assert serialized_row["billed_source"] == "August compute total"
    assert serialized_row["allocation_model"] == "metered"
    assert serialized_row["preferred_key"] == "active_session_seconds"
    assert serialized_row["applied_key"] == "active_session_seconds"
    assert serialized_row["fallback_used"] is False
    assert serialized_row["usage_unit"] == "active_session_seconds"
    assert serialized_row["usage_numerator"] == "1.25"
    assert serialized_row["usage_denominator"] == "1.25"
    assert serialized_row["confidence"] == "high"
    assert serialized_row["calculated_at"] == "2026-08-24T00:00:00Z"
    assert serialized_row["latest_observed_at"] == "2026-08-24T00:00:00Z"
    assert serialized_summary["rows_shown"] == 1
    assert serialized_summary["rows_total"] == 1
    assert serialized["calculated_at"] == "2026-08-24T00:00:00Z"
    assert serialized["latest_observed_at"] == "2026-08-24T00:00:00Z"
    assert serialized["disclaimer"] == (
        "Operational cost allocation from declared billed totals and observed usage; "
        "not an invoice or billing-accurate charge."
    )
    with pytest.raises(ValidationError):
        CostUsageObservation(
            source_resource_id=FOUNDRY_RESOURCE_ID,
            runtime_kind="unknown",
            coverage_complete=True,
            prompt="must not be accepted",
        )
