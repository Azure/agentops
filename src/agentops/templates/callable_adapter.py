"""Callable adapter template for AgentOps evaluations.

This module shows the expected function signature for a callable adapter.
Replace the body with your own logic — e.g. run an Agent Framework workflow,
call a LangChain chain, invoke a custom pipeline, etc.

Usage in run.yaml:
  target:
    execution_mode: local
    local:
      callable: my_module:run_evaluation

The function receives two arguments:
  - input_text (str): the user prompt from the dataset row
  - context (dict): the full dataset row (all fields)

It must return a dict with at least a "response" key:
  {"response": "the model/agent output text"}
"""
from __future__ import annotations


def run_evaluation(input_text: str, context: dict) -> dict:
    """Run a single evaluation turn and return the response.

    Replace this implementation with your own logic.
    """
    # Example: echo the input back (like the subprocess fake adapter).
    # In practice you would call your agent/model here:
    #
    #   from my_agent import workflow
    #   result = workflow.invoke(input_text)
    #   return {"response": result.output}
    #
    return {"response": input_text}
