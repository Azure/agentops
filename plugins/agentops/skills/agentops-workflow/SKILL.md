---
name: agentops-workflow
description: Set up AgentOps release-readiness workflows: PR eval gates, Doctor/evidence artifacts, and safe deploy handoffs to azd or Foundry prompt-agent tooling. Trigger on "CI", "CD", "pipeline", "workflow", "GitHub Actions", "Azure DevOps", "ADO", "PR gate", "deploy", "environments", "GitFlow", "release branch", "promote to prod", "DevOps", "can we ship".
---

# AgentOps Workflow

Help the user wire AgentOps into the release path so every candidate has a
clear gate and proof pack. The default starting point is a PR eval gate. Full
DEV/QA/PROD workflows are useful only after Azure auth, environments, and a real
deployment owner are configured.

**Pick the platform up front.** AgentOps supports two:

- `--platform github` (default) - writes `.github/workflows/*.yml` using
  GitHub Actions. Auth via OIDC + GitHub Environments.
- `--platform azure-devops` - writes `.azuredevops/pipelines/*.yml` using
  Azure DevOps Pipelines. Auth via a Service Connection + a variable
  group named `agentops`.

The conceptual workflows are identical: one PR gate, optional deploy stages
(dev/qa/prod), and a scheduled Doctor workflow. PR, production, and watchdog
templates run `agentops doctor --evidence-pack` so reviewers get
`evidence.json` and `evidence.md` in artifacts.

For a new repository or tutorial, start with the PR gate only:
`agentops workflow generate --kinds pr`. Generate DEV/QA/PROD deploy
workflows only after environments, Azure auth, and real build/deploy
commands are configured.

For copied accelerators or unfamiliar repos (for example GPT-RAG, Live Voice
Practice, AI Landing Zone/Bicep-based apps), run `agentops workflow analyze`
first and use the findings as the implementation plan before generating or
editing workflows.

AgentOps is **azd-first** for app/infrastructure deployment and
**Foundry-native** for prompt-agent candidate workflows. Do not invent a
parallel deployment system. AgentOps should gate quality and record proof;
`azd provision`, `azd deploy`, azd hooks, Foundry Toolkit, the
`microsoft-foundry` skill, and project tooling own lifecycle actions.

## Branch model assumed

```
feature/* ── PR ──▶ develop                 [agentops-pr]          gate
                       │
                       └── merge ─▶ develop  [agentops-deploy-dev]  build + eval + deploy DEV
release/* ── push                            [agentops-deploy-qa]   build + eval + deploy QA
release/* ── PR ──▶ main                     [agentops-pr]          gate
                       │
                       └── merge ─▶ main     [agentops-deploy-prod] safety eval + build + deploy PROD
```

If the user is on trunk-based development, omit `qa` and `release/**`
and have them generate `--kinds pr,dev,prod`.

## Step 0 - Prerequisites

1. `pip install "agentops-toolkit @ git+https://github.com/placerda/agentops.git@foundry-operate-readiness"` if `agentops` is missing.
2. `agentops eval analyze` has been reviewed, `agentops.yaml` exists at the
   project root, and `agentops eval run` works locally.
3. The user's repo follows GitFlow (or is willing to). If not, ask which
   branches map to dev/qa/prod and adjust the triggers after
   generation.

## Step 1 - Generate the workflows

First analyze the repo shape:

```bash
agentops workflow analyze
agentops workflow analyze --format markdown --out agentops-workflow-plan.md
```

Use the analysis to decide whether `--deploy-mode auto` is enough or whether
you need to adapt placeholders/project-specific deployment. The analyzer is
local-only and looks for `azure.yaml`, Bicep, AgentOps prompt-agent config,
landing-zone manifests, private-network signals, Docker/Container Apps signals,
and existing CI folders. Treat README matches as hints only; structural files
drive the recommendation.

**GitHub Actions (default):**

```bash
agentops workflow generate --kinds pr
# or full scaffold:
agentops workflow generate --kinds pr,dev,qa,prod --force
```

