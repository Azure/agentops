"""Tests for the noisy-dependency log filter."""

from __future__ import annotations

import logging
from typing import List

import pytest

from agentops.utils.logging import setup_logging


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture(autouse=True)
def _restore_logging():
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    for name in ("httpx", "openai", "azure", "httpcore", "urllib3"):
        logger = logging.getLogger(name)
        logger.filters.clear()
        logger.setLevel(logging.NOTSET)


def _attach() -> _Capture:
    capture = _Capture()
    root = logging.getLogger()
    root.addHandler(capture)
    root.setLevel(logging.DEBUG)
    return capture


def test_noisy_info_dropped_even_after_basic_config_force() -> None:
    """A third-party ``basicConfig(force=True)`` must not resurrect the noise.

    ``force=True`` removes the root handlers we filtered, and any import can
    reset ``httpx``'s level back to INFO. The filter lives on the logger
    itself so neither can undo it.
    """
    setup_logging(verbose=False)
    logging.basicConfig(level=logging.INFO, force=True)
    logging.getLogger("httpx").setLevel(logging.INFO)
    capture = _attach()

    logging.getLogger("httpx").info("HTTP Request: GET https://x 200 OK")
    logging.getLogger("openai").info("noise")

    assert capture.messages == []


def test_warnings_and_own_logs_still_pass() -> None:
    setup_logging(verbose=False)
    capture = _attach()

    logging.getLogger("httpx").warning("real problem")
    logging.getLogger("agentops.demo").info("our own message")

    assert capture.messages == ["real problem", "our own message"]


def test_verbose_keeps_everything() -> None:
    setup_logging(verbose=True)
    capture = _attach()

    logging.getLogger("httpx").info("HTTP Request: GET https://x 200 OK")

    assert capture.messages == ["HTTP Request: GET https://x 200 OK"]
