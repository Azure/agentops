---
name: agentops-eval
description: Run AgentOps release-readiness evaluations against Foundry prompt agents, Foundry hosted endpoints, HTTP/JSON agents, or raw model deployments. Trigger on phrases like "run eval", "evaluate my agent", "benchmark", "agentops eval", "compare runs", "can we ship". Uses the flat agentops.yaml schema.
---

# AgentOps Eval

End-to-end release-gate workflow: install -> init -> configure -> run -> read
report -> decide whether the candidate is ready to ship.

AgentOps evaluates an existing candidate. It does **not** create or deploy
Foundry agents. If the user still needs a Prompt Agent or Hosted Agent, hand off
to Foundry Toolkit / the `microsoft-foundry` skill / azd first, then come back
with a `name:version` or URL.

## Step 0 - Setup

1. Install if missing: `pip install "agentops-accelerator[foundry] @ git+https://github.com/Azure/agentops.git@main"`.
2. If `agentops.yaml` does not exist at the project root, run `agentops init`.
   The init wizard prompts (azd-style) for the Foundry project endpoint,
   agent reference, and dataset path, persists each answer to
   `.agentops/.env` + `agentops.yaml` as it goes. Existing azd workspaces, or
   runs with `--azd-env`, use `.azure/<env>/.env` instead. Pass `--no-prompt`
   plus the explicit flags
   (`--project-endpoint`, `--agent`, `--dataset`, …) for non-interactive
   runs. Run `agentops init show` later to inspect the resolved config.

## Step 1 - Analyze evaluation setup

Run the deterministic local triage first:

```bash
agentops eval analyze
```

Use its output to decide whether the repo is ready for `agentops eval run` or
needs skill-assisted setup. If it recommends `agentops-config`, fix the target
and protocol. If it recommends `agentops-dataset`, create/map realistic JSONL
rows. If it recommends `agentops-eval`, inspect the app scenario and evaluator
expectations before running.

## Step 2 - Identify the agent target

Read the codebase (README, entry point, env vars) and pick the right value
for the `agent:` field of `agentops.yaml`:

| Pattern in code / env | `agent:` value |
|---|---|
| Foundry Prompt Agent ID like `name:1` | `"<name>:<version>"` |
| Foundry Hosted Agent endpoint URL ending in `/agents/...` | `"https://<resource>.services.ai.azure.com/api/projects/<p>/agents/..."` |
| Plain HTTP/JSON endpoint (FastAPI, Express, ACA, AKS) | `"https://<host>/<path>"` |
| Raw Foundry/Azure OpenAI model deployment | `"model:<deployment-name>"` |

If nothing is found, ask the user once for the agent identifier.

## Step 3 - Make sure the dataset exists

`agentops.yaml` points to a JSONL file (default
`.agentops/data/smoke.jsonl`). Each row needs at least `input` and a label
that maps to the metric you care about (`expected`, `context`,
`tool_calls`...). If the dataset is empty or unrelated, run the
`agentops-dataset` skill before running the eval.

## Step 4 - Run the evaluation

```bash
agentops eval run
```

Optional flags:

- `--config <path>` - point at a different `agentops.yaml`.
- `--output <dir>` - choose where to write `results.json` and `report.md`
  (defaults to `.agentops/results/<timestamp>/`).

Exit codes:

- `0` - succeeded and all thresholds passed
- `2` - succeeded but at least one threshold failed (gate-friendly)
- `1` - runtime/configuration error

## Step 4b - Pick the right execution path

| Target | Foundry server-side eval through AgentOps | AgentOps local runner | Default guidance |
|---|---|---|---|
| Foundry Prompt Agent (`name:version`) | `execution: cloud` | yes | Use cloud when the user wants the official Foundry-hosted eval record; use local for fast feedback or fallback. |
| Foundry Hosted Agent URL | no | yes | Use local runner; optionally set `publish: true` to upload local metrics to Classic Foundry. |
| Generic HTTP/JSON endpoint | no | yes | Use local runner. |
| Raw model deployment | no | yes | Use local runner. |

For prompt-agent CI gates, prefer AgentOps cloud eval because Foundry executes
the managed eval while AgentOps enforces thresholds and writes normalized
`results.json` / `report.md` artifacts. The official AI Agent Evaluation GitHub
Action or Azure DevOps extension is still useful for standalone platform-native
validation, but do not substitute it for the AgentOps PR gate when the user needs
threshold enforcement, baselines, Doctor readiness, release evidence, or local
fallback.

## Step 5 - Inspect results and release evidence

```bash
agentops report generate                   # regenerate report.md from latest results.json
agentops report generate --in <results.json>
```

Open `.agentops/results/latest/report.md`. To compare two runs, hand both
`results.json` files to the user or run the next eval with
`--baseline <previous-results.json>` so AgentOps adds a **Comparison vs
Baseline** section to the report.

For production promotion, generate the Doctor evidence pack:

```bash
agentops doctor --evidence-pack
```

Open `.agentops/release/latest/evidence.md`. It summarizes eval, baseline,
Doctor, workflow, Foundry, monitoring, AI Landing Zone, and trace-regression
readiness without creating a second exit-code contract.

## Step 5b - (Optional) Promote reviewed traces to regression rows

If the user has exported Foundry/App Insights traces, preview candidate
regression rows first:

```bash
agentops eval promote-traces --source <traces.jsonl>
```

Only write files after review:

```bash
agentops eval promote-traces --source <traces.jsonl> --apply
```

Default `self-similarity` labels are for drift detection, not human-verified
ground truth. Use `--label-mode pending` when reviewers must fill expected
answers before the dataset gates releases.

## Step 6 - (Optional) Foundry execution / visibility

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
`name:version` Foundry agents. `publish` is implicit - a cloud run is
always recorded by Foundry. The local JSONL remains the dataset source of
truth; AgentOps syncs it to Foundry Data/Datasets by default and uses that
dataset version in the Evals run.

```yaml
execution: cloud
# project_endpoint: "https://<resource>.services.ai.azure.com/api/projects/<p>"
dataset_sync:
  mode: auto            # sync local JSONL to Foundry Data/Datasets
```

With `execution: local` and no `publish: true`, AgentOps runs locally
and only writes local artifacts.

After a cloud run, inspect `.agentops/results/latest/cloud_evaluation.json`.
Its `dataset` block explains whether the run used inline rows or a Foundry
dataset reference.

## Tips

- Evaluators are auto-selected from the agent type and dataset columns.
  Override only when needed via the `evaluators:` block - most users do
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
