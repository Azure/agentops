---
name: agentops-workspace-setup
description: Guide users through initializing an AgentOps workspace, configuring CI/CD pipelines, and managing workspace settings. Trigger when users ask to initialize agentops, scaffold workspace, generate CI/CD workflow, set up GitHub Actions, configure agentops, validate config, show config, customize workspace paths, or set up evaluation pipelines. Common phrases include "initialize agentops", "set up workspace", "config cicd", "CI/CD pipeline", "GitHub Actions", "generate workflow", "configure agentops", "workspace setup", "config.yaml", "config validate", "config show". Install agentops-toolkit via pip. Commands are agentops init, agentops config cicd, agentops config validate, and agentops config show.
---

# AgentOps Workspace Setup

> **Prerequisite:** Install the AgentOps CLI with `pip install agentops-toolkit`.

## Purpose

Guide users through initializing an AgentOps evaluation workspace, configuring CI/CD pipelines with GitHub Actions, and managing workspace configuration.

## When to Use

- User wants to start using AgentOps in a new project.
- User asks how to set up the `.agentops/` directory.
- User wants to generate a GitHub Actions workflow for evaluation.
- User asks about CI/CD integration for AgentOps evaluations.
- User wants to inspect or validate workspace configuration.
- User asks about workspace directory structure or config.yaml.

## Available Commands

```bash
agentops init [--path <dir>] [--force]                # Scaffold .agentops/ workspace
agentops config cicd [--force] [--dir <path>]         # Generate GitHub Actions workflow
agentops config validate                              # Validate workspace config (planned)
agentops config show                                  # Show resolved config (planned)
```

### Key Flags

| Command | Flag | Description |
|---|---|---|
| `init` | `--path / --dir` | Target project directory (default: current directory) |
| `init` | `--force` | Overwrite existing files |
| `config cicd` | `--force` | Overwrite existing workflow file |
| `config cicd` | `--dir` | Project root directory (default: current directory) |

## Recommended Workflow

### Initialize a New Workspace

1. Navigate to your project root.
2. Run `agentops init` to scaffold the `.agentops/` directory.
3. Review the generated files and customize as needed.

```bash
cd my-project
agentops init
```

This creates:

```
.agentops/
├── config.yaml                 # Workspace defaults
├── run.yaml                    # Default run configuration
├── run-rag.yaml                # RAG evaluation run config
├── run-agent.yaml              # Agent evaluation run config
├── .gitignore                  # Git exclusions for results
├── bundles/
│   ├── model_direct_baseline.yaml
│   ├── rag_retrieval_baseline.yaml
│   └── agent_tools_baseline.yaml
├── datasets/
│   ├── smoke-model-direct.yaml
│   ├── smoke-rag.yaml
│   └── smoke-agent-tools.yaml
├── data/
│   ├── smoke-model-direct.jsonl
│   ├── smoke-rag.jsonl
│   └── smoke-agent-tools.jsonl
└── results/                    # Created on first run
```

Use `--force` to re-scaffold and overwrite existing files:

```bash
agentops init --force
```

### Configure run.yaml

Edit `.agentops/run.yaml` to point to your bundle, dataset, and backend:

```yaml
version: 1
bundle:
  path: bundles/model_direct_baseline.yaml
dataset:
  path: datasets/smoke-model-direct.yaml
backend:
  type: foundry
  target: model
  model: gpt-4o-mini
  project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
  timeout_seconds: 1800
output:
  write_report: true
```

Backend type options:
- `type: foundry` — Microsoft Foundry Agent Service (default)
- `type: subprocess` — Custom subprocess pipeline

Foundry target options:
- `target: agent` — Evaluate a Foundry agent (requires `agent_id`)
- `target: model` — Evaluate a model deployment directly (requires `model`)

### Set Up CI/CD with GitHub Actions

1. Generate the workflow file:

```bash
agentops config cicd
```

This creates `.github/workflows/agentops-eval.yml`.

2. Configure GitHub repository settings:

**Repository variables** (Settings → Secrets and variables → Actions → Variables):

| Variable | Value |
|---|---|
| `AZURE_CLIENT_ID` | Application (client) ID |
| `AZURE_TENANT_ID` | Directory (tenant) ID |
| `AZURE_SUBSCRIPTION_ID` | Subscription ID |

**Repository secret** (Settings → Secrets and variables → Actions → Secrets):

| Secret | Value |
|---|---|
| `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` | Foundry project endpoint URL |

3. The workflow uses **Workload Identity Federation (OIDC)** — no client secrets to rotate.

4. Triggers:
   - `pull_request` — Runs on PRs targeting `main` or `develop`
   - `workflow_dispatch` — Manual runs from the Actions tab

5. Push a PR to trigger the evaluation automatically.

### Regenerate the workflow file

Use `--force` to overwrite an existing workflow:

```bash
agentops config cicd --force
```

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Command succeeded |
| `1` | Runtime or configuration error |

## Workspace Config Reference

The `.agentops/config.yaml` file controls workspace-level defaults:

```yaml
paths:
  bundles_dir: bundles
  datasets_dir: datasets
  data_dir: data
  results_dir: results
defaults:
  backend: foundry
  timeout_seconds: 1800
report:
  generate_markdown: true
```

## CI/CD Artifacts

The generated workflow uploads these artifacts as `agentops-eval-results`:

| File | Description |
|---|---|
| `results.json` | Machine-readable evaluation results |
| `report.md` | Human-readable Markdown summary |
| `cloud_evaluation.json` | Foundry portal link (cloud mode only) |
| `backend_metrics.json` | Raw backend scores per row |

## Troubleshooting

- **"No .agentops workspace found"** — Run `agentops init` first.
- **Workflow file already exists** — Use `agentops config cicd --force` to overwrite.
- **OIDC authentication fails** — Ensure federated credentials match your repo and branch pattern.
- **Missing environment variables** — Set `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` as a repository secret.
