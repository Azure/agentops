"""Tests for the MLOps hygiene check.

Covers the workspace-file rules that flag CI/CD gaps and governance
oversights (deploy workflow missing, results not gitignored, dataset
files lacking a version).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentops.agent.checks.opex_workspace import run_opex_workspace_check
from agentops.agent.findings import Category, Severity


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    # Minimal layout: a valid agentops.yaml so the agent_pinning and
    # thresholds rules don't fire by accident.
    (tmp_path / "agentops.yaml").write_text(
        "agent: my-agent:1\nthresholds:\n  coherence: '>=3'\n",
        encoding="utf-8",
    )
    return tmp_path


def _ids(findings) -> set:
    return {f.id for f in findings}


# ---------------------------------------------------------------------------
# no_deploy_workflow
# ---------------------------------------------------------------------------


def test_no_deploy_workflow_emitted_when_pr_gate_exists_but_no_deploys(
    workspace: Path,
) -> None:
    wf_dir = workspace / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "agentops-pr.yml").write_text("name: pr\n", encoding="utf-8")

    findings = run_opex_workspace_check(workspace)
    assert "opex.no_deploy_workflow" in _ids(findings)


def test_no_deploy_workflow_silent_when_deploy_exists(workspace: Path) -> None:
    wf_dir = workspace / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "agentops-pr.yml").write_text("name: pr\n", encoding="utf-8")
    (wf_dir / "agentops-deploy-dev.yml").write_text(
        "name: deploy-dev\n", encoding="utf-8"
    )

    findings = run_opex_workspace_check(workspace)
    assert "opex.no_deploy_workflow" not in _ids(findings)


def test_no_deploy_workflow_silent_when_no_pr_gate(workspace: Path) -> None:
    # If there's no PR gate, `opex.no_pr_gate` already fires; we don't
    # also spam about deploy workflows.
    wf_dir = workspace / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "ci.yml").write_text("name: ci\n", encoding="utf-8")

    findings = run_opex_workspace_check(workspace)
    assert "opex.no_deploy_workflow" not in _ids(findings)


# ---------------------------------------------------------------------------
# results_not_gitignored
# ---------------------------------------------------------------------------


def test_results_not_gitignored_emitted_when_results_present_and_no_ignore(
    workspace: Path,
) -> None:
    (workspace / ".agentops").mkdir()
    (workspace / ".agentops" / "results").mkdir()

    findings = run_opex_workspace_check(workspace)
    assert "opex.results_not_gitignored" in _ids(findings)


def test_results_not_gitignored_silent_when_gitignore_covers_it(
    workspace: Path,
) -> None:
    (workspace / ".agentops").mkdir()
    (workspace / ".agentops" / "results").mkdir()
    (workspace / ".agentops" / ".gitignore").write_text(
        "results/\n", encoding="utf-8"
    )

    findings = run_opex_workspace_check(workspace)
    assert "opex.results_not_gitignored" not in _ids(findings)


def test_results_not_gitignored_silent_when_no_results_dir(workspace: Path) -> None:
    # No results directory yet — nothing to leak, nothing to warn.
    findings = run_opex_workspace_check(workspace)
    assert "opex.results_not_gitignored" not in _ids(findings)


def test_results_not_gitignored_accepts_repo_root_gitignore(
    workspace: Path,
) -> None:
    (workspace / ".agentops").mkdir()
    (workspace / ".agentops" / "results").mkdir()
    (workspace / ".gitignore").write_text(
        ".agentops/results/\n", encoding="utf-8"
    )
    findings = run_opex_workspace_check(workspace)
    assert "opex.results_not_gitignored" not in _ids(findings)


# ---------------------------------------------------------------------------
# unversioned_dataset
# ---------------------------------------------------------------------------


def test_unversioned_dataset_emitted_when_version_missing(workspace: Path) -> None:
    datasets = workspace / ".agentops" / "datasets"
    datasets.mkdir(parents=True)
    (datasets / "smoke.yaml").write_text(
        "name: smoke\nsource:\n  type: file\n  path: ../data/smoke.jsonl\n",
        encoding="utf-8",
    )

    findings = run_opex_workspace_check(workspace)
    finding = next(
        (f for f in findings if f.id == "opex.unversioned_dataset"), None
    )
    assert finding is not None
    assert finding.category == Category.OPERATIONAL_EXCELLENCE
    assert finding.severity == Severity.WARNING
    assert "smoke.yaml" in finding.evidence.get("files", [])


def test_unversioned_dataset_silent_when_all_versioned(workspace: Path) -> None:
    datasets = workspace / ".agentops" / "datasets"
    datasets.mkdir(parents=True)
    (datasets / "smoke.yaml").write_text(
        "version: 1\nname: smoke\n", encoding="utf-8"
    )

    findings = run_opex_workspace_check(workspace)
    assert "opex.unversioned_dataset" not in _ids(findings)


def test_unversioned_dataset_silent_when_no_datasets_dir(workspace: Path) -> None:
    findings = run_opex_workspace_check(workspace)
    assert "opex.unversioned_dataset" not in _ids(findings)


# ---------------------------------------------------------------------------
# unversioned_bundle
# ---------------------------------------------------------------------------


def test_unversioned_bundle_emitted_when_version_missing(workspace: Path) -> None:
    bundles = workspace / ".agentops" / "bundles"
    bundles.mkdir(parents=True)
    (bundles / "rag.yaml").write_text(
        "name: rag\nevaluators: []\n", encoding="utf-8"
    )
    findings = run_opex_workspace_check(workspace)
    finding = next(
        (f for f in findings if f.id == "opex.unversioned_bundle"), None
    )
    assert finding is not None
    assert "rag.yaml" in finding.evidence.get("files", [])


def test_unversioned_bundle_silent_when_all_versioned(workspace: Path) -> None:
    bundles = workspace / ".agentops" / "bundles"
    bundles.mkdir(parents=True)
    (bundles / "rag.yaml").write_text("version: 1\nname: rag\n", encoding="utf-8")
    assert "opex.unversioned_bundle" not in _ids(run_opex_workspace_check(workspace))


def test_unversioned_bundle_silent_when_no_bundles_dir(workspace: Path) -> None:
    assert "opex.unversioned_bundle" not in _ids(run_opex_workspace_check(workspace))


# ---------------------------------------------------------------------------
# results_dir_bloat
# ---------------------------------------------------------------------------


def test_results_dir_bloat_silent_under_threshold(workspace: Path) -> None:
    results = workspace / ".agentops" / "results"
    results.mkdir(parents=True)
    for i in range(10):
        (results / f"run-{i}").mkdir()
    assert "opex.results_dir_bloat" not in _ids(run_opex_workspace_check(workspace))


def test_results_dir_bloat_emitted_above_threshold(workspace: Path) -> None:
    results = workspace / ".agentops" / "results"
    results.mkdir(parents=True)
    for i in range(60):
        (results / f"run-{i:03d}").mkdir()
    finding = next(
        (f for f in run_opex_workspace_check(workspace) if f.id == "opex.results_dir_bloat"),
        None,
    )
    assert finding is not None
    assert finding.severity == Severity.WARNING
    assert finding.evidence["run_count"] == 60


def test_results_dir_bloat_promotes_to_critical(workspace: Path) -> None:
    results = workspace / ".agentops" / "results"
    results.mkdir(parents=True)
    for i in range(250):
        (results / f"run-{i:04d}").mkdir()
    finding = next(
        f for f in run_opex_workspace_check(workspace) if f.id == "opex.results_dir_bloat"
    )
    assert finding.severity == Severity.CRITICAL


def test_results_dir_bloat_ignores_latest(workspace: Path) -> None:
    """`latest/` is a pointer dir — it shouldn't count toward the threshold."""
    results = workspace / ".agentops" / "results"
    results.mkdir(parents=True)
    (results / "latest").mkdir()
    for i in range(40):
        (results / f"run-{i}").mkdir()
    # Total entries = 41 but the rule counts 40, well below the 50 threshold.
    assert "opex.results_dir_bloat" not in _ids(run_opex_workspace_check(workspace))


