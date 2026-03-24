# Tutorial: Installing AgentOps Copilot Skills

This tutorial explains how to install the AgentOps Copilot skills, what each skill does, and how to verify they are working correctly — including using AgentOps itself to evaluate skill quality.

## Why install skills?

When you ask GitHub Copilot a question about running evaluations or investigating a regression, it does its best with general knowledge. But Copilot does not know the specifics of AgentOps — what commands exist, what flags they accept, what outputs they produce, and which commands are still planned but not implemented.

Skills close that gap. Each skill is a structured document that tells Copilot *exactly* how to help with a particular workflow. After installation, Copilot stops guessing and starts giving accurate, specific guidance grounded in the actual CLI behavior.

The difference is noticeable. Without the skill, Copilot might suggest `agentops monitor dashboard` (which is planned but not implemented). With the skill, Copilot will tell you honestly that monitoring is planned, and pivot to what you *can* do today — inspect `results.json` and `report.md`.

## The three AgentOps skills

| Skill | Purpose | When it activates |
|---|---|---|
| `agentops-run-evals` | Walks through the full evaluation workflow from workspace setup to report interpretation. Covers `init`, `eval run`, `report`, and `eval compare`. | You ask about running evaluations, finding configs, or understanding results. |
| `agentops-investigate-regression` | Guides regression investigation using the comparison command. Structures findings into observations vs hypotheses and ends with actionable next steps. | You mention score drops, threshold failures, comparing runs, or quality degradation. |
| `agentops-observability-triage` | Provides honest status on what observability features exist today versus what is planned. Redirects to available artifact-based triage instead of pretending monitoring commands exist. | You ask about tracing, monitoring, dashboards, or alerts. |

The skills are complementary. In a typical workflow, `run-evals` helps you get started, `investigate-regression` helps when something goes wrong, and `observability-triage` sets expectations about what is and is not available yet.

## Prerequisites

