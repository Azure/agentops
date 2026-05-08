# Tutorial: Foundry Agent Evaluation

This tutorial evaluates a deployed Foundry agent end-to-end — system instructions, tools, retrieval, and all. You send prompts through the agent the same way a real user would, then measure the quality of the responses.

Agent evaluation is different from model-direct evaluation in important ways. When you evaluate a model directly, you get the raw model output — concise, predictable, and closely matching expected answers. When you evaluate an agent, you get the *agent's* output, which is shaped by its instructions, may include information gathered from tools, and is phrased in the agent's style. This means agent similarity scores are typically lower than model-direct scores, even when the agent is working correctly.

That difference is not a flaw — it is the point. Agent evaluation tells you whether the complete system behaves the way your users will experience it, not just whether the underlying model knows the right answer.

## When to use agent evaluation

Use agent evaluation when you want to:

- **Test the end-to-end experience** your users will actually see, including agent instructions, tool calls, and knowledge base lookups
- **Catch regressions caused by agent configuration changes** — modified system instructions, added/removed tools, updated knowledge sources
- **Measure real latency** including the agent orchestration overhead (thread creation, tool execution, polling)
- **Validate that tools work correctly** — if an agent has a calculator tool, does it actually call it and return the right answer?

### How agent scores differ from model-direct

In our testing, the same QA dataset scored **5.0** on model-direct (perfect similarity) and **3.4** on an agent. The agent was answering correctly, but it was rephrasing answers in its own style, adding context, and sometimes including extra details from its system instructions.

A SimilarityEvaluator score of 3.4 on an agent is not a failure — it means the agent is producing responses that capture the core meaning but differ from the exact expected text. Set your thresholds accordingly. A threshold of ≥ 3 is usually appropriate for agents, while model-direct can sustain ≥ 4 or even ≥ 5 on clean datasets.

If you see agent scores drop to 1.0 on questions that the model-direct handles at 5.0, that is worth investigating. It usually means the agent's instructions are conflicting with the question, a tool call failed, or the agent is hallucinating.

### Agent vs model-direct: quick decision guide

| Question | Model-Direct | Agent |
|---|---|---|
| What does the raw model do with this prompt? | ✅ | |
| Is the agent responding correctly to users? | | ✅ |
| Did a model version change affect quality? | ✅ | ✅ |
| Did agent instruction changes affect quality? | | ✅ |
| What is the real latency users experience? | | ✅ |
| Can I get a fast baseline with no agent setup? | ✅ | |

## Prerequisites

- Python 3.11+
- Azure CLI (`az login`)
- A Foundry project with a deployed agent
- A model deployment in the same project (used as the judge model for SimilarityEvaluator)
- `pip install "agentops-toolkit @ git+https://github.com/Azure/agentops.git@develop"`

## Part 1: Create the agent in Foundry

If you already have an agent, skip to Part 2.

### 1) Open the Foundry portal

Go to `https://ai.azure.com` and open your Foundry project.

### 2) Create a new agent

Navigate to **Build > Agents** and create a new agent. For this tutorial, a simple QA agent works well:

**System instructions:**
```text
You are a factual question-answering assistant.

Rules:
1. Answer short factual questions clearly and directly.
2. Keep answers concise — one or two sentences when possible.
3. Do not invent facts. If uncertain, say so.
4. Do not use markdown formatting in responses.
```

Choose a model deployment (e.g., `gpt-5.1`) and save the agent.

### 3) Note the agent identifier

After saving, you need the agent's identifier for the run config. There are two types:

- **Named agents** (new Foundry experience): use the agent name, optionally with a version — e.g., `my-agent` or `my-agent:3`
- **Legacy agents** (asst_ prefix): use the full ID — e.g., `asst_ftDQySPlKUwcgR1eiXEzUEO5`

AgentOps handles both. Named agents use the Foundry Responses API; legacy agents use the Threads API.

## Part 2: Set up AgentOps

### 1) Azure login

```bash
az login
```

### 2) Set the project endpoint

PowerShell:
```powershell
$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://<resource>.services.ai.azure.com/api/projects/<project>"
```

Bash/zsh:
```bash
export AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="https://<resource>.services.ai.azure.com/api/projects/<project>"
```

### 3) Initialize the workspace

```bash
agentops init
```

## Part 3: Configure the agent run

Open `agentops.yaml` at your project root and point it at your agent:

```yaml
version: 1
agent: "my-agent:1"                       # ← your agent name:version (or asst_ ID)
dataset: .agentops/data/smoke-agent-tools.jsonl
thresholds:
  similarity: ">=3"
  avg_latency_seconds: "<=20"
```

Key points:
- `agent` is a single string. AgentOps recognizes the `name:version` shape
  and routes the run to the Foundry Agent Service automatically.
- The judge model used by AI-assisted evaluators (SimilarityEvaluator) is
  taken from `AZURE_OPENAI_DEPLOYMENT` (set in Part 2).
- Evaluators are auto-selected from the dataset row shape — `input` +
  `expected` triggers `SimilarityEvaluator`. No `bundle` to maintain.

## Part 4: Review the dataset

The sample dataset at `.agentops/data/smoke-agent-tools.jsonl` contains five prompts designed for an agent with tool capabilities:

