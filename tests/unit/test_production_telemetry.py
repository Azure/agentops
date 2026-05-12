"""Tests for :mod:`agentops.agent.production_telemetry`."""

from __future__ import annotations

from unittest import mock

from agentops.agent.production_telemetry import (
    _build_cards,
    collect_production_metrics,
    extract_application_id,
)


def test_extract_application_id_from_full_connection_string():
    cs = (
        "InstrumentationKey=11111111-2222-3333-4444-555555555555;"
        "IngestionEndpoint=https://eastus2.in.applicationinsights.azure.com/;"
        "ApplicationId=abcdef00-1111-2222-3333-444444444444"
    )
    assert extract_application_id(cs) == "abcdef00-1111-2222-3333-444444444444"


def test_extract_application_id_missing():
    assert extract_application_id("InstrumentationKey=abc") is None
    assert extract_application_id(None) is None
    assert extract_application_id("") is None


def test_collect_returns_empty_when_no_app_id():
    payload = collect_production_metrics(None)
    assert payload["has_data"] is False
    assert payload["cards"] == []


def test_build_cards_from_summary_and_buckets():
    summary = {"rows": [{"invocations": 200, "errors": 4, "avg_ms": 1200, "p95_ms": 4500}]}
    invocations = {"rows": [
        {"timestamp": "2026-05-11T20:00:00Z", "count": 50},
        {"timestamp": "2026-05-11T21:00:00Z", "count": 150},
    ]}
    latency = {"rows": [
        {"timestamp": "2026-05-11T20:00:00Z", "p95_ms": 2000},
        {"timestamp": "2026-05-11T21:00:00Z", "p95_ms": 6000},
    ]}
    tokens = {"rows": [{"input_tokens": 12500, "output_tokens": 3400}]}

    cards = _build_cards(summary, invocations, latency, tokens)
    by_key = {c["key"]: c for c in cards}

    assert by_key["prod_invocations"]["value"] == 200
    # 4/200 = 2% → "watch" tone
    assert by_key["prod_errors"]["value"] == "2%"
    assert by_key["prod_errors"]["badge"]["tone"] == "warn"
    # P95 4.5s → "snappy"
    assert by_key["prod_p95"]["value"] == "4.50"
    assert by_key["prod_p95"]["badge"]["tone"] == "ok"
    # Tokens: 15.9k formatted, value_kind text
    assert by_key["prod_tokens"]["value"] == "15.9k"
    assert by_key["prod_tokens"]["value_kind"] == "text"
    # Sparkline series carry the hourly buckets.
    assert by_key["prod_invocations"]["series"] == [50.0, 150.0]
    # Latency series is scaled to seconds.
    assert by_key["prod_p95"]["series"] == [2.0, 6.0]


def test_build_cards_empty_when_no_summary_rows():
    cards = _build_cards({"rows": []}, {}, {}, {})
    assert cards == []


def test_error_rate_badge_thresholds():
    # 0 errors -> healthy
    cards = _build_cards(
        {"rows": [{"invocations": 100, "errors": 0, "avg_ms": 1000, "p95_ms": 2000}]},
        {}, {}, {},
    )
    by_key = {c["key"]: c for c in cards}
    assert by_key["prod_errors"]["badge"]["tone"] == "ok"

    # 10% errors -> elevated
    cards = _build_cards(
        {"rows": [{"invocations": 100, "errors": 10, "avg_ms": 1000, "p95_ms": 2000}]},
        {}, {}, {},
    )
    by_key = {c["key"]: c for c in cards}
    assert by_key["prod_errors"]["badge"]["tone"] == "crit"


def test_collect_caches_per_app_id():
    """Two back-to-back calls with the same app_id hit the API once."""
    app_id = "cache-test-id"
    summary_rows = {"rows": [{"invocations": 1, "errors": 0, "avg_ms": 0, "p95_ms": 0}]}

    call_count = {"n": 0}

    def fake_run_query(_app_id, _bearer, _kql):
        call_count["n"] += 1
        return summary_rows

    with mock.patch(
        "agentops.agent.production_telemetry._acquire_token",
        return_value="fake-bearer",
    ), mock.patch(
        "agentops.agent.production_telemetry._run_query",
        side_effect=fake_run_query,
    ):
        from agentops.agent import production_telemetry as pt
        pt._cache.pop(app_id, None)
        first = collect_production_metrics(app_id)
        first_calls = call_count["n"]
        second = collect_production_metrics(app_id)
        # Second call must be served from cache → no additional API hits.
        assert call_count["n"] == first_calls
        assert first == second
