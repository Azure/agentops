---
name: agentops-dataset
description: Generate evaluation datasets (JSONL data + YAML config) tailored to the project. Trigger when users ask to create test data, generate a dataset, or prepare evaluation data. Common phrases include "dataset", "test data", "evaluation data", "JSONL", "generate data", "create dataset", "sample data". Install agentops-toolkit via pip.
---

# AgentOps Dataset

Generate a custom evaluation dataset from the codebase. Never offer starter datasets — always create project-specific data.

## Step 0 — Prerequisites

1. Run `pip install agentops-toolkit` if `agentops` command is not available.
2. Run `agentops init` if `.agentops/` directory does not exist.

## Step 1 — Understand the domain

Read the codebase: system prompt, tool definitions, README, sample inputs/outputs, test fixtures. Understand the agent's **primary purpose** and identify the scenario:

| Primary purpose | Scenario |
|---|---|
| Agent that orchestrates tools to complete tasks | Agent with tools |
| Agent that retrieves context to answer questions | RAG |
| Conversational assistant (chat, Q&A, persona) | Conversational |
| Direct model call with no agent logic | Model quality |

> A RAG agent that uses a search tool is still primarily RAG. The test is: *what is the agent's main job?*

## Step 2 — Confirm topics and count

1. Ask: *"What topics should the test data cover?"*
2. Ask: *"How many rows? (suggest 5–10)"*

## Step 3 — Generate JSONL rows

Use the correct fields for the scenario:

| Scenario | JSONL fields |
|---|---|
| Model quality | `input`, `expected` |
| Conversational | `input`, `expected` |
| RAG | `input`, `expected`, `context` |
| Agent with tools | `input`, `expected`, `tool_definitions`, `tool_calls` |
| Content safety | `input`, `expected` |

Write `.agentops/data/data.jsonl` — one JSON object per line. Rows must:
- Cover distinct use cases from the codebase
- Include realistic, domain-specific content
- Have at least one edge case
- Reflect actual tool schemas and system prompt

## Step 4 — Write dataset YAML config

Write `.agentops/datasets/dataset.yaml` using this **exact** structure — no alternatives:
```yaml
version: 1
name: dataset
description: <one-line description>
source:
  type: file
  path: ../data/data.jsonl
format:
  type: jsonl
  input_field: input
  expected_field: expected
metadata:
  scenario: <scenario>
  size_hint: <row_count>
```

**NEVER** use `path:` or `fields:` at the top level — the correct keys are `source:` and `format:`. If unsure, read an existing starter config from `.agentops/datasets/` as a reference template.

For RAG scenarios, add `context_field: context` under `format:`:
```yaml
format:
  type: jsonl
  input_field: input
  expected_field: expected
  context_field: context
```

## Step 4.5 — RAG context enrichment

If the scenario is **RAG** and the generated JSONL has no `context` field:

1. **Find the project's retrieval logic** — search the codebase for how it fetches context today:
   - Look for search/retrieval client initialization, index or collection names, embedding calls
   - Check `.env` files and code for endpoint URLs, API keys, index names used by the retriever
   - The project may use Azure AI Search, Cosmos DB vector search, FAISS, Pinecone, or any other store — read the code to find out

2. **Build a retrieval script** at `.agentops/rag_context.py` (**never** in `src/`) that:
   - Reads the project's own retrieval config (env vars, endpoint, index name) from whatever the project uses
   - For each row in the JSONL, queries the retrieval backend with `row["input"]` and writes the result into `row["context"]`
   - Uses only stdlib (`urllib.request`, `json`, `os`) — no third-party dependencies
   - Accepts the JSONL file path as a CLI argument: `python .agentops/rag_context.py .agentops/data/data.jsonl`

3. Verify: each JSONL row now has a `context` field.
4. Update dataset YAML to include `context_field: context` under `format:`.

If no retrieval backend can be identified, state: *"RAG context cannot be populated automatically — either add `context` manually to each row or switch to `model_quality_baseline` bundle which does not require it."*

## Step 5 — Present for review

Show the generated rows and say: *"These are starter rows for validation. For production evaluations, use real user queries or domain expert–curated data."*

## Outputs

- `.agentops/data/data.jsonl` — JSONL rows
- `.agentops/datasets/dataset.yaml` — dataset config

## Rules

- **NEVER** offer starter datasets (`smoke-model-direct.jsonl`, etc.) — always generate custom data.
- **NEVER** leave `<replace-...>` placeholders in JSONL or YAML.
- **NEVER** use `path:` or `fields:` at the dataset config top level — the correct structure uses `source:` and `format:`. Read a starter config from `.agentops/datasets/` if unsure.
- Use generic file names: `data.jsonl`, `dataset.yaml` — not project-specific prefixes.
- State the scenario assumption: *"Generating dataset for RAG scenario (detected retriever)"*.
- Mark generated data as draft — not production-grade.
- Do not run evaluations — delegate to `/agentops-eval`.
- Do not generate run.yaml — delegate to `/agentops-config`.
