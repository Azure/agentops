"""Detector protocol and shared data classes for spec-conformance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Protocol


@dataclass
class SpecTask:
    """A single checklist item extracted from a spec document."""

    text: str
    checked: bool
    line: int
    mentioned_paths: List[str] = field(default_factory=list)


@dataclass
class SpecDocument:
    """Unified view of a project's spec, populated by a :class:`Detector`.

    The structure is intentionally narrow: every field is either a list
    of strings (extracted via simple regex heuristics) or a primitive,
    so detectors stay deterministic and easy to test.
    """

    format: str
    root: Path
    files: List[Path] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
    tasks: List[SpecTask] = field(default_factory=list)
    references: Dict[str, List[str]] = field(default_factory=dict)
    text: str = ""
    last_modified: Optional[datetime] = None

    @property
    def is_empty(self) -> bool:
        return not (self.capabilities or self.tasks or self.text.strip())


class Detector(Protocol):
    """Protocol every spec-format detector must satisfy."""

    name: str

    def hint_paths(self, workspace: Path) -> List[Path]:
        """Paths that, if present, suggest the user *intends* to use
        this spec format even when the actual content is missing or
        empty (e.g. ``.specify/`` directory with no spec.md). Used by
        the doctor to fire a ``spec_missing`` finding instead of
        silently skipping the check."""

    def detect(self, workspace: Path) -> Optional[SpecDocument]:
        """Inspect ``workspace`` and return a :class:`SpecDocument`
        when this format is detected. Return ``None`` to opt out."""