**Azure DevOps Pipelines:**

```bash
agentops workflow generate --platform azure-devops --kinds pr
# or full scaffold:
agentops workflow generate --platform azure-devops --kinds pr,dev,qa,prod --force
```

The full scaffold writes:

| Kind | GitHub Actions path | Azure DevOps path | Trigger | Environment |
|---|---|---|---|---|
| `pr` | `.github/workflows/agentops-pr.yml` | `.azuredevops/pipelines/agentops-pr.yml` | PRs to `develop`, `release/**`, `main` | `dev` |
| `dev` | `.github/workflows/agentops-deploy-dev.yml` | `.azuredevops/pipelines/agentops-deploy-dev.yml` | push to `develop` | `dev` |
| `qa` | `.github/workflows/agentops-deploy-qa.yml` | `.azuredevops/pipelines/agentops-deploy-qa.yml` | push to `release/**` | `qa` |
| `prod` | `.github/workflows/agentops-deploy-prod.yml` | `.azuredevops/pipelines/agentops-deploy-prod.yml` | push to `main` | `production` |
| `watchdog` | `.github/workflows/agentops-watchdog.yml` | `.azuredevops/pipelines/agentops-watchdog.yml` | daily cron (06:00 UTC) | `dev` |

PR, PROD, and watchdog workflows upload release evidence. Explain that this is
a projection of existing eval/Doctor/Foundry/monitoring signals, not a separate
exit-code contract.

Useful flags:

- `--platform github | azure-devops` - pick the CI/CD platform.
- `--force` - overwrite existing workflow files.
- `--kinds pr,dev,qa,prod` - generate a subset. Prefer `--kinds pr`
  until deploy environments are configured.
- `--deploy-mode auto|placeholder|azd|prompt-agent` - `auto` uses azd
  templates when `azure.yaml` exists, otherwise uses prompt-agent templates
  when `agentops.yaml` targets a Foundry prompt agent; `azd` forces
  `azd provision` / `azd deploy`; `prompt-agent` stages/evaluates a Foundry
  prompt candidate; `placeholder` keeps the generic stack-agnostic scaffold.
- `--dir <path>` - non-default repo root.

## Step 2 - Configure environments and Azure auth

### GitHub Actions

Walk the user through Settings → Environments and create three:

1. **`dev`** - no extra protection. Set any DEV-specific variables here
   (e.g. `ACA_APP_NAME`, `AZURE_RESOURCE_GROUP` pointing at the dev RG).
2. **`qa`** - usually no required reviewers, but isolated variables for
   the QA environment.
3. **`production`** - set:
   - **Required reviewers**: at least one (deploys to PROD will pause
     here until approved).
   - (Optional) **Wait timer** for an extra delay.
   - (Optional) **Deployment branches**: restrict to `main`.
   - PROD-specific variables (e.g. production resource group).

Tell the user that env-specific variables on the `production` environment
will override repo-level ones automatically inside the prod workflow.

### Azure DevOps

In **Pipelines → Environments**, create three: `dev`, `qa`,
`production`. On `production`, add a manual approval check (Approvals
and checks → New check → Approvals).

In **Pipelines → Library**, create a variable group named `agentops`
with these variables (mark sensitive ones as secret if needed):

- `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_DEPLOYMENT`
- `APPLICATIONINSIGHTS_CONNECTION_STRING` - optional fallback if the
  Foundry project's App Insights connection cannot be auto-discovered.

In **Project settings → Service connections**, create an Azure Resource
Manager service connection named `agentops-azure` scoped to the
subscription that hosts your Foundry project.

Grant the build service "Contribute to pull requests" permission on the
repository (Project settings → Repositories → Security → `Build Service`)
so the PR-comment step can post.

## Step 3 - Configure Azure auth

### GitHub Actions (OIDC)

At repository level (Settings → Secrets and variables → Actions →
**Variables** tab), set:

