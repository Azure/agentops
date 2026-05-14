"""Severity-ranked findings produced by the watchdog agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class Category(str, Enum):
    """High-level grouping for a finding.

    Categories are stable user-facing buckets used for filtering and for
    grouping the watchdog report. They are independent of severity:
    a `quality` finding can be `critical`, `warning`, or `info`.

    Categories mirror the **Microsoft Well-Architected Framework for AI**
    pillars and are the single source of truth shared by doctor findings,
    ``waf-checklist.csv`` (``pillar`` column), the dashboard rows, and
    config skip-lists.

    * ``quality``                - eval-driven signals (regression, content-safety)
    * ``performance``            - latency / throughput signals
    * ``reliability``            - error / failure signals
    * ``operational_excellence`` - workspace + CI hygiene, Foundry config
      audit, and spec-conformance signals (pinning, gates, drift,
      versioning, spec-vs-implementation alignment)
    * ``security``               - repo & identity surface beyond Foundry Compliance
    * ``responsible_ai``         - prompt + eval-bundle heuristics for RAI practices
    """

    QUALITY = "quality"
    PERFORMANCE = "performance"
    RELIABILITY = "reliability"
    OPERATIONAL_EXCELLENCE = "operational_excellence"
    SECURITY = "security"
    RESPONSIBLE_AI = "responsible_ai"


class Severity(str, Enum):
    """Severity level for a finding."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    def __lt__(self, other: object) -> bool:  # type: ignore[override]
        if not isinstance(other, Severity):
            return NotImplemented
        return self.rank < other.rank

    def __le__(self, other: object) -> bool:  # type: ignore[override]
        if not isinstance(other, Severity):
            return NotImplemented
        return self.rank <= other.rank

    def __gt__(self, other: object) -> bool:  # type: ignore[override]
        if not isinstance(other, Severity):
            return NotImplemented
        return self.rank > other.rank

    def __ge__(self, other: object) -> bool:  # type: ignore[override]
        if not isinstance(other, Severity):
            return NotImplemented
        return self.rank >= other.rank


_SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.WARNING: 1,
    Severity.CRITICAL: 2,
}


_SEVERITY_EMOJI = {
    Severity.INFO: "ℹ️",
    Severity.WARNING: "⚠️",
    Severity.CRITICAL: "🚨",
}


def severity_emoji(severity: Severity) -> str:
    return _SEVERITY_EMOJI[severity]


@dataclass
class Finding:
    """A single observation the watchdog agent surfaces."""

    id: str
    severity: Severity
    title: str
    summary: str
    recommendation: str
    source: str
    category: Category = Category.QUALITY
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity.value,
            "category": self.category.value,
            "title": self.title,
            "summary": self.summary,
            "recommendation": self.recommendation,
            "source": self.source,
            "evidence": self.evidence,
        }
