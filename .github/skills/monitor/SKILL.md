---
name: monitor
description: Guidance on monitoring evaluation quality over time. Trigger when users say "monitoring", "dashboards", "alerts", "monitor setup", "quality over time", "trending", "track scores", "evaluation health", "monitor evals". Monitor commands are planned but not yet implemented. Install agentops-toolkit via pip.
---

# AgentOps Monitor

> **Prerequisite:** Install the AgentOps CLI with `pip install agentops-toolkit`.

## Purpose
Provide honest guidance on monitoring capabilities. The `agentops monitor show` and `agentops monitor configure` commands are **planned but not yet implemented**. This skill redirects to multi-run comparison as the current way to track quality over time.

## When to Use
- User asks how to monitor evaluation quality over time.
- User asks about dashboards, alerts, or quality trending.
- User wants to track score changes across multiple runs.
- User asks about `agentops monitor setup`, `show`, or `configure`.

## Before You Start

Before running any commands, check the workspace for required configuration:

1. **Is AgentOps initialized?** Look for `.agentops/` directory. If missing, run `agentops init` first.
2. **Is the endpoint configured?** Search for `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` in `.env`, `.env.local`, environment variables, or run.yaml (`project_endpoint_env`). If not found, **ask the user** for the Foundry project endpoint URL.
3. **Does a run.yaml exist?** Check `.agentops/run.yaml`. If it needs a model deployment name or agent ID that is not filled in, **ask the user** for those specific values.

Only ask about values you cannot find in the codebase or environment files.

## Current Status

### Planned Commands (Not Yet Available)

```bash
agentops monitor show        # View dashboards — PLANNED, not implemented
agentops monitor configure   # Configure alerts — PLANNED, not implemented
```

**Do not present these commands as available.** If the user asks to run them, explain that they are planned for a future release.

## What Works Today

### Multi-run trending (the current "dashboard")

Run evaluations periodically (daily, per-PR, per-release) and compare:

```bash
# Run eval (produces timestamped results in .agentops/results/)
agentops eval run -f html

# Compare the last 3 runs to see the trend
agentops eval compare --runs <oldest>,<middle>,<latest> -f html
```

The HTML comparison report is a self-contained dashboard showing:
- **Status per run**: `PASS (100% · 5/5)` or `FAIL (80% · 4/5)`
- **Score direction**: ↑ improved / ↓ regressed / → unchanged vs baseline
- **Best scores**: green-highlighted cells across all compared runs
- **Row pass rates**: `(4/5)` per evaluator — shows consistency

### CI-based monitoring

Use GitHub Actions to run evaluations on every PR:

```bash
agentops workflow generate
```

This creates `.github/workflows/agentops-eval.yml` which:
- Runs `agentops eval run` on every pull request
- Gates the PR on threshold pass/fail (exit code 0 vs 2)
- Posts `report.md` as a PR comment
- Uploads artifacts for historical reference

This is the current alternative to real-time monitoring — every PR gets an evaluation checkpoint.

### Manual trending workflow

1. Run the same config regularly:
   ```bash
   agentops eval run -c .agentops/run.yaml -f html
   ```
2. Each run creates a timestamped folder in `.agentops/results/`
3. Compare any N runs:
   ```bash
   agentops eval compare --runs 2026-03-01_100000,2026-03-15_100000,latest -f html
   ```
4. The Evaluators table with ↑↓ arrows shows the quality trend

### Exit codes as health signal

| Exit Code | Meaning | Health |
|---|---|---|
| `0` | All thresholds passed | Healthy |
| `2` | One or more thresholds failed | Degraded |
| `1` | Runtime or configuration error | Error |

In CI, exit code 2 blocks the PR — this is your automated quality gate.

## Guardrails
- Do not present `agentops monitor show` or `agentops monitor configure` as available — they are planned.
- Do not suggest external monitoring tools unless the user asks.
- The HTML comparison report IS the current dashboard — it's self-contained, no server needed.
- Redirect to `agentops eval compare` for trending needs.

## Examples
- "How do I monitor eval quality over time?"
  → Run evals periodically and compare: `agentops eval compare --runs <old>,<mid>,<new> -f html`. The trend arrows show quality direction across runs.
- "Can I set up alerts for quality drops?"
  → `agentops monitor configure` is planned. Today, use CI gating: `agentops workflow generate` creates a GitHub Actions workflow that fails the PR when thresholds are missed (exit code 2).
- "I want a dashboard for my evaluations"
  → `agentops monitor show` is planned. Today, generate HTML reports: `agentops eval compare --runs <run1>,<run2>,<run3> -f html` — it produces a self-contained visual dashboard.
- "How do I track if my model is getting worse?"
  → Run the same eval config weekly, then compare: `agentops eval compare --runs <week1>,<week2>,<week3> -f html`. Status + ↑↓ arrows show the trend.

## Learn More
- Documentation: https://github.com/Azure/agentops
- PyPI: https://pypi.org/project/agentops-toolkit/
