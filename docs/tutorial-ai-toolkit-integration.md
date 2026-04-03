# AI Toolkit Integration Guide

This guide explains how AgentOps and VS Code AI Toolkit work together across the agent evaluation lifecycle.

---

## Overview

**AI Toolkit** is a VS Code extension pack for interactive AI agent development — model discovery, prompt engineering, bulk runs, evaluation, and tracing. **AgentOps** is a CLI for automated, CI-friendly evaluation workflows with threshold gating.

They are complementary:

| Concern | AI Toolkit | AgentOps CLI |
|---|---|---|
| **When** | Design-time, interactive | Run-time, automated (CI/CD) |
| **Evaluators** | Built-in (F1, similarity, coherence) + custom LLM/Python | Azure Evaluation SDK via Foundry cloud/local |
| **Datasets** | JSONL/CSV with `query`, `response`, `ground_truth` | JSONL with configurable field mapping |
| **Comparison** | Version-based side-by-side in UI | N-run CLI comparison with threshold gating |
| **Output** | In-editor UI + Data Wrangler | `results.json` + `report.md` / `report.html` |
| **Use case** | Rapid prototyping and iteration | CI pipeline gating and trending |

Both tools are built on the **Azure Evaluation SDK** — that shared foundation is the integration seam.

---

## Dataset Interoperability

### The field mapping solution

AI Toolkit uses `query` and `ground_truth` as field names. AgentOps uses `input` and `expected` by default. The dataset config's `input_field` and `expected_field` settings bridge this gap:

```yaml
# .agentops/datasets/smoke-aitoolkit.yaml
version: 1
name: smoke_aitoolkit
description: Dataset using AI Toolkit field naming conventions.
source:
  type: file
  path: ../data/smoke-aitoolkit.jsonl
format:
  type: jsonl
  input_field: query           # AI Toolkit's field name for prompts
  expected_field: ground_truth  # AI Toolkit's field name for expected answers
metadata:
  scenario: model_direct
  source_tool: ai-toolkit
```

This lets the same JSONL file work in both tools without conversion.

### Getting started

`agentops init` scaffolds workspace files, including an AI Toolkit-compatible dataset template at `.agentops/datasets/smoke-aitoolkit.yaml` with a matching JSONL data file at `.agentops/data/smoke-aitoolkit.jsonl`.

### Importing from AI Toolkit

1. In AI Toolkit's Agent Builder, switch to the **Evaluation** tab.
2. Run your dataset and review results.
3. **Export** the dataset as CSV or JSONL.
4. Place the exported file in `.agentops/data/`.
5. Create a dataset YAML config with `input_field: query` and `expected_field: ground_truth`.
6. Reference it from your `run.yaml`.

### Exporting to AI Toolkit

1. After `agentops eval run`, the `results.json` contains per-row scores.
2. Open `results.json` in VS Code — use the Data Wrangler extension for interactive analysis.
3. To re-import into AI Toolkit, convert to JSONL with `query` and `ground_truth` columns.

---

## Recommended Workflow

### Phase 1: Prototype in AI Toolkit

Use AI Toolkit's interactive features for rapid iteration:

1. **Model selection** — browse the Model Catalog, compare models side-by-side in the Playground.
2. **Prompt engineering** — use Agent Builder to iterate on system prompts with real-time feedback.
3. **Bulk run** — generate synthetic data or import your dataset, run all rows against the model.
4. **Interactive evaluation** — use built-in evaluators (similarity, coherence, F1) to score results.
5. **Manual review** — use thumbs up/down to flag good and bad responses.

### Phase 2: Codify in AgentOps

Lock your validated configuration for CI:

1. **Export the dataset** from AI Toolkit as JSONL.
2. **Create dataset config** with AI Toolkit field mapping:
   ```yaml
   format:
     input_field: query
     expected_field: ground_truth
   ```
3. **Create a bundle** codifying the evaluators and thresholds you validated:
   ```yaml
   evaluators:
     - name: SimilarityEvaluator
       source: foundry
       enabled: true
   thresholds:
     - evaluator: SimilarityEvaluator
       criteria: ">="
       value: 3
   ```
4. **Create a run config** pointing to the bundle and dataset:
   ```yaml
   bundle:
     path: bundles/my_baseline.yaml
   dataset:
     path: datasets/my_aitoolkit_dataset.yaml
   backend:
     type: foundry
     target: model
     model: gpt-4.1
   ```

### Phase 3: Gate in CI

Add AgentOps to your CI pipeline:

```yaml
# GitHub Actions example
- name: Run evaluation
  run: agentops eval run -f html
  env:
    AZURE_AI_FOUNDRY_PROJECT_ENDPOINT: ${{ secrets.FOUNDRY_ENDPOINT }}

# Exit code 0 = all thresholds passed
# Exit code 2 = threshold failure → block merge
```

### Phase 4: Investigate with both tools

When CI fails:

1. `agentops eval compare --runs <last_pass>,latest -f html` — identify regressions.
2. Open the HTML report — check which rows and evaluators failed.
3. Go back to AI Toolkit's Agent Builder — test failing rows interactively.
4. Iterate on prompt changes, re-export, re-run.

---

## Tracing

AI Toolkit hosts a local OTLP trace collector on `http://localhost:4318` that visualizes GenAI traces following OpenTelemetry semantic conventions.

AgentOps tracing (`agentops trace init`) is planned for a future release. When available, it will emit OTLP-compatible traces that can be visualized in AI Toolkit's tracing UI — no additional configuration needed.

In the meantime, you can instrument your agent code directly using AI Toolkit's tracing setup (see [AI Toolkit tracing documentation](https://github.com/microsoft/vscode-ai-toolkit/blob/main/doc/tracing.md)) and use AgentOps' HTML reports for evaluation-specific observability.

---

## Custom Evaluators

AI Toolkit supports custom Python evaluators as simple functions:

```python
def measure_tone(query, response, **kwargs):
    return {"score": 4, "reason": "Professional tone maintained."}
```

AgentOps bundles currently reference evaluator classes from the Azure Evaluation SDK. Support for portable Python function evaluators (compatible with AI Toolkit's format) is planned.

---

## Summary

| Stage | Tool | What you do |
|---|---|---|
| Explore models | AI Toolkit | Browse catalog, compare in Playground |
| Engineer prompts | AI Toolkit | Iterate in Agent Builder |
| Prototype evaluation | AI Toolkit | Bulk run + built-in evaluators |
| Lock config for CI | AgentOps | Bundle YAML + dataset YAML + run YAML |
| Gate merges | AgentOps | `agentops eval run` in CI pipeline |
| Investigate regressions | Both | AgentOps comparison + AI Toolkit interactive drill-down |
| Monitor trends | AgentOps | `agentops eval compare` across periodic runs |
| Trace execution | AI Toolkit | OTLP collector + tracing UI |
