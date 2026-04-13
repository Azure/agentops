---
name: agentops-regression
description: Investigate evaluation regressions — compare runs, analyze per-row scores, identify root causes. Trigger when users report score drops, threshold failures, or quality degradation between runs. Common phrases include "regression", "score dropped", "threshold failed", "compare runs", "why worse", "which rows failed", "debug evaluation", "quality degradation". Install agentops-toolkit via pip.
---

# AgentOps Regression

Investigate score drops and threshold failures between evaluation runs.

## Step 1 — Find the runs

Check `.agentops/results/` for timestamped directories. Need at least two runs (baseline + current). If missing, delegate to `/agentops-eval`.

## Step 2 — Compare

```bash
agentops eval compare --runs <baseline>,<current>
```

Look for `↓` indicators and negative deltas. A regression is confirmed when:
- A run's status flips from PASS → FAIL
- A previously-passing row now fails

Minor numeric shifts within passing thresholds are NOT regressions.

## Step 3 — Find failing rows

Open `results.json` for both runs. Compare `row_metrics`:
- Rows with the largest negative delta
- Rows that went pass → fail
- Clusters of failures in one evaluator

## Step 4 — Diagnose root cause

| Cause | What to check |
|---|---|
| Model update | Deployment version changed |
| Prompt drift | System prompt or instructions modified |
| Data drift | New/different dataset rows |
| Tool schema change | Tool definitions modified |
| Context quality | RAG retriever returning different passages |
| Threshold tightened | Bundle threshold values changed |

## Step 5 — Fix and verify

| Finding | Action |
|---|---|
| Model regression | Pin model version or switch deployment |
| Prompt issue | Revert or iterate on prompt |
| Bad test rows | Fix dataset, re-run |
| Threshold too strict | Adjust in bundle (`/agentops-config`) |
| Retriever degraded | Debug retrieval pipeline separately |

After fixing:
```bash
agentops eval run
agentops eval compare --runs <baseline>,latest
```

## Rules

- Work with actual scores — never guess root causes.
- Do not modify `results.json` — it is immutable.
- Do not adjust thresholds to hide real regressions.
- Delegate execution to `/agentops-eval`, config to `/agentops-config`.
