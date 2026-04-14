<h1 align="center">AgentOps Toolkit</h1>

<p align="center">
AgentOps CLI for evaluation, observability, and operational workflows for Microsoft Foundry Agents and Models.
</p>

<p align="center">
<a href="https://github.com/Azure/agentops/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Azure/agentops/actions/workflows/ci.yml/badge.svg?branch=develop"/></a>
<a href="https://github.com/Azure/agentops/actions/workflows/release.yml"><img alt="Release" src="https://github.com/Azure/agentops/actions/workflows/release.yml/badge.svg"/></a>
<a href="https://github.com/Azure/agentops/blob/main/LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-green.svg"/></a>
<img alt="Status: Preview" src="https://img.shields.io/badge/Status-Preview-orange.svg"/>
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

<p align="center">
<img alt="Quickstart demo: agentops init and eval run" src="https://raw.githubusercontent.com/Azure/agentops/main/media/quickstart.gif"/>
</p>

### 1) Install

```bash
python -m venv .venv
# activate your venv in the current shell
python -m pip install -U pip
python -m pip install agentops-toolkit
```

### 2) Initialize and Configure

```bash
agentops init
```

This creates `.agentops/` with starter bundles, datasets, and run configs for common scenarios (model quality, RAG, agent workflow, content safety).

Set your Foundry project endpoint:

```bash
export AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="https://<resource>.services.ai.azure.com/api/projects/<project>"
```

Then edit `.agentops/run.yaml` to set your `agent_id` and `model` deployment name.

> Authentication uses `DefaultAzureCredential` — run `az login` locally, or use service principal env vars in CI.

### 3) Run Evaluation

```bash
agentops eval run
```

Results are written to `.agentops/results/latest/`:
- `results.json` — machine-readable scores
- `report.md` — human-readable summary

To run a different scenario:

```bash
agentops eval run --config .agentops/run-rag.yaml
```

To regenerate the report from existing results:

```bash
agentops report generate
```

See [Concepts](https://github.com/Azure/agentops/blob/main/docs/concepts.md) for an overview of bundles, datasets, evaluators, backends, and the configuration model.

## Commands

| Command | Description | Status |
|---|---|---|
| `agentops --version` | Show installed version | ✅ |
| `agentops init [--path DIR]` | Scaffold project workspace, starter files, and coding agent skills | ✅ |
| `agentops eval run [--config PATH]` | Evaluate a dataset against a bundle | ✅ |
| `agentops eval compare --runs ID1,ID2` | Compare two past runs | ✅ |
| `agentops report generate [--in FILE]` | Regenerate `report.md` from `results.json` | ✅ |
| `agentops workflow generate` | Generate GitHub Actions workflow | ✅ |
| `agentops skills install [--platform <p>]` | Install coding agent skills (Copilot, Claude) | ✅ |
| `agentops run list\|show` | List or inspect past runs | 🚧 |
| `agentops bundle list\|show` | Browse bundle catalog | 🚧 |
| `agentops dataset validate\|describe` | Dataset utilities | 🚧 |
| `agentops trace init` | Tracing setup | 🚧 |
| `agentops monitor setup\|show\|configure` | Monitoring operations | 🚧 |

Planned commands return a friendly message indicating they are not yet implemented.

## Documentation

### Concepts and Architecture

- [Concepts](https://github.com/Azure/agentops/blob/main/docs/concepts.md) — bundles, datasets, evaluators, backends, configuration model
- [How It Works](https://github.com/Azure/agentops/blob/main/docs/how-it-works.md) — architecture, request flow, full schema reference
- [Bundles](https://github.com/Azure/agentops/blob/main/docs/bundles.md) — bundle authoring and evaluator configuration

### Tutorials

- [Model-direct evaluation](https://github.com/Azure/agentops/blob/main/docs/tutorial-model-direct.md)
- [Foundry agent evaluation](https://github.com/Azure/agentops/blob/main/docs/tutorial-basic-foundry-agent.md)
- [RAG evaluation](https://github.com/Azure/agentops/blob/main/docs/tutorial-rag.md)
- [HTTP-deployed agent evaluation](https://github.com/Azure/agentops/blob/main/docs/tutorial-http-agent.md)
- [Conversational agent evaluation](https://github.com/Azure/agentops/blob/main/docs/tutorial-conversational-agent.md)
- [Agent workflow evaluation](https://github.com/Azure/agentops/blob/main/docs/tutorial-agent-workflow.md)
- [Baseline comparison](https://github.com/Azure/agentops/blob/main/docs/tutorial-baseline-comparison.md)

### Operations

- [CI/CD with GitHub Actions](https://github.com/Azure/agentops/blob/main/docs/ci-github-actions.md)
- [Copilot skills installation](https://github.com/Azure/agentops/blob/main/docs/tutorial-copilot-skills.md)
- [Release process](https://github.com/Azure/agentops/blob/main/docs/release-process.md)
- [Built-in evaluator reference](https://github.com/Azure/agentops/blob/main/docs/foundry-evaluation-sdk-built-in-evaluators.md)

## Contributing

See [CONTRIBUTING.md](https://github.com/Azure/agentops/blob/main/CONTRIBUTING.md) for architecture rules, testing expectations, and contribution workflow.
