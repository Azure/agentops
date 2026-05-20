"""Detector for the AGENTS.md / copilot-instructions.md convention."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from agentops.agent.sources.spec_detectors._base import SpecDocument
from agentops.agent.sources.spec_detectors.spec_kit import (
    _extract_capabilities,
    _extract_references,
    _extract_tasks,
)

_CANDIDATE_PATHS = (
    "AGENTS.md",
    ".github/copilot-instructions.md",
    ".github/instructions.md",
    "CLAUDE.md",
)


@dataclass
class AgentsMdDetector:
    """Detector for AGENTS.md-style single-file spec conventions."""

    name: str = "agents-md"

    def hint_paths(self, workspace: Path) -> List[Path]:
        return [workspace / p for p in _CANDIDATE_PATHS if (workspace / p).exists()]

    def detect(self, workspace: Path) -> Optional[SpecDocument]:
        files: List[Path] = []
        chunks: List[str] = []
        latest_mtime: Optional[float] = None

        for relative in _CANDIDATE_PATHS:
            p = workspace / relative
            if not p.is_file():
                continue
            files.append(p)
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            chunks.append(f"# === {relative} ===\n{text}")
            try:
                mtime = p.stat().st_mtime
                if latest_mtime is None or mtime > latest_mtime:
                    latest_mtime = mtime
            except OSError:
                pass

        if not files:
            return None

        merged = "\n\n".join(chunks)
        last_modified = (
            datetime.fromtimestamp(latest_mtime, tz=timezone.utc)
            if latest_mtime is not None
            else None
        )

        return SpecDocument(
            format=self.name,
            root=workspace,
            files=files,
            capabilities=_extract_capabilities(merged),
            tasks=_extract_tasks(merged),
            references=_extract_references(merged),
            text=merged,
            last_modified=last_modified,
        )
