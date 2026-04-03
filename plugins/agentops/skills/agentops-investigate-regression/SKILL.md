---
name: agentops-investigate-regression
description: Help users investigate evaluation regressions in AgentOps by comparing runs, analyzing row-level scores, and identifying root causes. Trigger when users say "regression", "score dropped", "threshold failed", "compare runs", "why did this eval get worse", "which rows failed", "debug evaluation", "quality degradation". Install agentops-toolkit via pip. Commands are agentops eval run, agentops eval compare, and agentops report.
---

# AgentOps Investigate Regression

> **Prerequisite:** Install the AgentOps CLI with `pip install agentops-toolkit`.

## Purpose
Guide users through regression investigation using N-run comparison, row-level score analysis, and structured root cause identification.

## When to Use
- User reports lower scores versus previous runs.
- User reports new threshold failures (PASS → FAIL).
- User asks to compare current and prior evaluation outcomes.
- CI gating changed from pass to fail and root cause is unclear.
- User asks which specific rows or questions are failing.

## Available Commands

```bash
agentops eval run [-c <config>] [-f md|html|all]                    # Generate fresh results
agentops report [-f md|html|all]                                     # Regenerate report
agentops eval compare --runs <id1>,<id2>[,...] [-f md|html|all]      # Compare N runs
```

Run identifiers for `--runs` can be:
- Timestamped folder names (e.g. `2026-03-01_100000`)
- The keyword `latest`
- Absolute or relative paths to a `results.json` or a run directory

## Investigation Workflow

1. **Reproduce:** `agentops eval run -f html` to get fresh results with visual report.
2. **Compare:** `agentops eval compare --runs <baseline>,latest -f html`
3. **Check the verdict:** NO REGRESSIONS vs REGRESSIONS DETECTED
4. **Read run config:** Check Status row — `FAIL (60% · 3/5)` tells you exactly how many rows failed.
5. **Read Evaluators table:**
   - ● green dot = Met threshold, ● red dot = Missed
   - ↑ improved / ↓ regressed vs baseline
   - `(3/5)` = row pass rate for this evaluator
6. **Drill into Row Details:** Find exactly which rows scored below threshold and why.
7. **Act:** Fix the identified issues (prompt tuning, dataset quality, model selection).

## Understanding the Report

### What REGRESSIONS DETECTED means
A regression is detected ONLY when:
- A run's overall status flips from **PASS to FAIL** vs baseline
- A previously-passing **row** now fails

A minor numeric decrease (e.g., latency 4.84s → 6.00s) that stays within the threshold (≤ 10s) is **NOT** a regression. The verdict focuses on threshold-breaking changes, not noise.

### Comparison types
The report auto-detects what's being compared:
- **Model Comparison** — same dataset, different models → full row-level analysis valid
- **Agent Comparison** — same dataset, different agents → full row-level analysis valid
- **Dataset Coverage** — different datasets → row details skipped (rows aren't comparable)
- **General** — multiple things vary

### Evaluators table
Each cell shows: `● score ↑ delta (n/n rows)`
- **● dot** = Met (green) or Missed (red) vs the absolute threshold target
- **↑↓ delta** = direction vs baseline run (improved/regressed/unchanged)
- **(n/n)** = how many rows met the threshold out of total
- **Green highlight** = best score across all runs
- Metrics without thresholds (like `samples_evaluated`) show as plain informational numbers

### Row Details table
Each cell shows per-evaluator scores: `● SimilarityEvaluator: 2`
- Green ● = this row met the threshold
- Red ● = this row missed — **this is why the run failed**

### Status
`PASS (100% · 5/5)` = all rows met all thresholds
`FAIL (60% · 3/5)` = 3 of 5 rows passed, 2 failed → the specific rows that failed explain the FAIL

## Root Cause Checklist
When you find regressions:

1. **Which rows failed?** → Check Row Details for red ● dots
2. **Which evaluator failed?** → The evaluator with red dots tells you what's weak
3. **Is it the model?** → Compare same dataset across models to isolate
4. **Is it the dataset?** → Some questions are inherently harder (real-time, ambiguous)
5. **Is it the agent instructions?** → Compare agent versions on same dataset
6. **Is it random variance?** → Run the same config 2-3 times and compare

## Guardrails
- Do not infer causality from correlation alone.
- Separate observations (data from artifacts) from hypotheses (plausible causes).
- Keep remediation advice tied to reproducible checks.
- When comparing runs with different datasets, do NOT analyze row-level changes — they're different questions.

## Examples
- "My eval went from PASS to FAIL after changing model"
  → `agentops eval compare --runs <old>,<new> -f html`. Check Evaluators for ↓ regressed metrics and Row Details for newly-failing rows.
- "Which specific questions are failing?"
  → Open the HTML report, scroll to Row Details — each row shows the actual score per evaluator with ● Met/Missed.
- "Is gpt-4.1 better than gpt-5.1 for my use case?"
  → Create two run.yaml files (same dataset, different model), run both, compare. The Evaluators table with row pass rates tells you which model handles your questions better.
- "Why is CI failing now?"
  → `agentops eval compare --runs <last_pass>,latest -f html`. The Status line shows `FAIL (80% · 4/5)` — one row regressed. Row Details shows which.

## Working with VS Code AI Toolkit

When investigating regressions, AI Toolkit provides complementary interactive analysis:

### Drill into failing rows interactively
1. Run `agentops eval compare --runs <baseline>,latest -f html` to identify which rows regressed.
2. Export the dataset with scores to JSONL/CSV.
3. Open in AI Toolkit's Data Wrangler for interactive filtering, sorting, and visualization of score distributions.

### Re-evaluate in Agent Builder
If a regression appears prompt-related:
1. Open AI Toolkit's Agent Builder with the same model and prompt.
2. Test the failing rows individually in the interactive Playground.
3. Iterate on prompt changes, then re-export and re-run with `agentops eval run`.

### Dataset compatibility
AI Toolkit uses `query`/`ground_truth` fields; AgentOps maps them via dataset config:
```yaml
format:
  input_field: query
  expected_field: ground_truth
```
This lets the same JSONL file work in both tools without conversion.

## Learn More
- Documentation: https://github.com/Azure/agentops
- PyPI: https://pypi.org/project/agentops-toolkit/