```jsonl
{"id":"1","input":"What is the weather in Seattle today?","expected":"I'll check the weather for Seattle..."}
{"id":"2","input":"Convert 100 USD to EUR","expected":"100 USD is approximately 92 EUR..."}
```

These prompts include questions that might trigger tool calls (weather, currency conversion, search). If your agent does not have these tools, it will answer based on its knowledge, which may score lower on similarity. That is expected — the evaluation measures what the agent *actually does*, not what it could do with the right tools.

### Adapting the dataset to your agent

For meaningful evaluation, your dataset should match what your agent is designed to do. If your agent is a customer support bot, test it with customer support questions. If it is a code assistant, test it with coding tasks. The smoke dataset is just a starting point.

## Part 5: Run the evaluation

```bash
agentops eval run
```

AgentOps will:
1. Send each prompt to the agent via the Foundry API
2. Wait for the agent to process the request (including any tool calls)
3. Collect the agent's response
4. Run SimilarityEvaluator comparing the response to the expected answer
5. Measure latency per row
6. Write results under `.agentops/results/<timestamp>/` and mirror them to `.agentops/results/latest/`

### What to expect

Agent evaluations take longer than model-direct because each prompt involves:
- Thread or session creation
- Message delivery
- Agent processing (may include tool calls)
- Response collection

A 5-row agent evaluation typically takes 30–60 seconds in local mode, compared to 10–20 seconds for model-direct.

### Reading the results

Open the report in VS Code and press `Ctrl+Shift+V` to render the Markdown:

```powershell
code .agentops/results/latest/report.md
```

For an agent with the simple QA instructions above, expect:

- **SimilarityEvaluator** around 3–4 (the agent captures meaning but rephrases)
- **avg_latency_seconds** around 5–15s per row (agent orchestration overhead)
- Some rows may fail the ≥ 3 threshold if the agent's response diverges significantly

If most rows score 4–5, your agent is working well. If most score 1–2, check the agent's instructions, verify it has access to the right tools, and look at the actual responses in `backend.stdout.log`.

## Part 6: Compare with a baseline

You've only run a single evaluation, so `.agentops/results/` has one timestamped run plus the `latest/` mirror. To compare a future run against it, you don't need to copy anything — just point `--baseline` at the previous result. AgentOps loads the baseline into memory before refreshing `latest/`, so `latest/results.json` works as a shorthand for "the run before this one".

**1. Change something** — agent instructions, model deployment, an evaluator threshold, the dataset.

**2. Re-run with `--baseline`:**

```bash
agentops eval run --baseline .agentops/results/latest/results.json
```

> Prefer a stable, named reference? Point at the specific timestamp folder you want to keep, e.g. `--baseline .agentops/results/2026-05-06T20-13-21Z/results.json`.

`report.md` now contains a **Comparison vs Baseline** table with per-metric deltas (🟢 improved / 🔴 regressed / ⚪ unchanged). See the [Baseline Comparison Tutorial](tutorial-baseline-comparison.md) for the full PR-gating workflow.

## Evaluation scenarios

AgentOps auto-selects evaluators from the dataset row shape:

| Scenario | Required row fields | Evaluators auto-selected | Use case |
|---|---|---|---|
| **Model Quality** | `input`, `expected` | SimilarityEvaluator, CoherenceEvaluator, FluencyEvaluator, F1ScoreEvaluator | Benchmark raw model quality |
| **RAG Quality** | `input`, `expected`, `context` | GroundednessEvaluator, RelevanceEvaluator, RetrievalEvaluator | Evaluate grounding against context |
| **Conversational** | `input`, `expected` | CoherenceEvaluator, FluencyEvaluator, RelevanceEvaluator, SimilarityEvaluator | Chatbots and Q&A agents |
| **Agent Workflow** | `input`, `expected`, `tool_definitions`, `tool_calls` | TaskCompletionEvaluator, ToolCallAccuracyEvaluator | Agents with tool calling |

The RAG scenario uses GroundednessEvaluator instead of SimilarityEvaluator because the key question is whether the agent's response is grounded in the retrieved context, not whether it matches a specific expected answer.

## Notes

- **Cloud vs local mode**: By default, AgentOps uses Foundry Cloud Evaluation with the `azure_ai_evaluator` API. Set `AGENTOPS_FOUNDRY_MODE=local` to invoke the agent row-by-row and run evaluators locally (requires `pip install azure-ai-evaluation`).
- **Authentication**: `DefaultAzureCredential` handles auth automatically. For local dev, use `az login`. For CI, set `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET`.
- **Named vs legacy agents**: Named agents (e.g., `my-agent:3`) use the Responses API. Legacy agents (`asst_*`) use the Threads API. Both work transparently.
- **Exit codes**: `0` = all thresholds passed, `2` = threshold failures, `1` = error.

## Next steps

- [Model-Direct Tutorial](tutorial-model-direct.md) — evaluate a model without agents
- [RAG Tutorial](tutorial-rag.md) — evaluate retrieval-augmented responses
- [Baseline Comparison Tutorial](tutorial-baseline-comparison.md) — compare runs and detect regressions
- [Copilot Skills Tutorial](tutorial-copilot-skills.md) — use the installed AgentOps skills to build an eval workflow with Copilot
