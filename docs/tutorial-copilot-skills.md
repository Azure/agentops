# Tutorial: Installing AgentOps Copilot Skills

Goal: install the AgentOps Copilot skills so GitHub Copilot can guide you through evaluation workflows, regression investigation, and triage — right from your editor.

## What are Copilot Skills?

Copilot skills are structured instructions (`SKILL.md` files) that teach GitHub Copilot how to use a specific tool correctly. When installed, Copilot automatically applies the right skill based on what you're asking about.

AgentOps publishes three skills:

| Skill | What it does |
|---|---|
| `agentops-run-evals` | Guides the full evaluation workflow — init, run, report, compare |
| `agentops-investigate-regression` | Helps investigate score drops and threshold failures using `eval compare` |
| `agentops-observability-triage` | Provides honest guidance on what's available today vs planned |

## Prerequisites

- VS Code with GitHub Copilot Chat extension
- `pip install agentops-toolkit` (the skills reference CLI commands)

## Installation

### Option 1: Install from GitHub (recommended)

The skills are distributed from the `Azure/agentops` repository. In VS Code:

1. Open the **Copilot Chat** panel.
2. Use the skill install flow and point to this repository:
   - **Source:** `Azure/agentops`
   - **Skill path:** `.github/plugins/agentops/skills/`
3. Select the skills you want to install.

Once installed, the skills appear in your `~/.agents/skills/` directory and are automatically available in all workspaces.

### Option 2: Manual copy

Copy the skill files directly to your user-level skills directory:

**macOS / Linux:**
```bash
git clone https://github.com/Azure/agentops.git /tmp/agentops
cp -r /tmp/agentops/.github/plugins/agentops/skills/* ~/.agents/skills/
rm -rf /tmp/agentops
```

**Windows (PowerShell):**
```powershell
git clone https://github.com/Azure/agentops.git $env:TEMP\agentops
Copy-Item -Recurse "$env:TEMP\agentops\.github\plugins\agentops\skills\*" "$env:USERPROFILE\.agents\skills\"
Remove-Item -Recurse -Force "$env:TEMP\agentops"
```

### Option 3: Add to your project

If you want the skills available only within a specific project, copy them into your repo:

```bash
mkdir -p .github/plugins/agentops/skills
cp -r <agentops-repo>/.github/plugins/agentops/skills/* .github/plugins/agentops/skills/
```

## Verify installation

After installation, check that the skills are present:

```bash
ls ~/.agents/skills/
# Should show: agentops-run-evals/  agentops-investigate-regression/  agentops-observability-triage/
```

## Usage

Once installed, just ask Copilot naturally. The skills trigger automatically based on your question:

### Running evaluations
> "How do I start running evaluations with AgentOps?"

Copilot will guide you through `agentops init` → `agentops eval run` → inspecting results.

### Investigating regressions
> "My evaluation scores dropped after I changed the model. What should I do?"

Copilot will suggest `agentops eval compare --runs <baseline>,latest` and walk you through interpreting the comparison report.

### Observability and triage
> "Can AgentOps set up monitoring alerts?"

Copilot will honestly state which commands are available today and which are planned, then guide you to artifact-based triage.

## Updating skills

To get the latest skill versions, re-install from the repository:

```bash
# Pull latest and re-copy
git clone https://github.com/Azure/agentops.git /tmp/agentops
cp -r /tmp/agentops/.github/plugins/agentops/skills/* ~/.agents/skills/
rm -rf /tmp/agentops
```

## Evaluating skill quality

You can use AgentOps itself to evaluate whether the skills produce correct guidance. Create a dataset where each row sends a user question with the skill content as context, and use SimilarityEvaluator to measure response alignment:

```bash
# Run skill quality evaluation
agentops eval run -c .agentops/run-skills.yaml

# Compare after skill changes
agentops eval compare --runs skill-baseline,latest
```

This lets you regression-test skill changes the same way you regression-test agent behavior.

## Next steps

- [Baseline Comparison Tutorial](tutorial-baseline-comparison.md)
- [Model-Direct Evaluation Tutorial](tutorial-model-direct.md)
- [RAG Evaluation Tutorial](tutorial-rag.md)
- [CI/CD Integration Guide](ci-github-actions.md)
