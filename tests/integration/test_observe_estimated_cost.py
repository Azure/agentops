from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from agentops.agent.knowledge.pricing import load_packaged_price_reference
from agentops.agent.observe.cache import ObserveCache
from agentops.agent.observe.queries import SourceResult
from agentops.agent.observe.service import ObserveService, estimate_token_cost
from agentops.agent.observe.ui import ESTIMATED_COST_DISCLAIMER, render_runs_table
from agentops.core.observe import (
    ObserveScope,
    ObservedRun,
    ResourceInventory,
    RunModelUsage,
    TelemetrySource,
)
from agentops.core.observe_pricing import load_price_reference
from fixtures.observe import make_price_entry, make_price_reference_payload


def test_packaged_reference_to_run_contract_to_console_is_offline_and_exact() -> None:
    now = datetime(2026, 8, 31, tzinfo=timezone.utc)
    run = ObservedRun(
        source_id="workspace-a",
        run_key="conversation-price-integration",
        run_key_kind="conversation",
        agent_key="agent-a",
        source_kind="foundry_prompt",
        started_at=now - timedelta(minutes=2),
        last_activity_at=now - timedelta(minutes=1),
        status="succeeded",
        turns=1,
        failed_turns=0,
        tool_invocations=0,
        tool_failures=0,
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        model_usage=[
            RunModelUsage(
                model="gpt-5-nano",
                input_tokens=1_000_000,
                output_tokens=1_000_000,
                cache_read_tokens=1_000_000,
            )
        ],
    )
    estimate = estimate_token_cost(
        run.model_usage,
        price_reference=load_packaged_price_reference(),
        as_of=now,
    )
    priced = run.model_copy(update={"estimated_cost": estimate})

    html = render_runs_table(
        [priced],
        bounds={"rows_shown": 1, "rows_total_in_scope": 1, "truncated": False},
    )

    assert str(estimate.amount) == "0.455"
    assert "USD 0.455" in html
    assert "Completeness: partial" in html
    assert "Price reference 2026.08.31, effective 2026-08-31" in html
    assert ESTIMATED_COST_DISCLAIMER in html


@pytest.mark.parametrize("view", ["agents", "models"])
def test_rollups_price_only_whole_runs_when_one_model_is_unpriced(view: str) -> None:
    now = datetime(2026, 8, 31, tzinfo=timezone.utc)
    project_id = (
        "/subscriptions/11111111-1111-1111-1111-111111111111/"
        "resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/foundry/"
        "projects/project-a"
    )
    source = TelemetrySource(
        source_id="workspace-a",
        resource_id=(
            "/subscriptions/11111111-1111-1111-1111-111111111111/"
            "resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/logs"
        ),
        workspace_id="workspace-a",
        project_resource_ids=[project_id],
        state="available",
    )
    scope = ObserveScope(mode="projects", project_resource_ids=[project_id])
    inventory = ResourceInventory(
        scope=scope,
        telemetry_sources=[source],
        discovered_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=4),
    )
    pricing_runs = [
        {
            "run_key": "run-complete",
            "model_usage": [
                {
                    "model": "model-a",
                    "input_tokens": 3,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "reasoning_tokens": 0,
                }
            ],
            "model_usage_truncated": False,
        },
        {
            "run_key": "run-incomplete",
            "model_usage": [
                {
                    "model": "model-a",
                    "input_tokens": 5,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "reasoning_tokens": 0,
                },
                {
                    "model": "model-b",
                    "input_tokens": 7,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "reasoning_tokens": 0,
                },
            ],
            "model_usage_truncated": False,
        },
    ]
    common = {
        "project_resource_id": project_id,
        "model": "model-a",
        "failures": 0,
        "input_tokens": 8,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "scope_run_count": 2,
        "pricing_runs": pricing_runs,
        "pricing_runs_truncated": False,
        "total_in_scope": 1,
    }
    row = (
        {
            **common,
            "agent_key": "agent-a",
            "agent_id": "agent-a",
            "agent_name": "Agent A",
            "invocations": 2,
            "last_seen": now,
        }
        if view == "agents"
        else {
            **common,
            "deployment": "model-a",
            "requests": 2,
            "last_seen": now,
        }
    )
    reference = load_price_reference(
        json.dumps(
            make_price_reference_payload(
                entries=[
                    make_price_entry(
                        model="model-a",
                        token_class="input",
                        unit_price="2",
                        per_tokens=1,
                    )
                ]
            )
        )
    )
    service = ObserveService(
        discovery_client=object(),
        query_client=object(),
        runtime=object(),
        clock=lambda: now,
        cache=ObserveCache(ttl_seconds=120),
        price_reference=reference,
    )

    normalized, _coverage = service._normalize_view(
        view,
        [SourceResult(source_id=source.source_id, status="success", tables=[row])],
        [source],
        inventory=inventory,
        window_end=now,
        refreshed_at=now,
    )
    estimate = normalized[0].estimated_cost

    assert estimate.amount == Decimal("6")
    assert estimate.completeness == "partial"
    assert estimate.covered_run_count == estimate.scope_run_count == 2
    assert estimate.unpriced_run_count == 1
