---
name: agentops-regression
description: Investigate evaluation regressions — compare runs, analyze per-row scores, identify root causes. Trigger when users report score drops, threshold failures, or quality degradation between runs. Common phrases include "regression", "score dropped", "threshold failed", "compare runs", "why worse", "which rows failed", "debug evaluation", "quality degradation". Install agentops-toolkit via pip.
---

# AgentOps Regression

## Purpose

Investigate evaluation score drops and threshold failures. Compare runs side-by-side, identify which rows regressed, and guide root-cause analysis.

## When to Use

- Exit code `2` — thresholds failed.
- Scores dropped between two runs.
- User asks "why did this eval get worse" or "which rows failed".

## Before You Start

1. **AgentOps installed?** Check if `agentops` CLI is available. If not: `pip install agentops-toolkit`.
2. **Workspace exists?** Check for `.agentops/`. If missing: `agentops init`.
3. **Foundry endpoint configured?** Search for `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` in environment variables, `.env`, `.env.local`. If not found, ask the user for the endpoint URL and instruct them to set it.
4. **Two runs available?** Need a baseline and a current run. Check `.agentops/results/` for timestamped directories.
5. **Results exist?** Each run must have `results.json`.

## Steps

### Step 1 — Identify the regression

```bash
agentops eval compare --runs <baseline>,<current>
```

Review the comparison output for ↓ indicators and delta values.

### Step 2 — Analyze per-row scores

Open `results.json` for both runs. Compare `row_metrics` to find rows where scores dropped. Look for:
- Rows with the largest negative delta
- Rows that went from pass → fail
- Clusters of failures in specific evaluators

### Step 3 — Check what changed

Common regression causes:
| Cause | What to check |
|---|---|
| Model update | Deployment version, model name change |
| Prompt drift | System prompt or instructions changed |
| Data drift | New dataset rows, different distribution |
| Tool schema change | Tool definitions modified |
| Context quality | RAG retriever returning different passages |
| Threshold tightened | Bundle threshold values changed |

### Step 4 — Act on findings

| Finding | Action |
|---|---|
| Model regression | Pin model version or switch deployment |
| Prompt issue | Revert or iterate on prompt changes |
| Bad test rows | Fix dataset and re-run |
| Threshold too strict | Adjust thresholds in bundle (use `/agentops-config`) |
| Retriever degraded | Debug retrieval pipeline separately |

### Step 5 — Verify fix

Re-run the evaluation after the fix:
```bash
agentops eval run
agentops eval compare --runs <baseline>,latest
```

## Guardrails

- Work with actual scores — never guess what caused a regression.
- Do not modify `results.json` — it is immutable.
- Do not adjust thresholds to hide real regressions.
- Delegate execution to `/agentops-eval` and config changes to `/agentops-config`.
