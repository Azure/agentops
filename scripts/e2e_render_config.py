"""Render scenario-specific agentops.yaml files for the e2e workflow.

Reads target identifiers from environment variables (set by the GitHub
Actions workflow from repo Actions Variables + Bicep outputs) and writes
one agentops.yaml per scenario into ``./e2e-runs/<scenario>/``.

Scenarios:
  - foundry-prompt: AGENTOPS_E2E_FOUNDRY_PROMPT_AGENT (e.g. ``e2e-prompt:1``)
  - foundry-hosted: AGENTOPS_E2E_FOUNDRY_HOSTED_URL  (https URL)
  - http-aca:      AGENTOPS_E2E_ACA_URL              (https URL of echo app)
  - model-direct:  AGENTOPS_E2E_MODEL_DEPLOYMENT     (deployment name)

A scenario is skipped (no file written) when its env var is unset, which
lets the workflow run partial scenarios via ``workflow_dispatch.inputs``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET_BASIC = ROOT / "scripts" / "e2e_data" / "basic.jsonl"
DATASET_RAG = ROOT / "scripts" / "e2e_data" / "rag.jsonl"
DATASET_TOOLS = ROOT / "scripts" / "e2e_data" / "tools.jsonl"


def _ensure_datasets() -> None:
    DATASET_BASIC.parent.mkdir(parents=True, exist_ok=True)
    if not DATASET_BASIC.exists():
        rows = [
            {"input": "What is 2+2?", "expected": "4"},
            {"input": "Capital of France?", "expected": "Paris"},
            {"input": "Color of the sky on a clear day?", "expected": "blue"},
        ]
        DATASET_BASIC.write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )
    if not DATASET_RAG.exists():
        rows = [
            {
                "input": "What is the capital of France?",
                "expected": "Paris",
                "context": "France is a country in Western Europe. Its capital is Paris.",
            },
            {
                "input": "What language is spoken in Brazil?",
                "expected": "Portuguese",
                "context": "Brazil is a South American country. The official language is Portuguese.",
            },
        ]
        DATASET_RAG.write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )
    if not DATASET_TOOLS.exists():
        weather_tool = {
            "type": "function",
            "name": "get_weather",
            "description": "Get the current weather for a given location.",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
        }
        rows = [
            {
                "input": f"What's the weather in {city}?",
                "expected": f"Calls get_weather with location='{city}'.",
                "tool_definitions": [weather_tool],
                "tool_calls": [
                    {
                        "type": "tool_call",
                        "tool_call_id": f"call_{i}",
                        "name": "get_weather",
                        "arguments": {"location": city},
                    }
                ],
            }
            for i, city in enumerate(
                ["Paris, France", "Tokyo, Japan", "Sao Paulo, Brazil"], start=1
            )
        ]
        DATASET_TOOLS.write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )


def _write(scenario: str, body: str, header: str | None = None) -> Path:
    out_dir = ROOT / "e2e-runs" / scenario
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = out_dir / "agentops.yaml"
    cfg.write_text(body, encoding="utf-8")
    if header is not None:
        (out_dir / "HEADER.md").write_text(header, encoding="utf-8")
    return cfg


def render() -> list[str]:
    _ensure_datasets()
    written: list[str] = []
    rel_basic = DATASET_BASIC.relative_to(ROOT).as_posix()
    rel_rag = DATASET_RAG.relative_to(ROOT).as_posix()
    rel_tools = DATASET_TOOLS.relative_to(ROOT).as_posix()

    prompt_agent = os.environ.get("AGENTOPS_E2E_FOUNDRY_PROMPT_AGENT")
    if prompt_agent:
        _write(
            "foundry-prompt",
            f"""version: 1
agent: {prompt_agent}
dataset: ../../{rel_basic}
# Permissive thresholds: e2e is a smoke test for the pipeline, not a quality gate.
thresholds:
  coherence: ">=1"
  fluency: ">=1"
  similarity: ">=1"
  f1_score: ">=0"
  avg_latency_seconds: "<=60"
""",
            header=f"""# Scenario: foundry-prompt

**Target:** Foundry prompt agent `{prompt_agent}` (created manually in the Foundry portal).

**What it does:** A general-purpose prompt agent backed by `gpt-4o-mini`. It
answers short factual questions with the canonical short answer
(e.g. "What is 2+2?" → "4"). No tools, no retrieval.

**Why this scenario exists:** It exercises the AgentOps invocation path for
agents referenced via `agent_reference` (the OpenAI Responses API integration
exposed by the Foundry Agent Service).

**Dataset:** `{rel_basic}` (3 short factual rows).

