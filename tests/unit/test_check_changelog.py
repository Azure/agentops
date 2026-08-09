"""Tests for ``scripts/check_changelog.py``.

Locks in the decision table for the CI changelog guard. The guard exists
because ``cut-release.yml`` inserts a versioned heading beneath
``## [Unreleased]``; it never generates content. Releases 0.8.4 and 0.8.5 both
published empty sections.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_changelog.py"

CHANGELOG_WITH_ENTRY = """\
# Changelog

## [Unreleased]

### Fixed
- **Something broke.** And now it does not.

## [0.8.5] - 2026-08-07

### Fixed
- **An older fix.** Already published.
"""

CHANGELOG_EMPTY_UNRELEASED = """\
# Changelog

## [Unreleased]

## [0.8.5] - 2026-08-07

### Fixed
- **An older fix.** Already published.
"""


@pytest.fixture(scope="module")
def guard():
    spec = importlib.util.spec_from_file_location("check_changelog", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_changelog"] = module
    spec.loader.exec_module(module)
    return module


# --- PR title parsing ------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected_type", "expected_breaking"),
    [
        ("fix: stop the crash", "fix", False),
        ("fix(cli): stop the crash", "fix", False),
        ("feat!: drop python 3.10", "feat", True),
        ("docs: tidy the readme", "docs", False),
        ("chore(deps): bump cryptography", "chore", False),
        ("Bump mcp from 1.27.1 to 1.28.1", None, False),
        ("refactor: extract a helper\n\nBREAKING CHANGE: nope", "refactor", True),
    ],
)
def test_parse_commit_type(guard, title, expected_type, expected_breaking):
    assert guard.parse_commit_type(title) == (expected_type, expected_breaking)


# --- which PRs need an entry ----------------------------------------------


def test_fix_touching_src_requires_entry(guard):
    required, reason = guard.entry_required(
        "fix: stop the crash", [], "someone", ["src/agentops/cli.py"]
    )
    assert required is True
    assert "fix" in reason


def test_docs_only_pr_does_not_require_entry(guard):
    required, reason = guard.entry_required(
        "docs: tidy the readme", [], "someone", ["docs/release-process.md", "README.md"]
    )
    assert required is False
    assert "no shipped file changed" in reason


def test_fix_touching_only_docs_does_not_require_entry(guard):
    required, _ = guard.entry_required(
        "fix: typo in the tutorial", [], "someone", ["docs/tutorial.md"]
    )
    assert required is False


def test_skip_label_overrides_everything(guard):
    required, reason = guard.entry_required(
        "feat!: rewrite the world",
        ["no-changelog"],
        "someone",
        ["src/agentops/cli.py"],
    )
    assert required is False
    assert "no-changelog" in reason


def test_dependabot_is_exempt(guard):
    required, reason = guard.entry_required(
        "chore(deps): bump cryptography from 48 to 50",
        ["dependencies"],
        "dependabot[bot]",
        ["pyproject.toml", "uv.lock"],
    )
    assert required is False
    assert "automated author" in reason


def test_untyped_title_touching_src_requires_entry(guard):
    required, reason = guard.entry_required(
        "make the thing faster", [], "someone", ["src/agentops/core/evaluators.py"]
    )
    assert required is True
    assert "conventional-commit" in reason


def test_chore_touching_src_does_not_require_entry(guard):
    required, _ = guard.entry_required(
        "chore: reorder imports", [], "someone", ["src/agentops/cli.py"]
    )
    assert required is False


def test_breaking_change_always_requires_entry(guard):
    required, reason = guard.entry_required(
        "refactor!: drop the legacy loader", [], "someone", ["src/agentops/cli.py"]
    )
    assert required is True
    assert "breaking" in reason


def test_ci_only_change_does_not_require_entry(guard):
    required, _ = guard.entry_required(
        "fix: correct the workflow trigger",
        [],
        "someone",
        [".github/workflows/ci.yml"],
    )
    assert required is False


def test_github_plugin_marketplace_is_shipping(guard):
    """``cut-release`` version-syncs this file alongside the Claude one."""
    required, _ = guard.entry_required(
        "fix: correct the plugin marketplace entry",
        [],
        "someone",
        [".github/plugin/marketplace.json"],
    )
    assert required is True


def test_issue_template_change_does_not_require_entry(guard):
    required, _ = guard.entry_required(
        "fix: clarify the bug report form",
        [],
        "someone",
        [".github/ISSUE_TEMPLATE/bug_report.yml"],
    )
    assert required is False


# --- section parsing -------------------------------------------------------


def test_unreleased_line_range(guard):
    assert guard.unreleased_line_range(CHANGELOG_WITH_ENTRY) == (4, 8)


def test_unreleased_line_range_without_heading(guard):
    with pytest.raises(ValueError, match="Unreleased"):
        guard.unreleased_line_range("# Changelog\n\n## [0.8.5] - 2026-08-07\n")


def test_unreleased_has_content(guard):
    assert guard.unreleased_has_content(CHANGELOG_WITH_ENTRY) is True
    assert guard.unreleased_has_content(CHANGELOG_EMPTY_UNRELEASED) is False


def test_unreleased_with_only_a_subheading_is_empty(guard):
    text = "# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n## [0.8.5] - 2026-08-07\n"
    assert guard.unreleased_has_content(text) is False


def test_unreleased_at_end_of_file(guard):
    text = "# Changelog\n\n## [Unreleased]\n\n### Added\n- **A thing.** It works.\n"
    assert guard.unreleased_has_content(text) is True


# --- diff placement --------------------------------------------------------


def test_entry_under_unreleased_passes(guard):
    diff = (
        "diff --git a/CHANGELOG.md b/CHANGELOG.md\n"
        "--- a/CHANGELOG.md\n"
        "+++ b/CHANGELOG.md\n"
        "@@ -3,0 +4,3 @@\n"
        "+\n"
        "+### Fixed\n"
        "+- **Something broke.** And now it does not.\n"
    )
    assert guard.entry_added_under_unreleased(CHANGELOG_WITH_ENTRY, diff) is True


def test_entry_under_released_heading_fails(guard):
    changelog = """\
