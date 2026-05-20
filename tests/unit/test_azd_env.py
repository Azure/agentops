"""Tests for :mod:`agentops.utils.azd_env`."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentops.utils.azd_env import (
    discover_azd_env,
    ensure_azd_env,
    ensure_azure_gitignore,
    parse_env_file,
    set_env_values,
)


def _write_env(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discover_returns_not_found_without_azure_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    location = discover_azd_env(tmp_path)
    assert location.found is False
    assert location.status == "not_found"


def test_discover_honours_azure_env_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _write_env(tmp_path / ".azure" / "dev" / ".env", "FOO=dev\n")
    _write_env(tmp_path / ".azure" / "prod" / ".env", "FOO=prod\n")
    monkeypatch.setenv("AZURE_ENV_NAME", "prod")
    location = discover_azd_env(tmp_path)
    assert location.name == "prod"
    assert location.env_path == (tmp_path / ".azure" / "prod" / ".env").resolve()
    assert location.found is True


def test_discover_honours_default_environment_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    _write_env(tmp_path / ".azure" / "dev" / ".env", "FOO=dev\n")
    _write_env(tmp_path / ".azure" / "prod" / ".env", "FOO=prod\n")
    (tmp_path / ".azure" / "config.json").write_text(
        json.dumps({"version": 1, "defaultEnvironment": "dev"}) + "\n",
        encoding="utf-8",
    )
    location = discover_azd_env(tmp_path)
    assert location.name == "dev"


def test_discover_falls_back_to_single_env_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    _write_env(tmp_path / ".azure" / "myenv" / ".env", "FOO=bar\n")
    location = discover_azd_env(tmp_path)
    assert location.name == "myenv"


def test_discover_reports_ambiguity_for_multiple_envs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    _write_env(tmp_path / ".azure" / "dev" / ".env", "FOO=dev\n")
    _write_env(tmp_path / ".azure" / "prod" / ".env", "FOO=prod\n")
    location = discover_azd_env(tmp_path)
    assert location.status == "ambiguous"
    assert sorted(location.candidates) == ["dev", "prod"]


def test_discover_missing_env_file_reports_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AZURE_ENV_NAME", "dev")
    (tmp_path / ".azure").mkdir()
    location = discover_azd_env(tmp_path)
    assert location.status == "missing_env_file"
    assert location.name == "dev"


# ---------------------------------------------------------------------------
# Bootstrap / gitignore
# ---------------------------------------------------------------------------


def test_ensure_azd_env_creates_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    location = ensure_azd_env(tmp_path, "dev")
    assert location.found is True
    assert (tmp_path / ".azure" / "dev" / ".env").is_file()
    assert (tmp_path / ".azure" / ".gitignore").is_file()
    config = json.loads((tmp_path / ".azure" / "config.json").read_text(encoding="utf-8"))
    assert config["defaultEnvironment"] == "dev"


def test_ensure_azd_env_preserves_existing_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    (tmp_path / ".azure").mkdir()
    (tmp_path / ".azure" / "config.json").write_text(
        json.dumps({"version": 1, "defaultEnvironment": "prod"}) + "\n",
        encoding="utf-8",
    )
    ensure_azd_env(tmp_path, "dev")
    config = json.loads((tmp_path / ".azure" / "config.json").read_text(encoding="utf-8"))
    assert config["defaultEnvironment"] == "prod"


def test_ensure_azd_env_is_idempotent(tmp_path: Path):
    ensure_azd_env(tmp_path, "dev")
    env_file = tmp_path / ".azure" / "dev" / ".env"
    env_file.write_text("MY_VALUE=hello\n", encoding="utf-8")
    # Second call must not overwrite the .env content.
    ensure_azd_env(tmp_path, "dev")
    assert env_file.read_text(encoding="utf-8") == "MY_VALUE=hello\n"


def test_ensure_azure_gitignore_creates_pattern(tmp_path: Path):
    azure_dir = tmp_path / ".azure"
    ensure_azure_gitignore(azure_dir)
    gi = (azure_dir / ".gitignore").read_text(encoding="utf-8")
    assert "*/.env" in gi


def test_ensure_azure_gitignore_appends_when_missing(tmp_path: Path):
    azure_dir = tmp_path / ".azure"
    azure_dir.mkdir()
    (azure_dir / ".gitignore").write_text("# existing\nfoo\n", encoding="utf-8")
    created = ensure_azure_gitignore(azure_dir)
    assert created is True
    text = (azure_dir / ".gitignore").read_text(encoding="utf-8")
    assert "foo" in text
    assert "*/.env" in text


def test_ensure_azure_gitignore_is_idempotent(tmp_path: Path):
    azure_dir = tmp_path / ".azure"
    azure_dir.mkdir()
    (azure_dir / ".gitignore").write_text("*/.env\n", encoding="utf-8")
    created = ensure_azure_gitignore(azure_dir)
    assert created is False


# ---------------------------------------------------------------------------
# set_env_values — line-preserving
# ---------------------------------------------------------------------------


def test_set_env_values_appends_new_key(tmp_path: Path):
    env = tmp_path / ".env"
    changed = set_env_values(env, {"NEW_KEY": "hello"})
    assert changed == ["NEW_KEY"]
    assert "NEW_KEY=hello" in env.read_text(encoding="utf-8")


def test_set_env_values_replaces_existing_line(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "# header\nFOO=old\nBAR=keep\n# trailer\n",
        encoding="utf-8",
    )
    changed = set_env_values(env, {"FOO": "new"})
    assert changed == ["FOO"]
    text = env.read_text(encoding="utf-8")
    assert "# header" in text
    assert "BAR=keep" in text
    assert "# trailer" in text
    assert "FOO=new" in text
    assert "FOO=old" not in text


def test_set_env_values_quotes_values_with_spaces(tmp_path: Path):
    env = tmp_path / ".env"
    set_env_values(env, {"GREETING": "hello world"})
    text = env.read_text(encoding="utf-8")
    assert 'GREETING="hello world"' in text


def test_set_env_values_no_op_when_unchanged(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("FOO=hello\n", encoding="utf-8")
    mtime_before = env.stat().st_mtime_ns
    changed = set_env_values(env, {"FOO": "hello"})
    assert changed == []
    # The file may or may not be rewritten; either way the parsed value
    # is correct and nothing was reported as changed.
    assert parse_env_file(env) == {"FOO": "hello"}
    # In practice we leave the file alone when nothing changed.
    assert env.stat().st_mtime_ns == mtime_before
