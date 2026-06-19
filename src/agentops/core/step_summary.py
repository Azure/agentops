"""Helpers for surfacing AgentOps gate output in CI job summaries.

When AgentOps runs inside GitHub Actions, the ``GITHUB_STEP_SUMMARY``
environment variable points at a Markdown file whose contents render on the
workflow run page. These helpers append AgentOps output there so reviewers can
read the rendered report directly on the run page, without downloading the
uploaded artifacts.

All writes are best-effort and never raise, so command handlers can call them
unconditionally: outside GitHub Actions they simply do nothing.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _summary_path() -> Optional[Path]:
    raw = os.environ.get("GITHUB_STEP_SUMMARY")
    if not raw or not raw.strip():
        return None
    return Path(raw)


def is_active() -> bool:
    """Return True when a GitHub Actions step summary target is available."""
    return _summary_path() is not None


def append_step_summary(markdown: str) -> bool:
    """Append a Markdown block to the GitHub Actions step summary.

    Returns ``True`` when the block was written, ``False`` when not running
    under GitHub Actions or when the write failed. Never raises.
    """
    path = _summary_path()
    if path is None:
        return False
    try:
        text = markdown if markdown.endswith("\n") else markdown + "\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)
            handle.write("\n")
        return True
    except Exception:
        return False


def append_report_file(report_path: Path, *, heading: Optional[str] = None) -> bool:
    """Append the contents of a rendered report file to the step summary.

    Used for ``agentops eval run`` so the full ``report.md`` renders inline on
    the workflow run page. Returns ``False`` when not under GitHub Actions or
    when the report cannot be read. Never raises.
    """
    if _summary_path() is None:
        return False
    try:
        body = Path(report_path).read_text(encoding="utf-8")
    except Exception:
        return False
    block = body if heading is None else f"{heading}\n\n{body}"
    return append_step_summary(block)
