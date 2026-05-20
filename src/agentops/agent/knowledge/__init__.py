"""WAF AI Landing Zones knowledge base for the Doctor agent.

This package ships a CSV (`waf-checklist.csv`) that maps every Doctor
finding id to the Microsoft Well-Architected Framework (WAF) for AI
workloads pillar/area it belongs to.

The shipped CSV is the **packaged baseline**. Users can override or
extend it on a per-workspace basis by dropping a file at
``.agentops/waf-checklist.csv`` (created by ``agentops init``). The
loader merges the two: rows in the workspace file override packaged
rows when ``doctor_check_id`` matches, and add new rows when the id
is new. This keeps the toolkit's baseline updateable while letting
teams version their own rules alongside the project.

Strict scope rule: a row only exists if Doctor itself can produce
that finding. Items that are exclusively visible through Foundry
Operate -> Compliance (public network, private endpoints,
content-filter attachment, custom subdomain) are intentionally *not*
in the packaged CSV - they belong to Foundry's surface, not the
Doctor's.

The CSV's ``doctor_check_id`` column may carry either a fully
qualified finding id (``opex.no_pr_gate``) or a prefix
(``regression`` covers ``regression.coherence``,
``regression.fluency``, ...). Lookups in :func:`find_waf_item` walk
the dot segments from longest to shortest so the most specific row
wins.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

_CSV_NAME = "waf-checklist.csv"
WORKSPACE_OVERRIDE_PATH = Path(".agentops") / "waf-checklist.csv"
_EXPECTED_COLUMNS = (
    "pillar",
    "area",
    "item_id",
    "title",
    "detection_source",
    "detection_signal",
    "doctor_check_id",
    "status",
    "reference_url",
)


@dataclass(frozen=True)
class WAFItem:
    """One row of the WAF checklist."""

    pillar: str
    area: str
    item_id: str
    title: str
    detection_source: str
    detection_signal: str
    doctor_check_id: str
    status: str
    reference_url: str


def _row_to_item(row: Dict[str, str]) -> Optional[WAFItem]:
    check_id = (row.get("doctor_check_id") or "").strip()
    if not check_id:
        return None
    return WAFItem(
        pillar=(row.get("pillar") or "").strip(),
        area=(row.get("area") or "").strip(),
        item_id=(row.get("item_id") or "").strip(),
        title=(row.get("title") or "").strip(),
        detection_source=(row.get("detection_source") or "").strip(),
        detection_signal=(row.get("detection_signal") or "").strip(),
        doctor_check_id=check_id,
        status=(row.get("status") or "").strip(),
        reference_url=(row.get("reference_url") or "").strip(),
    )


def _parse_csv_text(label: str, text: str) -> List[WAFItem]:
    reader = csv.DictReader(text.splitlines())
    missing = [c for c in _EXPECTED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        log.warning("WAF checklist %s is missing columns: %s", label, missing)
        return []
    items: List[WAFItem] = []
    for row in reader:
        item = _row_to_item(row)
        if item is not None:
            items.append(item)
    return items


@lru_cache(maxsize=1)
def _packaged_items() -> List[WAFItem]:
    """Load the packaged baseline (cached - ships in the wheel)."""
    try:
        text = (
            resources.files(__name__).joinpath(_CSV_NAME).read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        log.warning("Packaged WAF checklist not found")
        return []
    except OSError as exc:
        log.warning("Packaged WAF checklist could not be read: %s", exc)
        return []
    return _parse_csv_text("packaged", text)


def _workspace_items(workspace: Path) -> List[WAFItem]:
    """Load the workspace override file. NOT cached - users edit it in place."""
    override = workspace / WORKSPACE_OVERRIDE_PATH
    if not override.is_file():
        return []
    try:
        text = override.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("Workspace WAF checklist at %s unreadable: %s", override, exc)
        return []
    # Strip comment lines (#-prefixed) before parsing as CSV.
    cleaned = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    return _parse_csv_text(f"workspace ({override})", cleaned)


def load_waf_checklist(workspace: Optional[Path] = None) -> List[WAFItem]:
    """Load the merged WAF checklist (packaged + optional workspace override).

    Workspace rows override packaged rows by ``doctor_check_id`` and
    add new ids that the packaged file does not carry. The packaged
    baseline alone is returned when ``workspace`` is ``None`` or has
    no override file.
    """
    items_by_id: Dict[str, WAFItem] = {
        item.doctor_check_id: item for item in _packaged_items()
    }
    if workspace is not None:
        for item in _workspace_items(workspace):
            items_by_id[item.doctor_check_id] = item
    return list(items_by_id.values())


def waf_index_by_check_id(
    workspace: Optional[Path] = None,
) -> Dict[str, WAFItem]:
    """Return a `{doctor_check_id: WAFItem}` map for quick reporter lookup."""
    return {item.doctor_check_id: item for item in load_waf_checklist(workspace)}


def find_waf_item(
    finding_id: str, workspace: Optional[Path] = None
) -> Optional[WAFItem]:
    """Return the WAF row matching a finding id (most specific prefix wins).

    Walks the dot segments from longest to shortest. For
    ``safety.runtime.content_filter`` we try the full id first, then
    ``safety.runtime``, then ``safety``. The first match is returned.

    When ``workspace`` is provided, workspace-level overrides take
    precedence (see :func:`load_waf_checklist`).
    """
    if not finding_id:
        return None
    index = waf_index_by_check_id(workspace)
    parts = finding_id.split(".")
    while parts:
        candidate = ".".join(parts)
        item = index.get(candidate)
        if item is not None:
            return item
        parts.pop()
    return None
