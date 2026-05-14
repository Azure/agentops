---
name: agentops-dataset
description: Create or extend a JSONL evaluation dataset for AgentOps. Trigger on "create dataset", "generate test data", "JSONL", "more eval rows". Infer the agent's domain from the codebase and produce realistic rows; never fabricate data when the domain is unclear.
---

# AgentOps Dataset

Generate a small, realistic JSONL dataset for the agent under
evaluation. Default location: `.agentops/data/smoke.jsonl` (referenced
from `agentops.yaml`).

## Step 0 - Prerequisites

1. `pip install "agentops-toolkit @ git+https://github.com/Azure/agentops.git@develop"` if `agentops` is missing.
2. `agentops init` if `agentops.yaml` does not exist.

## Step 1 - Pick the columns

Read `agentops.yaml` (and the agent code) to figure out the agent type,
then choose the row schema:

| Agent type | Required columns | Optional columns |
|---|---|---|
| Direct model / Q&A | `input`, `expected` | - |
| RAG | `input`, `expected`, `context` | - |
| Conversational | `input`, `expected` | - |
| Tool-using agent | `input`, `expected`, `tool_calls` | `tool_definitions` |

`input` is always the user prompt. `expected` is the gold answer.
`context` is the retrieved passage(s). `tool_calls` is a list of
`{name, arguments}` describing the expected tool invocations.

## Step 2 - Ground the rows in the codebase

- Read the README, system prompt, tool definitions, and any sample
  fixtures.
- Generate **5–10 rows** that exercise the agent's actual capabilities.
- If the domain is unclear, generate a tiny generic draft and clearly
  flag it as a placeholder.

## Step 3 - Write the JSONL

One JSON object per line, no trailing commas, UTF-8:

```json
{"input": "What is the refund policy?", "expected": "Refunds within 30 days...", "context": "Refund policy: ..."}
```

Save to the path referenced by `dataset:` in `agentops.yaml` (default
`.agentops/data/smoke.jsonl`).

## Step 4 - Sanity-check

Run a quick eval and confirm rows are picked up:

```bash
agentops eval run
```

Open `.agentops/results/latest/report.md` and confirm the row count
matches.

## Guardrails

- Do not invent customer data, real names, or sensitive content.
- Keep rows short - datasets are meant to be quick gates, not full QA
  suites.
- If the user already has a domain dataset, prefer pointing
  `agentops.yaml` at that file rather than generating new rows.
