"""Unit tests for callable adapter support in LocalAdapterBackend."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agentops.backends.local_adapter_backend import _load_callable


def test_load_callable_resolves_valid_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Write a small callable module in a temp dir and import from there.
    (tmp_path / "echo_adapter.py").write_text(
        "def echo(input_text: str, context: dict) -> dict:\n"
        '    return {"response": input_text}\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    fn = _load_callable("echo_adapter:echo")
    assert callable(fn)
    result = fn("hello", {"input": "hello"})
    assert result == {"response": "hello"}


def test_load_callable_bad_module() -> None:
    with pytest.raises(ValueError, match="Could not import module"):
        _load_callable("nonexistent_module_xyz:func")


def test_load_callable_bad_function(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "echo_adapter2.py").write_text(
        "def echo(input_text, context):\n    return {}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="has no function"):
        _load_callable("echo_adapter2:nonexistent_function")


def test_load_callable_non_callable() -> None:
    # json module has a constant we can use — __name__ is a str, not callable
    with pytest.raises(ValueError, match="non-callable"):
        _load_callable("json:__file__")


def test_load_callable_from_agentops_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify _load_callable can import a module placed inside .agentops/ directory."""
    # Create a .agentops/ directory with a callable module
    agentops_dir = tmp_path / ".agentops"
    agentops_dir.mkdir()
    adapter_file = agentops_dir / "my_test_adapter_in_agentops.py"
    adapter_file.write_text(
        "def run_evaluation(input_text, context):\n"
        "    return {'response': 'from-agentops-dir'}\n",
        encoding="utf-8",
    )

    # Change cwd to tmp_path (the project root) and clean sys.path / modules
    monkeypatch.chdir(tmp_path)
    original_path = sys.path.copy()
    # Remove any stale entries that might interfere
    monkeypatch.setattr("sys.path", [p for p in sys.path if str(tmp_path) not in p])

    try:
        fn = _load_callable("my_test_adapter_in_agentops:run_evaluation")
        assert callable(fn)
        result = fn("test", {})
        assert result == {"response": "from-agentops-dir"}
    finally:
        # Clean up imported module
        sys.modules.pop("my_test_adapter_in_agentops", None)


def test_load_callable_error_message_mentions_agentops_dir() -> None:
    """Verify the error message mentions .agentops/ as a valid location."""
    with pytest.raises(ValueError, match=r"\.agentops/"):
        _load_callable("nonexistent_module_xyz:func")
