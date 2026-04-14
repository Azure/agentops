---
name: agentops-workflow
description: Generate CI/CD pipelines tailored to the project — PR gating, post-merge CI evaluation, and CD with safety QA + deploy placeholder. Trigger when users ask to automate evaluations in CI, set up PR gating, generate workflow files, or create pipelines for their project. Common phrases include "CI/CD", "GitHub Actions", "pipeline", "workflow", "PR gating", "continuous evaluation", "automate evals", "workflow generate", "CI setup", "generate pipelines", "create pipelines for my project". Install agentops-toolkit via pip.
---

# AgentOps Workflow

Generate a complete CI/CD pipeline suite for AgentOps evaluations — tailored to the project's evaluation scenarios, bundles, and Foundry configuration.

## Pipeline Types

`agentops workflow generate` auto-detects which pipelines to create:

| Pipeline | File | When generated | Purpose |
|---|---|---|---|
| **PR Evaluation** | `agentops-eval.yml` | Always | Fast evaluation gate on pull requests |
| **CI Evaluation** | `agentops-eval-ci.yml` | Multiple bundles or run configs detected | Full evaluation on merge to develop/main |
| **CD Pipeline** | `agentops-eval-cd.yml` | Multiple bundles or run configs detected | Safety QA gate + deploy placeholder on merge to main |

### Pipeline Flow (GenAIOps-inspired)

```
feature/* → PR to develop   → agentops-eval.yml (PR gate)
             merge to develop → agentops-eval-ci.yml (CI evaluation)
             release/* → PR to main → agentops-eval.yml (PR gate)
             merge to main   → agentops-eval-cd.yml (safety QA → deploy)
```

## Step 0 — Prerequisites

1. **AgentOps installed?** Check if `agentops` CLI is available. If not: `pip install agentops-toolkit`.
2. **Workspace exists?** Check for `.agentops/`. If missing: `agentops init`.
3. **Foundry endpoint configured?** Search for `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` in environment variables, `.env`, `.env.local`, `.azure/<env>/.env`. If not found, ask the user for the endpoint URL.
4. **run.yaml ready?** A valid run config is required. If missing, delegate to `/agentops-config`.

## Step 1 — Workspace Inspection

Before generating, inspect the workspace to understand what pipelines are needed:

1. **List bundles**: Read `.agentops/bundles/` — identify which evaluation scenarios are configured.
2. **List run configs**: Check `.agentops/` for `run*.yaml` files — if multiple configs exist, CI and CD pipelines are appropriate.
3. **Check Foundry endpoint**: Look for `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` or `project_endpoint` in run.yaml and env vars.
4. **Detect branches**: Run `git branch -a` to list local and remote branches.
   - If `main` and `develop` exist → use them (default convention, no question needed).
   - If branches don't exist yet → use `main`/`develop` convention (no question needed).
   - If the repo uses different names (e.g. `master` instead of `main`, or no `develop`) → ask the user to confirm which branches to use for PR targets and push triggers.

Present a summary:
```
Detected:
  Bundles: model_quality_baseline, rag_quality_baseline
  Run configs: run.yaml
  Foundry endpoint: ✓ (from .env)
  Branches: main, develop
  Pipelines: PR (always), CI + CD (multiple bundles detected)
```

## Step 2 — Ask Only What Cannot Be Inferred

Only ask critical questions that workspace inspection cannot answer:

1. If no Foundry endpoint found: *"What is your Azure AI Foundry project endpoint URL?"*
2. If branches differ from the `main`/`develop` convention: *"Your repo uses `master` instead of `main`. Should the pipelines target `master`, or do you plan to rename it to `main`?"*

**DO NOT ask about**:
- Bundle selection (inferred from workspace)
- Evaluation scenarios (inferred from bundles)
- Authentication method (always OIDC / Workload Identity Federation)
- Workflow file locations (standard `.github/workflows/` paths)
- Which pipelines to generate (auto-detected)

## Step 3 — Generate Workflows

```bash
agentops workflow generate [--force] [--dir <path>]
```

Flags:
- `--force` — Overwrite existing workflow files.
- `--dir` — Target directory (default: current directory).

