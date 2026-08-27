"""Interactive setup wizard for AgentOps (``agentops init``).

The wizard asks the user one question at a time for the values AgentOps
needs to evaluate, observe, and analyze a Foundry agent — the project
endpoint, the agent identifier, and the dataset path.

Storage model:

* ``agent`` and ``dataset`` are declarative project config and stay in
  ``agentops.yaml``. They are version-controlled and rarely change
  between environments.
* ``AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`` is environment-specific and lands
  in the AgentOps-owned ``.agentops/.env`` by default. If the workspace
  already has an active azd environment, or the user explicitly passes
  ``--azd-env``, AgentOps writes the same value to ``.azure/<env>/.env``
  instead.
* ``APPLICATIONINSIGHTS_CONNECTION_STRING`` can still be saved to the same
  selected env file when supplied non-interactively, but the interactive
  wizard does not ask for it; runtime commands can discover it from the
  Foundry project later.
* Canonical Azure variable names are preserved so the Azure SDKs and
  ``azd`` templates can read them directly.

The design intentionally mirrors ``azd``: simple sequential prompts, each
showing the *current* effective value as the default, with empty-input
meaning "keep current". A non-TTY environment (CI, redirected stdin)
falls back to a clear error so the wizard never hangs in pipelines.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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

# Legacy placeholder that older workspaces may still carry in agentops.yaml.
# The wizard NEVER writes this value again; it is only recognised on read so a
# pre-existing ``my-agent:1`` is treated as "no target configured" rather than a
# real evaluation target. See :func:`is_placeholder_agent`.
AGENT_PLACEHOLDER_VALUE = "my-agent:1"
AZ_CLI_DISCOVERY_TIMEOUT_SECONDS = 5

AGENT_TITLE = "Evaluation target (optional)"
AGENT_HELP = (
    "What you want to evaluate. This is OPTIONAL — choose 'Configure later'\n"
    "to run project observability only (Doctor, Cockpit, Observe) with no\n"
    "target committed. Pick a target kind:\n"
    "  1) Foundry prompt agent   — <name>:<version> (e.g. quickstart-agent:2)\n"
    "  2) Foundry hosted agent    — https://<res>.services.ai.azure.com/"
    "api/projects/<proj>/agents/<name>\n"
    "  3) Foundry model deploy    — model:<deployment> (e.g. model:gpt-4.1)\n"
    "  4) External HTTP agent     — any https:// endpoint (ACA, AKS, "
    "LangGraph, custom)\n"
    "  5) Configure later         — project observability only, no target"
)

# Numeric menu keys for the guided target-kind choice.
AGENT_CHOICE_PROMPT = "1"
AGENT_CHOICE_HOSTED_URL = "2"
AGENT_CHOICE_MODEL = "3"
AGENT_CHOICE_HTTP = "4"
AGENT_CHOICE_LATER = "5"

AGENT_LATER_CONFIRMATION = (
    "Project observability only — no evaluation target will be written to "
    "agentops.yaml. Doctor, Cockpit, and Observe work without one; run "
    "`agentops init` again (or set `agent:` in agentops.yaml) when you are "
    "ready to evaluate."
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

REQUIRED_CONFIGURATION_MESSAGE = (
    "AgentOps needs a Foundry project endpoint and a dataset path before it can "
    "finish configuration. The evaluation target (agent) is optional — you can "
    "configure it later. Enter the missing value, or press Ctrl+C to cancel and "
    "re-run `agentops init` later."
)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


# Endpoint-default provenance — informational only, used by the wizard to
# decide whether to suppress a discovered default value because it likely
# belongs to a *different* environment than the one being configured.
ENDPOINT_SOURCE_AZD_ENV_FILE = "azd-env-file"
ENDPOINT_SOURCE_AGENTOPS_ENV_FILE = "agentops-env-file"
ENDPOINT_SOURCE_PROCESS_ENV = "process-env"
ENDPOINT_SOURCE_YAML_LEGACY = "yaml-legacy"
ENDPOINT_SOURCE_AZD_RESOURCE_DISCOVERY = "azd-resource-discovery"
ENDPOINT_SOURCE_NONE = "none"


@dataclass
class WizardAnswers:
    """User answers collected by the wizard.

    The ``project_endpoint_source`` / ``project_endpoint_source_path`` fields
    are populated by :func:`discover_defaults` to record *where* the default
    project endpoint came from. They are informational only — never persisted
    by :func:`apply_answers` — and the prompt loop in :func:`run_wizard` uses
    them to suppress cross-environment leak (for example, a sandbox endpoint
    exported in ``$AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`` bleeding into a
    fresh ``--azd-env dev`` setup).
    """

    project_endpoint: Optional[str] = None
    agent: Optional[str] = None
    protocol: Optional[str] = None
    dataset: Optional[str] = None
    appinsights_connection_string: Optional[str] = None
    project_endpoint_source: Optional[str] = None
    project_endpoint_source_path: Optional[Path] = None
    # Set by the "Configure later" wizard choice. When true, :func:`apply_answers`
    # strips a pre-existing legacy placeholder agent so the workspace is treated
    # as project-observability-only rather than keeping a fake target.
    clear_agent: bool = False


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

    The returned :class:`WizardAnswers` also carries provenance for
    ``project_endpoint`` via ``project_endpoint_source`` and
    ``project_endpoint_source_path`` so the prompt loop can detect and
    suppress cross-environment leaks (e.g. a shell-exported sandbox
    endpoint bleeding into a fresh ``--azd-env dev`` setup).
    """
    workspace = workspace.resolve()
    yaml_data = _read_agentops_yaml(workspace)
    env_values, env_source_path = _read_active_env_with_source(workspace)

    project_endpoint: Optional[str] = None
    endpoint_source: str = ENDPOINT_SOURCE_NONE
    endpoint_source_path: Optional[Path] = None

    env_value = env_values.get(ENV_KEY_PROJECT_ENDPOINT)
    if env_value:
        project_endpoint = env_value
        endpoint_source_path = env_source_path
        if env_source_path is not None and ".azure" in env_source_path.parts:
            endpoint_source = ENDPOINT_SOURCE_AZD_ENV_FILE
        else:
            endpoint_source = ENDPOINT_SOURCE_AGENTOPS_ENV_FILE
    else:
        proc_value = os.environ.get(ENV_KEY_PROJECT_ENDPOINT)
        if proc_value:
            project_endpoint = proc_value
            endpoint_source = ENDPOINT_SOURCE_PROCESS_ENV
        else:
            yaml_value = _as_str(yaml_data.get("project_endpoint"))
            if yaml_value:
                project_endpoint = yaml_value
                endpoint_source = ENDPOINT_SOURCE_YAML_LEGACY
            else:
                discovered_value = _discover_foundry_project_endpoint_from_azd_env(
                    workspace,
                    env_values,
                )
                if discovered_value:
                    project_endpoint = discovered_value
                    endpoint_source = ENDPOINT_SOURCE_AZD_RESOURCE_DISCOVERY
                    endpoint_source_path = env_source_path

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
        project_endpoint_source=endpoint_source,
        project_endpoint_source_path=endpoint_source_path,
    )