**Evaluators (auto-inferred from dataset shape):** `coherence`, `fluency`,
`similarity`, `f1_score`, plus `avg_latency_seconds`. Thresholds are very
permissive — this is a pipeline smoke test, not a quality gate.
""",
        )
        written.append("foundry-prompt")

    hosted_agent = os.environ.get("AGENTOPS_E2E_FOUNDRY_HOSTED_AGENT")
    if hosted_agent:
        _write(
            "foundry-hosted",
            f"""version: 1
agent: {hosted_agent}
dataset: ../../{rel_tools}
thresholds:
  tool_call_accuracy: ">=0"
  task_completion: ">=0"
  intent_resolution: ">=0"
  task_adherence: ">=0"
  f1_score: ">=0"
  avg_latency_seconds: "<=60"
""",
            header=f"""# Scenario: foundry-hosted (agent with tools)

**Target:** Foundry hosted agent `{hosted_agent}` — created dynamically by
this workflow run via `scripts/e2e_hosted_agent.py create` and deleted in
`teardown-live`.

**What it does:** A weather assistant backed by `gpt-4o-mini` with a single
function tool `get_weather(location)`. The agent's instructions tell it to
*always* invoke `get_weather` when the user asks about the weather, instead
of fabricating an answer.

**Tool registered on the agent:**

```json
{{
  "type": "function",
  "name": "get_weather",
  "description": "Get the current weather for a given location.",
  "parameters": {{
    "type": "object",
    "properties": {{ "location": {{ "type": "string" }} }},
    "required": ["location"]
  }}
}}
```

**Dataset:** `{rel_tools}` (3 weather questions, each with the expected
`get_weather` tool call as ground truth).

**Evaluators (auto-inferred from `tool_definitions` + `tool_calls`):**
`task_completion`, `tool_call_accuracy`, `intent_resolution`,
`task_adherence`, plus `f1_score` and `avg_latency_seconds`. Thresholds are
very permissive — this is a pipeline smoke test, not a quality gate.
""",
        )
        written.append("foundry-hosted")

    aca_url = os.environ.get("AGENTOPS_E2E_ACA_URL")
    if aca_url:
        # The echo container does not produce real answers; use an explicit
        # evaluators list so we only assert the round-trip and latency.
        _write(
            "http-aca",
            f"""version: 1
agent: {aca_url}
dataset: ../../{rel_basic}
protocol: http-json
request_field: message
response_field: json.message
evaluators:
  - name: avg_latency_seconds
thresholds:
  avg_latency_seconds: "<=60"
""",
            header=f"""# Scenario: http-aca

**Target:** A small echo Container App provisioned per run by
`infra/e2e/perrun.bicep` at `{aca_url}`.

**What it does:** It is *not* a real model — it is a deliberately dumb HTTP
echo service (FastAPI / built-in handler) that returns the request body's
`message` field back as the response. The point is to validate the AgentOps
HTTP-JSON invocation path end to end (POST a JSON body, parse a JSON
response with a configurable dot-path, and round-trip latency) against a
freshly-deployed Azure resource the workflow itself owns.

**Dataset:** `{rel_basic}` (3 short factual rows). Because the echo agent
cannot answer correctly, only `avg_latency_seconds` is enabled — every
quality evaluator would deterministically fail.

**Evaluators:** `avg_latency_seconds` only.
""",
        )
        written.append("http-aca")

    model_deployment = os.environ.get("AGENTOPS_E2E_MODEL_DEPLOYMENT")
    if model_deployment:
        _write(
            "model-direct",
            f"""version: 1
agent: model:{model_deployment}
dataset: ../../{rel_basic}
thresholds:
  coherence: ">=1"
  fluency: ">=1"
  similarity: ">=1"
  f1_score: ">=0"
  avg_latency_seconds: "<=60"
""",
            header=f"""# Scenario: model-direct

**Target:** Azure OpenAI model deployment `{model_deployment}` invoked via
`chat.completions` (no agent layer between AgentOps and the model).

**What it does:** Sends each dataset row's `input` straight to the model as a
single user message; the model's reply is taken as the response.

**Dataset:** `{rel_basic}` (3 short factual rows).

**Evaluators (auto-inferred from dataset shape):** `coherence`, `fluency`,
`similarity`, `f1_score`, plus `avg_latency_seconds`. Thresholds are very
permissive — this is a pipeline smoke test, not a quality gate.
""",
        )
        written.append("model-direct")

    return written


def main() -> int:
    written = render()
    if not written:
        print("ERROR: no scenario env vars set; nothing to render.", file=sys.stderr)
        return 1
    for s in written:
        print(f"rendered: e2e-runs/{s}/agentops.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
