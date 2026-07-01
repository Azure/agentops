# Foundry operations dashboard (Azure Monitor workbook)

`foundry-ops.workbook.json` is an Azure Monitor **gallery-template workbook** that
visualizes Azure AI Foundry / Azure OpenAI operational metrics end to end: PTU
utilization, PAYG spillover, traffic and token consumption, latency percentiles,
and error / throttling rates.

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

The standalone `.kql` files expose the parameter bindings as `let` declarations
at the top; the workbook substitutes the same values through its parameter bar.

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
| `Log Analytics Reader` | The Log Analytics workspace | Let the workbook query `AzureMetrics` / `AzureDiagnostics` |

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
workbook schema **without a live Azure environment**. Before relying on it in
production, smoke-validate it against a real Log Analytics workspace that
receives `RequestResponse` and `AzureOpenAIRequestUsage` from an Azure OpenAI
resource: confirm the parameters resolve, each tab renders, and the derived
metrics match expectations. Adjust metric/field names if your platform emits
different `AzureMetrics` names or `properties_s` shapes.
