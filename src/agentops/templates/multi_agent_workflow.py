"""Multi-agent workflow using Azure AI Agent Framework (azure-ai-agents).

Demonstrates a router-to-specialist pattern where:
1. A Router Agent analyzes the query and picks the right specialist
2. The selected Specialist Agent has Python function tools
3. Agent Framework auto-executes tool calls via enable_auto_function_calls
4. Tool calls are captured from run steps for evaluation

All agents are created dynamically via AgentsClient and cleaned up after use.

Prerequisites:
  pip install azure-ai-agents azure-identity

Environment variables:
  AZURE_AI_FOUNDRY_PROJECT_ENDPOINT  — Foundry project endpoint
  AZURE_OPENAI_DEPLOYMENT            — model deployment name (e.g. gpt-5.1)

Usage in run.yaml:
  target:
    type: agent
    hosting: local
    execution_mode: local
    framework: agent_framework
    local:
      callable: multi_agent_workflow:run_evaluation
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ENDPOINT = os.environ.get("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", "")
MODEL = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")

_client = None
_token: str | None = None


def _get_client():
    """Lazily initialize the AgentsClient."""
    global _client
    if _client is None:
        from azure.ai.agents import AgentsClient
        from azure.identity import DefaultAzureCredential

        _client = AgentsClient(
            endpoint=PROJECT_ENDPOINT,
            credential=DefaultAzureCredential(),
        )
    return _client


def _get_token() -> str:
    """Get a bearer token for REST API calls (messages, run steps)."""
    global _token
    from azure.identity import DefaultAzureCredential

    _token = DefaultAzureCredential().get_token(
        "https://ai.azure.com/.default"
    ).token
    return _token


def _rest_get(path: str) -> dict:
    """GET request to the Foundry project API."""
    url = f"{PROJECT_ENDPOINT}{path}?api-version=2025-05-01"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {_get_token()}"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


# ── Tool functions for specialist agents ───────────────────────────────
# Agent Framework uses real Python functions (not JSON schemas).
# FunctionTool wraps these and auto-executes them when the agent
# makes a tool call via enable_auto_function_calls().


def get_weather(city: str) -> str:
    """Get current weather for a city"""
    return f"Current weather in {city}: 55°F, partly cloudy."


def convert_currency(amount: str, from_currency: str, to_currency: str) -> str:
    """Convert an amount from one currency to another"""
    amt = float(amount)
    return f"{amt} {from_currency} = {amt * 0.92:.2f} {to_currency}"


def calculate_compound_interest(principal: str, rate: str, years: str) -> str:
    """Calculate compound interest"""
    p, r, y = float(principal), float(rate) / 100, int(float(years))
    total = p * ((1 + r) ** y)
    interest = total - p
    return f"Compound interest: ${interest:,.2f}, total: ${total:,.2f}"


def search_news(query: str, max_results: str = "5") -> str:
    """Search for recent news articles"""
    return f"Found {max_results} articles about '{query}'."


def search_flights(origin: str, destination: str, date: str) -> str:
    """Search for available flights"""
    return f"Found 3 flights from {origin} to {destination} on {date}."


# ── Specialist configurations ──────────────────────────────────────────

SPECIALISTS: dict[str, dict[str, Any]] = {
    "weather": {
        "name": "WeatherSpecialist",
        "instructions": (
            "You are a weather specialist. Use the get_weather tool "
            "to answer weather queries. Always call the tool before responding."
        ),
        "functions": [get_weather],
    },
    "finance": {
        "name": "FinanceSpecialist",
        "instructions": (
            "You are a finance specialist. Use convert_currency or "
            "calculate_compound_interest tools as needed. Always call "
            "the appropriate tool before responding."
        ),
        "functions": [convert_currency, calculate_compound_interest],
    },
    "search": {
        "name": "SearchSpecialist",
        "instructions": (
            "You are a search specialist. Use search_news or search_flights "
            "tools as needed. Always call the appropriate tool before responding."
        ),
        "functions": [search_news, search_flights],
    },
}


def _extract_response_and_tool_calls(
    thread_id: str, run_id: str
) -> dict[str, Any]:
    """Extract response text and tool calls from a completed run."""
    # Get assistant messages
    msgs = _rest_get(f"/threads/{thread_id}/messages")
    response_text = ""
    for msg in msgs.get("data", []):
        if msg.get("role") == "assistant":
            for block in msg.get("content", []):
                if block.get("type") == "text":
                    val = block.get("text", {})
                    response_text += (
                        val.get("value", "") if isinstance(val, dict) else str(val)
                    )
            break

    # Get tool calls from run steps
    steps = _rest_get(f"/threads/{thread_id}/runs/{run_id}/steps")
    tool_calls: list[dict[str, Any]] = []
    for step in steps.get("data", []):
        details = step.get("step_details", {})
        if details.get("type") == "tool_calls":
            for tc in details.get("tool_calls", []):
                if tc.get("type") == "function":
                    fn = tc.get("function", {})
                    tool_calls.append({
                        "name": fn.get("name", ""),
                        "arguments": json.loads(fn.get("arguments", "{}")),
                    })

    return {"response": response_text.strip(), "tool_calls": tool_calls}


def _route_query(client: Any, router_id: str, query: str) -> str:
    """Use the Router Agent to determine which specialist to use."""
    from azure.ai.agents.models import ThreadMessageOptions

    result = client.create_thread_and_process_run(
        agent_id=router_id,
        thread={
            "messages": [
                ThreadMessageOptions(role="user", content=query)
            ]
        },
    )

    msgs = _rest_get(f"/threads/{result.thread_id}/messages")
    routing_decision = ""
    for msg in msgs.get("data", []):
        if msg.get("role") == "assistant":
            for block in msg.get("content", []):
                if block.get("type") == "text":
                    val = block.get("text", {})
                    routing_decision = (
                        val.get("value", "").strip().lower()
                        if isinstance(val, dict)
                        else str(val).strip().lower()
                    )
            break

    if "weather" in routing_decision:
        return "weather"
    if any(k in routing_decision for k in ("finance", "currency", "interest")):
        return "finance"
    return "search"


def run_evaluation(input_text: str, context: dict) -> dict:
    """Multi-agent workflow entry point for AgentOps evaluation.

    Orchestrates: Router Agent → Specialist Agent (with tools) → Response.
    All agents are created dynamically via Agent Framework SDK
    (azure-ai-agents AgentsClient) and cleaned up after each call.
    """
    if not PROJECT_ENDPOINT or not MODEL:
        raise ValueError(
            "Set AZURE_AI_FOUNDRY_PROJECT_ENDPOINT and AZURE_OPENAI_DEPLOYMENT"
        )

    from azure.ai.agents.models import FunctionTool, ThreadMessageOptions, ToolSet

    client = _get_client()
    created_agents: list[str] = []

    try:
        # Step 1: Create Router Agent (no tools, just routing logic)
        router = client.create_agent(
            model=MODEL,
            name="RouterAgent",
            instructions=(
                "You are a routing agent. Given a user query, respond with ONLY "
                "one word indicating the specialist to handle it:\n"
                "- 'weather' for weather queries\n"
                "- 'finance' for currency conversion or interest calculations\n"
                "- 'search' for news, flights, or general search queries\n"
                "Respond with only the category word, nothing else."
            ),
        )
        created_agents.append(router.id)
        logger.info("Created Router Agent: %s", router.id)

        # Step 2: Route the query
        specialist_key = _route_query(client, router.id, input_text)
        logger.info("Router selected specialist: %s", specialist_key)

        # Step 3: Create Specialist Agent with FunctionTool
        spec = SPECIALISTS[specialist_key]
        functions = FunctionTool(functions=spec["functions"])
        toolset = ToolSet()
        toolset.add(functions)

        specialist = client.create_agent(
            model=MODEL,
            name=str(spec["name"]),
            instructions=str(spec["instructions"]),
            toolset=toolset,
        )
        created_agents.append(specialist.id)
        logger.info(
            "Created Specialist: %s (%s)", spec["name"], specialist.id
        )

        # Step 4: Enable auto function calls and run
        client.enable_auto_function_calls(toolset)

        result = client.create_thread_and_process_run(
            agent_id=specialist.id,
            thread={
                "messages": [
                    ThreadMessageOptions(role="user", content=input_text)
                ]
            },
        )
        logger.info("Specialist run completed: %s", result.status)

        # Step 5: Extract response and tool calls
        output = _extract_response_and_tool_calls(
            result.thread_id, result.id
        )
        logger.info(
            "Result: response=%d chars, tool_calls=%d",
            len(output["response"]),
            len(output["tool_calls"]),
        )
        return output

    finally:
        # Cleanup all created agents
        for agent_id in created_agents:
            try:
                client.delete_agent(agent_id)
                logger.debug("Deleted agent: %s", agent_id)
            except Exception:
                logger.debug("Failed to delete agent: %s", agent_id, exc_info=True)
