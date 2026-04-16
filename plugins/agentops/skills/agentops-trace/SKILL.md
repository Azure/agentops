---
name: agentops-trace
description: Guidance on tracing for AgentOps evaluations. Trigger when users ask about tracing agent execution, setting up telemetry, or inspecting spans. Common phrases include "tracing", "trace setup", "distributed tracing", "span", "telemetry", "trace evaluation", "trace agent", "OTLP", "Jaeger", "Azure Monitor traces". Install agentops-toolkit via pip.
---

# AgentOps Trace

## Purpose

Provide guidance on the built-in OpenTelemetry (OTel) tracing that is automatically emitted during every `agentops eval run`.

## Before You Start

1. **AgentOps installed?** Check if `agentops` CLI is available. If not: `pip install agentops-toolkit`.
2. **Workspace exists?** Check for `.agentops/`. If missing: `agentops init`.

## How It Works

Tracing is **opt-in** and controlled by a single environment variable:

| Variable | Required? | Default | Description |
|---|---|---|---|
| `AGENTOPS_OTLP_ENDPOINT` | No | *(unset — tracing disabled)* | Base URL of the OTLP/HTTP collector. AgentOps appends `/v1/traces`. |

When set, every `agentops eval run` emits a full trace tree:

```
RUN <bundle_name>                         (root span — the whole evaluation)
├── eval_item 0                           (one per dataset row)
│   ├── evaluator builtin.similarity      (one per evaluator score)
│   └── evaluator builtin.coherence
├── eval_item 1
│   └── ...
└── (final attributes: pass_rate, items_total, items_passed)
```

When unset, tracing is fully disabled — zero overhead, no OTel packages imported.

## Quick Start — Local Jaeger

```bash
# 1. Start Jaeger (OTLP on 4318, UI on 16686)
docker run -d --name jaeger -p 16686:16686 -p 4318:4318 jaegertracing/jaeger:latest

# 2. Install OTel packages
pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http

# 3. Enable tracing
export AGENTOPS_OTLP_ENDPOINT=http://localhost:4318   # Linux/macOS
$env:AGENTOPS_OTLP_ENDPOINT = "http://localhost:4318"  # PowerShell

# 4. Run evaluation
agentops eval run

# 5. View traces at http://localhost:16686 → service "agentops"
```

## Semantic Conventions

Spans follow three OTel semantic convention layers:

- **CICD** (`cicd.pipeline.*`) — models the evaluation run as a CI/CD pipeline
- **GenAI** (`gen_ai.*`) — follows the OTel GenAI spec for agent/model invocation
- **AgentOps** (`agentops.eval.*`) — evaluation-specific: scores, thresholds, pass/fail

Full attribute reference: `docs/telemetry.md`.

## Production Backends

Any OTLP-compatible backend works:

| Backend | Setup |
|---|---|
| Azure Monitor / App Insights | Use OTel Collector with Azure Monitor exporter, or native OTLP ingestion |
| Grafana Tempo | Point `AGENTOPS_OTLP_ENDPOINT` at the Tempo OTLP receiver |
| Datadog | Use the Datadog OTLP ingest endpoint |

## Agent Execution Tracing

Agent execution tracing (tool calls, LLM calls, retrieval steps) is **already provided by Foundry and the Agent Framework** — AgentOps does not reimplement it. The skill can help users **verify that tracing is properly configured** in their agent code.

### What to check

When a user asks about agent tracing, inspect their codebase for:

1. **Foundry agents** — Tracing is automatic. Verify the agent is deployed and visible in the Foundry portal → Agent → Traces tab.
2. **Agent Framework SDK** — Check that `AIProjectClient` or `AgentsClient` is configured with the correct project endpoint. Traces flow to Azure Monitor automatically.
3. **Custom agents (HTTP/local)** — Look for OTel instrumentation in the agent code:
   - `opentelemetry` imports and `TracerProvider` setup
   - `APPLICATIONINSIGHTS_CONNECTION_STRING` or `OTEL_EXPORTER_OTLP_ENDPOINT` env vars
   - If missing, guide the user to add OTel SDK setup to their agent entrypoint

### Verification steps

1. Check for tracing env vars: `APPLICATIONINSIGHTS_CONNECTION_STRING`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`
2. Search agent code for `TracerProvider`, `trace.get_tracer`, or `configure_azure_monitor`
3. If nothing is found, suggest adding Azure Monitor OpenTelemetry: `pip install azure-monitor-opentelemetry` + `configure_azure_monitor()`
4. Point user to Foundry portal or Azure Monitor to confirm traces are flowing

## Rules

- Tracing is built-in and emitted automatically when `AGENTOPS_OTLP_ENDPOINT` is set.
- Do not suggest `agentops trace init` — that command has been removed.
- For latency analysis without OTel, point users to `avg_latency_seconds` in evaluation bundles.
- Agent execution tracing is handled by Foundry/Agent Framework natively — the skill helps verify it is configured, not reimplement it.
