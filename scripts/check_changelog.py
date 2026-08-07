#!/usr/bin/env python3
"""CHANGELOG guards for CI.

Two subcommands, two failure modes, one root cause.

``check-pr`` runs on every pull request to ``develop``. It fails when a PR
changes user-visible behaviour but adds nothing under ``## [Unreleased]`` in
``CHANGELOG.md``.

``check-unreleased`` runs inside ``cut-release.yml`` before the release branch
is created. It fails when ``## [Unreleased]`` is empty.

Why both: ``cut-release.yml`` does not generate changelog content. It renames
the ``## [Unreleased]`` heading to ``## [X.Y.Z] - <date>``. If nothing was
written under ``[Unreleased]`` during the cycle, the published release section
is empty. Releases 0.8.4 and 0.8.5 both shipped that way and were backfilled by
hand afterwards. ``check-pr`` stops the omission at the source;
``check-unreleased`` is the last line of defence at the point of no return.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Conventional-commit types that describe user-visible behaviour.
TYPES_REQUIRING_ENTRY = frozenset({"feat", "fix", "perf", "revert"})

#: Label that skips ``check-pr`` entirely.
SKIP_LABEL = "no-changelog"

#: Bot authors that cannot write a changelog entry themselves. Their changes
#: are covered by ``check-unreleased`` when the release is cut instead.
EXEMPT_AUTHORS = frozenset({"dependabot[bot]", "github-actions[bot]"})

#: Path prefixes whose changes never reach a user of the published package.
NON_SHIPPING_PREFIXES = (
    "docs/",
    "tests/",
    ".github/",
    ".vscode/",
    "media/",
    "tombstones/",
)

#: Top-level files that never reach a user of the published package.
NON_SHIPPING_FILES = frozenset(
    {
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "README.md",
        "SECURITY.md",
        "AGENTS.md",
        ".gitattributes",
        ".gitignore",
        ".pre-commit-config.yaml",
        "launch.json",
    }
)

_TITLE_RE = re.compile(
    r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]*)\))?(?P<breaking>!)?:",
)
_HEADING_RE = re.compile(r"^##\s+\[")
_UNRELEASED_RE = re.compile(r"^##\s+\[Unreleased\]", re.IGNORECASE)
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@")


# --------------------------------------------------------------------------
# Pure decision logic (unit-tested)
# --------------------------------------------------------------------------


def parse_commit_type(title: str) -> tuple[str | None, bool]:
    """Return ``(type, is_breaking)`` for a conventional-commit PR title.

    ``type`` is ``None`` when the title does not follow the convention.
    """
    match = _TITLE_RE.match(title.strip())
    if match is None:
        return None, "BREAKING CHANGE" in title
    breaking = bool(match.group("breaking")) or "BREAKING CHANGE" in title
    return match.group("type").lower(), breaking


def touches_shipped_code(changed_files: list[str]) -> bool:
    """True when the diff touches anything that ends up in a release."""
    for raw in changed_files:
        path = raw.strip().replace("\\", "/")
        if not path:
            continue
        if path in NON_SHIPPING_FILES:
            continue
        if path.startswith(NON_SHIPPING_PREFIXES):
            continue
        return True
    return False


def entry_required(
    title: str,
    labels: list[str],
    author: str,
    changed_files: list[str],
) -> tuple[bool, str]:
    """Decide whether this PR must add an ``[Unreleased]`` entry.

    Returns ``(required, reason)``. ``reason`` is always populated so the CI
    log explains the decision either way.
    """
    normalised_labels = {label.strip().lower() for label in labels}
    if SKIP_LABEL in normalised_labels:
        return False, f"the `{SKIP_LABEL}` label is applied"

    if author.strip().lower() in EXEMPT_AUTHORS:
        return False, (
            f"`{author}` is an automated author and cannot write an entry; "
            "its changes are covered by the cut-release guard"
        )

    if not touches_shipped_code(changed_files):
        return False, "no shipped file changed (docs, tests, and CI only)"

    commit_type, breaking = parse_commit_type(title)
    if breaking:
        return True, "the PR is marked as a breaking change"
    if commit_type is None:
        return True, (
            "the PR title has no conventional-commit type and the diff "
            "touches shipped code"
        )
    if commit_type in TYPES_REQUIRING_ENTRY:
        return True, f"the PR title is typed `{commit_type}:`"
    return False, f"`{commit_type}:` does not describe user-visible behaviour"


def unreleased_line_range(changelog: str) -> tuple[int, int]:
    """Return the 1-based ``[start, end)`` line range of ``## [Unreleased]``.

    ``start`` is the line after the heading. ``end`` is the line of the next
    ``## [`` heading, or one past the last line when there is none.
    Raises ``ValueError`` when the heading is missing.
    """
    lines = changelog.splitlines()
    start = None
    for index, line in enumerate(lines):
        if _UNRELEASED_RE.match(line):
            start = index + 2  # 1-based, first line after the heading
            break
    if start is None:
        raise ValueError("CHANGELOG.md has no `## [Unreleased]` heading")

    for index in range(start - 1, len(lines)):
        if _HEADING_RE.match(lines[index]):
            return start, index + 1
    return start, len(lines) + 1


def unreleased_has_content(changelog: str) -> bool:
    """True when ``[Unreleased]`` holds at least one bullet."""
    lines = changelog.splitlines()
    start, end = unreleased_line_range(changelog)
    for number in range(start, end):
        line = lines[number - 1].strip()
        if line.startswith(("- ", "* ")):
            return True
    return False


def added_line_numbers(diff: str) -> set[int]:
    """Return new-file line numbers of added lines in a unified diff."""
    added: set[int] = set()
    cursor = 0
    for line in diff.splitlines():
        hunk = _HUNK_RE.match(line)
        if hunk is not None:
            cursor = int(hunk.group("start"))
            continue
        if cursor == 0:
            continue
        if line.startswith("+++"):
            continue
        if line.startswith("+"):
            added.add(cursor)
            cursor += 1
        elif line.startswith("-"):
            continue
        else:
            cursor += 1
    return added


def entry_added_under_unreleased(changelog: str, changelog_diff: str) -> bool:
    """True when the PR added a bullet inside the ``[Unreleased]`` section.

    ``changelog`` is the head version of the file. An entry added under an
    already-released heading does not count: ``cut-release.yml`` only ever
    promotes ``[Unreleased]``, so such an entry is never published.
    """
    if not changelog_diff.strip():
        return False
    start, end = unreleased_line_range(changelog)
    lines = changelog.splitlines()
    for number in sorted(added_line_numbers(changelog_diff)):
        if not start <= number < end:
            continue
        if lines[number - 1].strip().startswith(("- ", "* ")):
            return True
    return False


# --------------------------------------------------------------------------
# CI plumbing
# --------------------------------------------------------------------------


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _parse_labels(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []


def _fail(message: str) -> int:
    print(f"::error::{message}", file=sys.stderr)
    return 1


def _cmd_check_pr(args: argparse.Namespace) -> int:
    title = os.environ.get("PR_TITLE", "")
    labels = _parse_labels(os.environ.get("PR_LABELS", ""))
    author = os.environ.get("PR_AUTHOR", "")
    base = args.base

    changed_files = [
        line for line in _git("diff", "--name-only", f"{base}...HEAD").splitlines()
    ]

    print(f"PR title:  {title!r}")
    print(f"Author:    {author}")
    print(f"Labels:    {labels or '(none)'}")
    print(f"Changed:   {len(changed_files)} file(s)")

    required, reason = entry_required(title, labels, author, changed_files)
    if not required:
        print(f"::notice::CHANGELOG entry not required: {reason}.")
        return 0

    print(f"CHANGELOG entry required because {reason}.")

    changelog_path = REPO_ROOT / "CHANGELOG.md"
    if not changelog_path.exists():
        return _fail("CHANGELOG.md is missing from the repository.")

    changelog = changelog_path.read_text(encoding="utf-8")
    diff = _git("diff", "--unified=0", f"{base}...HEAD", "--", "CHANGELOG.md")

    try:
        if entry_added_under_unreleased(changelog, diff):
            print("::notice::Found a new entry under `## [Unreleased]`.")
            return 0
    except ValueError as exc:
        return _fail(str(exc))

    touched = bool(diff.strip())
    detail = (
        "CHANGELOG.md was modified, but no new bullet landed under "
        "`## [Unreleased]`. `cut-release.yml` only promotes the "
        "`[Unreleased]` section, so an entry filed under an already-released "
        "heading is never published."
        if touched
        else "CHANGELOG.md was not modified."
    )
    return _fail(
        f"This PR needs a CHANGELOG entry because {reason}. {detail} "
        "Add a bullet under `## [Unreleased]`, or apply the "
        f"`{SKIP_LABEL}` label if the change really is invisible to users."
    )


def _cmd_check_unreleased(args: argparse.Namespace) -> int:
    path = Path(args.path) if args.path else REPO_ROOT / "CHANGELOG.md"
    if not path.exists():
        return _fail(f"{path} does not exist.")

    changelog = path.read_text(encoding="utf-8")
    try:
        if unreleased_has_content(changelog):
            print("`## [Unreleased]` has content. Safe to cut the release.")
            return 0
    except ValueError as exc:
        return _fail(str(exc))

    return _fail(
        "`## [Unreleased]` is empty, so this release would publish an empty "
        "CHANGELOG section. Releases 0.8.4 and 0.8.5 shipped this way and had "
        "to be backfilled by hand. Write the entries on `develop` first, then "
        "re-run Cut Release."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    pr = sub.add_parser(
        "check-pr",
        help="Fail when a user-visible PR adds no [Unreleased] entry.",
    )
    pr.add_argument(
        "--base",
        default="origin/develop",
        help="Base ref to diff against (default: origin/develop).",
    )
    pr.set_defaults(func=_cmd_check_pr)

    unreleased = sub.add_parser(
        "check-unreleased",
        help="Fail when the [Unreleased] section is empty.",
    )
    unreleased.add_argument("--path", default=None, help="Path to CHANGELOG.md.")
    unreleased.set_defaults(func=_cmd_check_unreleased)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
