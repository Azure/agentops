"""Bounded in-process caches for non-sensitive Observe data."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, Generic, Hashable, Literal, TypeVar


class SensitiveValueError(ValueError):
    """Raised when raw generative-AI content is offered to a shared cache."""


_SENSITIVE_KEYS = {
    "inputmessages",
    "input_messages",
    "outputmessages",
    "output_messages",
    "systeminstructions",
    "system_instructions",
    "toolcontent",
    "tool_content",
    "evaluationexplanation",
    "evaluation_explanation",
}


def _contains_sensitive_value(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                return True
            if _contains_sensitive_value(item):
                return True
    elif isinstance(value, (list, tuple, set)):
        return any(_contains_sensitive_value(item) for item in value)
    return False


K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


@dataclass(frozen=True)
class _Entry(Generic[V]):
    created_at: float
    value: V


@dataclass(frozen=True)
class CacheLookup(Generic[V]):
    """One cache lookup, including whether an expired value is still usable."""

    state: Literal["fresh", "stale", "miss"]
    value: V | None = None


class ObserveCache(Generic[K, V]):
    """Thread-safe TTL/LRU cache that refuses sensitive Observe values."""

    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_entries: int = 256,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[K, _Entry[V]] = OrderedDict()
        self._lock = RLock()

    def get(self, key: K, *, bypass: bool = False) -> V | None:
        return self.lookup(key, bypass=bypass).value

    def lookup(
        self,
        key: K,
        *,
        bypass: bool = False,
        max_stale_seconds: float = 0,
    ) -> CacheLookup[V]:
        """Return a fresh or explicitly allowed stale value for *key*."""
        if bypass:
            return CacheLookup(state="miss")
        if max_stale_seconds < 0:
            raise ValueError("max_stale_seconds cannot be negative")
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return CacheLookup(state="miss")
            age = self._clock() - entry.created_at
            if age >= self._ttl_seconds + max_stale_seconds:
                del self._entries[key]
                return CacheLookup(state="miss")
            self._entries.move_to_end(key)
            if age >= self._ttl_seconds:
                return CacheLookup(state="stale", value=entry.value)
            return CacheLookup(state="fresh", value=entry.value)

    def set(self, key: K, value: V) -> None:
        if _contains_sensitive_value(value):
            raise SensitiveValueError(
                "raw generative-AI content cannot be stored in Observe caches"
            )
        with self._lock:
            self._entries[key] = _Entry(self._clock(), value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
