# Tutorial: Evaluating a Conversational Agent (Agent Framework)

This tutorial shows how to evaluate a **conversational agent** built with Microsoft Agent Framework using AgentOps.

Conversational agents — chatbots, Q&A assistants, multi-turn assistants — don't use tool calling or retrieval. The evaluation focuses on **response quality**: coherence, fluency, relevance, and similarity to expected answers.

## When to Use This Scenario

Use the **conversational agent** evaluation when:

- Your agent responds to open-ended user messages without calling external tools
- You want to measure response quality for a Q&A or chat assistant
- Your agent is built with Microsoft Agent Framework and runs as local Python code
- You want CI-friendly quality gates before deploying

This tutorial uses the **callable adapter** to invoke the agent directly as a Python function — no subprocess, no HTTP server needed.

## Prerequisites

- Python 3.11+
- AgentOps installed: `pip install agentops-toolkit`
- Microsoft Agent Framework SDK installed (for your agent code)
- An Azure OpenAI deployment for AI-assisted evaluators (CoherenceEvaluator, etc.)
- `az login` completed

## Part 1: Initialize the Workspace

```bash
cd your-project-root
agentops init
```

This creates the `.agentops/` workspace with starter bundles, datasets, and templates.

Confirm the conversational bundle and dataset exist:

```
.agentops/
├── bundles/
│   └── conversational_agent_baseline.yaml
├── datasets/
│   └── smoke-conversational.yaml
├── data/
│   └── smoke-conversational.jsonl
└── callable_adapter.py
```

## Part 2: Point to Your Agent Function

The callable adapter lets you point AgentOps directly to a Python function in your project. Your function just needs to follow this contract:

```
(input_text: str, context: dict) -> dict   returning {"response": "..."}
```

AgentOps calls it once per dataset row — no wrapper code, no subprocess, no HTTP server.

### Option A: Point directly to your existing function

If your project already has a function with the right signature, just reference it in `run.yaml`:

```yaml
local:
  callable: my_agent.app:chat
```

For example, if your Agent Framework code lives in `my_agent/app.py`:

```python
# my_agent/app.py

def chat(input_text: str, context: dict) -> dict:
    """Entry point called by AgentOps for each dataset row."""
    result = agent.invoke(input_text)
    return {"response": result.output}
```

### Option B: Use the starter template

`agentops init` already creates `.agentops/callable_adapter.py` with the correct signature and placeholder code. Open it and replace the body with your agent call — typically 2-3 lines:

```python
# .agentops/callable_adapter.py  (created by agentops init)

def run_evaluation(input_text: str, context: dict) -> dict:
    from my_agent.app import agent
    result = agent.invoke(input_text)
    return {"response": result.output}
```

The function must:
- Accept `(input_text: str, context: dict)`
- Return a dict with at least a `"response"` key
- Be importable from the project root

## Part 3: Configure the Run

Edit `.agentops/run.yaml` to point to your function and select the conversational bundle:

```yaml
version: 1

target:
  type: agent
  hosting: local
  execution_mode: local
  local:
    # Point to your function: module.path:function_name
    callable: my_agent.app:chat

bundle:
  name: conversational_agent_baseline

dataset:
  name: smoke-conversational

execution:
  timeout_seconds: 300

output:
  write_report: true
```

Key fields:
- `local.callable` — The `module:function` path to your agent function. Use your project's module path (e.g. `my_agent.app:chat`) or point to the starter template (`callable_adapter:run_evaluation`).
- `bundle.name: conversational_agent_baseline` — Evaluates coherence, fluency, relevance, and similarity.
- `dataset.name: smoke-conversational` — The conversational smoke dataset.

## Part 4: Set Up AI-Assisted Evaluator Credentials

The conversational evaluators (CoherenceEvaluator, FluencyEvaluator, etc.) are **AI-assisted** — they need an Azure OpenAI model to judge quality.

Set the environment variables:

