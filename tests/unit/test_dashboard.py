"""Tests for :mod:`agentops.agent.dashboard`."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentops.agent.dashboard import (
    build_dashboard_payload,
    render_dashboard_html,
)
from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.history import append_analysis, build_record
from agentops.agent.time_range import TimeRange


# Tests run against a wide time range so the dashboard filter does not
# accidentally exclude fixture runs based on the test wall clock.
_WIDE = TimeRange(
    key="custom",
    label="test-window",
    start=datetime(2000, 1, 1, tzinfo=timezone.utc),
    end=datetime(2100, 1, 1, tzinfo=timezone.utc),
    hours=24 * 365 * 100,
)


def _dir_to_iso(timestamp_dir: str) -> str:
    """Convert a filesystem-safe timestamp directory (`T20-00-00Z`) into a
    proper ISO-8601 string for the results.json `started_at` field."""
    # ``2026-05-11T20-00-00Z`` → ``2026-05-11T20:00:00+00:00``
    if "T" in timestamp_dir:
        date_part, time_part = timestamp_dir.split("T", 1)
        time_part = time_part.replace("Z", "")
        time_part = time_part.replace("-", ":")
        return f"{date_part}T{time_part}+00:00"
    return timestamp_dir


def _make_history(workspace: Path, *severities_and_categories):
    """Append one record per (severity, category) tuple given."""
    for idx, (sev, cat) in enumerate(severities_and_categories):
        finding = Finding(
            id=f"f-{idx}",
            severity=sev,
            title="t",
            summary="s",
            recommendation="r",
            source="test",
            category=cat,
        )
        record = build_record(
            [finding],
            sources_enabled=["results_history"],
            lookback_days=7,
            duration_seconds=0.5,
        )
        append_analysis(workspace, record)


def _write_eval_run(
    workspace: Path,
    *,
    timestamp_dir: str,
    passed: bool,
    metrics: dict,
    target: str = "agent-smoke:2",
    items_total: int = 3,
    execution: str = "cloud",
    duration: float = 12.3,
    started_at: str | None = None,
) -> None:
    out = workspace / ".agentops" / "results" / timestamp_dir
    out.mkdir(parents=True, exist_ok=True)
    # Real AgentOps writes a proper ISO timestamp into results.json; the
    # directory name is filesystem-safe (no colons) and not parsed.
    iso_ts = started_at or _dir_to_iso(timestamp_dir)
    payload = {
        "version": 1,
        "started_at": iso_ts,
        "finished_at": iso_ts,
        "duration_seconds": duration,
        "target": {"kind": "foundry_prompt", "raw": target},
        "summary": {
            "items_total": items_total,
            "items_passed_all": items_total if passed else 0,
            "overall_passed": passed,
            "items_pass_rate": 1.0 if passed else 0.0,
            "thresholds_total": 4,
            "thresholds_passed": 4 if passed else 2,
            "threshold_pass_rate": 1.0 if passed else 0.5,
        },
        "aggregate_metrics": metrics,
        "config": {"execution": execution},
    }
    (out / "results.json").write_text(json.dumps(payload), encoding="utf-8")


def test_empty_workspace_yields_empty_state(tmp_path: Path):
    payload = build_dashboard_payload(tmp_path, time_range=_WIDE)
    assert payload["eval"]["has_runs"] is False
    assert payload["metrics"] == []
    assert payload["watchdog"]["has_history"] is False
    html = render_dashboard_html(payload)
    assert "No eval runs yet" in html
    assert "No analysis history yet" in html
    assert "agentops eval run" in html


def test_dashboard_loads_eval_runs(tmp_path: Path):
    _write_eval_run(
        tmp_path,
        timestamp_dir="2026-05-11T20-00-00Z",
        passed=True,
        metrics={"coherence": 4.5, "similarity": 4.0, "fluency": 3.7, "f1_score": 0.9},
    )
    _write_eval_run(
        tmp_path,
        timestamp_dir="2026-05-11T21-00-00Z",
        passed=False,
        metrics={"coherence": 4.0, "similarity": 3.0, "fluency": 3.0, "f1_score": 0.6},
        target="agent-smoke:3",
    )

    payload = build_dashboard_payload(tmp_path, time_range=_WIDE)
    assert payload["eval"]["has_runs"] is True
    eval_keys = {c["key"] for c in payload["eval"]["cards"]}
    assert "total_runs" in eval_keys
    assert "pass_rate" in eval_keys
    assert "latest_run" in eval_keys
    # Latest target wins.
    latest_card = next(c for c in payload["eval"]["cards"] if c["key"] == "latest_run")
    assert latest_card["value"] == "agent-smoke:3"
    assert latest_card["badge"]["tone"] == "crit"

    metric_keys = {c["key"] for c in payload["metrics"]}
    assert {"coherence", "similarity", "fluency", "f1_score"} <= metric_keys


def test_pass_rate_badge_reflects_history(tmp_path: Path):
    for i in range(4):
        _write_eval_run(
            tmp_path,
            timestamp_dir=f"2026-05-11T0{i}-00-00Z",
            passed=True,
            metrics={"coherence": 4.0},
        )
    payload = build_dashboard_payload(tmp_path, time_range=_WIDE)
    pass_card = next(c for c in payload["eval"]["cards"] if c["key"] == "pass_rate")
    assert pass_card["value"] == "100%"
    assert pass_card["badge"]["tone"] == "ok"


def test_metric_trend_badge_detects_regression_for_quality(tmp_path: Path):
    _write_eval_run(
        tmp_path, timestamp_dir="2026-05-11T01-00-00Z", passed=True,
        metrics={"coherence": 5.0},
    )
    _write_eval_run(
        tmp_path, timestamp_dir="2026-05-11T02-00-00Z", passed=True,
        metrics={"coherence": 3.0},
    )
    payload = build_dashboard_payload(tmp_path, time_range=_WIDE)
    coh = next(c for c in payload["metrics"] if c["key"] == "coherence")
    assert coh["badge"]["label"] == "regressed"
    assert coh["badge"]["tone"] == "warn"


def test_metric_trend_badge_treats_latency_inversely(tmp_path: Path):
    _write_eval_run(
        tmp_path, timestamp_dir="2026-05-11T01-00-00Z", passed=True,
        metrics={"avg_latency_seconds": 5.0},
    )
    _write_eval_run(
        tmp_path, timestamp_dir="2026-05-11T02-00-00Z", passed=True,
        metrics={"avg_latency_seconds": 2.0},
    )
    payload = build_dashboard_payload(tmp_path, time_range=_WIDE)
    lat = next(c for c in payload["metrics"] if c["key"] == "avg_latency_seconds")
    # Latency dropping is an improvement, not a regression.
    assert lat["badge"]["label"] == "improved"
    assert lat["badge"]["tone"] == "ok"


def test_telemetry_status_reflects_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AGENTOPS_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)

    payload = build_dashboard_payload(tmp_path, time_range=_WIDE)
    assert payload["telemetry"]["enabled"] is False
    assert payload["telemetry"]["source"] == "off"

    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "InstrumentationKey=abc")
    payload = build_dashboard_payload(tmp_path, time_range=_WIDE)
    assert payload["telemetry"]["enabled"] is True
    assert payload["telemetry"]["source"] == "env"


def test_watchdog_section_still_renders_when_no_eval_runs(tmp_path: Path):
    _make_history(tmp_path, (Severity.WARNING, Category.QUALITY))
    payload = build_dashboard_payload(tmp_path, time_range=_WIDE)
    assert payload["watchdog"]["has_history"] is True
    cat_keys = {c["key"] for c in payload["watchdog"]["category_cards"]}
    assert cat_keys == {"quality", "performance", "reliability", "security"}


def test_html_includes_all_sections_when_data_present(tmp_path: Path):
    _write_eval_run(
        tmp_path, timestamp_dir="2026-05-11T01-00-00Z", passed=True,
        metrics={"coherence": 5.0, "fluency": 4.0},
    )
    _make_history(tmp_path, (Severity.INFO, Category.QUALITY))
    payload = build_dashboard_payload(tmp_path, time_range=_WIDE)
    html = render_dashboard_html(payload)
    assert "Evaluation runs" in html
    assert "Quality metrics" in html
    assert "Watchdog findings" in html
    assert "Telemetry" in html
    assert "<svg" in html


def test_create_app_serves_dashboard(tmp_path: Path):
    """FastAPI integration smoke test (skipped if FastAPI not installed)."""
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        import pytest
        pytest.skip("fastapi extras not installed")

    from agentops.agent.dashboard import create_app

    _make_history(tmp_path, (Severity.INFO, Category.QUALITY))
    _write_eval_run(
        tmp_path, timestamp_dir="2026-05-11T01-00-00Z", passed=True,
        metrics={"coherence": 5.0},
    )
    client = TestClient(create_app(tmp_path))

    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "AgentOps dashboard" in r.text
    # Range bar is present.
    assert "range-pills" in r.text
    assert 'range=1d' in r.text and 'range=7d' in r.text and 'range=30d' in r.text

    # Custom range round-trip.
    r = client.get("/?range=custom&from=2020-01-01&to=2030-01-01")
    assert r.status_code == 200

    # Unknown range falls back to 7d default.
    r = client.get("/?range=eternity")
    assert r.status_code == 200

    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

    r = client.get("/api/history")
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = client.get("/api/eval-runs")
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = client.get("/api/telemetry")
    assert r.status_code == 200
    payload = r.json()
    assert "enabled" in payload
    assert "source" in payload

