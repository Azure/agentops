# HTTP agent to dev

Use this tutorial when your agent runs as an HTTP service behind a URL, not as a
Foundry-managed prompt agent. The worked example is the GPT-RAG orchestrator
deployed by the `Azure/gpt-rag` template, which runs its `maf_lite` strategy as a
FastAPI app inside an Azure Container App. You deploy it, take ownership of the
cloned orchestrator, and add an AgentOps PR gate that evaluates the HTTP endpoint
before merge.

The path is the same sandbox to dev story as the other tutorials, adapted for an
endpoint-based agent:

```mermaid
flowchart LR
    E["<b>Evaluate</b><br/>Deploy the sandbox<br/>Run evals<br/>Catch weak answers"]
    S["<b>Ship</b><br/>Own the repo<br/>Open PR<br/>Deploy to dev"]
    O["<b>Observe</b><br/>Read traces<br/>Run Doctor<br/>Check telemetry"]
    W["<b>Own</b><br/>Review evidence<br/>Block regressions<br/>Make the call"]

    E --> S --> O --> W
```

Sandbox is the environment you deploy first and experiment against. Dev is the
shared environment CI deploys and evaluates on every PR. The PR gate proves the
deployed HTTP endpoint still passes your thresholds before a merge ships it.

!!! info "HTTP agent vs Foundry prompt agent"
    A Foundry prompt agent is referenced as `name:version` and hosted by
    Foundry. An HTTP agent is any service you call at a URL. The GPT-RAG
    orchestrator answers over HTTP because `maf_lite` is its default strategy,
    so you evaluate it by posting requests to its endpoint, not by staging a
    prompt version.

## Before you run the tutorial

Have these ready once, so the walkthrough stays on the deploy and evaluate flow
instead of permission prompts.

- Azure Developer CLI (`azd`) and Azure CLI (`az`), both signed in to the
  subscription and tenant that will host the deployment.
- The Copilot CLI with the `agentops` and `microsoft-foundry` skills installed,
  so the agent can read the repo and propose the GitHub and Azure setup steps.
- Permission to create resources in the target subscription, and push access to
  a GitHub repository you control for the orchestrator.
- A Foundry project with a chat-capable deployment for the judge model that
  AgentOps uses to score answers. See [Evaluation](evaluation.md) for how
  scoring works.

## 1. Deploy the sandbox

Create the GPT-RAG workspace from the template. The first azd environment is your
sandbox.

```powershell
azd init -t Azure/gpt-rag
```

Name the environment with a unique suffix so it does not collide with anyone
else's resource names, for example `gptrag-sandbox-2606182303` (the pattern is
`gptrag-sandbox-yymmddhhmm`).

azd downloads the template into a `gpt-rag` directory. Change into it, then set
the required values:

```powershell
cd gpt-rag
azd env set AZURE_LOCATION <region>
azd env set AZURE_SUBSCRIPTION_ID <subscription-id>
```

!!! tip "Why a unique name"
    The azd environment name seeds globally unique Azure resource names like the
    storage account. A plain `sandbox` often clashes with another deployment, so
    a timestamp suffix keeps yours distinct.

Provision and deploy everything:

```powershell
azd up
```

!!! info "What the deploy does"
    A predeploy hook reads `manifest.json` and clones each component from
    upstream. The orchestrator is cloned into a sibling `gpt-rag-orchestrator`
    directory, pinned to tag `v2.8.6`, and built into a container image. Because
    `maf_lite` is the orchestrator's default strategy, the deployed orchestrator
    answers over HTTP at `POST /orchestrator`.

## 2. Stand up a dev environment

Create a second environment in the same checkout, set its values, and deploy it.
Give it its own unique suffix, for example `gptrag-dev-2606182303` (pattern
`gptrag-dev-yymmddhhmm`).

```powershell
azd env new gptrag-dev-2606182303
azd env set AZURE_LOCATION <region>
azd env set AZURE_SUBSCRIPTION_ID <subscription-id>
azd up
```

!!! info "Why a separate dev environment"
    The PR gate must evaluate a deployed target that is not your sandbox
    playground. Dev is the environment CI deploys and evaluates on every PR, so
    a passing gate means the reviewed change is healthy in a real deployment.
    See [Ship](ship.md) and [Evaluation](evaluation.md) for the release
    contract and scoring depth.

## 3. Index a document so the agent has something to answer

Your agent grounds its answers on indexed content, so give it one document to
work with. This tutorial uses a short sample manual.

