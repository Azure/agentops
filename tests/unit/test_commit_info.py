"""Tests for commit metadata capture (``pipeline.commit_info``)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentops.pipeline import commit_info


def _run(args: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["init"], cwd=repo)
    _run(["config", "user.email", "dev@example.com"], cwd=repo)
    _run(["config", "user.name", "Dev"], cwd=repo)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _run(["add", "README.md"], cwd=repo)
    _run(["commit", "-m", "Initial commit"], cwd=repo)
    return repo


def test_local_fallback_resolves_head_commit(tmp_path, monkeypatch):
    for env_var in commit_info._CI_SHA_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)
    repo = _init_repo(tmp_path)
    expected_sha = _run(["rev-parse", "HEAD"], cwd=repo)

    info = commit_info.resolve_commit_info(workspace=repo)

    assert info is not None
    assert info.sha == expected_sha
    assert info.source == "local"
    assert info.subject == "Initial commit"
    assert info.author == "Dev"
    assert info.short_sha and expected_sha.startswith(info.short_sha)


def test_ci_env_var_takes_precedence_over_local_head(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    first_sha = _run(["rev-parse", "HEAD"], cwd=repo)
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    _run(["commit", "-am", "Second commit"], cwd=repo)
    second_sha = _run(["rev-parse", "HEAD"], cwd=repo)
    assert first_sha != second_sha

    monkeypatch.setenv("GITHUB_SHA", first_sha)
    monkeypatch.delenv("BUILD_SOURCEVERSION", raising=False)
    monkeypatch.delenv("Build.SourceVersion", raising=False)

    info = commit_info.resolve_commit_info(workspace=repo)

    assert info is not None
    assert info.sha == first_sha
    assert info.source == "ci"
    assert info.subject == "Initial commit"


def test_env_var_precedence_order(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)
    sha = _run(["rev-parse", "HEAD"], cwd=repo)

    monkeypatch.setenv("GITHUB_SHA", sha)
    monkeypatch.setenv("BUILD_SOURCEVERSION", "some-other-sha")
    monkeypatch.setenv("Build.SourceVersion", "yet-another-sha")

    assert commit_info._ci_git_sha() == sha

    monkeypatch.delenv("GITHUB_SHA", raising=False)
    assert commit_info._ci_git_sha() == "some-other-sha"

    monkeypatch.delenv("BUILD_SOURCEVERSION", raising=False)
    assert commit_info._ci_git_sha() == "yet-another-sha"


def test_non_git_workspace_returns_none_without_error(tmp_path, monkeypatch):
    for env_var in commit_info._CI_SHA_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)
    empty_dir = tmp_path / "not-a-repo"
    empty_dir.mkdir()

    info = commit_info.resolve_commit_info(workspace=empty_dir)

    assert info is None


def test_missing_git_binary_returns_none_without_raising(tmp_path, monkeypatch):
    for env_var in commit_info._CI_SHA_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)

    def _raise(*args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(commit_info.subprocess, "run", _raise)

    info = commit_info.resolve_commit_info(workspace=tmp_path)

    assert info is None


def test_ci_sha_not_resolvable_locally_returns_none(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("GITHUB_SHA", "0" * 40)
    monkeypatch.delenv("BUILD_SOURCEVERSION", raising=False)
    monkeypatch.delenv("Build.SourceVersion", raising=False)

    info = commit_info.resolve_commit_info(workspace=repo)

    assert info is None


@pytest.mark.parametrize("env_var", list(commit_info._CI_SHA_ENV_VARS))
def test_each_ci_env_var_is_recognized(tmp_path, monkeypatch, env_var):
    repo = _init_repo(tmp_path)
    sha = _run(["rev-parse", "HEAD"], cwd=repo)
    for other in commit_info._CI_SHA_ENV_VARS:
        monkeypatch.delenv(other, raising=False)
    monkeypatch.setenv(env_var, sha)

    info = commit_info.resolve_commit_info(workspace=repo)

    assert info is not None
    assert info.sha == sha
    assert info.source == "ci"
