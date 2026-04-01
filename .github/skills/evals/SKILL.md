---
name: evals
description: Guide users through running AgentOps evaluations end to end — single runs, multi-model benchmarks, and N-run comparisons. Trigger when users ask to initialize AgentOps, run an evaluation, compare runs, benchmark models, regenerate a report, or summarize results. Common phrases include "run eval", "start agentops", "compare models", "benchmark agents", "run.yaml", "report", "evaluation results", "which model is best". Install agentops-toolkit via pip. Commands are agentops init, agentops eval run, agentops eval compare, and agentops report generate.
---

# AgentOps Run Evaluations

> **Prerequisite:** Install the AgentOps CLI with `pip install agentops-toolkit`.

## Purpose
Guide users through the full AgentOps evaluation workflow — workspace setup, running evaluations, comparing N runs, benchmarking models/agents, and interpreting reports.

## When to Use
- User wants to start using AgentOps in a project.
- User asks how to run an evaluation with `run.yaml`.
- User wants to compare evaluation runs (2 or more).
- User wants to benchmark multiple models or agents on the same dataset.
- User asks how to regenerate reports or choose report format.
- User asks where evaluation outputs are written.

## Codebase Analysis (Do This First)

**Before asking any questions, analyze the user's workspace to infer the evaluation scenario, bundle, endpoint, and dataset fields automatically.** Only ask questions about things you cannot determine from the code.

### Step 1 — Detect the evaluation scenario

Search the codebase for signals that reveal the scenario. Use the first matching row:

| Signal in code | Scenario | Bundle | Run template |
|---|---|---|---|
| `tool_definitions`, `function_call`, `@tool`, tool schemas, MCP tool registration | Agent with tools | `agent_workflow_baseline` | `run-agent.yaml` / `run-http-agent-tools.yaml` |
| `SearchIndex`, `VectorStore`, `context`, RAG pipeline, embedding calls, retriever | RAG | `rag_quality_baseline` | `run-rag.yaml` / `run-http-rag.yaml` |
| Chat interface, conversation history, assistant persona, system prompt only | Conversational agent | `conversational_agent_baseline` | `run.yaml` / `run-http-model.yaml` |
| Direct model call, completion API, no agent logic | Model quality | `model_quality_baseline` | `run.yaml` / `run-http-model.yaml` |
| Safety review, content filtering, red-teaming | Content safety | `safe_agent_baseline` | (custom run.yaml) |

### Step 2 — Detect the endpoint type

| Signal in code | Endpoint kind | `hosting` value |
|---|---|---|
| `AIProjectClient`, Foundry project endpoint, `azure-ai-projects` | `foundry_agent` | `foundry` |
| FastAPI, Flask, Django, Express, HTTP server, REST API | `http` | `local`, `aks`, or `containerapps` |
| No server — script, notebook, or library | local adapter | `local` (use `target.local.callable`) |

Also check:
- `agent_id` references → Foundry hosted agent
- `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` in env files → Foundry
- Deployment configs (Dockerfile, bicep, ACA manifests) → containerized HTTP

### Step 3 — Generate a custom dataset

**NEVER ask the user to pick a starter dataset.** The starter datasets are generic examples. Instead, create a custom dataset tailored to the project:

1. Read the codebase to understand what the agent/model does (system prompt, tools, domain).
2. Write a JSONL file with **5–10 realistic rows** covering the project's actual use cases.
3. Use the correct fields for the scenario:

| Scenario | Required JSONL fields | Example |
|---|---|---|
| Model quality | `input`, `expected` | `{"input": "Summarize this ticket", "expected": "The customer reports..."}` |
| Conversational | `input`, `expected` | `{"input": "How do I reset my password?", "expected": "Go to Settings > Security..."}` |
| RAG | `input`, `expected`, `context` | `{"input": "What is the refund policy?", "expected": "...", "context": "From our FAQ: refunds are..."}` |
| Agent with tools | `input`, `expected`, `tool_definitions`, `tool_calls` | `{"input": "Check order #123", "expected": "...", "tool_definitions": [...], "tool_calls": [...]}` |

4. Create the matching dataset YAML config pointing to the JSONL file.
5. Show the generated dataset to the user and ask if it looks right before proceeding.

### Step 4 — Generate the run.yaml

Using the detected scenario, endpoint, and generated dataset, produce a complete `run.yaml`. Fill in all values — do not leave `<replace-...>` placeholders. If a value cannot be determined (e.g., `agent_id`), ask the user for just that specific value.

### What to ask the user (only if needed)

Only ask about information you **cannot** infer from the codebase:
- Foundry `agent_id` (if not in code or env files)
- Foundry `model` deployment name (if not discoverable)
- HTTP endpoint URL (if not in code, env files, or deployment configs)
- `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` value (if not set)

**Do NOT ask:** which bundle, which dataset, which scenario, which run template. Determine these yourself.

## Available Commands

```bash
pip install agentops-toolkit                          # Install the CLI
agentops init [--path <dir>]                          # Scaffold workspace
agentops eval run [-c <run.yaml>] [-f md|html|all]    # Run evaluation
agentops report generate [--in <results.json>] [-f md|html|all] # Regenerate report
agentops eval compare --runs <id1>,<id2>[,<id3>,...] [-f md|html|all]  # Compare N runs
```

