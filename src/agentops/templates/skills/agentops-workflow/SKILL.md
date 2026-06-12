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
`evidence.md` in artifacts and, for GitHub Actions, in the run summary with the
Doctor finding summary. A separate scheduled Doctor workflow is optional for
periodic health checks, not the default release path.

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

For Foundry prompt-agent configs (`agent: name:version`), the generated eval gate
should use **AgentOps cloud eval in Foundry**: a temporary cloud config plus
`agentops eval run`, not the legacy official Action/task. Foundry still executes
the managed eval; AgentOps enforces thresholds, writes `results.json` /
`report.md`, and makes PR failures explainable in the summary.

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
   and variables are needed. For the prompt-agent tutorial, `pr` normally
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
   - `git branch -vv` to confirm the local trunk branch tracks
     `origin/main` when the tutorial uses trunk-based `main`.
   - `gh variable list --env <env>` and `gh secret list --env <env>`.
   - `agentops init show`, local `.agentops/.env` or `.azure/<env>/.env`, and
     `azd env get-values` values before `az account show`.
   - `az account show` only as a proposal for tenant/subscription; confirm
     before writing it to GitHub variables.
6. For GitHub OIDC, treat `AZURE_TENANT_ID` as the tenant that owns the app
   registration / federated credential, not merely the tenant associated with
   the subscription or a `managedByTenants` entry. Before writing
   `AZURE_TENANT_ID`, verify the chosen tenant can see the app registration and
   the exact federated credential:
   - `az ad app show --id <AZURE_CLIENT_ID>` in the active tenant, or an
     equivalent Microsoft Graph query scoped to the proposed tenant.
   - `az ad app federated-credential list --id <AZURE_CLIENT_ID>` and confirm
     the `subject`, `issuer`, and `audiences`.
   If the app is visible in one tenant but the Azure subscription is associated
   with another tenant, use the app/federated-credential tenant for
   `AZURE_TENANT_ID`; the subscription id remains `AZURE_SUBSCRIPTION_ID`.
   Do not copy a `managedByTenants[*].tenantId` value into GitHub variables
   unless the app and federated credential are verified there too.
7. When creating or connecting the GitHub remote for the prompt-agent tutorial,
   make sure the local trunk branch tracks the remote trunk before telling the
   user to continue:
   - If `main` is newly pushed, use `git push -u origin main`.
   - If `origin/main` already exists, use
     `git branch --set-upstream-to=origin/main main`.
   - Verify with `git branch -vv`; `main` must show `[origin/main]`.
   Without this, a later `git pull` on `main` can fetch but not update the
   local branch.
8. Copy CI variables from local AgentOps/azd configuration into the GitHub
   environment used by the workflow. Reuse local values for
   `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`, `AZURE_OPENAI_ENDPOINT`,
   `AZURE_OPENAI_DEPLOYMENT`, and optional
   `APPLICATIONINSIGHTS_CONNECTION_STRING` instead of asking the user to type
   them again. Explain `AZURE_OPENAI_DEPLOYMENT` only if it is missing: it is
   the Azure OpenAI deployment used as the evaluator/judge model, not the
   user's agent.
9. For prompt-agent tutorials that use Foundry trace sampling / trace-to-dataset,
   verify observability RBAC before telling the user step 18 is ready:
   - Resolve the dev Foundry project managed identity principal id.
   - Resolve the connected Application Insights resource.
   - Grant or verify **Reader** on that Application Insights resource to the dev
     Foundry project managed identity.
   - If the App Insights component is workspace-based, also grant or verify
     **Reader** on the backing Log Analytics workspace.
   This is separate from GitHub OIDC and separate from the signed-in user's
   portal access. Operate dashboards can still render while trace-to-dataset
   fails if the project identity cannot read App Insights.
10. Do not enumerate subscriptions, Foundry projects, Azure OpenAI resources, or
   model deployments to guess missing values. If `AZURE_SUBSCRIPTION_ID`,
   `AZURE_TENANT_ID`, `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`, or
   `AZURE_OPENAI_DEPLOYMENT` is absent from AgentOps/azd/local env, ask the user
   to choose or provide it. Only run a scoped Azure query after the user confirms
   the subscription and the exact missing value.
