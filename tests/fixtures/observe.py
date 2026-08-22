"""Reusable fakes for Observe unit and integration tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any


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
