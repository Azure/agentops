# Tutorial: Baseline Comparison

Goal: compare two evaluation runs to detect **regressions** — metric drops, threshold flips, and item-level failures — using `agentops eval compare`.

## Prerequisites

- Python 3.11+
- `pip install agentops-toolkit`
- At least two completed evaluation runs (each with `results.json`)

## Part 1: Understand the comparison model

When you compare two runs, AgentOps produces:

| Output | Description |
|---|---|
| `comparison.json` | Machine-readable: metric deltas, threshold flips, item-level changes |
| `comparison.md` | Human-readable: summary tables with REGRESSION / IMPROVEMENT labels |

Exit codes follow the same CI-friendly contract:

| Code | Meaning |
|---|---|
| `0` | No regressions detected |
| `2` | Regressions detected |
| `1` | Error (bad run ID, missing files, etc.) |

### What counts as a regression?

- A **metric decreased** (for higher-is-better metrics like `SimilarityEvaluator`)
- A **metric increased** (for lower-is-better metrics like `avg_latency_seconds`)
- A **threshold flipped** from PASS to FAIL
- An **item that was passing** now fails

AgentOps automatically detects metric polarity from your threshold criteria:
- `>=` or `>` → higher is better (decrease = regression)
- `<=` or `<` → lower is better (increase = regression)

## Part 2: Run two evaluations

### 1) Run the baseline

```bash
agentops eval run -c .agentops/run.yaml
```

This creates a timestamped output directory under `.agentops/results/`, for example:
```
.agentops/results/2026-03-19_100000/
├── results.json
├── report.md
└── backend_metrics.json
```

The latest run is also copied to `.agentops/results/latest/`.

### 2) Make a change

Change something — update the model deployment, modify the dataset, adjust prompts, or upgrade the agent.

### 3) Run again

```bash
agentops eval run -c .agentops/run.yaml
```

Now you have two runs:
```
.agentops/results/2026-03-19_100000/   ← baseline
.agentops/results/2026-03-19_140000/   ← current
```

## Part 3: Compare runs

### Using timestamped folder names

```bash
agentops eval compare --runs 2026-03-19_100000,2026-03-19_140000
```

### Using the `latest` keyword

```bash
agentops eval compare --runs 2026-03-19_100000,latest
```

### Using custom output directory

```bash
agentops eval compare --runs 2026-03-19_100000,latest -o .agentops/results/my-comparison
```

### Run identifiers

The `--runs` flag accepts:
- **Timestamped folder names** (e.g. `2026-03-19_100000`) — resolved under `.agentops/results/`
- **`latest`** — always points to the most recent run
- **Relative or absolute paths** to a `results.json` file or a directory containing one

## Part 4: Read the comparison report

### comparison.md

```markdown
# AgentOps Comparison Report

## Overview
- Baseline run: **2026-03-19_100000** (model_direct_baseline)
- Current run: **2026-03-19_140000** (model_direct_baseline)
- Verdict: **REGRESSIONS DETECTED**

## Metric Deltas
| Metric | Baseline | Current | Delta | Delta % | Direction |
|---|---:|---:|---:|---:|---|
| SimilarityEvaluator | 5.000000 | 1.800000 | -3.200000 | -64.00% | regressed |
| avg_latency_seconds | 5.686304 | 4.585443 | -1.100861 | -19.36% | improved |

## Threshold Changes
| Evaluator | Criteria | Baseline | Current | Change |
|---|---|---|---|---|
| SimilarityEvaluator | >= | PASS | FAIL | REGRESSION |

## Item Changes
| Row | Baseline | Current | Change |
|---:|---|---|---|
| 1 | PASS | FAIL | REGRESSION |
| 2 | PASS | FAIL | REGRESSION |
```

### comparison.json

The structured output contains:

```json
{
  "version": 1,
  "baseline": { "run_id": "...", "bundle_name": "...", "started_at": "..." },
  "current":  { "run_id": "...", "bundle_name": "...", "started_at": "..." },
  "metric_deltas": [
    { "name": "SimilarityEvaluator", "baseline_value": 5.0, "current_value": 1.8,
      "delta": -3.2, "delta_percent": -64.0, "direction": "regressed" }
  ],
  "threshold_deltas": [
    { "evaluator": "SimilarityEvaluator", "criteria": ">=",
      "baseline_passed": true, "current_passed": false, "flipped": true }
  ],
  "item_deltas": [ ... ],
  "summary": {
    "metrics_improved": 1, "metrics_regressed": 1,
    "thresholds_flipped_pass_to_fail": 1,
    "items_newly_failing": 2,
    "has_regressions": true
  }
}
```

## Part 5: Use in CI

Add comparison to your GitHub Actions workflow:

```yaml
- name: Run evaluation
  run: agentops eval run -o .agentops/results/current

- name: Compare with baseline
  run: agentops eval compare --runs baseline,current
  # Exit code 2 fails the job when regressions are detected
```

### Baseline management strategies

| Strategy | How | Best for |
|---|---|---|
| **Timestamped** | Keep `.agentops/results/<timestamp>/` in version control or artifacts | Full audit trail |
| **Named** | Use `-o .agentops/results/baseline` to write to a fixed name | Simple CI gating |
| **Latest** | Compare against `latest` from the previous pipeline run | Rolling comparisons |

## Part 6: Investigate regressions

When regressions are detected:

1. **Check `comparison.md`** — which metrics regressed? Which thresholds flipped?
2. **Check item-level changes** — is the regression broad (many rows) or concentrated (one row)?
3. **Diff your changes** — what changed between the two runs? (dataset, model, config)
4. **Rerun with controlled changes** — isolate the variable that caused the regression

### Common causes

| Symptom | Likely cause |
|---|---|
| All items regressed | Model deployment changed or broken |
| Some items regressed | Dataset rows changed or new edge cases |
| Latency regressed only | Infrastructure or throttling issue |
| Threshold flipped but metrics stable | Threshold was borderline; consider adjusting |

## Next steps

- [Model-Direct Evaluation Tutorial](tutorial-model-direct.md)
- [RAG Evaluation Tutorial](tutorial-rag.md)
- [Foundry Agent Evaluation Tutorial](tutorial-basic-foundry-agent.md)
- [CI/CD Integration Guide](ci-github-actions.md)
