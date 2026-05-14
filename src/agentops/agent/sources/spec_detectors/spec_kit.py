"""Detector for the GitHub spec-kit layout (``.specify/``).

Reads ``spec.md``, ``plan.md``, and ``tasks.md`` from the ``.specify/``
directory at the workspace root. Capabilities are extracted from
markdown headings + leading bullet points; tasks come from
``- [ ]`` / ``- [x]`` checklist items in ``tasks.md``. References
to evaluators, datasets, and agent ids are detected via backticked
identifiers and common AgentOps keywords.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from agentops.agent.sources.spec_detectors._base import SpecDocument, SpecTask

_SPECIFY_DIR = ".specify"
_DOC_NAMES = ("spec.md", "plan.md", "tasks.md")

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+?)\s*$", re.MULTILINE)
_TASK_RE = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.+?)\s*$")
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_EVALUATOR_RE = re.compile(r"\b([A-Z][A-Za-z0-9]*Evaluator)\b")
_AGENT_ID_RE = re.compile(r"\b([a-z][a-z0-9_\-]*:[a-zA-Z0-9._\-]+)\b")
_PATH_RE = re.compile(r"`?(\.?/?[\w\-./]+\.(?:py|md|yaml|yml|jsonl|json|ts|tsx|js))`?")


@dataclass
class SpecKitDetector:
    """Detector for GitHub spec-kit (``.specify/``) projects."""

    name: str = "spec-kit"

    def hint_paths(self, workspace: Path) -> List[Path]:
        base = workspace / _SPECIFY_DIR
        return [base] if base.exists() else []

    def detect(self, workspace: Path) -> Optional[SpecDocument]:
        base = workspace / _SPECIFY_DIR
        if not base.is_dir():
            return None

        files: List[Path] = []
        chunks: List[str] = []
        latest_mtime: Optional[float] = None
        tasks: List[SpecTask] = []

        for name in _DOC_NAMES:
            p = base / name
            if not p.is_file():
                continue
            files.append(p)
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            chunks.append(f"# === {name} ===\n{text}")
            try:
                mtime = p.stat().st_mtime
                if latest_mtime is None or mtime > latest_mtime:
                    latest_mtime = mtime
            except OSError:
                pass
            if name == "tasks.md":
                tasks.extend(_extract_tasks(text))

        if not files:
            return None

        merged = "\n\n".join(chunks)
        capabilities = _extract_capabilities(merged)
        references = _extract_references(merged)
        last_modified = (
            datetime.fromtimestamp(latest_mtime, tz=timezone.utc)
            if latest_mtime is not None
            else None
        )

        return SpecDocument(
            format=self.name,
            root=base,
            files=files,
            capabilities=capabilities,
            tasks=tasks,
            references=references,
            text=merged,
            last_modified=last_modified,
        )


def _extract_capabilities(text: str) -> List[str]:
    """Heuristic capability extraction: top-2 heading levels + first-bullet items."""
    caps: List[str] = []
    seen: set[str] = set()
    for match in _HEADING_RE.finditer(text):
        level = len(match.group(1))
        title = match.group(2).strip().rstrip("#").strip()
        if level <= 2 and title and title.lower() not in seen:
            caps.append(title)
            seen.add(title.lower())
    for match in _BULLET_RE.finditer(text):
        item = match.group(1).strip()
        if item.startswith("[") and "]" in item:
            continue  # task checklist item handled separately
        key = item.lower()
        if key not in seen and len(item) > 4:
            caps.append(item)
            seen.add(key)
    return caps[:60]


def _extract_tasks(text: str) -> List[SpecTask]:
    """Parse ``- [ ]`` / ``- [x]`` checklist items from ``tasks.md``."""
    tasks: List[SpecTask] = []
    for idx, raw_line in enumerate(text.splitlines(), start=1):
        m = _TASK_RE.match(raw_line)
        if not m:
            continue
        mark = m.group(1).lower()
        item = m.group(2).strip()
        paths = list(_PATH_RE.findall(item))
        tasks.append(
            SpecTask(
                text=item,
                checked=(mark == "x"),
                line=idx,
                mentioned_paths=[p for p in paths if p],
            )
        )
    return tasks


def _extract_references(text: str) -> Dict[str, List[str]]:
    """Collect evaluator class names, agent-id-like tokens, and backticked
    identifiers that look like file / module / dataset references."""
    refs: Dict[str, List[str]] = {
        "evaluators": [],
        "agent_ids": [],
        "datasets": [],
        "files": [],
    }
    seen_per_bucket: Dict[str, set[str]] = {k: set() for k in refs}

    def _push(bucket: str, value: str) -> None:
        v = value.strip()
        if not v or v in seen_per_bucket[bucket]:
            return
        seen_per_bucket[bucket].add(v)
        refs[bucket].append(v)

    for m in _EVALUATOR_RE.finditer(text):
        _push("evaluators", m.group(1))

    for m in _AGENT_ID_RE.finditer(text):
        token = m.group(1)
        if token.startswith(("http", "https")) or token in {"int:0", "str:0"}:
            continue
        _push("agent_ids", token)

    for m in _BACKTICK_RE.finditer(text):
        token = m.group(1).strip()
        if "/" in token and "." in token:
            _push("files", token)
        elif token.endswith(".jsonl") or token.endswith(".yaml") or token.endswith(".yml"):
            _push("datasets", token)

    return refs
