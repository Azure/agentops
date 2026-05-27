---
name: agentops-workflow
description: "Set up AgentOps release-readiness workflows: PR eval gates, Doctor/evidence artifacts, and safe deploy handoffs to azd or Foundry prompt-agent tooling. Trigger on CI, CD, pipeline, workflow, GitHub Actions, Azure DevOps, ADO, PR gate, deploy, environments, GitFlow, release branch, promote to prod, DevOps, can we ship."
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

The conceptual workflows are identical: one PR gate and optional deploy stages
(dev/qa/prod). The PR and production templates already run
`agentops doctor --evidence-pack` so reviewers get `evidence.json` and
`evidence.md` in artifacts. A separate scheduled Doctor workflow is optional
for periodic health checks, not the default release path.

For a new repository or tutorial, start with the PR gate only:
`agentops workflow generate --kinds pr`. Generate DEV/QA/PROD deploy
workflows only after environments, Azure auth, and real build/deploy
commands are configured.

For copied accelerators or unfamiliar repos (for example GPT-RAG, Live Voice
Practice, AI Landing Zone/Bicep-based apps), run `agentops workflow analyze`
first and use the findings as the implementation plan before generating or
editing workflows.

AgentOps reuses **azd** for app/infrastructure deployment when the repo already
has an azd project, and stays **Foundry-native** for prompt-agent candidate
workflows. Do not invent a parallel deployment system. AgentOps should gate
quality and record proof; `azd provision`, `azd deploy`, azd hooks, Foundry
Toolkit, the `microsoft-foundry` skill, and project tooling own lifecycle
actions.

## Fast path - generated GitHub setup

Use this path when the user already generated GitHub workflows or asks to get
the PR gate running. Stay local-first and deterministic; do not start
by discovering the whole Azure subscription.

1. Inspect the repo before cloud discovery:
   - `agentops init show --dir .` without `--reveal-secrets`.
   - `agentops.yaml`.
   - `.agentops/.env`, plus `.azure/config.json` and active `.azure/<env>/.env`
     when the repo uses azd.
   - `azd env get-values` when `azure.yaml` exists and azd is available.
   - `.github/workflows/agentops-*.yml`.
2. Read the generated workflows to determine exactly which GitHub environments
   and variables are needed. For the prompt-agent quickstart, `pr` normally
   means only `environment: dev`.
3. Treat `dev` here as a GitHub Actions environment for OIDC and variables. It
   normally points at the Foundry project already configured by `agentops init`;
   it does not require creating a new Foundry project.
4. Proceed only when these values are known or deliberately chosen:
   - GitHub `owner/repo`.
   - workflow environment names from `jobs.*.environment`.
   - `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`.
   - `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`.
   - `AZURE_OPENAI_DEPLOYMENT`.
   - optional `APPLICATIONINSIGHTS_CONNECTION_STRING`.
5. Prefer existing values and exact checks:
   - `git remote get-url origin` and `gh repo view --json nameWithOwner`.
   - `gh variable list --env <env>` and `gh secret list --env <env>`.
   - `agentops init show`, local `.agentops/.env` or `.azure/<env>/.env`, and
     `azd env get-values` values before `az account show`.
   - `az account show` only as a proposal for tenant/subscription; confirm
     before writing it to GitHub variables.
6. Copy CI variables from local AgentOps/azd configuration into the GitHub
   environment used by the workflow. Reuse local values for
   `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`, `AZURE_OPENAI_ENDPOINT`,
   `AZURE_OPENAI_DEPLOYMENT`, and optional
   `APPLICATIONINSIGHTS_CONNECTION_STRING` instead of asking the user to type
   them again. Explain `AZURE_OPENAI_DEPLOYMENT` only if it is missing: it is
   the Azure OpenAI deployment used as the evaluator/judge model, not the
   user's agent.
7. Do not enumerate subscriptions, Foundry projects, Azure OpenAI resources, or
   model deployments to guess missing values. If `AZURE_SUBSCRIPTION_ID`,
   `AZURE_TENANT_ID`, `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`, or
   `AZURE_OPENAI_DEPLOYMENT` is absent from AgentOps/azd/local env, ask the user
   to choose or provide it. Only run a scoped Azure query after the user confirms
   the subscription and the exact missing value.
8. For GitHub OIDC, derive the federated credential subject from the generated
   workflow. If the job has `environment: dev`, the subject is normally
   `repo:<owner>/<repo>:environment:dev`. Do not assume branch or
   `pull_request` subjects without reading the workflow.
9. Before triggering a Foundry prompt-agent workflow, make sure the OIDC app /
   service principal has Foundry data-plane access. It needs **Foundry User**
   (role id `53ca6127-db72-4b80-b1b0-d745d6d5456d`, formerly Azure AI User) at
   the Foundry project scope, or at the Foundry resource scope if that is the
   team's standard. Azure **Reader** is not enough; without this role the eval
   step fails on
   `Microsoft.CognitiveServices/accounts/AIServices/agents/read`.
10. If the Foundry RBAC assignment is missing, do not run the workflow yet.
   Show the exact GitHub OIDC client ID / service principal, desired role, and
   target Foundry scope, then ask the user to approve the role assignment or
   get an Azure/Foundry admin to grant it. After assignment, read it back or ask
   the user to confirm before dispatching the workflow.
   When the user approves and you know the Foundry scope, use the role id to
   avoid rename drift:
   - `az ad sp show --id <AZURE_CLIENT_ID> --query id -o tsv`
   - `az role assignment list --assignee <sp-object-id> --scope <foundry-scope> --include-inherited`
   - `az role assignment create --assignee-object-id <sp-object-id> --assignee-principal-type ServicePrincipal --role 53ca6127-db72-4b80-b1b0-d745d6d5456d --scope <foundry-scope>`