```bash
export AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="https://your-project.services.ai.azure.com"
export AZURE_OPENAI_ENDPOINT="https://your-openai.openai.azure.com/"
export AZURE_AI_MODEL_DEPLOYMENT_NAME="gpt-4o"
```

Or on Windows (PowerShell):

```powershell
$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://your-project.services.ai.azure.com"
$env:AZURE_OPENAI_ENDPOINT = "https://your-openai.openai.azure.com/"
$env:AZURE_AI_MODEL_DEPLOYMENT_NAME = "gpt-4o"
```

## Part 5: Review the Dataset

Check `.agentops/data/smoke-conversational.jsonl`:

```json
{"id":"1","input":"Hi, how are you doing today?","expected":"Hello! I'm doing well, thank you for asking. How can I help you today?"}
{"id":"2","input":"Can you explain what machine learning is in simple terms?","expected":"Machine learning is a type of artificial intelligence where computers learn patterns from data..."}
```

Each row has:
- `input` — The user message sent to the agent
- `expected` — The reference response for similarity comparison

Replace these with real conversations from your agent's domain.

## Part 6: Run the Evaluation

```bash
agentops eval run --config .agentops/run.yaml
```

Or from the project root using default config:

```bash
agentops eval run
```

### Output

```
AgentOps evaluation run
  Config: .agentops/run.yaml
  Bundle: conversational_agent_baseline
  Dataset: smoke-conversational (5 rows)
  Backend: local_adapter (callable)

Processing row 1/5
Processing row 2/5
...

Results: .agentops/results/latest/results.json
Report:  .agentops/results/latest/report.md

Summary:
  Overall: PASSED
  Thresholds: 4/4 passed
  CoherenceEvaluator avg: 4.2
  FluencyEvaluator avg: 4.5
  RelevanceEvaluator avg: 3.8
  SimilarityEvaluator avg: 3.6
```

### Exit Codes

- `0` — All thresholds passed
- `2` — One or more thresholds failed
- `1` — Runtime or configuration error

## Part 7: Review the Report

Open `.agentops/results/latest/report.md` to see per-row scores and threshold results.

To regenerate the report from existing results:

```bash
agentops report generate --in .agentops/results/latest/results.json
```

## Part 8: Compare Runs

After improving your agent, run the evaluation again and compare:

```bash
agentops eval run --output .agentops/results/after-improvement
agentops eval compare --runs .agentops/results/latest,.agentops/results/after-improvement
```

## Thresholds

The `conversational_agent_baseline` bundle enforces:

| Evaluator | Criteria | Threshold |
|---|---|---|
| CoherenceEvaluator | ≥ | 3.0 |
| FluencyEvaluator | ≥ | 3.0 |
| RelevanceEvaluator | ≥ | 3.0 |
| SimilarityEvaluator | ≥ | 3.0 |
| avg_latency_seconds | ≤ | 10.0 |

Scores range from 1 to 5. Adjust thresholds in `.agentops/bundles/conversational_agent_baseline.yaml` for your quality bar.

## CI/CD Integration

Add to your GitHub Actions or Azure Pipelines workflow:

```yaml
- name: Run conversational agent evaluation
  run: |
    pip install agentops-toolkit
    agentops eval run --config .agentops/run.yaml
```

The exit code `2` fails the pipeline when thresholds are not met.

## Notes

- **Callable vs subprocess**: The callable adapter is faster than subprocess because it avoids process spawning overhead and runs in-process.
- **Module resolution**: The callable path is resolved via `importlib.import_module()`. Ensure your module is importable from the project root (on `sys.path`).
- **AI-assisted evaluators**: CoherenceEvaluator, FluencyEvaluator, RelevanceEvaluator require an Azure OpenAI deployment. SimilarityEvaluator also requires a ground truth reference.
- **Local evaluator only**: If you want to skip AI-assisted evaluators, create a custom bundle with only `exact_match` and `avg_latency_seconds`.
