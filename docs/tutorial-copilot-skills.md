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

- VS Code with the GitHub Copilot Chat extension
- The AgentOps CLI installed: `pip install agentops-toolkit`

The skills reference CLI commands, so Copilot's guidance only works if the CLI is actually available in your environment.

## Installation

### Option 1: Install from GitHub (recommended)

The skills are distributed from the `Azure/agentops` repository, following the same pattern used by other Azure Copilot skills (like the ones in `microsoft/azure-skills`).

In VS Code:

1. Open **Copilot Chat**.
2. Use the skill install flow and point to this repository:
   - **Source:** `Azure/agentops`
   - **Skill path:** `plugins/agentops/skills/`
3. Select the skills you want to install.

Once installed, the skills appear in `~/.agents/skills/` and a lock file (`~/.agents/.skill-lock.json`) tracks where they came from. Skills are available across all workspaces.

### Option 2: Manual copy

If you prefer to manage skills manually:

**macOS / Linux:**
```bash
git clone https://github.com/Azure/agentops.git /tmp/agentops
cp -r /tmp/agentops/plugins/agentops/skills/* ~/.agents/skills/
rm -rf /tmp/agentops
```

**Windows (PowerShell):**
```powershell
git clone https://github.com/Azure/agentops.git $env:TEMP\agentops
Copy-Item -Recurse "$env:TEMP\agentops\plugins\agentops\skills\*" "$env:USERPROFILE\.agents\skills\"
Remove-Item -Recurse -Force "$env:TEMP\agentops"
```

### Option 3: Project-scoped installation

If you want the skills available only within a specific repository (useful for teams with different tool versions), copy them into the project:

```bash
mkdir -p plugins/agentops/skills
cp -r <agentops-repo>/plugins/agentops/skills/* plugins/agentops/skills/
```

This way the skills travel with the repo and every contributor gets them automatically.

## Verifying the installation

Check that the skill directories exist:

```bash
ls ~/.agents/skills/
# Expected: agentops-run-evals/  agentops-investigate-regression/  agentops-observability-triage/
```

Each directory should contain a `SKILL.md` file with YAML frontmatter (the `name` and `description` fields that Copilot uses for skill matching).

## Using the skills

You do not need to invoke skills explicitly. Copilot matches your question to the right skill based on trigger phrases in the skill description. Just ask naturally.

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

Pull the latest version from the repository and re-copy:

```bash
git clone https://github.com/Azure/agentops.git /tmp/agentops
cp -r /tmp/agentops/plugins/agentops/skills/* ~/.agents/skills/
rm -rf /tmp/agentops
```

If you installed via the VS Code skill install flow, the lock file tracks version hashes and will prompt for updates when the source repo changes.

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
