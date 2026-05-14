"""Spec-conformance check (Operational Excellence pillar).

Compares the project's spec-driven-development artifacts
(``.specify/spec.md`` + ``plan.md`` + ``tasks.md``, ``AGENTS.md``,
``.github/copilot-instructions.md``) against the AgentOps workspace
(``run.yaml``, ``.agentops/bundles/``, ``.agentops/datasets/``,
``CHANGELOG.md``) and flags drift between the two.

All findings live under :class:`Category.OPERATIONAL_EXCELLENCE` with
the ``opex.spec_conformance.*`` id prefix. Deterministic rules emit
``info``/``warning`` only — never ``critical`` — because spec
conformance is a soft signal.

The companion opt-in LLM rule
(``opex.spec_conformance.llm.implementation_gap``) lives in
:mod:`agentops.agent.llm_assist._spec_rules`.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

import yaml

from agentops.agent.config import SpecConformanceCheckConfig
from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.sources.spec_detectors import (
    DETECTORS,
    Detector,
    SpecDocument,
)

SOURCE_NAME = "spec_workspace"

_MIN_MEANINGFUL_LINES = 5


def run_spec_conformance_check(
    workspace: Path,
    config: SpecConformanceCheckConfig,
) -> List[Finding]:
    """Run all deterministic spec-conformance rules and return findings."""
    if not config.enabled:
        return []

    selected = _select_detectors(config.detectors)
    documents: List[SpecDocument] = []
    hint_only: List[Path] = []
    for det in selected:
        doc = det.detect(workspace)
        if doc is not None:
            documents.append(doc)
        else:
            hint_only.extend(det.hint_paths(workspace))

    findings: List[Finding] = []

    if not documents:
        if hint_only:
            findings.append(
                Finding(
                    id="opex.spec_conformance.spec_missing",
                    severity=Severity.WARNING,
                    category=Category.OPERATIONAL_EXCELLENCE,
                    title="Spec-driven setup detected, but spec content is missing",
                    summary=(
                        "Spec scaffolding was found in the workspace "
                        "(e.g. `.specify/` directory or a "
                        "`copilot-instructions.md` shell) but no "
                        "actual spec document could be loaded. The "
                        "doctor can't verify implementation/spec "
                        "alignment without content."
                    ),
                    recommendation=(
                        "Author `.specify/spec.md` (spec-kit) or "
                        "`AGENTS.md` describing the agent's "
                        "capabilities, evaluators, and datasets, "
                        "then re-run `agentops doctor`."
                    ),
                    source=SOURCE_NAME,
                    evidence={"hint_paths": [str(p) for p in hint_only]},
                )
            )
        return _filter_skipped(findings, config.skip)

    for doc in documents:
        findings.extend(_check_spec_empty(doc))
        findings.extend(_check_tasks(doc, config.stale_after_days))
        findings.extend(_check_evaluator_drift(workspace, doc))
        findings.extend(_check_dataset_drift(workspace, doc))
        findings.extend(_check_agent_drift(workspace, doc))
        findings.extend(_check_copilot_instructions_link(doc))
        findings.extend(_check_changelog_link(workspace, doc))

    deduped: List[Finding] = []
    seen: set[tuple[str, str]] = set()
    for f in findings:
        key = (f.id, _evidence_key(f))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f)

    return _filter_skipped(deduped, config.skip)


def _select_detectors(names: Iterable[str]) -> List[Detector]:
    requested = {n.strip().lower() for n in names if n and n.strip()}
    if not requested:
        return list(DETECTORS)
    return [d for d in DETECTORS if d.name.lower() in requested]


def _filter_skipped(findings: List[Finding], skip: Iterable[str]) -> List[Finding]:
    skip_set = {s.strip() for s in skip if s and s.strip()}
    if not skip_set:
        return findings
    return [f for f in findings if f.id not in skip_set]


def _evidence_key(f: Finding) -> str:
    """Stable key from a finding's evidence for de-duplication across detectors."""
    if not isinstance(f.evidence, dict):
        return ""
    parts = []
    for k in sorted(f.evidence):
        v = f.evidence[k]
        parts.append(f"{k}={v!r}")
    return "|".join(parts)


