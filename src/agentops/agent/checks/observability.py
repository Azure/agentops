"""Foundry observability readiness checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List

from agentops.agent.findings import Category, Finding, Severity
from agentops.utils.yaml import load_yaml

SOURCE_NAME = "observability"


def run_observability_check(workspace: Path) -> List[Finding]:
    """Validate repo-side intent for Foundry observability signals.

    These checks are deliberately read-only. Foundry owns the runtime surfaces
    for traces, multi-turn eval, and optional rubric evaluators; AgentOps
    verifies whether the repo has enough metadata and evidence to make those
    signals part of release readiness.
    """

    config = _safe_config(workspace)
    if not config and not (workspace / ".agentops").exists():
        return []

    findings: List[Finding] = []
    findings.extend(_check_multiturn_coverage(config, workspace))
    return findings


def _check_multiturn_coverage(config: dict[str, Any], workspace: Path) -> List[Finding]:
    """Emit a coverage finding only for datasets that are actually multi-turn.

    Multi-turn is a property of the configured dataset, not the project:

    * ``dataset_kind: single-turn`` -> not applicable, no finding.
    * ``dataset_kind: multi-turn`` -> applicable; flag missing coverage.
    * ``dataset_kind: auto`` (or unset) -> infer applicability only from
      readable dataset rows that carry a non-empty ``messages`` conversation
      array. A missing or unreadable dataset is "cannot verify", never
      "missing", so it emits no finding.
    """

    kind = str(config.get("dataset_kind") or "auto")
    if kind == "single-turn":
        return []
    if kind == "multi-turn":
        rows = _load_dataset_rows(config, workspace)
        if _lineage_has_multiturn(workspace) or (rows and _rows_have_conversations(rows)):
            return []
        if rows is None:
            # Dataset missing/unreadable -> cannot verify, never "missing".
            return []
        return [
            Finding(
                id="observability.multiturn_coverage_missing",
                severity=Severity.INFO,
                category=Category.QUALITY,
                title="Multi-turn dataset has no conversation coverage yet",
                summary=(
                    "`dataset_kind: multi-turn` is declared but AgentOps found no "
                    "conversation rows with a `messages` array in the dataset and "
                    "no trace-derived multi-turn rows. Foundry multi-turn "
                    "evaluation needs full conversations to catch context "
                    "carryover, tone drift, contradictions, and task-completion "
                    "failures."
                ),
                recommendation=(
                    "Add conversation rows with a `messages` array to the dataset, "
                    "or use Foundry traces-to-dataset output, then re-run the eval "
                    "gate so multi-turn coverage is scored."
                ),
                source=SOURCE_NAME,
            )
        ]

    # auto / unset: only infer from actual dataset content. A missing or
    # unreadable dataset is unverifiable and must not be reported as missing.
    return []


def _lineage_has_multiturn(workspace: Path) -> bool:
    """Return True when trace lineage records multi-turn conversation rows."""
    manifest = _trace_manifest(workspace)
    lineage = manifest.get("lineage") if isinstance(manifest, dict) else {}
    return isinstance(lineage, dict) and int(lineage.get("multi_turn_rows") or 0) > 0


def _load_dataset_rows(config: dict[str, Any], workspace: Path) -> List[dict[str, Any]] | None:
    """Load JSONL dataset rows relative to the workspace.

    Returns ``None`` when the dataset is not declared, missing, or unreadable
    (the "cannot verify" state). Returns a list of row dicts otherwise.
    """
    dataset = config.get("dataset")
    if not isinstance(dataset, str) or not dataset.strip():
        return None
    if dataset.startswith(("http://", "https://")):
        # Remote datasets are not readable read-only; cannot verify locally.
        return None
    path = workspace / dataset
    if not path.exists() or not path.is_file():
        return None
    rows: List[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            rows.append(record)
    return rows


def _rows_have_conversations(rows: List[dict[str, Any]]) -> bool:
    """A row qualifies as multi-turn only with a non-empty ``messages`` array."""
    for row in rows:
        messages = row.get("messages")
        if isinstance(messages, list) and len(messages) > 0:
            return True
    return False


def _trace_manifest(workspace: Path) -> dict[str, Any]:
    path = workspace / ".agentops" / "data" / "trace-regression-manifest.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_config(workspace: Path) -> dict[str, Any]:
    path = workspace / "agentops.yaml"
    if not path.exists():
        return {}
    try:
        data = load_yaml(path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
