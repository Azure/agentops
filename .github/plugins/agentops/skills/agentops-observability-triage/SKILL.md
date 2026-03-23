---
name: agentops-observability-triage
description: Guide users on observability and triage workflows for AgentOps evaluations. Trigger when users ask about tracing, monitoring, dashboards, alerts, run health, production triage, or understanding evaluation outputs. Common phrases include "set up tracing", "monitor evals", "create alerts", "triage failed evaluations", "observability", "understand eval results", "what do these scores mean". Install agentops-toolkit via pip. Tracing and monitoring commands are planned for a future release.
---

# AgentOps Observability Triage

> **Prerequisite:** Install the AgentOps CLI with `pip install agentops-toolkit`.

## Purpose
Provide practical observability guidance using current reporting artifacts. Frame tracing/monitoring as planned future features while showing what's available today — including HTML reports with visual indicators and N-run comparison dashboards.

## When to Use
- User asks how to monitor ongoing evaluation quality.
- User asks for tracing, dashboards, or alerts.
- User needs triage steps after an unexpected evaluation outcome.
- User asks what the evaluation scores and indicators mean.

## Available Commands

```bash
agentops eval run [-c <config>] [-f md|html|all]                    # Generate results
agentops report [--in <results.json>] [-f md|html|all]              # Regenerate report
agentops eval compare --runs <id1>,<id2>[,...] [-f md|html|all]      # Compare N runs
```

## Planned Commands (Not Yet Available)

```bash
agentops trace init             # Initialize tracing
agentops monitor setup          # Set up monitoring
agentops monitor dashboard      # Configure dashboards
agentops monitor alert          # Configure alerts
```

## Triage Workflow

### Quick triage (single run)
1. `agentops eval run -f html` — run and generate HTML report
2. Open `report.html` — check overall status, threshold checks, item verdicts
3. If FAIL: look at which evaluator thresholds were missed

### Deep triage (comparison)
1. `agentops eval compare --runs <baseline>,latest -f html`
2. Open `comparison.html` — visual dashboard with:
   - **Status**: `PASS (100% · 5/5)` or `FAIL (60% · 3/5)` — immediate pass rate
   - **Evaluators**: ● dots (Met/Missed), ↑↓ arrows (direction vs baseline), (n/n) row rates
   - **Row Details**: per-row scores showing exactly which questions failed
3. Check if regression is real (threshold flip) or noise (minor shift within threshold)

### Multi-run trending
1. Run the same config multiple times over days/weeks
2. Compare all: `agentops eval compare --runs <oldest>,<middle>,<latest> -f html`
3. The Evaluators table shows trend direction for each metric across all runs

### Model selection
1. Create run configs for each candidate model (same dataset + bundle)
2. Run each: `agentops eval run -c <model-config> -f html`
3. Compare: `agentops eval compare --runs <model1>,<model2>,<model3> -f html`
4. Report auto-detects "Model Comparison" and shows side-by-side with best highlighting
5. Pick the model that meets thresholds at the best quality/latency/cost ratio

## Understanding Report Indicators

### HTML visual indicators
- **● green dot** — evaluator score Met the threshold target
- **● red dot** — evaluator score Missed the threshold target
- **↑ green arrow** — score improved vs baseline
- **↓ red arrow** — score regressed vs baseline
- **→ gray arrow** — unchanged
- **Green highlighted cell** — best score across all compared runs
- **(3/5)** — 3 out of 5 rows met this evaluator's threshold
- **Muted gray text** — informational metric (no threshold, e.g., samples_evaluated)

### Status
- `PASS (100% · 5/5)` — all 5 rows met all thresholds
- `FAIL (80% · 4/5)` — 4 of 5 rows passed, 1 failed
- PASS = all row thresholds met · FAIL = one or more rows missed

### Verdict
- **NO REGRESSIONS** — no run's status flipped PASS→FAIL vs baseline
- **REGRESSIONS DETECTED** — at least one run has newly-failing rows or status flipped

### Comparison types (auto-detected)
- **Model Comparison** — comparing different models on same dataset
- **Agent Comparison** — comparing different agents on same dataset
- **Dataset Coverage** — testing same model/agent on different datasets
- **General** — multiple parameters vary

## Report Formats
- `-f md` — Markdown (default), good for PRs and CI logs
- `-f html` — professional visual dashboard, best for analysis
- `-f all` — generates both

## Guardrails
- Do not present tracing or monitoring commands as available today.
- Do not imply real-time dashboards or alerts currently exist.
- Always pivot to concrete available outputs when asked about unimplemented features.
- The HTML report IS the current dashboard — it's self-contained, no server needed.

## Examples
- "How do I set up tracing?"
  → Tracing (`agentops trace init`) is planned. For now, use `-f html` to generate visual reports with per-row score breakdowns.
- "Can I monitor eval quality over time?"
  → Run evals periodically and compare: `agentops eval compare --runs <old>,<mid>,<new> -f html`. The trend arrows show quality direction.
- "What does FAIL (80% · 4/5) mean?"
  → 4 of 5 dataset rows met all evaluator thresholds, 1 row missed. Check Row Details to see which row and which evaluator scored below target.
- "What do the colored dots mean?"
  → Green ● = score met the threshold target, Red ● = missed. In the Evaluators table, this is the aggregate score; in Row Details, it's per-row.

## Learn More
- Documentation: https://github.com/Azure/agentops
- PyPI: https://pypi.org/project/agentops-toolkit/
