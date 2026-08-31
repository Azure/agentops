"""Reusable fakes for Observe unit and integration tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any


OBSERVE_FIXTURE_ROW_LIMIT = 4
OBSERVE_FIXTURE_TIME = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
OBSERVE_FIXTURE_PROJECT = (
    "/subscriptions/11111111-1111-1111-1111-111111111111/"
    "resourcegroups/rg/providers/microsoft.cognitiveservices/accounts/foundry/"
    "projects/project-a"
)
ATTRIBUTION_FIXTURE_TENANT = "22222222-2222-2222-2222-222222222222"
ATTRIBUTION_FIXTURE_NAMESPACE = "33333333-3333-4333-8333-333333333333"
ATTRIBUTION_FIXTURE_PRINCIPAL = "alex@example.test"
ATTRIBUTION_FIXTURE_GROUPS = (
    "44444444-4444-4444-8444-444444444444",
    "55555555-5555-4555-8555-555555555555",
)


def _fixture_index(index: int) -> int:
    if not 0 <= index < OBSERVE_FIXTURE_ROW_LIMIT:
        raise ValueError(
            f"fixture index must be between 0 and {OBSERVE_FIXTURE_ROW_LIMIT - 1}"
        )
    return index


def make_agent_usage_row(index: int = 0, **overrides: Any) -> dict[str, Any]:
    """Return one small, deterministic bounded-query agent row."""
    index = _fixture_index(index)
    row: dict[str, Any] = {
        "project_resource_id": OBSERVE_FIXTURE_PROJECT,
        "agent_key": f"agent-{index + 1}",
        "agent_id": f"agent-{index + 1}",
        "agent_name": f"Agent {index + 1}",
        "model": "gpt-5-nano",
        "invocations": 4 + index,
        "failures": 0,
        "p95_latency_ms": 80.0 + index,
        "input_tokens": 40 + index,
        "output_tokens": 20 + index,
        "last_seen": OBSERVE_FIXTURE_TIME - timedelta(minutes=index),
    }
    row.update(overrides)
    return row


def make_model_usage_row(index: int = 0, **overrides: Any) -> dict[str, Any]:
    """Return one model row with complete granular-token usage."""
    index = _fixture_index(index)
    row: dict[str, Any] = {
        "project_resource_id": OBSERVE_FIXTURE_PROJECT,
        "agent_id": f"agent-{index + 1}",
        "deployment": "gpt-5-nano-prod",
        "model": "gpt-5-nano",
        "requests": 4 + index,
        "failures": 0,
        "p95_latency_ms": 75.0 + index,
        "input_tokens": 40 + index,
        "output_tokens": 20 + index,
        "cache_read_tokens": 8 + index,
        "cache_write_tokens": 2 + index,
        "reasoning_tokens": 5 + index,
        "last_seen": OBSERVE_FIXTURE_TIME - timedelta(minutes=index),
    }
    row.update(overrides)
    return row


def make_tool_usage_row(index: int = 0, **overrides: Any) -> dict[str, Any]:
    """Return one attributed tool-invocation row."""
    index = _fixture_index(index)
    row: dict[str, Any] = {
        "project_resource_id": OBSERVE_FIXTURE_PROJECT,
        "tool_name": "search" if index % 2 == 0 else "grounding",
        "operation_name": "execute_tool",
        "agent_key": f"agent-{index + 1}",
        "agent_id": f"agent-{index + 1}",
        "invocations": 3 + index,
        "failures": 0,
        "p95_latency_ms": 35.0 + index,
        "last_seen": OBSERVE_FIXTURE_TIME - timedelta(minutes=index),
    }
    row.update(overrides)
    return row


def make_run_usage_row(index: int = 0, **overrides: Any) -> dict[str, Any]:
    """Return one run row with duration, granular tokens, and direct credits."""
    index = _fixture_index(index)
    started_at = OBSERVE_FIXTURE_TIME - timedelta(minutes=10 + index)
    last_activity_at = started_at + timedelta(minutes=5)
    row: dict[str, Any] = {
        "project_resource_id": OBSERVE_FIXTURE_PROJECT,
        "run_key": f"conversation-{index + 1}",
        "run_key_kind": "conversation",
        "agent_key": f"agent-{index + 1}",
        "agent_id": f"agent-{index + 1}",
        "operation_name": "credit.consume",
        "started_at": started_at,
        "last_activity_at": last_activity_at,
        "duration_ms": 300_000.0,
        "turns": 2,
        "failed_turns": 0,
        "tool_invocations": 1,
        "tool_failures": 0,
        "input_tokens": 30 + index,
        "output_tokens": 12 + index,
        "cache_read_tokens": 6 + index,
        "cache_write_tokens": 1 + index,
        "reasoning_tokens": 4 + index,
        "credits": "1.5",
        "credit_events": 1,
    }
    row.update(overrides)
    return row


def make_scope_facet_option_row(
    index: int = 0,
    *,
    dimension: str = "agent",
    **overrides: Any,
) -> dict[str, Any]:
    """Return one facet enumeration row as the scope-options query returns it.

    A facet row carries the selectable value, the label the console displays for
    it, and the observed activity the enumeration orders by. Labels stay to
    identifiers and names: no facet fixture may carry model input, model output,
    or prompt text, because a facet label reaches the console address.
    """
    index = _fixture_index(index)
    row: dict[str, Any] = {
        "value": f"{dimension}-{index + 1}",
        "label": f"{dimension.capitalize()} {index + 1}",
        # Descending activity is what the enumeration orders by, so the fixture
        # is built descending to make an unordered implementation visible.
        "activity": (OBSERVE_FIXTURE_ROW_LIMIT - index) * 10,
    }
    row.update(overrides)
    return row


def make_run_model_usage_row(
    index: int = 0,
    *,
    model: str = "gpt-5-nano",
    **overrides: Any,
) -> dict[str, Any]:
    """Return one run-to-model row carrying that model's own five token counts.

    This is the inner grouping of the runs query: one row per run *per model*,
    which is what lets a multi-model run be priced at each model's own rates
    instead of one model's rates applied to a combined total.
    """
    index = _fixture_index(index)
    row: dict[str, Any] = {
        "project_resource_id": OBSERVE_FIXTURE_PROJECT,
        "run_key": f"conversation-{index + 1}",
        "agent_key": f"agent-{index + 1}",
        "model": model,
        "input_tokens": 30 + index,
        "output_tokens": 12 + index,
        "cache_read_tokens": 6 + index,
        "cache_write_tokens": 1 + index,
        "reasoning_tokens": 4 + index,
    }
    row.update(overrides)
    return row


def make_multi_model_run_rows(
    index: int = 0,
    *,
    models: tuple[str, ...] = ("gpt-5-nano", "gpt-5-mini"),
) -> list[dict[str, Any]]:
    """Return the per-model rows of a single run that used more than one model.

    Every row shares one ``run_key`` and carries different token counts, so
    pricing them at one model's rates produces a different figure than pricing
    each at its own — which is what makes FR-035a testable.
    """
    index = _fixture_index(index)
    return [
        make_run_model_usage_row(
            index,
            model=model,
            input_tokens=30 + index + (position * 100),
            output_tokens=12 + index + (position * 50),
        )
        for position, model in enumerate(models)
    ]


def make_price_entry(
    *,
    model: str = "gpt-5-nano",
    token_class: str = "input",
    unit_price: str = "0.05",
    currency: str = "USD",
    per_tokens: int = 1_000_000,
    **overrides: Any,
) -> dict[str, Any]:
    """Return one priced entry of a list-price reference.

    ``unit_price`` is a decimal *string* rather than a float: a binary float
    cannot represent a published price exactly, and a cost figure derived from
    one drifts in a way an operator cannot account for.
    """
    entry: dict[str, Any] = {
        "model": model,
        "token_class": token_class,
        "unit_price": unit_price,
        "currency": currency,
        "per_tokens": per_tokens,
    }
    entry.update(overrides)
    return entry


def make_price_reference_payload(
    *,
    version: str = "fixture-v1",
    effective_date: str = "2026-08-01",
    source: str = "Published list prices (fixture)",
    entries: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Return a complete price reference payload for the pure parser.

    The default reference prices one model across the three token classes the
    spec names at minimum, leaving ``gpt-5-mini`` unpriced so the *not priced*
    path stays reachable from the default fixture without extra setup.
    """
    if entries is None:
        entries = [
            make_price_entry(token_class="input", unit_price="0.05"),
            make_price_entry(token_class="output", unit_price="0.40"),
            make_price_entry(token_class="cache_read", unit_price="0.005"),
        ]
    payload: dict[str, Any] = {
        "version": version,
        "effective_date": effective_date,
        "source": source,
        "entries": entries,
    }
    payload.update(overrides)
    return payload


