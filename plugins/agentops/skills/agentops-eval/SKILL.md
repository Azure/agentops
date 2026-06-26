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

1. Install if missing: `pip install "agentops-accelerator @ git+https://github.com/Azure/agentops.git@main"`.
2. If `agentops.yaml` does not exist at the project root, run `agentops init`.
   The init wizard prompts (azd-style) for the Foundry project endpoint,
   agent reference, and dataset path, persists each answer to
   `.agentops/.env` + `agentops.yaml` as it goes. Existing azd workspaces, or
   runs with `--azd-env`, use `.azure/<env>/.env` instead. Pass `--no-prompt`
   plus the explicit flags
   (`--project-endpoint`, `--agent`, `--dataset`, …) for non-interactive
   runs. Run `agentops init show` later to inspect the resolved config.

## Step 0.5 - Ensure agent-build and data-plane RBAC on the AI Services account

AgentOps eval (cloud graders **and** local AI-assisted evaluators) calls
`/openai/deployments/.../chat/completions` on the AI Services account
that backs the Foundry project. Creating a project through the Foundry
portal only assigns the user `Foundry User` at the *project* scope,
which does **not** cover the parent account. The Foundry UI may then block
agent creation with "You don't have permission to build agents in this
project" and ask for the old portal label, `Azure AI User`; the current Azure
RBAC role is `Foundry User` (`53ca6127-db72-4b80-b1b0-d745d6d5456d`).

You also need `Cognitive Services OpenAI User`
(`5e0bd9bd-7b93-4f28-af87-19fc36ad61bd`) for OpenAI data-plane actions on
the parent account. Subscription `Owner` is insufficient because the built-in
`Owner` role has `actions: ["*"]` but `dataActions: []`. The first
`agentops eval run` against a fresh workspace otherwise fails with:

```
PermissionDenied … lacks the required data action
'Microsoft.CognitiveServices/accounts/OpenAI/deployments/chat/completions/action'
```

Run this preflight before Step 1. It must grant `Foundry User` and
`Cognitive Services OpenAI User` to the signed-in user on the AI Services
account, plus the OpenAI data-plane role to any Foundry/Azure AI managed
identities in the resource group. Cloud evaluations run server-side and some
graders authenticate as those managed identities, so assigning only the user
can still produce intermittent `AuthenticationError` grader failures. The
commands are idempotent (`RoleAssignmentExists` means the role was already
granted):

```bash
# 1. Resolve the AI Services account from agentops.yaml / .azure/<env>/.env
PROJECT_ENDPOINT=$(grep -h '^AZURE_AI_FOUNDRY_PROJECT_ENDPOINT' .azure/*/.env .agentops/.env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"')
ACCOUNT_HOST=$(echo "$PROJECT_ENDPOINT" | awk -F[/:] '{print $4}')
ACCOUNT_NAME=$(echo "$ACCOUNT_HOST" | cut -d. -f1)

# 2. Resolve subscription, resource group, account scope, and signed-in object ID
SUB_ID=$(az account show --query id -o tsv)
RG=$(az cognitiveservices account list --subscription "$SUB_ID" --query "[?name=='$ACCOUNT_NAME'].resourceGroup | [0]" -o tsv)
ACCOUNT_ID=$(az cognitiveservices account show -g "$RG" -n "$ACCOUNT_NAME" --query id -o tsv)
OBJ_ID=$(az ad signed-in-user show --query id -o tsv)

# 3. Grant the user portal build access and data-plane access at account scope.
az role assignment create \
  --assignee "$OBJ_ID" \
  --role "53ca6127-db72-4b80-b1b0-d745d6d5456d" \
  --scope "$ACCOUNT_ID"

az role assignment create \
  --assignee "$OBJ_ID" \
  --role "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd" \
  --scope "$ACCOUNT_ID"

# 4. Grant the same data-plane role to Foundry/Azure AI managed identities.
az resource list -g "$RG" \
  --query "[?identity.principalId!=null].identity.principalId" -o tsv |
while read -r PRINCIPAL_ID; do
  [ -z "$PRINCIPAL_ID" ] && continue
  az role assignment create \
    --assignee-object-id "$PRINCIPAL_ID" \
    --assignee-principal-type ServicePrincipal \
    --role "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd" \
    --scope "$ACCOUNT_ID"
done
```

