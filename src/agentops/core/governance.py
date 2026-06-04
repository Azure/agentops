"""Read-only governance artifact discovery for ASSERT, ACS, and red-team evidence.

The external ASSERT and ACS schemas are intentionally not treated as stable
AgentOps contracts. AgentOps recognizes a small set of durable metadata fields,
preserves unknown schema changes by ignoring them, and only raises parse errors
when an artifact is structurally unreadable. Summaries are evidence-oriented and
never include red-team payload text.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

SUMMARY_SCHEMA_VERSION = 1
MAX_ARTIFACT_BYTES = 5 * 1024 * 1024

_ASSERT_PATTERNS = (
    ".assert/*.yml",
    ".assert/*.yaml",
    ".assert/*.json",
    "assert/*.yml",
    "assert/*.yaml",
    "assert/*.json",
    "*assert*.yml",
    "*assert*.yaml",
    "*assert*.json",
)
_ACS_PATTERNS = ("acs.yml", "acs.yaml", "agent-control.yml", "agent-control.yaml", ".acs/*.yml", ".acs/*.yaml")
_REDTEAM_PATTERNS = (
    ".agentops/governance/redteam-plan.md",
    ".agentops/governance/redteam-results.*",
    "redteam-plan.md",
    "redteam-results.*",
    "red-team-plan.md",
    "red-team-results.*",
)
_ACS_CHECKPOINT_ALIASES = {
    "input": "input",
    "prompt": "input",
    "llm": "llm",
    "model": "llm",
    "state": "state",
    "memory": "state",
    "tool": "tool",
    "tools": "tool",
    "tool_execution": "tool",
    "output": "output",
    "response": "output",
}
_ACS_REQUIRED_CHECKPOINTS = ("input", "llm", "state", "tool", "output")


class GovernanceArtifactError(ValueError):
    """Raised when a configured governance artifact cannot be summarized."""


@dataclass(frozen=True)
class GovernanceArtifactSummary:
    """Stable summary of one governance artifact family."""

    kind: str
    status: str
    path: Optional[str] = None
    sha256: Optional[str] = None
    size_bytes: Optional[int] = None
    schema_version: str = "unknown"
    name: Optional[str] = None
    configured: bool = False
    message: Optional[str] = None
    counts: dict[str, int] = field(default_factory=dict)
    checkpoints_covered: tuple[str, ...] = ()
    checkpoints_missing: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary_schema_version": SUMMARY_SCHEMA_VERSION,
            "kind": self.kind,
            "status": self.status,
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "schema_version": self.schema_version,
            "name": self.name,
            "configured": self.configured,
            "message": self.message,
            "counts": dict(self.counts),
            "checkpoints_covered": list(self.checkpoints_covered),
            "checkpoints_missing": list(self.checkpoints_missing),
        }


def summarize_assert(workspace: Path, configured: Any = None) -> GovernanceArtifactSummary:
    """Summarize ASSERT policy/results artifacts without assuming a stable schema."""

    artifact = _select_artifact(workspace, configured, _ASSERT_PATTERNS)
    if artifact is None:
        return GovernanceArtifactSummary(kind="assert", status="not_configured")
    loaded = _load_artifact(workspace, artifact, configured=configured is not None)
    if loaded.status != "present":
        return loaded
    data, error = _parse_mapping(artifact)
    if error:
        return _replace(loaded, status="invalid", message=error)
    counts = _extract_counts(data)
    return _replace(
        loaded,
        schema_version=_version_from(data),
        name=_name_from(data, ("evaluation_name", "name", "policy_name", "id")),
        counts=counts,
    )


def summarize_acs(workspace: Path, configured: Any = None) -> GovernanceArtifactSummary:
    """Summarize ACS contracts and checkpoint coverage."""

    artifact = _select_artifact(workspace, configured, _ACS_PATTERNS)
    if artifact is None:
        return GovernanceArtifactSummary(kind="acs", status="not_configured")
    loaded = _load_artifact(workspace, artifact, configured=configured is not None, kind="acs")
    if loaded.status != "present":
        return loaded
    data, error = _parse_mapping(artifact)
    if error:
        return _replace(loaded, status="invalid", message=error)
    covered = _acs_checkpoints(data)
    missing = tuple(checkpoint for checkpoint in _ACS_REQUIRED_CHECKPOINTS if checkpoint not in covered)
    status = "present" if not missing else "partial"
    return _replace(
        loaded,
        status=status,
        schema_version=_version_from(data),
        name=_name_from(data, ("name", "id", "title")),
        checkpoints_covered=tuple(sorted(covered)),
        checkpoints_missing=missing,
        message=("ACS contract is missing checkpoint coverage." if missing else None),
    )


def summarize_redteam(workspace: Path, configured: Any = None) -> GovernanceArtifactSummary:
    """Summarize red-team plan/results metadata without exposing payload text."""

    artifact = _select_artifact(workspace, configured, _REDTEAM_PATTERNS)
    if artifact is None:
        return GovernanceArtifactSummary(kind="redteam", status="not_configured")
    loaded = _load_artifact(workspace, artifact, configured=configured is not None, kind="redteam")
    if loaded.status != "present":
        return loaded
    data, error = _parse_mapping(artifact)
    if error:
        return _replace(loaded, status="invalid", message=error)
    return _replace(
        loaded,
        schema_version=_version_from(data),
        name=_name_from(data, ("campaign", "name", "title", "id")),
        counts=_extract_counts(data),
    )


def _select_artifact(workspace: Path, configured: Any, patterns: Iterable[str]) -> Optional[Path]:
    root = workspace.resolve()
    configured_paths = _configured_paths(configured)
    if configured_paths:
        for path in configured_paths:
            resolved = path if path.is_absolute() else root / path
            if resolved.is_dir():
                found = _discover(resolved, ("*.yml", "*.yaml", "*.json", "*.md"))
                if found:
                    return found[0]
                return resolved
            return resolved
    found = _discover(root, patterns)
    return found[0] if found else None


def _configured_paths(value: Any) -> list[Path]:
    if value is None or value == "":
        return []
    if isinstance(value, (str, Path)):
        return [Path(value)]
    if isinstance(value, list):
        return [Path(item) for item in value if isinstance(item, (str, Path))]
    return []


def _discover(root: Path, patterns: Iterable[str]) -> list[Path]:
    found: list[Path] = []
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if path.is_file():
                found.append(path.resolve())
    return sorted(set(found))


def _load_artifact(
    workspace: Path,
    artifact: Path,
    *,
    configured: bool,
    kind: str = "assert",
) -> GovernanceArtifactSummary:
    root = workspace.resolve()
    resolved = artifact.resolve()
    if not _is_relative_to(resolved, root):
        return GovernanceArtifactSummary(
            kind=kind,
            status="invalid",
            configured=configured,
            path=str(artifact),
            message="configured path resolves outside the workspace",
        )
    if not resolved.exists():
        return GovernanceArtifactSummary(
            kind=kind,
            status="missing",
            configured=configured,
            path=_display_path(resolved, root),
            message="configured artifact was not found",
        )
    if not resolved.is_file():
        return GovernanceArtifactSummary(
            kind=kind,
            status="invalid",
            configured=configured,
            path=_display_path(resolved, root),
            message="configured artifact is not a file",
        )
    size = resolved.stat().st_size
    if size > MAX_ARTIFACT_BYTES:
        return GovernanceArtifactSummary(
            kind=kind,
            status="invalid",
            configured=configured,
            path=_display_path(resolved, root),
            size_bytes=size,
            message=f"artifact exceeds {MAX_ARTIFACT_BYTES} bytes",
        )
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    return GovernanceArtifactSummary(
        kind=kind,
        status="present",
        configured=configured,
        path=_display_path(resolved, root),
        sha256=digest,
        size_bytes=size,
    )


def _parse_mapping(path: Path) -> tuple[dict[str, Any], Optional[str]]:
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
        elif suffix in {".yml", ".yaml"}:
            value = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
        else:
            return {}, None
    except (json.JSONDecodeError, YAMLError, OSError, UnicodeDecodeError) as exc:
        return {}, f"artifact could not be parsed: {exc}"
    if not isinstance(value, dict):
        return {}, "artifact root must be a mapping"
    return value, None


def _extract_counts(data: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in ("total", "passed", "failed", "blocked", "warnings", "critical"):
        value = _find_numeric(data, key)
        if value is not None:
            counts[key] = value
    return counts


def _find_numeric(value: Any, target_key: str) -> Optional[int]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() == target_key and isinstance(child, int):
                return child
            nested = _find_numeric(child, target_key)
            if nested is not None:
                return nested
    if isinstance(value, list):
        for child in value:
            nested = _find_numeric(child, target_key)
            if nested is not None:
                return nested
    return None


def _version_from(data: dict[str, Any]) -> str:
    for key in ("version", "schema_version", "spec_version"):
        value = data.get(key)
        if value is not None:
            return str(value)
    return "unknown"


def _name_from(data: dict[str, Any], keys: Iterable[str]) -> Optional[str]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _acs_checkpoints(data: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    checkpoints = data.get("checkpoints")
    candidates: list[str] = []
    if isinstance(checkpoints, dict):
        candidates.extend(str(key) for key in checkpoints.keys())
    elif isinstance(checkpoints, list):
        for item in checkpoints:
            if isinstance(item, str):
                candidates.append(item)
            elif isinstance(item, dict):
                for key in ("name", "id", "checkpoint", "type"):
                    value = item.get(key)
                    if isinstance(value, str):
                        candidates.append(value)
                        break
    for value in candidates:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        checkpoint = _ACS_CHECKPOINT_ALIASES.get(normalized)
        if checkpoint:
            found.add(checkpoint)
    return found


def _replace(summary: GovernanceArtifactSummary, **updates: Any) -> GovernanceArtifactSummary:
    data = summary.__dict__.copy()
    data.update(updates)
    return GovernanceArtifactSummary(**data)


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
