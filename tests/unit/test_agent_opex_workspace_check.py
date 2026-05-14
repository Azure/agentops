"""Tests for the watchdog MLOps check."""

from __future__ import annotations

import textwrap
from pathlib import Path

from agentops.agent.checks.opex_workspace import run_opex_workspace_check
from agentops.agent.findings import Category, Severity


def _write_yaml(workspace: Path, body: str) -> None:
    (workspace / "agentops.yaml").write_text(textwrap.dedent(body), encoding="utf-8")


def test_no_config_returns_no_findings(tmp_path: Path) -> None:
    # Empty workspace: no agentops.yaml. Nothing to nag about.
    assert run_opex_workspace_check(tmp_path) == []


def test_unpinned_agent_warning(tmp_path: Path) -> None:
    _write_yaml(tmp_path, """
        version: 1
        agent: my-agent
        dataset: data/smoke.jsonl
    """)
    findings = run_opex_workspace_check(tmp_path)
    ids = {f.id for f in findings}
    assert "opex.unpinned_agent" in ids
    f = next(f for f in findings if f.id == "opex.unpinned_agent")
    assert f.severity is Severity.WARNING
    assert f.category is Category.OPERATIONAL_EXCELLENCE


def test_pinned_agent_passes(tmp_path: Path) -> None:
    _write_yaml(tmp_path, """
        version: 1
        agent: my-agent:3
        thresholds:
          coherence: ">=3"
        dataset: data/smoke.jsonl
    """)
    findings = run_opex_workspace_check(tmp_path)
    ids = {f.id for f in findings}
    assert "opex.unpinned_agent" not in ids


def test_unpinned_agent_with_latest_alias_warns(tmp_path: Path) -> None:
    _write_yaml(tmp_path, """
        version: 1
        agent: my-agent:latest
        dataset: data/smoke.jsonl
    """)
    findings = run_opex_workspace_check(tmp_path)
    ids = {f.id for f in findings}
    assert "opex.unpinned_agent" in ids


def test_http_agent_target_is_considered_pinned(tmp_path: Path) -> None:
    _write_yaml(tmp_path, """
        version: 1
        agent: https://api.example.com/chat
        dataset: data/smoke.jsonl
        thresholds:
          coherence: ">=3"
    """)
    findings = run_opex_workspace_check(tmp_path)
    ids = {f.id for f in findings}
    assert "opex.unpinned_agent" not in ids


def test_no_thresholds_warning(tmp_path: Path) -> None:
    _write_yaml(tmp_path, """
        version: 1
        agent: my-agent:3
        dataset: data/smoke.jsonl
    """)
    findings = run_opex_workspace_check(tmp_path)
    ids = {f.id for f in findings}
    assert "opex.no_thresholds" in ids


def test_thresholds_present_passes(tmp_path: Path) -> None:
    _write_yaml(tmp_path, """
        version: 1
        agent: my-agent:3
        dataset: data/smoke.jsonl
        thresholds:
          coherence: ">=3"
          avg_latency_seconds: "<=30"
    """)
    findings = run_opex_workspace_check(tmp_path)
    ids = {f.id for f in findings}
    assert "opex.no_thresholds" not in ids


def test_no_pr_gate_when_workflows_dir_exists_but_missing_file(tmp_path: Path) -> None:
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    findings = run_opex_workspace_check(tmp_path)
    ids = {f.id for f in findings}
    assert "opex.no_pr_gate" in ids


def test_no_pr_gate_silent_when_no_workflows_dir(tmp_path: Path) -> None:
    findings = run_opex_workspace_check(tmp_path)
    ids = {f.id for f in findings}
    assert "opex.no_pr_gate" not in ids


def test_no_pr_gate_passes_when_file_exists(tmp_path: Path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "agentops-pr.yml").write_text("name: AgentOps PR\n", encoding="utf-8")
    findings = run_opex_workspace_check(tmp_path)
    ids = {f.id for f in findings}
    assert "opex.no_pr_gate" not in ids