def is_placeholder_agent(value: Optional[str]) -> bool:
    return (value or "").strip().lower() == AGENT_PLACEHOLDER_VALUE


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


_URL_RE = re.compile(r"^https?://[^\s]+$")
_AGENT_REF_RE = re.compile(r"^[A-Za-z0-9._\-]+:[A-Za-z0-9._\-]+$")
_MODEL_REF_RE = re.compile(r"^model:[A-Za-z0-9._\-]+$")

# Foundry portal / browser hosts. A URL rooted at one of these is something a
# user copied from the Foundry web experience, not an addressable agent
# endpoint. Rejected with specific guidance instead of silently accepted.
_FOUNDRY_PORTAL_HOSTS = ("ai.azure.com", "ml.azure.com")

# A Foundry data-plane host (project/agent endpoints live here).
_FOUNDRY_SERVICES_HOST_SUFFIX = ".services.ai.azure.com"


def validate_project_endpoint(value: str) -> Optional[str]:
    """Return an error string if ``value`` is not a usable endpoint."""
    if not value:
        return None  # empty = skip
    if not _URL_RE.match(value):
        return "Project endpoint must start with https:// or http://."
    return None


def _url_host(value: str) -> str:
    """Lower-cased hostname (no port) for ``value``; '' when unparseable."""
    from urllib.parse import urlparse  # noqa: PLC0415

    try:
        netloc = urlparse(value).netloc.lower()
    except Exception:  # noqa: BLE001
        return ""
    return netloc.split("@")[-1].split(":")[0]


