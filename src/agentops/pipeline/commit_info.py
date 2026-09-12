"""Commit metadata capture for evaluation runs.

Generalizes the CI environment-variable lookup already used for Foundry
prompt-agent deploy gating (``prompt_deploy._git_sha()``) so every
``agentops eval run`` invocation can record which commit it was produced
from, for both CI and local execution.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Literal, Optional

from agentops.core.results import CommitInfo

_CI_SHA_ENV_VARS = ("GITHUB_SHA", "BUILD_SOURCEVERSION", "Build.SourceVersion")
_FIELD_SEP = "\x1f"
_GIT_SHOW_FORMAT = f"%H{_FIELD_SEP}%h{_FIELD_SEP}%s{_FIELD_SEP}%an{_FIELD_SEP}%aI"
_GIT_TIMEOUT_SECONDS = 5


def _ci_git_sha() -> Optional[str]:
    for env_var in _CI_SHA_ENV_VARS:
        value = os.environ.get(env_var)
        if value:
            return value
    return None


def _run_git(args: list[str], *, cwd: Optional[Path]) -> Optional[str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # Covers a missing `git` binary, a timeout, and any other
        # subprocess-launch failure - commit capture is always best-effort.
        return None
    if completed.returncode != 0:
        return None
    output = completed.stdout.strip()
    return output or None


def resolve_commit_info(*, workspace: Optional[Path] = None) -> Optional[CommitInfo]:
    """Best-effort resolution of the commit an evaluation run was produced from.

    Tries CI-provided source-control environment variables first (the same
    ones already relied on for Foundry prompt-agent deploy gating), then
    falls back to the local git repository's current commit. Returns
    ``None`` - never raises - when neither source yields a resolvable
    commit, e.g. outside a git repository or when ``git`` is unavailable.
    """
    sha = _ci_git_sha()
    source: Literal["ci", "local"] = "ci"
    if not sha:
        sha = _run_git(["rev-parse", "HEAD"], cwd=workspace)
        source = "local"
    if not sha:
        return None

    show_output = _run_git(
        ["show", "-s", f"--format={_GIT_SHOW_FORMAT}", sha],
        cwd=workspace,
    )
    if not show_output:
        return None

    parts = show_output.split(_FIELD_SEP)
    if len(parts) != 5:
        return None
    full_sha, short_sha, subject, author, authored_at = parts
    if not full_sha or not short_sha:
        return None

    return CommitInfo(
        sha=full_sha,
        short_sha=short_sha,
        subject=subject,
        author=author,
        authored_at=authored_at,
        source=source,
    )


def commit_exists_locally(sha: str, *, workspace: Optional[Path] = None) -> bool:
    """Whether ``sha`` is resolvable as a commit in the local git history.

    Used to decide whether a fuller git-based diff is possible between two
    recorded commits, or whether the comparison must fall back to only the
    fields already recorded on each run (e.g. a shallow CI checkout that
    doesn't contain an older baseline commit). Returns ``False`` - never
    raises - when ``git`` is unavailable or the workspace isn't a repo.
    """
    try:
        completed = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0