def make_scope_facet_option_rows_at_scale(
    count: int = 1000,
    *,
    dimension: str = "agent",
) -> list[dict[str, Any]]:
    """Return enough distinct facet rows to exercise the enumeration bound.

    Deliberately not bounded by ``OBSERVE_FIXTURE_ROW_LIMIT``: the point of this
    builder is to exceed the roughly fifty-option display bound and the
    thousand-distinct-value performance budget.
    """
    if count < 0:
        raise ValueError("count must be non-negative")
    return [
        {
            "value": f"{dimension}-{index + 1}",
            "label": f"{dimension.capitalize()} {index + 1}",
            "activity": count - index,
        }
        for index in range(count)
    ]


def make_run_usage_rows_at_scale(count: int = 1000) -> list[dict[str, Any]]:
    """Return enough distinct run rows to exercise the scale budgets.

    Used by the timed tests to assert a thousand-run scope resolves within
    budget and by the render test to assert the row bound renders without an
    unbounded per-row cost.
    """
    if count < 0:
        raise ValueError("count must be non-negative")
    started_at = OBSERVE_FIXTURE_TIME - timedelta(days=1)
    return [
        {
            "project_resource_id": OBSERVE_FIXTURE_PROJECT,
            "run_key": f"conversation-{index + 1}",
            "run_key_kind": "conversation",
            "agent_key": f"agent-{(index % 50) + 1}",
            "agent_id": f"agent-{(index % 50) + 1}",
            "operation_name": "credit.consume",
            "started_at": started_at + timedelta(seconds=index),
            "last_activity_at": started_at + timedelta(seconds=index + 30),
            "duration_ms": 30_000.0,
            "turns": 2,
            "failed_turns": 0,
            "tool_invocations": 1,
            "tool_failures": 0,
            "input_tokens": 30,
            "output_tokens": 12,
            "cache_read_tokens": 6,
            "cache_write_tokens": 1,
            "reasoning_tokens": 4,
            "credits": "1.5",
            "credit_events": 1,
        }
        for index in range(count)
    ]


