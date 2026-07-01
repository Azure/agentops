# Foundry operations workbook: KQL library

This page lists the Kusto queries behind the [Foundry operations
workbook](foundry-ops-workbook.md). Each query maps to one derived metric in the
workbook, so you can run it directly in Log Analytics, adapt it for an alert, or
paste it into your own dashboard.

!!! note "Column names depend on the diagnostic mode"
    These queries target `AzureDiagnostics`, which is the Azure diagnostics
    collection mode. If your resource uses resource-specific tables instead, the
    table and column names differ, so adjust the `where Category` filters and the
    parsed field names to match your workspace schema. Both diagnostic categories
    from the [prerequisites](foundry-ops-workbook.md#prerequisites) must be
    enabled for every query below to return rows.

## Request volume over time

Counts requests per five-minute bin so you can see traffic peaks and drops.

Columns returned: `TimeGenerated`, `Requests`.

```kusto
AzureDiagnostics
| where Category == "RequestResponse"
| where ResourceProvider == "MICROSOFT.COGNITIVESERVICES"
| summarize Requests = count() by bin(TimeGenerated, 5m)
| order by TimeGenerated asc
```

## Success rate

Reports the share of requests that returned a `2xx` status in each bin.

Columns returned: `TimeGenerated`, `SuccessRate`.

```kusto
AzureDiagnostics
| where Category == "RequestResponse"
| extend Status = toint(ResultSignature)
| summarize Total = count(), Success = countif(Status between (200 .. 299))
    by bin(TimeGenerated, 15m)
| extend SuccessRate = round(100.0 * Success / Total, 2)
| project TimeGenerated, SuccessRate
| order by TimeGenerated asc
```

## Error rate by status code

Breaks failures down by HTTP status code so you can tell a client error from a
server error.

Columns returned: `Status`, `Errors`.

```kusto
AzureDiagnostics
| where Category == "RequestResponse"
| extend Status = toint(ResultSignature)
| where Status >= 400
| summarize Errors = count() by Status
| order by Errors desc
```

## Throttled requests

Tracks `429` responses over time, the first sign that you are hitting a rate or
quota limit.

Columns returned: `TimeGenerated`, `Throttled`.

```kusto
AzureDiagnostics
| where Category == "RequestResponse"
| where toint(ResultSignature) == 429
| summarize Throttled = count() by bin(TimeGenerated, 5m)
| order by TimeGenerated asc
```

## Latency percentiles

Computes p50, p95, and p99 request latency in milliseconds per bin.

Columns returned: `TimeGenerated`, `p50`, `p95`, `p99`.

```kusto
AzureDiagnostics
| where Category == "RequestResponse"
| where isnotnull(DurationMs)
| summarize
    p50 = percentile(DurationMs, 50),
    p95 = percentile(DurationMs, 95),
    p99 = percentile(DurationMs, 99)
    by bin(TimeGenerated, 15m)
| order by TimeGenerated asc
```

## Token consumption

Sums prompt, completion, and total tokens from the usage logs per bin.

Columns returned: `TimeGenerated`, `PromptTokens`, `CompletionTokens`, `TotalTokens`.

```kusto
AzureDiagnostics
| where Category == "AzureOpenAIRequestUsage"
| extend props = parse_json(properties_s)
| extend
    PromptTokens = tolong(props.promptTokens),
    CompletionTokens = tolong(props.completionTokens),
    TotalTokens = tolong(props.totalTokens)
| summarize
    PromptTokens = sum(PromptTokens),
    CompletionTokens = sum(CompletionTokens),
    TotalTokens = sum(TotalTokens)
    by bin(TimeGenerated, 15m)
| order by TimeGenerated asc
```

## Tokens per minute

Turns total token usage into a tokens-per-minute rate for capacity planning.

Columns returned: `TimeGenerated`, `TokensPerMinute`.

```kusto
AzureDiagnostics
| where Category == "AzureOpenAIRequestUsage"
| extend props = parse_json(properties_s)
| extend TotalTokens = tolong(props.totalTokens)
| summarize TokensPerMinute = sum(TotalTokens) by bin(TimeGenerated, 1m)
| order by TimeGenerated asc
```

## Normalized provisioned throughput usage

Derives `PTU_Normalizado` by dividing tokens per minute by the provisioned
throughput of the deployment, so utilization is comparable across deployments.

Columns returned: `TimeGenerated`, `DeploymentName`, `PTU_Normalizado`.

```kusto
// Set this to the provisioned throughput units of the deployment you measure.
let provisioned_ptu = 100.0;
AzureDiagnostics
| where Category == "AzureOpenAIRequestUsage"
| extend props = parse_json(properties_s)
| extend
    DeploymentName = tostring(props.modelDeploymentName),
    TotalTokens = tolong(props.totalTokens)
| summarize TokensPerMinute = sum(TotalTokens) by bin(TimeGenerated, 1m), DeploymentName
| extend PTU_Normalizado = round(TokensPerMinute / provisioned_ptu, 3)
| project TimeGenerated, DeploymentName, PTU_Normalizado
| order by TimeGenerated asc
```

## Top deployments by usage

Ranks deployments by total tokens so you can see which one drives consumption.

Columns returned: `DeploymentName`, `TotalTokens`.

```kusto
AzureDiagnostics
| where Category == "AzureOpenAIRequestUsage"
| extend props = parse_json(properties_s)
| extend
    DeploymentName = tostring(props.modelDeploymentName),
    TotalTokens = tolong(props.totalTokens)
| summarize TotalTokens = sum(TotalTokens) by DeploymentName
| order by TotalTokens desc
```

## Next

Return to the [Foundry operations workbook](foundry-ops-workbook.md) overview, or
see the readiness loop these signals feed on the [Operate](operate.md) page.
