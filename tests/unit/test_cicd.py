"""Tests for `agentops workflow generate` (4-template GenAIOps GitFlow scaffold)."""

from pathlib import Path

import yaml
from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.services.cicd import (
    ALL_KINDS,
    generate_cicd_workflow,
    generate_cicd_workflows,
)


runner = CliRunner()

_WORKFLOW_DIR = ".github/workflows"
_PR_PATH = f"{_WORKFLOW_DIR}/agentops-pr.yml"
_DEV_PATH = f"{_WORKFLOW_DIR}/agentops-deploy-dev.yml"
_QA_PATH = f"{_WORKFLOW_DIR}/agentops-deploy-qa.yml"
_PROD_PATH = f"{_WORKFLOW_DIR}/agentops-deploy-prod.yml"

ALL_PATHS = (_PR_PATH, _DEV_PATH, _QA_PATH, _PROD_PATH)


# ---------------------------------------------------------------------------
# generate_cicd_workflows — defaults to all four
# ---------------------------------------------------------------------------


def test_default_generates_all_four_templates(tmp_path: Path) -> None:
    result = generate_cicd_workflows(directory=tmp_path)

    assert {p.name for p in result.created_files} == {
        "agentops-pr.yml",
        "agentops-deploy-dev.yml",
        "agentops-deploy-qa.yml",
        "agentops-deploy-prod.yml",
    }
    for rel in ALL_PATHS:
        assert (tmp_path / rel).exists()


def test_kinds_filter_subset(tmp_path: Path) -> None:
    result = generate_cicd_workflows(directory=tmp_path, kinds=["pr", "dev"])

    assert {p.name for p in result.created_files} == {
        "agentops-pr.yml",
        "agentops-deploy-dev.yml",
    }
    assert (tmp_path / _PR_PATH).exists()
    assert (tmp_path / _DEV_PATH).exists()
    assert not (tmp_path / _QA_PATH).exists()
    assert not (tmp_path / _PROD_PATH).exists()


def test_kinds_unknown_value_is_ignored(tmp_path: Path) -> None:
    result = generate_cicd_workflows(directory=tmp_path, kinds=["pr", "bogus"])
    assert {p.name for p in result.created_files} == {"agentops-pr.yml"}


def test_kinds_dedupes(tmp_path: Path) -> None:
    result = generate_cicd_workflows(directory=tmp_path, kinds=["pr", "pr", "dev"])
    assert len(result.created_files) == 2


def test_skips_existing_without_force(tmp_path: Path) -> None:
    pr = tmp_path / _PR_PATH
    pr.parent.mkdir(parents=True, exist_ok=True)
    pr.write_text("existing", encoding="utf-8")

    result = generate_cicd_workflows(directory=tmp_path, kinds=["pr", "dev"])

    assert len(result.skipped_files) == 1
    assert len(result.created_files) == 1
    assert pr.read_text(encoding="utf-8") == "existing"
    assert (tmp_path / _DEV_PATH).exists()


def test_force_overwrites_all(tmp_path: Path) -> None:
    for rel in ALL_PATHS:
        wf = tmp_path / rel
        wf.parent.mkdir(parents=True, exist_ok=True)
        wf.write_text("old", encoding="utf-8")

    result = generate_cicd_workflows(directory=tmp_path, force=True)

    assert len(result.overwritten_files) == 4
    assert len(result.skipped_files) == 0
    for rel in ALL_PATHS:
        assert (tmp_path / rel).read_text(encoding="utf-8") != "old"


def test_legacy_generate_cicd_workflow_writes_pr_only(tmp_path: Path) -> None:
    result = generate_cicd_workflow(directory=tmp_path)
    assert {p.name for p in result.created_files} == {"agentops-pr.yml"}
    assert (tmp_path / _PR_PATH).exists()
    assert not (tmp_path / _DEV_PATH).exists()


# ---------------------------------------------------------------------------
# Template content checks
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_all_templates_are_valid_yaml(tmp_path: Path) -> None:
    generate_cicd_workflows(directory=tmp_path)
    for rel in ALL_PATHS:
        data = _read_yaml(tmp_path / rel)
        assert isinstance(data, dict)
        assert "jobs" in data
        # `on:` is parsed as the boolean True by yaml.safe_load when the key
        # is bare; just check the raw text contains the trigger block.
        text = (tmp_path / rel).read_text(encoding="utf-8")
        assert "\non:" in text


