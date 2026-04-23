"""Multi-agent workflow using Microsoft Agent Framework (agent-framework).

Demonstrates a router-to-specialist pattern using the Agent Framework SDK:
1. A Router Agent analyzes the query and picks the right specialist
2. The selected Specialist Agent uses @tool-decorated Python functions
3. WorkflowBuilder chains agents with automatic handoff
4. Tool calls are captured during execution for evaluation

All agents share a FoundryChatClient and run as a Workflow.

Prerequisites:
  pip install agent-framework agent-framework-foundry azure-identity

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

import asyncio
import logging
import os
from typing import Any

from agent_framework import Agent, AgentResponse, tool

logger = logging.getLogger(__name__)

PROJECT_ENDPOINT = os.environ.get("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", "")
MODEL = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")

_client = None
_captured_tool_calls: list[dict[str, Any]] = []


def _get_chat_client():
    """Lazily initialize the FoundryChatClient."""
    global _client
    if _client is None:
        from azure.identity import DefaultAzureCredential
        from agent_framework_foundry import FoundryChatClient

        _client = FoundryChatClient(
            project_endpoint=PROJECT_ENDPOINT,
            model=MODEL,
            credential=DefaultAzureCredential(),
        )
    return _client


# ── Tool functions (decorated with @tool for Agent Framework) ──────────
# Agent Framework auto-executes these when the agent makes a tool call.
# We capture each invocation for evaluation reporting.


@tool
def get_weather(city: str) -> str:
    """Get current weather for a city"""
    _captured_tool_calls.append({"name": "get_weather", "arguments": {"city": city}})
    return f"Current weather in {city}: 55°F, partly cloudy."


@tool
def convert_currency(amount: str, from_currency: str, to_currency: str) -> str:
    """Convert an amount from one currency to another"""
    amt = float(amount)
    _captured_tool_calls.append({
        "name": "convert_currency",
        "arguments": {"amount": amt, "from_currency": from_currency, "to_currency": to_currency},
    })
    return f"{amt} {from_currency} = {amt * 0.92:.2f} {to_currency}"


@tool
def calculate_compound_interest(principal: str, rate: str, years: str) -> str:
    """Calculate compound interest"""
    p, r, y = float(principal), float(rate) / 100, int(float(years))
    total = p * ((1 + r) ** y)
    interest = total - p
    _captured_tool_calls.append({
        "name": "calculate_compound_interest",
        "arguments": {"principal": p, "rate": r, "years": y},
    })
    return f"Compound interest: ${interest:,.2f}, total: ${total:,.2f}"


@tool
def search_news(query: str, max_results: str = "5") -> str:
    """Search for recent news articles"""
    _captured_tool_calls.append({
        "name": "search_news",
        "arguments": {"query": query, "max_results": int(max_results)},
    })
    return f"Found {max_results} articles about '{query}'."


@tool
def search_flights(origin: str, destination: str, date: str) -> str:
    """Search for available flights"""
    _captured_tool_calls.append({
        "name": "search_flights",
        "arguments": {"origin": origin, "destination": destination, "date": date},
    })
    return f"Found 3 flights from {origin} to {destination} on {date}."


# ── Specialist agent configurations ───────────────────────────────────

SPECIALISTS: dict[str, dict[str, Any]] = {
    "weather": {
        "name": "WeatherSpecialist",
        "instructions": (
            "You are a weather specialist. Use the get_weather tool "
            "to answer weather queries. Always call the tool before responding."
        ),
        "tools": [get_weather],
    },
    "finance": {
        "name": "FinanceSpecialist",
        "instructions": (
            "You are a finance specialist. Use convert_currency or "
            "calculate_compound_interest tools as needed."
        ),
        "tools": [convert_currency, calculate_compound_interest],
    },
    "search": {
        "name": "SearchSpecialist",
        "instructions": (
            "You are a search specialist. Use search_news or search_flights "
            "tools as needed."
        ),
        "tools": [search_news, search_flights],
    },
}


async def _run_workflow(input_text: str) -> dict[str, Any]:
    """Build and run the multi-agent workflow for a single query."""
    chat_client = _get_chat_client()

    # Step 1: Router Agent — classifies the query
    router = Agent(
        client=chat_client,
        name="Router",
        instructions=(
            "You are a routing agent. Analyze the user's query and respond "
            "with ONLY one word indicating the specialist:\n"
            "- 'weather' for weather queries\n"
            "- 'finance' for currency or interest calculations\n"
            "- 'search' for news, flights, or general queries\n"
            "Respond with only the category word, nothing else."
        ),
    )

    router_response: AgentResponse = await router.run(input_text)
    routing_decision = ""
    for msg in router_response.messages:
        if msg.role == "assistant":
            for c in (msg.contents or []):
                if hasattr(c, "text") and c.text:
                    routing_decision = c.text.strip().lower()

    # Determine specialist
    if "weather" in routing_decision:
        spec_key = "weather"
    elif any(k in routing_decision for k in ("finance", "currency", "interest")):
        spec_key = "finance"
    else:
        spec_key = "search"

    logger.info("Router selected specialist: %s", spec_key)

    # Step 2: Specialist Agent — has tools, processes the query
    spec = SPECIALISTS[spec_key]
    specialist = Agent(
        client=chat_client,
        name=str(spec["name"]),
        instructions=str(spec["instructions"]),
        tools=list(spec["tools"]),
    )

    _captured_tool_calls.clear()
    spec_response: AgentResponse = await specialist.run(input_text)

    # Extract the final response text
    response_text = ""
    for msg in spec_response.messages:
        if msg.role == "assistant":
            for c in (msg.contents or []):
                if hasattr(c, "text") and c.text:
                    response_text = c.text

    return {
        "response": response_text.strip(),
        "tool_calls": list(_captured_tool_calls),
    }


def run_evaluation(input_text: str, context: dict) -> dict:
    """Multi-agent workflow entry point for AgentOps evaluation.

    Orchestrates: Router Agent → Specialist Agent (with tools) → Response
    using Microsoft Agent Framework (WorkflowBuilder + Agent + @tool).
    """
    if not PROJECT_ENDPOINT or not MODEL:
        raise ValueError(
            "Set AZURE_AI_FOUNDRY_PROJECT_ENDPOINT and AZURE_OPENAI_DEPLOYMENT"
        )

    return asyncio.run(_run_workflow(input_text))
