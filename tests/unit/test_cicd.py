from pathlib import Path

from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.services.cicd import generate_cicd_workflow


runner = CliRunner()

_WORKFLOW_PATH = ".github/workflows/agentops-eval.yml"


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


def test_cli_config_cicd_creates_workflow(tmp_path: Path) -> None:
    result = runner.invoke(app, ["config", "cicd", "--dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "created" in result.stdout

    workflow = tmp_path / _WORKFLOW_PATH
    assert workflow.exists()


def test_cli_config_cicd_skips_existing(tmp_path: Path) -> None:
    workflow = tmp_path / _WORKFLOW_PATH
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text("existing", encoding="utf-8")

    result = runner.invoke(app, ["config", "cicd", "--dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "skipped" in result.stdout


def test_cli_config_cicd_force_overwrites(tmp_path: Path) -> None:
    workflow = tmp_path / _WORKFLOW_PATH
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text("old", encoding="utf-8")

    result = runner.invoke(app, ["config", "cicd", "--force", "--dir", str(tmp_path)])

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
