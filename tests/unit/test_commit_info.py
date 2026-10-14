"""Tests for commit metadata capture (``pipeline.commit_info``)."""

from __future__ import annotations

import json
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
    monkeypatch.setenv("GITHUB_SHA", "sha-from-github")
    monkeypatch.setenv("BUILD_SOURCEVERSION", "sha-from-ado")

    assert commit_info._ci_git_sha() == "sha-from-github"

    monkeypatch.delenv("GITHUB_SHA", raising=False)
    assert commit_info._ci_git_sha() == "sha-from-ado"


def test_dotted_build_sourceversion_is_not_a_real_env_var_name(monkeypatch):
    """Azure Pipelines exposes ``Build.SourceVersion`` to scripts as the
    env var ``BUILD_SOURCEVERSION`` (dots become underscores, uppercased) -
    the literal dotted name is never actually set by the platform, so it
    must not be in ``_CI_SHA_ENV_VARS`` and must not be picked up even if
    something else in the environment happens to set it."""
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.delenv("BUILD_SOURCEVERSION", raising=False)
    monkeypatch.setenv("Build.SourceVersion", "should-never-be-read")

    assert "Build.SourceVersion" not in commit_info._CI_SHA_ENV_VARS
    assert commit_info._ci_git_sha() is None


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


def _write_pull_request_event(path: Path, *, head_sha: str) -> None:
    path.write_text(
        json.dumps({"pull_request": {"head": {"sha": head_sha}}}),
        encoding="utf-8",
    )


def test_pull_request_event_uses_head_sha_not_merge_commit(
    tmp_path, monkeypatch
):
    """On ``pull_request`` events, ``GITHUB_SHA`` is the temporary merge
    commit, not the PR branch's real head - the event payload's
    ``pull_request.head.sha`` must win instead."""
    repo = _init_repo(tmp_path)
    head_sha = _run(["rev-parse", "HEAD"], cwd=repo)
    merge_commit_sha = "f" * 40

    event_path = tmp_path / "event.json"
    _write_pull_request_event(event_path, head_sha=head_sha)

    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_SHA", merge_commit_sha)
    monkeypatch.delenv("BUILD_SOURCEVERSION", raising=False)
    monkeypatch.delenv("Build.SourceVersion", raising=False)

    info = commit_info.resolve_commit_info(workspace=repo)

    assert info is not None
    assert info.sha == head_sha
    assert info.sha != merge_commit_sha
    assert info.source == "ci"


def test_pull_request_event_falls_back_to_github_sha_when_payload_unusable(
    tmp_path, monkeypatch
):
    repo = _init_repo(tmp_path)
    sha = _run(["rev-parse", "HEAD"], cwd=repo)

    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setenv("GITHUB_SHA", sha)
    monkeypatch.delenv("BUILD_SOURCEVERSION", raising=False)
    monkeypatch.delenv("Build.SourceVersion", raising=False)

    info = commit_info.resolve_commit_info(workspace=repo)

    assert info is not None
    assert info.sha == sha
    assert info.source == "ci"


def test_pull_request_event_falls_back_to_github_sha_when_head_not_resolvable(
    tmp_path, monkeypatch
):
    """The generated PR workflow's default checkout has no `ref`/
    `fetch-depth` override, so the real PR head commit object usually
    isn't present locally - only the merge commit GITHUB_SHA points at
    is. Using an unresolvable head_sha anyway would make `git show` fail
    and commit capture return nothing for the whole run (worse than the
    wrong-but-resolvable GITHUB_SHA attribution this replaces), so it must
    fall back to GITHUB_SHA instead."""
    repo = _init_repo(tmp_path)
    merge_commit_sha = _run(["rev-parse", "HEAD"], cwd=repo)
    unresolvable_head_sha = "9" * 40  # not a real commit in this repo

    event_path = tmp_path / "event.json"
    _write_pull_request_event(event_path, head_sha=unresolvable_head_sha)

    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_SHA", merge_commit_sha)
    monkeypatch.delenv("BUILD_SOURCEVERSION", raising=False)
    monkeypatch.delenv("Build.SourceVersion", raising=False)

    info = commit_info.resolve_commit_info(workspace=repo)

    assert info is not None
    assert info.sha == merge_commit_sha
    assert info.source == "ci"


def test_non_pull_request_event_still_uses_github_sha_directly(
    tmp_path, monkeypatch
):
    """Push events have no ``pull_request`` payload to read - GITHUB_SHA is
    already the right commit and must be used as-is."""
    repo = _init_repo(tmp_path)
    sha = _run(["rev-parse", "HEAD"], cwd=repo)

    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.setenv("GITHUB_SHA", sha)
    monkeypatch.delenv("BUILD_SOURCEVERSION", raising=False)
    monkeypatch.delenv("Build.SourceVersion", raising=False)

    info = commit_info.resolve_commit_info(workspace=repo)

    assert info is not None
    assert info.sha == sha
    assert info.source == "ci"
