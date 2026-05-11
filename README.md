<h1 align="center">AgentOps Toolkit</h1>

<p align="center">
AgentOps CLI for evaluation, observability, and operational workflows for Microsoft Foundry Agents and Models.
</p>

<p align="center">
<a href="https://pypi.org/project/agentops-toolkit/"><img alt="PyPI" src="https://img.shields.io/pypi/v/agentops-toolkit.svg?label=PyPI&color=blue"/></a>
<a href="https://marketplace.visualstudio.com/items?itemName=AgentOpsToolkit.agentops-toolkit"><img alt="VS Code Extension" src="https://img.shields.io/badge/VS%20Code-Extension-007ACC.svg?logo=visualstudiocode"/></a>
<a href="https://github.com/Azure/agentops/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Azure/agentops/actions/workflows/ci.yml/badge.svg?branch=develop"/></a>
<a href="https://github.com/Azure/agentops/actions/workflows/release.yml"><img alt="Release" src="https://github.com/Azure/agentops/actions/workflows/release.yml/badge.svg"/></a>
<a href="https://github.com/Azure/agentops"><img alt="Status: Preview" src="https://img.shields.io/badge/Status-Preview-orange.svg"/></a>
<br/>
<a href="https://www.python.org/downloads/"><img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB.svg"/></a>
<a href="https://typer.tiangolo.com/"><img alt="CLI: Typer" src="https://img.shields.io/badge/CLI-Typer-5A67D8.svg"/></a>
<a href="https://learn.microsoft.com/azure/ai-foundry/"><img alt="Built on Microsoft Foundry" src="https://img.shields.io/badge/Built%20on-Microsoft%20Foundry-0078D4.svg"/></a>
<a href="https://github.com/Azure/agentops/blob/main/LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-green.svg"/></a>
</p>

## Overview

AgentOps Toolkit is a CLI built on Microsoft Foundry that standardizes evaluation workflows for AI agents and models, helping teams run and automate evaluations with consistent inputs and outputs.

The project enables:

- Consistent local and CI execution of agent evaluations
- Automatic evaluator selection based on dataset shape (RAG, agent-with-tools, model quality)
- Stable machine-readable outputs for automation
- Human-readable reports for PR reviews and quality gates
- Baseline comparison to detect regressions across runs

Core outputs:

- `results.json` (machine-readable)
- `report.md` (human-readable)

Exit code contract:

- `0` execution succeeded and all thresholds passed
- `2` execution succeeded but one or more thresholds failed
- `1` runtime or configuration error

## Quickstart

### 1) Install

```bash
python -m venv .venv
python -m pip install -U pip
python -m pip install "agentops-toolkit[foundry] @ git+https://github.com/Azure/agentops.git@develop"
```

### 2) Bootstrap

```bash
agentops init
```

This writes a single `agentops.yaml` at the project root and a tiny seed dataset at `.agentops/data/smoke.jsonl`. Edit `agentops.yaml` to point at your agent.

### 3) Configure your agent

Pick one of these forms for the `agent:` field — AgentOps classifies the target automatically:

```yaml
agent: "my-rag:3"                          # Foundry prompt agent (name:version)
agent: "https://...services.ai.azure.com/.../agents/<id>"  # Foundry hosted endpoint
agent: "https://api.example.com/chat"      # any HTTP/JSON agent (ACA, AKS, custom)
agent: "model:gpt-4o"                       # raw Foundry model deployment
```

Evaluators are inferred from the dataset shape (rows with `context` → RAG evaluators, rows with `tool_calls`/`tool_definitions` → agent-workflow evaluators). The full minimal config is:

```yaml
version: 1
agent: "my-rag:3"
dataset: .agentops/data/smoke.jsonl
```

### 4) Run

```bash
export AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="https://<resource>.services.ai.azure.com/api/projects/<project>"
agentops eval run
```

Outputs land in `.agentops/results/latest/`:

- `results.json` — machine-readable (versioned, stable schema)
- `report.md` — human-readable, PR-friendly

To compare against a previous run, pass `--baseline`:

```bash
agentops eval run --baseline .agentops/results/baseline/results.json
```

The report grows a `Comparison vs Baseline` section with per-metric deltas.

---

## Commands

| Command | Description |
|---|---|
| `agentops --version` | Show installed version |
| `agentops init` | Bootstrap `agentops.yaml` and a seed dataset |
| `agentops eval run [--config PATH] [--baseline PATH]` | Run an evaluation |
| `agentops report generate [--in FILE]` | Regenerate `report.md` from `results.json` |
| `agentops workflow generate` | Generate GitHub Actions workflow |
| `agentops skills install [--platform <p>]` | Install coding agent skills (Copilot, Claude) |
| `agentops mcp serve` | Start the AgentOps MCP server (stdio). Requires `pip install "agentops-toolkit[mcp] @ git+https://github.com/Azure/agentops.git@develop"`. |
| `agentops agent analyze` | Run the watchdog over your run history. Requires `pip install "agentops-toolkit[agent] @ git+https://github.com/Azure/agentops.git@develop"`. |
| `agentops agent serve` | Start the watchdog as a FastAPI Copilot Extension. Requires `pip install "agentops-toolkit[agent] @ git+https://github.com/Azure/agentops.git@develop"`. |

## Documentation

- [Quickstart tutorial](https://github.com/Azure/agentops/blob/main/docs/tutorial-quickstart.md) — bootstrap a workspace and run one evaluation.
- [End-to-end tutorial](https://github.com/Azure/agentops/blob/main/docs/tutorial-end-to-end.md) — full do-it-yourself tour: Foundry hosted agent, baseline comparison, GitFlow CI/CD, watchdog.
- [Copilot skills tutorial](https://github.com/Azure/agentops/blob/main/docs/tutorial-copilot-skills.md) — use AgentOps skills to have Copilot configure, run, explain, and wire evals into CI.
- Per-scenario tutorials:
  - [Foundry hosted agent](https://github.com/Azure/agentops/blob/main/docs/tutorial-basic-foundry-agent.md)
  - [Model-direct](https://github.com/Azure/agentops/blob/main/docs/tutorial-model-direct.md)
  - [RAG](https://github.com/Azure/agentops/blob/main/docs/tutorial-rag.md)
  - [Conversational agent](https://github.com/Azure/agentops/blob/main/docs/tutorial-conversational-agent.md)
  - [Agent with tool calling](https://github.com/Azure/agentops/blob/main/docs/tutorial-agent-workflow.md)
  - [HTTP-deployed agent](https://github.com/Azure/agentops/blob/main/docs/tutorial-http-agent.md)
- [Baseline comparison](https://github.com/Azure/agentops/blob/main/docs/tutorial-baseline-comparison.md)
- [Watchdog agent](https://github.com/Azure/agentops/blob/main/docs/tutorial-agent-watchdog.md)
- [CI/CD with GitHub Actions](https://github.com/Azure/agentops/blob/main/docs/ci-github-actions.md)
- [Built-in evaluator reference](https://github.com/Azure/agentops/blob/main/docs/foundry-evaluation-sdk-built-in-evaluators.md)
- [Release process](https://github.com/Azure/agentops/blob/main/docs/release-process.md)

## Contributing

See [CONTRIBUTING.md](https://github.com/Azure/agentops/blob/main/CONTRIBUTING.md) for architecture rules, testing expectations, and contribution workflow.
