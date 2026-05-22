"""Verify that plugins/agentops/skills/ is in sync with src/agentops/templates/skills/.

The single source of truth for skill files is src/agentops/templates/skills/.
The VS Code extension at plugins/agentops/skills/ must be an exact copy.

If this test fails, run:
    scripts/sync-skills.sh   (Linux/macOS)
    scripts/sync-skills.ps1  (Windows)
"""

from pathlib import Path

# Repository root is four levels up from this test file (tests/unit/test_skills_sync.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC_SKILLS = _REPO_ROOT / "src" / "agentops" / "templates" / "skills"
_PLUGIN_SKILLS = _REPO_ROOT / "plugins" / "agentops" / "skills"


def _skill_dirs() -> list[str]:
    """Return skill directory names from the source of truth."""
    if not _SRC_SKILLS.is_dir():
        return []
    return sorted(d.name for d in _SRC_SKILLS.iterdir() if d.is_dir())


def test_plugin_skills_directory_exists() -> None:
    assert _PLUGIN_SKILLS.is_dir(), (
        f"Plugin skills directory missing: {_PLUGIN_SKILLS}\n"
        "Run scripts/sync-skills.sh (or .ps1) to create it."
    )


def test_all_skills_present_in_plugin() -> None:
    """Every skill in src/ must also exist in plugins/."""
    src_skills = _skill_dirs()
    assert src_skills, "No skills found in src/agentops/templates/skills/"

    for skill_name in src_skills:
        plugin_file = _PLUGIN_SKILLS / skill_name / "SKILL.md"
        assert plugin_file.exists(), (
            f"Skill '{skill_name}/SKILL.md' exists in src/ but not in plugins/.\n"
            "Run scripts/sync-skills.sh (or .ps1) to fix."
        )


def test_skill_contents_match() -> None:
    """Content of each SKILL.md must be identical between src/ and plugins/."""
    src_skills = _skill_dirs()
    mismatched: list[str] = []

    for skill_name in src_skills:
        src_file = _SRC_SKILLS / skill_name / "SKILL.md"
        plugin_file = _PLUGIN_SKILLS / skill_name / "SKILL.md"

        if not src_file.exists() or not plugin_file.exists():
            continue

        src_content = src_file.read_text(encoding="utf-8")
        plugin_content = plugin_file.read_text(encoding="utf-8")

        if src_content != plugin_content:
            mismatched.append(skill_name)

    assert not mismatched, (
        f"Skill file(s) out of sync: {', '.join(mismatched)}\n"
        "Run scripts/sync-skills.sh (or .ps1) to fix."
    )


def test_no_extra_skills_in_plugin() -> None:
    """Plugin dir should not contain skills that don't exist in src/."""
    src_skills = set(_skill_dirs())
    if not _PLUGIN_SKILLS.is_dir():
        return

    plugin_skills = {d.name for d in _PLUGIN_SKILLS.iterdir() if d.is_dir()}
    extra = plugin_skills - src_skills
    assert not extra, (
        f"Plugin contains skill(s) not in src/: {', '.join(sorted(extra))}\n"
        "Remove them or add them to src/agentops/templates/skills/ first."
    )