11. For GitHub OIDC, derive the federated credential subject from the generated
   workflow. If the job has `environment: dev`, the subject is normally
   `repo:<owner>/<repo>:environment:dev`. Do not assume branch or
   `pull_request` subjects without reading the workflow.
12. Before triggering a Foundry prompt-agent workflow, make sure the OIDC app /
   service principal has **two** RBAC assignments. Both are required; the eval
   step fails silently (every metric returns `null`) if only one is in place.
   1. **Foundry User** on the Foundry project (or the Foundry resource scope
      if that is the team's standard). Role id
      `53ca6127-db72-4b80-b1b0-d745d6d5456d` (formerly Azure AI User). Without
      this the candidate-staging step fails on
      `Microsoft.CognitiveServices/accounts/AIServices/agents/read`.
   2. **Cognitive Services OpenAI User** on the underlying Azure AI Services
      account that hosts the evaluator model deployment
      (typically the parent account of the Foundry project). Role id
      `5e0bd9bd-7b93-4f28-af87-19fc36ad61bd`. Without this the Foundry
      `azure_ai_evaluator` graders fail with a 401 `PermissionDenied` on
      `Microsoft.CognitiveServices/accounts/OpenAI/deployments/chat/completions/action`
      and every metric comes back `null` in the cloud eval report. AgentOps now
      lifts that error into `results.json` and the orchestrator's "0 usable
      metric scores" warning so the cause is visible in CI logs, but the
      workflow still fails the gate. Grant this role **before** the first run.
   Azure **Reader** is not enough for either step.
13. If either RBAC assignment is missing, do not run the workflow yet.
   Show the exact GitHub OIDC client ID / service principal, desired role,
   target scope (project for Foundry User, AI Services account for Cognitive
   Services OpenAI User), then ask the user to approve the role assignment or
   get an Azure/Foundry admin to grant it. After assignment, read it back or ask
   the user to confirm before dispatching the workflow.
   When the user approves and you know the scopes, use the role ids to avoid
   rename drift:
   - `az ad sp show --id <AZURE_CLIENT_ID> --query id -o tsv`
   - `az role assignment list --assignee <sp-object-id> --scope <foundry-scope> --include-inherited`
   - `az role assignment create --assignee-object-id <sp-object-id> --assignee-principal-type ServicePrincipal --role 53ca6127-db72-4b80-b1b0-d745d6d5456d --scope <foundry-scope>`
   - `az role assignment create --assignee-object-id <sp-object-id> --assignee-principal-type ServicePrincipal --role 5e0bd9bd-7b93-4f28-af87-19fc36ad61bd --scope <ai-services-account-scope>`
   The AI Services account scope looks like
   `/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<ai-account-name>`
   and can be derived from
   `az cognitiveservices account list --resource-group <foundry-project-rg> --query "[?kind=='AIServices'].id" -o tsv`.
14. Ask before creating or updating GitHub repos, GitHub environments,
   variables/secrets, Entra app registrations/service principals, federated
   credentials, managed identities, or Azure RBAC assignments.
15. When creating federated credentials from PowerShell, avoid fragile
   interpolation. Do **not** write `"repo:$repo:environment:$envName"` because
   `$repo:` can be parsed as a scoped variable. Use
   `"repo:${repo}:environment:${envName}"` or
   `("repo:{0}:environment:{1}" -f $repo, $envName)`, then build JSON from a
   PowerShell object with `ConvertTo-Json`.
16. After creating or updating a federated credential, read it back and verify
    before triggering a workflow:
    - `subject` exactly matches the generated workflow subject.
    - `issuer` is `https://token.actions.githubusercontent.com`.
    - `audiences` includes `api://AzureADTokenExchange`.
    If any value differs, fix the credential before running GitHub Actions.
17. After setting GitHub environment variables, read them back and verify
    `AZURE_TENANT_ID` still matches the app/federated-credential tenant before
    triggering a run. If `azure/login` fails with `AADSTS53003`, first re-check
    this tenant/app alignment before assuming Conditional Access is the root
    cause.
18. Do not dispatch `gh workflow run` as a surprise validation step. First show
    that the GitHub environment, variables/secrets, federated credential, and
    Foundry RBAC are ready, then ask the user before triggering workflows.
19. Avoid broad discovery unless local config is missing. Do **not** run broad
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

The PR workflow uses the eval step as the hard merge gate. Doctor also
runs there and writes release evidence; by default the Doctor step blocks
the PR on critical findings such as regression detection (`--severity-fail
critical`, the default behavior of `agentops workflow generate
--doctor-gate critical`). This catches metric drops (for example
groundedness going from 5.0 to 4.0) that would still pass the configured
eval thresholds. To restore the pre-1.x advisory behavior — Doctor writes
release evidence but does not block the PR — generate with `--doctor-gate
none`. DEV/QA/PROD deploy workflows always keep Doctor as a critical
release gate; the `--doctor-gate` flag only controls the PR template.

## Step 0 - Prerequisites

1. `pip install "agentops-accelerator @ git+https://github.com/Azure/agentops.git@main"` if `agentops` is missing.
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
(preferred for the tutorials), or at repository level when intentionally shared
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

For Foundry prompt-agent projects that use trace sampling or
**Create dataset → From traces**, also verify the Foundry project managed
identity can read telemetry: grant or verify **Reader** on the connected
Application Insights resource, and on the backing Log Analytics workspace when
the App Insights component is workspace-based. This permission is not covered by
the GitHub OIDC service principal roles above.

Then configure Workload Identity Federation on the Azure side
(`federated-credentials` on the app registration) for **each branch /
environment** the workflows will run from. See
`docs/ci-github-actions.md` for the exact `az` commands.

Also grant the same app registration / service principal **two** Azure
RBAC roles before the first workflow run; both are required and the eval
step fails silently (every metric returns `null`) if only one is in place:

1. **Foundry User** on the Foundry project or Foundry resource. The PR gate
   uses Foundry data-plane APIs to read prompt agents; Azure `Reader` only
   proves ARM access and will still fail the eval step with
   `Microsoft.CognitiveServices/accounts/AIServices/agents/read`.
2. **Cognitive Services OpenAI User** on the underlying Azure AI Services
   account that hosts the evaluator model deployment. Without this, Foundry
   `azure_ai_evaluator` graders fail with a 401 `PermissionDenied` on the
   OpenAI `chat/completions/action` data action and every metric returns
   `null` in the cloud eval report. AgentOps surfaces that error in
   `results.json` and the orchestrator's "0 usable metric scores" warning,
   but the workflow still fails the gate — fix the role before the run.

Tell the user that CI evals emit `agentops.eval.*` telemetry and scheduled
Doctor runs emit `agentops.agent.finding.*` telemetry when App Insights is
configured or auto-discovered. The Cockpit uses those signals for Azure
Monitor deep links.

### Azure DevOps (Service Connection)

Already done in Step 2 - the `agentops-azure` service connection
handles auth. Make sure the underlying service principal or managed
identity has **both** the **Foundry User** role on the Foundry project (or
Foundry resource) **and** the **Cognitive Services OpenAI User** role on the
underlying Azure AI Services account that hosts the evaluator model. Both
are required; without the OpenAI User role the Foundry graders fail with a
401 `PermissionDenied` and every cloud eval metric returns `null`.

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
2. look up the seed agent in the active environment's Foundry project;
   if it does not exist (typical first deploy into dev / qa / prod),
   read the optional `prompt_agent_bootstrap` block from
   `agentops.yaml` (required `model`, optional `description`,
   `model_parameters`, `tools`) plus `prompt_file` and create the
   first version automatically (recorded as `action: "bootstrapped"`);
3. otherwise create or reuse a candidate Foundry prompt-agent version
   from `prompt_file`;
4. generate `.agentops/deployments/agentops.candidate.yaml`;
5. run `agentops eval run` against the candidate version;
6. record `.agentops/deployments/foundry-agent.json` as a deployment
   artifact only when the gate passes.

This avoids the bad pattern of evaluating one agent version and deploying a
different prompt. The invariant is: **evaluated version == deployed version**.
Foundry manages agent versions; AgentOps owns the repo-side gate and
deployment record. For multi-environment prompt-agent workflows
(sandbox → dev → qa → prod), strongly recommend adding the
`prompt_agent_bootstrap` block so operators do not have to manually
recreate the seed agent in every Foundry project.

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
  `--force`, `--dir`, `--kinds`, `--platform`, `--deploy-mode`, and
  `--doctor-gate`.
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
