# AgentOps Skills for GitHub Copilot

Copilot agent skills for running standardized evaluation workflows with
[AgentOps Toolkit](https://github.com/Azure/agentops) and Microsoft Foundry agents.

## Skills

| Skill | What it does |
|---|---|
| **agentops-eval** | Run evaluations end to end — single runs, multi-model benchmarks, and N-run comparisons |
| **agentops-config** | Infer the evaluation scenario from your codebase and generate `run.yaml` |
| **agentops-dataset** | Generate evaluation datasets (JSONL + YAML config) tailored to the project |
| **agentops-report** | Interpret evaluation reports, explain scores, and regenerate `report.md` |
| **agentops-regression** | Investigate regressions — compare runs, analyze per-row scores, identify root causes |
| **agentops-workflow** | Generate CI/CD pipelines (GitHub Actions) with PR gating and post-merge evaluation |
| **agentops-trace** | Set up OTLP tracing for evaluation runs |
| **agentops-monitor** | Guidance on monitoring evaluation quality over time |

## Installation

### VS Code Extension Marketplace

Install from the
[VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=AgentOpsToolkit.agentops-toolkit)
or search **"AgentOps Skills"** in the VS Code Extensions view.

### Agent Plugin Marketplace

The AgentOps plugin is also available through the cross-tool **Agent Plugin
Marketplace**, which works with VS Code Copilot, Copilot CLI, and Claude Code.

**VS Code** — add this to your `.vscode/settings.json`:

```json
{
  "chat.plugins.extraKnownMarketplaces": ["Azure/agentops"],
  "chat.plugins.enabledPlugins": ["agentops-toolkit"]
}
```

**Claude Code** — register the marketplace:

```bash
claude plugin marketplace add Azure/agentops
```

## Usage

Open **Copilot Chat** in VS Code and describe what you want to do.
Skills are invoked automatically when your request matches their domain.

### Configure and run an evaluation

```
> Set up an evaluation for my Foundry agent
> Generate a dataset for my RAG pipeline
> Run the default evaluation against my agent
```

### Benchmark and compare

```
> Benchmark gpt-4o vs gpt-4o-mini using the smoke dataset
> Compare the last two runs and tell me what changed
```

### Understand results

```
> Explain the scores in my latest report
> Which rows failed the groundedness threshold?
> Why did similarity drop between these two runs?
```

### Automate with CI/CD

```
> Generate a GitHub Actions workflow that gates PRs on evaluation quality
```

## Links

- [AgentOps Toolkit](https://github.com/Azure/agentops) — CLI and documentation
- [Tutorial: Basic Foundry Agent](https://github.com/Azure/agentops/blob/main/docs/tutorial-basic-foundry-agent.md)
- [How It Works](https://github.com/Azure/agentops/blob/main/docs/how-it-works.md)
