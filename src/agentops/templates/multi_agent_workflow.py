"""Minimal multi-agent workflow using Azure AI Agent Framework.

Demonstrates a router→specialist pattern where:
1. A Router Agent analyzes the query and picks the right specialist
2. The selected Specialist Agent has tools and processes the query
3. Tool calls are captured from the run steps

All agents are created dynamically via the Agent Framework SDK
(azure-ai-projects + OpenAI Assistants API) and cleaned up after use.

Prerequisites:
  pip install azure-ai-projects azure-identity openai

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
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ENDPOINT = os.environ.get("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", "")
MODEL = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")

# Lazy-initialized client
_oai_client = None


def _get_openai_client():
    """Get an AzureOpenAI client for the Assistants API."""
    global _oai_client
    if _oai_client is None:
        from azure.identity import DefaultAzureCredential
        from openai import AzureOpenAI

        cred = DefaultAzureCredential()
        token = cred.get_token("https://cognitiveservices.azure.com/.default")

        # Extract base endpoint (without /api/projects/... path)
        endpoint = PROJECT_ENDPOINT.split("/api/")[0] if "/api/" in PROJECT_ENDPOINT else PROJECT_ENDPOINT

        _oai_client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_version="2025-03-01-preview",
            azure_ad_token=token.token,
        )
    return _oai_client


# ── Tool definitions for specialist agents ─────────────────────────────

WEATHER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]

FINANCE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "convert_currency",
            "description": "Convert an amount from one currency to another",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number"},
                    "from_currency": {"type": "string"},
                    "to_currency": {"type": "string"},
                },
                "required": ["amount", "from_currency", "to_currency"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_compound_interest",
            "description": "Calculate compound interest",
            "parameters": {
                "type": "object",
                "properties": {
                    "principal": {"type": "number"},
                    "rate": {"type": "number"},
                    "years": {"type": "integer"},
                },
                "required": ["principal", "rate", "years"],
            },
        },
    },
]

SEARCH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_news",
            "description": "Search for recent news articles",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_flights",
            "description": "Search for available flights",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string"},
                    "destination": {"type": "string"},
                    "date": {"type": "string"},
                },
                "required": ["origin", "destination", "date"],
            },
        },
    },
]

# Simulated tool results
TOOL_RESULTS = {
    "get_weather": lambda args: f"Current weather in {args.get('city', 'unknown')}: 55°F, partly cloudy.",
    "convert_currency": lambda args: f"{args.get('amount', 0)} {args.get('from_currency', '')} = {args.get('amount', 0) * 0.92:.2f} {args.get('to_currency', '')}",
    "calculate_compound_interest": lambda args: f"Compound interest: ${args.get('principal', 0) * ((1 + args.get('rate', 0)) ** args.get('years', 0) - 1):,.2f}",
    "search_news": lambda args: f"Found 5 articles about '{args.get('query', '')}'.",
    "search_flights": lambda args: f"Found 3 flights from {args.get('origin', '')} to {args.get('destination', '')} on {args.get('date', '')}.",
}

# ── Specialist definitions ─────────────────────────────────────────────

SPECIALISTS = {
    "weather": {
        "name": "WeatherSpecialist",
        "instructions": "You are a weather specialist. Use the get_weather tool to answer weather queries. Always call the tool before responding.",
        "tools": WEATHER_TOOLS,
    },
    "finance": {
        "name": "FinanceSpecialist",
        "instructions": "You are a finance specialist. Use convert_currency or calculate_compound_interest tools as needed. Always call the appropriate tool before responding.",
        "tools": FINANCE_TOOLS,
    },
    "search": {
        "name": "SearchSpecialist",
        "instructions": "You are a search specialist. Use search_news or search_flights tools as needed. Always call the appropriate tool before responding.",
        "tools": SEARCH_TOOLS,
    },
}


def _create_agent(client, name: str, instructions: str, tools: list) -> Any:
    """Create an Agent Framework agent via the Assistants API."""
    return client.beta.assistants.create(
        model=MODEL,
        name=name,
        instructions=instructions,
        tools=tools,
    )


def _run_agent_with_tools(client, agent_id: str, query: str) -> dict:
    """Run an agent, handle tool calls, and return response + tool_calls."""
    thread = client.beta.threads.create()
    client.beta.threads.messages.create(
        thread_id=thread.id, role="user", content=query
    )

    run = client.beta.threads.runs.create_and_poll(
        thread_id=thread.id,
        assistant_id=agent_id,
    )

    all_tool_calls = []

    # Handle tool calls if the agent requested them
    while run.status == "requires_action":
        tool_outputs = []
        for tc in run.required_action.submit_tool_outputs.tool_calls:
            fn_name = tc.function.name
            fn_args = json.loads(tc.function.arguments)
            all_tool_calls.append({"name": fn_name, "arguments": fn_args})

            handler = TOOL_RESULTS.get(fn_name)
            result = handler(fn_args) if handler else f"Unknown tool: {fn_name}"
            tool_outputs.append({"tool_call_id": tc.id, "output": result})

        run = client.beta.threads.runs.submit_tool_outputs_and_poll(
            thread_id=thread.id, run_id=run.id, tool_outputs=tool_outputs
        )

    # Extract final response
    messages = client.beta.threads.messages.list(thread_id=thread.id)
    response_text = ""
    for msg in messages.data:
        if msg.role == "assistant":
            for block in msg.content:
                if block.type == "text":
                    response_text += block.text.value
            break

    # Cleanup thread
    client.beta.threads.delete(thread.id)

    return {"response": response_text, "tool_calls": all_tool_calls}


def _route_query(client, router_id: str, query: str) -> str:
    """Use the Router Agent to determine which specialist to use."""
    thread = client.beta.threads.create()
    client.beta.threads.messages.create(
        thread_id=thread.id, role="user", content=query
    )

    client.beta.threads.runs.create_and_poll(
        thread_id=thread.id, assistant_id=router_id
    )

    messages = client.beta.threads.messages.list(thread_id=thread.id)
    routing_decision = ""
    for msg in messages.data:
        if msg.role == "assistant":
            for block in msg.content:
                if block.type == "text":
                    routing_decision = block.text.value.strip().lower()
            break

    client.beta.threads.delete(thread.id)

    # Parse routing decision
    if "weather" in routing_decision:
        return "weather"
    elif "finance" in routing_decision or "currency" in routing_decision or "interest" in routing_decision:
        return "finance"
    else:
        return "search"


def run_evaluation(input_text: str, context: dict) -> dict:
    """Multi-agent workflow entry point for AgentOps evaluation.

    Orchestrates: Router Agent → Specialist Agent (with tools) → Response.
    All agents are created dynamically and cleaned up after each call.
    """
    if not PROJECT_ENDPOINT or not MODEL:
        raise ValueError(
            "Set AZURE_AI_FOUNDRY_PROJECT_ENDPOINT and AZURE_OPENAI_DEPLOYMENT"
        )

    client = _get_openai_client()
    created_agents = []

    try:
        # Step 1: Create Router Agent
        router = _create_agent(
            client,
            name="RouterAgent",
            instructions=(
                "You are a routing agent. Given a user query, respond with ONLY "
                "one word indicating the specialist to handle it:\n"
                "- 'weather' for weather queries\n"
                "- 'finance' for currency conversion or interest calculations\n"
                "- 'search' for news, flights, or general search queries\n"
                "Respond with only the category word, nothing else."
            ),
            tools=[],
        )
        created_agents.append(router.id)
        logger.info("Created Router Agent: %s", router.id)

        # Step 2: Route the query
        specialist_key = _route_query(client, router.id, input_text)
        logger.info("Router selected specialist: %s", specialist_key)

        # Step 3: Create the selected Specialist Agent
        spec_config = SPECIALISTS[specialist_key]
        specialist = _create_agent(
            client,
            name=spec_config["name"],
            instructions=spec_config["instructions"],
            tools=spec_config["tools"],
        )
        created_agents.append(specialist.id)
        logger.info("Created Specialist Agent: %s (%s)", spec_config["name"], specialist.id)

        # Step 4: Run the specialist with tool handling
        result = _run_agent_with_tools(client, specialist.id, input_text)
        logger.info(
            "Specialist returned: response=%d chars, tool_calls=%d",
            len(result["response"]),
            len(result["tool_calls"]),
        )

        return result

    finally:
        # Step 5: Cleanup — delete all created agents
        for agent_id in created_agents:
            try:
                client.beta.assistants.delete(agent_id)
                logger.debug("Deleted agent: %s", agent_id)
            except Exception:
                logger.debug("Failed to delete agent: %s", agent_id, exc_info=True)
