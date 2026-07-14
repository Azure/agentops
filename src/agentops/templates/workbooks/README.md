# Foundry operations dashboard (Azure Monitor workbook)

`foundry-ops.workbook.json` is an Azure Monitor **gallery-template workbook** that
visualizes Azure AI Foundry / Azure OpenAI operational metrics end to end: PTU
utilization, PAYG spillover, traffic and token consumption, latency percentiles,
error / throttling rates, and Foundry-owned trace-evaluation results.

The workbook is scoped **per Azure OpenAI resource** and **per Log Analytics
workspace**. Pick the subscription, workspace, and Azure OpenAI resource in the
parameters bar, then use the deployment, model, time-range, and streaming
filters to narrow each tab.

## Tabs

| Tab | Shows |
| --- | --- |
| Capacity | `PTU_Avg_Pct`, `PTU_Max_Pct`, `Ratelimit`, PAYG `Spillover`, `Aggregated_PTU_With_Spillover`, `PTU_Normalizado` |
| Traffic and tokens | `TotalCalls`, `AzureOpenAIRequests`, `InputTokens`, `OutputTokens`, `TotalTokens`, `ProcessedPromptTokens`, `GeneratedTokens`, `TokensPorRequest`, and a PTU vs PAYG spillover pivot |
| Latency | `TTFT`, `TBT`, `TTLT`, tokens/sec, time-to-response, and `P95` / `P99` / `Avg` / `Max` over `DurationMs` |
| Errors and throttling | HTTP `429` / `400` / `500` counts, `BlockedCalls`, `TasaThrottling_Pct`, `TasaError_Pct` |
| Agent behavior | Data status and freshness, observed `invoke_agent` invocations, evaluated traces, evaluation-event counts, per-evaluator pass rate / volume / raw-score trends, and recent failed or low-score trace correlation |

`PTU_Normalizado` (`PTU% * 100000`) is a **display-only** scaling series so the
0-100 PTU utilization percentage can share a Y axis with raw token counts in a
single timechart. Read true utilization from `PTU_Avg_Pct` / `PTU_Max_Pct`.

## KQL queries

The raw KQL for each derived-metric family lives under
[`queries/`](./queries), so operators can reuse the logic outside the workbook:

| File | Derived metrics |
| --- | --- |
| [`capacity_ptu_spillover.kql`](./queries/capacity_ptu_spillover.kql) | PTU avg/max %, Ratelimit, Spillover, Aggregated PTU with spillover, PTU_Normalizado |
| [`traffic_tokens.kql`](./queries/traffic_tokens.kql) | Calls, input/output/total tokens, processed/generated tokens, tokens per request, PTU vs PAYG split |
| [`latency_percentiles.kql`](./queries/latency_percentiles.kql) | TTFT/TBT/TTLT, tokens/sec, time-to-response, and P95/P99/Avg/Max over DurationMs |
| [`errors_throttling.kql`](./queries/errors_throttling.kql) | HTTP 429/400/500 counts, blocked calls, throttling rate, error rate |
| [`agent_behavior.kql`](./queries/agent_behavior.kql) | Versioned `agent_behavior/v1` normalization for Foundry evaluation events and observed `invoke_agent` spans across workspace-based and classic Application Insights tables |

The standalone `.kql` files expose the parameter bindings as `let` declarations
at the top; the workbook substitutes the same values through its parameter bar.

## Agent behavior tab

The **Agent behavior** tab is an additive, read-only view over
`gen_ai.evaluation.result` events created by Microsoft Foundry trace evaluation.
Trace evaluation is a **preview, platform-owned** feature. AgentOps does not
create, schedule, gate, or edit the evaluations shown in the workbook.

The `agent_behavior/v1` KQL normalizer supports both recognized Application
Insights shapes:

- workspace-based `AppEvents` (`Name`, `Properties`) and `AppDependencies`
  (`Properties`);
- classic `customEvents` (`name`, `customDimensions`) and `dependencies`
  (`customDimensions`).

The mapping uses an explicit set of known `gen_ai.*` properties and keeps the
raw property bag in the normalized row. Missing optional values do not remove
events. Missing agent versions appear as **Version not reported**, and
nonnumeric score values remain visible as raw score text rather than becoming
zero.

