---
name: agentops-trace
description: Guidance on tracing for AgentOps evaluations. Trigger when users ask about tracing agent execution, setting up telemetry, or inspecting spans. Common phrases include "tracing", "trace init", "trace setup", "distributed tracing", "span", "telemetry", "trace evaluation", "trace agent". Install agentops-toolkit via pip.
---

# AgentOps Trace

## Purpose

Provide guidance on tracing agent execution. The `agentops trace` command is **planned but not yet implemented**.

## Before You Start

1. **AgentOps installed?** Check if `agentops` CLI is available. If not: `pip install agentops-toolkit`.
2. **Workspace exists?** Check for `.agentops/`. If missing: `agentops init`.
3. **Foundry endpoint configured?** Search for `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` in environment variables, `.env`, `.env.local`. If not found, ask the user for the endpoint URL and instruct them to set it.

## Status

🚧 **Not yet implemented.** The CLI stub exists but has no runtime behavior.

## Current Alternatives

Until `agentops trace` is available, use these tools directly:

| Tool | Use case |
|---|---|
| Azure Monitor / Application Insights | Production tracing for Foundry agents |
| OpenTelemetry SDK | Custom span instrumentation |
| Foundry portal | Built-in agent execution traces |
| `results.json` row metrics | Per-row latency via `avg_latency_seconds` |

## What Will Be Available

When implemented, `agentops trace init` will:
- Configure OpenTelemetry export for AgentOps evaluation runs
- Capture per-row agent execution spans
- Link traces to evaluation results for debugging

## Guardrails

- Do not pretend tracing features exist — clearly state they are planned.
- For latency analysis, point users to `avg_latency_seconds` in evaluation bundles.
- For production tracing, recommend Azure Monitor or OpenTelemetry directly.
