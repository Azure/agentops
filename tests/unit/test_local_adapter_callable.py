"""Unit tests for callable adapter support in LocalAdapterBackend."""
from __future__ import annotations

import pytest

from agentops.backends.local_adapter_backend import _load_callable


def test_load_callable_resolves_valid_path() -> None:
    fn = _load_callable("tests.fixtures.fake_adapter:main_callable")
    assert callable(fn)
    result = fn("hello", {"input": "hello"})
    assert result == {"response": "hello"}


def test_load_callable_bad_module() -> None:
    with pytest.raises(ValueError, match="Could not import module"):
        _load_callable("nonexistent_module_xyz:func")


def test_load_callable_bad_function() -> None:
    with pytest.raises(ValueError, match="has no function"):
        _load_callable("tests.fixtures.fake_adapter:nonexistent_function")


def test_load_callable_non_callable() -> None:
    # json module has a constant we can use — __name__ is a str, not callable
    with pytest.raises(ValueError, match="non-callable"):
        _load_callable("json:__file__")
