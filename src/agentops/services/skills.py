"""Coding agent skills installation service for `agentops skills install`."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Dict, List

_TEMPLATE_PACKAGE = "agentops.templates"

_SKILLS: tuple[str, ...] = (
    "skills/evals/SKILL.md",
    "skills/regression/SKILL.md",
    "skills/trace/SKILL.md",
    "skills/monitor/SKILL.md",
    "skills/workflows/SKILL.md",
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
}

_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)


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
        or (resolved / ".github" / "skills").exists()
    ):
        platforms.append("copilot")

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
            # "skills/evals/SKILL.md" → "evals"
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
