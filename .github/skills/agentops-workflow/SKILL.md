---
name: agentops-workflow
description: Set up CI/CD pipelines for AgentOps evaluations using GitHub Actions. Trigger when users ask to automate evaluations in CI, set up PR gating, or generate workflow files. Common phrases include "CI/CD", "GitHub Actions", "pipeline", "workflow", "PR gating", "continuous evaluation", "automate evals", "workflow generate", "CI setup". Install agentops-toolkit via pip.
---

# AgentOps Workflow

Generate CI/CD workflow files for automated evaluations on PRs and pushes.

## Step 0 — Prerequisites

1. Run `pip install agentops-toolkit` if `agentops` command is not available.
2. Run `agentops init` if `.agentops/` directory does not exist.
3. Ensure `.agentops/run.yaml` exists and is valid. If not, delegate to `/agentops-config`.

## Step 1 — Generate workflow

```bash
agentops workflow generate [--force] [--dir <path>]
```

- `--force` — overwrite existing workflow files
- `--dir` — target directory (default: `.github/workflows/`)

Generates `.github/workflows/agentops-eval.yml` which: checks out repo → sets up Python → installs deps → runs `agentops eval run` → uses exit code to pass/fail CI.

## Step 2 — Configure secrets

Set these in repository Settings → Secrets and variables → Actions:

| Secret | Purpose |
|---|---|
| `AZURE_CLIENT_ID` | Service principal for Azure auth |
| `AZURE_TENANT_ID` | Azure AD tenant |
| `AZURE_CLIENT_SECRET` | Service principal secret |
| `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` | Foundry project URL |

## Exit Code Gating

| Code | CI result | Meaning |
|---|---|---|
| `0` | Pass | All thresholds met |
| `2` | Fail | Threshold(s) failed — blocks merge |
| `1` | Fail | Runtime error |

## Rules

- Do not modify generated workflow files beyond user-requested changes.
- Do not add features beyond what `agentops workflow generate` produces.
- Delegate evaluation configuration to `/agentops-config`.
