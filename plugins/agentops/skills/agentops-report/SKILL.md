---
name: agentops-report
description: Read, regenerate, and explain AgentOps evaluation reports. Trigger on "show report", "explain scores", "regenerate report", "what do these metrics mean". Operates on results.json and report.md produced by `agentops eval run`.
---

# AgentOps Report

Help the user understand a finished AgentOps run.

## Step 0 — Locate the run

Latest run: `.agentops/results/latest/`. Each run produces:

- `results.json` — machine-readable metrics, per-row scores, thresholds.
- `report.md` — human-readable summary suitable for PR comments.
- `cloud_evaluation.json` (only when `publish:` was set) — deep-link to
  the Foundry Evaluations panel. `mode: classic` for `publish: foundry`,
  `mode: cloud` for `publish: foundry_cloud` (preview, server-side run
  via the OpenAI Evals API).

## Step 1 — Regenerate report.md if needed

```bash
agentops report generate                   # uses .agentops/results/latest/results.json
agentops report generate --in <results.json> --out <report.md>
```

`report generate` always reads the flat 1.0 results schema and emits
Markdown. There is no HTML format.

## Step 2 — Explain the metrics

Common metrics and their meaning:

| Metric | Range | Higher is better? | Notes |
|---|---|---|---|
| `similarity` | 1-5 | yes | LLM-judged similarity to `expected`. |
| `coherence` | 1-5 | yes | Answer is internally consistent. |
| `fluency` | 1-5 | yes | Natural language quality. |
| `groundedness` | 1-5 | yes | Answer is supported by `context` (RAG). |
| `relevance` | 1-5 | yes | Answer is on-topic for `input`. |
| `f1_score` | 0-1 | yes | Token overlap with `expected`. |
| `tool_call_accuracy` | 0-1 | yes | Predicted tool calls match `tool_calls`. |
| `intent_resolution` | 0-1 | yes | User intent was resolved. |
| `task_completion` | 0-1 | yes | Multi-step task finished. |
| `avg_latency_seconds` | seconds | no | Wall-clock latency per row. |

Pass/fail rows are derived from `thresholds:` in `agentops.yaml`. The
exit code of the original run reflects the gate:

- `0` → all thresholds passed
- `2` → one or more thresholds failed
- `1` → runtime error

## Step 3 — Help the user act on results

- For low scores on a specific metric, point at the lowest-scoring rows
  in `results.json` (`row_metrics[]` and `item_evaluations[]`) and
  suggest concrete prompt or retrieval changes.
- For latency regressions, look at `run_metrics.avg_latency_seconds` and
  per-row latency.
- To compare a new run against a previous one, re-run with
  `agentops eval run --baseline <previous-results.json>` and explain the
  generated **Comparison vs Baseline** section.

## Guardrails

- Never invent metric values. If a metric is absent, say so.
- Do not edit `results.json` by hand — re-run the eval.
