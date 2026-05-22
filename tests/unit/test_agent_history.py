"""Tests for :mod:`agentops.agent.history`."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.history import (
    AnalysisRecord,
    append_analysis,
    build_record,
    history_path,
    load_analysis_history,
)


def _finding(severity: Severity, category: Category, idx: int = 0) -> Finding:
    return Finding(
        id=f"f-{idx}",
        severity=severity,
        title=f"finding-{idx}",
        summary="x",
        recommendation="y",
        source="test",
        category=category,
    )


def test_build_record_counts_severity_and_category():
    findings = [
        _finding(Severity.INFO, Category.QUALITY, 0),
        _finding(Severity.WARNING, Category.QUALITY, 1),
        _finding(Severity.CRITICAL, Category.SECURITY, 2),
    ]
    record = build_record(
        findings,
        sources_enabled=["results_history", "azure_monitor"],
        lookback_days=7,
        duration_seconds=3.14,
        timestamp=datetime(2026, 5, 11, 18, 22, tzinfo=timezone.utc),
    )
    assert record.findings_total == 3
    assert record.findings_by_severity == {"info": 1, "warning": 1, "critical": 1}
    assert record.findings_by_category["quality"] == 2
    assert record.findings_by_category["security"] == 1
    assert record.findings_by_category["performance"] == 0
    assert record.max_severity == "critical"
    assert record.sources_enabled == ["results_history", "azure_monitor"]
    assert record.lookback_days == 7
    assert record.duration_seconds == 3.14
    assert record.timestamp.startswith("2026-05-11T18:22")


def test_build_record_empty_findings():
    record = build_record(
        [], sources_enabled=["results_history"], lookback_days=None, duration_seconds=0.0,
    )
    assert record.findings_total == 0
    assert record.max_severity is None
    assert record.findings_by_severity == {"info": 0, "warning": 0, "critical": 0}
    assert all(v == 0 for v in record.findings_by_category.values())


def test_append_then_load_roundtrip(tmp_path: Path):
    findings = [_finding(Severity.CRITICAL, Category.PERFORMANCE, 0)]
    record = build_record(
        findings, sources_enabled=["results_history"], lookback_days=14, duration_seconds=1.2,
    )
    out = append_analysis(tmp_path, record)
    assert out == history_path(tmp_path)
    assert out.exists()

    loaded = load_analysis_history(tmp_path)
    assert len(loaded) == 1
    assert isinstance(loaded[0], AnalysisRecord)
    assert loaded[0].findings_total == 1
    assert loaded[0].max_severity == "critical"


def test_load_history_returns_empty_when_missing(tmp_path: Path):
    assert load_analysis_history(tmp_path) == []


def test_load_history_respects_limit(tmp_path: Path):
    for i in range(5):
        record = build_record(
            [_finding(Severity.INFO, Category.QUALITY, i)],
            sources_enabled=["results_history"],
            lookback_days=7,
            duration_seconds=0.1,
        )
        append_analysis(tmp_path, record)

    loaded = load_analysis_history(tmp_path, limit=2)
    assert len(loaded) == 2
    # Limit returns the most recent records.
    assert loaded[0].findings[0]["id"] == "f-3"
    assert loaded[1].findings[0]["id"] == "f-4"


def test_load_history_skips_malformed_lines(tmp_path: Path):
    path = history_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    valid = build_record(
        [], sources_enabled=["results_history"], lookback_days=7, duration_seconds=0.1,
    ).to_dict()
    path.write_text(
        json.dumps(valid) + "\n"
        "not-a-json-line\n"
        "\n"
        + json.dumps(valid) + "\n",
        encoding="utf-8",
    )

    loaded = load_analysis_history(tmp_path)
    # Two valid records, malformed line skipped silently.
    assert len(loaded) == 2
