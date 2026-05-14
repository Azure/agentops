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


def test_build_cards_tokens_per_model_breakdown():
    """When multiple models contribute, the Tokens card aggregates the
    total and surfaces a per-model bullet list in the help tooltip."""
    summary = {"rows": [{"invocations": 10, "errors": 0, "avg_ms": 100, "p95_ms": 500}]}
    tokens = {"rows": [
        {"model_name": "gpt-4o-mini", "input_tokens": 12000, "output_tokens": 4000},
        {"model_name": "text-embedding-3-small", "input_tokens": 8000, "output_tokens": 0},
    ]}
    cards = _build_cards(summary, {}, {}, tokens)
    tok = next(c for c in cards if c["key"] == "prod_tokens")

    # Grand total = 12000 + 4000 + 8000 + 0 = 24000 → "24.0k"
    assert tok["value"] == "24.0k"
    # Unit mentions both the in/out split and how many models contributed.
    assert "across 2 models" in tok["unit"]
    # Per-model breakdown lives in the help text as bullets.
    assert "gpt-4o-mini" in tok["help"]
    assert "text-embedding-3-small" in tok["help"]
    assert "Per-model breakdown" in tok["help"]
    # Source footer mentions aggregation across deployments.
    assert "across 2 deployments" in tok["source"]


def test_build_cards_tokens_single_model_includes_breakdown():
    """A single-model deployment should still surface a per-model
    breakdown in the tooltip — the documented behavior is that the
    Tokens card *always* breaks the grand total down per deployment,
    regardless of model count."""
    summary = {"rows": [{"invocations": 5, "errors": 0, "avg_ms": 100, "p95_ms": 500}]}
    tokens = {"rows": [
        {"model_name": "gpt-4o-mini", "input_tokens": 1000, "output_tokens": 500},
    ]}
    cards = _build_cards(summary, {}, {}, tokens)
    tok = next(c for c in cards if c["key"] == "prod_tokens")
    # No "across N models" suffix when there is only one.
    assert "across" not in tok["unit"]
    # But the per-model breakdown bullet list IS present.
    assert "Per-model breakdown" in tok["help"]
    assert "gpt-4o-mini" in tok["help"]


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


def test_collect_does_not_cache_empty_results():
    """A transient query failure must not poison the cache with empty
    results — the next call must retry instead of serving stale empties."""
    app_id = "no-cache-empty-id"
    call_count = {"n": 0}

    def fake_run_query(_app_id, _bearer, _kql):
        call_count["n"] += 1
        return None  # simulate transient failure

    with mock.patch(
        "agentops.agent.production_telemetry._acquire_token",
        return_value="fake-bearer",
    ), mock.patch(
        "agentops.agent.production_telemetry._run_query",
        side_effect=fake_run_query,
    ):
        from agentops.agent import production_telemetry as pt
        pt._cache.pop(f"{app_id}:24", None)

        first = collect_production_metrics(app_id)
        # _run_query is called 4 times per collect (summary, invocations, latency, tokens).
        assert call_count["n"] == 4
        assert first["has_data"] is False
        assert "reason" in first["diagnostics"]

        # Second call retries from scratch instead of serving the cached empty.
        second = collect_production_metrics(app_id)
        assert call_count["n"] == 8
        assert second["has_data"] is False


def test_collect_does_not_cache_zero_invocation_results():
    """When the query succeeds but returns zero matching rows, the result
    is reported with a clear diagnostic and not cached."""
    app_id = "zero-invocations-id"
    call_count = {"n": 0}

    def fake_run_query(_app_id, _bearer, _kql):
        call_count["n"] += 1
        # Summary KQL with summarize-no-by returns 1 row of zeros, but we
        # simulate the case where _build_cards still produces nothing
        # because the row aggregates are all empty / zero-rows.
        return {"rows": []}

    with mock.patch(
        "agentops.agent.production_telemetry._acquire_token",
        return_value="fake-bearer",
    ), mock.patch(
        "agentops.agent.production_telemetry._run_query",
        side_effect=fake_run_query,
    ):
        from agentops.agent import production_telemetry as pt
        pt._cache.pop(f"{app_id}:24", None)

        first = collect_production_metrics(app_id)
        first_calls = call_count["n"]
        assert first["has_data"] is False
        assert first["diagnostics"].get("reason")

        # No cache → second call hits the API again.
        collect_production_metrics(app_id)
        assert call_count["n"] > first_calls


def test_run_query_treats_application_insights_error_response_as_failure(monkeypatch):
    """When the App Insights REST API returns HTTP 200 with an `error`
    object (its normal way of reporting KQL failures), `_run_query`
    must return None, not an empty success."""
    from agentops.agent.production_telemetry import _run_query

    class _Resp:
        def __enter__(self):
            return self
        def __exit__(self, *_a):
            return False
        def read(self):
            return b'{"error": {"message": "syntax error in KQL", "code": "BadRequest"}}'

    def fake_urlopen(_req, timeout=None):
        return _Resp()

    monkeypatch.setattr(
        "urllib.request.urlopen", fake_urlopen
    )
    assert _run_query("app-id", "bearer", "bad | kql") is None


def test_humanize_token_error_handles_default_credential_wall_of_text():
    """The DefaultAzureCredential failure message is a 1-2kb wall of text
    citing every credential in the chain. The dashboard must not dump it
    raw into the error tile — surface the actionable `az login` hint
    instead."""
    from agentops.agent.production_telemetry import _humanize_token_error

    raw = (
        "DefaultAzureCredential failed to retrieve a token from the "
        "included credentials. Attempted credentials: "
        "EnvironmentCredential: EnvironmentCredential authentication "
        "unavailable. Environment variables are not fully configured. "
        "...lots of text... AzureCliCredential: Failed to invoke the "
        "Azure CLI AzurePowerShellCredential: Failed to invoke PowerShell. "
        "Enable debug logging for additional information. "
        "InteractiveBrowserCredential unavailable."
    )
    msg = _humanize_token_error(Exception(raw))
    assert "az login" in msg
    assert "DefaultAzureCredential" not in msg  # no wall of text
    assert len(msg) < 300


def test_humanize_token_error_no_cache_accounts():
    from agentops.agent.production_telemetry import _humanize_token_error
    msg = _humanize_token_error(Exception("SharedTokenCacheCredential: No accounts were found in the cache."))
    assert "az login" in msg


def test_humanize_token_error_truncates_unknown_exception():
    from agentops.agent.production_telemetry import _humanize_token_error
    msg = _humanize_token_error(Exception("x" * 1000))
    assert len(msg) <= 300
    assert msg.endswith("...") or "Token acquisition failed" in msg
