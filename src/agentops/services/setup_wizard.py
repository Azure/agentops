"""Interactive setup wizard for AgentOps (``agentops init``).

The wizard asks the user one question at a time for the values AgentOps
needs to evaluate, observe, and analyze a Foundry agent — the project
endpoint, the agent identifier, and the dataset path.

Storage model (azd-first):

* ``agent`` and ``dataset`` are declarative project config and stay in
  ``agentops.yaml``. They are version-controlled and rarely change
  between environments.
* ``AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`` is environment-specific and lands
  in ``.azure/<active-env>/.env`` — the same file ``azd`` uses, so Doctor,
  the Cockpit, and ``agentops eval run`` all see one source of truth. The
  file is git-ignored via ``.azure/.gitignore``.
* ``APPLICATIONINSIGHTS_CONNECTION_STRING`` can still be saved to the same
  env file when supplied non-interactively, but the interactive wizard does
  not ask for it; runtime commands can discover it from the Foundry project
  later.
* Canonical Azure variable names are preserved so the Azure SDKs and
  ``azd`` templates can read them directly.

The design intentionally mirrors ``azd``: simple sequential prompts, each
showing the *current* effective value as the default, with empty-input
meaning "keep current". A non-TTY environment (CI, redirected stdin)
falls back to a clear error so the wizard never hangs in pipelines.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Collection, List, Optional


# ---------------------------------------------------------------------------
# Question prompts
# ---------------------------------------------------------------------------

PROJECT_ENDPOINT_TITLE = "Foundry project endpoint"
PROJECT_ENDPOINT_HELP = (
    "The HTTPS URL of your Microsoft Foundry project. Used by `agentops eval "
    "run`, `agentops doctor`, and the cockpit to discover the workspace.\n"
    "Example: https://acct.services.ai.azure.com/api/projects/proj-default"
)

AGENT_TITLE = "Agent (name:version, model:deployment, or URL)"
AGENT_HELP = (
    "What you are evaluating. One of:\n"
    "  * <name>:<version> — Foundry prompt agent (e.g. quickstart-agent:2)\n"
    "  * model:<deployment> — Foundry model deployment\n"
    "  * https://... — a Foundry hosted endpoint or any HTTP/JSON agent"
)

DATASET_TITLE = "Dataset path (JSONL file with `input` / `expected` rows)"
DATASET_HELP = (
    "Path to the JSONL dataset, relative to the project root.\n"
    "Default: .agentops/data/smoke.jsonl"
)

# Canonical environment-variable names AgentOps reads. We never rename
# variables that the Azure SDKs and azd templates expect — only AgentOps-
# specific knobs get the ``AGENTOPS_`` prefix.
ENV_KEY_PROJECT_ENDPOINT = "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT"
ENV_KEY_APPINSIGHTS = "APPLICATIONINSIGHTS_CONNECTION_STRING"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class WizardAnswers:
    """User answers collected by the wizard."""

    project_endpoint: Optional[str] = None
    agent: Optional[str] = None
    dataset: Optional[str] = None
    appinsights_connection_string: Optional[str] = None


@dataclass
class WizardResult:
    """What changed on disk after running the wizard."""

    yaml_path: Path
    env_path: Optional[Path]
    yaml_updated: bool = False
    env_updated: bool = False
    yaml_fields: List[str] = field(default_factory=list)
    env_keys: List[str] = field(default_factory=list)
    azd_env_name: Optional[str] = None
    azd_env_created: bool = False


# ---------------------------------------------------------------------------
# Defaults discovery
# ---------------------------------------------------------------------------


def discover_defaults(workspace: Path) -> WizardAnswers:
    """Read existing values from agentops.yaml + azd env + process env.

    Returns the *current effective values* the wizard should pre-fill as
    defaults. Empty fields mean "no current value, ask the user fresh".
    """
    workspace = workspace.resolve()
    yaml_data = _read_agentops_yaml(workspace)
    env_values = _read_active_env(workspace)

    project_endpoint = (
        env_values.get(ENV_KEY_PROJECT_ENDPOINT)
        or os.environ.get(ENV_KEY_PROJECT_ENDPOINT)
        or _as_str(yaml_data.get("project_endpoint"))
    )
    agent = _as_str(yaml_data.get("agent"))
    dataset = _as_str(yaml_data.get("dataset"))
    appinsights = (
        env_values.get(ENV_KEY_APPINSIGHTS)
        or os.environ.get(ENV_KEY_APPINSIGHTS)
    )

    return WizardAnswers(
        project_endpoint=project_endpoint,
        agent=agent,
        dataset=dataset,
        appinsights_connection_string=appinsights,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


_URL_RE = re.compile(r"^https?://[^\s]+$")
_AGENT_REF_RE = re.compile(r"^[A-Za-z0-9._\-]+:[A-Za-z0-9._\-]+$")


def validate_project_endpoint(value: str) -> Optional[str]:
    """Return an error string if ``value`` is not a usable endpoint."""
    if not value:
        return None  # empty = skip
    if not _URL_RE.match(value):
        return "Project endpoint must start with https:// or http://."
    return None


def validate_agent(value: str) -> Optional[str]:
    if not value:
        return None
    if _URL_RE.match(value):
        return None
    if _AGENT_REF_RE.match(value):
        return None
    return (
        "Agent must be one of: <name>:<version>, model:<deployment>, or "
        "an https:// URL."
    )


def validate_dataset(value: str, workspace: Path) -> Optional[str]:
    if not value:
        return None
    candidate = (workspace / value).resolve()
    if not candidate.exists():
        return f"Dataset file does not exist: {candidate}"
    return None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def apply_answers(
    workspace: Path,
    answers: WizardAnswers,
    *,
    default_env_name: str = "dev",
    bootstrap_azd_env: bool = True,
) -> WizardResult:
    """Write the user's answers to ``agentops.yaml`` and the active env file.

    Behavior:

    * ``agent`` and ``dataset`` are persisted to ``agentops.yaml`` only.
    * ``project_endpoint`` and ``appinsights_connection_string`` are
      persisted as environment variables in ``.azure/<env>/.env`` (the
      active azd environment).
    * When no azd environment exists yet and ``bootstrap_azd_env`` is
      ``True`` (the default), one is created named ``default_env_name``
      so the wizard always has somewhere safe to write the secret.

    Only fields that the user actually provided (non-empty, non-``None``)
    are touched. Existing values not covered by an answer are preserved.
    """
    from agentops.utils.azd_env import (  # noqa: PLC0415
        AzdEnvLocation,
        discover_azd_env,
        ensure_azd_env,
        set_env_values,
    )

    workspace = workspace.resolve()
    yaml_path = workspace / "agentops.yaml"
    result = WizardResult(yaml_path=yaml_path, env_path=None)

    # --- agentops.yaml --------------------------------------------------
    yaml_data = _read_agentops_yaml(workspace)

    def _changed(field_name: str, new_value: Optional[str]) -> bool:
        if new_value is None:
            return False
        current = _as_str(yaml_data.get(field_name))
        return current != new_value

    yaml_dirty = False
    if _changed("agent", answers.agent):
        yaml_data["agent"] = answers.agent
        result.yaml_fields.append("agent")
        yaml_dirty = True
    if _changed("dataset", answers.dataset):
        yaml_data["dataset"] = answers.dataset
        result.yaml_fields.append("dataset")
        yaml_dirty = True

    if yaml_dirty:
        if "version" not in yaml_data:
            yaml_data["version"] = 1
        _write_agentops_yaml(yaml_path, yaml_data)
        result.yaml_updated = True

    # --- .azure/<env>/.env ---------------------------------------------
    env_updates: dict[str, str] = {}
    if answers.project_endpoint is not None:
        env_updates[ENV_KEY_PROJECT_ENDPOINT] = answers.project_endpoint
    if answers.appinsights_connection_string is not None:
        env_updates[ENV_KEY_APPINSIGHTS] = answers.appinsights_connection_string

    if not env_updates:
        return result

    location: AzdEnvLocation = discover_azd_env(workspace)
    if not location.found:
        if not bootstrap_azd_env:
            return result
        if location.status == "ambiguous":
            raise RuntimeError(
                "Multiple azd environments found but no default is set. "
                "Set AZURE_ENV_NAME or write defaultEnvironment to "
                ".azure/config.json, then re-run `agentops init`."
            )
        env_name = location.name or default_env_name
        location = ensure_azd_env(workspace, env_name)
        result.azd_env_created = True

    assert location.env_path is not None  # narrowing for type checkers
    result.env_path = location.env_path
    result.azd_env_name = location.name

    changed_keys = set_env_values(location.env_path, env_updates)
    if changed_keys:
        result.env_updated = True
        result.env_keys.extend(sorted(changed_keys))

    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _as_str(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _read_agentops_yaml(workspace: Path) -> dict:
    path = workspace / "agentops.yaml"
    if not path.exists():
        return {}
    try:
        import yaml  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _write_agentops_yaml(path: Path, data: dict) -> None:
    import yaml  # noqa: PLC0415

    path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve simple field order for readability: version, agent, dataset,
    # project_endpoint (legacy, only kept if already present), then
    # everything else.
    ordered_keys = ["version", "agent", "dataset", "project_endpoint"]
    ordered: dict = {}
    for key in ordered_keys:
        if key in data:
            ordered[key] = data[key]
    for key, value in data.items():
        if key not in ordered:
            ordered[key] = value
    path.write_text(
        yaml.safe_dump(ordered, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def _read_active_env(workspace: Path) -> dict[str, str]:
    """Read the active env file (azd env first, then legacy locations)."""
    from agentops.utils.azd_env import discover_azd_env, parse_env_file  # noqa: PLC0415

    location = discover_azd_env(workspace)
    if location.found and location.env_path is not None:
        return parse_env_file(location.env_path)
    # Legacy compat — still read .agentops/.env so users mid-migration
    # see their old values as defaults.
    legacy = workspace / ".agentops" / ".env"
    if legacy.is_file():
        return parse_env_file(legacy)
    return {}


# ---------------------------------------------------------------------------
# Prompt loop (Typer-friendly)
# ---------------------------------------------------------------------------


PromptFn = Callable[[str, Optional[str]], str]
OnAnswerFn = Callable[[str, str], None]


def _mask_secret(value: str) -> str:
    """Show only the tail of a secret so the user can recognise it without leaking."""
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return "•" * 8 + value[-4:]


def _can_encode(text: str) -> bool:
    """Return True if the active stdout encoding can render ``text``.

    Used to choose between Unicode glyphs (✓, •) and ASCII fallbacks (*, .)
    so the wizard does not crash on legacy Windows code pages (cp1252).
    """
    import sys  # noqa: PLC0415

    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def run_wizard(
    workspace: Path,
    prompt: PromptFn,
    echo: Callable[[str], None],
    *,
    on_answer: Optional[OnAnswerFn] = None,
    reconfigure: bool = False,
    force_prompt_fields: Optional[Collection[str]] = None,
) -> WizardAnswers:
    """Drive the interactive question loop.

    ``prompt`` is called as ``prompt(question, default)`` and must return
    the user's answer (empty string = keep current). ``echo`` prints
    explanatory text between questions. Both are injected so the function
    is unit-testable without touching the real terminal.

    ``on_answer`` is invoked as ``on_answer(field_name, value)`` after
    each new (non-empty, changed, validated) answer. The CLI uses it to
    persist values to disk immediately, so a Ctrl+C mid-wizard does not
    discard answers the user already provided.

    When ``reconfigure`` is ``False`` (the default), any value that is
    already configured — read from ``agentops.yaml``, the active azd
    environment, or the process env — is reused silently with a single
    confirmation line. Set ``reconfigure=True`` to force the wizard to
    re-ask every question even when defaults are present.

    ``force_prompt_fields`` is narrower than ``reconfigure``: it re-asks only
    selected fields while still reusing other existing defaults. The CLI uses
    this on a first interactive run so starter ``agentops.yaml`` values remain
    visible defaults instead of being accepted as real user choices.
    """
    defaults = discover_defaults(workspace)
    answers = WizardAnswers()
    skipped: list[str] = []
    forced_fields = set(force_prompt_fields or ())
    unicode_ok = _can_encode("✓•")
    ok_glyph = "✓" if unicode_ok else "*"

    def _should_prompt(field_name: str, value: Optional[str]) -> bool:
        return reconfigure or field_name in forced_fields or not value

    def _persist(field_name: str, value: str) -> None:
        if on_answer is not None:
            try:
                on_answer(field_name, value)
            except Exception as exc:  # noqa: BLE001
                echo(f"  ! could not persist {field_name}: {exc}")

    def _confirm_existing(label: str, value: str, secret: bool = False) -> None:
        """Acknowledge a pre-existing value without re-prompting."""
        display = _mask_secret(value) if secret else value
        if not unicode_ok and secret:
            # Fall back to plain bullets so cp1252 stdouts do not crash.
            display = "*" * 8 + value[-4:] if len(value) > 8 else "*" * len(value)
        echo(f"  {ok_glyph} {label}: {display}")

    # 1) Foundry project endpoint
    if not _should_prompt("project_endpoint", defaults.project_endpoint):
        _confirm_existing(PROJECT_ENDPOINT_TITLE, defaults.project_endpoint or "")
        skipped.append("project_endpoint")
    else:
        echo("")
        echo(PROJECT_ENDPOINT_TITLE)
        echo(_indent(PROJECT_ENDPOINT_HELP))
        while True:
            raw = prompt("Foundry project endpoint", defaults.project_endpoint)
            value = raw.strip()
            if not value:
                break  # keep current / leave blank
            err = validate_project_endpoint(value)
            if err:
                echo("  ! " + err)
                continue
            if value != (defaults.project_endpoint or ""):
                answers.project_endpoint = value
                _persist("project_endpoint", value)
            break

    # 2) Agent
    if not _should_prompt("agent", defaults.agent):
        _confirm_existing(AGENT_TITLE, defaults.agent or "")
        skipped.append("agent")
    else:
        echo("")
        echo(AGENT_TITLE)
        echo(_indent(AGENT_HELP))
        while True:
            raw = prompt("Agent", defaults.agent)
            value = raw.strip()
            if not value:
                break
            err = validate_agent(value)
            if err:
                echo("  ! " + err)
                continue
            if value != (defaults.agent or ""):
                answers.agent = value
                _persist("agent", value)
            break

    # 3) Dataset
    if not _should_prompt("dataset", defaults.dataset):
        _confirm_existing(DATASET_TITLE, defaults.dataset or "")
        skipped.append("dataset")
    else:
        echo("")
        echo(DATASET_TITLE)
        echo(_indent(DATASET_HELP))
        while True:
            raw = prompt("Dataset path", defaults.dataset or ".agentops/data/smoke.jsonl")
            value = raw.strip()
            if not value:
                break
            err = validate_dataset(value, workspace)
            if err:
                echo("  ! " + err)
                continue
            if value != (defaults.dataset or ""):
                answers.dataset = value
                _persist("dataset", value)
            break

    # Surface a hint only when EVERY managed value was already set, so the
    # user knows how to edit values without thinking the wizard "did nothing".
    expected = ["project_endpoint", "agent", "dataset"]
    if not reconfigure and set(skipped) == set(expected):
        echo("")
        echo("All values already configured. Re-run with --reconfigure to change them.")

    return answers


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


# ---------------------------------------------------------------------------
# `agentops init show` — inspect the current setup
# ---------------------------------------------------------------------------


@dataclass
class SetupSnapshotVar:
    """One row in the ``agentops init show`` output."""

    key: str
    value: Optional[str]
    source: str  # "azd-env" | "process-env" | "agentops.yaml" | "default" | "not set"
    secret: bool = False
    required: bool = False
    description: str = ""


@dataclass
class SetupSnapshot:
    """The full ``agentops init show`` payload."""

    workspace: Path
    azd_env_name: Optional[str]
    azd_env_path: Optional[Path]
    azd_status: str
    azd_reason: Optional[str]
    yaml_path: Path
    yaml_present: bool
    yaml_agent: Optional[str]
    yaml_dataset: Optional[str]
    yaml_project_endpoint: Optional[str]
    variables: List[SetupSnapshotVar] = field(default_factory=list)
    legacy_env_path: Optional[Path] = None

    @property
    def missing_required(self) -> List[str]:
        return [v.key for v in self.variables if v.required and not v.value]


# Registry of variables the wizard manages, used by `setup show`.
# Order matters: this is how they show up in the report.
_MANAGED_VARS: tuple[tuple[str, bool, bool, str], ...] = (
    (
        ENV_KEY_PROJECT_ENDPOINT,
        False,
        True,
        "Foundry project endpoint used by Doctor, Cockpit, and eval run.",
    ),
    (
        ENV_KEY_APPINSIGHTS,
        True,
        False,
        "Application Insights connection string for tracing and Cockpit telemetry.",
    ),
    (
        "AGENTOPS_FOUNDRY_MODE",
        False,
        False,
        "Foundry execution mode (`cloud` or `local`). AgentOps-specific.",
    ),
)


def collect_snapshot(workspace: Path) -> SetupSnapshot:
    """Snapshot the current AgentOps configuration for display."""
    from agentops.utils.azd_env import discover_azd_env, parse_env_file  # noqa: PLC0415

    workspace = workspace.resolve()
    yaml_data = _read_agentops_yaml(workspace)
    yaml_path = workspace / "agentops.yaml"

    location = discover_azd_env(workspace)
    env_values: dict[str, str] = {}
    if location.found and location.env_path is not None:
        env_values = parse_env_file(location.env_path)

    legacy = workspace / ".agentops" / ".env"
    legacy_env_path: Optional[Path] = legacy if legacy.is_file() else None
    legacy_values = parse_env_file(legacy) if legacy_env_path else {}

    variables: List[SetupSnapshotVar] = []
    for key, is_secret, is_required, description in _MANAGED_VARS:
        proc_value = os.environ.get(key)
        env_value = env_values.get(key) or legacy_values.get(key)
        # Process env wins only when it actually differs from the file —
        # otherwise we attribute the value to the (more durable) env file.
        if proc_value is not None and proc_value != env_value:
            value, source = proc_value, "process-env"
        elif env_value:
            value, source = env_value, "azd-env" if env_values.get(key) else "legacy-.agentops/.env"
        elif proc_value:
            value, source = proc_value, "process-env"
        elif key == "AGENTOPS_FOUNDRY_MODE":
            value, source = "cloud", "default"
        else:
            value, source = None, "not set"
        variables.append(
            SetupSnapshotVar(
                key=key,
                value=value,
                source=source,
                secret=is_secret,
                required=is_required,
                description=description,
            )
        )

    return SetupSnapshot(
        workspace=workspace,
        azd_env_name=location.name,
        azd_env_path=location.env_path,
        azd_status=location.status,
        azd_reason=location.reason,
        yaml_path=yaml_path,
        yaml_present=yaml_path.exists(),
        yaml_agent=_as_str(yaml_data.get("agent")),
        yaml_dataset=_as_str(yaml_data.get("dataset")),
        yaml_project_endpoint=_as_str(yaml_data.get("project_endpoint")),
        variables=variables,
        legacy_env_path=legacy_env_path,
    )


def mask_secret(value: Optional[str]) -> str:
    """Return a UI-safe rendering of a secret value."""
    if not value:
        return "(not set)"
    if len(value) <= 8:
        return "***"
    return value[:4] + "***" + value[-4:]
