#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────
# AgentOps — Post-Publish Tombstone Verification
# Companion to the pre-publish verify-tombstone-testpypi CI job
# (see .github/workflows/release.yml:223 and staging.yml:222).
#
# What it does:
#   1. PyPI:  pip install agentops-toolkit==<version> in an isolated venv,
#             asserts the redirect to agentops-accelerator, and proves the
#             tombstone ships zero Python modules (shadow-free).
#   2. VSIX:  vsce show AgentOpsToolkit.agentops-skills --json, asserts the
#             publisher, version, latest-entry position, and deprecation hint.
#   3. GH:    gh release view v<version> --json …, asserts tag, draft/prerelease
#             flags, release body, and presence of the 6 expected artifacts.
#
# Usage:  python3 scripts/verify_tombstones.py --version 0.3.0
# Prereqs:
#   - Python 3.11+ (uses stdlib only — no third-party imports)
#   - vsce on PATH (skip with --skip-vsix): npm install -g @vscode/vsce
#   - gh  on PATH (skip with --skip-gh-release), authenticated: gh auth login
#   - Network access to pypi.org / test.pypi.org / marketplace.visualstudio.com
#
# Exit codes:
#   0 = all (non-skipped) checks PASS
#   1 = one or more checks FAILED
#   2 = a required prerequisite tool is missing for a check that wasn't skipped
# ─────────────────────────────────────────────────────────────────────

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# Sentinel exit code for missing prerequisite tools (vsce, gh).
EXIT_PREREQ_MISSING = 2

# The 6 release assets attached by .github/workflows/release.yml.
# Each entry is a substring match against the asset filename.
GH_RELEASE_ASSET_PATTERNS: tuple[str, ...] = (
    "agentops_accelerator-{version}.tar.gz",
    "agentops_accelerator-{version}-py3-none-any.whl",
    "agentops_toolkit-{version}.tar.gz",
    "agentops_toolkit-{version}-py3-none-any.whl",
    "agentops-skills.vsix",
    "agentops-toolkit-tombstone.vsix",
)

# Tombstone deprecation-hint substrings — at least one must appear in displayName.
VSIX_DEPRECATION_HINTS: tuple[str, ...] = ("deprecated", "renamed")

# Tombstone identifiers.
PYPI_TOMBSTONE_NAME = "agentops-toolkit"
PYPI_REAL_NAME = "agentops-accelerator"
VSIX_TOMBSTONE_ID = "AgentOpsToolkit.agentops-skills"
VSIX_EXPECTED_PUBLISHER = "AgentOpsToolkit"


@dataclass
class CheckResult:
    """Outcome of a single sub-check within a category (PyPI/VSIX/GH)."""

    name: str
    passed: bool
    detail: str = ""


@dataclass
class CategoryResult:
    """Aggregate of sub-checks for one category. ``skipped`` short-circuits totals."""

    label: str
    checks: list[CheckResult] = field(default_factory=list)
    skipped: bool = False
    prereq_missing: bool = False

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks) and not self.prereq_missing

    @property
    def total(self) -> int:
        return len(self.checks)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)


# ─── output helpers ─────────────────────────────────────────────────


def _emit(symbol: str, name: str, detail: str = "") -> None:
    """Print a per-check line: '  ✓ check name  (optional detail)'."""

    suffix = f"  ({detail})" if detail else ""
    print(f"  {symbol} {name}{suffix}", flush=True)


def emit_pass(name: str, detail: str = "") -> None:
    _emit("✓", name, detail)


def emit_fail(name: str, detail: str = "") -> None:
    _emit("✗", name, detail)


def emit_section(title: str) -> None:
    print(f"\n── {title} ──", flush=True)


