"""Agent Framework callable adapter for AgentOps evaluations.

Wraps an Azure AI Foundry Agent (created via the Agent Framework SDK)
so it can be evaluated locally through the AgentOps callable interface.

This adapter creates a thread, sends the input as a user message, runs the
agent, polls for completion, and returns the response along with any tool
calls made during execution.

Prerequisites:
  pip install azure-ai-projects azure-identity

Environment variables:
  AZURE_AI_FOUNDRY_PROJECT_ENDPOINT  — your Foundry project endpoint
    e.g. https://<account>.services.ai.azure.com/api/projects/<project>
  AGENT_ID  — the agent ID (e.g. "asst_abc123" or "my-agent:3")

Usage in run.yaml:
  target:
    type: agent
    hosting: local
    execution_mode: local
    framework: agent_framework
    local:
      callable: agent_framework_adapter:run_evaluation
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────
PROJECT_ENDPOINT = os.environ.get("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", "")
AGENT_ID = os.environ.get("AGENT_ID", "")
POLL_INTERVAL = float(os.environ.get("AGENT_POLL_INTERVAL", "2"))
MAX_POLL_ATTEMPTS = int(os.environ.get("AGENT_MAX_POLL_ATTEMPTS", "120"))

# Lazy-initialized client
_client = None


def _get_client():
    """Lazily initialize the AIProjectClient."""
    global _client
    if _client is None:
        if not PROJECT_ENDPOINT:
            raise ValueError(
                "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT must be set. "
                "Example: https://<account>.services.ai.azure.com/api/projects/<project>"
            )
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        _client = AIProjectClient(
            endpoint=PROJECT_ENDPOINT,
            credential=DefaultAzureCredential(),
        )
    return _client


def _invoke_via_threads_api(
    input_text: str,
    agent_id: str,
    api_version: str = "2025-05-01",
) -> dict[str, Any]:
    """Invoke an agent using the Threads/Runs REST API pattern.

    Used for agents with 'asst_*' IDs (Agent Service format).
    Flow: create thread → add message → create run → poll → get messages.
    """
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential()
    token = credential.get_token("https://ai.azure.com/.default").token

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    base_url = PROJECT_ENDPOINT

    import urllib.request

    def _api_call(method: str, path: str, body: dict | None = None) -> dict:
        url = f"{base_url}{path}?api-version={api_version}"
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    # Step 1: Create thread
    thread = _api_call("POST", "/threads", {})
    thread_id = thread["id"]

    # Step 2: Add user message
    _api_call("POST", f"/threads/{thread_id}/messages", {
        "role": "user",
        "content": input_text,
    })

    # Step 3: Create run
    run = _api_call("POST", f"/threads/{thread_id}/runs", {
        "assistant_id": agent_id,
    })
    run_id = run["id"]

    # Step 4: Poll for completion
    terminal_success = {"completed"}
    terminal_failure = {"failed", "cancelled", "expired", "requires_action"}

    for _ in range(MAX_POLL_ATTEMPTS):
        status_resp = _api_call("GET", f"/threads/{thread_id}/runs/{run_id}")
        status = status_resp.get("status", "")

        if status in terminal_success:
            break
        if status in terminal_failure:
            raise RuntimeError(f"Agent run ended with status '{status}'")
        time.sleep(POLL_INTERVAL)
    else:
        raise TimeoutError("Timed out waiting for agent run completion")

    # Step 5: Get messages and extract response
    messages = _api_call("GET", f"/threads/{thread_id}/messages")

    response_text = ""
    tool_calls = []

    for msg in messages.get("data", []):
        if msg.get("role") != "assistant":
            continue
        for content_block in msg.get("content", []):
            if content_block.get("type") == "text":
                text_val = content_block.get("text", {})
                if isinstance(text_val, dict):
                    response_text += text_val.get("value", "")
                else:
                    response_text += str(text_val)

    # Extract tool calls from run steps if available
    try:
        steps = _api_call("GET", f"/threads/{thread_id}/runs/{run_id}/steps")
        for step in steps.get("data", []):
            step_details = step.get("step_details", {})
            if step_details.get("type") == "tool_calls":
                for tc in step_details.get("tool_calls", []):
                    if tc.get("type") == "function":
                        fn = tc.get("function", {})
                        tool_calls.append({
                            "name": fn.get("name", ""),
                            "arguments": json.loads(fn.get("arguments", "{}")),
                        })
    except Exception:
        logger.debug("Could not retrieve run steps for tool calls", exc_info=True)

    return {"response": response_text.strip(), "tool_calls": tool_calls}


def _invoke_via_responses_api(
    input_text: str,
    agent_id: str,
    model: str | None = None,
    api_version: str = "2025-05-01",
) -> dict[str, Any]:
    """Invoke an agent using the Responses API with agent_reference.

    Used for named agents (e.g. 'FoundryAgent', 'my-agent:3') that are
    not in 'asst_*' format. Requires a model deployment name.
    """
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential()
    token = credential.get_token("https://ai.azure.com/.default").token

    model_name = model or os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")
    if not model_name:
        raise ValueError(
            "Agent reference mode requires a model deployment name. "
            "Set AZURE_OPENAI_DEPLOYMENT or AGENT_MODEL env var."
        )

    # Parse agent name and optional version (e.g., "my-agent:3")
    agent_name = agent_id
    agent_version = None
    if ":" in agent_id:
        agent_name, agent_version = agent_id.split(":", 1)
        agent_name = agent_name.strip()
        agent_version = agent_version.strip() or None

    agent_reference: dict[str, Any] = {
        "type": "agent_reference",
        "name": agent_name,
    }
    if agent_version:
        agent_reference["version"] = agent_version

    body = json.dumps({
        "model": model_name,
        "input": [{"role": "user", "content": input_text}],
        "agent_reference": agent_reference,
    }).encode()

    import urllib.request

    url = f"{PROJECT_ENDPOINT}/openai/v1/responses"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())

    # Extract response text and tool calls from Responses API output
    response_text = ""
    tool_calls = []

    for item in data.get("output", []):
        if item.get("type") == "message":
            for content_block in item.get("content", []):
                if content_block.get("type") == "output_text":
                    response_text += content_block.get("text", "")
        elif item.get("type") == "function_call":
            tool_calls.append({
                "name": item.get("name", ""),
                "arguments": json.loads(item.get("arguments", "{}")),
            })

    return {"response": response_text.strip(), "tool_calls": tool_calls}


def run_evaluation(input_text: str, context: dict) -> dict:
    """Evaluate a single input against an Agent Framework agent.

    This is the entry point called by AgentOps local adapter backend.
    It invokes the configured Foundry agent and returns the response
    along with any tool calls made during execution.

    Supports two agent ID formats:
      - 'asst_*': Uses the Threads/Runs API (Agent Service)
      - Named agents (e.g. 'FoundryAgent', 'my-agent:3'): Uses the
        Responses API with agent_reference

    Args:
        input_text: The user prompt from the dataset row.
        context: The full dataset row (all fields including tool_definitions).

    Returns:
        dict with at least {"response": "..."} and optionally {"tool_calls": [...]}.
    """
    agent_id = AGENT_ID
    if not agent_id:
        raise ValueError(
            "AGENT_ID must be set. Example: AGENT_ID=asst_abc123 or AGENT_ID=my-agent:3"
        )

    if agent_id.startswith("asst_"):
        return _invoke_via_threads_api(input_text, agent_id)
    else:
        model = os.environ.get("AGENT_MODEL") or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
        return _invoke_via_responses_api(input_text, agent_id, model=model)