# Changelog

## [Unreleased]

## [0.8.5] - 2026-08-07

### Fixed
- **An older fix.** Already published.
- **Sneaked in here instead.** Never promoted.
"""
    diff = (
        "diff --git a/CHANGELOG.md b/CHANGELOG.md\n"
        "--- a/CHANGELOG.md\n"
        "+++ b/CHANGELOG.md\n"
        "@@ -8,0 +9 @@\n"
        "+- **Sneaked in here instead.** Never promoted.\n"
    )
    assert guard.entry_added_under_unreleased(changelog, diff) is False


def test_no_changelog_diff_fails(guard):
    assert guard.entry_added_under_unreleased(CHANGELOG_WITH_ENTRY, "") is False


def test_subheading_only_addition_fails(guard):
    changelog = (
        "# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n## [0.8.5] - 2026-08-07\n"
    )
    diff = "--- a/CHANGELOG.md\n+++ b/CHANGELOG.md\n@@ -3,0 +4,2 @@\n+\n+### Fixed\n"
    assert guard.entry_added_under_unreleased(changelog, diff) is False


def test_added_line_numbers_tracks_context(guard):
    diff = (
        "--- a/CHANGELOG.md\n+++ b/CHANGELOG.md\n@@ -1,2 +1,3 @@\n one\n+two\n three\n"
    )
    assert guard.added_line_numbers(diff) == {2}


def test_added_line_numbers_ignores_no_newline_marker(guard):
    """``\\ No newline at end of file`` is an annotation, not a line."""
    diff = (
        "--- a/CHANGELOG.md\n"
        "+++ b/CHANGELOG.md\n"
        "@@ -1,3 +1,5 @@\n"
        " one\n"
        " two\n"
        "-three\n"
        "\\ No newline at end of file\n"
        "+three\n"
        "+four\n"
        "\\ No newline at end of file\n"
    )
    assert guard.added_line_numbers(diff) == {3, 4}


def test_added_line_numbers_across_multiple_hunks(guard):
    diff = (
        "--- a/CHANGELOG.md\n"
        "+++ b/CHANGELOG.md\n"
        "@@ -3,0 +4,2 @@\n"
        "+### Fixed\n"
        "+- **First.** In the Unreleased block.\n"
        "@@ -20,0 +23 @@\n"
        "+- **Second.** Much further down.\n"
    )
    assert guard.added_line_numbers(diff) == {4, 5, 23}


def test_real_changelog_unreleased_section_is_parseable(guard):
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start, end = guard.unreleased_line_range(text)
    assert start < end
    # Deliberately not asserting that the section has content. Immediately
    # after cut-release.yml runs, `## [Unreleased]` is legitimately empty
    # because everything moved under the new `## [X.Y.Z]` heading. Asserting
    # content here made every release pull request fail its build.
    assert isinstance(guard.unreleased_has_content(text), bool)


def test_unreleased_section_emptied_by_a_release_cut_is_still_parseable(guard):
    """A freshly cut release leaves `[Unreleased]` empty. That must parse."""
    text = (
        "# Changelog\n"
        "\n"
        "## [Unreleased]\n"
        "\n"
        "## [1.2.3] - 2026-01-01\n"
        "\n"
        "### Added\n"
        "- Something that shipped.\n"
    )
    start, end = guard.unreleased_line_range(text)
    assert start < end
    assert guard.unreleased_has_content(text) is False


def test_real_changelog_survives_a_release_cut(guard):
    """Run the real cut against the real CHANGELOG and re-parse the result.

    Release 0.8.6 failed CI because no test ever constructed the repository's
    post-cut state. `apply_release_cut` is the same function `cut-release.yml`
    invokes, so this exercises the shipped code path rather than a copy.
    """
    before = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    # Deliberately no precondition on `[Unreleased]` having content: on a
    # release branch it is empty, and that is the state this test exists for.

    after = guard.apply_release_cut(before, "99.0.0", "2099-12-31")

    start, end = guard.unreleased_line_range(after)
    assert start < end
    # Everything moved under the new heading, so the section is now empty. The
    # guard must report that as a fact instead of raising.
    assert guard.unreleased_has_content(after) is False
    assert "## [99.0.0] - 2099-12-31" in after
    # No content is lost, only re-attributed.
    assert after.replace("\n\n## [99.0.0] - 2099-12-31", "", 1) == before


def test_release_cut_is_idempotent_on_content(guard):
    """Cutting twice must not duplicate the bullets under the first heading."""
    text = "# Changelog\n\n## [Unreleased]\n\n- One entry.\n"

    once = guard.apply_release_cut(text, "1.0.0", "2026-01-01")
    twice = guard.apply_release_cut(once, "1.0.1", "2026-01-02")

    assert twice.count("- One entry.") == 1
    assert twice.index("## [1.0.1]") < twice.index("## [1.0.0]")


def test_release_cut_rejects_a_changelog_without_the_marker(guard):
    with pytest.raises(ValueError, match=r"\[Unreleased\]"):
        guard.apply_release_cut("# Changelog\n\n## [1.0.0] - 2026-01-01\n", "2.0.0", "x")


@pytest.mark.parametrize(
    ("title", "labels", "changed_files"),
    [
        pytest.param(
            "chore(deps): bump cryptography from 48.0.1 to 50.0.0",
            ["dependencies", "python:uv"],
            ["uv.lock"],
            id="pr-362-lockfile",
        ),
        pytest.param(
            "chore(deps): bump actions/setup-python from 6 to 7",
            ["dependencies"],
            [
                ".github/workflows/agentops-watchdog.yml",
                ".github/workflows/ci.yml",
                ".github/workflows/e2e.yml",
                ".github/workflows/release.yml",
                ".github/workflows/staging.yml",
            ],
            id="pr-358-actions",
        ),
        pytest.param(
            "chore(deps-dev): update mcp requirement from <2,>=1.0 to >=1.0,<3",
            ["dependencies"],
            ["pyproject.toml"],
            id="pr-359-shipping-manifest",
        ),
    ],
)
def test_real_dependabot_pull_requests_never_require_an_entry(
    guard, title, labels, changed_files
):
    """Payloads copied verbatim from merged Dependabot PRs.

    `pyproject.toml` is shipping code, so that case only passes because the
    author exemption is checked before the file classification. A regression
    that reorders those checks would block every dependency bump.
    """
    required, reason = guard.entry_required(
        title, labels, "dependabot[bot]", changed_files
    )

    assert required is False
    assert "automated author" in reason