- `AZURE_CLIENT_ID` - App registration / managed identity used for OIDC.
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` - Foundry project URL used by the
  eval step.
- `APPLICATIONINSIGHTS_CONNECTION_STRING` - optional fallback as a
  variable or secret. Generated workflows first try to auto-discover App
  Insights from the Foundry project endpoint; this value makes eval and
  Doctor telemetry explicit.

Then configure Workload Identity Federation on the Azure side
(`federated-credentials` on the app registration) for **each branch /
environment** the workflows will run from. See
`docs/ci-github-actions.md` for the exact `az` commands.

Tell the user that CI evals emit `agentops.eval.*` telemetry and scheduled
Doctor runs emit `agentops.agent.finding.*` telemetry when App Insights is
configured or auto-discovered. The Cockpit uses those signals for Azure
Monitor deep links.

### Azure DevOps (Service Connection)

Already done in Step 2 - the `agentops-azure` service connection
handles auth. Make sure the underlying service principal or managed
identity has the **Azure AI User** role on the Foundry account.

## Step 4 - Use azd for deployment

If the repo already has `azure.yaml`, generate azd-backed deployment
workflows:

```bash
agentops workflow generate --kinds pr,dev,qa,prod --deploy-mode azd --force
```

The deploy workflows will:

1. run `azd env new ... || azd env select ...` in CI;
2. run `azd provision --no-prompt` for DEV by default;
3. run `azd provision --no-prompt` for QA/PROD only when manually
   requested (`provision=true` in GitHub Actions or
   `RUN_AZD_PROVISION=true` in Azure DevOps);
4. run `agentops eval run` as the quality/safety gate;
5. run `azd env refresh` on the deploy runner so a fresh CI workspace can
   recover outputs from the previous infrastructure provision;
6. run `azd deploy --no-prompt`.

Set `AZURE_ENV_NAME` per GitHub Environment / Azure DevOps variable
group if the user's azd env names are not exactly `dev`, `qa`, and
`production`. Set `AZURE_LOCATION` when the azd template needs an
explicit region.

### If the user asks for "zero-trust deployment"

Do **not** replicate azd. Do this instead:

1. Inspect the app and ask only for missing critical choices (region,
   target host, private networking yes/no if not obvious).
2. Prefer an existing azd template or AVM-backed template that already
   implements managed identity, RBAC-only data access, private endpoints
   where required, and no secrets in source.
3. Create or adapt `azure.yaml`, `infra/`, and azd-native hooks declared
   in `azure.yaml` (`preprovision`, `postprovision`, `predeploy`,
   `postdeploy`) as needed.
4. Run `azd provision` to validate the infrastructure path.
5. Re-run `agentops workflow generate --deploy-mode azd --force` so CI
   delegates provision/deploy to azd.

Never call ad-hoc hook scripts from the workflow (for example
`./agentops/deploy.sh` or `./.azd/hooks/*`). If custom behavior is
needed, put it behind azd's native hook mechanism in `azure.yaml`.

### Copied accelerators / AI Landing Zone apps

For Azure AI accelerators copied from templates, use AgentOps to make the
landing-zone path actionable:

1. AgentOps owns eval gates, Doctor, reports, Cockpit readiness, and the
   workflow guardrails around deployment.
2. Foundry owns hosted agents, prompt-agent versions, evaluations, traces,
   monitoring, datasets, and operations.
3. azd/Bicep/AILZ owns app and infrastructure deploy when `azure.yaml` or
   `infra/*.bicep` exists.
4. Project-specific steps such as indexing, data seeding, model deployment,
   container build/push, App Config updates, or private-network post-provision
   work stay in azd hooks or existing project tooling.

If `scripts/Invoke-PreflightChecks.ps1` exists, keep it in the deployment path:
AgentOps-generated azd workflows run it with `-Strict` before `azd provision`.
Doctor surfaces the same path as `AI Landing Zone deployment readiness`, with
evidence for preflight, `agentops.yaml`, azd workflow coverage, network
isolation, and the private runner path.

If `agentops workflow analyze` reports network isolation, private endpoints,
jumpbox/Bastion, Azure Firewall, or ACR Tasks, do not assume GitHub-hosted
runners can deploy everything. Plan self-hosted runner, jumpbox handoff, or ACR
Tasks agent-pool execution before enabling DEV/QA/PROD deploy stages.

If `azure.yaml` is missing and the user is not asking to create the
deployment assets yet, check whether this is a Foundry prompt agent. If
`agentops.yaml` has `agent: "name:version"`, prefer prompt-agent mode:

```bash
agentops workflow generate --kinds pr,dev,qa,prod --deploy-mode prompt-agent --force
```

Prompt-agent workflows:

1. read `prompt_file` from `agentops.yaml` or
   `AGENTOPS_AGENT_PROMPT_FILE`;
2. create or reuse a candidate Foundry prompt-agent version from that file;
3. generate `.agentops/deployments/agentops.candidate.yaml`;
4. run `agentops eval run` against the candidate version;
5. record `.agentops/deployments/foundry-agent.json` as a deployment
   artifact only when the gate passes.

This avoids the bad pattern of evaluating one agent version and deploying a
different prompt. The invariant is: **evaluated version == deployed version**.
Foundry manages agent versions; AgentOps owns the repo-side gate and
deployment record.

If this is not a Foundry prompt agent and azd is not ready, generate
`--kinds pr` only or use `--deploy-mode placeholder`. Do not ship
DEV/QA/PROD workflows that pretend deployment is wired.

## Step 5 - Branch protection

In Settings → Branches, add a rule for both `develop` and `main`:

- Require a pull request before merging.
- Require status checks to pass: select **`AgentOps PR / Eval (PR gate)`**
  (the job name from `agentops-pr.yml`).
- Optional: require linear history.

This makes the eval gate a hard merge requirement.

## Step 6 - Iterate

Common follow-ups:

- **Tighten thresholds for QA/PROD** - copy `agentops.yaml` to
  `agentops-qa.yaml` / `agentops-prod.yaml` and tighten the
  `thresholds:` block. Point each workflow at its own config via the
  `inputs.config` default.
- **Scheduled runs** - add a `schedule:` entry in `agentops-pr.yml` (or a
  new `agentops-nightly.yml`) to evaluate against `main` nightly.
- **Matrix per scenario** - if the user has multiple AgentOps config files,
  extend the eval job with `strategy.matrix.config:` and reference
  `${{ matrix.config }}`.
- **Regression baseline** - wire the deploy templates to download the
  previous run's `results.json` artifact and call
  `agentops eval run --baseline <results.json>`.

## Guardrails

- Do **not** invent CLI flags. The supported `workflow analyze` flags are
  `--dir`, `--format`, and `--out`. The supported `workflow generate` flags are
  `--force`, `--dir`, `--kinds`, `--platform`, and `--deploy-mode`.
- Do **not** push DEV/QA/PROD deploy workflows with placeholder
  Build/Deploy steps or missing OIDC variables; generate PR-only first.
- Do **not** create parallel workflow files. Prefer editing the
  generated ones.
- Do **not** auto-fill app/infrastructure deployment with raw Azure CLI
  steps that bypass azd. AgentOps gates; azd provisions and deploys. For
  Foundry prompt agents, use `--deploy-mode prompt-agent` so the workflow
  calls the Foundry SDK and evaluates the candidate version before marking
  it deployed.
- Do **not** use AgentOps workflows to create or deploy Foundry Hosted Agents.
  Use Foundry Toolkit / the `microsoft-foundry` skill / the app's azd path,
  then point AgentOps at the deployed URL for gates and evidence.
- The four workflow names (`agentops-pr`, `agentops-deploy-dev`,
  `agentops-deploy-qa`, `agentops-deploy-prod`) are fixed - don't rename
  them or branch-protection wiring will break.
