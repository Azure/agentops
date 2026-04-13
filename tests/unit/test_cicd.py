from pathlib import Path

from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.services.cicd import generate_cicd_workflow, generate_cicd_workflows


runner = CliRunner()

_WORKFLOW_PATH = ".github/workflows/agentops-eval.yml"
_CI_WORKFLOW_PATH = ".github/workflows/agentops-eval-ci.yml"
_CD_WORKFLOW_PATH = ".github/workflows/agentops-eval-cd.yml"


def _scaffold_agentops_workspace(tmp_path: Path, bundles: list[str] | None = None, run_configs: list[str] | None = None) -> None:
    """Create a minimal .agentops/ workspace with optional bundles and run configs."""
    agentops_dir = tmp_path / ".agentops"
    agentops_dir.mkdir(parents=True, exist_ok=True)

    # Default run.yaml
    (agentops_dir / "run.yaml").write_text("version: 1\n", encoding="utf-8")

    if bundles:
        bundles_dir = agentops_dir / "bundles"
        bundles_dir.mkdir(parents=True, exist_ok=True)
        for name in bundles:
            (bundles_dir / name).write_text("version: 1\n", encoding="utf-8")

    if run_configs:
        for name in run_configs:
            (agentops_dir / name).write_text("version: 1\n", encoding="utf-8")


def test_generate_cicd_creates_workflow(tmp_path: Path) -> None:
    result = generate_cicd_workflow(directory=tmp_path)

    workflow = tmp_path / _WORKFLOW_PATH
    assert workflow.exists()
    assert len(result.created_files) == 1
    assert len(result.skipped_files) == 0

    content = workflow.read_text(encoding="utf-8")
    assert "agentops eval run" in content
    assert "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT" in content


def test_generate_cicd_skips_existing(tmp_path: Path) -> None:
    workflow = tmp_path / _WORKFLOW_PATH
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text("existing content", encoding="utf-8")

    result = generate_cicd_workflow(directory=tmp_path, force=False)

    assert len(result.skipped_files) == 1
    assert len(result.created_files) == 0
    assert workflow.read_text(encoding="utf-8") == "existing content"


def test_generate_cicd_overwrites_with_force(tmp_path: Path) -> None:
    workflow = tmp_path / _WORKFLOW_PATH
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text("old content", encoding="utf-8")

    result = generate_cicd_workflow(directory=tmp_path, force=True)

    assert len(result.overwritten_files) == 1
    assert len(result.skipped_files) == 0

    content = workflow.read_text(encoding="utf-8")
    assert "agentops eval run" in content
    assert content != "old content"


