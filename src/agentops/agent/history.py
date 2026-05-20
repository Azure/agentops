"""Append-only analysis history for the watchdog agent.

Each ``agentops doctor`` invocation appends one JSON record to
``.agentops/agent/history.jsonl``. The file is the canonical local
storage for the cockpit (``agentops cockpit``) and for any future
trend-based checks. No Azure resource required.

When OpenTelemetry tracing is configured, the same record is also
emitted as a span (see :func:`agentops.utils.telemetry.agent_analyze_span`);
the local JSONL remains authoritative because it works even when
tracing is disabled.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentops.agent.findings import Category, Finding, Severity

_HISTORY_REL_PATH = ".agentops/agent/history.jsonl"


@dataclass
class AnalysisRecord:
    """A single watchdog analysis, captured for the cockpit and trend checks."""

    timestamp: str
    findings_total: int
    findings_by_severity: Dict[str, int]
    findings_by_category: Dict[str, int]
    max_severity: Optional[str]
    sources_enabled: List[str]
    lookback_days: Optional[int]
    duration_seconds: Optional[float]
    findings: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "findings_total": self.findings_total,
            "findings_by_severity": self.findings_by_severity,
            "findings_by_category": self.findings_by_category,
            "max_severity": self.max_severity,
            "sources_enabled": self.sources_enabled,
            "lookback_days": self.lookback_days,
            "duration_seconds": self.duration_seconds,
            "findings": self.findings,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AnalysisRecord":
        return cls(
            timestamp=str(payload.get("timestamp", "")),
            findings_total=int(payload.get("findings_total", 0)),
            findings_by_severity=dict(payload.get("findings_by_severity") or {}),
            findings_by_category=dict(payload.get("findings_by_category") or {}),
            max_severity=payload.get("max_severity"),
            sources_enabled=list(payload.get("sources_enabled") or []),
            lookback_days=payload.get("lookback_days"),
            duration_seconds=payload.get("duration_seconds"),
            findings=list(payload.get("findings") or []),
        )


def history_path(workspace: Path) -> Path:
    """Return the absolute path to the analysis history file."""
    return workspace / _HISTORY_REL_PATH


def build_record(
    findings: List[Finding],
    *,
    sources_enabled: List[str],
    lookback_days: Optional[int],
    duration_seconds: Optional[float],
    timestamp: Optional[datetime] = None,
) -> AnalysisRecord:
    """Reduce a finding list into a serialisable :class:`AnalysisRecord`."""
    now = timestamp or datetime.now(timezone.utc)
    severity_counts = Counter(f.severity.value for f in findings)
    category_counts = Counter(f.category.value for f in findings)
    max_severity = max(findings, key=lambda f: f.severity.rank).severity.value if findings else None

    return AnalysisRecord(
        timestamp=now.isoformat(),
        findings_total=len(findings),
        findings_by_severity={s.value: severity_counts.get(s.value, 0) for s in Severity},
        findings_by_category={c.value: category_counts.get(c.value, 0) for c in Category},
        max_severity=max_severity,
        sources_enabled=list(sources_enabled),
        lookback_days=lookback_days,
        duration_seconds=duration_seconds,
        findings=[f.to_dict() for f in findings],
    )


def append_analysis(workspace: Path, record: AnalysisRecord) -> Path:
    """Append a record to the workspace's history.jsonl. Returns the path."""
    path = history_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    return path


def load_analysis_history(
    workspace: Path,
    *,
    limit: Optional[int] = None,
) -> List[AnalysisRecord]:
    """Load all records (or the most recent ``limit``) from history.jsonl.

    Returns an empty list when the file does not exist, so callers can
    treat history as "best effort" without special-casing first runs.
    Malformed lines are skipped silently rather than crashing the
    cockpit or trend checks.
    """
    path = history_path(workspace)
    if not path.exists():
        return []

    records: List[AnalysisRecord] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except ValueError:
                continue
            if isinstance(payload, dict):
                records.append(AnalysisRecord.from_dict(payload))

    if limit is not None and limit > 0:
        records = records[-limit:]
    return records
