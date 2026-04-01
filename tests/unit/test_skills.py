from pathlib import Path

from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.services.skills import (
    SkillsInstallResult,
    detect_platforms,
    install_skills,
)

runner = CliRunner()

_COPILOT_SKILL_PATHS = [
    ".github/skills/evals/SKILL.md",
    ".github/skills/regression/SKILL.md",
    ".github/skills/trace/SKILL.md",
    ".github/skills/monitor/SKILL.md",
    ".github/skills/workflows/SKILL.md",
]

_CLAUDE_SKILL_PATHS = [
    ".claude/commands/evals.md",
    ".claude/commands/regression.md",
    ".claude/commands/trace.md",
    ".claude/commands/monitor.md",
    ".claude/commands/workflows.md",
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
    assert len(result.created_files) == 5
    assert len(result.skipped_files) == 0

    for rel in _COPILOT_SKILL_PATHS:
        skill_file = tmp_path / rel
        assert skill_file.exists(), f"Missing: {rel}"
        content = skill_file.read_text(encoding="utf-8")
        assert "AgentOps" in content


def test_copilot_files_have_frontmatter(tmp_path: Path) -> None:
    install_skills(directory=tmp_path, platforms=["copilot"])
    content = (
        tmp_path / ".github/skills/evals/SKILL.md"
    ).read_text(encoding="utf-8")
    assert content.startswith("---")


# ---------------------------------------------------------------------------
# install_skills — claude platform
# ---------------------------------------------------------------------------


def test_install_creates_claude_files(tmp_path: Path) -> None:
    result = install_skills(directory=tmp_path, platforms=["claude"])

    assert result.platforms == ["claude"]
    assert len(result.created_files) == 5

    for rel in _CLAUDE_SKILL_PATHS:
        skill_file = tmp_path / rel
        assert skill_file.exists(), f"Missing: {rel}"


def test_claude_files_strip_frontmatter(tmp_path: Path) -> None:
    install_skills(directory=tmp_path, platforms=["claude"])
    content = (
        tmp_path / ".claude/commands/evals.md"
    ).read_text(encoding="utf-8")
    assert not content.startswith("---")
    assert "AgentOps" in content


# ---------------------------------------------------------------------------
# install_skills — multi-platform
# ---------------------------------------------------------------------------


def test_install_multi_platform(tmp_path: Path) -> None:
    result = install_skills(
        directory=tmp_path, platforms=["copilot", "claude"]
    )
    assert len(result.created_files) == 10  # 5 per platform
    assert result.platforms == ["copilot", "claude"]


# ---------------------------------------------------------------------------
# install_skills — skip / overwrite
# ---------------------------------------------------------------------------


def test_install_skips_existing(tmp_path: Path) -> None:
    install_skills(directory=tmp_path, platforms=["copilot"])

    skill = tmp_path / ".github/skills/evals/SKILL.md"
    skill.write_text("custom content", encoding="utf-8")

    result = install_skills(directory=tmp_path, platforms=["copilot"], force=False)

    assert len(result.skipped_files) == 5
    assert len(result.created_files) == 0
    assert skill.read_text(encoding="utf-8") == "custom content"


def test_install_overwrites_with_force(tmp_path: Path) -> None:
    install_skills(directory=tmp_path, platforms=["copilot"])

    skill = tmp_path / ".github/skills/evals/SKILL.md"
    skill.write_text("custom content", encoding="utf-8")

    result = install_skills(directory=tmp_path, platforms=["copilot"], force=True)

    assert len(result.overwritten_files) == 5
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
    result = runner.invoke(
        app, ["skills", "install", "--dir", str(tmp_path)]
    )
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

    result = runner.invoke(
        app, ["skills", "install", "--dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "skipped" in result.stdout


def test_cli_skills_install_force_overwrites(tmp_path: Path) -> None:
    install_skills(directory=tmp_path, platforms=["copilot"])

    result = runner.invoke(
        app, ["skills", "install", "--force", "--dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "overwritten" in result.stdout


# ---------------------------------------------------------------------------
# CLI — agentops init includes skills
# ---------------------------------------------------------------------------


def test_cli_init_installs_skills(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "--dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "Initialized workspace" in result.stdout
    assert "Skills platforms" in result.stdout

    # Skills should be created (copilot default since no platform detected)
    for rel in _COPILOT_SKILL_PATHS:
        assert (tmp_path / rel).exists(), f"Missing after init: {rel}"


def test_cli_init_detects_claude(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()

    result = runner.invoke(app, ["init", "--dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "Detected coding agent platform(s): claude" in result.stdout

    for rel in _CLAUDE_SKILL_PATHS:
        assert (tmp_path / rel).exists(), f"Missing after init: {rel}"
