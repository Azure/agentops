"""Offline SC-006 acceptance check for standard-scope attribution rendering."""

from __future__ import annotations

from time import perf_counter

import pytest

from agentops.agent.observe.ui import (
    render_attribution_controls,
    render_department_view,
)


SAMPLES_PER_VIEW = 20
MAX_P95_SECONDS = 5.0


def _standard_scope_payload(metric: str) -> dict[str, object]:
    usage = {
        "invocations": 100,
        "input_tokens": 1_000,
        "output_tokens": 500,
        "tool_invocations": 10,
        "active_session_seconds": "60",
    }
    rows: list[dict[str, object]] = []
    for index in range(100):
        rows.append(
            {
                "kind": "department",
                "department_id": f"department-{index:03d}",
                "department_label": f"Department {index:03d}",
                "filter_token": f"at1~d~g1~config~scope~{index:03d}",
                "member_count": 2,
                "usage": usage,
                "cost": (
                    {
                        "period_id": "2026-08",
                        "component_id": "standard",
                        "amount": "10.00",
                        "currency": "USD",
                        "currency_minor_units": 2,
                        "usage_numerator": "1",
                        "usage_denominator": "100",
                        "allocation_key": "weighted_tokens",
                        "confidence": "high",
                    }
                    if metric == "cost"
                    else None
                ),
                "mapping_state": "mapped",
            }
        )
    return {
        "metric": metric,
        "group_by": "department",
        "access_boundary": "aggregate",
        "rows": rows,
        "summary": {
            "metric": metric,
            "period_id": "2026-08" if metric == "cost" else None,
            "component_id": "standard" if metric == "cost" else None,
            "total": usage,
            "distinct_users": 200,
            "omitted_users": 0,
        },
        "primary_measure": "allocated_amount" if metric == "cost" else "invocations",
        "calculated_at": "2026-08-25T12:00:00Z",
        "latest_observed_at": "2026-08-25T11:59:00Z",
    }


def _standard_scope_coverage() -> list[dict[str, object]]:
    return [
        {
            "source_id": f"synthetic-source-{index}",
            "dimension": "user_attribution",
            "status": "available",
            "attribution_level": "department",
            "eligible_records": 200,
            "attributed_records": 200,
        }
        for index in range(3)
    ]


def _nearest_rank_p95(samples: list[float]) -> float:
    return sorted(samples)[(95 * len(samples) + 99) // 100 - 1]


@pytest.mark.parametrize("metric", ["usage", "cost"])
def test_standard_scope_department_view_p95_is_at_most_five_seconds(
    metric: str,
) -> None:
    """Measure the bounded 100-department display path without Azure variability."""

    payload = _standard_scope_payload(metric)
    render_attribution_controls(payload, cost_available=True)
    render_department_view(payload, coverage=_standard_scope_coverage())
    durations: list[float] = []
    for _ in range(SAMPLES_PER_VIEW):
        started = perf_counter()
        controls = render_attribution_controls(
            payload,
            cost_available=True,
            period_options=({"id": "2026-08", "label": "August 2026"},),
            component_options=({"id": "standard", "label": "Standard"},),
        )
        rendered = controls + render_department_view(
            payload,
            coverage=_standard_scope_coverage(),
        )
        durations.append(perf_counter() - started)
        assert "Department 000" in rendered
        assert "Department 099" in rendered

    assert _nearest_rank_p95(durations) <= MAX_P95_SECONDS, (
        f"{metric} standard-scope display p95 was "
        f"{_nearest_rank_p95(durations):.3f}s"
    )
