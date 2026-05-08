---
name: agentops-workflow
description: Set up the full GenAIOps GitFlow CI/CD scaffold for an AgentOps project. Generates four GitHub Actions workflows (PR gate + Deploy DEV / QA / PROD) wired to GitHub Environments, OIDC auth, and AgentOps eval gating. Trigger on "CI", "CD", "pipeline", "workflow", "GitHub Actions", "PR gate", "deploy", "environments", "GitFlow", "release branch", "promote to prod", "DevOps", "GenAIOps pipeline".
---

# AgentOps Workflow

Help the user wire AgentOps into a real GenAIOps GitFlow CI/CD setup with
three environments (`dev`, `qa`, `production`) and an automatic eval gate
on every change.

This skill produces four workflow files via `agentops workflow generate`
and then walks the user through the GitHub-side configuration (OIDC,
environments, branch protection, deploy step).

## Branch model assumed

```
feature/* ── PR ──▶ develop                 [agentops-pr.yml]      gate
                       │
                       └── merge ─▶ develop  [agentops-deploy-dev.yml]   build + eval + deploy DEV
release/* ── push                            [agentops-deploy-qa.yml]    build + eval + deploy QA
release/* ── PR ──▶ main                     [agentops-pr.yml]      gate
                       │
                       └── merge ─▶ main     [agentops-deploy-prod.yml]  safety eval + build + deploy PROD
```

If the user is on trunk-based development, omit `qa` and `release/**`
and have them generate `--kinds pr,dev,prod`.

## Step 0 — Prerequisites

1. `pip install "agentops-toolkit @ git+https://github.com/Azure/agentops.git@develop"` if `agentops` is missing.
2. `.agentops/run.yaml` exists and `agentops eval run` works locally.
3. The user's repo follows GitFlow (or is willing to). If not, ask which
   branches map to dev/qa/prod and adjust the `on:` triggers after
   generation.

## Step 1 — Generate the workflows

```bash
agentops workflow generate
```

This writes **four** files into `.github/workflows/`:

| File | Trigger | Environment |
|---|---|---|
| `agentops-pr.yml` | PRs to `develop`, `release/**`, `main` | (none) |
| `agentops-deploy-dev.yml` | push to `develop` | `dev` |
| `agentops-deploy-qa.yml` | push to `release/**` | `qa` |
| `agentops-deploy-prod.yml` | push to `main` | `production` |

Useful flags:

- `--force` — overwrite existing workflow files.
- `--kinds pr,dev,qa,prod` — generate a subset (e.g. `--kinds pr,dev,prod`
  for trunk-based teams).
- `--dir <path>` — non-default repo root.

## Step 2 — Configure GitHub Environments

Walk the user through Settings → Environments and create three:

1. **`dev`** — no extra protection. Set any DEV-specific variables here
   (e.g. `ACA_APP_NAME`, `AZURE_RESOURCE_GROUP` pointing at the dev RG).
2. **`qa`** — usually no required reviewers, but isolated variables for
   the QA environment.
3. **`production`** — set:
   - **Required reviewers**: at least one (deploys to PROD will pause
     here until approved).
   - (Optional) **Wait timer** for an extra delay.
   - (Optional) **Deployment branches**: restrict to `main`.
   - PROD-specific variables (e.g. production resource group).

Tell the user that env-specific variables on the `production` environment
will override repo-level ones automatically inside the prod workflow.

## Step 3 — Configure repository variables for OIDC

At repository level (Settings → Secrets and variables → Actions →
**Variables** tab), set:

- `AZURE_CLIENT_ID` — App registration / managed identity used for OIDC.
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` — Foundry project URL used by the
  eval step.

Then configure Workload Identity Federation on the Azure side
(`federated-credentials` on the app registration) for **each branch /
environment** the workflows will run from. See
`docs/ci-github-actions.md` for the exact `az` commands.

## Step 4 — Fill in the Build and Deploy placeholders

Each `agentops-deploy-*.yml` has a `Build (placeholder)` and a
`Deploy (placeholder)` step. The dev template includes commented
example snippets for the most common stacks. Replace them based on
the user's stack:

- **Container Apps** — replace Build with `az acr build` and Deploy
  with `az containerapp update --image ...`.
- **App Service** — replace Build with the package step, Deploy with
  `azure/webapps-deploy@v3`.
- **Foundry hosted agent** — Build is typically empty; Deploy publishes
  a new agent version (project-specific tooling).
- **azd-managed app** — replace Build with `azd package` and Deploy
  with `azd deploy --no-prompt` (set `AZURE_ENV_NAME` per environment).

Don't invent commands you can't see in the user's repo. If the stack
isn't obvious, ask.

## Step 5 — Branch protection

In Settings → Branches, add a rule for both `develop` and `main`:

- Require a pull request before merging.
- Require status checks to pass: select **`AgentOps PR / Eval (PR gate)`**
  (the job name from `agentops-pr.yml`).
- Optional: require linear history.

This makes the eval gate a hard merge requirement.

## Step 6 — Iterate

Common follow-ups:

- **Tighten thresholds for QA/PROD** — copy `.agentops/run.yaml` to
  `.agentops/run-qa.yaml` / `.agentops/run-prod.yaml` and tighten the
  bundle thresholds. Point each workflow at its own config via the
  `inputs.config` default.
- **Scheduled runs** — add a `schedule:` entry in `agentops-pr.yml` (or a
  new `agentops-nightly.yml`) to evaluate against `main` nightly.
- **Matrix per scenario** — if the user has multiple `runs/*.yaml` files,
  extend the eval job with `strategy.matrix.config:` and reference
  `${{ matrix.config }}`.
- **Regression baseline** — wire the deploy templates to download the
  previous run's `results.json` artifact and call
  `agentops eval compare`.

## Guardrails

- Do **not** invent CLI flags. The supported `workflow generate` flags
  are `--force`, `--dir`, `--kinds`.
- Do **not** create parallel workflow files. Prefer editing the
  generated ones.
- Do **not** auto-fill Build/Deploy with steps you can't justify from
  the user's existing code. Ask before guessing.
- The four workflow names (`agentops-pr`, `agentops-deploy-dev`,
  `agentops-deploy-qa`, `agentops-deploy-prod`) are fixed — don't rename
  them or branch-protection wiring will break.
