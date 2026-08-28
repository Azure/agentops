"""Tests for bounded non-sensitive Observe caches."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agentops.agent.observe.cache import ObserveCache, SensitiveValueError


@dataclass
class FakeClock:
    now: float = 100.0

    def __call__(self) -> float:
        return self.now


def test_ttl_identity_keying_refresh_and_eviction() -> None:
    clock = FakeClock()
    cache = ObserveCache(ttl_seconds=10, max_entries=2, clock=clock)

    cache.set(("scope", "identity-a"), {"value": 1})
    assert cache.get(("scope", "identity-a")) == {"value": 1}
    assert cache.get(("scope", "identity-b")) is None
    assert cache.get(("scope", "identity-a"), bypass=True) is None

    clock.now += 11
    assert cache.get(("scope", "identity-a")) is None

    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    assert cache.get("a") is None
    assert cache.get("c") == 3


def test_lookup_distinguishes_fresh_stale_and_miss() -> None:
    clock = FakeClock()
    cache = ObserveCache(ttl_seconds=10, clock=clock)
    cache.set("key", {"value": 1})

    assert cache.lookup("key").state == "fresh"
    clock.now += 11
    stale = cache.lookup("key", max_stale_seconds=5)
    assert stale.state == "stale"
    assert stale.value == {"value": 1}
    clock.now += 5
    assert cache.lookup("key", max_stale_seconds=5).state == "miss"


@pytest.mark.parametrize(
    "value",
    [
        {"input_messages": ["private"]},
        {"outputMessages": ["private"]},
        {"system_instructions": "private"},
        {"evaluation_explanation": "private"},
        {"nested": {"tool_content": {"result": "private"}}},
    ],
)
def test_sensitive_values_are_rejected(value: object) -> None:
    cache = ObserveCache(ttl_seconds=10)
    with pytest.raises(SensitiveValueError):
        cache.set("unsafe", value)
