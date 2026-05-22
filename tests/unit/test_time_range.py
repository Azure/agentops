"""Tests for :mod:`agentops.agent.time_range`."""

from __future__ import annotations

from datetime import datetime, timezone

from agentops.agent.time_range import parse_time_range, preset_keys


_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc)


def test_default_is_seven_days():
    r = parse_time_range(now=_NOW)
    assert r.key == "7d"
    assert r.hours == 24 * 7
    assert r.label == "Last 7 days"
    assert r.end == _NOW
    assert (r.end - r.start).days == 7


def test_one_day_preset():
    r = parse_time_range("1d", now=_NOW)
    assert r.key == "1d"
    assert r.hours == 24
    assert (r.end - r.start).days == 1


def test_thirty_day_preset():
    r = parse_time_range("30d", now=_NOW)
    assert r.key == "30d"
    assert r.hours == 24 * 30


def test_unknown_preset_falls_back_to_seven_days():
    r = parse_time_range("eternity", now=_NOW)
    assert r.key == "7d"


def test_custom_range_inclusive_end():
    r = parse_time_range("custom", "2026-05-01", "2026-05-08", now=_NOW)
    assert r.key == "custom"
    assert r.start == datetime(2026, 5, 1, tzinfo=timezone.utc)
    # End date is treated as inclusive (rolled forward to next day).
    assert r.end == datetime(2026, 5, 9, tzinfo=timezone.utc)


def test_custom_range_invalid_falls_back():
    r = parse_time_range("custom", "not-a-date", "2026-05-08", now=_NOW)
    assert r.key == "7d"


def test_custom_range_inverted_falls_back():
    """Custom range where to <= from must fall back, not crash."""
    r = parse_time_range("custom", "2026-05-08", "2026-05-01", now=_NOW)
    assert r.key == "7d"


def test_contains_respects_window():
    r = parse_time_range("7d", now=_NOW)
    inside = _NOW - __import__("datetime").timedelta(days=2)
    outside = _NOW - __import__("datetime").timedelta(days=10)
    assert r.contains(inside) is True
    assert r.contains(outside) is False
    assert r.contains(None) is False


def test_contains_treats_naive_as_utc():
    r = parse_time_range("7d", now=_NOW)
    naive = (_NOW - __import__("datetime").timedelta(days=2)).replace(tzinfo=None)
    assert r.contains(naive) is True


def test_to_query_round_trip_for_preset():
    r = parse_time_range("30d", now=_NOW)
    assert r.to_query() == "range=30d"


def test_to_query_for_custom():
    r = parse_time_range("custom", "2026-05-01", "2026-05-08", now=_NOW)
    assert r.to_query() == "range=custom&from=2026-05-01&to=2026-05-09"


def test_preset_keys_order():
    assert list(preset_keys()) == ["1d", "7d", "30d"]