def test_cli_workflow_generate_creates_workflow(tmp_path: Path) -> None:
    result = runner.invoke(app, ["workflow", "generate", "--dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "created" in result.stdout

    workflow = tmp_path / _WORKFLOW_PATH
    assert workflow.exists()


def test_cli_workflow_generate_skips_existing(tmp_path: Path) -> None:
    workflow = tmp_path / _WORKFLOW_PATH
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text("existing", encoding="utf-8")

    result = runner.invoke(app, ["workflow", "generate", "--dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "skipped" in result.stdout


def test_cli_workflow_generate_force_overwrites(tmp_path: Path) -> None:
    workflow = tmp_path / _WORKFLOW_PATH
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text("old", encoding="utf-8")

    result = runner.invoke(app, ["workflow", "generate", "--force", "--dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "overwritten" in result.stdout


def test_workflow_template_has_required_features(tmp_path: Path) -> None:
    """Verify the generated workflow meets issue #20 acceptance criteria."""
    generate_cicd_workflow(directory=tmp_path)

    content = (tmp_path / _WORKFLOW_PATH).read_text(encoding="utf-8")

    # Triggers
    assert "pull_request" in content
    assert "workflow_dispatch" in content

    # Python 3.11
    assert "3.11" in content

    # Install
    assert "agentops-toolkit" in content

    # Eval command
    assert "agentops eval run" in content

    # Artifacts
    assert "results.json" in content
    assert "report.md" in content

    # Exit code handling
    assert "EXIT_CODE" in content
    assert "exit $EXIT_CODE" in content

    # OIDC auth
    assert "azure/login@v2" in content


# ---------------------------------------------------------------------------
# generate_cicd_workflows — multi-template generation
# ---------------------------------------------------------------------------


def test_generate_workflows_pr_only_default(tmp_path: Path) -> None:
    """Minimal workspace (no extra bundles) → only PR template generated."""
    _scaffold_agentops_workspace(tmp_path, bundles=["model_quality_baseline.yaml"])

    result = generate_cicd_workflows(directory=tmp_path)

    assert len(result.created_files) == 1
    assert (tmp_path / _WORKFLOW_PATH).exists()
    assert not (tmp_path / _CI_WORKFLOW_PATH).exists()
    assert not (tmp_path / _CD_WORKFLOW_PATH).exists()


def test_generate_workflows_auto_detects_ci_and_cd(tmp_path: Path) -> None:
    """Multiple bundles in workspace → PR + CI + CD templates generated."""
    _scaffold_agentops_workspace(
        tmp_path,
        bundles=["model_quality_baseline.yaml", "safe_agent_baseline.yaml"],
    )

    result = generate_cicd_workflows(directory=tmp_path)

    created_names = {p.name for p in result.created_files}
    assert "agentops-eval.yml" in created_names
    assert "agentops-eval-ci.yml" in created_names
    assert "agentops-eval-cd.yml" in created_names
    assert (tmp_path / _WORKFLOW_PATH).exists()
    assert (tmp_path / _CI_WORKFLOW_PATH).exists()
    assert (tmp_path / _CD_WORKFLOW_PATH).exists()


def test_generate_workflows_auto_detects_ci(tmp_path: Path) -> None:
    """Multiple bundles → PR + CI + CD templates generated."""
    _scaffold_agentops_workspace(
        tmp_path,
        bundles=["model_quality_baseline.yaml", "rag_quality_baseline.yaml"],
    )

    result = generate_cicd_workflows(directory=tmp_path)

    created_names = {p.name for p in result.created_files}
    assert "agentops-eval.yml" in created_names
    assert "agentops-eval-ci.yml" in created_names
    assert "agentops-eval-cd.yml" in created_names


def test_generate_workflows_creates_all_templates(tmp_path: Path) -> None:
    """Explicit kinds=all → all three templates generated."""
    result = generate_cicd_workflows(
        directory=tmp_path,
        kinds=["pr", "ci", "cd"],
    )

    assert len(result.created_files) == 3
    assert (tmp_path / _WORKFLOW_PATH).exists()
    assert (tmp_path / _CI_WORKFLOW_PATH).exists()
    assert (tmp_path / _CD_WORKFLOW_PATH).exists()


def test_generate_workflows_skips_existing_without_force(tmp_path: Path) -> None:
    """Existing files are skipped without --force, per file."""
    # Pre-create the PR workflow
    pr_workflow = tmp_path / _WORKFLOW_PATH
    pr_workflow.parent.mkdir(parents=True, exist_ok=True)
    pr_workflow.write_text("existing", encoding="utf-8")

    result = generate_cicd_workflows(
        directory=tmp_path,
        kinds=["pr", "ci"],
        force=False,
    )

    assert len(result.skipped_files) == 1
    assert len(result.created_files) == 1
    assert pr_workflow.read_text(encoding="utf-8") == "existing"
    assert (tmp_path / _CI_WORKFLOW_PATH).exists()


def test_generate_workflows_force_overwrites_all(tmp_path: Path) -> None:
    """Force overwrites all existing files."""
    for rel in (_WORKFLOW_PATH, _CI_WORKFLOW_PATH, _CD_WORKFLOW_PATH):
        wf = tmp_path / rel
        wf.parent.mkdir(parents=True, exist_ok=True)
        wf.write_text("old", encoding="utf-8")

    result = generate_cicd_workflows(
        directory=tmp_path,
        kinds=["pr", "ci", "cd"],
        force=True,
    )

    assert len(result.overwritten_files) == 3
    assert len(result.skipped_files) == 0

    for rel in (_WORKFLOW_PATH, _CI_WORKFLOW_PATH, _CD_WORKFLOW_PATH):
        content = (tmp_path / rel).read_text(encoding="utf-8")
        assert content != "old"
        assert "agentops" in content.lower()


def test_ci_template_has_required_features(tmp_path: Path) -> None:
    """Verify the CI template has expected structure."""
    generate_cicd_workflows(directory=tmp_path, kinds=["ci"])

    content = (tmp_path / _CI_WORKFLOW_PATH).read_text(encoding="utf-8")

    # Triggers
    assert "push" in content
    assert "workflow_dispatch" in content

    # Branches
    assert "develop" in content
    assert "main" in content

    # Core features
    assert "3.11" in content
    assert "agentops-toolkit" in content
    assert "agentops eval run" in content
    assert "EXIT_CODE" in content
    assert "azure/login@v2" in content

    # Artifacts
    assert "results.json" in content
    assert "report.md" in content

    # Matrix strategy (commented out but present)
    assert "matrix" in content


def test_cd_template_has_required_features(tmp_path: Path) -> None:
    """Verify the CD template has expected structure."""
    generate_cicd_workflows(directory=tmp_path, kinds=["cd"])

    content = (tmp_path / _CD_WORKFLOW_PATH).read_text(encoding="utf-8")

    # Triggers
    assert "push" in content
    assert "workflow_dispatch" in content

    # Branches
    assert "main" in content

    # Core features
    assert "3.11" in content
    assert "agentops-toolkit" in content
    assert "agentops eval run" in content
    assert "EXIT_CODE" in content
    assert "azure/login@v2" in content

    # Two-job structure
    assert "safety-qa" in content
    assert "deploy" in content
    assert "needs: [safety-qa]" in content

    # Artifacts
    assert "results.json" in content
    assert "report.md" in content


def test_cli_workflow_generate_creates_multiple(tmp_path: Path) -> None:
    """CLI generates multiple workflows when workspace triggers auto-detection."""
    _scaffold_agentops_workspace(
        tmp_path,
        bundles=["model_quality_baseline.yaml", "safe_agent_baseline.yaml"],
    )

    result = runner.invoke(app, ["workflow", "generate", "--dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "created" in result.stdout
    assert (tmp_path / _WORKFLOW_PATH).exists()
    assert (tmp_path / _CD_WORKFLOW_PATH).exists()


def test_cli_workflow_generate_shows_all_files(tmp_path: Path) -> None:
    """CLI output lists all generated files."""
    _scaffold_agentops_workspace(
        tmp_path,
        bundles=["model_quality_baseline.yaml", "rag_quality_baseline.yaml"],
    )

    result = runner.invoke(app, ["workflow", "generate", "--dir", str(tmp_path)])

    assert result.exit_code == 0
    # Should mention all created files
    assert result.stdout.count("+ created") >= 2