def test_pr_template_triggers_and_no_environment(tmp_path: Path) -> None:
    generate_cicd_workflows(directory=tmp_path, kinds=["pr"])
    content = (tmp_path / _PR_PATH).read_text(encoding="utf-8")

    assert "pull_request" in content
    # PR fires for develop, release/**, and main
    assert "develop" in content
    assert "release/**" in content
    assert "main" in content

    assert "agentops eval run" in content
    assert "agentops-toolkit" in content
    assert "azure/login@v2" in content
    assert "actions/setup-python@v5" in content
    assert "3.11" in content

    # PR template has no environment (it's a gate, not a deploy)
    assert "environment:" not in content

    # PR comment idempotency marker
    assert "<!-- agentops-pr-report -->" in content


def test_dev_template_triggers_and_environment(tmp_path: Path) -> None:
    generate_cicd_workflows(directory=tmp_path, kinds=["dev"])
    content = (tmp_path / _DEV_PATH).read_text(encoding="utf-8")

    assert "push" in content
    assert "develop" in content
    assert "environment: dev" in content
    assert "agentops eval run" in content
    # Has eval, build, deploy jobs
    assert "needs: eval" in content
    assert "needs: build" in content


def test_qa_template_triggers_and_environment(tmp_path: Path) -> None:
    generate_cicd_workflows(directory=tmp_path, kinds=["qa"])
    content = (tmp_path / _QA_PATH).read_text(encoding="utf-8")

    assert "push" in content
    assert "release/**" in content
    assert "environment: qa" in content
    assert "agentops eval run" in content
    assert "needs: eval" in content
    assert "needs: build" in content


def test_prod_template_triggers_and_environment_with_reviewer_hint(tmp_path: Path) -> None:
    generate_cicd_workflows(directory=tmp_path, kinds=["prod"])
    content = (tmp_path / _PROD_PATH).read_text(encoding="utf-8")

    assert "push" in content
    assert "main" in content
    assert "environment: production" in content
    assert "agentops eval run" in content
    # Prod uses safety-eval as the gate name and warns about reviewers
    assert "safety-eval" in content
    assert "Required reviewers" in content or "REQUIRED REVIEWERS" in content


def test_all_kinds_constant_matches_documented_set() -> None:
    assert set(ALL_KINDS) == {"pr", "dev", "qa", "prod"}


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_cli_default_creates_all_four(tmp_path: Path) -> None:
    result = runner.invoke(app, ["workflow", "generate", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.stdout
    assert result.stdout.count("+ created") == 4
    for rel in ALL_PATHS:
        assert (tmp_path / rel).exists()


def test_cli_kinds_subset(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["workflow", "generate", "--dir", str(tmp_path), "--kinds", "pr,prod"],
    )

    assert result.exit_code == 0, result.stdout
    assert (tmp_path / _PR_PATH).exists()
    assert (tmp_path / _PROD_PATH).exists()
    assert not (tmp_path / _DEV_PATH).exists()
    assert not (tmp_path / _QA_PATH).exists()


def test_cli_kinds_invalid_value_fails(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["workflow", "generate", "--dir", str(tmp_path), "--kinds", "pr,nonsense"],
    )

    assert result.exit_code == 1
    assert "unknown" in result.stdout.lower() or "unknown" in (result.stderr or "").lower()


def test_cli_skips_existing_without_force(tmp_path: Path) -> None:
    pr = tmp_path / _PR_PATH
    pr.parent.mkdir(parents=True, exist_ok=True)
    pr.write_text("existing", encoding="utf-8")

    result = runner.invoke(app, ["workflow", "generate", "--dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "skipped" in result.stdout
    assert pr.read_text(encoding="utf-8") == "existing"


def test_cli_force_overwrites(tmp_path: Path) -> None:
    pr = tmp_path / _PR_PATH
    pr.parent.mkdir(parents=True, exist_ok=True)
    pr.write_text("old", encoding="utf-8")

    result = runner.invoke(
        app,
        ["workflow", "generate", "--dir", str(tmp_path), "--force"],
    )

    assert result.exit_code == 0
    assert "overwritten" in result.stdout


def test_cli_next_steps_mention_environments(tmp_path: Path) -> None:
    result = runner.invoke(app, ["workflow", "generate", "--dir", str(tmp_path)])

    assert result.exit_code == 0
    out = result.stdout
    assert "Next steps" in out
    assert "dev" in out and "qa" in out and "production" in out
    assert "OIDC" in out or "Workload Identity Federation" in out
    assert "branch" in out.lower()
