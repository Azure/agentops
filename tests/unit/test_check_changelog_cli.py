"""End-to-end tests for the ``check_changelog`` command line.

The pure decision functions are covered in ``test_check_changelog.py``. This
module drives the script as a subprocess against throwaway git repositories so
the parts CI actually depends on get exercised: argument parsing, environment
variable handling, ``git diff`` invocation, and process exit codes.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_changelog.py"

BASE_CHANGELOG = """\
# Changelog

## [Unreleased]

## [0.8.5] - 2026-08-07

### Fixed
- **An older fix.** Already published.
"""

CHANGELOG_ENTRY_UNDER_UNRELEASED = """\
# Changelog

## [Unreleased]

### Fixed
- **Something broke.** And now it does not.

## [0.8.5] - 2026-08-07

### Fixed
- **An older fix.** Already published.
"""

CHANGELOG_ENTRY_UNDER_RELEASED = """\
# Changelog

## [Unreleased]

## [0.8.5] - 2026-08-07

### Fixed
- **An older fix.** Already published.
- **Filed in the wrong section.** Never promoted.
"""


@pytest.fixture(scope="module")
def guard():
    spec = importlib.util.spec_from_file_location("check_changelog_cli", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_changelog_cli"] = module
    spec.loader.exec_module(module)
    return module


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo with the script installed and a ``base`` branch to diff from."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "src" / "agentops").mkdir(parents=True)
    shutil.copy2(SCRIPT_PATH, root / "scripts" / "check_changelog.py")

    _run_git(root, "init", "-q", "-b", "base")
    _run_git(root, "config", "user.email", "test@example.com")
    _run_git(root, "config", "user.name", "Test")
    _run_git(root, "config", "core.autocrlf", "false")

    (root / "CHANGELOG.md").write_text(BASE_CHANGELOG, encoding="utf-8")
    (root / "src" / "agentops" / "cli.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-q", "-m", "initial")
    _run_git(root, "checkout", "-q", "-b", "feature")
    return root


def _check_pr(
    repo: Path,
    *,
    title: str,
    labels: str = "",
    author: str = "human",
    base: str = "base",
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PR_TITLE=title, PR_LABELS=labels, PR_AUTHOR=author)
    return subprocess.run(
        [sys.executable, "scripts/check_changelog.py", "check-pr", "--base", base],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def _commit_all(repo: Path, message: str) -> None:
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", message)


# --- check-pr exit codes ---------------------------------------------------


def test_fix_with_unreleased_entry_exits_zero(repo: Path) -> None:
    (repo / "src" / "agentops" / "cli.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text(
        CHANGELOG_ENTRY_UNDER_UNRELEASED, encoding="utf-8"
    )
    _commit_all(repo, "fix: stop the crash")

    result = _check_pr(repo, title="fix: stop the crash")
    assert result.returncode == 0, result.stderr
    assert "Unreleased" in result.stdout


def test_fix_without_entry_exits_one(repo: Path) -> None:
    (repo / "src" / "agentops" / "cli.py").write_text("VALUE = 2\n", encoding="utf-8")
    _commit_all(repo, "fix: stop the crash")

    result = _check_pr(repo, title="fix: stop the crash")
    assert result.returncode == 1
    assert "CHANGELOG.md was not modified" in result.stderr


def test_entry_under_released_heading_exits_one(repo: Path) -> None:
    (repo / "src" / "agentops" / "cli.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text(CHANGELOG_ENTRY_UNDER_RELEASED, encoding="utf-8")
    _commit_all(repo, "fix: stop the crash")

    result = _check_pr(repo, title="fix: stop the crash")
    assert result.returncode == 1
    assert "no new bullet landed under" in result.stderr


def test_docs_only_change_exits_zero(repo: Path) -> None:
    (repo / "docs" / "guide.md").write_text("# Guide\n\nMore.\n", encoding="utf-8")
    _commit_all(repo, "docs: expand the guide")

    result = _check_pr(repo, title="docs: expand the guide")
    assert result.returncode == 0, result.stderr
    assert "not required" in result.stdout


def test_skip_label_exits_zero(repo: Path) -> None:
    (repo / "src" / "agentops" / "cli.py").write_text("VALUE = 2\n", encoding="utf-8")
    _commit_all(repo, "fix: stop the crash")

    result = _check_pr(
        repo, title="fix: stop the crash", labels='["bug","no-changelog"]'
    )
    assert result.returncode == 0, result.stderr
    assert "no-changelog" in result.stdout


def test_dependabot_author_exits_zero(repo: Path) -> None:
    (repo / "src" / "agentops" / "cli.py").write_text("VALUE = 2\n", encoding="utf-8")
    _commit_all(repo, "chore(deps): bump cryptography")

    result = _check_pr(
        repo,
        title="chore(deps): bump cryptography from 48 to 50",
        author="dependabot[bot]",
    )
    assert result.returncode == 0, result.stderr


def test_empty_diff_fails_loudly(repo: Path) -> None:
    """No commits on the branch means the guard saw nothing. That is a failure."""
    result = _check_pr(repo, title="fix: stop the crash")
    assert result.returncode == 1
    assert "No changed files detected against `base`" in result.stderr


def test_multi_hunk_changelog_entry_is_found(repo: Path) -> None:
    """The added bullet sits in the second hunk, far below an unrelated first."""
    changelog = (
        "# Changelog\n"
        "\n"
        "Some preamble that changed.\n"
        "\n"
        "## [Unreleased]\n"
        "\n"
        "### Fixed\n"
        "- **Something broke.** And now it does not.\n"
        "\n"
        "## [0.8.5] - 2026-08-07\n"
        "\n"
        "### Fixed\n"
        "- **An older fix.** Already published.\n"
    )
    (repo / "src" / "agentops" / "cli.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    _commit_all(repo, "fix: stop the crash")

    diff = subprocess.run(
        ["git", "diff", "--unified=0", "base...HEAD", "--", "CHANGELOG.md"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    assert diff.count("@@ -") >= 2, diff

    result = _check_pr(repo, title="fix: stop the crash")
    assert result.returncode == 0, result.stderr


# --- check-unreleased exit codes -------------------------------------------


def test_check_unreleased_passes_with_content(repo: Path, tmp_path: Path) -> None:
    path = tmp_path / "filled.md"
    path.write_text(CHANGELOG_ENTRY_UNDER_UNRELEASED, encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_changelog.py",
            "check-unreleased",
            "--path",
            str(path),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr


def test_check_unreleased_fails_when_empty(repo: Path) -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_changelog.py", "check-unreleased"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 1
    assert "is empty" in result.stderr


def test_check_unreleased_fails_on_missing_file(repo: Path, tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_changelog.py",
            "check-unreleased",
            "--path",
            str(tmp_path / "nope.md"),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 1
    assert "does not exist" in result.stderr


def test_no_subcommand_is_a_usage_error(repo: Path) -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_changelog.py"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 2


# --- label parsing ---------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        ("   ", []),
        ('["bug","no-changelog"]', ["bug", "no-changelog"]),
        ("[]", []),
        ("bug, no-changelog", ["bug", "no-changelog"]),
        ("no-changelog", ["no-changelog"]),
        ('{"name": "bug"}', []),
        ("not json at all", ["not json at all"]),
    ],
)
def test_parse_labels(guard, raw, expected) -> None:
    assert guard._parse_labels(raw) == expected


def test_parse_labels_feeds_the_skip_path(guard) -> None:
    labels = guard._parse_labels('["no-changelog"]')
    required, reason = guard.entry_required(
        "fix: stop the crash", labels, "human", ["src/agentops/cli.py"]
    )
    assert required is False
    assert guard.SKIP_LABEL in reason


# --- git helper ------------------------------------------------------------


def test_git_helper_decodes_utf8(guard, tmp_path: Path) -> None:
    """``_git`` must not fall back to the locale codec; Windows uses cp1252.

    U+201D encodes to ``e2 80 9d`` in UTF-8, and ``0x9d`` is undefined in
    cp1252, so a locale decode raises rather than merely producing mojibake.
    """
    subject = "fix: quote the \u201cthing\u201d properly"
    root = tmp_path / "utf8"
    root.mkdir()
    _run_git(root, "init", "-q", "-b", "base")
    _run_git(root, "config", "user.email", "test@example.com")
    _run_git(root, "config", "user.name", "Test")
    (root / "file.md").write_text("body\n", encoding="utf-8")
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-q", "-m", subject)

    original = guard.REPO_ROOT
    try:
        guard.REPO_ROOT = root
        out = guard._git("log", "-1", "--format=%s")
    finally:
        guard.REPO_ROOT = original

    assert out.strip() == subject
