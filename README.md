<h1 align="center">AgentOps Toolkit</h1>

<p align="center">
AgentOps CLI for evaluation, observability, and operational workflows for Microsoft Foundry Agents and Models.
</p>

<p align="center">
<a href="./LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-green.svg"/></a>
<img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB.svg"/>
<img alt="CLI" src="https://img.shields.io/badge/CLI-Typer-5A67D8.svg"/>
<img alt="Built on Microsoft Foundry" src="https://img.shields.io/badge/Built%20on-Microsoft%20Foundry-0078D4.svg"/>
</p>

## Overview

AgentOps Toolkit is a CLI built on Microsoft Foundry that standardizes evaluation and operational workflows for AI agents and models, helping teams run, monitor, and automate AgentOps processes.

The project enables:

- Consistent local and CI execution of agent evaluations
- Reusable evaluation policies through bundles
- Operational observability through tracing, monitoring, and run inspection
- Stable machine-readable outputs for automation
- Human-readable reports for PR reviews and quality gates

Operational capabilities include:

- Standardized evaluation workflows
- Run history and result inspection
- Tracing and observability
- Monitoring (dashboards and alerts)
- CI/CD automation
- Operational reporting and analysis

Core outputs:

- `results.json` (machine-readable)
- `report.md` (human-readable)

Exit code contract:

- `0` execution succeeded and all thresholds passed
- `2` execution succeeded but one or more thresholds failed
- `1` runtime or configuration error

## Quickstart

This section is structured for demos and onboarding, so you can present the project flow end-to-end in a few minutes.

### 1) Install

```bash
python -m venv .venv
# activate your venv in the current shell
python -m pip install -U pip
python -m pip install agentops-toolkit
```

### 2) Initialize Workspace

```bash
agentops init
```

Generated structure:

```text
.agentops/
├── config.yaml
├── run.yaml
├── run-rag.yaml
├── run-agent.yaml
├── .gitignore
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
└── results/
```

### 3) Configure Foundry Endpoint

PowerShell:

```powershell
$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://<resource>.services.ai.azure.com/api/projects/<project>"
```

Bash/zsh:

```bash
export AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="https://<resource>.services.ai.azure.com/api/projects/<project>"
```

Authentication uses `DefaultAzureCredential`:
- local: `az login`
- CI/CD: service principal env vars
- Azure-hosted: managed identity

### 4) Choose Scenario Run Config

Starter run files created by `agentops init`:
- `.agentops/run.yaml` (default model-direct)
- `.agentops/run-rag.yaml` (agent + rag baseline)
- `.agentops/run-agent.yaml` (agent + tools baseline)

Important:
- Replace placeholders (`backend.model`, `backend.agent_id`) with values that exist in your Foundry project.
- There is no universal deployment name guaranteed across all Foundry projects/regions.

### 5) Run Evaluation

```bash
agentops eval run
```

Or run a specific scenario file:

```bash
agentops eval run --config .agentops/run-rag.yaml
agentops eval run --config .agentops/run-agent.yaml
```

Default behavior:
- input config: `.agentops/run.yaml`
- output location: timestamped folder under `.agentops/results/`
- latest pointer: `.agentops/results/latest/`

### 6) Regenerate Report (Optional)

```bash
agentops report
```

Default input:
- `.agentops/results/latest/results.json`

## Evaluation Scenarios

Starter bundles created by `agentops init`:

| Bundle | Evaluators | Typical use |
|---|---|---|
| `model_direct_baseline` (default) | `SimilarityEvaluator` + `avg_latency_seconds` | Model-direct QA checks |
| `rag_retrieval_baseline` | `GroundednessEvaluator` + `avg_latency_seconds` | RAG groundedness checks |
| `agent_tools_baseline` | `SimilarityEvaluator` + `avg_latency_seconds` | Agent-with-tools baseline (placeholder) |

`datasets/` stores YAML dataset definitions.
`data/` stores JSONL rows referenced by dataset definitions.

## Commands

### Command Line Reference

| Command | Description | Status |
|---|---|---|
| `agentops init [--path DIR]` | Scaffold project workspace and starter files | Available |
| `agentops eval run` | Evaluate a dataset against a bundle | Available |
| `agentops eval compare --runs ID1,ID2` | Compare two past runs | Planned (stub) |
| `agentops run list\|show` | List or inspect past runs | Planned (stub) |
| `agentops run view <id> [--entry N]` | Deep run inspection | Planned (stub) |
| `agentops report` | Regenerate `report.md` from `results.json` | Available |
| `agentops report show\|export` | View/export reports | Planned (stub) |
| `agentops bundle list\|show` | Browse bundle catalog | Planned (stub) |
| `agentops dataset validate\|describe\|import` | Dataset utilities | Planned (stub) |
| `agentops config validate\|show\|cicd` | Config/CI utilities | Planned (stub) |
| `agentops trace init` | Tracing setup | Planned (stub) |
| `agentops monitor setup\|dashboard\|alert` | Monitoring operations | Planned (stub) |
| `agentops model list` | List Foundry model deployments | Planned (stub) |
| `agentops agent list` | List Foundry agents | Planned (stub) |

Implemented command usage:

```bash
agentops init [--path <dir>]
agentops eval run [--config <path>] [--output <dir>]
agentops report [--in <results.json>] [--out <report.md>]
```

For planned commands, the CLI returns a friendly message indicating the command is planned but not implemented in this release.

## Project Structure

High-level code layout:

- `src/agentops/cli/` command entrypoints (Typer)
- `src/agentops/services/` orchestration workflows
- `src/agentops/backends/` execution engines (`foundry`, `subprocess`)
- `src/agentops/core/` schemas, thresholds, and report generation
- `src/agentops/templates/` starter workspace assets
- `tests/unit/` and `tests/integration/` automated tests

## Documentation

- Architecture and request flow: [docs/how-it-works.md](docs/how-it-works.md)
- Foundry agent tutorial: [docs/tutorial-basic-foundry-agent.md](docs/tutorial-basic-foundry-agent.md)
- Model-direct tutorial: [docs/tutorial-model-direct.md](docs/tutorial-model-direct.md)
- RAG tutorial: [docs/tutorial-rag.md](docs/tutorial-rag.md)
- Built-in evaluator notes: [docs/foundry-evaluation-sdk-built-in-evaluators.md](docs/foundry-evaluation-sdk-built-in-evaluators.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for architecture rules, testing expectations, and contribution workflow.