def _url_path(value: str) -> str:
    from urllib.parse import urlparse  # noqa: PLC0415

    try:
        return urlparse(value).path
    except Exception:  # noqa: BLE001
        return ""


def classify_agent_url_problem(value: str) -> Optional[str]:
    """Return a specific rejection message when ``value`` is a mis-pasted URL.

    Catches the two most common mistakes users make when pasting an agent:

    * a Foundry **project endpoint** (``.../api/projects/<proj>`` with no
      ``/agents/`` segment) — that belongs in ``project_endpoint``, not
      ``agent``; and
    * a Foundry **portal / browser** URL (``https://ai.azure.com/...``) — that
      is a web page, not an addressable endpoint.

    Returns ``None`` when the URL is a plausible agent endpoint.
    """
    host = _url_host(value)
    if not host:
        return None
    if host in _FOUNDRY_PORTAL_HOSTS or host.endswith("." + _FOUNDRY_PORTAL_HOSTS[0]):
        return (
            "That looks like a Foundry portal/browser URL "
            f"({host}), not an agent endpoint. Open your agent in Foundry, copy "
            "its hosted endpoint URL (it contains '/agents/<name>'), or use a "
            "<name>:<version> reference instead."
        )
    path = _url_path(value).lower()
    if host.endswith(_FOUNDRY_SERVICES_HOST_SUFFIX) and "/agents/" not in path:
        return (
            "That looks like a Foundry project endpoint, not an agent. It has no "
            "'/agents/<name>' segment. Set it as the project endpoint instead, "
            "and choose a specific agent (name:version or a hosted '/agents/...' "
            "URL) as the evaluation target."
        )
    return None


def normalize_hosted_agent_url(value: str) -> tuple[Optional[str], Optional[str]]:
    """Validate + canonicalize a pasted Foundry hosted-agent URL.

    Accepts the common shapes users paste, e.g.::

        https://<res>.services.ai.azure.com/api/projects/<p>/agents/<a>
        https://<res>.services.ai.azure.com/.../agents/<a>/versions/<v>
        https://<res>.services.ai.azure.com/.../endpoint/protocols/openai/responses

    Returns ``(normalized_url, None)`` on success or ``(None, error)`` with a
    specific, actionable message. Rejects project endpoints and portal URLs.
    """
    text = (value or "").strip()
    if not text:
        return None, "Enter the hosted agent URL, or choose another target type."
    if not _URL_RE.match(text):
        return None, "Hosted agent URL must start with https:// or http://."
    problem = classify_agent_url_problem(text)
    if problem is not None:
        return None, problem
    if "/agents/" not in _url_path(text).lower():
        return None, (
            "A Foundry hosted agent URL must contain an '/agents/<name>' "
            "segment. Paste the agent's hosted endpoint URL from Foundry."
        )
    # Normalize: drop trailing slashes and surrounding whitespace.
    normalized = text.rstrip("/")
    return normalized, None


def validate_agent(value: str) -> Optional[str]:
    """Validate an ``--agent`` value (scripted path) or a free-form entry.

    Shares the URL rejection rules used by the interactive wizard so a project
    endpoint or portal URL pasted via ``--agent`` fails with the same guidance.
    """
    if not value:
        return None
    if _URL_RE.match(value):
        return classify_agent_url_problem(value)
    if _AGENT_REF_RE.match(value):
        return None
    return (
        "Agent must be one of: <name>:<version>, model:<deployment>, or "
        "an https:// URL."
    )


def validate_foundry_prompt_ref(value: str) -> Optional[str]:
    """Validate a ``<name>:<version>`` Foundry prompt-agent reference."""
    text = (value or "").strip()
    if not text:
        return "Enter a <name>:<version> reference, or choose another target type."
    if text.lower().startswith("model:"):
        return "That is a model deployment. Choose the 'model deployment' target type."
    if _URL_RE.match(text):
        return "That is a URL. Choose the hosted-agent or external-HTTP target type."
    if not _AGENT_REF_RE.match(text):
        return "Use <name>:<version> (e.g. quickstart-agent:2)."
    return None


def validate_model_deployment(value: str) -> Optional[str]:
    """Validate a ``model:<deployment>`` reference."""
    text = (value or "").strip()
    if not text:
        return "Enter model:<deployment> (e.g. model:gpt-4.1)."
    if not _MODEL_REF_RE.match(text):
        return "Use model:<deployment> (e.g. model:gpt-4.1)."
    return None


