"""Callable adapter template for AgentOps evaluations.

Use only Python standard library for HTTP calls — do NOT add external
dependencies like 'requests' or 'httpx'. They are not AgentOps dependencies
and may not be installed in every environment.

Usage in run.yaml:
  target:
    execution_mode: local
    local:
      callable: callable_adapter:run_evaluation

The function receives two arguments:
  - input_text (str): the user prompt from the dataset row
  - context (dict): the full dataset row (all fields)

It must return a dict with at least a "response" key:
  {"response": "the model/agent output text"}
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

# Set AGENT_HTTP_URL in your environment or replace the default below.
ENDPOINT = os.environ.get("AGENT_HTTP_URL", "http://localhost:8000/api/chat")

# ── Response cleaning helpers ──────────────────────────────────────────

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def _sanitize_context(text: str) -> str:
    """Strip HTML comments, document metadata noise, and collapse blank lines."""
    text = _HTML_COMMENT_RE.sub("", text)
    # Remove lines that are only document source tags like [Copy 002 Vw ...]
    text = re.sub(r"^\[.*?\]\s*$", "", text, flags=re.MULTILINE)
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    return text.strip()


def run_evaluation(input_text: str, context: dict) -> dict:
    """Run a single evaluation turn and return the response.

    Replace or adapt this implementation for your agent/model endpoint.
    """
    # --- Option 1: Standard JSON POST (default) ---
    body = json.dumps({"message": input_text}).encode()
    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return {"response": data.get("text", data.get("response", ""))}

    # --- Option 2: SSE / streaming endpoint ---
    # Uncomment the block below if your endpoint returns Server-Sent Events.
    #
    # body = json.dumps({"message": input_text}).encode()
    # req = urllib.request.Request(
    #     ENDPOINT,
    #     data=body,
    #     headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    #     method="POST",
    # )
    # chunks: list[str] = []
    # with urllib.request.urlopen(req) as resp:
    #     for raw_line in resp:
    #         line = raw_line.decode().strip()
    #         if line.startswith("data: "):
    #             payload = line[6:]
    #             if payload == "[DONE]":
    #                 break
    #             try:
    #                 event = json.loads(payload)
    #                 chunks.append(event.get("content", event.get("text", "")))
    #             except json.JSONDecodeError:
    #                 chunks.append(payload)
    # response_text = "".join(chunks)
    # return {"response": response_text}

    # --- Option 3: Direct Python call (no HTTP) ---
    # If your agent is a local Python object, call it directly:
    #
    # from my_agent import workflow
    # result = workflow.invoke(input_text)
    # return {"response": result.output}

    # --- Context sanitization (RAG scenarios) ---
    # If your dataset has a "context" field with raw document content,
    # clean it before returning:
    #
    # ctx = context.get("context", "")
    # if ctx:
    #     context["context"] = _sanitize_context(ctx)
