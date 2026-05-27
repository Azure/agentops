"""Tests for :mod:`agentops.utils.dotenv_loader`."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentops.utils.dotenv_loader import load_workspace_dotenv, parse_env_file


def _seed_azd_env(workspace: Path, env_name: str, content: str) -> Path:
    env_dir = workspace / ".azure" / env_name
    env_dir.mkdir(parents=True)
    env_path = env_dir / ".env"
    env_path.write_text(content, encoding="utf-8")
    (workspace / ".azure" / "config.json").write_text(
        json.dumps({"version": 1, "defaultEnvironment": env_name}) + "\n",
        encoding="utf-8",
    )
    return env_path


def test_parse_env_file_handles_quotes_and_comments(tmp_path: Path):
    f = tmp_path / ".env"
    f.write_text(
        "# this is a comment\n"
        "\n"
        "PLAIN=hello\n"
        'QUOTED_DOUBLE="value with spaces"\n'
        "QUOTED_SINGLE='single quotes'\n"
        "WITH_EQUALS=key=value;more=stuff\n"
        "export EXPORTED=ok\n",
        encoding="utf-8",
    )
    parsed = parse_env_file(f)
    assert parsed["PLAIN"] == "hello"
    assert parsed["QUOTED_DOUBLE"] == "value with spaces"
    assert parsed["QUOTED_SINGLE"] == "single quotes"
    assert parsed["WITH_EQUALS"] == "key=value;more=stuff"
    assert parsed["EXPORTED"] == "ok"


def test_parse_env_file_missing_returns_empty(tmp_path: Path):
    assert parse_env_file(tmp_path / "does-not-exist") == {}


def test_parse_env_file_ignores_malformed_lines(tmp_path: Path):
    f = tmp_path / ".env"
    f.write_text(
        "this has no equals sign\n"
        " = no_key\n"
        "VALID=ok\n",
        encoding="utf-8",
    )
    parsed = parse_env_file(f)
    assert parsed == {"VALID": "ok"}


def test_loader_skips_existing_env_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Process env wins. Loader must NOT overwrite existing variables."""
    monkeypatch.setenv("EXISTING", "from-shell")
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    _seed_azd_env(tmp_path, "dev", "EXISTING=from-file\nNEW_ONE=from-file\n")
    load_workspace_dotenv(tmp_path)
    assert os.environ["EXISTING"] == "from-shell"
    assert os.environ["NEW_ONE"] == "from-file"


def test_loader_prefers_active_azd_env_over_agentops_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The active azd env file wins when the workspace has opted into azd."""
    monkeypatch.delenv("AGENTOPS_X", raising=False)
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    _seed_azd_env(tmp_path, "dev", "AGENTOPS_X=from-azd\n")
    (tmp_path / ".agentops").mkdir()
    (tmp_path / ".agentops" / ".env").write_text(
        "AGENTOPS_X=from-legacy\n", encoding="utf-8"
    )
    loaded = load_workspace_dotenv(tmp_path)
    assert loaded is not None
    assert os.environ["AGENTOPS_X"] == "from-azd"


def test_loader_falls_back_to_agentops_env_when_no_azd_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AGENTOPS_LEGACY_X", raising=False)
    (tmp_path / ".agentops").mkdir()
    (tmp_path / ".agentops" / ".env").write_text(
        "AGENTOPS_LEGACY_X=from-legacy\n", encoding="utf-8"
    )
    loaded = load_workspace_dotenv(tmp_path)
    assert loaded is not None
    assert os.environ["AGENTOPS_LEGACY_X"] == "from-legacy"


def test_loader_returns_none_when_nothing_to_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    assert load_workspace_dotenv(tmp_path) is None


def test_loader_honours_azure_env_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """AZURE_ENV_NAME selects the env over .azure/config.json defaultEnvironment."""
    monkeypatch.delenv("AGENTOPS_PICK", raising=False)
    _seed_azd_env(tmp_path, "dev", "AGENTOPS_PICK=dev-value\n")
    # Also create a prod env that should be ignored.
    (tmp_path / ".azure" / "prod").mkdir()
    (tmp_path / ".azure" / "prod" / ".env").write_text(
        "AGENTOPS_PICK=prod-value\n", encoding="utf-8"
    )
    monkeypatch.setenv("AZURE_ENV_NAME", "prod")
    load_workspace_dotenv(tmp_path)
    assert os.environ["AGENTOPS_PICK"] == "prod-value"
