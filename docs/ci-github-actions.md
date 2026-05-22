# AgentOps GenAIOps GitFlow on GitHub Actions

This guide shows how to wire AgentOps into a complete GenAIOps CI/CD
pipeline on GitHub Actions, mapped to a classic GitFlow branching model
with three deployment environments (`dev`, `qa`, `production`).

`agentops workflow analyze` is the safe first step for a copied accelerator or
unfamiliar repository: it inspects local files and recommends whether AgentOps
should generate azd-backed deployment, Foundry prompt-agent deployment, or a
placeholder scaffold for project-specific adaptation.

`agentops workflow generate --kinds pr` is the safe first generated workflow for
a new repository: it creates only the PR eval gate. Generate the full DEV/QA/PROD
deploy scaffold after GitHub Environments and Azure OIDC are configured.
If the repo has `azure.yaml`, AgentOps can generate azd-backed deploy
workflows that delegate provision/deploy to Azure Developer CLI. For the
Quick Start-style Foundry prompt agent, AgentOps can also generate a
prompt-agent workflow: it creates a candidate Foundry agent version from a
source-controlled prompt file, evaluates that exact version, then records it
as deployed only when the gate passes.

The full scaffold ships five templates:

| File | Trigger | GitHub Environment | Purpose |
|---|---|---|---|
| `agentops-pr.yml` | PRs to `develop`, `release/**`, `main` | `dev` | Eval gate plus release evidence. Fails the PR if thresholds or critical Doctor findings block the candidate. Comments report and evidence on PR. |
| `agentops-deploy-dev.yml` | push to `develop` | `dev` | Eval → build → deploy DEV |
| `agentops-deploy-qa.yml` | push to `release/**` | `qa` | Eval → build → deploy QA |
| `agentops-deploy-prod.yml` | push to `main` | `production` | Safety eval → release evidence → build → deploy PROD (gated by required reviewers) |
| `agentops-watchdog.yml` | daily cron | `dev` | Scheduled Doctor + release evidence |

## GitFlow assumed

```mermaid
flowchart LR
    feat["feature/*"] -->|PR| prGate1{{"agentops-pr.yml<br/>(gate)"}}
    prGate1 -->|merge| dev["develop"]
    dev --> deployDev["agentops-deploy-dev.yml"]
    deployDev --> DEV(["DEV"])

    rel["release/*"] -->|push| deployQa["agentops-deploy-qa.yml"]
    deployQa --> QA(["QA"])

    rel -->|PR| prGate2{{"agentops-pr.yml<br/>(gate)"}}
    prGate2 -->|merge| main["main"]
    main --> deployProd["agentops-deploy-prod.yml"]
    deployProd --> PROD(["PROD<br/>(required reviewers)"])

    classDef gate fill:#fff3cd,stroke:#856404,color:#000;
    classDef env fill:#d1ecf1,stroke:#0c5460,color:#000;
    class prGate1,prGate2 gate;
    class DEV,QA,PROD env;
```

If you are on trunk-based development, generate only the templates you
need: `agentops workflow generate --kinds pr,dev,prod`.

## Quick start

```bash
# 1. Analyze and fix eval setup before the first blocking run.
agentops eval analyze

# 2. Make sure your eval works locally first.
agentops eval run

# 3. Analyze the repo shape before generating workflows.
agentops workflow analyze

# 4. Generate the PR gate first.
agentops workflow generate --kinds pr

# 5. Configure GitHub (see sections below):
#    - OIDC repo variables
#    - dev environment
#    - branch protection on develop and main

# 6. Commit and push the PR gate.

# 7. Only after deploy wiring is real, generate the full scaffold.
#    auto uses azd when azure.yaml exists, or prompt-agent when agentops.yaml
#    targets a Foundry prompt agent (name:version).
agentops workflow generate --kinds pr,dev,qa,prod --deploy-mode auto --force
```

## Configuration walkthrough

### 1. Repository variables (OIDC)

In Settings → Secrets and variables → Actions → **Variables**, add:

| Variable | Purpose |
|---|---|
| `AZURE_CLIENT_ID` | App registration / managed identity used for federated login |
| `AZURE_TENANT_ID` | Azure AD tenant |
| `AZURE_SUBSCRIPTION_ID` | Target subscription |
| `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` | Foundry project URL (used by the eval step) |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Optional fallback when the Foundry project's App Insights connection cannot be auto-discovered |

