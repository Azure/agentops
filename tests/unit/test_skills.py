import io
import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from agentops.cli.app import app
from agentops.services.skills import (
    _COPILOT_MARKER_END,
    _COPILOT_MARKER_START,
    _extract_skill_from_tarball,
    _parse_github_ref,
    _parse_skill_frontmatter,
    _validate_skill_name,
    detect_platforms,
    install_github_skill,
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


# ---------------------------------------------------------------------------
# GitHub ref parsing
# ---------------------------------------------------------------------------


def test_parse_github_ref_simple() -> None:
    ref = _parse_github_ref("donlee/pptx-designer")
    assert ref.owner == "donlee"
    assert ref.repo == "pptx-designer"
    assert ref.ref == "main"


def test_parse_github_ref_with_prefix() -> None:
    ref = _parse_github_ref("github:org/repo")
    assert ref.owner == "org"
    assert ref.repo == "repo"
    assert ref.ref == "main"


def test_parse_github_ref_with_version() -> None:
    ref = _parse_github_ref("github:org/repo@v1.2.3")
    assert ref.owner == "org"
    assert ref.repo == "repo"
    assert ref.ref == "v1.2.3"


def test_parse_github_ref_with_branch() -> None:
    ref = _parse_github_ref("org/repo@feature/my-branch")
    assert ref.ref == "feature/my-branch"


def test_parse_github_ref_invalid() -> None:
    with pytest.raises(ValueError, match="Invalid GitHub skill reference"):
        _parse_github_ref("not-valid")


def test_parse_github_ref_empty() -> None:
    with pytest.raises(ValueError, match="Invalid GitHub skill reference"):
        _parse_github_ref("")


# ---------------------------------------------------------------------------
# Skill name validation
# ---------------------------------------------------------------------------


def test_validate_skill_name_valid() -> None:
    assert _validate_skill_name("pptx-designer") == "pptx-designer"
    assert _validate_skill_name("myskill") == "myskill"
    assert _validate_skill_name("my-cool-skill") == "my-cool-skill"


def test_validate_skill_name_invalid() -> None:
    with pytest.raises(ValueError, match="Invalid skill name"):
        _validate_skill_name("My Skill")

    with pytest.raises(ValueError, match="Invalid skill name"):
        _validate_skill_name("../traversal")

    with pytest.raises(ValueError, match="Invalid skill name"):
        _validate_skill_name("")

    with pytest.raises(ValueError, match="Invalid skill name"):
        _validate_skill_name("UPPERCASE")


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


_VALID_FRONTMATTER = """\
---
name: test-skill
description: A test skill for unit testing.
license: MIT
---

# Test Skill

Instructions here.
"""


def test_parse_frontmatter_valid() -> None:
    meta = _parse_skill_frontmatter(_VALID_FRONTMATTER)
    assert meta["name"] == "test-skill"
    assert "test skill" in meta["description"].lower()


def test_parse_frontmatter_missing_name() -> None:
    content = "---\ndescription: test\n---\n# Body"
    with pytest.raises(ValueError, match="missing required 'name'"):
        _parse_skill_frontmatter(content)


def test_parse_frontmatter_missing_description() -> None:
    content = "---\nname: test\n---\n# Body"
    with pytest.raises(ValueError, match="missing required 'description'"):
        _parse_skill_frontmatter(content)


def test_parse_frontmatter_no_frontmatter() -> None:
    with pytest.raises(ValueError, match="missing YAML frontmatter"):
        _parse_skill_frontmatter("# Just a heading")


def test_parse_frontmatter_unclosed() -> None:
    with pytest.raises(ValueError, match="unclosed YAML frontmatter"):
        _parse_skill_frontmatter("---\nname: test\n")


def test_parse_frontmatter_multiline_description() -> None:
    content = "---\nname: test-skill\ndescription: >\n  A long\n  description here.\n---\n# Body"
    meta = _parse_skill_frontmatter(content)
    assert "long" in meta["description"]
    assert "description here" in meta["description"]


# ---------------------------------------------------------------------------
# Tarball extraction
# ---------------------------------------------------------------------------


def _make_test_tarball(files: dict[str, str], prefix: str = "owner-repo-abc123") -> bytes:
    """Create a gzipped tarball with the given files for testing."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, content in files.items():
            full_path = f"{prefix}/{path}"
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=full_path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_extract_skill_from_tarball() -> None:
    tarball = _make_test_tarball({
        "my-skill/SKILL.md": _VALID_FRONTMATTER,
        "my-skill/references/guide.md": "# Guide\n\nSome content.",
    })
    meta, files = _extract_skill_from_tarball(tarball, "my-skill")
    assert meta["name"] == "test-skill"
    assert "SKILL.md" in files
    assert "references/guide.md" in files


def test_extract_skill_prefers_repo_named_dir() -> None:
    tarball = _make_test_tarball({
        "my-skill/SKILL.md": _VALID_FRONTMATTER,
        "other-dir/SKILL.md": _VALID_FRONTMATTER,
    })
    meta, files = _extract_skill_from_tarball(tarball, "my-skill")
    assert meta["name"] == "test-skill"


def test_extract_skill_root_skill_md() -> None:
    tarball = _make_test_tarball({
        "SKILL.md": _VALID_FRONTMATTER,
    })
    meta, files = _extract_skill_from_tarball(tarball, "some-repo")
    assert meta["name"] == "test-skill"
    assert "SKILL.md" in files


def test_extract_skill_no_skill_md() -> None:
    tarball = _make_test_tarball({
        "README.md": "# Hello",
    })
    with pytest.raises(ValueError, match="No SKILL.md found"):
        _extract_skill_from_tarball(tarball, "some-repo")


def test_extract_skill_multiple_ambiguous() -> None:
    tarball = _make_test_tarball({
        "skill-a/SKILL.md": _VALID_FRONTMATTER,
        "skill-b/SKILL.md": _VALID_FRONTMATTER.replace("test-skill", "other-skill"),
    })
    with pytest.raises(ValueError, match="Multiple skills found"):
        _extract_skill_from_tarball(tarball, "unrelated-repo")


def test_extract_skill_skips_scripts() -> None:
    tarball = _make_test_tarball({
        "my-skill/SKILL.md": _VALID_FRONTMATTER,
        "my-skill/scripts/run.py": "print('hello')",
        "my-skill/references/ref.md": "# Ref",
    })
    _, files = _extract_skill_from_tarball(tarball, "my-skill")
    assert "references/ref.md" in files
    assert "scripts/run.py" not in files  # scripts blocked by default


def test_extract_skill_blocks_path_traversal() -> None:
    tarball = _make_test_tarball({
        "my-skill/SKILL.md": _VALID_FRONTMATTER,
        "my-skill/../../../etc/passwd": "root:x:0:0",
    })
    _, files = _extract_skill_from_tarball(tarball, "my-skill")
    assert all(".." not in p for p in files)


def test_extract_skill_blocks_hidden_files() -> None:
    tarball = _make_test_tarball({
        "my-skill/SKILL.md": _VALID_FRONTMATTER,
        "my-skill/.env": "SECRET=abc",
        "my-skill/references/guide.md": "# Guide",
    })
    _, files = _extract_skill_from_tarball(tarball, "my-skill")
    assert ".env" not in files
    assert "references/guide.md" in files


# ---------------------------------------------------------------------------
# install_github_skill (with mocked network)
# ---------------------------------------------------------------------------


def test_install_github_skill_copilot(tmp_path: Path) -> None:
    tarball = _make_test_tarball({
        "pptx-designer/SKILL.md": _VALID_FRONTMATTER,
        "pptx-designer/references/setup.md": "# Setup guide",
    })

    with patch(
        "agentops.services.skills._fetch_github_tarball", return_value=tarball
    ):
        result = install_github_skill(
            source="donlee/pptx-designer",
            directory=tmp_path,
            platforms=["copilot"],
            force=True,
        )

    # SKILL.md installed
    skill_path = tmp_path / ".github/skills/test-skill/SKILL.md"
    assert skill_path.exists()
    content = skill_path.read_text(encoding="utf-8")
    assert content.startswith("---")  # frontmatter preserved for copilot

    # Reference file installed
    ref_path = tmp_path / ".github/skills/test-skill/references/setup.md"
    assert ref_path.exists()

    # Provenance file created
    prov_path = tmp_path / ".github/skills/test-skill/.installed-from.json"
    assert prov_path.exists()
    prov = json.loads(prov_path.read_text())
    assert prov["source"] == "github:donlee/pptx-designer"
    assert prov["skill_name"] == "test-skill"

    assert len(result.created_files) >= 2


def test_install_github_skill_claude(tmp_path: Path) -> None:
    tarball = _make_test_tarball({
        "pptx-designer/SKILL.md": _VALID_FRONTMATTER,
        "pptx-designer/references/setup.md": "# Setup guide",
    })

    with patch(
        "agentops.services.skills._fetch_github_tarball", return_value=tarball
    ):
        result = install_github_skill(
            source="donlee/pptx-designer",
            directory=tmp_path,
            platforms=["claude"],
        )

    # Claude gets a single .md file with frontmatter stripped
    skill_path = tmp_path / ".claude/commands/test-skill.md"
    assert skill_path.exists()
    content = skill_path.read_text(encoding="utf-8")
    assert not content.startswith("---")  # frontmatter stripped

    # Claude does NOT get reference files
    ref_path = tmp_path / ".claude/commands/references/setup.md"
    assert not ref_path.exists()


def test_install_github_skill_skip_existing(tmp_path: Path) -> None:
    tarball = _make_test_tarball({
        "my-skill/SKILL.md": _VALID_FRONTMATTER,
    })

    # Pre-create the file
    dest = tmp_path / ".github/skills/test-skill/SKILL.md"
    dest.parent.mkdir(parents=True)
    dest.write_text("custom content")

    with patch(
        "agentops.services.skills._fetch_github_tarball", return_value=tarball
    ):
        result = install_github_skill(
            source="org/my-skill",
            directory=tmp_path,
            platforms=["copilot"],
            force=False,
        )

    assert len(result.skipped_files) >= 1
    assert dest.read_text() == "custom content"


# ---------------------------------------------------------------------------
# CLI — agentops skills install --from
# ---------------------------------------------------------------------------


def test_cli_skills_install_from_github(tmp_path: Path) -> None:
    tarball = _make_test_tarball({
        "pptx-designer/SKILL.md": _VALID_FRONTMATTER,
    })

    with patch(
        "agentops.services.skills._fetch_github_tarball", return_value=tarball
    ):
        result = runner.invoke(
            app,
            [
                "skills", "install",
                "--from", "donlee/pptx-designer",
                "--dir", str(tmp_path),
            ],
        )

    assert result.exit_code == 0
    assert "Installing skill from GitHub" in result.stdout
    assert "created" in result.stdout


def test_cli_skills_install_from_invalid_ref(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "skills", "install",
            "--from", "not-valid-ref",
            "--dir", str(tmp_path),
        ],
    )
    assert result.exit_code == 1
