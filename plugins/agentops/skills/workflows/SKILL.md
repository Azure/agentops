---
name: workflows
description: Set up CI/CD pipelines for AgentOps evaluations using GitHub Actions. Trigger when users say "CI/CD", "GitHub Actions", "pipeline", "workflow", "PR gating", "continuous evaluation", "automate evals", "agentops workflow generate", "CI setup", "evaluation in CI". Install agentops-toolkit via pip. Command is agentops workflow generate.
---

# AgentOps Workflows

> **Prerequisite:** Install the AgentOps CLI with `pip install agentops-toolkit`.

## Purpose
Help users set up CI/CD pipelines that run AgentOps evaluations automatically — on pull requests, on schedule, or on demand. Uses GitHub Actions with Workload Identity Federation (OIDC) for secure Azure authentication.

## When to Use
- User wants to run evaluations in CI/CD.
- User asks about GitHub Actions integration.
- User wants to gate PRs on evaluation quality.
- User asks about `agentops workflow generate`.
- User wants to automate evaluation runs.

## Codebase Analysis (Do This First)

Before asking questions, check the workspace:

1. **Is AgentOps initialized?** Look for `.agentops/` directory. If not present, run `agentops init` first.
2. **Does a workflow already exist?** Check `.github/workflows/agentops-eval.yml`. If it exists, the user may want to customize it rather than regenerate.
3. **Is there a valid run.yaml?** Check `.agentops/run.yaml` — the workflow needs this to run evaluations.
4. **Which CI platform?** Check for `.github/workflows/` (GitHub Actions). Only GitHub Actions is supported today.
5. **Is the endpoint configured?** Search for `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` in `.env`, `.env.local`, or environment variables. If not found, **ask the user** for the Foundry project endpoint URL — they will need it to configure the GitHub secret.
6. **Are Azure credentials available?** Check if the user has `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`. If not, guide them through the OIDC setup.

Only ask about values you cannot find in the codebase or environment files.

## Available Commands

```bash
agentops workflow generate [--force] [--dir <path>]   # Generate GitHub Actions workflow
agentops init                                          # Scaffold .agentops/ workspace (prerequisite)
agentops eval run [-c <run.yaml>] [-f md|html|all]     # Run evaluation (what the workflow calls)
```

### Key flags
- `--force` — Overwrite existing workflow file
- `--dir` — Target repository root directory (default: current directory)

## Setup Workflow

### Step 1 — Initialize workspace
```bash
agentops init
```
Creates `.agentops/` with run config, bundles, datasets, and starter data.

### Step 2 — Generate the workflow
```bash
agentops workflow generate
```
Creates `.github/workflows/agentops-eval.yml`.

### Step 3 — Configure Azure authentication (OIDC)

The workflow uses **Workload Identity Federation** — no secrets to rotate.

**Azure setup (one-time):**
1. Create or reuse an App Registration in Microsoft Entra ID.
2. Add a Federated Credential:
   - Organization: your GitHub org/user
   - Repository: your repo name
   - Entity type: `Pull Request` (for PR triggers)
3. Grant the app the required role on your Foundry project (e.g., `Cognitive Services User`).

**GitHub setup:**

Set as **repository variables** (Settings → Secrets and variables → Actions → Variables):

| Variable | Value |
|---|---|
| `AZURE_CLIENT_ID` | Application (client) ID |
| `AZURE_TENANT_ID` | Directory (tenant) ID |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |

Set as **repository secret**:

| Secret | Value |
|---|---|
| `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` | Foundry project endpoint URL |

### Step 4 — Push a PR
The evaluation runs automatically on pull requests targeting `main`.

## How the Workflow Works

### Triggers
| Trigger | When |
|---|---|
| `pull_request` | Any PR targeting `main` |
| `workflow_dispatch` | Manual run from Actions tab (supports custom config path) |

### Exit codes and CI behavior
| Exit Code | Meaning | CI Result |
|---|---|---|
| `0` | All thresholds passed | Job passes |
| `2` | One or more thresholds failed | Job fails (gates the PR) |
| `1` | Runtime or configuration error | Job fails |

### Artifacts uploaded
The workflow uploads these as `agentops-eval-results`:

| File | Description |
|---|---|
| `results.json` | Machine-readable evaluation results |
| `report.md` | Human-readable summary |
| `backend_metrics.json` | Raw backend scores per row |
| `cloud_evaluation.json` | Foundry portal link (cloud eval only) |
| `backend.stdout.log` | Backend stdout capture |
| `backend.stderr.log` | Backend stderr capture |

Artifacts are uploaded even when the evaluation fails (`if: always()`).

### PR comments
The workflow automatically posts (or updates) a PR comment with the full `report.md`. Subsequent pushes to the same PR update the existing comment.

## Customization

### Multiple evaluation configs
Use a matrix strategy:
```yaml
jobs:
  evaluate:
    strategy:
      fail-fast: false
      matrix:
        config:
          - .agentops/runs/model-direct.yaml
          - .agentops/runs/rag-retrieval.yaml
    steps:
      - name: Run evaluation
        run: agentops eval run --config ${{ matrix.config }}
```

### Custom output directory
```yaml
- name: Run evaluation
  run: agentops eval run --config .agentops/run.yaml --output ./eval-output
```

### Different branch triggers
Edit `on.pull_request.branches` in the workflow file:
```yaml
on:
  pull_request:
    branches: [main, develop]
```

## Troubleshooting

| Problem | Solution |
|---|---|
| `agentops: command not found` | Ensure `pip install agentops-toolkit` runs before the eval step |
| Authentication errors | Check federated credential, verify `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` are set as variables |
| `Error: evaluation failed` (exit 1) | Check `.agentops/run.yaml` exists and is valid |
| `Threshold status: FAILED` (exit 2) | Review `report.md` — thresholds too strict or quality regressed |

## Guardrails
- Do not invent workflow features beyond what `agentops workflow generate` produces.
- Only GitHub Actions is supported today. If the user asks about other CI platforms, explain that only GitHub Actions is supported and offer to help adapt manually.
- The workflow requires `.agentops/run.yaml` — ensure the workspace is initialized first.
- Always recommend OIDC/Workload Identity Federation over client secrets.

## Examples
- "Set up CI for my evaluations"
  → `agentops init` (if needed), then `agentops workflow generate`. Configure OIDC credentials. Push a PR to trigger.
- "I want PRs blocked when eval quality drops"
  → The workflow already does this — exit code 2 (threshold failure) fails the GitHub Actions job, which blocks the PR merge.
- "How do I run evals on a schedule?"
  → Add a `schedule` trigger to the workflow: `on: schedule: [{cron: '0 6 * * 1'}]` for weekly Monday 6am UTC.
- "Can I run different eval configs per PR?"
  → Use matrix strategy (see Customization above) — one job per config, all run in parallel.

## Learn More
- Documentation: https://github.com/Azure/agentops
- CI/CD guide: `docs/ci-github-actions.md`
- PyPI: https://pypi.org/project/agentops-toolkit/
