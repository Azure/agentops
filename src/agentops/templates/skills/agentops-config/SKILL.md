---
name: agentops-config
description: Generate or update agentops.yaml (flat 1.0 schema) for AgentOps release-readiness gates. Trigger on "configure agentops", "agentops.yaml", "set up evaluation", "what should I evaluate". Infer the agent target and dataset from the codebase; ask only when nothing can be found.
---

# AgentOps Config

Generate `agentops.yaml` at the project root. This file references the agent
candidate and dataset used to answer "can we ship it?" The flat schema has only
a handful of fields - most projects need just `version`, `agent`, and
`dataset`.

This skill configures AgentOps release gates. It does **not** create or deploy
Foundry agents. If the user needs to create/deploy a Prompt Agent or Hosted
Agent first, hand off to Foundry Toolkit / the `microsoft-foundry` skill / azd,
then return here once there is a `name:version` or URL.

## Step 0 - Prerequisites

1. `pip install "agentops-toolkit[foundry] @ git+https://github.com/placerda/agentops.git@foundry-operate-readiness"` if `agentops` is missing.
2. Run `agentops eval analyze` first. If it reports missing or ambiguous
   target/dataset/scenario signals, use this skill to adapt the config.
3. If `agentops.yaml` does not exist, run `agentops init` first. The init
   wizard already collects the agent reference and dataset path, so
   `agentops-config` is most useful when the user wants to **tweak** an
   existing config (add thresholds, switch to a different agent target,
   add HTTP auth headers, etc.) rather than create one from scratch.

## Step 1 - Detect the agent target

Search the codebase for the strongest signal and pick one:

| Signal | `agent:` value |
|---|---|
| Foundry Prompt Agent ID `name:N` | `"<name>:<N>"` |
| Foundry Hosted Agent URL `https://...services.ai.azure.com/...agents/...` | the full URL |
| Any other HTTP endpoint your agent serves (FastAPI, Express, ACA, AKS) | the full URL |
| Direct model use (`openai.chat.completions.create(model=...)`) with no orchestration | `"model:<deployment-name>"` |

Look in: `README.md`, `main.py`/`server.py`/`app.ts`, `.env`/`.env.local`,
`.azure/<env>/.env`, `infra/`, IaC outputs. If nothing is found, ask the
user once.

## Step 2 - Detect the dataset

If a JSONL with rows that include `input` already exists in the repo, use
its path. Otherwise leave the default `.agentops/data/smoke.jsonl` and
hand off to the `agentops-dataset` skill before the first run.

## Step 3 - Write agentops.yaml

Minimal example:

```yaml
version: 1
agent: "my-rag:3"
dataset: .agentops/data/smoke.jsonl
```

HTTP/JSON example:

```yaml
version: 1
agent: "https://my-aca-app.eastus2.azurecontainerapps.io/chat"
dataset: .agentops/data/smoke.jsonl
request_field: message      # default is "message"
response_field: text         # dot-path; default is "text"
auth_header_env: MY_API_TOKEN
```

Optional extras (only add when the user asks for them):

```yaml
thresholds:
  coherence: ">=3"
  groundedness: ">=3"
  avg_latency_seconds: "<=30"

# Publish results to the Foundry Evaluations panel.
# - execution: local + publish: true  → Classic Foundry (uploads metrics)
# - execution: cloud                  → New Foundry (server-side run;
#                                       publish is implicit, cloud always publishes)
execution: local
publish: true
# project_endpoint: "https://<resource>.services.ai.azure.com/api/projects/<p>"

# Cloud dataset submission policy. The local JSONL remains the source of truth;
# cloud runs sync it to Foundry Data/Datasets by default.
dataset_sync:
  mode: auto            # auto | foundry | inline
  # name: agentops-smoke
  # version: content-hash

evaluators:           # rare - AgentOps auto-selects from agent + dataset
  - name: similarity
    threshold: ">=4"
```

## Step 4 - Validate

Run `agentops eval run` once. If the config is malformed AgentOps prints a
clear error pointing at the offending key. Adjust and re-run.

## Guardrails

- Do **not** add legacy keys (`bundle`, `target`, `execution`, `output`,
  `backend`). The 1.0 schema rejects them.
- Do **not** fabricate agent IDs, endpoint URLs, or model deployment
  names. Ask the user when uncertain.
- Keep the file small. Auto-selection covers most metrics.
- Keep local JSONL canonical. For cloud runs, prefer `dataset_sync.mode: auto`
  so AgentOps keeps Foundry Data/Datasets in sync; use `inline` only for quick
  experiments or environments without dataset upload permission.
