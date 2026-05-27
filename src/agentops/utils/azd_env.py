"""azd environment integration for AgentOps.

AgentOps can share wizard-collected configuration with ``azd`` when a workspace
already has, or explicitly requests, an azd environment. Fresh AgentOps-only
workspaces use ``.agentops/.env`` instead. This module is the single source of
truth for:

* **Discovery** — find the active ``azd`` environment using the same rules
  Doctor already uses (``AZURE_ENV_NAME`` env var first, then
  ``.azure/config.json``'s ``defaultEnvironment``, then the single
  environment folder when only one exists).
* **Reading** — parse the ``.env`` file with the same lenient
  ``KEY=VALUE`` rules as our :mod:`agentops.utils.dotenv_loader`.
* **Writing** — line-preserving updates that keep comments, ordering, and
  untouched keys intact. Newly added keys go to the end of the file.
* **Bootstrap** — when the caller deliberately opts into azd, create the
  minimal layout (``.azure/<name>/.env`` + ``.azure/config.json`` +
  ``.azure/.gitignore``) so AgentOps never writes secrets to a git-tracked
  file.

Notes for reviewers:

* **No new dependencies.** The parser/writer here use the same micro-format
  rules as ``dotenv_loader.parse_env_file``.
* **azd-compatible, not azd-required.** If ``azd`` is installed we can call its
  CLI to mutate envs; if not, we fall back to direct line-preserving edits.
* **Secret safety.** Whenever we create the ``.azure/`` directory we also
  drop a ``.gitignore`` that excludes ``*/.env`` so the wizard cannot leak
  ``APPLICATIONINSIGHTS_CONNECTION_STRING`` into source control even if
  the user has not run ``azd init`` themselves.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@dataclass
class AzdEnvLocation:
    """Where (and how) an azd environment was discovered."""

    name: Optional[str]
    env_path: Optional[Path]
    azure_dir: Path
    status: str  # "ok" | "missing_env_file" | "ambiguous" | "not_found"
    reason: Optional[str] = None
    candidates: List[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.status == "ok" and self.env_path is not None


def discover_azd_env(workspace: Path) -> AzdEnvLocation:
    """Resolve which azd environment AgentOps should target.

    Order of precedence:

    1. ``AZURE_ENV_NAME`` from the process environment.
    2. ``defaultEnvironment`` from ``.azure/config.json``.
    3. The only sub-directory of ``.azure/`` that contains a ``.env`` file
       (when exactly one exists).

    Returns an :class:`AzdEnvLocation` describing what was found. The
    caller is expected to look at ``found`` / ``status`` to decide
    whether to prompt for bootstrap.
    """
    workspace = workspace.resolve()
    azure_dir = workspace / ".azure"

    if not azure_dir.is_dir():
        return AzdEnvLocation(
            name=None,
            env_path=None,
            azure_dir=azure_dir,
            status="not_found",
            reason="workspace has no .azure directory",
        )

    env_name: Optional[str] = os.environ.get("AZURE_ENV_NAME") or None
    config_path = azure_dir / "config.json"
    if not env_name and config_path.is_file():
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                candidate = raw.get("defaultEnvironment")
                if isinstance(candidate, str) and candidate.strip():
                    env_name = candidate.strip()
        except (json.JSONDecodeError, OSError):
            pass

    if not env_name:
        candidates = sorted(
            p.name for p in azure_dir.iterdir()
            if p.is_dir() and (p / ".env").is_file()
        )
        if len(candidates) == 1:
            env_name = candidates[0]
        elif candidates:
            return AzdEnvLocation(
                name=None,
                env_path=None,
                azure_dir=azure_dir,
                status="ambiguous",
                reason=(
                    "multiple azd environments found; set AZURE_ENV_NAME "
                    "or .azure/config.json defaultEnvironment"
                ),
                candidates=candidates,
            )

    if not env_name:
        return AzdEnvLocation(
            name=None,
            env_path=None,
            azure_dir=azure_dir,
            status="not_found",
            reason="no azd environment selected",
        )

    env_path = azure_dir / env_name / ".env"
    if not env_path.is_file():
        return AzdEnvLocation(
            name=env_name,
            env_path=env_path,
            azure_dir=azure_dir,
            status="missing_env_file",
            reason=f"{env_path} does not exist",
        )

    return AzdEnvLocation(
        name=env_name,
        env_path=env_path,
        azure_dir=azure_dir,
        status="ok",
    )


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def parse_env_file(path: Path) -> Dict[str, str]:
    """Parse a ``KEY=VALUE`` file. Returns ``{}`` on any error.

    Matches the lenient rules of ``dotenv_loader.parse_env_file``:
    skips blank lines and ``#`` comments, tolerates ``export`` prefixes,
    strips matching single or double quotes from values.
    """
    if not path.exists() or not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


_GITIGNORE_LINES = (
    "# Created by AgentOps to keep azd environment secrets out of git.",
    "# azd creates the same rule by default; this file is harmless when",
    "# both tools are used together.",
    "*/.env",
)


def ensure_azure_gitignore(azure_dir: Path) -> bool:
    """Write ``.azure/.gitignore`` so per-env ``.env`` files are excluded.

    Returns ``True`` when the file was created or augmented. Idempotent —
    if a usable ignore pattern is already present, returns ``False``.
    """
    azure_dir.mkdir(parents=True, exist_ok=True)
    gitignore = azure_dir / ".gitignore"
    needed = "*/.env"
    if gitignore.is_file():
        try:
            existing = gitignore.read_text(encoding="utf-8")
        except OSError:
            existing = ""
        stripped = {
            ln.strip() for ln in existing.splitlines() if ln.strip() and not ln.startswith("#")
        }
        if needed in stripped or ".env" in stripped or "**/.env" in stripped:
            return False
        with gitignore.open("a", encoding="utf-8") as fh:
            if not existing.endswith("\n"):
                fh.write("\n")
            fh.write(needed + "\n")
        return True
    gitignore.write_text("\n".join(_GITIGNORE_LINES) + "\n", encoding="utf-8")
    return True


def ensure_azd_env(
    workspace: Path,
    env_name: str,
    *,
    set_default: bool = True,
) -> AzdEnvLocation:
    """Create ``.azure/<env_name>/.env`` if missing and protect it from git.

    Idempotent. If the environment already exists, returns its current
    location. Otherwise it:

    1. Creates ``.azure/`` and ``.azure/<env_name>/``.
    2. Writes ``.azure/.gitignore`` so ``*/.env`` is excluded from git.
    3. Creates an empty ``.env`` with a single-line header.
    4. When ``set_default`` is ``True`` and ``.azure/config.json`` is
       absent or missing a ``defaultEnvironment``, writes that field.

    Returns the resulting :class:`AzdEnvLocation`.
    """
    workspace = workspace.resolve()
    azure_dir = workspace / ".azure"
    env_dir = azure_dir / env_name
    env_path = env_dir / ".env"

    env_dir.mkdir(parents=True, exist_ok=True)
    ensure_azure_gitignore(azure_dir)

    if not env_path.exists():
        env_path.write_text(
            "# Managed alongside azd. Run `agentops init` or `azd env set`\n"
            "# to update values here. Secrets in this file are git-ignored\n"
            "# via .azure/.gitignore.\n",
            encoding="utf-8",
        )

    if set_default:
        config_path = azure_dir / "config.json"
        config: Dict[str, object] = {}
        if config_path.is_file():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    config = data
            except (json.JSONDecodeError, OSError):
                config = {}
        if not isinstance(config.get("defaultEnvironment"), str) or not config["defaultEnvironment"]:
            config.setdefault("version", 1)
            config["defaultEnvironment"] = env_name
            config_path.write_text(
                json.dumps(config, indent=2) + "\n",
                encoding="utf-8",
            )

    return AzdEnvLocation(
        name=env_name,
        env_path=env_path,
        azure_dir=azure_dir,
        status="ok",
    )


def set_default_azd_env(workspace: Path, env_name: str) -> Path:
    """Force ``.azure/config.json``'s ``defaultEnvironment`` to ``env_name``.

    Unlike :func:`ensure_azd_env`, this *always* writes the field even if
    a different default was already configured. Use it when the user has
    explicitly named an azd environment (for example, ``--azd-env qa``)
    and that intent should override any previous default.

    Returns the path to ``.azure/config.json``.
    """
    workspace = workspace.resolve()
    azure_dir = workspace / ".azure"
    azure_dir.mkdir(parents=True, exist_ok=True)
    config_path = azure_dir / "config.json"
    config: Dict[str, object] = {}
    if config_path.is_file():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                config = data
        except (json.JSONDecodeError, OSError):
            config = {}
    config.setdefault("version", 1)
    config["defaultEnvironment"] = env_name
    config_path.write_text(
        json.dumps(config, indent=2) + "\n",
        encoding="utf-8",
    )
    return config_path


# ---------------------------------------------------------------------------
# Writing — line-preserving
# ---------------------------------------------------------------------------


def set_env_values(env_path: Path, updates: Dict[str, str]) -> List[str]:
    """Update the ``.env`` file with ``updates`` (line-preserving).

    For each key in ``updates``:

    * If the file already contains a non-commented assignment for that
      key, rewrite the matching line in place — preserving comments and
      ordering for every other line.
    * Otherwise, append ``KEY=VALUE`` to the end of the file.

    Returns the list of keys that were actually written (i.e. the value
    changed compared to what was already on disk).
    """
    env_path.parent.mkdir(parents=True, exist_ok=True)

    if env_path.exists():
        try:
            text = env_path.read_text(encoding="utf-8")
        except OSError:
            text = ""
    else:
        text = ""
    lines = text.splitlines()
    trailing_newline = text.endswith("\n") if text else True

    current = parse_env_file(env_path) if env_path.exists() else {}

    changed: List[str] = []
    remaining_updates = dict(updates)

    for idx, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key_part = stripped[len("export "):] if stripped.startswith("export ") else stripped
        if "=" not in key_part:
            continue
        key = key_part.split("=", 1)[0].strip()
        if key in remaining_updates:
            new_value = remaining_updates.pop(key)
            if current.get(key) != new_value:
                lines[idx] = f"{key}={_format_value(new_value)}"
                changed.append(key)
            # If unchanged we still want to skip the append-fallback below,
            # so we leave the line untouched and remove the key.

    for key, new_value in remaining_updates.items():
        # Key was not present in the file — append it.
        if current.get(key) == new_value:
            # Nothing to do; ``current`` reflects what parse_env_file
            # would see, so this means the value really matches.
            continue
        lines.append(f"{key}={_format_value(new_value)}")
        changed.append(key)

    if changed or not env_path.exists():
        new_text = "\n".join(lines)
        if trailing_newline or not new_text.endswith("\n"):
            new_text += "\n"
        env_path.write_text(new_text, encoding="utf-8")

    return changed


def _format_value(value: str) -> str:
    if value == "":
        return ""
    if any(ch in value for ch in (" ", "#", "=", "\t")):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


# ---------------------------------------------------------------------------
# azd CLI integration (optional preferred path)
# ---------------------------------------------------------------------------


def azd_cli_available() -> bool:
    """Return ``True`` when the ``azd`` CLI is on ``PATH``.

    On Windows the binary may be ``azd.cmd``; ``shutil.which`` handles
    both via ``PATHEXT``.
    """
    return shutil.which("azd") is not None


def azd_env_set(workspace: Path, env_name: str, key: str, value: str) -> Tuple[bool, str]:
    """Call ``azd env set KEY VALUE`` for the given workspace.

    Returns ``(success, message)``. The wizard prefers this path when the
    azd CLI is installed because it gives azd a chance to apply its own
    formatting and triggers any update hooks. We fall back to direct file
    edits via :func:`set_env_values` when azd is not available or fails.
    """
    if not azd_cli_available():
        return False, "azd CLI not found on PATH"
    try:
        result = subprocess.run(  # noqa: S603,S607
            ["azd", "env", "set", key, value, "-e", env_name],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"azd env set failed to launch: {exc}"
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "azd env set failed").strip()
    return True, ""
