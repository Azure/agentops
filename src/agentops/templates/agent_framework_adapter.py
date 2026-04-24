"""Agent Framework adapter for evaluating a pre-deployed Foundry Agent.

Wraps a pre-deployed Azure AI Foundry Agent using the Microsoft Agent
Framework SDK (agent_framework.foundry.FoundryAgent). The agent's
instructions, model, and hosted tools are configured on the service —
this adapter just connects and runs it.

For multi-agent workflows with dynamic agents, use multi_agent_workflow.py
instead.

Reference: github.com/microsoft/agent-framework/python/samples/02-agents/
           providers/foundry/foundry_agent_with_function_tools.py

Prerequisites:
  pip install agent-framework[foundry] azure-identity

Environment variables:
  AZURE_AI_FOUNDRY_PROJECT_ENDPOINT  — Foundry project endpoint
  AGENT_NAME     — name of the agent in Foundry (e.g. "WeatherAgent")
  AGENT_VERSION  — version of the agent (optional, e.g. "1.0")

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

logger = logging.getLogger(__name__)

PROJECT_ENDPOINT = os.environ.get("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", "")
AGENT_NAME = os.environ.get("AGENT_NAME", "")
AGENT_VERSION = os.environ.get("AGENT_VERSION", None)


async def _run_agent(input_text: str) -> dict[str, Any]:
    """Run a pre-deployed Foundry Agent via Microsoft Agent Framework."""
    from azure.identity import DefaultAzureCredential
    from agent_framework.foundry import FoundryAgent

    agent = FoundryAgent(
        project_endpoint=PROJECT_ENDPOINT,
        agent_name=AGENT_NAME,
        agent_version=AGENT_VERSION,
        credential=DefaultAzureCredential(),
    )

    result = await agent.run(input_text)

    # Extract response text from the last assistant message
    response_text = result.text or ""

    return {"response": response_text.strip()}


def run_evaluation(input_text: str, context: dict) -> dict:
    """Callable entry point for AgentOps evaluation.

    Invokes a pre-deployed Foundry Agent using Microsoft Agent Framework
    (FoundryAgent) and returns the response.
    """
    if not PROJECT_ENDPOINT or not AGENT_NAME:
        raise ValueError(
            "Set AZURE_AI_FOUNDRY_PROJECT_ENDPOINT and AGENT_NAME. "
            "Example: AGENT_NAME=my-agent AGENT_VERSION=1.0"
        )

    return asyncio.run(_run_agent(input_text))
