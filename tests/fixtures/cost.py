"""Reusable cost-model payloads for contract and allocation tests."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


FOUNDRY_RESOURCE_ID = (
    "/subscriptions/11111111-1111-1111-1111-111111111111/"
    "resourceGroups/AI-Prod/providers/Microsoft.CognitiveServices/accounts/Foundry"
)


def valid_cost_model_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "periods": [
            {
                "id": "2026-08",
                "starts_at": "2026-08-01T00:00:00Z",
                "ends_at": "2026-09-01T00:00:00Z",
                "components": [
                    {
                        "id": "gpt-ptu-prod",
                        "type": "provisioned_throughput",
                        "billing_boundary": {
                            "kind": "resource",
                            "value": FOUNDRY_RESOURCE_ID,
                            "label": "Production Foundry",
                        },
                        "billed_source": "August provisioned throughput commitment",
                        "billed_total": "12000.00",
                        "currency": "USD",
                        "currency_minor_units": 2,
                        "allocation_model": "commitment",
                        "allocation_key": "weighted_tokens",
                        "fallback_key": "total_tokens",
                        "token_weights": {
                            "input_tokens": "1",
                            "output_tokens": "4",
                            "cache_read_tokens": "0.25",
                        },
                        "usage_match": {
                            "source_resource_ids": [
                                f"  {FOUNDRY_RESOURCE_ID}/  ",
                                FOUNDRY_RESOURCE_ID.lower(),
                            ],
                            "deployments": [" gpt-prod ", "gpt-prod"],
                        },
                    }
                ],
            }
        ],
    }


def invalid_cost_model_payload() -> dict[str, Any]:
    payload = valid_cost_model_payload()
    payload["periods"][0]["components"][0]["billed_total"] = "-1"
    return payload


def overlapping_cost_model_payload() -> dict[str, Any]:
    payload = valid_cost_model_payload()
    second_period = deepcopy(payload["periods"][0])
    second_period.update(
        {
            "id": "2026-08-overlap",
            "starts_at": "2026-08-15T00:00:00Z",
            "ends_at": "2026-09-15T00:00:00Z",
        }
    )
    payload["periods"].append(second_period)
    return payload


def valid_multi_period_cost_model_payload() -> dict[str, Any]:
    payload = valid_cost_model_payload()
    second_period = deepcopy(payload["periods"][0])
    second_period.update(
        {
            "id": "2026-09",
            "starts_at": "2026-09-01T00:00:00Z",
            "ends_at": "2026-10-01T00:00:00Z",
        }
    )
    second_period["components"][0]["id"] = "gpt-ptu-september"
    payload["periods"].append(second_period)
    return payload


def mixed_currency_cost_model_payload() -> dict[str, Any]:
    payload = valid_cost_model_payload()
    search = {
        "id": "search-prod",
        "type": "search",
        "billing_boundary": {
            "kind": "resource",
            "value": (
                "/subscriptions/11111111-1111-1111-1111-111111111111/"
                "resourceGroups/AI-Prod/providers/Microsoft.Search/"
                "searchServices/Search-Prod"
            ),
        },
        "billed_source": "August search billed total",
        "billed_total": "830",
        "currency": "EUR",
        "currency_minor_units": 2,
        "allocation_model": "metered",
        "allocation_key": "tool_invocations",
        "usage_match": {"tool_names": ["product_search"]},
    }
    payload["periods"][0]["components"].append(search)
    return payload


def fallback_cost_model_payload() -> dict[str, Any]:
    payload = valid_cost_model_payload()
    component = payload["periods"][0]["components"][0]
    component.update(
        {
            "id": "credits-prod",
            "type": "credit_prepaid",
            "billed_source": "August prepaid credit pool",
            "billed_total": "2500.00",
            "allocation_key": "credits",
            "fallback_key": "credit_events",
            "token_weights": None,
            "usage_match": {
                "runtime_kinds": [" copilot_studio ", "copilot_studio"],
                "credit_event_operations": [" InvokeAgent ", "InvokeAgent"],
            },
        }
    )
    return payload


def secret_shaped_cost_model_payload() -> dict[str, Any]:
    payload = valid_cost_model_payload()
    payload["periods"][0]["components"][0]["client_secret"] = "do-not-echo"
    return payload
