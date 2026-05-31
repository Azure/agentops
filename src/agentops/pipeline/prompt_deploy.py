"""Foundry prompt-agent deployment helper for generated CI/CD workflows.

This module is intentionally not a public ``agentops`` command. Generated
workflows invoke it with ``python -m`` so AgentOps can keep the CLI surface
small while still providing a tested prompt-agent deploy path.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from agentops.core.agentops_config import (
    AgentOpsConfig,
    PromptAgentBootstrap,
    classify_agent,
)
from agentops.core.config_loader import load_agentops_config
from agentops.utils.yaml import load_yaml, save_yaml

DEFAULT_PROMPT_FILE = Path(".agentops/prompts/agent-instructions.md")
DEFAULT_DEPLOYMENT_RECORD = Path(".agentops/deployments/foundry-agent.json")
DEFAULT_CANDIDATE_CONFIG = Path(".agentops/deployments/agentops.candidate.yaml")


def stage_prompt_agent_candidate(
    *,
    config_path: Path,
    prompt_file: Optional[Path] = None,
    environment: str,
    output_path: Path = DEFAULT_DEPLOYMENT_RECORD,
    eval_config_path: Path = DEFAULT_CANDIDATE_CONFIG,
) -> Dict[str, Any]:
    """Create or reuse a Foundry prompt-agent version and write eval config.

    The generated eval config points at the candidate version, so the CI gate
    evaluates the same Foundry agent definition that the deploy stage records.

    If the target Foundry project does not yet contain the seed agent
    referenced by ``agent`` (the SDK raises a 404), the function falls back to
    bootstrapping the agent using ``prompt_agent_bootstrap`` from
    ``agentops.yaml``. This lets CI/CD provision dev/qa/prod from a
    sandbox-only authoring flow without requiring a manual portal step.
    """

    config_path = config_path.resolve()
    config = load_agentops_config(config_path)
    target = classify_agent(config.agent, config.protocol)
    if target.kind != "foundry_prompt" or not target.name or not target.version:
        raise ValueError(
            "prompt-agent deployment requires agentops.yaml agent to be a "
            "Foundry prompt agent in 'name:version' form"
        )

    resolved_prompt = _resolve_prompt_file(
        config_path=config_path,
        config=config,
        explicit=prompt_file,
    )
    instructions = resolved_prompt.read_text(encoding="utf-8")
    if not instructions.strip():
        raise ValueError(f"prompt file is empty: {resolved_prompt}")

    endpoint = config.project_endpoint or os.environ.get("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        raise ValueError(
            "prompt-agent deployment requires project_endpoint in agentops.yaml "
            "or AZURE_AI_FOUNDRY_PROJECT_ENDPOINT"
        )

    prompt_hash = hashlib.sha256(instructions.encode("utf-8")).hexdigest()

    try:
        current = _get_agent_version(endpoint, target.name, target.version)
    except Exception as exc:  # noqa: BLE001 — narrowed by _is_not_found_error below
        if _is_not_found_error(exc):
            current = None
        else:
            raise

    metadata = _deployment_metadata(
        environment=environment,
        prompt_hash=prompt_hash,
    )
    description = (
        f"AgentOps {environment} candidate from "
        f"{resolved_prompt.as_posix()} ({prompt_hash[:12]})"
    )

    if current is None:
        # Bootstrap path: target project does not yet contain the seed agent.
        if config.prompt_agent_bootstrap is None:
            raise ValueError(
                f"Foundry agent {target.name}:{target.version} does not exist "
                f"in project {endpoint}, and 'prompt_agent_bootstrap' is not "
                "configured in agentops.yaml.\n\n"
                "Either create the agent manually in the target project, or "
                "add prompt_agent_bootstrap defaults so CI/CD can create the "
                "first version automatically. Minimal example:\n\n"
                "  prompt_agent_bootstrap:\n"
                "    model: <your-model-deployment-name>\n\n"
                "Then re-run the deploy workflow."
            )
        created = _bootstrap_prompt_agent(
            endpoint=endpoint,
            agent_name=target.name,
            bootstrap=config.prompt_agent_bootstrap,
            instructions=instructions,
            metadata=metadata,
            description=description,
        )
        candidate_version = str(
            getattr(created, "version", None)
            or _get_mapping_value(created, "version")
            or ""
        )
        if not candidate_version:
            raise ValueError("Foundry create_version did not return a version")
        action = "bootstrapped"
    else:
        definition = getattr(current, "definition", None) or _get_mapping_value(current, "definition")
        if definition is None:
            raise ValueError(
                f"Foundry agent {target.name}:{target.version} did not include a definition"
            )

        kind = str(_get_definition_value(definition, "kind") or "").lower()
        if kind != "prompt":
            raise ValueError(
                f"Foundry agent {target.name}:{target.version} is kind {kind!r}; "
                "prompt-agent deployment only supports kind 'prompt'"
            )

        current_instructions = _get_definition_value(definition, "instructions") or ""
        if str(current_instructions) == instructions:
            candidate_version = target.version
            action = "reused"
            created = current
        else:
            candidate_definition = _copy_definition(definition)
            _set_definition_value(candidate_definition, "instructions", instructions)
            created = _create_agent_version(
                endpoint,
                target.name,
                candidate_definition,
                metadata=metadata,
                description=description,
            )
            candidate_version = str(
                getattr(created, "version", None)
                or _get_mapping_value(created, "version")
                or ""
            )
            if not candidate_version:
                raise ValueError("Foundry create_version did not return a version")
            action = "created"

    eval_config_path = eval_config_path.resolve()
    output_path = output_path.resolve()
    _write_candidate_eval_config(
        source_config_path=config_path,
        config=config,
        candidate_agent=f"{target.name}:{candidate_version}",
        destination=eval_config_path,
    )

    record = {
        "version": 1,
        "type": "foundry_prompt_agent_deployment",
        "environment": environment,
        "action": action,
        "agent_name": target.name,
        "source_agent": f"{target.name}:{target.version}",
        "candidate_agent": f"{target.name}:{candidate_version}",
        "source_version": target.version,
        "candidate_version": candidate_version,
        "project_endpoint": endpoint,
        "prompt_file": str(resolved_prompt),
        "prompt_sha256": prompt_hash,
        "eval_config": str(eval_config_path),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "workflow_url": _workflow_url(),
        "foundry_agent_version_id": str(
            getattr(created, "id", None) or _get_mapping_value(created, "id") or ""
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def summarize_deployment(record_path: Path, *, environment: str) -> Dict[str, Any]:
    record = json.loads(record_path.read_text(encoding="utf-8"))
    candidate = record.get("candidate_agent", "unknown")
    action = record.get("action", "recorded")
    prompt_hash = str(record.get("prompt_sha256", ""))[:12]
    message = (
        f"Foundry prompt agent {candidate} passed the AgentOps gate for "
        f"{environment} ({action}, prompt {prompt_hash})."
    )
    print(message)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write("## Foundry prompt-agent deployment\n\n")
            handle.write(f"- **Environment:** {environment}\n")
            handle.write(f"- **Agent version:** `{candidate}`\n")
            handle.write(f"- **Action:** `{action}`\n")
            handle.write(f"- **Prompt hash:** `{record.get('prompt_sha256', '')}`\n")
            if action == "bootstrapped":
                handle.write(
                    "- Agent did not exist in this environment; created from "
                    "`prompt_agent_bootstrap` defaults.\n"
                )
            if record.get("workflow_url"):
                handle.write(f"- **Workflow:** {record['workflow_url']}\n")
    return record


def _resolve_prompt_file(
    *,
    config_path: Path,
    config: AgentOpsConfig,
    explicit: Optional[Path],
) -> Path:
    raw = (
        explicit
        or _path_from_env("AGENTOPS_AGENT_PROMPT_FILE")
        or config.prompt_file
        or DEFAULT_PROMPT_FILE
    )
    path = raw if raw.is_absolute() else (config_path.parent / raw)
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(
            f"prompt file not found: {path}. Set prompt_file in agentops.yaml "
            "or AGENTOPS_AGENT_PROMPT_FILE in the workflow environment."
        )
    return path


def _path_from_env(name: str) -> Optional[Path]:
    value = os.environ.get(name)
    if not value or not value.strip():
        return None
    # Azure DevOps leaves undefined variables as literal "$(NAME)" strings.
    if value.strip().startswith("$(") and value.strip().endswith(")"):
        return None
    return Path(value)


def _get_agent_version(endpoint: str, agent_name: str, agent_version: str) -> Any:
    client = _project_client(endpoint)
    return client.agents.get_version(agent_name, agent_version)


def _is_not_found_error(exc: BaseException) -> bool:
    """True when ``exc`` is an Azure 404 / ResourceNotFound, false otherwise.

    Deliberately narrow: 401/403/5xx / generic errors must propagate so
    callers see auth, RBAC, or transport failures clearly instead of being
    masked by a bootstrap path.
    """

    status = getattr(exc, "status_code", None)
    if status == 404:
        return True
    try:
        from azure.core.exceptions import ResourceNotFoundError  # noqa: WPS433
    except ImportError:
        return False
    return isinstance(exc, ResourceNotFoundError)


def _bootstrap_prompt_agent(
    *,
    endpoint: str,
    agent_name: str,
    bootstrap: PromptAgentBootstrap,
    instructions: str,
    metadata: Dict[str, str],
    description: str,
) -> Any:
    """Create the first version of a prompt agent from bootstrap defaults."""

    definition: Dict[str, Any] = {
        "kind": "prompt",
        "model": bootstrap.model,
        "instructions": instructions,
    }
    if bootstrap.model_parameters:
        definition["model_parameters"] = dict(bootstrap.model_parameters)
    if bootstrap.tools:
        definition["tools"] = [dict(tool) for tool in bootstrap.tools]

    return _create_agent_version(
        endpoint,
        agent_name,
        definition,
        metadata=metadata,
        description=bootstrap.description or description,
    )


def _create_agent_version(
    endpoint: str,
    agent_name: str,
    definition: Any,
    *,
    metadata: Dict[str, str],
    description: str,
) -> Any:
    client = _project_client(endpoint)
    definition_dict = _definition_to_dict(definition)
    body: Dict[str, Any] = {
        "definition": definition_dict,
        "metadata": metadata,
        "description": description,
    }
    body = {key: value for key, value in body.items() if value is not None}
    return client.agents.create_version(
        agent_name,
        body=body,
    )


def _project_client(endpoint: str) -> Any:
    try:
        from azure.ai.projects import AIProjectClient  # noqa: WPS433
        from azure.identity import DefaultAzureCredential  # noqa: WPS433
    except ImportError as exc:
        raise RuntimeError(
            "prompt-agent deployment requires azure-ai-projects and "
            "azure-identity; install agentops-accelerator[foundry]"
        ) from exc

    credential = DefaultAzureCredential(
        exclude_developer_cli_credential=True,
        process_timeout=30,
    )
    return AIProjectClient(endpoint=endpoint, credential=credential)


def _get_definition_value(definition: Any, key: str) -> Any:
    if hasattr(definition, "get"):
        value = definition.get(key)
        if value is not None:
            return value
    data = getattr(definition, "_data", None)
    if isinstance(data, dict):
        value = data.get(key)
        if value is not None:
            return value
    return getattr(definition, key, None)


def _set_definition_value(definition: Any, key: str, value: Any) -> None:
    if hasattr(definition, "__setitem__"):
        definition[key] = value
        return
    data = getattr(definition, "_data", None)
    if isinstance(data, dict):
        data[key] = value
        return
    setattr(definition, key, value)


def _get_mapping_value(value: Any, key: str) -> Any:
    if hasattr(value, "get"):
        return value.get(key)
    return None


def _copy_definition(definition: Any) -> Dict[str, Any]:
    """Return a deep copy of ``definition`` as a plain dict.

    The Foundry SDK's typed definition models (e.g. ``PromptAgentDefinition``)
    expose ``.copy()``, but in ``azure-ai-projects`` 2.x that returns a stripped
    base ``Model`` whose JSON shape is ``{"_data": {...}}`` instead of the
    flattened payload the service expects. To stay compatible across SDK
    versions we always normalize to a plain dict here.
    """

    return copy.deepcopy(_definition_to_dict(definition))


def _definition_to_dict(definition: Any) -> Dict[str, Any]:
    """Best-effort conversion of an SDK definition object into a plain dict."""

    if isinstance(definition, dict):
        return dict(definition)
    data = getattr(definition, "_data", None)
    if isinstance(data, dict):
        return dict(data)
    if hasattr(definition, "items"):
        try:
            return {key: value for key, value in definition.items()}
        except Exception:  # noqa: BLE001 — fall through to attribute scrape
            pass
    if hasattr(definition, "as_dict"):
        try:
            return dict(definition.as_dict())
        except Exception:  # noqa: BLE001
            pass
    raise TypeError(
        f"Cannot convert Foundry agent definition of type {type(definition).__name__} "
        "to a dict; expected a mapping-compatible object."
    )


def _deployment_metadata(*, environment: str, prompt_hash: str) -> Dict[str, str]:
    metadata = {
        "agentops.env": environment[:512],
        "agentops.prompt_sha256": prompt_hash,
        "agentops.git_sha": _git_sha()[:512],
    }
    workflow_url = _workflow_url()
    if workflow_url:
        metadata["agentops.workflow_url"] = workflow_url[:512]
    return {key: value for key, value in metadata.items() if value}


def _git_sha() -> str:
    return (
        os.environ.get("GITHUB_SHA")
        or os.environ.get("BUILD_SOURCEVERSION")
        or os.environ.get("Build.SourceVersion")
        or ""
    )


def _workflow_url() -> str:
    if os.environ.get("GITHUB_SERVER_URL") and os.environ.get("GITHUB_REPOSITORY"):
        run_id = os.environ.get("GITHUB_RUN_ID")
        if run_id:
            return (
                f"{os.environ['GITHUB_SERVER_URL']}/"
                f"{os.environ['GITHUB_REPOSITORY']}/actions/runs/{run_id}"
            )
    collection_uri = os.environ.get("SYSTEM_COLLECTIONURI")
    team_project = os.environ.get("SYSTEM_TEAMPROJECT")
    build_id = os.environ.get("BUILD_BUILDID")
    if collection_uri and team_project and build_id:
        return f"{collection_uri}{team_project}/_build/results?buildId={build_id}"
    return ""


def _write_candidate_eval_config(
    *,
    source_config_path: Path,
    config: AgentOpsConfig,
    candidate_agent: str,
    destination: Path,
) -> None:
    data = load_yaml(source_config_path)
    data["agent"] = candidate_agent
    dataset_path = config.dataset
    if not dataset_path.is_absolute():
        dataset_path = (source_config_path.parent / dataset_path).resolve()
    data["dataset"] = str(dataset_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    save_yaml(destination, data)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    stage = subparsers.add_parser("stage", help="stage a prompt-agent candidate")
    stage.add_argument("--config", type=Path, default=Path("agentops.yaml"))
    stage.add_argument("--prompt-file", type=Path)
    stage.add_argument("--environment", required=True)
    stage.add_argument("--out", type=Path, default=DEFAULT_DEPLOYMENT_RECORD)
    stage.add_argument("--eval-config", type=Path, default=DEFAULT_CANDIDATE_CONFIG)

    summarize = subparsers.add_parser("summarize", help="summarize a deployment record")
    summarize.add_argument("--deployment", type=Path, default=DEFAULT_DEPLOYMENT_RECORD)
    summarize.add_argument("--environment", required=True)
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    args = _build_parser().parse_args(argv)
    if args.command == "stage":
        record = stage_prompt_agent_candidate(
            config_path=args.config,
            prompt_file=args.prompt_file,
            environment=args.environment,
            output_path=args.out,
            eval_config_path=args.eval_config,
        )
        print(
            "AgentOps staged Foundry prompt candidate "
            f"{record['candidate_agent']} ({record['action']})."
        )
        return
    if args.command == "summarize":
        summarize_deployment(args.deployment, environment=args.environment)


if __name__ == "__main__":
    main()
