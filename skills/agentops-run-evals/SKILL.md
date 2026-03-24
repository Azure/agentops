---
name: agentops-run-evals
description: Guide users through running AgentOps evaluations end to end — single runs, multi-model benchmarks, and N-run comparisons. Trigger when users ask to initialize AgentOps, run an evaluation, compare runs, benchmark models, regenerate a report, or summarize results. Common phrases include "run eval", "start agentops", "compare models", "benchmark agents", "run.yaml", "report", "evaluation results", "which model is best". Install agentops-toolkit via pip. Commands are agentops init, agentops eval run, agentops eval compare, and agentops report.
---

# AgentOps Run Evaluations

> **Prerequisite:** Install the AgentOps CLI with `pip install agentops-toolkit`.

## Purpose
Guide users through the full AgentOps evaluation workflow — workspace setup, running evaluations, comparing N runs, benchmarking models/agents, and interpreting reports.

## When to Use
- User wants to start using AgentOps in a project.
- User asks how to run an evaluation with `run.yaml`.
- User wants to compare evaluation runs (2 or more).
- User wants to benchmark multiple models or agents on the same dataset.
- User asks how to regenerate reports or choose report format.
- User asks where evaluation outputs are written.

## Available Commands

```bash
pip install agentops-toolkit                          # Install the CLI
agentops init [--path <dir>] [--force]                # Scaffold workspace
agentops eval run [-c <run.yaml>] [-f md|html|all]    # Run evaluation
agentops report [--in <results.json>] [--out <report.md>] [-f md|html|all] # Regenerate report
agentops eval compare --runs <id1>,<id2>[,<id3>,...] [-f md|html|all]  # Compare N runs
```

### Key flags
- `-c / --config` — path to run.yaml (default: `.agentops/run.yaml`)
- `-f / --format` — report format: `md` (default), `html`, or `all`
- `-o / --output` — output directory override
- `--runs` — comma-separated run IDs (timestamps, `latest`, or paths)
- `--force` — overwrite existing starter files (`init` only)
- `--out` — output path for regenerated report (`report` only)

## Recommended Workflow

### Single evaluation
1. `agentops init` — scaffold `.agentops/` workspace
2. Edit `.agentops/run.yaml` with bundle, dataset, and backend settings
3. Set env: `$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://..."`
4. `agentops eval run` — run evaluation
5. Check `.agentops/results/latest/results.json` and `report.md`

### Multi-model benchmark
1. Create one run.yaml per model (same dataset + bundle, different `model:`):
   ```yaml
   # run-gpt51.yaml          # run-gpt41.yaml
   backend:                   backend:
     type: foundry              type: foundry
     target: model              target: model
     model: gpt-5.1             model: gpt-4.1
   ```
2. Run each: `agentops eval run -c .agentops/run-gpt51.yaml -f html`
3. Compare all: `agentops eval compare --runs <id1>,<id2>,<id3> -f html`
4. Open the HTML report — shows side-by-side scores, ● Met/Missed dots, ↑↓ direction arrows, row pass rates, and best-run highlighting

### Multi-agent comparison
Same approach — create one run.yaml per agent version:
```yaml
backend:
  type: foundry
  target: agent
  agent_id: my-agent:1    # or my-agent:2, my-agent:3
```

## Report Formats
- **`md`** (default) — Markdown, suitable for PRs and CI logs
- **`html`** — professional dashboard with visual indicators (● dots, ↑↓ arrows, color-coded badges, best highlighting)
- **`all`** — generates both

## Comparison Report Sections
The comparison report contains:

1. **Header** — verdict (NO REGRESSIONS / REGRESSIONS DETECTED), comparison type, varying parameter
2. **Run Config** — identity fields (Target, Model, Agent) + Status with pass rate (e.g., `PASS (100% · 5/5)`)
3. **Evaluators** — unified table showing per-evaluator:
   - Target threshold (e.g., `>= 3`)
   - Score per run with ● green/red dot (Met/Missed vs target)
   - Delta + ↑↓ direction vs baseline (improved/regressed/unchanged)
   - Row pass rate (e.g., `(4/5)`)
   - Best run highlighted with green background
   - Informational metrics (like `samples_evaluated`) shown as plain numbers
4. **Row Details** — per-row evaluator scores with ● dots (only when same dataset across runs)
5. **Fixed Parameters** — reference config info at bottom

## Comparison Types (auto-detected)
- **Model Comparison** — same dataset, model varies
- **Agent Comparison** — same dataset, agent varies
- **Dataset Coverage** — same agent/model, dataset varies (row details skipped)
- **General Comparison** — multiple things vary

## Regression Detection
A regression is detected ONLY when:
- A run's overall status flips from PASS to FAIL vs baseline
- A previously-passing row now fails

Minor numeric shifts within passing thresholds are NOT regressions.

## Evaluation Terminology
- **Met** / **Missed** — evaluator score vs absolute threshold target
- **improved** / **regressed** / **unchanged** — score direction vs baseline run
- **PASS** / **FAIL** — overall run status (PASS = all row thresholds met, FAIL = any row missed)

## Exit Codes
- `0` — succeeded and all thresholds passed (eval run) / no regressions (compare)
- `2` — thresholds failed (eval run) / regressions detected (compare)
- `1` — runtime or configuration error

## Expected Outputs
- `results.json` — machine-readable normalized results
- `report.md` / `report.html` — human-readable report (per format flag)
- `cloud_evaluation.json` — Foundry portal URL (cloud eval only)
- `comparison.json` + `comparison.md` / `comparison.html` — comparison outputs

## Environment Setup
```bash
# Required for Foundry backend
$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"

# Authentication
az login  # local development
# CI/CD: set AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_CLIENT_SECRET
```

## Guardrails
- Do not invent commands or flags beyond documented CLI behavior.
- Planned commands (`run list`, `bundle show`, `trace init`, `monitor`) are NOT implemented — state they are planned.
- The `--format` flag accepts only `md`, `html`, or `all`.
- When comparing runs with different datasets, row-level comparison is not meaningful — the report handles this automatically.

## Examples
- "Compare 3 models on the same dataset"
  → Create 3 run.yaml files (one per model), run each with `agentops eval run -c <config> -f html`, then `agentops eval compare --runs <id1>,<id2>,<id3> -f html`
- "Which model should I use?"
  → Run multi-model benchmark, check Evaluators table for best scores and latency, pick the model that meets thresholds at lowest cost
- "Why did my eval fail?"
  → Check the Row Details section — it shows per-row scores with ● Met/Missed so you can see exactly which rows scored below threshold

## Learn More
- Documentation: https://github.com/Azure/agentops
- PyPI: https://pypi.org/project/agentops-toolkit/
