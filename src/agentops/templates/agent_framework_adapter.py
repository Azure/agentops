"""Agent Framework adapter for evaluating a single Foundry Agent with tools.

Wraps a pre-deployed Azure AI Foundry Agent using the Microsoft Agent
Framework SDK (FoundryAgent) with local @tool functions for tool call
capture. The agent's instructions and model are configured on the
service; local tools provide the implementations that Agent Framework
auto-executes.

For multi-agent workflows with routing, use multi_agent_workflow.py.

Reference: github.com/microsoft/agent-framework/python/samples/02-agents/
           providers/foundry/foundry_agent_with_function_tools.py

Prerequisites:
  pip install agent-framework[foundry] azure-identity

Environment variables:
  AZURE_AI_FOUNDRY_PROJECT_ENDPOINT  — Foundry project endpoint
  AGENT_NAME     — name of the agent in Foundry
  AGENT_VERSION  — version of the agent (optional)

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

from agent_framework import AgentResponse, tool

logger = logging.getLogger(__name__)

PROJECT_ENDPOINT = os.environ.get("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", "")
AGENT_NAME = os.environ.get("AGENT_NAME", "")
AGENT_VERSION = os.environ.get("AGENT_VERSION", None)

_captured_tool_calls: list[dict[str, Any]] = []


# ── Local tool implementations ─────────────────────────────────────────
# These must match the tool declarations on the Foundry agent.
# Agent Framework auto-executes them when the agent makes a tool call.
# Replace or extend these with your agent's actual tools.


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
    """Run a pre-deployed Foundry Agent with local tool implementations."""
    from azure.identity import DefaultAzureCredential
    from agent_framework.foundry import FoundryAgent

    agent = FoundryAgent(
        project_endpoint=PROJECT_ENDPOINT,
        agent_name=AGENT_NAME,
        agent_version=AGENT_VERSION,
        credential=DefaultAzureCredential(),
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

    Invokes a pre-deployed Foundry Agent with local @tool functions.
    Agent Framework auto-executes tools; invocations are captured
    and returned alongside the response for evaluator scoring.
    """
    if not PROJECT_ENDPOINT or not AGENT_NAME:
        raise ValueError(
            "Set AZURE_AI_FOUNDRY_PROJECT_ENDPOINT and AGENT_NAME. "
            "Example: AGENT_NAME=my-agent AGENT_VERSION=1.0"
        )

    return asyncio.run(_run_agent(input_text))