def make_agent_usage_rows_at_scale(count: int = 1000) -> list[dict[str, Any]]:
    """Return enough distinct agent rows to exercise the Overview budget."""
    if count < 0:
        raise ValueError("count must be non-negative")
    return [
        {
            "project_resource_id": OBSERVE_FIXTURE_PROJECT,
            "agent_key": f"agent-{index + 1}",
            "agent_id": f"agent-{index + 1}",
            "agent_name": f"Agent {index + 1}",
            "model": "gpt-5-nano",
            "invocations": 4,
            "failures": 0,
            "p95_latency_ms": 80.0,
            "input_tokens": 40,
            "output_tokens": 20,
            "last_seen": OBSERVE_FIXTURE_TIME,
        }
        for index in range(count)
    ]


def make_attribution_user_key(index: int = 0, *, generation: int = 1) -> str:
    """Return a syntactically valid deterministic pseudonymous fixture key."""
    if index < 0:
        raise ValueError("fixture index must be non-negative")
    return f"usr1.g{generation}.{index + 1:064x}"


def make_attribution_identity_row(
    index: int = 0,
    *,
    alias_state: str = "authenticated",
    **overrides: Any,
) -> dict[str, Any]:
    """Return telemetry-shaped identity evidence without production user data."""
    identity = (
        ATTRIBUTION_FIXTURE_PRINCIPAL
        if index == 0
        else f"synthetic-user-{index + 1}@example.test"
    )
    row: dict[str, Any] = {
        "UserAuthenticatedId": identity,
        "Properties": {"enduser.id": identity},
        "invocations": index + 1,
        "input_tokens": (index + 1) * 10,
        "output_tokens": (index + 1) * 5,
        "tool_invocations": index % 3,
    }
    if alias_state == "missing":
        row["UserAuthenticatedId"] = ""
        row["Properties"] = {}
    elif alias_state == "column_only":
        row["Properties"] = {}
    elif alias_state == "property_only":
        row["UserAuthenticatedId"] = ""
    elif alias_state == "conflicting":
        row["Properties"] = {"enduser.id": f"conflict-{index + 1}@example.test"}
    elif alias_state != "authenticated":
        raise ValueError(f"unsupported alias_state: {alias_state}")
    row.update(overrides)
    return row