The status row appears before quality results and distinguishes these states:

| State | Meaning |
| --- | --- |
| `Schema unavailable` | Matching event names exist, but none of the v1 evaluator, score, or label properties are recognized. Inspect the retained raw properties before updating the versioned mapping. |
| `No access` | The workbook query shows a native permission error. Request `Log Analytics Reader` on the selected workspace. |
| `No data` | The recognized event tables contain no `gen_ai.evaluation.result` events in the selected time range. |
| `Filter empty` | Events exist in the time range, but none match the environment, agent, version, and evaluator filters. |
| `Possible ingestion delay` | The newest matching event is more than 15 minutes old. This may be ingestion delay or simply no recent Foundry trace evaluation. |

The three count columns intentionally have different meanings:

- **Observed invoke_agent invocations** counts distinct invocation keys visible
  in the selected workspace.
- **Evaluated traces** counts distinct trace IDs represented by evaluation
  events.
- **Evaluation events** counts evaluator results. One evaluated trace can emit
  several events.

The workbook does not calculate evaluation coverage because the workspace might
not contain a complete invocation denominator. Pass rate uses only recognized
pass/fail labels. Raw score trends stay in a table grouped by evaluator because
different evaluators can use different scales; pass-rate and event-volume charts
use comparable units.

The recent-events table keeps trace, response, and conversation IDs when
available. Copy the trace ID, open the matching Microsoft Foundry project,
select **Tracing**, and search for the ID. The workbook cannot build a reliable
project-specific Foundry deep link from a Log Analytics workspace ID alone.

## Required diagnostic settings

The Azure OpenAI resource must send both of these log categories to the Log
Analytics workspace the workbook queries:

- `RequestResponse`
- `AzureOpenAIRequestUsage`

If they are missing, create them with:

```bash
az monitor diagnostic-settings create \
  --name agentops-foundry-ops \
  --resource <azure-openai-resource-id> \
  --workspace <log-analytics-workspace-id> \
  --logs '[{"category":"RequestResponse","enabled":true},{"category":"AzureOpenAIRequestUsage","enabled":true}]'
```

`agentops doctor` flags this automatically (rule
`waf.observability.aoai_diagnostic_categories`) and prints the exact command.

## Required roles

| Role | Scope | Why |
| --- | --- | --- |
| `Workbook Contributor` | Resource group that will hold the workbook | Deploy / update the `Microsoft.Insights/workbooks` resource |
| `Log Analytics Reader` | The Log Analytics workspace | Let the workbook query Azure OpenAI metrics/logs and Foundry-owned Application Insights events/spans |

## How to use it

Ship it with the CLI:

```bash
# Emit the ARM template without touching Azure.
agentops telemetry dashboard deploy --dry-run

# Deploy the workbook (needs Workbook Contributor on the resource group).
agentops telemetry dashboard deploy

# Open the workbook in the Azure portal.
agentops telemetry dashboard open

# Copy the workbook JSON to a local path for manual import.
agentops telemetry dashboard export --out ./foundry-ops.workbook.json
```

Or import it manually: **Azure portal → Monitor → Workbooks → New → Advanced
Editor**, paste the contents of `foundry-ops.workbook.json`, then Apply and Save,
selecting the target workspace and Azure OpenAI resource.

## Authoring note (validate before GA)

This workbook JSON was authored from the issue's KQL and the Azure Monitor
workbook schema **without a live Azure environment**. The Agent behavior
normalizer is validated with repository fixtures for both recognized table
shapes, missing optional fields, nonnumeric scores, multiple evaluators, absent
versions, and an unrecognized future schema. No live Foundry evaluation results
were invented or claimed.

Before relying on the workbook in production, smoke-validate it against a real
Log Analytics workspace that receives `RequestResponse`,
`AzureOpenAIRequestUsage`, Foundry `invoke_agent` spans, and
`gen_ai.evaluation.result` events. Confirm the parameters resolve, each tab
renders, data status is accurate, trace IDs correlate in Foundry Tracing, and
the derived metrics match expectations. Update the versioned normalizer instead
of silently accepting renamed properties.
