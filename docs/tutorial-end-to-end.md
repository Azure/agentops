# Tutorial — End-to-end with AgentOps

This is the long-form, do-it-yourself tour of AgentOps. By the end you
will have a real Foundry hosted agent under evaluation, a baseline
comparison run, four GitFlow CI/CD workflows running on GitHub, and a
watchdog report summarizing your run history.

It takes around 60–90 minutes the first time. Every step is concrete:
you copy a command, you see an artifact, you keep moving.

## What you will build

- A Foundry hosted agent created from the portal.
- A flat `agentops.yaml` pointing at that agent.
- A 5-row evaluation dataset.
- Two evaluation runs (a baseline and a "after a tweak" run) compared
  side-by-side.
- Four GitFlow workflows (`pr`, `dev`, `qa`, `prod`) wired to your own
  GitHub repository, gated on threshold pass/fail.
- A watchdog report combining your run history with optional
  Application Insights telemetry.

## Prerequisites

- Python 3.11 or later.
- Azure CLI (`az --version`) and `az login` working.
- An Azure AI Foundry project (`AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`).
- A model deployment in that project (`gpt-4o-mini` is enough).
- A GitHub account and the `gh` CLI (or use the web UI for pushes).
- An existing or new GitHub repo — empty is fine; we will populate it.

Set the project endpoint up front so every command picks it up:

```bash
export AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="https://<your-project>.services.ai.azure.com/api/projects/<project-name>"
export AZURE_OPENAI_ENDPOINT="https://<your-aoai-resource>.openai.azure.com"
export AZURE_OPENAI_DEPLOYMENT="gpt-4o-mini"
```

## 1. Install AgentOps

```bash
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install agentops-toolkit
agentops --version
```

## 2. Create a Foundry hosted agent