def _check_spec_empty(doc: SpecDocument) -> List[Finding]:
    meaningful = [
        line for line in doc.text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if len(meaningful) >= _MIN_MEANINGFUL_LINES:
        return []
    return [
        Finding(
            id="opex.spec_conformance.spec_empty",
            severity=Severity.INFO,
            category=Category.OPERATIONAL_EXCELLENCE,
            title=f"Spec document ({doc.format}) is effectively empty",
            summary=(
                "The spec file(s) were found but contain fewer than "
                f"{_MIN_MEANINGFUL_LINES} non-heading lines. Spec "
                "conformance checks need real content to compare "
                "against."
            ),
            recommendation=(
                "Flesh out the spec with capability bullets, "
                "evaluator names, dataset references, and the "
                "agent's target endpoint."
            ),
            source=SOURCE_NAME,
            evidence={
                "format": doc.format,
                "files": [str(p) for p in doc.files],
                "meaningful_lines": len(meaningful),
            },
        )
    ]


def _check_tasks(doc: SpecDocument, stale_after_days: int) -> List[Finding]:
    findings: List[Finding] = []
    if not doc.tasks:
        return findings

    now = datetime.now(timezone.utc)
    last_modified = doc.last_modified
    age_days = (
        (now - last_modified).total_seconds() / 86400.0
        if last_modified is not None
        else None
    )

    unchecked = [t for t in doc.tasks if not t.checked]
    if unchecked and age_days is not None and age_days > stale_after_days:
        findings.append(
            Finding(
                id="opex.spec_conformance.tasks_stale",
                severity=Severity.WARNING,
                category=Category.OPERATIONAL_EXCELLENCE,
                title="Open tasks in spec are older than the staleness window",
                summary=(
                    f"{len(unchecked)} unchecked task(s) in the spec "
                    f"haven't been touched for {age_days:.1f} day(s) "
                    f"(threshold: {stale_after_days})."
                ),
                recommendation=(
                    "Either close the tasks, drop them from the spec, "
                    "or update the spec to reflect the new reality."
                ),
                source=SOURCE_NAME,
                evidence={
                    "format": doc.format,
                    "open_tasks": len(unchecked),
                    "age_days": round(age_days, 2),
                    "threshold_days": stale_after_days,
                },
            )
        )

    orphans: List[str] = []
    for task in doc.tasks:
        if not task.checked:
            continue
        for rel in task.mentioned_paths:
            candidate = doc.root / rel
            if not candidate.exists():
                # Try resolving from the workspace root instead of the
                # spec root (e.g. spec-kit lives under .specify/ but
                # paths are workspace-relative).
                if not (doc.root.parent / rel).exists():
                    orphans.append(rel)

    if orphans:
        findings.append(
            Finding(
                id="opex.spec_conformance.tasks_orphaned",
                severity=Severity.WARNING,
                category=Category.OPERATIONAL_EXCELLENCE,
                title="Completed tasks reference paths that don't exist",
                summary=(
                    "One or more checked task items in the spec point "
                    "at files that aren't in the workspace. Either "
                    "the implementation was removed or the spec is "
                    "out of date."
                ),
                recommendation=(
                    "Update the spec to reflect the current code "
                    "layout, or restore the missing files."
                ),
                source=SOURCE_NAME,
                evidence={
                    "format": doc.format,
                    "missing_paths": orphans[:10],
                },
            )
        )

    return findings


def _check_evaluator_drift(workspace: Path, doc: SpecDocument) -> List[Finding]:
    mentioned = doc.references.get("evaluators") or []
    if not mentioned:
        return []
    declared = _collect_evaluator_names(workspace)
    if not declared:
        return []
    missing = [e for e in mentioned if e not in declared]
    if not missing:
        return []
    return [
        Finding(
            id="opex.spec_conformance.evaluator_drift",
            severity=Severity.WARNING,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="Spec names evaluators that no bundle declares",
            summary=(
                "The spec mentions evaluator classes that are absent "
                "from every `.agentops/bundles/*.yaml`. The "
                "implementation isn't measuring what the spec "
                "promises."
            ),
            recommendation=(
                "Either add the missing evaluator(s) to a bundle or "
                "update the spec to reflect what the project actually "
                "evaluates."
            ),
            source=SOURCE_NAME,
            evidence={"missing_evaluators": missing[:10]},
        )
    ]


def _check_dataset_drift(workspace: Path, doc: SpecDocument) -> List[Finding]:
    mentioned = doc.references.get("datasets") or []
    if not mentioned:
        return []
    available = {p.name for p in (workspace / ".agentops" / "datasets").glob("*.y*ml")}
    available |= {p.name for p in (workspace / ".agentops" / "data").glob("*.jsonl")}
    if not available:
        return []
    missing = [d for d in mentioned if Path(d).name not in available]
    if not missing:
        return []
    return [
        Finding(
            id="opex.spec_conformance.dataset_drift",
            severity=Severity.WARNING,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="Spec references datasets that aren't in the workspace",
            summary=(
                "Dataset filenames mentioned in the spec do not "
                "exist under `.agentops/datasets/` or "
                "`.agentops/data/`."
            ),
            recommendation=(
                "Add the missing dataset file(s) under "
                "`.agentops/datasets/` (and the matching JSONL under "
                "`.agentops/data/`), or update the spec."
            ),
            source=SOURCE_NAME,
            evidence={"missing_datasets": missing[:10]},
        )
    ]


def _check_agent_drift(workspace: Path, doc: SpecDocument) -> List[Finding]:
    mentioned = doc.references.get("agent_ids") or []
    if not mentioned:
        return []
    run_yaml = workspace / ".agentops" / "run.yaml"
    if not run_yaml.exists():
        run_yaml = workspace / "run.yaml"
    if not run_yaml.exists():
        return []
    try:
        raw = yaml.safe_load(run_yaml.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(raw, dict):
        return []
    target = raw.get("target") or {}
    endpoint = target.get("endpoint") or {}
    declared_agent = str(endpoint.get("agent_id") or "")
    if not declared_agent:
        return []
    if declared_agent in mentioned:
        return []
    return [
        Finding(
            id="opex.spec_conformance.agent_drift",
            severity=Severity.WARNING,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="Spec's agent identifier doesn't match `run.yaml`",
            summary=(
                f"`run.yaml` targets agent `{declared_agent}` but the "
                "spec mentions a different agent identifier. The "
                "evaluation is running against a different agent "
                "than the spec describes."
            ),
            recommendation=(
                "Pin `run.yaml`'s `target.endpoint.agent_id` to the "
                "agent named in the spec, or update the spec to "
                "match."
            ),
            source=SOURCE_NAME,
            evidence={
                "spec_agent_ids": mentioned[:5],
                "run_yaml_agent_id": declared_agent,
            },
        )
    ]


def _check_copilot_instructions_link(doc: SpecDocument) -> List[Finding]:
    if doc.format != "agents-md":
        return []
    has_copilot_file = any(
        str(p).endswith("copilot-instructions.md") for p in doc.files
    )
    if not has_copilot_file:
        return []
    if re.search(r"agentops", doc.text, re.IGNORECASE):
        return []
    return [
        Finding(
            id="opex.spec_conformance.copilot_instructions_missing_agentops",
            severity=Severity.INFO,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="`copilot-instructions.md` doesn't reference AgentOps",
            summary=(
                "Copilot instructions are present but never mention "
                "AgentOps or its skills. New contributors won't be "
                "guided toward the eval workflow."
            ),
            recommendation=(
                "Add a short AgentOps section to "
                "`.github/copilot-instructions.md` describing the "
                "eval workflow and the installed skills."
            ),
            source=SOURCE_NAME,
            evidence={"files": [str(p) for p in doc.files]},
        )
    ]


def _check_changelog_link(workspace: Path, doc: SpecDocument) -> List[Finding]:
    changelog = workspace / "CHANGELOG.md"
    if not changelog.exists():
        return []
    if doc.last_modified is None:
        return []
    try:
        changelog_mtime = datetime.fromtimestamp(
            changelog.stat().st_mtime, tz=timezone.utc
        )
    except OSError:
        return []
    if doc.last_modified <= changelog_mtime:
        return []
    return [
        Finding(
            id="opex.spec_conformance.changelog_unlinked_spec",
            severity=Severity.INFO,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="Spec was modified after the last CHANGELOG entry",
            summary=(
                "The spec changed but no corresponding CHANGELOG "
                "entry followed. The release narrative is drifting "
                "from the contract."
            ),
            recommendation=(
                "Add a CHANGELOG entry describing the spec change, "
                "or roll the spec change back if it was unintended."
            ),
            source=SOURCE_NAME,
            evidence={
                "spec_modified": doc.last_modified.isoformat(),
                "changelog_modified": changelog_mtime.isoformat(),
            },
        )
    ]


def _collect_evaluator_names(workspace: Path) -> set[str]:
    """Read every bundle YAML and return the set of evaluator class names."""
    out: set[str] = set()
    bundles_dir = workspace / ".agentops" / "bundles"
    if not bundles_dir.is_dir():
        return out
    for p in bundles_dir.glob("*.y*ml"):
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(raw, dict):
            continue
        for ev in raw.get("evaluators") or []:
            if isinstance(ev, dict):
                name = ev.get("class") or ev.get("name")
                if isinstance(name, str):
                    out.add(name.strip())
            elif isinstance(ev, str):
                out.add(ev.strip())
    return out


def detect_documents(
    workspace: Path,
    config: Optional[SpecConformanceCheckConfig] = None,
) -> List[SpecDocument]:
    """Public helper: return all spec documents discovered in ``workspace``.

    Used by the LLM rule to share detection with the deterministic
    check without re-implementing the registry walk.
    """
    cfg = config or SpecConformanceCheckConfig()
    out: List[SpecDocument] = []
    for det in _select_detectors(cfg.detectors):
        doc = det.detect(workspace)
        if doc is not None:
            out.append(doc)
    return out
