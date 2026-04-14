"""Coding agent skills installation and registration service."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
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
# Registration — add skill discovery entries to instruction files
# ---------------------------------------------------------------------------


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