# ---------------------------------------------------------------------------
# pruned cosmetic changelog rule
# ---------------------------------------------------------------------------


def test_no_changelog_is_not_a_default_doctor_finding(
    workspace: Path,
) -> None:
    (workspace / ".git").mkdir()
    findings = run_opex_workspace_check(workspace)
    assert "opex.no_changelog" not in _ids(findings)


def test_changelog_presence_does_not_affect_default_findings(workspace: Path) -> None:
    (workspace / ".git").mkdir()
    (workspace / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    assert "opex.no_changelog" not in _ids(run_opex_workspace_check(workspace))


def test_missing_git_repo_does_not_emit_changelog_finding(workspace: Path) -> None:
    # No .git/ → scratch workspace; the rule stays quiet.
    assert "opex.no_changelog" not in _ids(run_opex_workspace_check(workspace))


# ---------------------------------------------------------------------------
# workflow_concurrency_lock
# ---------------------------------------------------------------------------


def test_workflow_concurrency_emitted_when_block_missing(workspace: Path) -> None:
    wf_dir = workspace / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "agentops-pr.yml").write_text(
        "name: pr\non: [pull_request]\njobs: {}\n", encoding="utf-8"
    )
    finding = next(
        f
        for f in run_opex_workspace_check(workspace)
        if f.id == "opex.workflow_concurrency_lock"
    )
    assert "agentops-pr.yml" in finding.evidence.get("files", [])


