"""Tests for the spec-conformance check (deterministic rules + detectors)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from agentops.agent.checks.spec_conformance import (
    detect_documents,
    run_spec_conformance_check,
)
from agentops.agent.config import SpecConformanceCheckConfig
from agentops.agent.findings import Category, Severity
from agentops.agent.knowledge import find_waf_item
from agentops.agent.sources.spec_detectors import (
    AgentsMdDetector,
    SpecKitDetector,
)


def _make_spec_kit(tmp_path: Path, *, spec: str = "", tasks: str = "") -> Path:
    base = tmp_path / ".specify"
    base.mkdir()
    (base / "spec.md").write_text(spec, encoding="utf-8")
    if tasks:
        (base / "tasks.md").write_text(tasks, encoding="utf-8")
    return tmp_path


def _make_agents_md(tmp_path: Path, text: str) -> Path:
    (tmp_path / "AGENTS.md").write_text(text, encoding="utf-8")
    return tmp_path


def _ids(findings):
    return [f.id for f in findings]


# ---------------------------------------------------------------------------
# detectors
# ---------------------------------------------------------------------------


def test_spec_kit_detector_returns_none_when_no_specify_dir(tmp_path: Path) -> None:
    assert SpecKitDetector().detect(tmp_path) is None


def test_spec_kit_detector_extracts_capabilities_and_tasks(tmp_path: Path) -> None:
    _make_spec_kit(
        tmp_path,
        spec="# My Agent\n\n## Capabilities\n\n- Answer questions about onboarding\n",
        tasks="- [x] Add `src/agent.py`\n- [ ] Wire `GroundednessEvaluator`\n",
    )
    doc = SpecKitDetector().detect(tmp_path)
    assert doc is not None
    assert doc.format == "spec-kit"
    assert any("My Agent" == c or "Capabilities" == c for c in doc.capabilities)
    assert len(doc.tasks) == 2
    checked = [t for t in doc.tasks if t.checked]
    assert len(checked) == 1
    assert "src/agent.py" in checked[0].mentioned_paths
    assert "GroundednessEvaluator" in doc.references["evaluators"]


def test_agents_md_detector_extracts_references(tmp_path: Path) -> None:
    _make_agents_md(
        tmp_path,
        "# Agent spec\n\nWe target the `onboarding-bot:3` agent and use "
        "`GroundednessEvaluator` against `smoke-rag.jsonl`.",
    )
    doc = AgentsMdDetector().detect(tmp_path)
    assert doc is not None
    assert "GroundednessEvaluator" in doc.references["evaluators"]
    assert "onboarding-bot:3" in doc.references["agent_ids"]
    assert "smoke-rag.jsonl" in doc.references["datasets"]


# ---------------------------------------------------------------------------
# spec_missing / spec_empty / disabled / auto-skip
# ---------------------------------------------------------------------------


def test_disabled_check_returns_no_findings(tmp_path: Path) -> None:
    _make_spec_kit(tmp_path, spec="(empty)")
    findings = run_spec_conformance_check(
        tmp_path, SpecConformanceCheckConfig(enabled=False)
    )
    assert findings == []


def test_silent_when_no_spec_and_no_hints(tmp_path: Path) -> None:
    findings = run_spec_conformance_check(
        tmp_path, SpecConformanceCheckConfig()
    )
    assert findings == []


def test_spec_missing_fires_when_hint_paths_present_but_empty(tmp_path: Path) -> None:
    # .specify/ directory exists but no doc files inside.
    (tmp_path / ".specify").mkdir()
    findings = run_spec_conformance_check(
        tmp_path, SpecConformanceCheckConfig()
    )
    ids = _ids(findings)
    assert "opex.spec_conformance.spec_missing" in ids
    f = next(f for f in findings if f.id == "opex.spec_conformance.spec_missing")
    assert f.severity == Severity.WARNING
    assert f.category == Category.OPERATIONAL_EXCELLENCE


def test_spec_empty_fires_for_tiny_spec(tmp_path: Path) -> None:
    _make_agents_md(tmp_path, "# Title only\n")
    findings = run_spec_conformance_check(
        tmp_path, SpecConformanceCheckConfig()
    )
    assert "opex.spec_conformance.spec_empty" in _ids(findings)


# ---------------------------------------------------------------------------
# tasks_stale / tasks_orphaned
# ---------------------------------------------------------------------------


def test_tasks_stale_fires_when_tasks_file_is_old(tmp_path: Path) -> None:
    _make_spec_kit(
        tmp_path,
        spec="# Agent\nReal capabilities described here.\nAnother line.\nMore body.\nDone.\n",
        tasks="- [ ] open work item\n",
    )
    old = time.time() - 60 * 86400
    for name in ("spec.md", "tasks.md"):
        os.utime(tmp_path / ".specify" / name, (old, old))
    findings = run_spec_conformance_check(
        tmp_path, SpecConformanceCheckConfig(stale_after_days=30)
    )
    assert "opex.spec_conformance.tasks_stale" in _ids(findings)


def test_tasks_orphaned_fires_for_checked_task_missing_file(tmp_path: Path) -> None:
    _make_spec_kit(
        tmp_path,
        spec="# Agent\nReal capabilities described here.\nAnother line.\nMore body.\nDone.\n",
        tasks="- [x] Add `src/missing_module.py`\n",
    )
    findings = run_spec_conformance_check(
        tmp_path, SpecConformanceCheckConfig()
    )
    assert "opex.spec_conformance.tasks_orphaned" in _ids(findings)


# ---------------------------------------------------------------------------
# evaluator_drift / dataset_drift / agent_drift
# ---------------------------------------------------------------------------


def _seed_bundle(tmp_path: Path, *, evaluators: list[str]) -> None:
    bundles = tmp_path / ".agentops" / "bundles"
    bundles.mkdir(parents=True)
    body = "version: 1\nevaluators:\n"
    for ev in evaluators:
        body += f"  - class: {ev}\n"
    (bundles / "primary.yaml").write_text(body, encoding="utf-8")


def test_evaluator_drift_flags_missing_evaluator(tmp_path: Path) -> None:
    _seed_bundle(tmp_path, evaluators=["SimilarityEvaluator"])
    _make_agents_md(
        tmp_path,
        "# Agent\nWe rely on `GroundednessEvaluator` for RAG.\nSee `smoke.yaml`.\n"
        "Body content goes here.\nAnother line.\n",
    )
    findings = run_spec_conformance_check(
        tmp_path, SpecConformanceCheckConfig()
    )
    drift = next(
        (f for f in findings if f.id == "opex.spec_conformance.evaluator_drift"),
        None,
    )
    assert drift is not None
    assert "GroundednessEvaluator" in drift.evidence["missing_evaluators"]


def test_evaluator_drift_silent_when_bundle_covers_spec(tmp_path: Path) -> None:
    _seed_bundle(tmp_path, evaluators=["GroundednessEvaluator"])
    _make_agents_md(
        tmp_path,
        "# Agent\nWe rely on `GroundednessEvaluator`.\nBody content.\nMore.\nMore.\n",
    )
    findings = run_spec_conformance_check(
        tmp_path, SpecConformanceCheckConfig()
    )
    assert "opex.spec_conformance.evaluator_drift" not in _ids(findings)


def test_dataset_drift_flags_missing_dataset(tmp_path: Path) -> None:
    (tmp_path / ".agentops" / "datasets").mkdir(parents=True)
    (tmp_path / ".agentops" / "datasets" / "present.yaml").write_text(
        "version: 1\n", encoding="utf-8"
    )
    _make_agents_md(
        tmp_path,
        "# Agent\nUses `missing-dataset.yaml`.\nBody.\nMore body.\nMore.\n",
    )
    findings = run_spec_conformance_check(
        tmp_path, SpecConformanceCheckConfig()
    )
    drift = next(
        (f for f in findings if f.id == "opex.spec_conformance.dataset_drift"),
        None,
    )
    assert drift is not None
    assert "missing-dataset.yaml" in drift.evidence["missing_datasets"]


def test_agent_drift_flags_mismatched_agent_id(tmp_path: Path) -> None:
    (tmp_path / ".agentops").mkdir()
    (tmp_path / ".agentops" / "run.yaml").write_text(
        "version: 1\ntarget:\n  endpoint:\n    agent_id: other-bot:1\n",
        encoding="utf-8",
    )
    _make_agents_md(
        tmp_path,
        "# Agent\nTarget agent `onboarding-bot:3`.\nBody.\nMore body.\nMore.\n",
    )
    findings = run_spec_conformance_check(
        tmp_path, SpecConformanceCheckConfig()
    )
    drift = next(
        (f for f in findings if f.id == "opex.spec_conformance.agent_drift"),
        None,
    )
    assert drift is not None
    assert drift.evidence["run_yaml_agent_id"] == "other-bot:1"


# ---------------------------------------------------------------------------
# copilot instructions + skip list + WAF citation
# ---------------------------------------------------------------------------


def test_copilot_instructions_missing_agentops(tmp_path: Path) -> None:
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "copilot-instructions.md").write_text(
        "# Copilot guidance\n\nGenerate tests.\nUse pytest.\nDocument code.\n"
        "Prefer type hints.\nUse pathlib.\n",
        encoding="utf-8",
    )
    findings = run_spec_conformance_check(
        tmp_path, SpecConformanceCheckConfig()
    )
    assert (
        "opex.spec_conformance.copilot_instructions_missing_agentops"
        in _ids(findings)
    )


def test_copilot_instructions_silent_when_agentops_mentioned(tmp_path: Path) -> None:
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "copilot-instructions.md").write_text(
        "# Copilot guidance\nUse agentops for evals.\nBody.\nMore.\nMore.\n",
        encoding="utf-8",
    )
    findings = run_spec_conformance_check(
        tmp_path, SpecConformanceCheckConfig()
    )
    assert (
        "opex.spec_conformance.copilot_instructions_missing_agentops"
        not in _ids(findings)
    )


def test_skip_list_silences_specific_finding(tmp_path: Path) -> None:
    (tmp_path / ".specify").mkdir()
    cfg = SpecConformanceCheckConfig(skip=["opex.spec_conformance.spec_missing"])
    findings = run_spec_conformance_check(tmp_path, cfg)
    assert findings == []


def test_waf_citation_present_for_every_new_finding_id() -> None:
    new_ids = [
        "opex.spec_conformance.spec_missing",
        "opex.spec_conformance.spec_empty",
        "opex.spec_conformance.tasks_stale",
        "opex.spec_conformance.tasks_orphaned",
        "opex.spec_conformance.evaluator_drift",
        "opex.spec_conformance.dataset_drift",
        "opex.spec_conformance.agent_drift",
        "opex.spec_conformance.copilot_instructions_missing_agentops",
        "opex.spec_conformance.changelog_unlinked_spec",
        "opex.spec_conformance.llm.implementation_gap",
    ]
    for fid in new_ids:
        item = find_waf_item(fid)
        assert item is not None, f"WAF row missing for {fid}"
        assert item.pillar == "OperationalExcellence"
        assert item.area == "Documentation"


def test_detect_documents_helper_returns_all_matching_formats(tmp_path: Path) -> None:
    _make_spec_kit(tmp_path, spec="# A\nBody\nMore\nMore\nMore\n")
    _make_agents_md(tmp_path, "# B\nBody\nMore\nMore\nMore\n")
    docs = detect_documents(tmp_path)
    formats = {d.format for d in docs}
    assert formats == {"spec-kit", "agents-md"}
