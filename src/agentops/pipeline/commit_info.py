"""Commit metadata capture for evaluation runs.

Generalizes the CI environment-variable lookup already used for Foundry
prompt-agent deploy gating (``prompt_deploy._git_sha()``) so every
``agentops eval run`` invocation can record which commit it was produced
from, for both CI and local execution.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Literal, Optional

from agentops.core.results import CommitInfo

_CI_SHA_ENV_VARS = ("GITHUB_SHA", "BUILD_SOURCEVERSION")
_FIELD_SEP = "\x1f"
_GIT_SHOW_FORMAT = f"%H{_FIELD_SEP}%h{_FIELD_SEP}%s{_FIELD_SEP}%an{_FIELD_SEP}%aI"
_GIT_TIMEOUT_SECONDS = 5


def _pull_request_head_sha() -> Optional[str]:
    """The PR's head commit, read from the GitHub Actions event payload.

    On ``pull_request`` events ``GITHUB_SHA`` is the temporary merge commit
    GitHub synthesizes for the PR, not the commit the PR branch is actually
    at - so it must not be used to attribute a run to "the commit under
    review". ``GITHUB_EVENT_PATH`` points at a JSON payload containing the
    real head SHA at ``pull_request.head.sha``.
    """
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    try:
        with open(event_path, "r", encoding="utf-8") as handle:
            event = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(event, dict):
        return None
    pull_request = event.get("pull_request")
    if not isinstance(pull_request, dict):
        return None
    head = pull_request.get("head")
    if not isinstance(head, dict):
        return None
    sha = head.get("sha")
    return sha or None


def _ci_git_sha(*, workspace: Optional[Path] = None) -> Optional[str]:
    if os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
        head_sha = _pull_request_head_sha()
        # The generated PR workflow's default `actions/checkout` has no
        # `ref`/`fetch-depth` override, so it checks out only the
        # synthetic merge commit GITHUB_SHA points at - the real PR head
        # commit object usually isn't present locally at all. Using
        # head_sha anyway would make the later `git show` fail and commit
        # capture return nothing for the whole run, which is worse than
        # today's (wrong but resolvable) GITHUB_SHA attribution. Prefer
        # head_sha only when it's actually resolvable in this checkout
        # (e.g. the user customized the workflow to fetch full history or
        # the head ref); otherwise fall through to GITHUB_SHA below.
        if head_sha and commit_exists_locally(head_sha, workspace=workspace):
            return head_sha
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
    sha = _ci_git_sha(workspace=workspace)
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
