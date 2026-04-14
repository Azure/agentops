from pathlib import Path

from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.services.skills import (
    _COPILOT_MARKER_END,
    _COPILOT_MARKER_START,
    detect_platforms,
    install_skills,
    register_skills,
)

runner = CliRunner()

_COPILOT_SKILL_PATHS = [
    ".github/skills/agentops-eval/SKILL.md",
    ".github/skills/agentops-config/SKILL.md",
    ".github/skills/agentops-dataset/SKILL.md",
    ".github/skills/agentops-report/SKILL.md",
    ".github/skills/agentops-regression/SKILL.md",
    ".github/skills/agentops-trace/SKILL.md",
    ".github/skills/agentops-monitor/SKILL.md",
    ".github/skills/agentops-workflow/SKILL.md",
]

_CLAUDE_SKILL_PATHS = [
    ".claude/commands/agentops-eval.md",
    ".claude/commands/agentops-config.md",
    ".claude/commands/agentops-dataset.md",
    ".claude/commands/agentops-report.md",
    ".claude/commands/agentops-regression.md",
    ".claude/commands/agentops-trace.md",
    ".claude/commands/agentops-monitor.md",
    ".claude/commands/agentops-workflow.md",
]


# ---------------------------------------------------------------------------
# detect_platforms
# ---------------------------------------------------------------------------


def test_detect_platforms_empty(tmp_path: Path) -> None:
    assert detect_platforms(tmp_path) == []


def test_detect_platforms_copilot_instructions(tmp_path: Path) -> None:
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "copilot-instructions.md").write_text("# Instructions")
    assert detect_platforms(tmp_path) == ["copilot"]


def test_detect_platforms_copilot_skills_dir(tmp_path: Path) -> None:
    (tmp_path / ".github" / "skills").mkdir(parents=True)
    assert detect_platforms(tmp_path) == ["copilot"]