After generation, explain what was created and why:
- `agentops-eval.yml` — Runs on PRs to main/develop. Gates merges on evaluation thresholds.
- `agentops-eval-ci.yml` — (if generated) Runs on push to develop/main when `.agentops/`, `src/`, or `pyproject.toml` change. Comprehensive post-merge evaluation with commented-out matrix strategy and baseline comparison.
- `agentops-eval-cd.yml` — (if generated) Runs on push to main. Two-job pipeline: safety QA evaluation gate → deploy placeholder. The deploy job is a TODO for the team to fill in with their deployment commands.

## Step 4 — Configure Authentication

All pipelines use **Workload Identity Federation (OIDC)** — no client secrets to manage or rotate.

### Azure Setup (one-time)

1. **Create or reuse an App Registration** in Microsoft Entra ID (Azure AD).
2. **Add a Federated Credential**:
   - Go to App Registration → Certificates & secrets → Federated credentials → Add credential
   - Organization: your GitHub org/user
   - Repository: your repo name
   - Entity type: select **Pull Request** (for PR pipeline) AND **Branch** (for CI and CD pipelines)
   - Name: e.g. `github-agentops-eval`
3. **Grant the app required roles** on the Foundry project resource group:
   - `Cognitive Services User` — invoke agents and evaluator models
   - `Azure AI Developer` — access evaluation APIs and Foundry features

### GitHub Setup

Set these as **repository variables** (Settings → Secrets and variables → Actions → Variables tab):

| Variable | Value |
|---|---|
| `AZURE_CLIENT_ID` | Application (client) ID from App Registration |
| `AZURE_TENANT_ID` | Directory (tenant) ID |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |

Set this as a **repository secret** (Secrets tab):

| Secret | Value |
|---|---|
| `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` | Foundry project endpoint URL |

### Verify Auth Locally

```bash
az login
az account show --query "{sub:id, tenant:tenantId}" -o json
az account get-access-token --resource "https://cognitiveservices.azure.com" --query accessToken -o tsv
```

## Step 5 — Verify Pipelines

1. **PR pipeline**: Push a branch and open a PR → check the Actions tab for `AgentOps Evaluation`.
2. **CI pipeline**: Merge to develop → check Actions tab for `AgentOps CI Evaluation`.
3. **CD pipeline**: Merge to main → check Actions tab for `AgentOps CD Pipeline`. The safety-qa job runs evaluation; the deploy job prints a placeholder notice.
4. **Check results**: Download artifacts, review PR comments, inspect job summaries.

If any pipeline fails with authentication errors:
- Verify federated credential entity types match (Pull Request for PRs, Branch for push)
- Confirm the App Registration has `Cognitive Services User` role on the Foundry resource
- Check that variables and secrets are set at the repository level (not organization)

## Exit Code Gating

All pipelines use the same exit code contract:

| Exit code | CI result | Meaning |
|---|---|---|
| `0` | ✅ Pass | All thresholds met |
| `2` | ❌ Fail | Threshold(s) failed — blocks merge / blocks deploy |
| `1` | ❌ Fail | Runtime or configuration error |

## Customisation After Generation

- **Change branch triggers**: Edit `on.pull_request.branches` or `on.push.branches` in the workflow files.
- **Enable matrix strategy**: Uncomment the `strategy.matrix` block in `agentops-eval-ci.yml` and list your run configs.
- **Enable baseline comparison**: Uncomment the comparison step in `agentops-eval-ci.yml`.
- **Add deployment steps**: Edit the `deploy` job in `agentops-eval-cd.yml` — replace the placeholder with your actual deployment commands.
- **Add environment approval**: Uncomment `environment: production` in the deploy job for manual approval gates.

## Rules

- Do not modify generated workflow files beyond user-requested customisation.
- Always recommend OIDC / Workload Identity Federation over client secrets.
- Delegate evaluation configuration to `/agentops-config`.
- Delegate dataset creation to `/agentops-dataset`.
- Do not fabricate endpoint URLs, agent IDs, or deployment names.
- Do not ask about bundle/scenario selection if it can be inferred from the workspace.
