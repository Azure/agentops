"""Coding agent skills installation and registration service."""

from __future__ import annotations

import io
import json
import re
import tarfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Dict, List

_TEMPLATE_PACKAGE = "agentops.templates"

_SKILLS: tuple[str, ...] = (
    "skills/agentops-eval/SKILL.md",
    "skills/agentops-config/SKILL.md",
    "skills/agentops-dataset/SKILL.md",
    "skills/agentops-report/SKILL.md",
    "skills/agentops-regression/SKILL.md",
    "skills/agentops-trace/SKILL.md",
    "skills/agentops-monitor/SKILL.md",
    "skills/agentops-workflow/SKILL.md",
)

_PLATFORM_CONFIGS: Dict[str, Dict[str, str]] = {
    "copilot": {
        "target_dir": ".github/skills",
        "file_pattern": "{skill_name}/SKILL.md",
    },
    "claude": {
        "target_dir": ".claude/commands",
        "file_pattern": "{skill_name}.md",
    },
    "cursor": {
        "target_dir": ".github/skills",
        "file_pattern": "{skill_name}/SKILL.md",
    },
}

_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)

# ---------------------------------------------------------------------------
# Registration markers and content blocks
# ---------------------------------------------------------------------------

_COPILOT_MARKER_START = "<!-- agentops-skills-start -->"
_COPILOT_MARKER_END = "<!-- agentops-skills-end -->"

_COPILOT_BLOCK = f"""{_COPILOT_MARKER_START}
## AgentOps Evaluation & Operations

This project uses AgentOps for agent evaluation, monitoring, and benchmarking.
When the user asks about any of the topics below, read the corresponding skill
file **before** responding and follow its workflow step by step.

| Topic | Skill File | Trigger phrases |
|---|---|---|
| Run evaluations, benchmark, compare models | `.github/skills/agentops-eval/SKILL.md` | "run eval", "evaluate", "benchmark", "compare models" |
| Generate run.yaml configuration | `.github/skills/agentops-config/SKILL.md` | "configure", "run.yaml", "set up eval", "which bundle" |
| Generate evaluation datasets | `.github/skills/agentops-dataset/SKILL.md` | "create dataset", "generate test data", "JSONL" |
| Interpret and regenerate reports | `.github/skills/agentops-report/SKILL.md` | "report", "results", "explain scores" |
| Investigate regressions | `.github/skills/agentops-regression/SKILL.md` | "regression", "score dropped", "why worse" |
| Tracing and observability | `.github/skills/agentops-trace/SKILL.md` | "trace", "tracing", "spans", "telemetry" |
| Monitoring and alerts | `.github/skills/agentops-monitor/SKILL.md` | "monitor", "alerts", "dashboard" |
| CI/CD workflow setup | `.github/skills/agentops-workflow/SKILL.md` | "CI", "workflow", "pipeline", "GitHub Actions" |
{_COPILOT_MARKER_END}"""

_CURSOR_MDC = """\
---
description: AgentOps evaluation, monitoring, and benchmarking tools
globs: "**"
alwaysApply: true
---

When the user asks about evaluations, benchmarks, tracing, or monitoring,
read the corresponding skill file and follow its workflow step by step.

| Topic | Skill File |
|---|---|
| Run evaluations, benchmark, compare models | `.github/skills/agentops-eval/SKILL.md` |
| Generate run.yaml configuration | `.github/skills/agentops-config/SKILL.md` |
| Generate evaluation datasets | `.github/skills/agentops-dataset/SKILL.md` |
| Interpret and regenerate reports | `.github/skills/agentops-report/SKILL.md` |
| Investigate regressions | `.github/skills/agentops-regression/SKILL.md` |
| Tracing and observability | `.github/skills/agentops-trace/SKILL.md` |
| Monitoring and alerts | `.github/skills/agentops-monitor/SKILL.md` |
| CI/CD workflow setup | `.github/skills/agentops-workflow/SKILL.md` |
"""