def run(
    args: list[str],
    *,
    verbose: bool,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess, never raising on non-zero. ``--verbose`` echoes I/O."""

    if verbose:
        print(f"  $ {' '.join(args)}", flush=True)
    completed = subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=env,
    )
    if verbose:
        if completed.stdout:
            print(completed.stdout, end="", flush=True)
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr, flush=True)
    return completed


# ─── Check 1: PyPI tombstone ────────────────────────────────────────


def _venv_python(venv_dir: Path) -> Path:
    """Resolve the python executable inside a created venv (Windows-safe)."""

    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _parse_pip_show(stdout: str) -> dict[str, str]:
    """Parse ``pip show`` RFC-822-ish output into a flat dict (first occurrence wins)."""

    fields: dict[str, str] = {}
    for line in stdout.splitlines():
        if ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            key = key.strip()
            if key and key not in fields:
                fields[key] = value.strip()
    return fields


def _version_tuple(version: str) -> tuple[int, ...]:
    """Conservative PEP-440-ish parse: take leading dotted digits, zero-pad.

    GA versions only. Pre-release suffixes (``0.3.0a1``, ``0.3.0rc2``) and
    post-releases (``0.3.0.post1``) are NOT handled correctly — digit
    concatenation inflates the segment. Replace with ``packaging.version``
    before extending this script to handle non-GA versions.
    """

    head = version.split("+", 1)[0].split("-", 1)[0]
    parts: list[int] = []
    for piece in head.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if parts else (0,)


def check_pypi(version: str, repository: str, verbose: bool) -> CategoryResult:
    """Install the PyPI tombstone in an isolated venv and verify the redirect."""

    result = CategoryResult(label="PyPI")
    emit_section(f"PyPI tombstone — {PYPI_TOMBSTONE_NAME}=={version} ({repository})")

    with tempfile.TemporaryDirectory(prefix="agentops-verify-") as tmp:
        venv_dir = Path(tmp) / "venv"

        # Step 1: create venv with the host interpreter.
        venv_proc = run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            verbose=verbose,
        )
        if venv_proc.returncode != 0:
            result.checks.append(
                CheckResult(
                    "create isolated venv",
                    False,
                    f"python -m venv exit={venv_proc.returncode}: {venv_proc.stderr.strip()[:200]}",
                )
            )
            emit_fail("create isolated venv", result.checks[-1].detail)
            return result
        emit_pass("create isolated venv")
        result.checks.append(CheckResult("create isolated venv", True))

        python = _venv_python(venv_dir)
        if not python.exists():
            result.checks.append(
                CheckResult("locate venv python", False, f"missing {python}")
            )
            emit_fail("locate venv python", str(python))
            return result

        # Step 2: upgrade pip inside the venv so old bundled pips don't fail TLS.
        upgrade = run(
            [str(python), "-m", "pip", "install", "--upgrade", "pip"],
            verbose=verbose,
        )
        passed = upgrade.returncode == 0
        detail = "" if passed else f"exit={upgrade.returncode}"
        (emit_pass if passed else emit_fail)("upgrade pip in venv", detail)
        result.checks.append(CheckResult("upgrade pip in venv", passed, detail))

        # Step 3: pip install agentops-toolkit==<version> from the chosen index.
        # Unlike the pre-publish CI job at .github/workflows/release.yml:223 (which
        # retries 5x30s for TestPyPI propagation), this runs AFTER CI is green so
        # production PyPI propagation is already settled. No retry needed.
        install_cmd = [
            str(python),
            "-m",
            "pip",
            "install",
            f"{PYPI_TOMBSTONE_NAME}=={version}",
        ]
        if repository == "testpypi":
            install_cmd += [
                "--index-url",
                "https://test.pypi.org/simple/",
                "--extra-index-url",
                "https://pypi.org/simple/",
            ]
        install = run(install_cmd, verbose=verbose)
        install_ok = install.returncode == 0
        install_detail = "" if install_ok else f"exit={install.returncode}: {install.stderr.strip()[:200]}"
        (emit_pass if install_ok else emit_fail)(
            f"pip install {PYPI_TOMBSTONE_NAME}=={version}", install_detail
        )
        result.checks.append(
            CheckResult(f"pip install {PYPI_TOMBSTONE_NAME}=={version}", install_ok, install_detail)
        )
        if not install_ok:
            # Subsequent checks all depend on the install; stop here.
            return result

        # Step 4: pip show agentops-toolkit must declare agentops-accelerator under Requires.
        show_toolkit = run(
            [str(python), "-m", "pip", "show", PYPI_TOMBSTONE_NAME],
            verbose=verbose,
        )
        toolkit_fields = _parse_pip_show(show_toolkit.stdout)
        requires = toolkit_fields.get("Requires", "")
        requires_ok = (
            show_toolkit.returncode == 0 and PYPI_REAL_NAME in requires
        )
        (emit_pass if requires_ok else emit_fail)(
            f"{PYPI_TOMBSTONE_NAME} Requires: {PYPI_REAL_NAME}",
            f"Requires={requires!r}" if not requires_ok else "",
        )
        result.checks.append(
            CheckResult(
                f"{PYPI_TOMBSTONE_NAME} Requires: {PYPI_REAL_NAME}",
                requires_ok,
                requires,
            )
        )

        # Step 5: installed agentops-accelerator version must be >= <version>.
        show_real = run(
            [str(python), "-m", "pip", "show", PYPI_REAL_NAME],
            verbose=verbose,
        )
        real_fields = _parse_pip_show(show_real.stdout)
        real_version = real_fields.get("Version", "")
        try:
            version_ok = bool(real_version) and _version_tuple(real_version) >= _version_tuple(version)
        except (ValueError, TypeError):
            version_ok = False
        (emit_pass if version_ok else emit_fail)(
            f"{PYPI_REAL_NAME} >= {version}",
            f"installed={real_version!r}",
        )
        result.checks.append(
            CheckResult(f"{PYPI_REAL_NAME} >= {version}", version_ok, real_version)
        )

        # Step 6: import agentops resolves outside the tombstone's site-packages footprint.
        import_check = run(
            [str(python), "-c", "import agentops; print(agentops.__file__)"],
            verbose=verbose,
        )
        resolved = import_check.stdout.strip().splitlines()[-1] if import_check.stdout.strip() else ""
        import_ok = (
            import_check.returncode == 0
            and resolved
            and PYPI_TOMBSTONE_NAME not in resolved
            and "agentops" in resolved
        )
        (emit_pass if import_ok else emit_fail)(
            "import agentops resolves to real package",
            f"resolved={resolved!r}",
        )
        result.checks.append(
            CheckResult("import agentops resolves to real package", bool(import_ok), resolved)
        )

        # Step 7: agentops --version returns 0.
        cli_check = run(
            [str(python), "-m", "agentops", "--version"],
            verbose=verbose,
        )
        cli_ok = cli_check.returncode == 0
        (emit_pass if cli_ok else emit_fail)(
            "agentops --version exits 0",
            f"exit={cli_check.returncode}" if not cli_ok else cli_check.stdout.strip(),
        )
        result.checks.append(
            CheckResult("agentops --version exits 0", cli_ok, cli_check.stdout.strip())
        )

        # Step 8: tombstone owns no .py files (shadow-free).
        # Use importlib.metadata.files() from INSIDE the venv via a one-liner.
        files_probe = run(
            [
                str(python),
                "-c",
                (
                    "import json, sys\n"
                    "from importlib.metadata import files\n"
                    f"fs = files({PYPI_TOMBSTONE_NAME!r}) or []\n"
                    "print(json.dumps([str(f) for f in fs]))\n"
                ),
            ],
            verbose=verbose,
        )
        owned: list[str] = []
        try:
            owned = json.loads(files_probe.stdout.strip() or "[]")
        except json.JSONDecodeError:
            owned = []
        owned_py = [f for f in owned if f.endswith(".py")]
        shadow_free = files_probe.returncode == 0 and not owned_py
        (emit_pass if shadow_free else emit_fail)(
            f"{PYPI_TOMBSTONE_NAME} ships zero .py modules",
            f"owns_py={owned_py[:5]}" if owned_py else "",
        )
        result.checks.append(
            CheckResult(
                f"{PYPI_TOMBSTONE_NAME} ships zero .py modules",
                shadow_free,
                ",".join(owned_py[:5]),
            )
        )

    return result


# ─── Check 2: VSIX tombstone ────────────────────────────────────────


def check_vsix(version: str, verbose: bool) -> CategoryResult:
    """Query the VS Code Marketplace via ``vsce show --json`` and assert the contract."""

    result = CategoryResult(label="VSIX")
    emit_section(f"VSIX tombstone — {VSIX_TOMBSTONE_ID}")

    # Resolve via shutil.which so Windows finds ``vsce.cmd`` (npm batch shim) —
    # subprocess.run() with shell=False only auto-appends .exe, not .cmd/.bat.
    vsce_path = shutil.which("vsce")
    if vsce_path is None:
        result.prereq_missing = True
        emit_fail(
            "vsce on PATH",
            "Install: npm install -g @vscode/vsce  (or use --skip-vsix)",
        )
        return result

    show = run([vsce_path, "show", VSIX_TOMBSTONE_ID, "--json"], verbose=verbose)
    if show.returncode != 0:
        emit_fail("vsce show", f"exit={show.returncode}: {show.stderr.strip()[:200]}")
        result.checks.append(
            CheckResult("vsce show", False, show.stderr.strip()[:200])
        )
        return result

    try:
        payload = json.loads(show.stdout)
    except json.JSONDecodeError as exc:
        emit_fail("parse vsce JSON", str(exc))
        result.checks.append(CheckResult("parse vsce JSON", False, str(exc)))
        return result
    emit_pass("vsce show + parse JSON")
    result.checks.append(CheckResult("vsce show + parse JSON", True))

    # Assert publisher.
    publisher_name = (payload.get("publisher") or {}).get("publisherName", "")
    publisher_ok = publisher_name == VSIX_EXPECTED_PUBLISHER
    (emit_pass if publisher_ok else emit_fail)(
        f"publisher == {VSIX_EXPECTED_PUBLISHER}", f"got={publisher_name!r}"
    )
    result.checks.append(
        CheckResult(
            f"publisher == {VSIX_EXPECTED_PUBLISHER}", publisher_ok, publisher_name
        )
    )

    # Assert version appears in versions list.
    versions = payload.get("versions") or []
    version_strings = [str(v.get("version", "")) for v in versions]
    version_present = version in version_strings
    (emit_pass if version_present else emit_fail)(
        f"version {version} present", f"versions[:5]={version_strings[:5]}"
    )
    result.checks.append(
        CheckResult(
            f"version {version} present", version_present, ",".join(version_strings[:5])
        )
    )

    # Assert it is the latest entry (vsce returns newest first).
    latest_is_target = bool(version_strings) and version_strings[0] == version
    (emit_pass if latest_is_target else emit_fail)(
        f"version {version} is latest entry",
        f"latest={version_strings[0] if version_strings else '<empty>'!r}",
    )
    result.checks.append(
        CheckResult(
            f"version {version} is latest entry",
            latest_is_target,
            version_strings[0] if version_strings else "",
        )
    )

    # Assert displayName carries a deprecation hint.
    display_name = str(payload.get("displayName", ""))
    lowered = display_name.lower()
    hint_ok = any(hint in lowered for hint in VSIX_DEPRECATION_HINTS)
    (emit_pass if hint_ok else emit_fail)(
        "displayName contains deprecation hint", f"displayName={display_name!r}"
    )
    result.checks.append(
        CheckResult(
            "displayName contains deprecation hint", hint_ok, display_name
        )
    )

    return result


# ─── Check 3: GitHub Release ────────────────────────────────────────


def check_gh_release(version: str, repo: str, verbose: bool) -> CategoryResult:
    """Query the GH release with ``gh release view --json`` and assert assets/flags."""

    result = CategoryResult(label="GH")
    tag = f"v{version}"
    emit_section(f"GitHub Release — {tag} ({repo})")

    # Resolve via shutil.which for the same Windows .cmd/.bat reason as vsce.
    gh_path = shutil.which("gh")
    if gh_path is None:
        result.prereq_missing = True
        emit_fail(
            "gh on PATH",
            "Install: https://cli.github.com/  (or use --skip-gh-release)",
        )
        return result

    show = run(
        [
            gh_path,
            "release",
            "view",
            tag,
            "--repo",
            repo,
            "--json",
            "assets,body,tagName,isDraft,isPrerelease",
        ],
        verbose=verbose,
    )
    if show.returncode != 0:
        emit_fail("gh release view", f"exit={show.returncode}: {show.stderr.strip()[:200]}")
        result.checks.append(
            CheckResult("gh release view", False, show.stderr.strip()[:200])
        )
        return result

    try:
        payload = json.loads(show.stdout)
    except json.JSONDecodeError as exc:
        emit_fail("parse gh JSON", str(exc))
        result.checks.append(CheckResult("parse gh JSON", False, str(exc)))
        return result
    emit_pass("gh release view + parse JSON")
    result.checks.append(CheckResult("gh release view + parse JSON", True))

    # Assert tag.
    actual_tag = payload.get("tagName", "")
    tag_ok = actual_tag == tag
    (emit_pass if tag_ok else emit_fail)(
        f"tagName == {tag}", f"got={actual_tag!r}"
    )
    result.checks.append(CheckResult(f"tagName == {tag}", tag_ok, actual_tag))

    # Assert not a draft.
    is_draft = bool(payload.get("isDraft", True))
    draft_ok = not is_draft
    (emit_pass if draft_ok else emit_fail)(
        "isDraft == false", f"got={is_draft}"
    )
    result.checks.append(CheckResult("isDraft == false", draft_ok, str(is_draft)))

    # Assert not a prerelease.
    is_prerelease = bool(payload.get("isPrerelease", True))
    prerelease_ok = not is_prerelease
    (emit_pass if prerelease_ok else emit_fail)(
        "isPrerelease == false", f"got={is_prerelease}"
    )
    result.checks.append(
        CheckResult("isPrerelease == false", prerelease_ok, str(is_prerelease))
    )

    # Assert non-empty body.
    body = str(payload.get("body", ""))
    body_ok = bool(body.strip())
    (emit_pass if body_ok else emit_fail)(
        "release body is non-empty", f"len={len(body)}"
    )
    result.checks.append(CheckResult("release body is non-empty", body_ok, f"len={len(body)}"))

    # Assert each expected asset exists (substring match).
    asset_names = [str(a.get("name", "")) for a in (payload.get("assets") or [])]
    for pattern in GH_RELEASE_ASSET_PATTERNS:
        expected = pattern.format(version=version)
        present = any(expected in name for name in asset_names)
        (emit_pass if present else emit_fail)(
            f"asset present: {expected}",
            "" if present else f"assets={asset_names}",
        )
        result.checks.append(
            CheckResult(f"asset present: {expected}", present, expected)
        )

    return result


# ─── summary + entry point ──────────────────────────────────────────


def print_summary(version: str, categories: list[CategoryResult]) -> None:
    """Render the boxed SUMMARY block + the overall verdict line."""

    bar = "─" * 46
    print()
    print(bar)
    print(f" Tombstone verification — v{version}")
    print(bar)
    for cat in categories:
        if cat.skipped:
            print(f" {cat.label:5s} SKIPPED")
            continue
        if cat.prereq_missing:
            print(f" {cat.label:5s} PREREQ MISSING")
            continue
        verdict = "PASS" if cat.passed else "FAIL"
        print(f" {cat.label:5s} ({cat.passed_count}/{cat.total} checks): {verdict}")
    print(bar)
    overall = "PASS" if all(c.skipped or c.passed for c in categories) else "FAIL"
    print(f"Overall: {overall}")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify deprecation tombstones (PyPI + VSCode) for an AgentOps release.",
    )
    parser.add_argument(
        "--version",
        required=True,
        help="Version to verify (e.g. 0.3.0)",
    )
    parser.add_argument(
        "--repository",
        choices=("pypi", "testpypi"),
        default="pypi",
        help="PyPI index to query (default: pypi)",
    )
    parser.add_argument(
        "--skip-pypi", action="store_true", help="Skip PyPI tombstone checks"
    )
    parser.add_argument(
        "--skip-vsix",
        action="store_true",
        help="Skip VSIX (Marketplace) tombstone checks",
    )
    parser.add_argument(
        "--skip-gh-release",
        action="store_true",
        help="Skip GitHub Release asset audit",
    )
    parser.add_argument(
        "--gh-repo",
        default="Azure/agentops",
        metavar="OWNER/REPO",
        help="GitHub repo for release audit (default: Azure/agentops)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Echo full subprocess stdout/stderr for debugging",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    categories: list[CategoryResult] = []

    if args.skip_pypi:
        pypi = CategoryResult(label="PyPI", skipped=True)
        emit_section(f"PyPI tombstone — SKIPPED (--skip-pypi)")
    else:
        pypi = check_pypi(args.version, args.repository, args.verbose)
    categories.append(pypi)

    if args.skip_vsix:
        vsix = CategoryResult(label="VSIX", skipped=True)
        emit_section("VSIX tombstone — SKIPPED (--skip-vsix)")
    else:
        vsix = check_vsix(args.version, args.verbose)
    categories.append(vsix)

    if args.skip_gh_release:
        gh = CategoryResult(label="GH", skipped=True)
        emit_section("GitHub Release — SKIPPED (--skip-gh-release)")
    else:
        gh = check_gh_release(args.version, args.gh_repo, args.verbose)
    categories.append(gh)

    print_summary(args.version, categories)

    # Exit-code precedence: 2 if any non-skipped check had a missing prereq,
    # else 1 if any non-skipped check failed, else 0.
    if any(c.prereq_missing for c in categories):
        return EXIT_PREREQ_MISSING
    if any((not c.skipped and not c.passed) for c in categories):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
