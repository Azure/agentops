---
name: agentops-report
description: Interpret evaluation reports, explain indicators, and regenerate reports. Trigger when users ask to understand results, explain scores, or regenerate a report. Common phrases include "report", "interpret results", "what does this mean", "explain scores", "report generate", "results.json", "pass rate", "threshold". Install agentops-toolkit via pip.
---

# AgentOps Report

## Purpose

Help users understand evaluation results, explain report indicators, and regenerate reports from existing `results.json` files.

## When to Use

- User asks what an evaluation result means.
- User wants to regenerate a report after manual edits.
- User needs to compare report sections between runs.
- User asks about pass rates, thresholds, or score meanings.

## Before You Start

1. **AgentOps installed?** Check if `agentops` CLI is available. If not: `pip install agentops-toolkit`.
2. **Workspace exists?** Check for `.agentops/`. If missing: `agentops init`.
3. **Results exist?** Check for `.agentops/results/latest/results.json`. If missing, run `/agentops-eval` first.
4. **Foundry endpoint configured?** Search for `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` in environment variables, `.env`, `.env.local`. If not found, ask the user for the endpoint URL and instruct them to set it.

## Commands

| Command | Purpose |
|---|---|
| `agentops report generate --in <results.json> [--out <report.md>]` | Regenerate report from results |

## Report Indicators

| Symbol | Meaning |
|---|---|
| `●` (green) | Score meets or exceeds threshold |
| `●` (red) | Score below threshold |
| `↑` | Score improved vs. baseline |
| `↓` | Score regressed vs. baseline |
| `—` | No baseline available |

## Key Metrics

| Metric | Description |
|---|---|
| `run_pass` | `true` if all thresholds passed |
| `threshold_pass_rate` | Fraction of thresholds met |
| `items_pass_rate` | Fraction of rows passing all evaluators |
| per-evaluator avg | Mean score across all rows for one evaluator |
| per-evaluator stddev | Standard deviation (high = inconsistent) |

## Report Sections

### Single Run (`report.md`)
- **Summary**: overall pass/fail, item counts
- **Threshold Results**: per-evaluator threshold vs. actual score
- **Row Details**: per-row scores for each evaluator

### Comparison (`agentops eval compare`)
- **Side-by-side**: baseline vs. current scores
- **Delta**: absolute change per evaluator
- **Direction**: ↑ improved, ↓ regressed, — unchanged

## Steps

### Interpreting results
1. Open `.agentops/results/latest/report.md`.
2. Check the summary — is `run_pass: true`?
3. If false, find which thresholds failed (red dots).
4. Look at per-row scores to identify weak rows.
5. For AI evaluators (coherence, groundedness), scores are 1–5.
6. For content safety evaluators, lower is better (0 = safe).

### Regenerating a report
```bash
agentops report generate --in .agentops/results/latest/results.json
```

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success and all thresholds passed |
| `2` | Success but threshold(s) failed |
| `1` | Runtime or configuration error |

## Guardrails

- Use actual scores from `results.json` — never guess or estimate.
- Do not run evaluations — delegate to `/agentops-eval`.
- Do not modify `results.json` — it is an immutable run artifact.
- If the user needs different thresholds, delegate to `/agentops-config` to update the bundle.
