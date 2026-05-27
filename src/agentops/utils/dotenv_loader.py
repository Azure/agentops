"""Lightweight ``.env`` loader for the AgentOps workspace.

AgentOps auto-loads the workspace env file at startup so contributors do not
have to ``export`` env vars in every shell session. New AgentOps-only
workspaces use ``.agentops/.env``; repositories that already have an active
``azd`` environment keep using ``.azure/<active-env>/.env``.

Lookup order (first file that contributes at least one new variable wins):

1. ``.azure/<active-env>/.env`` — when an azd environment is already active.
2. ``.agentops/.env`` — AgentOps-owned local env file.
3. ``./.env`` — project-root fallback for hand-managed setups.

Design choices:

* **No new dependency.** We parse the file ourselves — only
  ``KEY=VALUE`` lines, blank lines, and ``#`` comments. No shell escapes
  or variable interpolation. This keeps the loader tiny and predictable.
* **Never override.** Variables already present in ``os.environ`` win.
  The ``.env`` files are a fallback, not an override — that matches how
  azd, dotenv, and direnv all behave.
* **Silent on failure.** Missing or malformed files do not crash
  AgentOps; startup must remain fast and robust.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Tuple


def _candidate_paths(workspace: Path) -> Iterable[Path]:
    """Return the ordered list of ``.env`` files AgentOps will look at."""
    workspace = workspace.resolve()
    # Prefer the active azd environment when one is configured.
    try:
        from agentops.utils.azd_env import discover_azd_env  # noqa: PLC0415

        location = discover_azd_env(workspace)
        if location.found and location.env_path is not None:
            yield location.env_path
    except Exception:  # noqa: BLE001
        # Discovery is best-effort; never crash startup on a malformed
        # ``.azure/`` layout.
        pass
    # Legacy fallback: pre-azd-refactor workspaces.
    yield workspace / ".agentops" / ".env"
    # Also support a project-root .env so users who already keep secrets
    # there benefit from the same auto-load (without overwriting OS env).
    yield workspace / ".env"


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a ``KEY=VALUE`` file. Returns an empty dict on any error."""
    if not path.exists() or not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        # Strip surrounding matching quotes (single or double).
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


def load_workspace_dotenv(workspace: Path | None = None) -> Tuple[Path, int] | None:
    """Load the active workspace ``.env`` file into ``os.environ``.

    Tries the active azd environment file first, then AgentOps' local
    ``.agentops/.env``, then project-root ``.env``. Values already set in
    the process environment win — this loader only *adds* missing keys.
    Returns ``(path, count)`` for the file that contributed at least one new
    variable, or ``None`` when nothing was loaded.
    """
    base = (workspace or Path.cwd()).resolve()
    for path in _candidate_paths(base):
        parsed = parse_env_file(path)
        if not parsed:
            continue
        added = 0
        for key, value in parsed.items():
            if key in os.environ:
                continue
            os.environ[key] = value
            added += 1
        if added:
            return path, added
    return None
