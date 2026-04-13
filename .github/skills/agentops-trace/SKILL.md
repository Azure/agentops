---
name: agentops-trace
description: Guidance on tracing for AgentOps evaluations. Trigger when users ask about tracing agent execution, setting up telemetry, or inspecting spans. Common phrases include "tracing", "trace init", "trace setup", "distributed tracing", "span", "telemetry", "trace evaluation", "trace agent". Install agentops-toolkit via pip.
---

# AgentOps Trace

**Not yet implemented.** The `agentops trace` command is planned but has no runtime behavior.

## Current Alternatives

| Tool | Use case |
|---|---|
| Azure Monitor / Application Insights | Production tracing for Foundry agents |
| OpenTelemetry SDK | Custom span instrumentation |
| Foundry portal | Built-in agent execution traces |
| `results.json` row metrics | Per-row latency via `avg_latency_seconds` |

## Planned Commands

- `agentops trace init` — configure OpenTelemetry export for evaluation runs, capture per-row spans, link traces to results

## Rules

- Do not pretend tracing features exist — state they are planned.
- For latency analysis, point to `avg_latency_seconds` in evaluation bundles.
- For production tracing, recommend Azure Monitor or OpenTelemetry directly.