@dataclass
class SkillsInstallResult:
    """Result of installing coding agent skills.

    Attributes:
        platforms: Platform names that were targeted.
        created_files: Paths of newly created files.
        overwritten_files: Paths of files that were overwritten.
        skipped_files: Paths of files that already existed and were skipped.
    """

    platforms: List[str] = field(default_factory=list)
    created_files: List[Path] = field(default_factory=list)
    overwritten_files: List[Path] = field(default_factory=list)
    skipped_files: List[Path] = field(default_factory=list)


def detect_platforms(directory: Path) -> list[str]:
    """Detect coding agent platforms present in the project.

    Returns a list of platform identifiers (e.g. ``["copilot"]``,
    ``["claude"]``, ``["copilot", "claude"]``).  Returns an empty list
    when no platform indicators are found.
    """
    resolved = directory.resolve()
    platforms: list[str] = []

    if (resolved / ".claude").exists() or (resolved / "CLAUDE.md").exists():
        platforms.append("claude")

    if (
        (resolved / ".github" / "copilot-instructions.md").exists()
        or (resolved / ".github" / "copilot_instructions.md").exists()
        or (resolved / ".github" / "skills").exists()
    ):
        platforms.append("copilot")

    if (
        (resolved / ".cursor" / "rules").exists()
        or (resolved / ".cursorrules").exists()
    ):
        platforms.append("cursor")

    return platforms


def _strip_yaml_frontmatter(content: str) -> str:
    """Remove YAML frontmatter delimited by ``---`` from content."""
    return _FRONTMATTER_RE.sub("", content)


def _transform_content(content: str, platform: str) -> str:
    """Apply platform-specific content transformations."""
    if platform == "claude":
        return _strip_yaml_frontmatter(content)
    return content


def install_skills(
    directory: Path,
    platforms: list[str],
    force: bool = False,
) -> SkillsInstallResult:
    """Install packaged coding agent skills for the specified platforms.

    Reads skill templates from the package and writes them to the
    platform-specific directories in the target *directory*.

    Args:
        directory: Root directory of the consumer repository.
        platforms: List of platform identifiers (e.g. ``["copilot"]``).
        force: When True, overwrite existing skill files.

    Returns:
        SkillsInstallResult with paths of created, overwritten, or skipped files.
    """
    result = SkillsInstallResult(platforms=list(platforms))
    templates_root = files(_TEMPLATE_PACKAGE)
    resolved = directory.resolve()

    for platform in platforms:
        config = _PLATFORM_CONFIGS.get(platform)
        if not config:
            continue

        target_dir = resolved / config["target_dir"]

        for skill_path in _SKILLS:
            # "skills/agentops-eval/SKILL.md" → "agentops-eval"
            skill_name = Path(skill_path).parent.name

            dest_relative = config["file_pattern"].format(skill_name=skill_name)
            dest = target_dir / dest_relative
            existed = dest.exists()

            if existed and not force:
                result.skipped_files.append(dest)
                continue

            dest.parent.mkdir(parents=True, exist_ok=True)
            raw = templates_root.joinpath(skill_path).read_text(encoding="utf-8")
            content = _transform_content(raw, platform)
            dest.write_text(content, encoding="utf-8")

            if existed:
                result.overwritten_files.append(dest)
            else:
                result.created_files.append(dest)

    return result


# ---------------------------------------------------------------------------
# GitHub-based skill installation
# ---------------------------------------------------------------------------

# Allowed sub-directories within a skill folder (agentskills.io spec).
_ALLOWED_SKILL_DIRS = {"references", "scripts", "assets"}

# Directories skipped by default for security (opt-in only).
_RESTRICTED_DIRS = {"scripts"}

_GITHUB_REF_RE = re.compile(
    r"^(?:github:)?"
    r"(?P<owner>[A-Za-z0-9._-]+)"
    r"/(?P<repo>[A-Za-z0-9._-]+)"
    r"(?:@(?P<ref>[A-Za-z0-9._/-]+))?$"
)

_PROVENANCE_FILE = ".installed-from.json"


@dataclass
class GitHubSkillRef:
    """Parsed GitHub skill reference."""

    owner: str
    repo: str
    ref: str  # branch, tag, or commit SHA


