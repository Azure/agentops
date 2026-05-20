"""Time range parsing for the AgentOps cockpit.

The cockpit supports three preset windows (``1d``, ``7d``, ``30d``)
plus a ``custom`` mode driven by ``from`` / ``to`` ISO date strings.
The parsing is intentionally tolerant: any unknown value falls back to
the 7-day default so the cockpit never breaks on bad query strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional


@dataclass(frozen=True)
class TimeRange:
    """A resolved time window for filtering cockpit data."""

    key: str            # "1d" | "7d" | "30d" | "custom"
    label: str          # display label, e.g. "Last 24h"
    start: datetime     # UTC, inclusive
    end: datetime       # UTC, exclusive
    hours: int          # convenience: rounded duration in hours, used by KQL

    def contains(self, ts: Optional[datetime]) -> bool:
        """Return True if ``ts`` (UTC) falls inside this window."""
        if ts is None:
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return self.start <= ts < self.end

    def to_query(self) -> str:
        """Return the URL query string that reproduces this range."""
        if self.key == "custom":
            return (
                f"range=custom"
                f"&from={self.start.strftime('%Y-%m-%d')}"
                f"&to={self.end.strftime('%Y-%m-%d')}"
            )
        return f"range={self.key}"


_PRESET_HOURS = {
    "1d": 24,
    "7d": 24 * 7,
    "30d": 24 * 30,
}

_PRESET_LABELS = {
    "1d": "Last 24h",
    "7d": "Last 7 days",
    "30d": "Last 30 days",
}


def parse_time_range(
    range_param: Optional[str] = None,
    from_param: Optional[str] = None,
    to_param: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
) -> TimeRange:
    """Parse the URL ``range``, ``from``, ``to`` params into a TimeRange.

    Falls back to the ``7d`` preset on any malformed input.
    """
    current = now or datetime.now(timezone.utc)
    key = (range_param or "7d").lower().strip()

    if key == "custom":
        start = _parse_iso_date(from_param)
        end = _parse_iso_date(to_param)
        if start and end and end > start:
            # Inclusive end-of-day for the "to" date.
            end = end + timedelta(days=1)
            hours = max(int((end - start).total_seconds() // 3600), 1)
            return TimeRange(
                key="custom",
                label=f"{start.strftime('%Y-%m-%d')} → {end.strftime('%Y-%m-%d')}",
                start=start,
                end=end,
                hours=hours,
            )
        # Invalid custom params → fall through to default.
        key = "7d"

    if key not in _PRESET_HOURS:
        key = "7d"

    hours = _PRESET_HOURS[key]
    return TimeRange(
        key=key,
        label=_PRESET_LABELS[key],
        start=current - timedelta(hours=hours),
        end=current,
        hours=hours,
    )


def preset_keys() -> Iterable[str]:
    """Iterate the supported preset keys, in display order."""
    return ("1d", "7d", "30d")


def _parse_iso_date(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    text = text.strip()
    # Accept either "2026-05-12" or full ISO-8601.
    try:
        if len(text) == 10:
            return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