- VS Code with the [GitHub Copilot Chat](https://marketplace.visualstudio.com/items?itemName=GitHub.copilot-chat) extension

The skills reference AgentOps CLI commands, so Copilot's guidance only works if you also have the CLI installed in your project:

```bash
pip install agentops-toolkit
```

## Installation

### Install in VS Code (recommended)

Open the Command Palette (`Ctrl+Shift+P` / `Cmd+Shift+P`) and run **Chat: Install Plugin From Source**. When prompted, enter:

```
https://github.com/Azure/agentops
```

Done. VS Code installs all three skills automatically, keeps them up to date, and makes them available across all your workspaces.

> **Command not found?** Agent plugins are in preview. Add `"chat.plugins.enabled": true` to your VS Code `settings.json` and reload.

### Other environments

> **Two different "Copilot CLI" tools:** The old `gh copilot suggest` / `gh copilot explain` extension runs in the terminal and does **not** support skill plugins. The **new standalone `copilot` CLI** (the agentic coding assistant) does — it uses `copilot plugin install`. If you run `copilot --version` and see a version number, you have the new one.

**GitHub Copilot CLI (standalone `copilot` command):**

```bash
# Register the agentops plugin marketplace once (per machine)
copilot plugin marketplace add Azure/agentops

# Then install the plugin
copilot plugin install agentops@Azure/agentops
```

The plugin stays in sync automatically. To update: `copilot plugin update agentops@Azure/agentops`

**Claude Code** and VS Code without the plugin both load skills from `~/.agents/skills/`. **Project-scoped installs** load from `.github/skills/` inside the repository.

**Global (Claude Code or manual VS Code):**

```bash
# macOS / Linux
git clone --depth 1 https://github.com/Azure/agentops.git /tmp/agentops
cp -r /tmp/agentops/skills/* ~/.agents/skills/
rm -rf /tmp/agentops
```

```powershell
# Windows
git clone --depth 1 https://github.com/Azure/agentops.git $env:TEMP\agentops
Copy-Item -Recurse "$env:TEMP\agentops\skills\*" "$env:USERPROFILE\.agents\skills\"
Remove-Item -Recurse -Force "$env:TEMP\agentops"
```

**Project-scoped (team repo, pinned version):**

```bash
# macOS / Linux
git clone --depth 1 https://github.com/Azure/agentops.git /tmp/agentops
cp -r /tmp/agentops/skills/* .github/skills/
rm -rf /tmp/agentops
```

Skills in `.github/skills/` are picked up automatically by VS Code Copilot Chat for everyone who opens the repository — no install step required for team members.

## Verifying the installation

**Plugin install (VS Code):** Open the Extensions view (`Ctrl+Shift+X`) and look for **AgentOps Evaluation Toolkit** under **Agent Plugins – Installed**. Or open Copilot Chat and type `@agentops` — the plugin appears in the agent picker.

**Copilot CLI:** Run `copilot plugin list` and confirm `agentops` appears in the output.

**Manual install:** Confirm the skill directories are present:

```bash
ls ~/.agents/skills/agentops-*
```

## Using the skills

### With the VS Code plugin → `@agentops`

Type `@agentops` in Copilot Chat to target the plugin directly. The plugin routes your question to the right skill automatically:

```
@agentops how do I run my first evaluation?
@agentops my scores dropped after a model update, what should I do?
@agentops is agentops monitor available yet?
```

### Manual / project-scoped / Claude Code → just ask

Skills activate automatically based on the topic of your question. No `@` prefix needed. Just open Copilot Chat or Claude Code and ask:

```
How do I start running evaluations with AgentOps?
My evaluation scores dropped. What should I do?
Can I set up monitoring alerts?
```

> **Tip:** You don't need to mention "AgentOps." Phrases like "run evaluations", "score dropped", or "compare runs" are enough to trigger the right skill.

### Example: starting an evaluation

> "How do I start running evaluations with AgentOps?"

With the `agentops-run-evals` skill installed, Copilot will respond with the correct sequence: `agentops init` to scaffold the workspace, then `agentops eval run` to execute, then point you to `.agentops/results/latest/` for the outputs. It will not suggest commands that do not exist.

### Example: investigating a regression

> "My evaluation scores dropped after I switched model deployments. What should I do?"

With `agentops-investigate-regression`, Copilot will suggest running `agentops eval compare --runs <baseline>,latest`, then walk you through interpreting the comparison report — which thresholds flipped, which metrics of the model or agent degraded, and whether the issue is broad or concentrated in specific rows. It separates factual observations from hypotheses and ends with concrete next steps.

### Example: asking about monitoring

> "Can I set up monitoring alerts for my evaluation quality?"

With `agentops-observability-triage`, Copilot will tell you directly that `agentops monitor setup`, `dashboard`, and `alert` commands are planned but not yet implemented. Instead of giving wrong instructions, it pivots to what works today: running `agentops eval run` and `agentops report` to generate artifacts, then inspecting `results.json` and `report.md` for triage.

## Updating skills

**Plugin install (VS Code):** VS Code checks for updates automatically every 24 hours. To trigger manually: Command Palette → **Extensions: Check for Extension Updates**.

**Copilot CLI:** `copilot plugin update agentops@Azure/agentops`

**Manual install:** Re-run the same `git clone` + `cp` commands to overwrite with the latest version.

## Evaluating skill quality with AgentOps

This is an advanced use case, but a natural one: you can use AgentOps to evaluate the quality of its own Copilot skills.

The idea is to create a dataset where each row contains a user question paired with the skill content as context, along with an expected answer that reflects correct guidance. Then SimilarityEvaluator measures whether the model (acting as Copilot) produces responses that align with those expectations.

For example, one row might be:
- **Input:** *"You are a Copilot assistant with this skill: [run-evals SKILL.md]. User asks: Is agentops eval compare available?"*
- **Expected:** *"Yes, agentops eval compare --runs is available. You can compare two runs by providing run IDs separated by a comma."*

Run it the same way as any other evaluation:

```bash
agentops eval run -c .agentops/run-skills.yaml
```

When we tested this against our three skills, the SimilarityEvaluator scored **4.2 out of 5** — the model consistently produced guidance aligned with what the skills intend.

This approach is valuable when you are actively iterating on skill content. Before and after editing a skill, run the evaluation and compare:

```bash
agentops eval compare --runs skill-baseline,latest
```

If the score drops, the skill change may have introduced inaccurate or confusing guidance. This is the same regression-detection pattern used for agents and models, applied to the skills themselves.

## Next steps

- [Baseline Comparison Tutorial](tutorial-baseline-comparison.md) — compare runs and detect regressions
- [Model-Direct Evaluation Tutorial](tutorial-model-direct.md) — evaluate a model deployment
- [RAG Evaluation Tutorial](tutorial-rag.md) — evaluate retrieval-augmented responses
- [CI/CD Integration Guide](ci-github-actions.md) — automate evaluation in pipelines