def test_workflow_concurrency_silent_when_block_present(workspace: Path) -> None:
    wf_dir = workspace / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "agentops-pr.yml").write_text(
        "name: pr\nconcurrency:\n  group: pr\n  cancel-in-progress: true\n"
        "on: [pull_request]\njobs: {}\n",
        encoding="utf-8",
    )
    assert "opex.workflow_concurrency_lock" not in _ids(
        run_opex_workspace_check(workspace)
    )


# ---------------------------------------------------------------------------
# workflow_action_sha_pinning
# ---------------------------------------------------------------------------


def test_workflow_sha_pinning_emitted_for_tag_refs(workspace: Path) -> None:
    wf_dir = workspace / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "agentops-pr.yml").write_text(
        "name: pr\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: actions/setup-python@v5\n",
        encoding="utf-8",
    )
    finding = next(
        f
        for f in run_opex_workspace_check(workspace)
        if f.id == "opex.workflow_action_sha_pinning"
    )
    offenders = finding.evidence.get("offenders", [])
    assert len(offenders) == 2
    assert all("@v" in o["ref"] for o in offenders)


def test_workflow_sha_pinning_silent_for_sha_pinned(workspace: Path) -> None:
    wf_dir = workspace / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    pinned_sha = "a" * 40
    (wf_dir / "agentops-pr.yml").write_text(
        "name: pr\njobs:\n  build:\n    steps:\n"
        f"      - uses: actions/checkout@{pinned_sha}\n",
        encoding="utf-8",
    )
    assert "opex.workflow_action_sha_pinning" not in _ids(
        run_opex_workspace_check(workspace)
    )


def test_workflow_sha_pinning_skips_local_actions(workspace: Path) -> None:
    wf_dir = workspace / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "agentops-pr.yml").write_text(
        "name: pr\njobs:\n  build:\n    steps:\n"
        "      - uses: ./.github/actions/local\n",
        encoding="utf-8",
    )
    assert "opex.workflow_action_sha_pinning" not in _ids(
        run_opex_workspace_check(workspace)
    )


# ---------------------------------------------------------------------------
# AI.26 max_tokens limit (opex.max_tokens_undefined)
# ---------------------------------------------------------------------------


def test_max_tokens_undefined_fires_when_bundle_lacks_max_tokens(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: my-agent:2\n", encoding="utf-8"
    )
    bundles = tmp_path / ".agentops" / "bundles"
    bundles.mkdir(parents=True)
    (bundles / "default.yaml").write_text(
        "version: 1\nevaluators:\n  - GroundednessEvaluator\n",
        encoding="utf-8",
    )
    findings = run_opex_workspace_check(tmp_path)
    f = next((f for f in findings if f.id == "opex.max_tokens_undefined"), None)
    assert f is not None
    assert "default.yaml" in f.evidence["files_without_max_tokens"][0]


def test_max_tokens_undefined_silent_when_every_file_declares_it(tmp_path: Path) -> None:
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: my-agent:2\nmodel: gpt-4o-mini\nmax_tokens: 800\n",
        encoding="utf-8",
    )
    bundles = tmp_path / ".agentops" / "bundles"
    bundles.mkdir(parents=True)
    (bundles / "default.yaml").write_text(
        "version: 1\nevaluators:\n  - GroundednessEvaluator\nmodel_config:\n  max_tokens: 500\n",
        encoding="utf-8",
    )
    findings = run_opex_workspace_check(tmp_path)
    assert not any(f.id == "opex.max_tokens_undefined" for f in findings)


def test_max_tokens_undefined_silent_when_no_model_shaped_files(tmp_path: Path) -> None:
    # Empty workspace - no model / evaluator / deployment keys anywhere.
    (tmp_path / "agentops.yaml").write_text(
        "version: 1\nagent: my-agent:2\n", encoding="utf-8"
    )
    findings = run_opex_workspace_check(tmp_path)
    assert not any(f.id == "opex.max_tokens_undefined" for f in findings)
