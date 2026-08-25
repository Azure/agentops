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
