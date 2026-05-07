"""Tiny ANSI color helpers for CLI progress output.

Colors are automatically disabled when stdout is not a TTY, when the
``NO_COLOR`` environment variable is set (https://no-color.org/), or when
``AGENTOPS_NO_COLOR`` is set. No emojis, no extended unicode — only plain
ASCII text wrapped in standard ANSI SGR escape codes that all modern
terminals (Windows Terminal, ConEmu, VS Code, macOS, Linux) understand.
"""

from __future__ import annotations

import os
import sys

_RESET = "\x1b[0m"
_CODES = {
    "dim": "\x1b[2m",
    "bold": "\x1b[1m",
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "blue": "\x1b[34m",
    "magenta": "\x1b[35m",
    "cyan": "\x1b[36m",
}


def _enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("AGENTOPS_NO_COLOR"):
        return False
    stream = sys.stdout
    try:
        return bool(stream.isatty())
    except Exception:  # noqa: BLE001
        return False


def style(text: str, *names: str) -> str:
    """Wrap ``text`` in the given ANSI styles (e.g. ``"green"``, ``"bold"``)."""
    if not _enabled() or not names:
        return text
    prefix = "".join(_CODES.get(name, "") for name in names)
    if not prefix:
        return text
    return f"{prefix}{text}{_RESET}"