def _parse_github_ref(source: str) -> GitHubSkillRef:
    """Parse ``github:org/repo@ref`` or ``org/repo`` into components.

    Raises ValueError on invalid input.
    """
    m = _GITHUB_REF_RE.match(source.strip())
    if not m:
        raise ValueError(
            f"Invalid GitHub skill reference: '{source}'. "
            "Expected format: github:org/repo or org/repo[@ref]"
        )
    return GitHubSkillRef(
        owner=m.group("owner"),
        repo=m.group("repo"),
        ref=m.group("ref") or "main",
    )


def _validate_skill_name(name: str) -> str:
    """Validate and sanitize a skill name from SKILL.md frontmatter.

    Raises ValueError if the name contains path traversal or invalid chars.
    """
    if not name or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise ValueError(
            f"Invalid skill name: '{name}'. "
            "Must be lowercase alphanumeric with single hyphens, "
            "e.g. 'pptx-designer'."
        )
    if ".." in name or "/" in name or "\\" in name:
        raise ValueError(f"Skill name contains path traversal: '{name}'")
    return name


def _parse_skill_frontmatter(content: str) -> dict[str, str]:
    """Extract YAML frontmatter fields from a SKILL.md file.

    Returns a dict with at least ``name`` and ``description`` keys.
    Uses simple line parsing to avoid a YAML dependency in this module.
    """
    if not content.startswith("---"):
        raise ValueError("SKILL.md is missing YAML frontmatter (must start with ---).")

    lines = content.split("\n")
    end_idx = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        raise ValueError("SKILL.md has unclosed YAML frontmatter.")

    meta: dict[str, str] = {}
    current_key = ""
    for line in lines[1:end_idx]:
        if line.startswith("  ") and current_key:
            # Continuation of multiline value
            meta[current_key] = meta.get(current_key, "") + " " + line.strip()
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip(">").strip('"').strip("'").strip()
            if key:
                current_key = key
                meta[key] = val

    if "name" not in meta:
        raise ValueError("SKILL.md frontmatter is missing required 'name' field.")
    if "description" not in meta:
        raise ValueError("SKILL.md frontmatter is missing required 'description' field.")

    return meta


def _fetch_github_tarball(ref: GitHubSkillRef) -> bytes:
    """Download a GitHub repo tarball for the given ref.

    Uses ``GITHUB_TOKEN`` or ``GH_TOKEN`` env var if available.
    """
    import os

    url = f"https://api.github.com/repos/{ref.owner}/{ref.repo}/tarball/{ref.ref}"

    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ValueError(
                f"GitHub repository not found: {ref.owner}/{ref.repo}@{ref.ref}"
            ) from e
        if e.code == 403:
            raise ValueError(
                f"GitHub API rate limit or access denied for {ref.owner}/{ref.repo}. "
                "Set GITHUB_TOKEN env var for authenticated access."
            ) from e
        raise ValueError(
            f"GitHub API error ({e.code}): {e.reason}"
        ) from e
    except urllib.error.URLError as e:
        raise ValueError(f"Network error fetching {ref.owner}/{ref.repo}: {e}") from e


