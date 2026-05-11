---
name: agentops-eval
description: Run AgentOps evaluations end-to-end against any agent (Foundry hosted/prompt agent, HTTP/JSON endpoint, or raw model deployment). Trigger on phrases like "run eval", "evaluate my agent", "benchmark", "agentops eval", "compare runs". Uses the flat agentops.yaml schema.
---

# AgentOps Eval

End-to-end workflow: install → init → configure → run → read report.

## Step 0 — Setup

1. Install if missing: `pip install "agentops-toolkit[foundry] @ git+https://github.com/Azure/agentops.git@develop"`.
2. If `agentops.yaml` does not exist at the project root, run `agentops init`.

## Step 1 — Identify the agent target

Read the codebase (README, entry point, env vars) and pick the right value
for the `agent:` field of `agentops.yaml`:

| Pattern in code / env | `agent:` value |
|---|---|
| `AIProjectClient`, `azure-ai-projects`, Foundry agent ID like `name:1` | `"<name>:<version>"` (Foundry prompt agent) |
| Foundry hosted agent endpoint URL ending in `/agents/...` | `"https://<resource>.services.ai.azure.com/api/projects/<p>/agents/..."` |
| Plain HTTP/JSON endpoint (FastAPI, Express, ACA, AKS) | `"https://<host>/<path>"` |
| Raw Foundry/Azure OpenAI model deployment | `"model:<deployment-name>"` |

If nothing is found, ask the user once for the agent identifier.

## Step 2 — Make sure the dataset exists

`agentops.yaml` points to a JSONL file (default
`.agentops/data/smoke.jsonl`). Each row needs at least `input` and a label
that maps to the metric you care about (`expected`, `context`,
`tool_calls`...). If the dataset is empty or unrelated, run the
`agentops-dataset` skill before running the eval.

## Step 3 — Run the evaluation

```bash
agentops eval run
```

Optional flags:

- `--config <path>` — point at a different `agentops.yaml`.
- `--output <dir>` — choose where to write `results.json` and `report.md`
  (defaults to `.agentops/results/<timestamp>/`).

Exit codes:

- `0` — succeeded and all thresholds passed
- `2` — succeeded but at least one threshold failed (gate-friendly)
- `1` — runtime/configuration error

## Step 4 — Inspect results

```bash
agentops report generate                   # regenerate report.md from latest results.json
agentops report generate --in <results.json>
```

Open `.agentops/results/latest/report.md`. To compare two runs, hand both
`results.json` files to the user or run the next eval with
`--baseline <previous-results.json>` so AgentOps adds a **Comparison vs
Baseline** section to the report.

## Step 5 — (Optional) Foundry execution / visibility

Two modes are supported. Both write a deep-link into
`.agentops/results/latest/cloud_evaluation.json` and require
`AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` (or the inline `project_endpoint`).

**Classic Foundry Evaluations panel** (works for any target kind):
AgentOps runs locally first, then uploads the metrics it computed.

```yaml
execution: local
publish: true
# project_endpoint: "https://<resource>.services.ai.azure.com/api/projects/<p>"
```

**New Foundry Evaluations panel** (preview): Foundry runs the agent +
evaluators server-side via the OpenAI Evals API. Only works for
`name:version` Foundry agents. `publish` is implicit — a cloud run is
always recorded by Foundry.

```yaml
execution: cloud
# project_endpoint: "https://<resource>.services.ai.azure.com/api/projects/<p>"
```

With `execution: local` and no `publish: true`, AgentOps runs locally
and only writes local artifacts.

## Tips

- Evaluators are auto-selected from the agent type and dataset columns.
  Override only when needed via the `evaluators:` block — most users do
  not need it.
- Set thresholds in `thresholds:` to gate CI:
  ```yaml
  thresholds:
    coherence: ">=3"
    avg_latency_seconds: "<=10"
  ```
- For HTTP/JSON agents that need auth, set
  `auth_header_env: MY_TOKEN_VAR` and AgentOps adds
  `Authorization: Bearer $MY_TOKEN_VAR`.
