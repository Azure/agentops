# Tutorial — agent workflow with tool calling

Evaluate an agent that calls **tools** (function calls / actions).
AgentOps grades both the **final natural-language answer** *and* the
**tool selection / arguments** the agent chose along the way.

## Required dataset shape

What turns a regular dataset into a tool-calling dataset is one or
both of these row fields:

| Field | What it is |
|---|---|
| `tool_definitions` | The tools the agent has access to (OpenAI tool-call schema). |
| `tool_calls` | The expected tool calls (name + arguments). |

When AgentOps sees `tool_calls` (or `tool_definitions`) in the
dataset rows, it auto-selects the **agent workflow** evaluators:
TaskCompletion, ToolCallAccuracy, IntentResolution, TaskAdherence,
plus the conversational baseline (Coherence, Fluency, Similarity,
F1Score, latency).

## 1. Bootstrap

```bash
pip install agentops-toolkit
agentops init
export AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="https://<resource>.services.ai.azure.com/api/projects/<project>"
```

## 2. Edit `agentops.yaml`

For a Foundry prompt agent that already has tools registered:

```yaml
version: 1
agent: "weather-bot:2"
dataset: .agentops/data/tools.jsonl
```

For an HTTP-deployed agent that returns tool calls in its response
body:

```yaml
version: 1
agent: "https://aca-weather-bot.example.com/"
http:
  request_field: message
  response_field: text
  tool_calls_field: tool_calls
dataset: .agentops/data/tools.jsonl
```

`tool_calls_field` tells AgentOps where in the response JSON to find
the structured tool calls (dot-path notation supported).

## 3. Dataset shape (`tools.jsonl`)

```jsonl
{"id":"1","input":"What's the weather in Paris, France?","expected":"Calls get_weather with location='Paris, France'.","tool_calls":[{"type":"function_call","name":"get_weather","arguments":{"location":"Paris, France"}}]}
{"id":"2","input":"How is the weather in Tokyo, Japan?","expected":"Calls get_weather with location='Tokyo, Japan'.","tool_calls":[{"type":"function_call","name":"get_weather","arguments":{"location":"Tokyo, Japan"}}]}
```

You can additionally include `tool_definitions` to give the evaluator
the schema of every tool the agent should know about. This sharpens
the **ToolSelectionEvaluator** judgement.

## 4. Run

```bash
agentops eval run
```

The report's per-row block shows:

- The agent's final text response
- The structured tool calls the agent emitted
- ToolCallAccuracy / IntentResolution / TaskAdherence scores

## 5. CI gate

In a PR check, fail when tool quality regresses:

```bash
agentops eval run --baseline .agentops/results/baseline/results.json
```

The baseline diff highlights when `tool_call_accuracy` drops — the
fastest signal that someone broke the function-calling layer.

## Build a real tool-calling agent

The repo's E2E test deploys a real Microsoft Agent Framework agent
(FastAPI on Container Apps) with a `get_weather` tool. See:

- `infra/e2e/agent-app/app.py` — minimal Agent Framework + FastAPI app
- `infra/e2e/perrun.bicep` — per-run ACA deployment
- `scripts/e2e_data/tools.jsonl` — the dataset used to grade it

That same setup is what `tutorial-http-agent.md` walks through.

## See also

- [tutorial-conversational-agent.md](tutorial-conversational-agent.md) — same shape, no tools
- [tutorial-http-agent.md](tutorial-http-agent.md) — deploying an HTTP agent
- [tutorial-rag.md](tutorial-rag.md) — RAG instead of tools
- [foundry-evaluation-sdk-built-in-evaluators.md](foundry-evaluation-sdk-built-in-evaluators.md) — full evaluator reference