def _extract_skill_from_tarball(
    tarball: bytes,
    repo_name: str,
) -> tuple[dict[str, str], dict[str, bytes]]:
    """Extract a single skill from a GitHub repo tarball.

    Returns (frontmatter_metadata, {relative_path: content_bytes}).

    Searches for the skill directory following agentskills.io convention:
      1. ``{repo_name}/SKILL.md`` (skill dir = repo name)
      2. Any ``*/SKILL.md`` at depth 1 from repo root
      3. ``SKILL.md`` at repo root

    Raises ValueError if no SKILL.md is found or multiple candidates exist.
    """
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        members = tar.getnames()

        # GitHub tarballs have a prefix like "owner-repo-sha/"
        prefix = ""
        for name in members:
            if "/" in name:
                prefix = name.split("/")[0] + "/"
                break

        # Find SKILL.md candidates
        candidates: list[str] = []
        for name in members:
            relative = name[len(prefix):] if name.startswith(prefix) else name
            parts = PurePosixPath(relative).parts
            if parts and parts[-1] == "SKILL.md":
                if len(parts) <= 2:
                    candidates.append(relative)

        if not candidates:
            raise ValueError(
                f"No SKILL.md found in {repo_name}. "
                "The repository must contain a skill directory with a SKILL.md file "
                "(agentskills.io standard)."
            )

        # Prefer {repo_name}/SKILL.md, then first candidate
        chosen = None
        for c in candidates:
            if c.startswith(repo_name + "/"):
                chosen = c
                break
        if chosen is None:
            if len(candidates) > 1:
                dirs = [str(PurePosixPath(c).parent) for c in candidates]
                raise ValueError(
                    f"Multiple skills found in {repo_name}: {', '.join(dirs)}. "
                    "Use github:org/repo with a repo that contains a single skill."
                )
            chosen = candidates[0]

        skill_dir = str(PurePosixPath(chosen).parent)
        if skill_dir == ".":
            skill_dir = ""

        # Read SKILL.md and parse frontmatter
        skill_md_path = prefix + chosen
        member = tar.getmember(skill_md_path)
        f = tar.extractfile(member)
        if f is None:
            raise ValueError(f"Cannot read {skill_md_path}")
        skill_md_content = f.read()
        metadata = _parse_skill_frontmatter(skill_md_content.decode("utf-8"))

        # Collect all files in the skill directory
        skill_prefix = prefix + (skill_dir + "/" if skill_dir else "")
        collected: dict[str, bytes] = {}

        for member in tar.getmembers():
            if not member.isfile():
                continue
            if not member.name.startswith(skill_prefix):
                continue

            relative = member.name[len(skill_prefix):]
            parts = PurePosixPath(relative).parts

            if not parts:
                continue

            # Security: block path traversal
            if any(p in ("..", "") for p in parts):
                continue
            if any(p.startswith(".") for p in parts):
                continue

            # Allow SKILL.md at root, and files in allowed subdirs
            if len(parts) == 1 and parts[0] == "SKILL.md":
                collected[relative] = skill_md_content
                continue

            top_dir = parts[0] if len(parts) > 1 else None
            if top_dir and top_dir in _ALLOWED_SKILL_DIRS:
                if top_dir in _RESTRICTED_DIRS:
                    continue  # Skip scripts/ by default
                f = tar.extractfile(member)
                if f:
                    collected[relative] = f.read()

    return metadata, collected


def install_github_skill(
    source: str,
    directory: Path,
    platforms: list[str],
    force: bool = False,
) -> SkillsInstallResult:
    """Install a skill from a GitHub repository.

    Downloads the repo archive, extracts the skill, validates it,
    and installs to platform-specific directories.

    Args:
        source: GitHub reference, e.g. ``github:org/repo``, ``org/repo@v1.0``.
        directory: Root directory of the consumer repository.
        platforms: Platform identifiers (e.g. ``["copilot"]``).
        force: When True, overwrite existing skill files.

    Returns:
        SkillsInstallResult with paths of created, overwritten, or skipped files.
    """
    ref = _parse_github_ref(source)
    result = SkillsInstallResult(platforms=list(platforms))
    resolved = directory.resolve()

    # Fetch and extract
    tarball = _fetch_github_tarball(ref)
    metadata, skill_files = _extract_skill_from_tarball(tarball, ref.repo)

    skill_name = _validate_skill_name(metadata["name"])

    if not skill_files:
        raise ValueError(f"No installable files found in {ref.owner}/{ref.repo}.")

    # Install to each platform
    for platform in platforms:
        config = _PLATFORM_CONFIGS.get(platform)
        if not config:
            continue

        target_dir = resolved / config["target_dir"]

        for relative_path, content_bytes in skill_files.items():
            if relative_path == "SKILL.md":
                # SKILL.md uses the platform file pattern
                dest_relative = config["file_pattern"].format(skill_name=skill_name)
                dest = target_dir / dest_relative
                text_content = content_bytes.decode("utf-8")
                text_content = _transform_content(text_content, platform)
                write_bytes = text_content.encode("utf-8")
            else:
                # Reference/asset files go alongside the SKILL.md
                if platform == "claude":
                    continue  # Claude only gets the single .md file
                skill_dest_dir = config["file_pattern"].format(
                    skill_name=skill_name
                )
                # e.g. "pptx-designer/SKILL.md" → "pptx-designer/"
                skill_base = str(PurePosixPath(skill_dest_dir).parent)
                dest = target_dir / skill_base / relative_path
                write_bytes = content_bytes

            # Security: ensure dest stays under target_dir
            try:
                dest.resolve().relative_to(target_dir.resolve())
            except ValueError:
                continue  # path traversal — skip silently

            existed = dest.exists()
            if existed and not force:
                result.skipped_files.append(dest)
                continue

            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(write_bytes)

            if existed:
                result.overwritten_files.append(dest)
            else:
                result.created_files.append(dest)

        # Write provenance file
        if platform != "claude":
            provenance_dest_rel = config["file_pattern"].format(
                skill_name=skill_name
            )
            provenance_dir = (
                target_dir / str(PurePosixPath(provenance_dest_rel).parent)
            )
            provenance = {
                "source": f"github:{ref.owner}/{ref.repo}",
                "ref": ref.ref,
                "skill_name": skill_name,
                "description": metadata.get("description", ""),
                "files": sorted(skill_files.keys()),
            }
            prov_path = provenance_dir / _PROVENANCE_FILE
            prov_path.parent.mkdir(parents=True, exist_ok=True)
            prov_path.write_text(
                json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
            )
            if prov_path not in result.created_files:
                result.created_files.append(prov_path)

    return result


