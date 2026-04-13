# AgentOps Skills for GitHub Copilot

Copilot agent skills for running standardized evaluation workflows with
[AgentOps Toolkit](https://github.com/Azure/agentops) and Microsoft Foundry agents.

## Skills

| Skill | What it does |
|---|---|
| **Workspace Setup** | Initialize an `.agentops/` workspace, create configs, manage bundles and datasets |
| **Run Evals** | Execute evaluations, multi-model benchmarks, N-run comparisons, and generate reports |
| **Investigate Regression** | Compare runs, analyze row-level scores, and identify root causes of regressions |
| **Observability & Triage** | Set up OTLP tracing, interpret evaluation outputs, triage failed runs |
| **Browse & Inspect** | List and inspect evaluation runs, view per-row scores, browse run history |
| **Dataset Management** | Validate, describe, and import datasets for evaluation workflows |

## Prerequisites

Install the AgentOps CLI in your project's virtual environment:

```bash
pip install agentops-toolkit
```

## Installation

Install from the
[VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=PUBLISHER_ID.agentops-skills)
or search **"AgentOps Skills"** in the VS Code Extensions view.

A **pre-release** channel is available for early access to new skills and updates —
enable it from the extension's Marketplace page or the Extensions view.

## Usage

Open **Copilot Chat** in VS Code and describe what you want to do.
The skills are invoked automatically when your request matches their domain:

```
> Initialize an agentops workspace for my project
> Run the default evaluation
> Compare run abc123 with run def456
> Which rows failed the groundedness threshold?
```

## Links

- [AgentOps Toolkit](https://github.com/Azure/agentops) — CLI and documentation
- [Tutorial: Basic Foundry Agent](https://github.com/Azure/agentops/blob/main/docs/tutorial-basic-foundry-agent.md)
- [How It Works](https://github.com/Azure/agentops/blob/main/docs/how-it-works.md)