PowerShell equivalent: replace `$(...)` with the PowerShell variable
assignments shown in `docs/tutorial-prompt-agent.md`.

If the user has not run `az login` yet, do that first. If
`az cognitiveservices account list` returns an empty RG, the AI Services
account lives in a different subscription - ask the user which one.

Skip this step only if the user explicitly says both roles are already
assigned on the parent AI Services account, or if the user can already build
agents in the Foundry UI and a previous `agentops eval run` succeeded against
the same Foundry account.

**Propagation:** data-plane role assignments do not take effect
instantly — allow several minutes (occasionally up to ~15) before the
first eval. The cloud/local graders authenticate per call, so if the
user runs an eval immediately after this preflight and sees intermittent
`AuthenticationError` on a subset of graders plus
`Threshold status: FAILED` while the visible thresholds are green, that
is propagation lag (a grader **execution** failure), not a quality
regression. Tell the user to wait a few minutes and re-run
`agentops eval run`; do not treat it as a failing gate or start changing
thresholds.

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

For Foundry Prompt Agents authored in the Sandbox portal, do not copy/paste the
instructions into a file manually. After `agentops.yaml` contains `agent:
name:version` and the correct project endpoint is available from
`agentops.yaml`, `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`, or the active
`.azure/<env>/.env`, run:

```bash
agentops prompt pull
```

This writes `.agentops/prompts/<agent-name>.prompt.md` by default, updates
`prompt_file` in `agentops.yaml` when needed, prints the resolved endpoint and
agent version before writing, validates that the Foundry definition is a prompt
agent, and refuses to overwrite changed prompt files unless `--force` is used.
Use `--out` only when the repository already has a stronger prompt-file
convention.

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
| azd `eval.yaml` recipe | `execution: azd` | no fallback | Use `agentops eval init` / `azd ai agent eval` for Foundry prompt/hosted agents when the user wants the public-preview azd AI agent eval workflow. |

For prompt-agent CI gates, prefer AgentOps cloud eval because Foundry executes
the managed eval while AgentOps enforces thresholds and writes normalized
`results.json` / `report.md` artifacts. The official AI Agent Evaluation GitHub
Action or Azure DevOps extension is still useful for standalone platform-native
validation, but do not substitute it for the AgentOps PR gate when the user needs
threshold enforcement, baselines, Doctor readiness, release evidence, or local
fallback.

When an `eval.yaml` includes Rubric evaluator dimensions, keep thresholds in
`agentops.yaml` aligned to the dimension metric names (for example
`booking_accuracy: ">=0.8"`). AgentOps binds Rubric/custom dimensions literally
and fails closed when a configured threshold has no matching emitted metric.

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
readiness without creating a second exit-code contract. If the repo has ASSERT,
ACS, or red-team evidence artifacts, use the `agentops-governance` skill to wire
their paths into `agentops.yaml` before generating the evidence pack.

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
  `Authorization: Bearer $MY_TOKEN_VAR`. For a shared-secret gate, override the
  header with `auth_header_name: X-API-KEY` and `auth_value_template: "{token}"`.
- For streaming HTTP agents (e.g. an SSE `text/event-stream` endpoint), set
  `response_mode: sse` (each `data:` line) or `response_mode: text` (raw
  streamed text). Use the optional `stream:` block to tune aggregation:
  `text_field` (dot-path to the token text when `data:` lines are JSON),
  `done_marker` (e.g. `[DONE]`), and `strip_leading_token: true` (drop a leading
  `conversation_id` prefix). `response_mode: json` (default) is unchanged.
