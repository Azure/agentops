"""Create or delete a transient Foundry hosted agent for the E2E pipeline.

The agent is a prompt agent with a single ``get_weather(location)`` function
tool, used to exercise the agent-with-tools evaluators (TaskCompletion,
ToolCallAccuracy, IntentResolution, ToolSelection, ToolInputAccuracy).

Subcommands:
    create --name <agent-name> [--model <deployment>]
        Creates the agent and prints two GitHub Actions output lines:
            agent_id=<name>:<version>
            agent_name=<name>
        Also writes ``e2e-runs/foundry-hosted/agent-info.json`` with the
        agent metadata so the transcript script can render an informative
        header.

    delete --name <agent-name>
        Deletes every version of the named agent, then deletes the agent
        itself. Idempotent: ignores 404s.

Authentication uses ``DefaultAzureCredential``. The Foundry project endpoint
is read from ``AZURE_AI_FOUNDRY_PROJECT_ENDPOINT``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = ROOT / "e2e-runs" / "foundry-hosted"

INSTRUCTIONS = (
    "You are a concise weather assistant. When the user asks about the "
    "weather, always call the get_weather function with the location they "
    "mention. Do not invent weather data and do not answer without calling "
    "the tool."
)

WEATHER_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "location": {
            "type": "string",
            "description": "City and country, e.g. 'Paris, France'.",
        }
    },
    "required": ["location"],
    "additionalProperties": False,
}


def _client():
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    endpoint = os.environ.get("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        raise SystemExit("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT is required")
    cred = DefaultAzureCredential(exclude_developer_cli_credential=True)
    return AIProjectClient(endpoint=endpoint, credential=cred)


def _emit_output(key: str, value: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    line = f"{key}={value}"
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    print(line)


def cmd_create(args: argparse.Namespace) -> int:
    from azure.ai.projects.models import (
        FunctionTool,
        PromptAgentDefinition,
    )

    client = _client()
    tool = FunctionTool(
        name="get_weather",
        description="Get the current weather for a given location.",
        parameters=WEATHER_TOOL_PARAMETERS,
        strict=True,
    )
    definition = PromptAgentDefinition(
        model=args.model,
        instructions=INSTRUCTIONS,
        tools=[tool],
    )

    from azure.core.exceptions import HttpResponseError, ServiceResponseError

    last_exc: Exception | None = None
    version = None
    for attempt in range(1, 6):
        try:
            version = client.agents.create_version(
                agent_name=args.name,
                definition=definition,
                description="AgentOps E2E transient hosted agent (weather + get_weather tool).",
            )
            break
        except (HttpResponseError, ServiceResponseError) as exc:
            status = getattr(exc, "status_code", None)
            transient = status is None or status >= 500 or status == 429
            print(
                f"create_version attempt {attempt}/5 failed (status={status}): {exc}",
                file=sys.stderr,
            )
            if not transient or attempt == 5:
                raise
            last_exc = exc
            time.sleep(min(2 ** attempt, 30))
    if version is None:
        raise SystemExit(f"create_version failed after retries: {last_exc!r}")

    version_id = getattr(version, "version", None) or getattr(version, "id", None)
    if not version_id:
        raise SystemExit(f"Could not determine version id from response: {version!r}")

    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    info = {
        "name": args.name,
        "version": str(version_id),
        "model": args.model,
        "instructions": INSTRUCTIONS,
        "tools": [
            {
                "type": "function",
                "name": "get_weather",
                "description": tool.description,
                "parameters": WEATHER_TOOL_PARAMETERS,
            }
        ],
    }
    (SCENARIO_DIR / "agent-info.json").write_text(
        json.dumps(info, indent=2), encoding="utf-8"
    )

    _emit_output("agent_id", f"{args.name}:{version_id}")
    _emit_output("agent_name", args.name)
    print(f"Created hosted agent: {args.name}:{version_id}", file=sys.stderr)
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    from azure.core.exceptions import ResourceNotFoundError

    client = _client()
    deleted_versions = 0
    try:
        for v in client.agents.list_versions(agent_name=args.name):
            ver_id = getattr(v, "version", None) or getattr(v, "id", None)
            if ver_id is None:
                continue
            try:
                client.agents.delete_version(agent_name=args.name, version=str(ver_id))
                deleted_versions += 1
            except ResourceNotFoundError:
                pass
    except ResourceNotFoundError:
        print(f"Agent {args.name} not found (already deleted)", file=sys.stderr)
        return 0

    try:
        client.agents.delete(agent_name=args.name)
    except ResourceNotFoundError:
        pass

    print(
        f"Deleted hosted agent {args.name} ({deleted_versions} version(s))",
        file=sys.stderr,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="Create the transient hosted agent")
    p_create.add_argument("--name", required=True)
    p_create.add_argument("--model", default="gpt-4o-mini")
    p_create.set_defaults(func=cmd_create)

    p_delete = sub.add_parser("delete", help="Delete the transient hosted agent")
    p_delete.add_argument("--name", required=True)
    p_delete.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