### Key flags
- `-c / --config` — path to run.yaml (default: `.agentops/run.yaml`)
- `-f / --format` — report format: `md` (default), `html`, or `all`
- `-o / --output` — output directory override
- `--runs` — comma-separated run IDs (timestamps, `latest`, or paths)

## Recommended Workflow

### Single evaluation
1. `agentops init` — scaffold `.agentops/` workspace (if not already done)
2. Analyze the codebase (Steps 1–4 above) — detect scenario, endpoint, and generate dataset + run.yaml
3. Confirm the generated files with the user
4. Set env: `$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://..."` (if Foundry)
5. `agentops eval run` — run evaluation
6. Check `.agentops/results/latest/results.json` and `report.md`

### Multi-model benchmark
1. Create one run.yaml per model (same dataset + bundle, different `model:`):
   ```yaml
   # run-gpt51.yaml
   target:
     type: model
     hosting: foundry
     execution_mode: remote
     endpoint:
       kind: foundry_agent
       model: gpt-5.1
       project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
   ```
2. Run each: `agentops eval run -c .agentops/run-gpt51.yaml -f html`
3. Compare all: `agentops eval compare --runs <id1>,<id2>,<id3> -f html`
4. Open the HTML report — shows side-by-side scores, ● Met/Missed dots, ↑↓ direction arrows, row pass rates, and best-run highlighting

### Multi-agent comparison
Same approach — create one run.yaml per agent version:
```yaml
target:
  type: agent
  hosting: foundry
  execution_mode: remote
  agent_mode: hosted
  endpoint:
    kind: foundry_agent
    agent_id: my-agent:1    # or my-agent:2, my-agent:3
```

## Report Formats
- **`md`** (default) — Markdown, suitable for PRs and CI logs
- **`html`** — professional dashboard with visual indicators (● dots, ↑↓ arrows, color-coded badges, best highlighting)
- **`all`** — generates both

## Comparison Report Sections
The comparison report contains:

1. **Header** — verdict (NO REGRESSIONS / REGRESSIONS DETECTED), comparison type, varying parameter
2. **Run Config** — identity fields (Target, Model, Agent) + Status with pass rate (e.g., `PASS (100% · 5/5)`)
3. **Evaluators** — unified table showing per-evaluator:
   - Target threshold (e.g., `>= 3`)
   - Score per run with ● green/red dot (Met/Missed vs target)
   - Delta + ↑↓ direction vs baseline (improved/regressed/unchanged)
   - Row pass rate (e.g., `(4/5)`)
   - Best run highlighted with green background
   - Informational metrics (like `samples_evaluated`) shown as plain numbers
4. **Row Details** — per-row evaluator scores with ● dots (only when same dataset across runs)
5. **Fixed Parameters** — reference config info at bottom

## Comparison Types (auto-detected)
- **Model Comparison** — same dataset, model varies
- **Agent Comparison** — same dataset, agent varies
- **Dataset Coverage** — same agent/model, dataset varies (row details skipped)
- **General Comparison** — multiple things vary

## Regression Detection
A regression is detected ONLY when:
- A run's overall status flips from PASS to FAIL vs baseline
- A previously-passing row now fails

Minor numeric shifts within passing thresholds are NOT regressions.

## Evaluation Terminology
- **Met** / **Missed** — evaluator score vs absolute threshold target
- **improved** / **regressed** / **unchanged** — score direction vs baseline run
- **PASS** / **FAIL** — overall run status (PASS = all row thresholds met, FAIL = any row missed)

## Exit Codes
- `0` — succeeded and all thresholds passed (eval run) / no regressions (compare)
- `2` — thresholds failed (eval run) / regressions detected (compare)
- `1` — runtime or configuration error

## Expected Outputs
- `results.json` — machine-readable normalized results
- `report.md` / `report.html` — human-readable report (per format flag)
- `cloud_evaluation.json` — Foundry portal URL (cloud eval only)
- `comparison.json` + `comparison.md` / `comparison.html` — comparison outputs

## Environment Setup
```bash
# Required for Foundry backend
$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"

# Authentication
az login  # local development
# CI/CD: set AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_CLIENT_SECRET
```

## Guardrails
- Do not invent commands or flags beyond documented CLI behavior.
- Planned commands (`run list`, `bundle show`, `trace init`, `monitor`) are NOT implemented — state they are planned.
- The `--format` flag accepts only `md`, `html`, or `all`.
- When comparing runs with different datasets, row-level comparison is not meaningful — the report handles this automatically.
- Always analyze the codebase before asking the user questions. Never ask which bundle or dataset to use.

## Examples
- "Run evals on my project"
  → Analyze codebase to detect scenario and endpoint, generate custom dataset + run.yaml, confirm with user, then run `agentops eval run`
- "Compare 3 models on the same dataset"
  → Create 3 run.yaml files (one per model), run each with `agentops eval run -c <config> -f html`, then `agentops eval compare --runs <id1>,<id2>,<id3> -f html`
- "Which model should I use?"
  → Run multi-model benchmark, check Evaluators table for best scores and latency, pick the model that meets thresholds at lowest cost
- "Why did my eval fail?"
  → Check the Row Details section — it shows per-row scores with ● Met/Missed so you can see exactly which rows scored below threshold

## Learn More
- Documentation: https://github.com/Azure/agentops
- PyPI: https://pypi.org/project/agentops-toolkit/
