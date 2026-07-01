# Foundry operations workbook

The Foundry operations workbook is an Azure Monitor workbook that AgentOps
deploys into your Log Analytics workspace. It turns the raw Azure OpenAI
diagnostic logs behind a Foundry project into operational charts for traffic,
latency, token consumption, and throttling. You deploy it once with a single
command and then open it in the Azure portal alongside the rest of your
monitoring.

!!! note "CLI availability"
    The `agentops telemetry dashboard` commands on this page ship in the
    companion feature release. If your installed version does not have them yet,
    update the package first with `pip install --upgrade agentops-accelerator`.

## When to use it

The workbook, the Cockpit, and Foundry each answer a different question, so pick
the surface that matches what you need.

| Surface | Best for | Scope |
|---|---|---|
| Foundry operations workbook | Usage, cost, token, and throttling trends over days and weeks. | Azure OpenAI platform metrics across every caller of the resource. |
| [Cockpit](operate.md#cockpit) | Reviewing Doctor findings and jumping to the traces behind them. | The AgentOps workspace and its release readiness signals. |
| Foundry portal | Reading a single conversation, trace, or evaluation run. | One agent run at a time inside the Foundry project. |

Reach for the workbook when someone asks how much the agent is being used, where
latency is coming from, or whether you are close to a throughput limit. Reach for
the Cockpit or Foundry when you need to inspect a specific finding or trace.

## Prerequisites

The workbook reads Azure OpenAI diagnostic logs, so those logs must be flowing
into a Log Analytics workspace before any chart has data.

Turn on both diagnostic categories on the Azure OpenAI resource that backs the
Foundry project:

| Diagnostic setting | Why it is needed |
|---|---|
| `RequestResponse` | Per-request logs for traffic, latency, status codes, and throttling. |
| `AzureOpenAIRequestUsage` | Token and usage logs for prompt, completion, and total tokens. |

Route both categories to the same Log Analytics workspace you plan to point the
workbook at. Allow time for the first logs to arrive, since diagnostic ingestion
can lag the first requests by a few minutes.

You also need the right Azure role for what you are doing:

| Action | Minimum role | Scope |
|---|---|---|
| View the workbook and its charts | Log Analytics Reader | The Log Analytics workspace. |
| Deploy or update the workbook | Workbook Contributor | The resource group or subscription that holds the workbook. |

## Deploy, open, and export

Deploy the workbook with one command from a configured workspace. AgentOps reads
the Foundry project endpoint, discovers the linked Log Analytics workspace, and
creates or updates the workbook in place.

```bash
agentops telemetry dashboard deploy
```

Open the deployed workbook directly in the Azure portal without hunting for it in
the resource list.

```bash
agentops telemetry dashboard open
```

If you cannot deploy because you lack Workbook Contributor, export the workbook
definition instead and hand it to someone who can import it.

```bash
agentops telemetry dashboard export
```

The export writes the workbook JSON to your workspace so a portal admin can
create the workbook manually, or so you can commit it and deploy it through your
own infrastructure pipeline.

## A tour of the four sections

The workbook is organized into four sections that read top to bottom, from
"how much traffic" down to "what is failing".

### 1. Traffic and usage

This section answers how much the agent is being called. It charts request volume
over time and breaks it down by deployment, so a spike or a drop is obvious at a
glance.

### 2. Latency and reliability

This section shows how the agent is performing. It plots p50, p95, and p99
latency and the success rate, so you can separate a slow tail from a broad
slowdown.

### 3. Tokens and throughput

This section covers consumption and capacity. It charts prompt, completion, and
total tokens, tokens per minute, and the normalized provisioned throughput usage
described below.

### 4. Errors and throttling

This section highlights what is failing. It counts errors by status code and
tracks throttled `429` responses, which is the first signal that you are hitting a
rate or quota limit.

!!! info "The PTU_Normalizado column"
    `PTU_Normalizado` is a derived column, not a raw platform metric. It
    normalizes token consumption against the provisioned throughput units of a
    deployment so that utilization is comparable across models and deployments of
    different sizes. Read a value near the top of its range as a deployment that
    is close to its provisioned capacity, and treat it as a planning signal rather
    than a hard limit.

## Troubleshooting empty charts

An empty workbook almost always means the underlying logs are missing, not that
the workbook is broken. Work through these causes from most to least common.

| Symptom | Likely cause | Fix |
|---|---|---|
| Every chart is empty | Diagnostic settings are off, or point at a different workspace. | Enable `RequestResponse` and `AzureOpenAIRequestUsage` on the Azure OpenAI resource and route both to the workbook's workspace. |
| Traffic and latency show data, tokens do not | Only `RequestResponse` is enabled. | Add the `AzureOpenAIRequestUsage` category. |
| Charts are empty only for recent time | Ingestion lag or no traffic in the window. | Widen the time range and re-run some requests, then wait a few minutes. |
| Charts load but you see a permissions error | Missing read access. | Grant Log Analytics Reader on the workspace. |

If data exists in the workspace but the workbook still looks wrong, confirm the
column names in your logs match the queries. The exact names depend on whether
the resource uses Azure diagnostics or resource-specific tables, which the
[KQL library](foundry-ops-workbook-kql.md) explains.

## Next

Browse the [KQL library](foundry-ops-workbook-kql.md) behind these charts, return
to [Operate](operate.md) for the readiness loop, or see where the signal comes
from on the [Observe](observe.md) page.