def test_detect_platforms_claude(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    assert detect_platforms(tmp_path) == ["claude"]


def test_detect_platforms_claude_md(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("# Claude")
    assert detect_platforms(tmp_path) == ["claude"]


def test_detect_platforms_multiple(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".github" / "skills").mkdir(parents=True)
    platforms = detect_platforms(tmp_path)
    assert "claude" in platforms
    assert "copilot" in platforms


# ---------------------------------------------------------------------------
# install_skills — copilot platform
# ---------------------------------------------------------------------------


def test_install_creates_copilot_files(tmp_path: Path) -> None:
    result = install_skills(directory=tmp_path, platforms=["copilot"])

    assert result.platforms == ["copilot"]
    assert len(result.created_files) == 8
    assert len(result.skipped_files) == 0

    for rel in _COPILOT_SKILL_PATHS:
        skill_file = tmp_path / rel
        assert skill_file.exists(), f"Missing: {rel}"
        content = skill_file.read_text(encoding="utf-8")
        assert "AgentOps" in content


def test_copilot_files_have_frontmatter(tmp_path: Path) -> None:
    install_skills(directory=tmp_path, platforms=["copilot"])
    content = (tmp_path / ".github/skills/agentops-eval/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert content.startswith("---")


# ---------------------------------------------------------------------------
# install_skills — claude platform
# ---------------------------------------------------------------------------


def test_install_creates_claude_files(tmp_path: Path) -> None:
    result = install_skills(directory=tmp_path, platforms=["claude"])

    assert result.platforms == ["claude"]
    assert len(result.created_files) == 8

    for rel in _CLAUDE_SKILL_PATHS:
        skill_file = tmp_path / rel
        assert skill_file.exists(), f"Missing: {rel}"


def test_claude_files_strip_frontmatter(tmp_path: Path) -> None:
    install_skills(directory=tmp_path, platforms=["claude"])
    content = (tmp_path / ".claude/commands/agentops-eval.md").read_text(
        encoding="utf-8"
    )
    assert not content.startswith("---")
    assert "AgentOps" in content


# ---------------------------------------------------------------------------
# install_skills — multi-platform
# ---------------------------------------------------------------------------


def test_install_multi_platform(tmp_path: Path) -> None:
    result = install_skills(directory=tmp_path, platforms=["copilot", "claude"])
    assert len(result.created_files) == 16  # 8 per platform
    assert result.platforms == ["copilot", "claude"]


# ---------------------------------------------------------------------------
# install_skills — skip / overwrite
# ---------------------------------------------------------------------------


def test_install_skips_existing(tmp_path: Path) -> None:
    install_skills(directory=tmp_path, platforms=["copilot"])

    skill = tmp_path / ".github/skills/agentops-eval/SKILL.md"
    skill.write_text("custom content", encoding="utf-8")

    result = install_skills(directory=tmp_path, platforms=["copilot"], force=False)

    assert len(result.skipped_files) == 8
    assert len(result.created_files) == 0
    assert skill.read_text(encoding="utf-8") == "custom content"


def test_install_overwrites_with_force(tmp_path: Path) -> None:
    install_skills(directory=tmp_path, platforms=["copilot"])

    skill = tmp_path / ".github/skills/agentops-eval/SKILL.md"
    skill.write_text("custom content", encoding="utf-8")

    result = install_skills(directory=tmp_path, platforms=["copilot"], force=True)

    assert len(result.overwritten_files) == 8
    content = skill.read_text(encoding="utf-8")
    assert content != "custom content"
    assert "AgentOps" in content


# ---------------------------------------------------------------------------
# install_skills — unknown platform
# ---------------------------------------------------------------------------


def test_install_unknown_platform(tmp_path: Path) -> None:
    result = install_skills(directory=tmp_path, platforms=["unknown"])
    assert len(result.created_files) == 0
    assert result.platforms == ["unknown"]


# ---------------------------------------------------------------------------
# CLI — agentops skills install
# ---------------------------------------------------------------------------


def test_cli_skills_install_default_copilot(tmp_path: Path) -> None:
    result = runner.invoke(app, ["skills", "install", "--dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "created" in result.stdout

    for rel in _COPILOT_SKILL_PATHS:
        assert (tmp_path / rel).exists()


def test_cli_skills_install_explicit_claude(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["skills", "install", "--platform", "claude", "--dir", str(tmp_path)],
    )
    assert result.exit_code == 0

    for rel in _CLAUDE_SKILL_PATHS:
        assert (tmp_path / rel).exists()


def test_cli_skills_install_skips_existing(tmp_path: Path) -> None:
    install_skills(directory=tmp_path, platforms=["copilot"])

    result = runner.invoke(app, ["skills", "install", "--dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "overwritten" in result.stdout


def test_cli_skills_install_force_overwrites(tmp_path: Path) -> None:
    install_skills(directory=tmp_path, platforms=["copilot"])

    result = runner.invoke(
        app, ["skills", "install", "--force", "--dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "overwritten" in result.stdout


# ---------------------------------------------------------------------------
# CLI — agentops init does NOT install skills (skills install is separate)
# ---------------------------------------------------------------------------


def test_cli_init_does_not_install_skills(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "--dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "Initialized workspace" in result.stdout
    assert "agentops skills install" in result.stdout

    # Skills should NOT be created during init
    for rel in _COPILOT_SKILL_PATHS:
        assert not (tmp_path / rel).exists(), f"Should not exist after init: {rel}"


# ---------------------------------------------------------------------------
# detect_platforms — cursor
# ---------------------------------------------------------------------------


def test_detect_platforms_cursor_rules_dir(tmp_path: Path) -> None:
    (tmp_path / ".cursor" / "rules").mkdir(parents=True)
    assert detect_platforms(tmp_path) == ["cursor"]


def test_detect_platforms_cursorrules_file(tmp_path: Path) -> None:
    (tmp_path / ".cursorrules").write_text("# rules")
    assert detect_platforms(tmp_path) == ["cursor"]


# ---------------------------------------------------------------------------
# detect_platforms — underscore copilot filename
# ---------------------------------------------------------------------------


def test_detect_platforms_copilot_underscore(tmp_path: Path) -> None:
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "copilot_instructions.md").write_text("# Instructions")
    assert detect_platforms(tmp_path) == ["copilot"]


# ---------------------------------------------------------------------------
# detect_platforms — copilot + cursor combo
# ---------------------------------------------------------------------------


def test_detect_platforms_copilot_and_cursor(tmp_path: Path) -> None:
    (tmp_path / ".github" / "skills").mkdir(parents=True)
    (tmp_path / ".cursorrules").write_text("# rules")
    platforms = detect_platforms(tmp_path)
    assert "copilot" in platforms
    assert "cursor" in platforms


# ---------------------------------------------------------------------------
# register_skills — copilot
# ---------------------------------------------------------------------------


def test_register_copilot_creates_file(tmp_path: Path) -> None:
    result = register_skills(directory=tmp_path, platforms=["copilot"])
    dest = tmp_path / ".github" / "copilot-instructions.md"
    assert dest.exists()
    content = dest.read_text(encoding="utf-8")
    assert _COPILOT_MARKER_START in content
    assert _COPILOT_MARKER_END in content
    assert "agentops-eval" in content
    assert len(result.registered_files) == 1


def test_register_copilot_appends_to_existing(tmp_path: Path) -> None:
    dest = tmp_path / ".github" / "copilot-instructions.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("# My Project\n\nExisting instructions.\n", encoding="utf-8")

    result = register_skills(directory=tmp_path, platforms=["copilot"])
    content = dest.read_text(encoding="utf-8")
    assert content.startswith("# My Project")
    assert "Existing instructions." in content
    assert _COPILOT_MARKER_START in content
    assert len(result.registered_files) == 1


def test_register_copilot_idempotent(tmp_path: Path) -> None:
    dest = tmp_path / ".github" / "copilot-instructions.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("# Project\n", encoding="utf-8")

    register_skills(directory=tmp_path, platforms=["copilot"])
    first_content = dest.read_text(encoding="utf-8")

    register_skills(directory=tmp_path, platforms=["copilot"])
    second_content = dest.read_text(encoding="utf-8")

    assert first_content == second_content


def test_register_copilot_replaces_existing_block(tmp_path: Path) -> None:
    dest = tmp_path / ".github" / "copilot-instructions.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        f"# Project\n\n{_COPILOT_MARKER_START}\nOLD CONTENT\n{_COPILOT_MARKER_END}\n\n# Footer\n",
        encoding="utf-8",
    )

    register_skills(directory=tmp_path, platforms=["copilot"])
    content = dest.read_text(encoding="utf-8")
    assert "OLD CONTENT" not in content
    assert "agentops-eval" in content
    assert "# Footer" in content


# ---------------------------------------------------------------------------
# register_skills — cursor
# ---------------------------------------------------------------------------


def test_register_cursor_creates_mdc(tmp_path: Path) -> None:
    result = register_skills(directory=tmp_path, platforms=["cursor"])
    dest = tmp_path / ".cursor" / "rules" / "agentops.mdc"
    assert dest.exists()
    content = dest.read_text(encoding="utf-8")
    assert "agentops-eval" in content
    assert "alwaysApply: true" in content
    assert len(result.registered_files) == 1


def test_register_cursor_overwrites(tmp_path: Path) -> None:
    dest = tmp_path / ".cursor" / "rules" / "agentops.mdc"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("old content", encoding="utf-8")

    register_skills(directory=tmp_path, platforms=["cursor"])
    content = dest.read_text(encoding="utf-8")
    assert "old content" not in content
    assert "agentops-eval" in content


# ---------------------------------------------------------------------------
# register_skills — unknown platform returns empty
# ---------------------------------------------------------------------------


def test_register_unknown_platform(tmp_path: Path) -> None:
    result = register_skills(directory=tmp_path, platforms=["unknown"])
    assert len(result.registered_files) == 0


# ---------------------------------------------------------------------------
# CLI — registration triggered by init
# ---------------------------------------------------------------------------


def test_cli_init_does_not_register_skills(tmp_path: Path) -> None:
    """After decoupling, `init` no longer registers skills."""
    result = runner.invoke(app, ["init", "--dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "registered skills in" not in result.stdout
    assert "agentops skills install" in result.stdout


def test_cli_skills_install_registers_skills(tmp_path: Path) -> None:
    result = runner.invoke(app, ["skills", "install", "--dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "registered skills in" in result.stdout


def test_cli_init_does_not_install_skills_claude(tmp_path: Path) -> None:
    """After decoupling, `init` no longer detects platforms or installs skills."""
    (tmp_path / ".claude").mkdir()

    result = runner.invoke(app, ["init", "--dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "agentops skills install" in result.stdout

    for rel in _CLAUDE_SKILL_PATHS:
        assert not (tmp_path / rel).exists(), f"Should not exist after init: {rel}"