def make_attribution_config_payload(
    *,
    empty: bool = False,
    singleton: bool = False,
    generation: int = 1,
) -> dict[str, Any]:
    """Return a bounded attribution config for bootstrap and mapping tests."""
    departments: list[dict[str, Any]] = []
    if not empty:
        departments = [
            {
                "id": "engineering",
                "label": "Engineering",
                "user_keys": [
                    make_attribution_user_key(0, generation=generation),
                    *(
                        []
                        if singleton
                        else [make_attribution_user_key(1, generation=generation)]
                    ),
                ],
                "group_ids": [ATTRIBUTION_FIXTURE_GROUPS[0]],
            },
            {
                "id": "finance",
                "label": "Finance",
                "user_keys": [make_attribution_user_key(2, generation=generation)],
                "group_ids": [ATTRIBUTION_FIXTURE_GROUPS[1]],
            },
        ]
    return {
        "version": 1,
        "enabled": True,
        "deployment_namespace": ATTRIBUTION_FIXTURE_NAMESPACE,
        "generation": generation,
        "departments": departments,
    }


def make_attribution_principal(
    *,
    group_overage: bool = False,
    groups: tuple[str, ...] = ATTRIBUTION_FIXTURE_GROUPS[:1],
) -> dict[str, Any]:
    """Return a validated-principal-shaped fixture with explicit overage state."""
    return {
        "tenant_id": ATTRIBUTION_FIXTURE_TENANT,
        "user_id": ATTRIBUTION_FIXTURE_PRINCIPAL,
        "user_name": ATTRIBUTION_FIXTURE_PRINCIPAL,
        "groups": [] if group_overage else list(groups),
        "groups_overage": group_overage,
        "access_token": "redacted-user-assertion",
    }


def make_attribution_result_row(
    index: int = 0,
    *,
    department_id: str | None = "engineering",
    **overrides: Any,
) -> dict[str, Any]:
    """Return one normalized synthetic attribution row."""
    row: dict[str, Any] = {
        "user_key": make_attribution_user_key(index),
        "raw_identity": (
            ATTRIBUTION_FIXTURE_PRINCIPAL
            if index == 0
            else f"synthetic-user-{index + 1}@example.test"
        ),
        "department_id": department_id,
        "department_label": (
            department_id.replace("-", " ").title() if department_id else None
        ),
        "invocations": index + 1,
        "input_tokens": (index + 1) * 10,
        "output_tokens": (index + 1) * 5,
        "tool_invocations": index % 3,
    }
    row.update(overrides)
    return row


def make_high_cardinality_attribution_rows(
    count: int = 501,
) -> list[dict[str, Any]]:
    """Return enough deterministic rows to exercise the 499-plus-Other bound."""
    if count < 0:
        raise ValueError("count must be non-negative")
    return [
        make_attribution_result_row(
            index,
            department_id="engineering" if index % 2 == 0 else "finance",
        )
        for index in range(count)
    ]


@dataclass
class FakeClock:
    current: datetime = field(
        default_factory=lambda: datetime(2026, 8, 21, tzinfo=timezone.utc)
    )

    def __call__(self) -> datetime:
        return self.current

    def advance(self, *, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


@dataclass
class FakeResourceGraphClient:
    rows: list[dict[str, Any]] = field(default_factory=list)
    error: Exception | None = None
    requests: list[Any] = field(default_factory=list)

    def resources(self, request: Any) -> Any:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(data=self.rows, skip_token=None)


@dataclass
class FakeFoundryConnections:
    values: list[Any] = field(default_factory=list)
    list_calls: int = 0

    def list(self) -> list[Any]:
        self.list_calls += 1
        return list(self.values)


@dataclass
class FakeLogsQueryClient:
    response: Any
    requests: list[Any] = field(default_factory=list)

    def query_batch(self, requests: list[Any]) -> Any:
        self.requests.extend(requests)
        return self.response


@dataclass
class FakeEasyAuthContext:
    tenant_id: str = "22222222-2222-2222-2222-222222222222"
    user_id: str = "11111111-1111-1111-1111-111111111111"
    user_assertion: str = "redacted-user-assertion"
    groups: tuple[str, ...] = ()


@dataclass
class FakeOboCredential:
    tokens: list[str] = field(default_factory=list)

    def get_token(self, *scopes: str, **_: Any) -> Any:
        self.tokens.extend(scopes)
        return SimpleNamespace(token="redacted-access-token", expires_on=2_000_000_000)
