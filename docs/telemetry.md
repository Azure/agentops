# Telemetry — Observability for Evaluation Runs

This document explains how AgentOps uses **OpenTelemetry (OTel)** to give you visibility into every evaluation run. It is written for developers who have never used OTel before, so we start with the basics.

---

## Table of Contents

1. [Why Telemetry?](#why-telemetry)
2. [Concepts You Need to Know](#concepts-you-need-to-know)
3. [How AgentOps Uses OTel](#how-agentops-uses-otel)
4. [Quick Start — Local Setup with Jaeger](#quick-start--local-setup-with-jaeger)
5. [Environment Variables](#environment-variables)
6. [The Trace Tree](#the-trace-tree)
7. [Semantic Conventions (Attributes)](#semantic-conventions-attributes)
8. [Viewing Traces in Jaeger](#viewing-traces-in-jaeger)
9. [Sending Traces to Azure Monitor](#sending-traces-to-azure-monitor)
10. [FAQ](#faq)

---

## Why Telemetry?

When you run `agentops eval run`, a lot happens under the hood — dataset rows are loaded, agents are invoked, evaluators score responses, thresholds are checked. If something is slow, fails, or produces surprising scores, you want to **see exactly what happened**.

Telemetry records the full timeline of every evaluation run so you can:

- **Debug slow evaluations** — see which rows or evaluators took the longest.
- **Trace failures** — pinpoint exactly where an error occurred.
- **Monitor quality over time** — forward data to dashboards in Azure Monitor, Grafana, or Datadog.
- **Audit runs** — keep a detailed, machine-readable record of what happened.

---

## Concepts You Need to Know

If you are already familiar with OpenTelemetry, skip to [How AgentOps Uses OTel](#how-agentops-uses-otel).

### What Is OpenTelemetry?

OpenTelemetry (OTel) is an **open standard** for collecting diagnostic data from software. Think of it as a universal language that lets your application say: "I started doing X, it took 200ms, here are the details, and it succeeded." Any tool that speaks OTel (Jaeger, Azure Monitor, Datadog, Grafana Tempo, etc.) can receive and display that data.

### What Is a Trace?

A **trace** represents a single end-to-end operation. In AgentOps, one evaluation run = one trace. A trace is made up of **spans**.

### What Is a Span?

A **span** is a unit of work with a start time, end time, a name, and key-value attributes. Spans nest inside each other to form a tree. Example:

```
RUN conversational_agent_baseline          ← root span (the whole run)
├── eval_item 0                            ← child span (one dataset row)
│   ├── invoke_agent my-agent              ← grandchild (the agent call)
│   ├── evaluator builtin.similarity       ← grandchild (scoring)
│   └── evaluator builtin.coherence        ← grandchild (scoring)
├── eval_item 1
│   ├── invoke_agent my-agent
│   ├── evaluator builtin.similarity
│   └── evaluator builtin.coherence
└── ...
```

Each span records **attributes** — structured key-value pairs like `agentops.eval.evaluator.score = 0.87`.

### What Is OTLP?

**OTLP** (OpenTelemetry Protocol) is the wire format used to send traces from your application to a backend. AgentOps uses **OTLP/HTTP** with **Protobuf** encoding — which simply means it sends a compact binary HTTP POST to a collector URL.

### What Is an Exporter?

An **exporter** is the component that ships span data out of your process. AgentOps uses the `OTLPSpanExporter` from the `opentelemetry-exporter-otlp-proto-http` package, which sends spans over HTTP.

### What Is a Collector / Backend?

A **collector** (or backend) is the server that receives spans. Popular options:

| Collector | Runs Locally? | Cloud? | Best For |
|---|---|---|---|
| [Jaeger](https://www.jaegertracing.io/) | Yes (Docker) | No | Local development, free |
| [Azure Monitor / App Insights](https://learn.microsoft.com/azure/azure-monitor/) | No | Yes | Production on Azure |
| [Grafana Tempo](https://grafana.com/oss/tempo/) | Yes | Yes | Teams already using Grafana |
| [Datadog](https://www.datadoghq.com/) | No | Yes | Multi-cloud SaaS |

You pick one, point `AGENTOPS_OTLP_ENDPOINT` at it, and spans start flowing.

---

## How AgentOps Uses OTel

All telemetry logic lives in **one file**: `src/agentops/utils/telemetry.py`.

### Design Principles

1. **Opt-in** — Tracing is disabled by default. Set `AGENTOPS_OTLP_ENDPOINT` to turn it on.
2. **Zero-cost when off** — Every function checks `_tracing_enabled` first and returns immediately if `False`. No OTel packages are imported.
3. **Lazy imports** — `opentelemetry` is imported inside `init_tracing()`, not at the top of the file. If you don't have OTel installed and tracing is off, everything still works.
4. **Graceful degradation** — If OTel packages are missing and you set the env var, the `ImportError` is caught silently. No crash.

### Lifecycle

```
1. runner.py calls init_tracing()
2.   → reads AGENTOPS_OTLP_ENDPOINT
3.   → if empty: return (no-op mode)
4.   → if set:  import opentelemetry, create TracerProvider, attach OTLP exporter
5. runner.py opens eval_run_span()       ← root span starts
6.   for each row:
7.     open eval_item_span()             ← child span
8.       open agent_invoke_span()        ← grandchild span
9.       set_agent_invoke_result()       ← record tokens, model
10.      record_evaluator_span() × N     ← one per evaluator
11.    set_eval_item_result()            ← mark row pass/fail
12. set_eval_run_result()                ← mark run pass/fail
13. runner.py calls shutdown()           ← flush & close
```

Each of these functions is a **context manager** (using Python's `with` statement), so spans are automatically closed even if an exception occurs.

---

## Quick Start — Local Setup with Jaeger

The fastest way to see traces is to run [Jaeger](https://www.jaegertracing.io/) locally with Docker.

### 1. Start Jaeger

```bash
docker run -d --name jaeger \
  -p 16686:16686 \
  -p 4318:4318 \
  jaegertracing/jaeger:latest
```

| Port | Purpose |
|---|---|
| `16686` | Jaeger Web UI |
| `4318` | OTLP/HTTP receiver (this is what AgentOps talks to) |

### 2. Install the OTel packages

```bash
pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
```

### 3. Set the environment variable

```bash
# Linux / macOS
export AGENTOPS_OTLP_ENDPOINT=http://localhost:4318

# Windows PowerShell
$env:AGENTOPS_OTLP_ENDPOINT = "http://localhost:4318"
```

### 4. Run an evaluation

```bash
agentops eval run --config .agentops/run.yaml
```

### 5. Open Jaeger

Go to [http://localhost:16686](http://localhost:16686), select the **agentops** service, and click **Find Traces**. You will see the full trace tree for your evaluation run.

---

## Environment Variables

| Variable | Required? | Default | Description |
|---|---|---|---|
| `AGENTOPS_OTLP_ENDPOINT` | No | *(unset — tracing disabled)* | Base URL of the OTLP/HTTP collector. AgentOps appends `/v1/traces` automatically. |

That's it — one variable controls everything.

When unset:
- No OTel packages are imported.
- All telemetry functions are no-ops.
- Zero performance overhead.

---

## The Trace Tree

Every `agentops eval run` produces one trace with the following span hierarchy:

```
RUN <bundle_name>                             kind=SERVER
│
│   Attributes:
│     cicd.pipeline.name = <bundle>
│     cicd.pipeline.action.name = "RUN"
│     agentops.eval.dataset = <dataset>
│     agentops.eval.backend = <foundry|http|local>
│     agentops.eval.target = <agent|model>
│     agentops.eval.model = <deployment>          (if applicable)
│     agentops.eval.agent_id = <agent_id>         (if applicable)
│
├── eval_item 0                                kind=INTERNAL
│   │   cicd.pipeline.task.name = "eval_item"
│   │   agentops.eval.item.index = 0
│   │   agentops.eval.item.input = "..."
│   │   agentops.eval.item.expected = "..."
│   │   agentops.eval.item.passed = true
│   │
│   ├── invoke_agent my-agent                  kind=CLIENT
│   │     gen_ai.operation.name = "invoke_agent"
│   │     gen_ai.provider.name = "azure.ai.inference"
│   │     gen_ai.request.model = "gpt-4o"
│   │     gen_ai.agent.id = "my-agent:3"
│   │     gen_ai.usage.input_tokens = 142
│   │     gen_ai.usage.output_tokens = 87
│   │
│   ├── evaluator builtin.similarity           kind=INTERNAL
│   │     agentops.eval.evaluator.name = "SimilarityEvaluator"
│   │     agentops.eval.evaluator.builtin = "builtin.similarity"
│   │     agentops.eval.evaluator.score = 0.91
│   │     agentops.eval.evaluator.threshold = 0.7
│   │     agentops.eval.evaluator.passed = true
│   │
│   └── evaluator builtin.coherence            kind=INTERNAL
│         agentops.eval.evaluator.score = 0.85
│         ...
│
├── eval_item 1
│   └── ...
│
└── (final attributes on root span)
      cicd.pipeline.result = "success"
      agentops.eval.items_total = 10
      agentops.eval.items_passed = 9
      agentops.eval.pass_rate = 0.9
```

### Span Kinds Explained

| Kind | Meaning | Used For |
|---|---|---|
| `SERVER` | Receives and processes a request | The root eval run span |
| `CLIENT` | Makes an outbound call | Agent/model invocation |
| `INTERNAL` | Internal operation within the service | Eval items, evaluators |

---

## Semantic Conventions (Attributes)

AgentOps uses three layers of semantic conventions to make traces interoperable with standard OTel tooling.

### 1. CICD Layer (`cicd.pipeline.*`)

Maps evaluation runs to the standard CI/CD semantic convention, so tools like Azure Monitor pipelines can understand the structure.

| Attribute | Example | Description |
|---|---|---|
| `cicd.pipeline.name` | `conversational_agent_baseline` | Bundle name |
| `cicd.pipeline.action.name` | `RUN` | Fixed action type |
| `cicd.pipeline.result` | `success` / `failure` | Overall run outcome |
| `cicd.pipeline.task.name` | `eval_item` | Task type for item spans |
| `cicd.pipeline.task.run.id` | `0` | Row index |
| `cicd.pipeline.task.run.result` | `success` / `failure` | Item outcome |

### 2. GenAI Layer (`gen_ai.*`)

Follows the [OTel GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) for agent and model invocation spans.

| Attribute | Example | Description |
|---|---|---|
| `gen_ai.operation.name` | `invoke_agent` / `chat` | Operation type |
| `gen_ai.provider.name` | `azure.ai.inference` | Provider |
| `gen_ai.request.model` | `gpt-4o` | Requested model deployment |
| `gen_ai.response.model` | `gpt-4o-2024-08-06` | Actual model version |
| `gen_ai.agent.id` | `my-agent:3` | Foundry agent identifier |
| `gen_ai.agent.name` | `my-agent` | Agent display name |
| `gen_ai.agent.version` | `3` | Agent version |
| `gen_ai.usage.input_tokens` | `142` | Input token count |
| `gen_ai.usage.output_tokens` | `87` | Output token count |

### 3. AgentOps Layer (`agentops.eval.*`)

Custom attributes for evaluation-specific data that has no standard equivalent.

| Attribute | Example | Description |
|---|---|---|
| `agentops.eval.dataset` | `smoke-model-direct` | Dataset name |
| `agentops.eval.backend` | `foundry` | Execution backend |
| `agentops.eval.target` | `agent` | Target type |
| `agentops.eval.model` | `gpt-4o` | Model deployment |
| `agentops.eval.agent_id` | `my-agent:3` | Agent ID |
| `agentops.eval.items_total` | `10` | Total rows evaluated |
| `agentops.eval.items_passed` | `9` | Rows passing thresholds |
| `agentops.eval.pass_rate` | `0.9` | Pass rate |
| `agentops.eval.item.index` | `0` | Row index |
| `agentops.eval.item.input` | `"What is 2+2?"` | Input text |
| `agentops.eval.item.expected` | `"4"` | Expected answer |
| `agentops.eval.item.passed` | `true` | Row pass/fail |
| `agentops.eval.evaluator.name` | `SimilarityEvaluator` | Class name |
| `agentops.eval.evaluator.builtin` | `builtin.similarity` | Builtin name |
| `agentops.eval.evaluator.source` | `local` / `foundry` | Where evaluator runs |
| `agentops.eval.evaluator.score` | `0.91` | Numeric score |
| `agentops.eval.evaluator.threshold` | `0.7` | Configured threshold |
| `agentops.eval.evaluator.passed` | `true` | Score vs threshold |

---

## Viewing Traces in Jaeger

Once you run an evaluation with `AGENTOPS_OTLP_ENDPOINT` set, open Jaeger at [http://localhost:16686](http://localhost:16686).

### Finding Your Trace

1. In the **Service** dropdown, select `agentops`.
2. Click **Find Traces**.
3. You will see one trace per evaluation run, named `RUN <bundle_name>`.

### Reading the Timeline

Jaeger shows spans as horizontal bars on a timeline:

```
|============ RUN conversational_agent_baseline (1.2s) ============|
  |=== eval_item 0 (400ms) ===|
    |= invoke_agent (350ms) =|
    |= similarity (20ms) =|
    |= coherence (15ms) =|
                               |=== eval_item 1 (380ms) ===|
                                 |= invoke_agent (330ms) =|
                                 ...
```

- **Longer bars** = more time. This immediately shows you where time is spent.
- Click any span to see its **attributes** (the key-value pairs listed above).
- Look for spans with **red** or **error** status to find failures.

### Common Questions You Can Answer

| Question | Where to Look |
|---|---|
| Which row was slowest? | Sort `eval_item` spans by duration |
| Why did a row fail? | Check `agentops.eval.item.passed` and evaluator scores |
| How many tokens did the agent use? | Check `gen_ai.usage.input_tokens` + `output_tokens` |
| What was the overall pass rate? | Root span → `agentops.eval.pass_rate` |
| Which evaluator scored lowest? | Compare `agentops.eval.evaluator.score` across evaluator spans |

---

## Sending Traces to Azure Monitor

For production, you may want traces in Azure Monitor / Application Insights instead of local Jaeger.

### Option A: Use the OTel Collector as a Proxy

Run the [OpenTelemetry Collector](https://opentelemetry.io/docs/collector/) with an Azure Monitor exporter:

```yaml
# otel-collector-config.yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318

exporters:
  azuremonitor:
    connection_string: "InstrumentationKey=<your-key>;..."

service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [azuremonitor]
```

Then set `AGENTOPS_OTLP_ENDPOINT=http://localhost:4318`.

### Option B: Use Azure Monitor's OTLP Endpoint Directly

Azure Monitor now supports OTLP ingestion natively. Set the endpoint to your Application Insights OTLP ingestion URL:

```bash
export AGENTOPS_OTLP_ENDPOINT=https://<region>.applicationinsights.azure.com
```

Refer to the [Azure Monitor OTLP documentation](https://learn.microsoft.com/azure/azure-monitor/app/opentelemetry-configuration) for details.

---

## Evaluation Tracing vs. Agent Execution Tracing

It is important to understand that AgentOps telemetry covers **evaluation observability** — not agent execution tracing. These are two different things:

| | Evaluation Tracing (AgentOps) | Agent Execution Tracing (Foundry / Agent Framework) |
|---|---|---|
| **What it traces** | The eval run: which rows were evaluated, what scores each evaluator gave, pass/fail, timing | What the agent did step-by-step: tool calls, LLM calls, retrieval, reasoning |
| **Who provides it** | AgentOps (`telemetry.py` → `runner.py`) | Foundry portal, Agent Framework SDK, Azure Monitor |
| **Where to see it** | Jaeger, Azure Monitor, any OTLP backend | Foundry portal → Agent → Traces tab, Azure Monitor |
| **Activation** | `AGENTOPS_OTLP_ENDPOINT` env var | Automatic for Foundry agents; `configure_azure_monitor()` for custom agents |

**AgentOps does not reimplement agent execution tracing** — Foundry and the Agent Framework already do that natively. If your agent runs on Foundry or uses the Agent Framework SDK, execution traces are generated automatically and visible in the Foundry portal.

For custom agents (HTTP or local), make sure your agent code has OTel instrumentation configured (e.g., `azure-monitor-opentelemetry` with `configure_azure_monitor()`). The `agentops-trace` skill can help verify this.

---

## FAQ

### Do I need OpenTelemetry installed to use AgentOps?

**No.** OTel is completely optional. If the packages are not installed, or `AGENTOPS_OTLP_ENDPOINT` is not set, everything works normally with zero overhead.

### What packages do I need for tracing?

```bash
pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
```

### Is there any performance overhead?

When tracing is **disabled** (the default), overhead is effectively zero — just a boolean check per function call.

When tracing is **enabled**, spans are batched and sent asynchronously by the `BatchSpanProcessor`, so the impact on evaluation runtime is minimal.

### Can I use a different backend (not Jaeger)?

Yes. Any OTLP-compatible backend works. Just point `AGENTOPS_OTLP_ENDPOINT` at it. Popular options: Grafana Tempo, Datadog, Honeycomb, Zipkin (with an OTLP adapter).

### Where is the telemetry code?

One file: [`src/agentops/utils/telemetry.py`](../src/agentops/utils/telemetry.py).

### Can I extend the spans with custom attributes?

Not currently via configuration. If you need custom attributes, you can modify `telemetry.py` directly — the API is straightforward. Each span is a standard OTel span, so you can call `span.set_attribute("my.custom.key", value)` anywhere inside a span context.

---

## Summary

| Topic | Key Point |
|---|---|
| **Activation** | Set `AGENTOPS_OTLP_ENDPOINT` — that's it |
| **Dependencies** | `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http` |
| **Local viewer** | Jaeger via Docker on port `16686` |
| **Production** | Azure Monitor, Grafana Tempo, or any OTLP backend |
| **Overhead** | Zero when disabled, minimal when enabled |
| **Code** | `src/agentops/utils/telemetry.py` (one file) |
| **Standards** | CICD semconv + GenAI semconv + AgentOps custom attributes |