11. Ask before creating or updating GitHub repos, GitHub environments,
   variables/secrets, Entra app registrations/service principals, federated
   credentials, managed identities, or Azure RBAC assignments.
12. When creating federated credentials from PowerShell, avoid fragile
   interpolation. Do **not** write `"repo:$repo:environment:$envName"` because
   `$repo:` can be parsed as a scoped variable. Use
   `"repo:${repo}:environment:${envName}"` or
   `("repo:{0}:environment:{1}" -f $repo, $envName)`, then build JSON from a
   PowerShell object with `ConvertTo-Json`.
13. After creating or updating a federated credential, read it back and verify
    before triggering a workflow:
    - `subject` exactly matches the generated workflow subject.
    - `issuer` is `https://token.actions.githubusercontent.com`.
    - `audiences` includes `api://AzureADTokenExchange`.
    If any value differs, fix the credential before running GitHub Actions.
14. Do not dispatch `gh workflow run` as a surprise validation step. First show
    that the GitHub environment, variables/secrets, federated credential, and
    Foundry RBAC are ready, then ask the user before triggering workflows.
15. Avoid broad discovery unless local config is missing. Do **not** run broad
   `az resource list`, `az graph query`, SDK inspection, or web search to find
   the Foundry project when `agentops init show`, `.agentops/.env`, or
   `.azure/<env>/.env` already has `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`. If the
   endpoint is missing, say exactly what is missing and ask the user before
   scanning the subscription.

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

1. `pip install "agentops-toolkit @ git+https://github.com/Azure/agentops.git@main"` if `agentops` is missing.
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
| `doctor` | `.github/workflows/agentops-doctor.yml` | `.azuredevops/pipelines/agentops-doctor.yml` | daily cron (06:00 UTC) | `dev` |

PR and PROD workflows upload release evidence. Explain that this is a
projection of existing eval/Doctor/Foundry/monitoring signals, not a separate
exit-code contract. Generate the optional scheduled Doctor workflow only when
the team explicitly wants periodic health-check artifacts outside PR/release
events.

Useful flags:

- `--platform github | azure-devops` - pick the CI/CD platform.
- `--force` - overwrite existing workflow files.
- `--kinds pr,dev,qa,prod` - generate a subset. Prefer `--kinds pr`
  until deploy environments are configured.
- `--kinds doctor` - optional scheduled Doctor-only workflow for periodic
  checks. Do not use it as a substitute for the PR gate.
- `--deploy-mode auto|placeholder|azd|prompt-agent` - `auto` uses azd
  templates when `azure.yaml` exists, otherwise uses prompt-agent templates
  when `agentops.yaml` targets a Foundry prompt agent; `azd` forces
  `azd provision` / `azd deploy`; `prompt-agent` stages/evaluates a Foundry
  prompt candidate; `placeholder` keeps the generic stack-agnostic scaffold.
- `--dir <path>` - non-default repo root.

## Step 2 - Configure environments and Azure auth

### GitHub Actions

Read the generated workflow files and create only the GitHub Environments used
by `jobs.*.environment`. For `pr`, that is usually only **`dev`**. For the full
scaffold, create **`dev`**, **`qa`**, and **`production`**.

- **`dev`** - no extra protection. Store the OIDC variables here when the
  generated jobs use `environment: dev`.
- **`qa`** - usually no required reviewers, but isolated variables for QA.
- **`production`** - set required reviewers, optional wait timer, optional
  deployment branch restriction to `main`, and production-specific variables.

Tell the user that environment-level variables override repository-level ones
inside jobs that declare that environment.

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

At the GitHub Environment level when the workflow declares an environment
(preferred for the quickstart), or at repository level when intentionally shared
across environments, set:

- `AZURE_CLIENT_ID` - App registration / managed identity used for OIDC.
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` - Foundry project URL used by the
  eval step.
- `AZURE_OPENAI_DEPLOYMENT` - existing Azure OpenAI deployment used as the
  evaluator/judge model. Reuse the local AgentOps/azd value when available.
- `APPLICATIONINSIGHTS_CONNECTION_STRING` - optional fallback as a
  variable or secret. Generated workflows first try to auto-discover App
  Insights from the Foundry project endpoint; this value makes eval and
  Doctor telemetry explicit.

Then configure Workload Identity Federation on the Azure side
(`federated-credentials` on the app registration) for **each branch /
environment** the workflows will run from. See
`docs/ci-github-actions.md` for the exact `az` commands.

Also grant the same app registration / service principal **Foundry User** on the
Foundry project or Foundry resource before the first workflow run. The PR gate
uses Foundry data-plane APIs to read prompt agents; Azure `Reader` only proves
ARM access and will still fail the eval step with
`Microsoft.CognitiveServices/accounts/AIServices/agents/read`.

Tell the user that CI evals emit `agentops.eval.*` telemetry and scheduled
Doctor runs emit `agentops.agent.finding.*` telemetry when App Insights is
configured or auto-discovered. The Cockpit uses those signals for Azure
Monitor deep links.

### Azure DevOps (Service Connection)

Already done in Step 2 - the `agentops-azure` service connection
handles auth. Make sure the underlying service principal or managed
identity has the **Foundry User** role on the Foundry project or resource.

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