[Download the sample document](media/vw-fuel-system.pdf) is the "Fuel System"
section of a Volkswagen service manual, 28 pages covering the carbureted 1968
through 1974 models. Save it locally.

Index it into the knowledge base behind the environment you will evaluate. With
the GPT-RAG template that is the `documents` blob container in that environment's
storage account; dropping a file there triggers ingestion, which chunks, embeds,
and indexes it into Azure AI Search:

```powershell
az storage blob upload `
  --account-name <storage-account> `
  --container-name documents `
  --file "vw-fuel-system.pdf" `
  --name "vw-fuel-system.pdf" `
  --auth-mode login
```

Ingestion runs in the background, so give it a couple of minutes before you
expect grounded answers.

!!! note "This document is a scanned PDF"
    The sample is image-only, with no text layer. GPT-RAG ingestion runs OCR, so
    it still indexes cleanly. Any knowledge base works the same way: the only
    thing that matters is that your agent has indexed content to ground on. If
    your agent reads a different store, index the document there instead. The rest
    of the tutorial just assumes the agent can answer questions about this
    document.

## 4. Take ownership of the cloned orchestrator

The agent you evaluate lives in the cloned orchestrator, so work from that
directory.

```powershell
cd ../gpt-rag-orchestrator
git remote -v
```

You will see `origin` pointing at the upstream project, checked out at the pinned
tag in a detached state:

```text
origin  https://github.com/azure/gpt-rag-orchestrator.git (fetch)
origin  https://github.com/azure/gpt-rag-orchestrator.git (push)
```

!!! warning "This intentionally disconnects from upstream"
    This tutorial makes the orchestrator your own service to evaluate and
    deploy. Removing `origin` detaches it from the GPT-RAG open source project
    so your commits and CI never target upstream. Do this only in your own copy.

Remove the upstream remote and start your own history:

```powershell
git remote remove origin
git checkout -b main
git add -A
git commit -m "Initial commit: my GPT-RAG orchestrator copy"
```

Then create your repository and push it with the GitHub CLI. Pick a name that
does not collide with a fork you may already have, for example
`gpt-rag-orchestrator-agentops`:

```powershell
gh repo create <owner>/gpt-rag-orchestrator-agentops --private --source . --push
```

!!! tip "Use a distinct repo name"
    `gh repo create` names a brand-new repo from the current folder, regardless
    of the local directory name. If you already keep a fork at
    `<owner>/gpt-rag-orchestrator`, give this one a different name like
    `gpt-rag-orchestrator-agentops` so your own copy stays easy to tell apart.

!!! note "The clone is shallow"
    The predeploy hook clones with `--depth 1`, so you only have the pinned
    commit. `git checkout -b main` simply names a branch at that commit, which
    is all you need to make it your own repo.

!!! tip "Ignore the upstream evaluations and pipelines"
    The vendored orchestrator ships its own `evaluations/` and `dataset/`
    folders and its own CI under `.github/workflows/` (`pr_pipeline.yaml`,
    `cicd_pipeline.yaml`, `block-pr-to-main.yml`). Those belong to the upstream
    project. Since you are building your own copy, you can ignore or delete them.
    AgentOps gives you your own eval dataset (Section 6) and generates your own
    workflows (Section 9), so nothing here depends on the orchestrator's.

    ```powershell
    # optional: remove the inherited eval pipeline and CI so only AgentOps runs
    Remove-Item -Recurse -Force evaluations
    Remove-Item -Force .github/workflows/*
    ```

## 5. Initialize AgentOps against the maf_lite endpoint

The orchestrator streams its answers as Server-Sent Events, and AgentOps reads
streamed responses natively, so you point it straight at `POST /orchestrator`.
No adapter route is needed.

!!! info "The orchestrator streams, AgentOps reads it natively"
    `POST /orchestrator` returns Server-Sent Events (`text/event-stream`): a
    conversation id followed by streamed answer chunks. AgentOps `http-json`
    reads the stream when you set `response_mode: text`, drops the leading
    conversation id, and scores the final answer. This needs AgentOps 0.4.4 or
    newer.

Get the dev orchestrator URL. The Container App name and endpoint are stored in
the deployment's App Configuration as `ORCHESTRATOR_APP_NAME` and
`ORCHESTRATOR_APP_ENDPOINT`; you can also read the ingress host directly:

```powershell
az containerapp show -n <ORCHESTRATOR_APP_NAME> -g <resource-group> --query properties.configuration.ingress.fqdn -o tsv
```

The `POST /orchestrator` route authenticates with a shared secret sent as the
`X-API-KEY` header (or skips auth when `DISABLE_AUTH=true` on dev). Export the
dev orchestrator key locally so AgentOps can send it. The key is the
deployment's `ORCHESTRATOR_APP_APIKEY`; never commit it:

```powershell
$env:ORCHESTRATOR_APP_APIKEY = "<dev-orchestrator-api-key>"
```

Sign in and run the wizard inside the orchestrator repo:

```powershell
az login
agentops init
```

Answer the prompts with the dev orchestrator values:

| Prompt | Answer |
|---|---|
| Foundry project endpoint | The dev Foundry project endpoint for the judge model, or press Enter to set it later. |
| Agent | The dev orchestrator URL, for example `https://<orchestrator-fqdn>/orchestrator`. |
| Dataset path | `.agentops/data/vw-smoke.jsonl` |

Then edit `agentops.yaml` so AgentOps reads the streamed response correctly:

```
edit agentops.yaml
```

```yaml
version: 1
agent: https://<orchestrator-fqdn>/orchestrator
dataset: .agentops/data/vw-smoke.jsonl
protocol: http-json
request_field: ask
response_mode: text
stream:
  strip_leading_token: true
auth_header_name: X-API-KEY
auth_value_template: "{token}"
auth_header_env: ORCHESTRATOR_APP_APIKEY
evaluators:
  relevance: ">=3"
  coherence: ">=3"
```

| Field | What it does |
|---|---|
| `agent` | The dev orchestrator URL AgentOps calls with `POST`. |
| `protocol: http-json` | Send one JSON request; here AgentOps reads a streamed response. |
| `request_field: ask` | Put each dataset input under the `ask` key, matching the orchestrator's own field name. |
| `response_mode: text` | Read the `text/event-stream` body and aggregate it into one answer instead of parsing a single JSON body. |
| `stream.strip_leading_token: true` | Drop the leading conversation id the orchestrator emits as its first chunk. |
| `auth_header_name: X-API-KEY` | Send the shared secret in the `X-API-KEY` header instead of `Authorization`. |
| `auth_value_template: "{token}"` | Send the raw token as the header value, with no `Bearer ` prefix. |
| `auth_header_env: ORCHESTRATOR_APP_APIKEY` | Read the secret from this env var; nothing is written to `agentops.yaml`. |
| `evaluators.relevance` / `evaluators.coherence` | Score each answer for on-topic relevance and readable coherence, requiring at least 3 out of 5. This smoke-core checks the agent answers sensibly, not that it is grounded. |

!!! note "How AgentOps calls the endpoint"
    AgentOps posts `{"ask": "<input>"}` with `Content-Type: application/json` and
    the `X-API-KEY` header from `ORCHESTRATOR_APP_APIKEY`, reads the streamed
    `text/event-stream` response, drops the leading conversation id, and scores
    the aggregated answer. The default `request_field` is `message`; you set it
    to `ask` because that is the orchestrator's vocabulary. If your endpoint
    emits structured `data:` JSON frames instead of raw text, set
    `response_mode: sse` and add `stream.text_field` to point at the token text.

## 6. Create the eval dataset

Create a small JSONL dataset grounded in the document you indexed. Each row is
one line of JSON: an `input` to ask and an `expected` describing the behavior you
want.

```
edit .agentops/data/vw-smoke.jsonl
```

```json
{"input":"What is the fuel tank capacity of the Volkswagen described in the manual?","expected":"States the fuel tank holds 15.8 U.S. gallons (about 60 liters) and sits beneath the rear luggage area ahead of the engine. On topic and consistent with the manual."}
{"input":"Which carburetor did the 1970 model use?","expected":"Identifies a single Solex 30 PICT-3 carburetor for the 1970 model. Concise and on topic."}
{"input":"What does the evaporative emission control system do?","expected":"Explains it keeps gasoline fumes from escaping to the atmosphere by venting the tank into a system that traps fuel vapors until the engine burns them, standard from the 1970 models. On topic and consistent with the manual."}
{"input":"What is the 0 to 100 km/h time of the latest electric Volkswagen ID.4?","expected":"Makes clear the indexed document does not cover modern electric models and does not invent a figure."}
```

!!! note "input maps to ask"
    AgentOps reads the `input` field from each row and sends it as `ask`. The
    `expected` values are acceptance criteria for judge-based scoring, not exact
    answer strings, so write them as reviewable behavior.

!!! warning "Smoke-core is relevance and coherence, not groundedness"
    The endpoint returns only the final text, not the retrieved context, so the
    judge cannot measure true groundedness here. This smoke-core scores relevance
    and coherence: the answer is on topic and reads sensibly. The first three rows
    should pass once the document is indexed; the last row checks that the agent
    refuses to invent facts the source does not contain. To measure real
    groundedness, evaluate a target that also returns its retrieved context. See
    [Evaluation](evaluation.md).

## 7. Run evals locally against the sandbox

With the dataset and target set, run the gate from the orchestrator repo:

```powershell
agentops eval run
```

You should see a `Threshold status` line and normalized output written under
`.agentops/results/latest/`.

!!! info "What eval run checks"
    It sends each dataset row to the orchestrator endpoint, scores the responses with the
    judge model, applies your thresholds, and writes `results.json` and
    `report.md`. It exits zero when thresholds pass and non-zero when a
    threshold fails or the endpoint errors, which is exactly what lets the PR
    gate block a merge. See [Evaluation](evaluation.md) for thresholds and
    metric concepts.

## 8. See your evals and traces

Two views show what actually happened, and you want both.

**Per-row evidence (local).** Every run writes normalized output under
`.agentops/results/latest/`. Open `report.md` to read each input, the aggregated
answer, the judge scores, and pass or fail against your thresholds:

```powershell
code .agentops/results/latest/report.md
```

**Runtime traces (Azure Monitor / Foundry).** Set an Application Insights
connection string before the run so the spans land somewhere you can read them:

```powershell
$env:APPLICATIONINSIGHTS_CONNECTION_STRING = "<app-insights-connection-string>"
agentops eval run
```

Two different kinds of trace show up, from two different producers:

- **Evaluation traces** come from AgentOps itself. Each `agentops eval run`
  emits `agentops.eval.*` spans (one run, one span tree), and scheduled Doctor
  runs emit `agentops.agent.finding.*` spans. These exist even if the agent has
  no telemetry of its own.
- **Application/agent traces** come from the orchestrator at runtime, not from
  AgentOps. They appear only if the orchestrator app is instrumented to emit its
  own OpenTelemetry to App Insights. AgentOps and the Doctor read those traces
  (p95 latency, error rate) but do not generate them.

Open the traces in the Foundry project's tracing view, or query them in Azure
Monitor Logs. `agentops cockpit --workspace .` deep-links the same spans into one
readiness view.

!!! info "Eval evidence vs runtime traces"
    The local `report.md` is the fastest way to see why a row passed or failed.
    The `agentops.eval.*` spans are how the same runs show up in Foundry. The
    agent's own request traces are separate runtime telemetry the Doctor reads
    for latency and errors. See [Observe](observe.md).

## 9. Add ASSERT and Red Team safety gates

Quality is not enough to ship. Before you generate the workflows, add the two
safety gates so CI blocks unsafe behavior the same way it blocks quality
regressions. Both write normalized JSON the evidence pack ingests, and both can
fail the PR.

- **ASSERT** turns natural-language safety policies into executable behavior
  tests (refuse prompt injection, no fabricated facts, no stereotyping). It
  drives a model deployment plus system prompt through LiteLLM, so you target the
  same chat model your orchestrator uses and describe its intended behavior.
- **Red Team** runs Foundry's PyRIT-backed adversarial scan across risk
  categories and attack strategies. Its target resolves from the YAML as a model
  deployment, agent, or endpoint, so it can scan your orchestrator endpoint or
  its chat model.

### Scaffold both (recommended)

Let the governance skill write the config and the `agentops.yaml` blocks for your
target:

```text
/skills agentops-governance
```

Ask it to scaffold ASSERT and the Red Team runner for this workspace, target your
orchestrator's chat model deployment, judge with safety-core and alignment, and
fail Red Team when the attack success rate exceeds 20 percent.

### Or add the blocks yourself

```powershell
pip install assert-ai "azure-ai-evaluation[redteam]"
```

Append to `agentops.yaml`:

```yaml
assert:
  config: ./assert/eval_config.yaml
  fail_on_violations: true

redteam:
  target:
    model_deployment: <your-chat-model-deployment>
  risk_categories: [violence, hate_unfairness]
  attack_strategies: [base64]
  num_objectives: 3
  fail_on_attack_success_rate: 0.2
```

ASSERT calls models through LiteLLM, which for Azure OpenAI expects three vars in
your shell or `.agentops/.env`:

```powershell
$env:AZURE_API_KEY = "<your Azure OpenAI account key>"
$env:AZURE_API_BASE = "https://<resource>.openai.azure.com"
$env:AZURE_API_VERSION = "2024-10-21"
```

Run both gates:

```powershell
agentops assert run
agentops redteam run
```

ASSERT writes `.agentops/assert/latest.json` and Red Team writes
`.agentops/redteam/latest.json`. Each exits non-zero on a policy violation or
when the attack success rate exceeds your threshold, which is exactly what blocks
the PR. For the full config schema, behavior presets, risk categories, and attack
strategies, see
[Add ASSERT and Red Team to the release gate](tutorial-prompt-agent.md#12-add-assert-and-red-team-to-the-release-gate).

!!! warning "These hit live Azure services"
    Both runners call live models. Run them against a non-production deployment
    and keep the objective count small while you wire them up. Red Team's matrix
    is `risk_categories x attack_strategies x num_objectives` and grows quickly.

## 10. Generate the PR + dev deploy workflows

You build your own CI here. `agentops workflow generate` writes fresh,
AgentOps-owned GitHub Actions into your repo, it does not reuse whatever CI the
upstream orchestrator shipped. The orchestrator's `azure.yaml` is used only as
the deploy project, so the deploy mode is `azd`.

```powershell
agentops workflow generate --kinds pr,dev --deploy-mode azd --doctor-gate critical --force
```

This writes two files, both prefixed `agentops-` so they never collide with the
orchestrator's existing workflows:

- `.github/workflows/agentops-pr.yml` - the PR gate (eval + Doctor).
- `.github/workflows/agentops-deploy-dev.yml` - the dev deploy workflow.

| Flag | What it does |
|---|---|
| `--kinds pr,dev` | Generate both the PR gate and the dev deploy workflow. |
| `--deploy-mode azd` | Deploy through the orchestrator's azd project, running `azd provision` and `azd deploy`. |
| `--doctor-gate critical` | Fail the PR only on critical Doctor findings. |
| `--force` | Overwrite existing AgentOps workflow files. |

!!! note "These are your workflows, not the orchestrator's"
    The generated files are yours to edit and own. If the vendored orchestrator
    still carries upstream workflows under `.github/workflows/` that you do not
    want running, delete them so only your `agentops-*` workflows fire. You can
    re-run `agentops workflow generate` any time to regenerate yours.

!!! info "What the PR gate does"
    The generated PR workflow runs `agentops eval run` against the dev orchestrator endpoint
    from `agentops.yaml`, applies your thresholds, then runs Doctor with
    `--severity-fail critical`. A failing threshold or a critical finding blocks
    the merge. See [Ship](ship.md) for the OIDC, RBAC, and GitHub environment
    wiring instead of reproducing it here.

## 11. Ship, observe, and own

The repo now carries everything CI needs. Close the loop with the same three
section pages the other tutorials use.

```powershell
agentops doctor --evidence-pack
```

- **Ship.** Push the repo, configure the `dev` GitHub environment and Azure
  OIDC, and open a PR so the gate runs against the dev endpoint. See
  [Ship](ship.md).
- **Observe.** Read traces, telemetry, and Doctor findings for the dev run. See
  [Observe](observe.md).
- **Own.** Review the evidence pack, decide ship or no-ship, and open Cockpit for
  a single readiness view with `agentops cockpit --workspace .`. See
  [Own](own.md).

## What you walk away knowing

- You can tell an HTTP agent apart from a Foundry prompt agent, and why the
  GPT-RAG orchestrator is the former.
- You deployed the GPT-RAG template into a sandbox and a dev environment, and you
  know why the PR gate evaluates dev rather than sandbox.
- You took ownership of the cloned orchestrator by removing the upstream remote
  and starting your own repository.
- You pointed AgentOps directly at the orchestrator's streaming endpoint with
  `response_mode: text`, and you can map `ask` and `text` to the real request and
  response shape.
- You indexed a sample document, built a smoke dataset from its content, and
  scored answers on relevance and coherence, knowing why that is smoke and not
  groundedness.
- You inspected both the per-row eval evidence and the runtime traces, and you
  know which spans AgentOps emits (`agentops.eval.*`) versus which come from the
  orchestrator's own runtime telemetry.
- You added ASSERT and Red Team as required safety gates alongside the eval gate,
  so CI blocks unsafe behavior, not just quality regressions.
- You ran local evals against the deployed endpoint and generated a PR gate that
  blocks regressions before they merge.
