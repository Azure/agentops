---
name: agentops-workflow
description: Generate CI/CD pipelines tailored to the project — PR gating, post-merge CI evaluation, and CD with safety QA + deploy placeholder. Trigger when users ask to automate evaluations in CI, set up PR gating, generate workflow files, or create pipelines for their project. Common phrases include "CI/CD", "GitHub Actions", "pipeline", "workflow", "PR gating", "continuous evaluation", "automate evals", "workflow generate", "CI setup", "generate pipelines", "create pipelines for my project". Install agentops-toolkit via pip.
---

# AgentOps Workflow

Generate a complete CI/CD pipeline suite tailored to the project's evaluation scenarios.

## Pipeline Types (auto-detected)

| Pipeline | File | When generated | Purpose |
|---|---|---|---|
| **PR Evaluation** | `agentops-eval.yml` | Always | Gate PRs on evaluation thresholds |
| **CI Evaluation** | `agentops-eval-ci.yml` | Multiple bundles/configs | Post-merge comprehensive evaluation |
| **CD Pipeline** | `agentops-eval-cd.yml` | Multiple bundles/configs | Safety QA gate + deploy placeholder |

## Step 0 — Prerequisites

1. Run `pip install agentops-toolkit` if `agentops` command is not available.
2. Run `agentops init` if `.agentops/` directory does not exist.
3. Ensure `.agentops/run.yaml` exists and is valid. If not, delegate to `/agentops-config`.

## Step 1 — Workspace Inspection

Before generating, inspect the workspace:

1. **List bundles** in `.agentops/bundles/` — identify scenarios (model quality, RAG, agent, safety).
2. **List run configs** in `.agentops/` — if multiple `run*.yaml` exist, CI and CD pipelines are appropriate.
3. **Check Foundry endpoint** — look in run.yaml, env vars, `.env`, `.azure/<env>/.env`.
4. **Detect branches** — run `git branch -a`. Use `main`/`develop` if they exist or if no branches exist yet. If the repo uses different names (e.g. `master`), ask the user to confirm.

Present what was detected. Only ask for: Foundry endpoint (if not found) and branch confirmation (if repo uses names other than `main`/`develop`).

**DO NOT ask about**: bundle selection, scenarios, auth method, workflow paths, which pipelines to generate.

## Step 2 — Generate Workflows

```bash
agentops workflow generate [--force] [--dir <path>]
```

Explain what was generated:
- `agentops-eval.yml` — PR gate on main/develop
- `agentops-eval-ci.yml` — (if generated) Post-merge CI with optional matrix strategy and baseline comparison
- `agentops-eval-cd.yml` — (if generated) Safety QA evaluation gate + deploy placeholder on merge to main

### Pipeline Flow

```
feature → PR to develop   → agentops-eval.yml
           merge to develop → agentops-eval-ci.yml
           release → PR to main → agentops-eval.yml
           merge to main    → agentops-eval-cd.yml (safety QA → deploy)
```

## Step 3 — Configure Authentication (OIDC)

### Azure Setup

1. Create/reuse App Registration in Microsoft Entra ID.
2. Add Federated Credential (entity types: **Pull Request** + **Branch** for your repo).
3. Grant roles on Foundry project: `Cognitive Services User`, `Azure AI Developer`.

### GitHub Setup

Repository **variables** (not secrets): `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`

Repository **secret**: `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`

## Step 4 — Verify

1. Push a PR → check `AgentOps Evaluation` in Actions tab.
2. Merge to develop → check `AgentOps CI Evaluation`.
3. Merge to main → check `AgentOps CD Pipeline`. Safety-qa job runs evaluation; deploy job prints a placeholder notice.

## Exit Code Gating

| Code | CI result | Meaning |
|---|---|---|
| `0` | Pass | All thresholds met |
| `2` | Fail | Threshold(s) failed — blocks merge / blocks deploy |
| `1` | Fail | Runtime error |

## Customisation After Generation

- **Change branch triggers**: Edit `on.pull_request.branches` or `on.push.branches`.
- **Enable matrix strategy**: Uncomment the `strategy.matrix` block in `agentops-eval-ci.yml`.
- **Enable baseline comparison**: Uncomment the comparison step in `agentops-eval-ci.yml`.
- **Add deployment steps**: Edit the `deploy` job in `agentops-eval-cd.yml`.
- **Add environment approval**: Uncomment `environment: production` in the deploy job.

## Rules

- Do not modify generated workflows beyond user-requested customisation.
- Always recommend OIDC over client secrets.
- Delegate evaluation configuration to `/agentops-config`.
- Delegate dataset creation to `/agentops-dataset`.
- Do not fabricate endpoint URLs, agent IDs, or deployment names.
- Do not ask about bundle/scenario if it can be inferred from the workspace.
