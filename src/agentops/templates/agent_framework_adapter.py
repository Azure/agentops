"""Agent Framework adapter for evaluating a single agent with tools.

Uses Microsoft Agent Framework Agent with FoundryChatClient to create
an agent with local @tool functions. Unlike FoundryAgent (which requires
tools declared server-side), this pattern defines tools entirely in code.

For multi-agent workflows with routing, use multi_agent_workflow.py.

Reference: github.com/microsoft/agent-framework/python/samples/
           03-workflows/_start-here/step2_agents_in_a_workflow.py

Prerequisites:
  pip install agent-framework[foundry] azure-identity

Environment variables:
  AZURE_AI_FOUNDRY_PROJECT_ENDPOINT  — Foundry project endpoint
  AZURE_OPENAI_DEPLOYMENT            — model deployment name

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
        from agent_framework.foundry import FoundryChatClient

        _client = FoundryChatClient(
            project_endpoint=PROJECT_ENDPOINT,
            model=MODEL,
            credential=DefaultAzureCredential(),
        )
    return _client


# ── Local tool implementations ─────────────────────────────────────────
# Replace these with your agent's actual tools.


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
def search_news(query: str, max_results: str = "5") -> str:
    """Search for recent news articles"""
    _captured_tool_calls.append({
        "name": "search_news",
        "arguments": {"query": query, "max_results": int(max_results)},
    })
    return f"Found {max_results} articles about '{query}'."


ALL_TOOLS = [get_weather, convert_currency, search_news]


async def _run_agent(input_text: str) -> dict[str, Any]:
    """Run a single agent with local @tool functions."""
    agent = Agent(
        client=_get_chat_client(),
        name="EvalAgent",
        instructions=(
            "You are a helpful assistant with tools. "
            "Use the appropriate tool to answer the user's query. "
            "Always call a tool before responding."
        ),
        tools=ALL_TOOLS,
    )

    _captured_tool_calls.clear()
    result: AgentResponse = await agent.run(input_text)

    response_text = result.text or ""

    return {
        "response": response_text.strip(),
        "tool_calls": list(_captured_tool_calls),
    }


def run_evaluation(input_text: str, context: dict) -> dict:
    """Callable entry point for AgentOps evaluation.

    Creates a single Agent with local @tool functions using
    Microsoft Agent Framework. Tool calls are captured and
    returned alongside the response for evaluator scoring.
    """
    if not PROJECT_ENDPOINT or not MODEL:
        raise ValueError(
            "Set AZURE_AI_FOUNDRY_PROJECT_ENDPOINT and AZURE_OPENAI_DEPLOYMENT"
        )

    return asyncio.run(_run_agent(input_text))
