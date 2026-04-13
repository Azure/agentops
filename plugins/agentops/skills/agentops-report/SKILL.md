---
name: agentops-report
description: Interpret evaluation reports, explain indicators, and regenerate reports. Trigger when users ask to understand results, explain scores, or regenerate a report. Common phrases include "report", "interpret results", "what does this mean", "explain scores", "report generate", "results.json", "pass rate", "threshold". Install agentops-toolkit via pip.
---

# AgentOps Report

Interpret evaluation results and regenerate reports from `results.json`.

## Step 1 — Find the results

Check `.agentops/results/latest/results.json`. If missing, delegate to `/agentops-eval`.

## Step 2 — Interpret the report

Open `.agentops/results/latest/report.md` (or `report.html`).

1. Check `run_pass` — `true` means all thresholds passed.
2. If `false`, find which evaluators failed (red `●` dots).
3. Check per-row scores to identify weak rows.

**Score scales:**
- AI evaluators (coherence, groundedness, fluency, similarity): 1–5 (higher = better)
- Content safety evaluators: 0–7 (lower = safer, 0 = safe)
- `avg_latency_seconds`: seconds (lower = better)

**Report indicators:**

| Symbol | Meaning |
|---|---|
| `●` green | Meets or exceeds threshold |
| `●` red | Below threshold |
| `↑` | Improved vs. baseline |
| `↓` | Regressed vs. baseline |

**Key metrics:**

| Metric | Meaning |
|---|---|
| `run_pass` | All thresholds passed? |
| `threshold_pass_rate` | Fraction of thresholds met |
| `items_pass_rate` | Fraction of rows passing all evaluators |
| per-evaluator avg | Mean score across rows |
| per-evaluator stddev | High stddev = inconsistent quality |

## Step 3 — Regenerate (if needed)

```bash
agentops report generate --in .agentops/results/latest/results.json
```

Add `-f html` for HTML format, or `-f all` for both.

## Exit Codes

- `0` — all thresholds passed
- `2` — threshold(s) failed
- `1` — runtime error

## Rules

- Use actual scores from `results.json` — never guess.
- Do not modify `results.json` — it is immutable.
- Do not run evaluations — delegate to `/agentops-eval`.
- For threshold changes, delegate to `/agentops-config`.
