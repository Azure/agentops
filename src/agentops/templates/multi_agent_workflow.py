"""Multi-agent workflow using Microsoft Agent Framework.

Demonstrates a router-to-specialist pattern following the official
Agent Framework workflow samples (microsoft/agent-framework):

  Router Agent → Coordinator (custom Executor) → Specialist Agent

The Coordinator examines the Router's output and forwards the original
user query to the correct Specialist Agent. Each specialist has @tool
functions that Agent Framework auto-executes.

Reference: github.com/microsoft/agent-framework/python/samples/03-workflows/

Prerequisites:
  pip install agent-framework[foundry] azure-identity

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

from agent_framework import (
    Agent,
    AgentExecutor,
    AgentExecutorRequest,
    AgentExecutorResponse,
    AgentResponse,
    Executor,
    Message,
    WorkflowBuilder,
    WorkflowContext,
    handler,
    tool,
)

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


# ── Tool functions (decorated with @tool for Agent Framework) ──────────


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


# ── Coordinator Executor ──────────────────────────────────────────────
# Routes the user query to the correct specialist based on the Router's
# classification. Follows the official Coordinator pattern from
# microsoft/agent-framework samples.


class RoutingCoordinator(Executor):
    """Routes between Router Agent and Specialist Agents."""

    SPECIALIST_IDS = {
        "weather": "weather_specialist",
        "finance": "finance_specialist",
        "search": "search_specialist",
    }

    def __init__(self) -> None:
        super().__init__(id="coordinator")

    @handler
    async def on_agent_response(
        self,
        response: AgentExecutorResponse,
        ctx: WorkflowContext[AgentExecutorRequest, AgentResponse],
    ) -> None:
        """Handle responses from Router and Specialist agents."""
        if response.executor_id != "router":
            # Specialist response — yield as workflow output
            await ctx.yield_output(response.agent_response)
            return

        # Router response — parse routing decision and forward to specialist
        routing_text = response.agent_response.text.strip().lower()

        if "weather" in routing_text:
            target = "weather_specialist"
        elif any(k in routing_text for k in ("finance", "currency", "interest")):
            target = "finance_specialist"
        else:
            target = "search_specialist"

        logger.info("Coordinator routing to: %s (router said: %s)", target, routing_text)

        # Forward the original user query to the specialist
        original_messages = list(response.full_conversation)
        user_query = ""
        for msg in original_messages:
            if msg.role == "user":
                user_query = msg.text or ""
                break

        await ctx.send_message(
            AgentExecutorRequest(
                messages=[Message("user", contents=[user_query])],
                should_respond=True,
            ),
            target_id=target,
        )


def _build_workflow():
    """Build the multi-agent workflow with Router → Coordinator → Specialists."""
    client = _get_chat_client()

    # Create agents
    router = AgentExecutor(Agent(
        client=client,
        name="router",
        instructions=(
            "You are a routing agent. Analyze the user's query and respond "
            "with ONLY one word:\n"
            "- 'weather' for weather queries\n"
            "- 'finance' for currency or interest calculations\n"
            "- 'search' for news, flights, or general queries\n"
            "Respond with only the category word, nothing else."
        ),
    ))

    weather = AgentExecutor(Agent(
        client=client,
        name="weather_specialist",
        instructions="Use the get_weather tool to answer weather queries.",
        tools=[get_weather],
    ))

    finance = AgentExecutor(Agent(
        client=client,
        name="finance_specialist",
        instructions=(
            "Use convert_currency or calculate_compound_interest tools as needed."
        ),
        tools=[convert_currency, calculate_compound_interest],
    ))

    search = AgentExecutor(Agent(
        client=client,
        name="search_specialist",
        instructions="Use search_news or search_flights tools as needed.",
        tools=[search_news, search_flights],
    ))

    coordinator = RoutingCoordinator()

    # Build workflow: Router → Coordinator ↔ Specialists
    workflow = (
        WorkflowBuilder(start_executor=router)
        # Router output goes to Coordinator
        .add_edge(router, coordinator)
        # Coordinator can route to any specialist
        .add_edge(coordinator, weather)
        .add_edge(coordinator, finance)
        .add_edge(coordinator, search)
        # Specialist output goes back to Coordinator (which yields output)
        .add_edge(weather, coordinator)
        .add_edge(finance, coordinator)
        .add_edge(search, coordinator)
        .build()
    )

    return workflow


async def _run_workflow(input_text: str) -> dict[str, Any]:
    """Run the multi-agent workflow for a single query."""
    workflow = _build_workflow()

    _captured_tool_calls.clear()
    events = await workflow.run(input_text)

    # Extract the final response from workflow outputs
    response_text = ""
    outputs = events.get_outputs()
    for output in outputs:
        if isinstance(output, AgentResponse) and output.text:
            response_text = output.text

    return {
        "response": response_text.strip(),
        "tool_calls": list(_captured_tool_calls),
    }


def run_evaluation(input_text: str, context: dict) -> dict:
    """Multi-agent workflow entry point for AgentOps evaluation.

    Uses Microsoft Agent Framework WorkflowBuilder with:
      Router Agent → RoutingCoordinator → Specialist Agents (@tool)
    """
    if not PROJECT_ENDPOINT or not MODEL:
        raise ValueError(
            "Set AZURE_AI_FOUNDRY_PROJECT_ENDPOINT and AZURE_OPENAI_DEPLOYMENT"
        )

    return asyncio.run(_run_workflow(input_text))