Then on the Azure side, configure Workload Identity Federation
(federated credentials) on the app registration so it can be assumed
from GitHub Actions runs. See
[Microsoft's WIF docs](https://learn.microsoft.com/azure/active-directory/workload-identities/workload-identity-federation-create-trust?pivots=identity-wif-apps-methods-azp).

The generated eval and doctor workflows install AgentOps telemetry support.
When `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` is set, AgentOps first tries to
auto-discover the Foundry project's Application Insights resource. If that
is not available in your tenant, set `APPLICATIONINSIGHTS_CONNECTION_STRING`
as either a repository/environment variable or a secret. CI eval runs then
emit `agentops.eval.*` spans, and scheduled Doctor runs emit
`agentops.agent.finding.*` spans that the Cockpit can deep-link into Azure
Monitor Logs.

### 2. GitHub Environments

In Settings → Environments, create three:

#### `dev`
- Usually no protection rules.
- Override env-specific variables here (e.g. dev resource group, dev
  ACA app name).

#### `qa`
- Optional: restrict deployment branches to `release/**`.
- Override env-specific variables for QA infra.

#### `production`
- **Required reviewers**: at least one. Deploys to PROD pause until
  approved.
- Optional: **Wait timer** for an extra cool-down.
- Optional: **Deployment branches**: restrict to `main`.
- Override env-specific variables for production infra.

Environment-level variables override repo-level ones automatically
when the workflow's `environment:` matches.

### 3. Analyze evaluation setup first

Before making eval a required PR/deploy gate, run:

```bash
agentops eval analyze --format markdown
```

This command is read-only and local-only. It checks whether `agentops.yaml`,
the target kind, and the dataset columns are ready for `agentops eval run`. If
the project looks like a RAG app, tool-using agent, HTTP/containerized app, or
other accelerator where deterministic inference is not enough, it recommends
using Copilot with `agentops-config`, `agentops-dataset`, and/or
`agentops-eval` before the first blocking run. When skills are missing from the
repo, the output includes the install command and a copy/paste Copilot handoff
prompt.

### 4. Choose deployment mode

AgentOps is azd-first for deployment: AgentOps runs the evaluation gate,
while Azure Developer CLI owns infrastructure, packaging, deployment, and
hooks declared in `azure.yaml`.

Before choosing manually, run:

```bash
agentops workflow analyze --format markdown
```

The analyzer is read-only and local-only. It looks for `azure.yaml`, Bicep
files, `agentops.yaml`, Foundry prompt-agent shape, source-controlled prompt
files, landing-zone manifests, private-network terms, Docker/Container Apps
signals, and existing CI folders. README matches such as GPT-RAG, Live Voice, or
AI Landing Zone are treated as hints; structural files drive the recommendation.
`workflow generate --deploy-mode auto` uses the same recommendation, so the
analysis and generated templates do not drift. If you omit `--deploy-mode`, the
default is `auto`; the command output prints the selected effective mode, for
example `azd (auto default)` or `placeholder (auto default)`.

Use one of these modes:

| Mode | When to use it |
|---|---|
| `--deploy-mode auto` | Default when the flag is omitted. Uses azd templates when `azure.yaml` exists; otherwise uses prompt-agent templates when `agentops.yaml` targets a Foundry prompt agent; otherwise keeps placeholders. |
| `--deploy-mode azd` | Force `azd provision` / `azd deploy` templates. Use this after creating or adapting `azure.yaml` and `infra/`. |
| `--deploy-mode prompt-agent` | For Foundry prompt agents. Create/reuse a candidate version from `prompt_file`, evaluate that exact version, then record it as deployed when the gate passes. |
| `--deploy-mode placeholder` | Keep stack-agnostic build/deploy placeholders for repos that are not azd-managed yet. |

For azd-managed repos:

```bash
agentops workflow generate --kinds pr,dev,qa,prod --deploy-mode azd --force
```

The generated deploy workflows:

1. install `azd`;
2. run `azd env new ... || azd env select ...` on each CI runner;
3. run `azd provision --no-prompt` for DEV by default;
4. run `azd provision --no-prompt` for QA/PROD only when manually requested;
5. run `agentops eval run` as the quality/safety gate;
6. run `azd env refresh` on the deploy runner;
7. run `azd deploy --no-prompt`.

For production deploys, generated templates also run
`agentops doctor --evidence-pack` after the eval gate and upload
`.agentops/release/latest/evidence.json` plus `evidence.md`. Warnings do not
change the exit-code contract; critical Doctor findings block when
`--severity-fail critical` is used.

Set `AZURE_ENV_NAME` per GitHub Environment if your azd env names differ
from `dev`, `qa`, and `production`. Set `AZURE_LOCATION` when the azd
template needs an explicit region.

#### Placeholder mode

When `azure.yaml` is missing or `--deploy-mode placeholder` is selected,
each `agentops-deploy-*.yml` ships with `Build (placeholder)` and
`Deploy (placeholder)` steps. Prefer creating an azd deployment first; if
that is not possible, replace the placeholders with project-specific
commands.

#### Foundry prompt agent

For the simplest Foundry prompt-agent workflow, keep the instructions in
source control and point `agentops.yaml` at them:

```yaml
version: 1
agent: "quickstart-agent:2"
dataset: .agentops/data/smoke.jsonl
execution: cloud
prompt_file: .agentops/prompts/agent-instructions.md
```

Then generate prompt-agent deploy workflows:

```bash
agentops workflow generate --kinds pr,dev,qa,prod --deploy-mode prompt-agent --force
```

Each deploy workflow does this:

1. stages a candidate Foundry prompt-agent version from `prompt_file`;
2. writes `.agentops/deployments/agentops.candidate.yaml` pointing at the
   candidate `name:version`;
3. runs `agentops eval run` against that candidate version;
4. runs `agentops doctor --evidence-pack` so the exact candidate has release evidence;
5. records `.agentops/deployments/foundry-agent.json` as a CI artifact only
   after the gate passes.

This keeps the invariant clear: **the evaluated agent version is the deployed
agent version**. Foundry remains the system of record for the agent and its
versions; AgentOps supplies the repo-side gate, deployment record, and
Cockpit visibility.

#### Container Apps

```yaml
# Build
- name: Build image
  run: |
    az acr build \
      --registry "${{ vars.ACR_NAME }}" \
      --image "myapp:${{ github.sha }}" \
      .

# Deploy
- name: Deploy to ACA
  run: |
    az containerapp update \
      --name "${{ vars.ACA_APP_NAME }}" \
      --resource-group "${{ vars.AZURE_RESOURCE_GROUP }}" \
      --image "${{ vars.ACR_NAME }}.azurecr.io/myapp:${{ github.sha }}"
```

#### App Service

```yaml
# Build
- uses: actions/setup-python@v5
  with: { python-version: "3.11" }
- run: pip install -r requirements.txt -t ./dist
- run: cp -r src ./dist/

# Deploy
- uses: azure/webapps-deploy@v3
  with:
    app-name: ${{ vars.WEBAPP_NAME }}
    package: ./dist
```

#### Foundry hosted agent

```yaml
# Build is typically empty: hosted agents are configured, not packaged.

# Deploy: publish a new agent version with whatever your project uses
# to manage Foundry agents (project-specific tooling).
```

#### Zero-trust deployment with azd

If you ask a coding agent to generate a zero-trust deployment, have it
create or adapt `azure.yaml`, `infra/`, and azd-native hooks such as
`preprovision`, `postprovision`, `predeploy`, and `postdeploy`. Do not
wire ad-hoc hook scripts directly into AgentOps workflows. After the azd
path is valid locally, regenerate the workflows with
`--deploy-mode azd`.

#### Copied Azure AI accelerators and AI Landing Zone projects

For copied accelerators such as GPT-RAG, Live Voice Practice, or apps based on
the Azure AI Landing Zone pattern, use AgentOps to turn the deployment path into
actionable readiness: landing-zone preflight, azd/Bicep workflow stages, Doctor
checks, eval gates, and post-deploy evidence.

```bash
agentops workflow analyze --format markdown --out agentops-workflow-plan.md
```

Use the output as the plan for your coding agent:

1. AgentOps owns repo-side eval gates, Doctor readiness checks, artifacts, and
   Cockpit visibility.
2. `azd` owns `provision`, `deploy`, and hooks for app/infra lifecycle when
   `azure.yaml` is present or can be added.
3. Foundry owns hosted agents, evaluations, traces, and operations.
4. Project-specific steps such as indexing data, seeding search, building
   containers, updating app config, or running private-network post-provision
   work stay in the accelerator's azd hooks or existing deployment tooling.

When `scripts/Invoke-PreflightChecks.ps1` is present, generated azd deploy
workflows run it with `-Strict` before `azd provision`. Doctor also reports
`AI Landing Zone deployment readiness` in the Operational Excellence findings,
including whether the preflight script, `agentops.yaml`, azd deploy workflow,
network isolation, and private-runner path are ready.

If the analyzer reports network isolation, private endpoints, jumpbox/Bastion,
Azure Firewall, or ACR Tasks signals, plan where private data-plane work runs
before making deployment automatic. GitHub-hosted runners usually cannot reach
private endpoints; use a self-hosted runner in the VNet, a jumpbox handoff, or
an ACR Tasks agent pool depending on the accelerator.

### 5. Branch protection

In Settings → Branches, add a rule for **both `develop` and `main`**:

- ✅ Require a pull request before merging.
- ✅ Require status checks to pass: select
  **`AgentOps PR / Eval (PR gate)`**.
- (Optional) Require linear history.

This makes the AgentOps eval a hard merge requirement.

## Exit codes

The eval step uses the AgentOps exit code contract to gate deploys:

| Exit code | Meaning | Job result |
|---|---|---|
| `0` | Eval ran, all thresholds passed | ✅ pass |
| `2` | Eval ran, one or more thresholds failed | ❌ fail (deploy never runs) |
| `1` | Runtime / config error | ❌ fail |

## Artifacts

Each workflow uploads (always - even on failure):

- `results.json` - machine-readable, versioned
- `report.md` - human-readable
- `cloud_evaluation.json` - present when using Foundry cloud evaluation;
  contains a deep link to the New Foundry Experience Evaluations page
- `evidence.json` and `evidence.md` - present in PR, PROD, and watchdog
  workflows after `agentops doctor --evidence-pack`

Artifact names per workflow:

| Workflow | Artifact name |
|---|---|
| `agentops-pr.yml` | `agentops-pr-results` plus release evidence in the same artifact |
| `agentops-deploy-dev.yml` | `agentops-dev-results` |
| `agentops-deploy-qa.yml` | `agentops-qa-results` |
| `agentops-deploy-prod.yml` | `agentops-prod-results` plus release evidence |
| `agentops-watchdog.yml` | `agentops-watchdog-history` plus release evidence |

## CLI reference

```bash
agentops eval analyze                          # inspect eval setup before first run
agentops eval promote-traces --source traces.jsonl --apply
agentops doctor --evidence-pack                # write release evidence
agentops workflow analyze                      # inspect repo and recommend stages
agentops workflow analyze --format json        # stable machine-readable analysis
agentops workflow generate --kinds pr          # safe first PR gate
agentops workflow generate                     # all five templates; deploy mode defaults to auto
agentops workflow generate --kinds pr,dev,prod # subset (trunk-based)
agentops workflow generate --deploy-mode azd   # delegate deploy to azd
agentops workflow generate --deploy-mode prompt-agent # Foundry prompt deployment
agentops workflow generate --platform azure-devops
agentops workflow generate --force             # overwrite existing files
agentops workflow generate --dir <path>        # different repo root
```

| Flag | Description | Default |
|---|---|---|
| `--kinds` | Comma-separated subset of `pr,dev,qa,prod,watchdog` | all five |
| `--platform` | `github` or `azure-devops` | `github` |
| `--deploy-mode` | `auto`, `placeholder`, `azd`, or `prompt-agent` | `auto` |
| `--force` | Overwrite existing workflow files | `false` |
| `--dir` | Repository root | `.` |

## Customisation tips

- **Tighten thresholds for QA / PROD** - copy `agentops.yaml` to
  `agentops-qa.yaml` / `agentops-prod.yaml` and tighten the
  `thresholds:` block. Update the `inputs.config` default in the
  matching workflow file.
- **Scheduled runs** - add a `schedule:` entry in `agentops-pr.yml` (or
  a new file) to evaluate against `main` nightly.
- **Matrix per scenario** - if you have multiple AgentOps config files, extend
  the eval job with `strategy.matrix.config:` and reference
  `${{ matrix.config }}` in the eval step.
- **Regression baseline** - wire deploy templates to download the
  previous run's `results.json` artifact and call
  `agentops eval run --baseline <results.json>`.

## Migration from the older 3-template layout

If your repository still has `agentops-eval.yml`, `agentops-eval-ci.yml`,
or `agentops-eval-cd.yml` from a prior version of AgentOps:

1. Delete the three old files.
2. Run `agentops workflow generate`.
3. Re-add Build / Deploy commands you had customised.
4. Update branch-protection status checks to point at the new
   `AgentOps PR` job.