@dataclass
class RegistrationResult:
    """Result of registering skills in coding agent instruction files.

    Attributes:
        registered_files: Instruction files that were created or updated.
    """

    registered_files: List[Path] = field(default_factory=list)


def _register_copilot(resolved: Path) -> Path | None:
    """Register skills in `.github/copilot-instructions.md`.

    - File absent → create with just the AgentOps block.
    - File exists, no marker → append block at end.
    - File exists, has marker → replace existing block (idempotent).
    """
    dest = resolved / ".github" / "copilot-instructions.md"
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not dest.exists():
        dest.write_text(_COPILOT_BLOCK + "\n", encoding="utf-8")
        return dest

    content = dest.read_text(encoding="utf-8")

    if _COPILOT_MARKER_START in content:
        # Replace existing block
        pattern = re.compile(
            re.escape(_COPILOT_MARKER_START) + r".*?" + re.escape(_COPILOT_MARKER_END),
            re.DOTALL,
        )
        new_content = pattern.sub(_COPILOT_BLOCK, content)
        if new_content != content:
            dest.write_text(new_content, encoding="utf-8")
        return dest

    # Append to end
    separator = "\n" if content.endswith("\n") else "\n\n"
    dest.write_text(content + separator + _COPILOT_BLOCK + "\n", encoding="utf-8")
    return dest


def _register_cursor(resolved: Path) -> Path | None:
    """Register skills in `.cursor/rules/agentops.mdc`.

    Always overwrites — this is a fully managed file.
    """
    dest = resolved / ".cursor" / "rules" / "agentops.mdc"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_CURSOR_MDC, encoding="utf-8")
    return dest


# Map platform names to their registration functions.
_PLATFORM_REGISTRARS: Dict[str, object] = {
    "copilot": _register_copilot,
    "cursor": _register_cursor,
}


def register_skills(
    directory: Path,
    platforms: list[str],
) -> RegistrationResult:
    """Register installed skills in coding agent instruction files.

    For each detected platform, writes or updates the appropriate
    instruction file so the AI assistant discovers the skill files.

    Args:
        directory: Root directory of the consumer repository.
        platforms: List of platform identifiers (e.g. ``["copilot"]``).

    Returns:
        RegistrationResult with paths of instruction files that were updated.
    """
    result = RegistrationResult()
    resolved = directory.resolve()

    for platform in platforms:
        registrar = _PLATFORM_REGISTRARS.get(platform)
        if registrar is None:
            continue
        path = registrar(resolved)  # type: ignore[operator]
        if path is not None:
            result.registered_files.append(path)

    return result