In the [Azure AI Foundry portal](https://ai.azure.com):

1. Open your project, then **Build → Agents → New agent**.
2. Name it `support-bot` (or any name).
3. Pick the model deployment from the prerequisites.
4. Paste a small system prompt:

   > *You are a concise factual assistant. Answer in one or two
   > sentences. If you don't know, say so.*

5. Save. The portal shows the agent name and a version number, e.g.
   `support-bot:1`.
6. Copy that `name:version` string — you will paste it into
   `agentops.yaml` next.

## 3. Initialize the workspace

In an empty folder (or the GitHub repo you want to use):

```bash
agentops init
```

You get:

```
.agentops/
├── agentops.yaml
├── data/
│   └── smoke.jsonl
├── datasets/
│   └── smoke.yaml
└── results/
.github/
└── skills/
    └── agentops-*/SKILL.md
```

Open `.agentops/agentops.yaml` and set the target to your hosted
agent:

```yaml
version: 1
agent: "support-bot:1"

dataset: ./data/smoke.jsonl

thresholds:
  coherence: ">=3"
  fluency: ">=3"
  similarity: ">=3"
  avg_latency_seconds: "<=10"
```

The `agent: "name:version"` shape is recognized as a **Foundry hosted
agent**. AgentOps invokes it through the Foundry project endpoint
using your `az login` credentials.

## 4. Author a tiny dataset

Replace `.agentops/data/smoke.jsonl` with five real questions:

```jsonl
{"input": "What is the capital of France?", "expected": "Paris."}
{"input": "Who wrote 'Pride and Prejudice'?", "expected": "Jane Austen."}
{"input": "What is the boiling point of water at sea level in Celsius?", "expected": "100 degrees Celsius."}
{"input": "Name the largest planet in the solar system.", "expected": "Jupiter."}
{"input": "What does HTTP stand for?", "expected": "HyperText Transfer Protocol."}
```

The dataset row shape decides which evaluators run. With `input` and
`expected` you get Coherence, Fluency, Similarity, F1Score, and
`avg_latency_seconds`. Add a `context` field to unlock RAG evaluators;
add `tool_calls` to unlock agent-workflow evaluators. See
[`tutorial-rag.md`](tutorial-rag.md) and
[`tutorial-agent-workflow.md`](tutorial-agent-workflow.md) for those
shapes.

## 5. Run your first evaluation

```bash
agentops eval run
```

The CLI:

1. Resolves the target from `agentops.yaml`.
2. Calls the Foundry hosted agent once per row.
3. Runs evaluators using `AZURE_OPENAI_DEPLOYMENT`.
4. Writes a timestamped run under `.agentops/results/<id>/` and
   updates `.agentops/results/latest/`.

Inspect the outputs:

```bash
cat .agentops/results/latest/report.md
```

The report has four sections you will revisit often:

- **Verdict** — one line: pass or fail.
- **Per-row transcript** — input, expected, agent response, metrics.
- **Aggregate metrics** — averages across rows.
- **Thresholds** — every rule from `agentops.yaml` with measured value.

The exit code is `0` (all thresholds passed) or `2` (one or more
failed). `1` means a runtime error.

## 6. Compare against a baseline

Snapshot the run id you just produced:

```bash
ls -1 .agentops/results
# 2026-04-28T15-30-12Z
export BASELINE=.agentops/results/2026-04-28T15-30-12Z/results.json
```

Now go back to the portal and tweak the system prompt — for example,
make it tell jokes instead of being concise. Save a new agent version
(e.g. `support-bot:2`) and update `agentops.yaml`:

```yaml
agent: "support-bot:2"
```

Re-run with the previous run as a baseline:

```bash
agentops eval run --baseline "$BASELINE"
```

The new `report.md` adds a **Comparison vs Baseline** section with
per-metric deltas. Watch `similarity` drop — the joke-telling agent
no longer matches the factual `expected` answers. This is the
regression-detection loop you will wire into CI next.

## 7. Generate the GitFlow workflows

```bash
agentops workflow generate
```

Four files appear under `.github/workflows/`:

| Workflow | Trigger | Purpose |
|---|---|---|
| `agentops-pr.yml` | Pull request opened against `develop` or `main` | Runs `agentops eval run` against the baseline; comments the report on the PR; gates merge on threshold pass/fail. |
| `agentops-deploy-dev.yml` | Push to `develop` | Deploys to the **dev** environment after a passing eval. |
| `agentops-deploy-qa.yml` | Push to a `release/*` branch | Deploys to **qa**. |
| `agentops-deploy-prod.yml` | Push to `main` | Deploys to **prod** after a passing eval. |

Read [`ci-github-actions.md`](ci-github-actions.md) for the full
reference. The defaults are sane: you do not need to edit them yet.

## 8. Push to GitHub and watch it run

Initialize the repo and push:

```bash
git init -b main
git add .
git commit -m "feat: bootstrap AgentOps eval and CI/CD"
gh repo create my-agent-evals --public --source=. --push
git checkout -b develop
git push -u origin develop
```

### Wire the GitHub Environments

Create three environments in **Settings → Environments**:

- `dev`
- `qa`
- `prod`

For each one, add the secrets and variables the workflows expect:

| Name | Where | Value |
|---|---|---|
| `AZURE_TENANT_ID` | Variable | Your Azure AD tenant id |
| `AZURE_SUBSCRIPTION_ID` | Variable | Subscription holding the Foundry project |
| `AZURE_CLIENT_ID` | Variable | App registration client id (federated) |
| `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` | Variable | Same value you exported earlier |
| `AZURE_OPENAI_ENDPOINT` | Variable | Same value you exported earlier |
| `AZURE_OPENAI_DEPLOYMENT` | Variable | `gpt-4o-mini` (or your deployment) |

### Configure OIDC (federated credential)

On the app registration backing `AZURE_CLIENT_ID`, add federated
credentials for each environment. The subject pattern is:

```
repo:<owner>/<repo>:environment:<env-name>
```

Add one for `environment:dev`, one for `environment:qa`, one for
`environment:prod`, and one for `pull_request` (used by
`agentops-pr.yml`). Grant the app's managed identity at least
`Cognitive Services OpenAI User` on the AOAI resource and the Foundry
project's data plane reader role.

### Open a PR

```bash
git checkout -b feature/tweak-prompt
# make any small change, e.g. edit smoke.jsonl
git commit -am "test: refine smoke dataset"
git push -u origin feature/tweak-prompt
gh pr create --base develop --fill
```

The `agentops-pr.yml` workflow runs. When it finishes you will see:

- A green or red check on the PR.
- A bot comment with the verdict, threshold table, and a link to the
  full `report.md` artifact.

Merge the PR. `agentops-deploy-dev.yml` triggers, runs an eval against
the dev environment, and deploys if it passes.

## 9. Run the Watchdog

The watchdog reads your accumulated run history and (optionally)
queries Application Insights and the Foundry control plane to flag
drifts that a single eval cannot see — repeated regressions, latency
trends, error spikes, safety findings.

```bash
pip install "agentops-toolkit[agent]"
agentops agent analyze
```

This produces `.agentops/agent/report.md`. With no `agent.yaml`
present, only the local results-history source is active and Azure
Monitor / Foundry control plane appear as `skipped` in the
diagnostics block. That is enough for the basic regression and
latency checks across all your previous runs.

To pull production telemetry, drop a starter `agent.yaml` into the
workspace and edit it:

```bash
cp "$(python -c 'import agentops, pathlib; print(pathlib.Path(agentops.__file__).parent / "templates" / "agent.yaml")')" .agentops/agent.yaml
```

```yaml
sources:
  results_history:
    enabled: true
  azure_monitor:
    enabled: true
    app_insights_resource_id: /subscriptions/<sub>/resourceGroups/<rg>/providers/microsoft.insights/components/<ai>
  foundry_control:
    enabled: true
    project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
```

Re-run `agentops agent analyze`. The findings table now mixes signals
from your eval history with live telemetry from the deployed agent.

> **Optional — WAF-AI security audit.** The watchdog can also run a
> read-only audit of your Foundry resource group against the
> [Well-Architected Framework for AI workloads — Security pillar][waf-ai].
> Enable the `azure_resources` source and the `posture` check in
> `agent.yaml` (commented stanzas are included), grant your identity
> `Reader` on the resource group, and re-run with
> `agentops agent analyze --categories security`. Full walkthrough:
> [`tutorial-agent-watchdog.md`](tutorial-agent-watchdog.md#2b-security-posture-audit-waf-ai).

For deeper integration (Copilot Chat extension, ACA deploy), see
[`tutorial-agent-watchdog.md`](tutorial-agent-watchdog.md).

[waf-ai]: https://learn.microsoft.com/azure/well-architected/ai/security

## 10. Where to go next

You now have the full AgentOps loop running end-to-end. From here:

- **Per-scenario tutorials** — adapt the dataset shape to your own
  agent:
  - [`tutorial-rag.md`](tutorial-rag.md) — retrieval-augmented agents.
  - [`tutorial-agent-workflow.md`](tutorial-agent-workflow.md) —
    tool-calling agents.
  - [`tutorial-conversational-agent.md`](tutorial-conversational-agent.md)
    — multi-turn assistants.
  - [`tutorial-http-agent.md`](tutorial-http-agent.md) — agents
    deployed outside Foundry (ACA, AKS, custom).
  - [`tutorial-model-direct.md`](tutorial-model-direct.md) — raw
    model deployments without an agent layer.
- **Deeper baseline workflows** —
  [`tutorial-baseline-comparison.md`](tutorial-baseline-comparison.md).
- **Watchdog as a Copilot extension** —
  [`tutorial-agent-watchdog.md`](tutorial-agent-watchdog.md).
- **CI/CD reference** —
  [`ci-github-actions.md`](ci-github-actions.md).
- **Architecture and concepts** —
  [`how-it-works.md`](how-it-works.md),
  [`concepts.md`](concepts.md).