def validate_external_http_agent(value: str) -> Optional[str]:
    """Validate an arbitrary external HTTP/JSON agent URL."""
    text = (value or "").strip()
    if not text:
        return "Enter the agent's https:// URL, or choose another target type."
    if not _URL_RE.match(text):
        return "External agent must be an http:// or https:// URL."
    return None


def validate_dataset(value: str, workspace: Path) -> Optional[str]:
    if not value:
        return None
    candidate = (workspace / value).resolve()
    if not candidate.exists():
        return f"Dataset file does not exist: {candidate}"
    return None


def _discover_foundry_project_endpoint_from_azd_env(
    workspace: Path,
    env_values: dict[str, str],
) -> Optional[str]:
    """Best-effort Foundry project endpoint discovery from the active azd env."""
    resource_group = _as_str(env_values.get("AZURE_RESOURCE_GROUP"))
    if not resource_group:
        return None
    az_cli = _az_cli_executable()
    if not az_cli:
        return None

    command = [
        az_cli,
        "resource",
        "list",
        "-g",
        resource_group,
        "--resource-type",
        "Microsoft.CognitiveServices/accounts/projects",
        "-o",
        "json",
    ]
    subscription_id = _as_str(env_values.get("AZURE_SUBSCRIPTION_ID"))
    if subscription_id:
        command.extend(["--subscription", subscription_id])

    try:
        result = subprocess.run(  # noqa: S603,S607
            command,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=AZ_CLI_DISCOVERY_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None

    try:
        resources = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None
    if not isinstance(resources, list):
        return None

    candidates: list[tuple[bool, str]] = []
    for item in resources:
        if not isinstance(item, dict):
            continue
        properties = item.get("properties") or _read_azure_resource_properties(
            workspace,
            item,
        )
        if not isinstance(properties, dict):
            continue
        endpoints = properties.get("endpoints")
        if not isinstance(endpoints, dict):
            continue
        endpoint = _as_str(endpoints.get("AI Foundry API"))
        if endpoint:
            candidates.append((bool(properties.get("isDefault")), endpoint))

    if not candidates:
        return None
    default_candidates = [
        endpoint for is_default, endpoint in candidates if is_default
    ]
    if len(default_candidates) == 1:
        return default_candidates[0]
    if len(candidates) == 1:
        return candidates[0][1]
    return None


def _read_azure_resource_properties(
    workspace: Path,
    resource: dict,
) -> Optional[dict]:
    resource_id = _as_str(resource.get("id"))
    if not resource_id:
        return None
    az_cli = _az_cli_executable()
    if not az_cli:
        return None
    try:
        result = subprocess.run(  # noqa: S603,S607
            [az_cli, "resource", "show", "--ids", resource_id, "-o", "json"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=AZ_CLI_DISCOVERY_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    properties = data.get("properties")
    return properties if isinstance(properties, dict) else None


def _az_cli_executable() -> Optional[str]:
    return shutil.which("az") or shutil.which("az.cmd")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def apply_answers(
    workspace: Path,
    answers: WizardAnswers,
    *,
    default_env_name: str = "dev",
    azd_env_name: Optional[str] = None,
    bootstrap_azd_env: bool = False,
) -> WizardResult:
    """Write the user's answers to ``agentops.yaml`` and the selected env file.

    Behavior:

    * ``agent`` and ``dataset`` are persisted to ``agentops.yaml`` only.
    * ``project_endpoint`` and ``appinsights_connection_string`` are
      persisted as environment variables in the active azd environment when
      one exists, or in ``.agentops/.env`` otherwise.
    * ``azd_env_name`` explicitly opts into creating/updating
      ``.azure/<env>/.env``. ``bootstrap_azd_env`` preserves the old internal
      behavior for callers that deliberately want to create an azd env.

    Only fields that the user actually provided (non-empty, non-``None``)
    are touched. Existing values not covered by an answer are preserved.
    """
    from agentops.utils.azd_env import (  # noqa: PLC0415
        AzdEnvLocation,
        discover_azd_env,
        ensure_azd_env,
        set_default_azd_env,
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
    if answers.clear_agent and is_placeholder_agent(_as_str(yaml_data.get("agent"))):
        # "Configure later" on a workspace still carrying the legacy placeholder:
        # strip it so readers treat this as project-observability-only rather
        # than a real (fake) target. We never remove a genuine agent here.
        yaml_data.pop("agent", None)
        yaml_data.pop("protocol", None)
        result.yaml_fields.append("agent")
        yaml_dirty = True
    if _changed("agent", answers.agent):
        yaml_data["agent"] = answers.agent
        result.yaml_fields.append("agent")
        yaml_dirty = True
    if _changed("protocol", answers.protocol):
        yaml_data["protocol"] = answers.protocol
        result.yaml_fields.append("protocol")
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

    # --- environment file ----------------------------------------------
    env_updates: dict[str, str] = {}
    if answers.project_endpoint is not None:
        env_updates[ENV_KEY_PROJECT_ENDPOINT] = answers.project_endpoint
    if answers.appinsights_connection_string is not None:
        env_updates[ENV_KEY_APPINSIGHTS] = answers.appinsights_connection_string

    if not env_updates:
        return result

    location: Optional[AzdEnvLocation] = None
    if azd_env_name:
        azd_env_path = workspace / ".azure" / azd_env_name / ".env"
        azd_env_preexisted = azd_env_path.is_file()
        location = ensure_azd_env(workspace, azd_env_name)
        set_default_azd_env(workspace, azd_env_name)
        result.azd_env_created = not azd_env_preexisted
    else:
        discovered = discover_azd_env(workspace)
        if discovered.found:
            location = discovered
        elif bootstrap_azd_env:
            if discovered.status == "ambiguous":
                raise RuntimeError(
                    "Multiple azd environments found but no default is set. "
                    "Set AZURE_ENV_NAME or write defaultEnvironment to "
                    ".azure/config.json, then re-run `agentops init`."
                )
            env_name = discovered.name or default_env_name
            location = ensure_azd_env(workspace, env_name)
            result.azd_env_created = True
        elif discovered.status == "ambiguous":
            location = None

    if location is not None:
        if not location.found and location.status == "ambiguous":
            raise RuntimeError(
                "Multiple azd environments found but no default is set. "
                "Set AZURE_ENV_NAME or write defaultEnvironment to "
                ".azure/config.json, then re-run `agentops init`."
            )
        assert location.env_path is not None  # narrowing for type checkers
        result.env_path = location.env_path
        result.azd_env_name = location.name
    else:
        result.env_path = ensure_agentops_env(workspace)

    changed_keys = set_env_values(result.env_path, env_updates)
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
        from agentops.utils.yaml import load_yaml  # noqa: PLC0415

        data = load_yaml(path)
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _write_agentops_yaml(path: Path, data: dict) -> None:
    from agentops.utils.yaml import save_yaml  # noqa: PLC0415

    path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve simple field order for readability: version, agent, dataset,
    # project_endpoint (legacy, only kept if already present), then
    # everything else.
    ordered_keys = ["version", "agent", "protocol", "dataset", "project_endpoint"]
    ordered: dict = {}
    for key in ordered_keys:
        if key in data:
            ordered[key] = data[key]
    for key, value in data.items():
        if key not in ordered:
            ordered[key] = value
    save_yaml(path, ordered)


_AGENTOPS_ENV_HEADER = (
    "# Managed by AgentOps. Run `agentops init` to update values here.\n"
    "# Local environment values in this file are git-ignored via .agentops/.gitignore.\n"
)


def ensure_agentops_env(workspace: Path) -> Path:
    """Create the AgentOps-owned local env file and protect it from git."""
    agentops_dir = workspace.resolve() / ".agentops"
    agentops_dir.mkdir(parents=True, exist_ok=True)
    ensure_agentops_gitignore(agentops_dir)
    env_path = agentops_dir / ".env"
    if not env_path.exists():
        env_path.write_text(_AGENTOPS_ENV_HEADER, encoding="utf-8")
    return env_path


def ensure_agentops_gitignore(agentops_dir: Path) -> bool:
    """Ensure ``.agentops/.gitignore`` excludes the local env file."""
    agentops_dir.mkdir(parents=True, exist_ok=True)
    gitignore = agentops_dir / ".gitignore"
    needed = ".env"
    if gitignore.is_file():
        try:
            existing = gitignore.read_text(encoding="utf-8")
        except OSError:
            existing = ""
        stripped = {
            ln.strip() for ln in existing.splitlines() if ln.strip() and not ln.startswith("#")
        }
        if needed in stripped or ".env*" in stripped or "*.env" in stripped:
            return False
        with gitignore.open("a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(".env\n")
        return True
    gitignore.write_text(
        "# Generated by agentops init\n"
        "# Keep local AgentOps environment values out of source control\n"
        ".env\n"
        ".env.*\n"
        "!.env.example\n"
        "results/**\n"
        "official-eval/**\n"
        ".resolved/**\n",
        encoding="utf-8",
    )
    return True


def _read_active_env(workspace: Path) -> dict[str, str]:
    """Read the active env file (azd env first, then AgentOps local env)."""
    values, _ = _read_active_env_with_source(workspace)
    return values


def _read_active_env_with_source(
    workspace: Path,
) -> tuple[dict[str, str], Optional[Path]]:
    """Read the active env file AND return the path it came from.

    The path is what lets the wizard later detect that a discovered default
    value came from a *different* azd environment than the one the user is
    currently configuring.
    """
    from agentops.utils.azd_env import discover_azd_env, parse_env_file  # noqa: PLC0415

    location = discover_azd_env(workspace)
    if location.found and location.env_path is not None:
        return parse_env_file(location.env_path), location.env_path
    agentops_env = workspace / ".agentops" / ".env"
    if agentops_env.is_file():
        return parse_env_file(agentops_env), agentops_env
    return {}, None


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


def _prompt_agent_target(
    prompt: PromptFn,
    echo: Callable[[str], None],
) -> tuple[Optional[str], Optional[str], bool]:
    """Run the guided evaluation-target menu.

    Returns ``(agent, protocol, configure_later)``:

    * choice 1-4 → ``(normalized_agent, protocol_or_None, False)``
    * choice 5   → ``(None, None, True)`` (project observability only)

    ``protocol`` is only set for the external-HTTP choice (``http-json``); the
    Foundry choices rely on runtime auto-classification.
    """
    while True:
        choice = prompt(
            "Choose a target kind [1-5]", AGENT_CHOICE_LATER
        ).strip()
        if not choice:
            # Pressing Enter defaults to "configure later": the safe,
            # non-destructive outcome that never writes a fake target.
            choice = AGENT_CHOICE_LATER

        if choice == AGENT_CHOICE_LATER:
            return None, None, True

        if choice == AGENT_CHOICE_PROMPT:
            label, validator, protocol = (
                "Foundry prompt agent (<name>:<version>)",
                validate_foundry_prompt_ref,
                None,
            )
        elif choice == AGENT_CHOICE_HOSTED_URL:
            value = _prompt_hosted_agent_url(prompt, echo)
            if value is None:
                continue  # user blanked out; back to the menu
            return value, None, False
        elif choice == AGENT_CHOICE_MODEL:
            label, validator, protocol = (
                "Model deployment (model:<deployment>)",
                validate_model_deployment,
                None,
            )
        elif choice == AGENT_CHOICE_HTTP:
            label, validator, protocol = (
                "External HTTP agent URL",
                validate_external_http_agent,
                "http-json",
            )
        else:
            echo("  ! Enter a number between 1 and 5.")
            continue

        value = _prompt_validated_value(prompt, echo, label, validator)
        if value is None:
            continue  # user blanked out; back to the menu
        return value, protocol, False


def _prompt_validated_value(
    prompt: PromptFn,
    echo: Callable[[str], None],
    label: str,
    validator: Callable[[str], Optional[str]],
) -> Optional[str]:
    """Prompt for a single value, re-asking on validation error.

    Returns the validated string, or ``None`` if the user entered nothing
    (signal to return to the target-kind menu).
    """
    while True:
        raw = prompt(label, None).strip()
        if not raw:
            return None
        err = validator(raw)
        if err:
            echo("  ! " + err)
            continue
        return raw


def _prompt_hosted_agent_url(
    prompt: PromptFn,
    echo: Callable[[str], None],
) -> Optional[str]:
    """Prompt for a Foundry hosted-agent URL, normalizing/rejecting as needed.

    Returns the canonical URL, or ``None`` if the user entered nothing.
    """
    while True:
        raw = prompt("Foundry hosted agent URL", None).strip()
        if not raw:
            return None
        normalized, err = normalize_hosted_agent_url(raw)
        if err:
            echo("  ! " + err)
            continue
        return normalized


def run_wizard(
    workspace: Path,
    prompt: PromptFn,
    echo: Callable[[str], None],
    *,
    defaults: Optional[WizardAnswers] = None,
    on_answer: Optional[OnAnswerFn] = None,
    reconfigure: bool = False,
    force_prompt_fields: Optional[Collection[str]] = None,
    target_env_name: Optional[str] = None,
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

    ``target_env_name`` is the azd environment the user is *explicitly*
    configuring (i.e. ``agentops init --azd-env <name>``). When provided, the
    wizard suppresses any discovered default for ``project_endpoint`` that
    came from a *different* source than ``.azure/<target>/.env`` (for example,
    an ``$AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`` exported in the shell from a
    sandbox project, or another azd env's ``.env``). The user is shown an
    explicit note about the discovered cross-environment value and asked to
    enter the endpoint for the targeted env explicitly. Without
    ``target_env_name`` (the bare ``agentops init`` case), behavior is
    unchanged.
    """
    defaults = defaults or discover_defaults(workspace)
    answers = WizardAnswers()
    skipped: list[str] = []
    forced_fields = set(force_prompt_fields or ())
    unicode_ok = _can_encode("✓•")
    ok_glyph = "✓" if unicode_ok else "*"

    def _should_prompt(field_name: str, value: Optional[str]) -> bool:
        return reconfigure or field_name in forced_fields or not value

    def _agent_default(value: Optional[str]) -> Optional[str]:
        return None if is_placeholder_agent(value) else value

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
    #
    # When the user explicitly asked for a specific azd env via --azd-env,
    # any default value that did NOT come from that env's own .env file is
    # suspect: it most likely belongs to a different environment (sandbox /
    # qa / prod) that just happens to be active in the user's shell. We
    # suppress the pre-fill and surface a note so the user knows what we
    # ignored and why.
    cross_env_note = _detect_cross_env_endpoint_leak(
        workspace=workspace,
        target_env_name=target_env_name,
        defaults=defaults,
    )
    suppress_endpoint_default = cross_env_note is not None
    effective_endpoint_default = (
        None if suppress_endpoint_default else defaults.project_endpoint
    )
    endpoint_default_needs_persist = (
        defaults.project_endpoint_source == ENDPOINT_SOURCE_AZD_RESOURCE_DISCOVERY
    )
    agent_default = _agent_default(defaults.agent)
    agent_needs_prompt = _should_prompt("agent", agent_default)
    endpoint_default_needs_review = (
        endpoint_default_needs_persist
        or agent_needs_prompt
        or bool(forced_fields)
    )

    if not suppress_endpoint_default and not _should_prompt(
        "project_endpoint", defaults.project_endpoint
    ) and not endpoint_default_needs_review:
        _confirm_existing(PROJECT_ENDPOINT_TITLE, defaults.project_endpoint or "")
        skipped.append("project_endpoint")
    else:
        echo("")
        echo(PROJECT_ENDPOINT_TITLE)
        echo(_indent(PROJECT_ENDPOINT_HELP))
        if cross_env_note:
            echo("")
            echo(_indent(cross_env_note))
        while True:
            raw = prompt("Foundry project endpoint", effective_endpoint_default)
            value = raw.strip()
            if not value:
                if effective_endpoint_default:
                    if endpoint_default_needs_persist:
                        answers.project_endpoint = effective_endpoint_default
                        _persist("project_endpoint", effective_endpoint_default)
                    break  # keep current
                echo("  ! Foundry project endpoint is required.")
                echo("  ! " + REQUIRED_CONFIGURATION_MESSAGE)
                continue
            err = validate_project_endpoint(value)
            if err:
                echo("  ! " + err)
                continue
            if endpoint_default_needs_persist or value != (
                defaults.project_endpoint or ""
            ):
                answers.project_endpoint = value
                _persist("project_endpoint", value)
            break

    # 2) Evaluation target (optional) — guided target-kind choice.
    if not agent_needs_prompt:
        _confirm_existing(AGENT_TITLE, agent_default or "")
        skipped.append("agent")
    else:
        echo("")
        echo(AGENT_TITLE)
        echo(_indent(AGENT_HELP))
        chosen_agent, chosen_protocol, configure_later = _prompt_agent_target(
            prompt, echo
        )
        if configure_later:
            answers.clear_agent = True
            echo(_indent(AGENT_LATER_CONFIRMATION))
        elif chosen_agent is not None:
            if chosen_agent != (defaults.agent or ""):
                answers.agent = chosen_agent
                _persist("agent", chosen_agent)
            if chosen_protocol is not None:
                answers.protocol = chosen_protocol
                _persist("protocol", chosen_protocol)

    # 3) Dataset
    if not _should_prompt("dataset", defaults.dataset):
        _confirm_existing(DATASET_TITLE, defaults.dataset or "")
        skipped.append("dataset")
    else:
        echo("")
        echo(DATASET_TITLE)
        echo(_indent(DATASET_HELP))
        dataset_default = defaults.dataset or ".agentops/data/smoke.jsonl"
        while True:
            raw = prompt("Dataset path", dataset_default)
            value = raw.strip()
            if not value:
                value = dataset_default
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


def _detect_cross_env_endpoint_leak(
    *,
    workspace: Path,
    target_env_name: Optional[str],
    defaults: WizardAnswers,
) -> Optional[str]:
    """Return a user-facing note when the discovered endpoint default looks
    like it belongs to a *different* environment than ``target_env_name``.

    Returns ``None`` when the default is trustworthy (or when the wizard
    was not given a target env to be opinionated about). The returned
    string is a complete multi-line explanation suitable to ``echo`` above
    the project-endpoint prompt; the caller is responsible for suppressing
    the pre-fill (so the user must type the correct endpoint explicitly).
    """
    if not target_env_name:
        return None
    if not defaults.project_endpoint:
        return None
    source = defaults.project_endpoint_source or ENDPOINT_SOURCE_NONE
    if source in (ENDPOINT_SOURCE_NONE, ENDPOINT_SOURCE_AGENTOPS_ENV_FILE):
        return None
    if source == ENDPOINT_SOURCE_AZD_ENV_FILE:
        target_env_file = (workspace / ".azure" / target_env_name / ".env").resolve()
        source_path = defaults.project_endpoint_source_path
        if source_path is not None and source_path.resolve() == target_env_file:
            return None  # Same azd env as the user targeted — safe to reuse.
        source_label = (
            f"another azd env file ({source_path})"
            if source_path is not None
            else "another azd env file"
        )
    elif source == ENDPOINT_SOURCE_PROCESS_ENV:
        source_label = f"your shell environment (${ENV_KEY_PROJECT_ENDPOINT})"
    elif source == ENDPOINT_SOURCE_YAML_LEGACY:
        source_label = "the legacy 'project_endpoint' field in agentops.yaml"
    else:  # pragma: no cover - defensive
        source_label = source

    return (
        f"Note: a Foundry project endpoint was discovered in {source_label}:\n"
        f"    {defaults.project_endpoint}\n"
        f"Not using it as a default for env '{target_env_name}' because it may "
        f"belong to a different environment.\n"
        f"Please paste the endpoint for the '{target_env_name}' Foundry project:"
    )


# ---------------------------------------------------------------------------
# `agentops init show` — inspect the current setup
# ---------------------------------------------------------------------------


@dataclass
class SetupSnapshotVar:
    """One row in the ``agentops init show`` output."""

    key: str
    value: Optional[str]
    source: str  # "azd-env" | "agentops-env" | "process-env" | "default" | "not set"
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
    agentops_env_path: Optional[Path]
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

    agentops_env = workspace / ".agentops" / ".env"
    agentops_env_path: Optional[Path] = agentops_env if agentops_env.is_file() else None
    agentops_values = parse_env_file(agentops_env) if agentops_env_path else {}

    variables: List[SetupSnapshotVar] = []
    for key, is_secret, is_required, description in _MANAGED_VARS:
        proc_value = os.environ.get(key)
        env_value = env_values.get(key) or agentops_values.get(key)
        # Process env wins only when it actually differs from the file —
        # otherwise we attribute the value to the (more durable) env file.
        if proc_value is not None and proc_value != env_value:
            value, source = proc_value, "process-env"
        elif env_value:
            value, source = env_value, "azd-env" if env_values.get(key) else "agentops-env"
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
        agentops_env_path=agentops_env_path,
        yaml_path=yaml_path,
        yaml_present=yaml_path.exists(),
        yaml_agent=_as_str(yaml_data.get("agent")),
        yaml_dataset=_as_str(yaml_data.get("dataset")),
        yaml_project_endpoint=_as_str(yaml_data.get("project_endpoint")),
        variables=variables,
        legacy_env_path=None,
    )


def mask_secret(value: Optional[str]) -> str:
    """Return a UI-safe rendering of a secret value."""
    if not value:
        return "(not set)"
    if len(value) <= 8:
        return "***"
    return value[:4] + "***" + value[-4:]
