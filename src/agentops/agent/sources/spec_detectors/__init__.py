"""Spec-conformance detectors.

Each detector inspects a workspace for a particular spec format and,
if present, returns a :class:`SpecDocument` describing the captured
capabilities, tasks, and references. The doctor runs every registered
detector that matches; ``checks/spec_conformance.py`` consumes the
results.

Detectors are intentionally light:

* No LLM calls — heuristics only (headings, bullets, checklists,
  backticked identifiers).
* No network I/O.
* Safe on partial workspaces — return ``None`` whenever the format
  isn't present rather than raising.

Adding a new spec format means dropping a new module in this package
and registering it in :data:`DETECTORS`.
"""

from __future__ import annotations

from agentops.agent.sources.spec_detectors._base import (
    Detector,
    SpecDocument,
    SpecTask,
)
from agentops.agent.sources.spec_detectors.agents_md import AgentsMdDetector
from agentops.agent.sources.spec_detectors.spec_kit import SpecKitDetector

DETECTORS: tuple[Detector, ...] = (
    SpecKitDetector(),
    AgentsMdDetector(),
)


__all__ = [
    "DETECTORS",
    "Detector",
    "SpecDocument",
    "SpecTask",
]
