---
name: trace
description: Guidance on tracing for AgentOps evaluations. Trigger when users say "tracing", "trace init", "trace setup", "distributed tracing", "span", "telemetry", "trace evaluation", "trace agent". The trace command is planned but not yet implemented. Install agentops-toolkit via pip.
---

# AgentOps Trace

> **Prerequisite:** Install the AgentOps CLI with `pip install agentops-toolkit`.

## Purpose
Provide honest guidance on tracing capabilities. The `agentops trace init` command is **planned but not yet implemented**. This skill redirects to what works today for inspecting evaluation execution details.

## When to Use
- User asks how to set up tracing for evaluations.
- User asks about distributed tracing, spans, or telemetry.
- User wants to understand what happened during an evaluation run.
- User asks about `agentops trace init`.

## Before You Start

Before running any commands, check the workspace for required configuration:

1. **Is AgentOps initialized?** Look for `.agentops/` directory. If missing, run `agentops init` first.
2. **Is the endpoint configured?** Search for `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` in `.env`, `.env.local`, environment variables, or run.yaml (`project_endpoint_env`). If not found, **ask the user** for the Foundry project endpoint URL.
3. **Does a run.yaml exist?** Check `.agentops/run.yaml`. If it needs a model deployment name or agent ID that is not filled in, **ask the user** for those specific values.

Only ask about values you cannot find in the codebase or environment files.

## Current Status

### Planned Commands (Not Yet Available)

```bash
agentops trace init     # Initialize tracing — PLANNED, not implemented
```

**Do not present this command as available.** If the user asks to run it, explain that it is planned for a future release.

## What Works Today

Although dedicated tracing is not yet available, you can inspect evaluation execution in detail using existing artifacts:

### Per-row score breakdown
```bash
agentops eval run -f html
```
Open `report.html` — the Row Details section shows per-row, per-evaluator scores with ● Met/Missed indicators. This is the closest equivalent to a trace of what happened during evaluation.

### Artifacts produced per run
Every evaluation run writes to `.agentops/results/latest/`:

| File | What it shows |
|---|---|
| `results.json` | Full evaluation results — per-row scores, thresholds, pass/fail |
| `report.md` / `report.html` | Human-readable summary with visual indicators |
| `backend_metrics.json` | Raw backend scores per row (evaluator outputs) |
| `backend.stdout.log` | Backend stdout capture — model/agent responses |
| `backend.stderr.log` | Backend stderr capture — errors, warnings, SDK logs |
| `cloud_evaluation.json` | Foundry portal link (cloud eval only) |

### Inspecting a specific row
Read `results.json` and look at `item_evaluations` — each entry contains the input, response, expected output, and all evaluator scores for that row.

### Comparing execution across runs
```bash
agentops eval compare --runs <baseline>,latest -f html
```
The comparison report shows how each row's scores changed between runs — useful for tracing when a specific behavior changed.

## Guardrails
- Do not present `agentops trace init` as available — it is planned.
- Do not suggest third-party tracing integrations unless the user asks.
- Redirect to concrete artifacts (`results.json`, `report.html`, logs) for current tracing needs.

## Examples
- "How do I set up tracing?"
  → `agentops trace init` is planned. Today, use `agentops eval run -f html` and inspect `report.html` for per-row score breakdowns, or read `backend.stdout.log` for raw model responses.
- "I want to see what the agent did for row 3"
  → Open `results.json`, find the entry in `item_evaluations` with that row's input. It shows the agent's response and all evaluator scores.
- "Can I trace agent tool calls?"
  → Run with the `agent_workflow_baseline` bundle — the evaluators score tool selection and tool input accuracy per row. Check Row Details in the HTML report.

## Learn More
- Documentation: https://github.com/Azure/agentops
- PyPI: https://pypi.org/project/agentops-toolkit/
