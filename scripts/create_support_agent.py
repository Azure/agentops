"""Create or delete a hosted Foundry support agent for the end-to-end tutorial.

The tutorial in ``docs/tutorial-end-to-end.md`` walks the user through a
realistic agent-with-tools evaluation. This helper avoids forcing the user to
click through the Foundry portal: it registers three function tools
(``lookup_order``, ``refund_order``, ``escalate_to_human``) on a fresh hosted
prompt agent in one command.

Usage::

    # Create the agent. Prints the ``name:version`` identifier you paste into
    # ``agentops.yaml``.
    python scripts/create_support_agent.py create --name support-bot

    # Create a degraded version that omits the tool-call instruction. Used to
    # demonstrate baseline regression detection.
    python scripts/create_support_agent.py create --name support-bot --variant v2-degraded

    # Delete every version of the agent (idempotent — ignores 404s).
    python scripts/create_support_agent.py delete --name support-bot

Authentication uses ``DefaultAzureCredential``; the project endpoint is read
from ``AZURE_AI_FOUNDRY_PROJECT_ENDPOINT``. The user must hold an Azure AI
data-plane role (``Azure AI User`` is enough) on the Foundry account.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

INSTRUCTIONS_GOOD = (
    "You are a customer support assistant. You MUST use the provided tools to "
    "answer the user. Choose exactly one tool per message and supply complete, "
    "correct arguments. Use:\n"
    "- lookup_order when the user asks about an order's status, location, or "
    "delivery details.\n"
    "- refund_order when the user explicitly asks for a refund or to return "
    "an item.\n"
    "- escalate_to_human when the user asks to speak with a human, manager, "
    "or representative, or expresses serious frustration.\n"
    "If the user is just greeting you or making small talk, respond briefly "
    "in plain text without calling any tool. Never invent data and never "
    "answer order-specific questions from memory."
)

INSTRUCTIONS_DEGRADED = (
    "You are a friendly customer support assistant. Answer the user in a "
    "warm, conversational tone. Reassure them and apologize for any "
    "inconvenience. Do not use any tools — just reply in plain text."
)


LOOKUP_ORDER_PARAMETERS = {
    "type": "object",
    "properties": {
        "order_id": {
            "type": "string",
            "description": "The order identifier the user mentioned, e.g. 'ORD-12345'.",
        }
    },
    "required": ["order_id"],
    "additionalProperties": False,
}

REFUND_ORDER_PARAMETERS = {
    "type": "object",
    "properties": {
        "order_id": {
            "type": "string",
            "description": "The order identifier to refund.",
        },
        "reason": {
            "type": "string",
            "description": "Short reason text the user gave (e.g. 'arrived broken').",
        },
    },
    "required": ["order_id", "reason"],
    "additionalProperties": False,
}

ESCALATE_TO_HUMAN_PARAMETERS = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "description": "Short topic the user wants to discuss (e.g. 'refund', 'billing').",
        }
    },
    "required": ["category"],
    "additionalProperties": False,
}


TOOL_SPECS = [
    (
        "lookup_order",
        "Look up the current status and shipping details of a customer order.",
        LOOKUP_ORDER_PARAMETERS,
    ),
    (
        "refund_order",
        "Issue a refund for a customer order, given the order id and a short reason.",
        REFUND_ORDER_PARAMETERS,
    ),
    (
        "escalate_to_human",
        "Hand the conversation over to a human agent for the given topic.",
        ESCALATE_TO_HUMAN_PARAMETERS,
    ),
]


def _client():
    import logging

    from azure.ai.projects import AIProjectClient
    from azure.core.exceptions import ClientAuthenticationError
    from azure.identity import DefaultAzureCredential

    # Silence azure-identity's verbose credential-chain logging so the
    # friendly "Run `az login`" message below isn't drowned out.
    logging.getLogger("azure.identity").setLevel(logging.ERROR)
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(
        logging.ERROR
    )

    endpoint = os.environ.get("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        raise SystemExit(
            "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT is required. "
            "Set it to your Foundry project URL, e.g. "
            "'https://<resource>.services.ai.azure.com/api/projects/<project>'."
        )
    cred = DefaultAzureCredential(exclude_developer_cli_credential=True)
    # Preflight: fail fast with a clear message if no Azure identity is available.
    # Without this, the SDK would otherwise dump a 30+ line credential-chain
    # error five times (once per retry) before bailing out.
    try:
        cred.get_token("https://ai.azure.com/.default")
    except ClientAuthenticationError:
        raise SystemExit(
            "Unable to acquire an Azure access token. Run `az login` "
            "(or set AZURE_CLIENT_ID/AZURE_TENANT_ID/AZURE_CLIENT_SECRET for "
            "service-principal auth) and try again."
        )
    return AIProjectClient(endpoint=endpoint, credential=cred)


def cmd_create(args: argparse.Namespace) -> int:
    from azure.ai.projects.models import FunctionTool, PromptAgentDefinition
    from azure.core.exceptions import HttpResponseError, ServiceResponseError

    instructions = (
        INSTRUCTIONS_DEGRADED if args.variant == "v2-degraded" else INSTRUCTIONS_GOOD
    )
    if args.variant == "v2-degraded":
        # The whole point of the degraded variant is to demonstrate a tool-quality
        # regression. We strip the tools entirely so the agent literally cannot
        # call lookup_order / refund_order / escalate_to_human — forcing
        # tool_call_accuracy and task_adherence to collapse.
        tools: list = []
    else:
        tools = [
            FunctionTool(name=name, description=desc, parameters=params, strict=True)
            for name, desc, params in TOOL_SPECS
        ]

    definition = PromptAgentDefinition(
        model=args.model,
        instructions=instructions,
        tools=tools,
    )

    client = _client()
    description = (
        "AgentOps tutorial support agent (degraded baseline)."
        if args.variant == "v2-degraded"
        else "AgentOps tutorial support agent (lookup_order, refund_order, escalate_to_human)."
    )

    last_exc: Exception | None = None
    version = None
    for attempt in range(1, 6):
        try:
            version = client.agents.create_version(
                agent_name=args.name,
                definition=definition,
                description=description,
            )
            break
        except (HttpResponseError, ServiceResponseError) as exc:
            status = getattr(exc, "status_code", None)
            transient = status is None or status >= 500 or status == 429
            print(
                f"create_version attempt {attempt}/5 failed (status={status}): {exc}",
                file=sys.stderr,
            )
            if status in (401, 403):
                # Don't retry on auth errors and surface a concrete fix.
                raise SystemExit(
                    "Foundry rejected the access token (HTTP "
                    f"{status}). Likely causes, in order of frequency:\n"
                    "  1. Stale Azure CLI token cache (very common when "
                    "this script worked earlier today and now suddenly "
                    "fails). Refresh with:\n"
                    "       az account clear\n"
                    "       az login\n"
                    "     If you have multiple tenants, add `--tenant <id>`.\n"
                    "  2. You're logged into the wrong Azure tenant. "
                    "Verify with `az account show` and re-login with "
                    "`az login --tenant <tenant-id>` if needed.\n"
                    "  3. Your account is missing the 'Azure AI User' "
                    "role on the Foundry account. Ask an admin (or run "
                    "yourself if you have permissions):\n"
                    "       az role assignment create \\\n"
                    "         --assignee <your-upn-or-object-id> \\\n"
                    "         --role 'Azure AI User' \\\n"
                    "         --scope <foundry-account-resource-id>\n"
                    "  4. AZURE_AI_FOUNDRY_PROJECT_ENDPOINT points at a "
                    "different project than the one where your role "
                    "assignment lives."
                ) from exc
            if not transient or attempt == 5:
                raise
            last_exc = exc
            time.sleep(min(2**attempt, 30))
    if version is None:
        raise SystemExit(f"create_version failed after retries: {last_exc!r}")

    version_id = getattr(version, "version", None) or getattr(version, "id", None)
    if not version_id:
        raise SystemExit(f"Could not determine version id from response: {version!r}")

    print(f"{args.name}:{version_id}")
    print(
        f"Created hosted agent {args.name}:{version_id} "
        f"(variant={args.variant}, model={args.model}).",
        file=sys.stderr,
    )

    # Read back the registered tools so the user can confirm they are
    # attached. The Foundry portal's Playground tab only surfaces tools
    # added through the portal's "Add" button — SDK-registered tools are
    # invisible there but DO get used at runtime. Printing them here
    # avoids the "I created the agent but Tools is empty" confusion.
    try:
        fetched = client.agents.get_version(
            agent_name=args.name, version=str(version_id)
        )
        definition = getattr(fetched, "definition", None)
        raw_tools = (
            getattr(definition, "tools", None)
            if definition is not None
            else None
        ) or []
        tool_names = []
        for t in raw_tools:
            n = getattr(t, "name", None)
            if n is None and isinstance(t, dict):
                n = t.get("name")
            if n:
                tool_names.append(n)
        if tool_names:
            print(
                f"Registered tools: {', '.join(tool_names)}",
                file=sys.stderr,
            )
    except Exception:  # noqa: BLE001 — read-back is best-effort
        pass

    print(
        "Paste the identifier above into the 'agent:' field of agentops.yaml.",
        file=sys.stderr,
    )
    print(
        "Note: the Foundry portal's Playground 'Tools' panel only lists "
        "tools added via the portal UI. SDK-registered tools (like these) "
        "show up under the agent's 'Code' / 'YAML' tab and ARE invoked "
        "at runtime — `agentops eval run` will exercise them.",
        file=sys.stderr,
    )
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    from azure.core.exceptions import ResourceNotFoundError

    client = _client()
    deleted = 0
    try:
        for v in client.agents.list_versions(agent_name=args.name):
            ver_id = getattr(v, "version", None) or getattr(v, "id", None)
            if ver_id is None:
                continue
            try:
                client.agents.delete_version(agent_name=args.name, version=str(ver_id))
                deleted += 1
            except ResourceNotFoundError:
                pass
    except ResourceNotFoundError:
        print(f"Agent {args.name} not found (already deleted).", file=sys.stderr)
        return 0

    try:
        client.agents.delete(agent_name=args.name)
    except ResourceNotFoundError:
        pass

    print(
        f"Deleted hosted agent {args.name} ({deleted} version(s)).",
        file=sys.stderr,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="Create the support agent.")
    p_create.add_argument("--name", required=True, help="Agent name, e.g. 'support-bot'.")
    p_create.add_argument(
        "--model",
        default="gpt-4o-mini",
        help="Model deployment to bind the agent to (default: gpt-4o-mini).",
    )
    p_create.add_argument(
        "--variant",
        choices=["v1-good", "v2-degraded"],
        default="v1-good",
        help=(
            "Which system prompt variant to register. v1-good is the "
            "tool-calling support assistant; v2-degraded is the friendly "
            "chatbot used to demonstrate regression detection."
        ),
    )
    p_create.set_defaults(func=cmd_create)

    p_delete = sub.add_parser("delete", help="Delete every version of the agent.")
    p_delete.add_argument("--name", required=True)
    p_delete.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
