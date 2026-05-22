"""AgentOps E2E hello-agent.

A Microsoft Agent Framework chat agent with **tool calling** exposed over
HTTP so the ``http-aca`` AgentOps scenario can exercise the http-json
invocation path against a real LLM that actually uses tools.

The agent is configured with one function tool, ``get_weather(location)``.
When the user asks about weather, the LLM picks the tool, the framework
executes it locally, the LLM observes the (canned) tool result and produces
a final natural-language answer. From AgentOps' perspective every request
is a single POST, but inside the agent there are multiple internal turns
(plan -> tool call -> tool result -> answer). This keeps the AgentOps
http-json contract simple while still exercising tool-call evaluation
metrics like ``tool_call_accuracy``.

Endpoints:
    GET  /        -> health check (``{"ok": true, "ready": <bool>}``)
    POST /        -> chat        (``{"message": "..."}`` ->
                                  ``{"text": "...", "tool_calls": [...]}``)

Auth:
    Azure OpenAI is reached via Microsoft Entra ID using
    ``DefaultAzureCredential``. In Azure Container Apps this resolves to
    the container's managed identity, which must be granted ``Cognitive
    Services OpenAI User`` on the AI Services / Foundry account.

Required environment:
    AZURE_OPENAI_ENDPOINT      e.g. https://<account>.openai.azure.com/
    AZURE_OPENAI_DEPLOYMENT    deployment name, e.g. ``gpt-4o-mini``

Optional:
    AZURE_CLIENT_ID            user-assigned managed identity client id
                               (DefaultAzureCredential picks it up
                               automatically when set).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("hello-agent")

INSTRUCTIONS = (
    "You are a concise factual assistant. "
    "When the user asks about the weather in a location, you MUST call the "
    "`get_weather` tool with that location instead of guessing. "
    "After the tool returns, summarize the weather for the user in one short "
    "sentence. For non-weather questions, answer directly in one short "
    "sentence with no caveats or follow-ups."
)

_agent = None
_credential = None


def _make_get_weather_tool():
    """Build the @tool-decorated get_weather function lazily.

    Defined inside a factory because the decorator import requires the
    agent_framework package to be available, which we do at lifespan time
    only (so the container can start even if deps are missing).
    """
    from agent_framework import FunctionInvocationContext, tool

    @tool(approval_mode="never_require")
    def get_weather(location: str, ctx: FunctionInvocationContext) -> str:
        """Get the current weather for a given location."""
        result_text = (
            f"It's 72°F (22°C) and partly cloudy in {location}, with light winds."
        )
        # Per-request list passed via function_invocation_kwargs so each
        # POST captures only its own tool calls (no global state).
        captured = ctx.kwargs.get("captured_calls")
        if isinstance(captured, list):
            captured.append({
                "type": "function_call",
                "name": "get_weather",
                "arguments": {"location": location},
                "result": result_text,
            })
        return result_text

    return get_weather


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the agent once at startup, close credential at shutdown."""
    global _agent, _credential
    from agent_framework import Agent
    from agent_framework.openai import OpenAIChatCompletionClient
    from azure.identity.aio import DefaultAzureCredential

    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT")

    if not endpoint or not deployment:
        log.warning(
            "AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_DEPLOYMENT not set — agent "
            "will return 503 on POST until configured."
        )
        yield
        return

    _credential = DefaultAzureCredential()
    _agent = Agent(
        client=OpenAIChatCompletionClient(
            model=deployment,
            azure_endpoint=endpoint,
            credential=_credential,
        ),
        instructions=INSTRUCTIONS,
        tools=[_make_get_weather_tool()],
    )
    log.info(
        "Agent initialized (endpoint=%s, deployment=%s, tools=[get_weather])",
        endpoint, deployment,
    )
    try:
        yield
    finally:
        if _credential is not None:
            await _credential.close()


app = FastAPI(title="agentops-e2e-hello-agent", lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str


@app.get("/")
async def root():
    return {
        "ok": True,
        "agent": "agentops-e2e-hello-agent",
        "ready": _agent is not None,
        "tools": ["get_weather"] if _agent is not None else [],
    }


def _extract_text(result: Any) -> str:
    for attr in ("text", "content", "message"):
        value = getattr(result, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return str(result)


@app.post("/")
async def chat(req: ChatRequest):
    if _agent is None:
        raise HTTPException(
            status_code=503,
            detail="agent not initialized; check AZURE_OPENAI_ENDPOINT/DEPLOYMENT and managed identity role",
        )
    captured_calls: list[dict] = []
    try:
        result = await _agent.run(
            req.message,
            function_invocation_kwargs={"captured_calls": captured_calls},
        )
    except Exception as exc:  # noqa: BLE001 — surface real error to caller
        log.exception("agent.run failed")
        raise HTTPException(status_code=500, detail=f"agent.run failed: {exc}") from exc

    return {"text": _extract_text(result), "tool_calls": captured_calls}
