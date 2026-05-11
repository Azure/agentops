# Tutorial — AgentOps Watchdog Agent

The watchdog agent gives the GenAIOps / DevOps engineer a single
command (and a Copilot Chat extension) that answers the question
*"are my agents healthy in production?"* by combining three signal
sources:

1. **AgentOps eval history** — every `.agentops/results/*/results.json`
   the pipeline has produced.
2. **Azure Monitor / Application Insights** — Foundry agent telemetry
   queried via KQL.
3. **Foundry control plane** — agent metadata and recent runs read
   through `azure-ai-projects`.
4. **Azure resource posture** — a read-only WAF-AI Security pillar audit
   of the Cognitive Services / Azure OpenAI account that hosts the agent
   and judge model.

The agent runs the same checks (regression, latency, errors, safety)
in three form factors:

| Form factor | Use it when… |
|---|---|
| `agentops agent analyze` (CLI) | You want a Markdown report locally or in CI. |
| `agentops agent serve` (FastAPI Copilot Extension) | You want a chat-driven watchdog inside GitHub Copilot Chat. |
| Container Apps deploy (`templates/agent-server/`) | You want the same Copilot Extension hosted publicly. |

## 1. Local dry-run

```powershell
pip install "agentops-toolkit[agent] @ git+https://github.com/Azure/agentops.git@develop"
agentops init                     # if you don't already have .agentops/

# Optional: drop a starter agent.yaml into the workspace.
$template = python -c "import agentops, pathlib; print(pathlib.Path(agentops.__file__).parent / 'templates' / 'agent.yaml')"
Copy-Item $template .agentops\agent.yaml

agentops agent analyze
```

The first run produces `.agentops/agent/report.md`. With no
`agent.yaml` the analyzer uses defaults: results-history is the only
active source, Azure Monitor and Foundry control are reported as
`skipped` in the diagnostics block.

## 2. Wire production telemetry

Edit `.agentops/agent.yaml`:

```yaml
sources:
  azure_monitor:
    enabled: true
    app_insights_resource_id: /subscriptions/.../components/myappi
  foundry_control:
    enabled: true
    project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
```

Install the agent extras (lazy SDKs only loaded when sources are
enabled):

```powershell
pip install "agentops-toolkit[agent] @ git+https://github.com/Azure/agentops.git@develop"
az login
agentops agent analyze --severity-fail critical
```

Exit codes are CI-friendly:

- `0` — analyzer ran clean
- `2` — a finding meets the configured `--severity-fail` floor
- `1` — runtime / configuration error

## 3. Security posture audit (WAF-AI)

The watchdog can also run a **read-only audit of the Azure footprint**
hosting your agent against the [Microsoft Well-Architected Framework
for AI workloads — Security pillar][waf-ai]. This is opt-in: the
findings live in their own `security` category and are skipped unless
both the `azure_resources` source and the `posture` check are enabled.

Why is this opt-in? The telemetry checks use App Insights and Foundry
metadata that you already configured in the previous step. Security
posture requires management-plane reads against the Azure resource group,
so the tutorial asks for the subscription, resource group, and Cognitive
Services account explicitly instead of guessing them.

The audit runs five high-impact rules against the Cognitive Services /
Azure OpenAI account:

| Rule id | Severity | What it checks |
|---|---|---|
| `waf.security.local_auth_disabled` | critical | `disableLocalAuth: true` (Entra ID only, no API keys) |
| `waf.security.public_network_access` | warning | Public access disabled, private endpoint, **or** ACL `defaultAction: Deny` |
| `waf.security.managed_identity` | warning | System- or user-assigned MI present on the account |
| `waf.security.diagnostic_settings` | warning | Diagnostic logs flowing to Log Analytics / storage / event hub |
| `waf.security.content_filter` | critical | Every model deployment has a RAI policy applied |

Required RBAC: **Reader** on the resource group (or on each individual
resource), granted to whoever runs `agentops agent analyze` (your local
identity locally, or the OIDC-federated identity in CI).

Find the account to audit:

```powershell
$env:AZURE_SUBSCRIPTION_ID = az account show --query id -o tsv
$resourceGroup = "<your-agent-resource-group>"

az cognitiveservices account list `
  --resource-group $resourceGroup `
  --query "[].{name:name,kind:kind,location:location,disableLocalAuth:properties.disableLocalAuth,publicNetworkAccess:properties.publicNetworkAccess}" `
  -o table
```

Pick the account that hosts your Azure OpenAI / AI Services deployment:

```powershell
$cognitiveAccount = "<ai-services-or-azure-openai-account-name>"
```

Enable in `.agentops/agent.yaml`:

```powershell
@"
version: 1
lookback_days: 7

sources:
  results_history:
    enabled: true
    path: .agentops/results
    lookback_runs: 10
  azure_monitor:
    enabled: true
    app_insights_resource_id: $appInsightsId
  foundry_control:
    enabled: true
    project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
  azure_resources:
    enabled: true
    subscription_id_env: AZURE_SUBSCRIPTION_ID
    resource_group: $resourceGroup
    cognitive_services_account: $cognitiveAccount

checks:
  latency:
    p95_threshold_seconds: 10.0
  errors:
    rate_threshold: 0.05
  posture:
    enabled: true
    pillar: security
    exclude_rules: []
"@ | Set-Content .agentops/agent.yaml -Encoding utf8
```

Run only the security category first:

```powershell
agentops agent analyze --categories security --severity-fail critical
code .agentops/agent/report.md
```

In the test run for this tutorial, `azure_resources` changed from
`disabled` to `ok` and the report produced two WAF-AI findings:

```text
## Verdict: ⚠️ Warnings found

| Category | Count |
|---|---|
| Security posture (WAF-AI — Security pillar) | 2 |

| Source | Status | Detail |
|---|---|---|
| azure_resources | ok |

| Severity | ID | Title | Source |
|---|---|---|---|
| warning | waf.security.diagnostic_settings | Diagnostic settings are missing or incomplete | azure_resources |
| warning | waf.security.public_network_access | Public network access is open and unrestricted | azure_resources |
```

The evidence blocks in that run showed:

```json
{
  "account": "aif-agentops-exp",
  "diagnostic_settings": []
}
```

```json
{
  "account": "aif-agentops-exp",
  "public_network_access": "Enabled",
  "private_endpoint_count": 0,
  "network_acls_default_action": "Allow"
}
```

Those are real management-plane findings: the account had Entra-only
authentication enabled, but it still needed diagnostic settings and a
network restriction plan.

Run only the security category, or skip a specific rule from the CLI:

```powershell
# Run every check, including the WAF audit (the default once enabled).
agentops agent analyze

# Only run the security audit.
agentops agent analyze --categories security

# Skip a specific rule on top of any YAML excludes.
agentops agent analyze --exclude-rules waf.security.diagnostic_settings

# Skip multiple rules.
agentops agent analyze --exclude-rules waf.security.diagnostic_settings,waf.security.managed_identity
```

The Markdown report groups findings by category, so security findings
appear under their own `### Security posture (WAF-AI — Security pillar)`
heading with a footer link back to the WAF-AI guidance.

[waf-ai]: https://learn.microsoft.com/azure/well-architected/ai/security

## 4. CI scheduled run

Pair the analyzer with a GitHub Actions schedule:

```yaml
on:
  schedule: [{ cron: "0 7 * * *" }]
  workflow_dispatch:
jobs:
  watchdog:
    runs-on: ubuntu-latest
    permissions: { id-token: write, contents: read }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install "agentops-toolkit[agent] @ git+https://github.com/Azure/agentops.git@develop"
      - uses: azure/login@v2
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
      - run: agentops agent analyze --severity-fail critical
      - uses: actions/upload-artifact@v4
        with:
          name: agentops-watchdog-report
          path: .agentops/agent/report.md
```

## 5. Copilot Chat extension (local)

```powershell
pip install "agentops-toolkit[agent] @ git+https://github.com/Azure/agentops.git@develop"
agentops agent serve --no-verify --port 8080
```

Then point a GitHub App's Copilot Extension webhook at
`http://localhost:8080/agents/messages`. **`--no-verify` is
local-only** — never expose that endpoint publicly without signature
validation.

## 6. Hosted Copilot Extension on Azure Container Apps

The repo ships a minimal scaffold:

```
src/agentops/templates/agent-server/
├── Dockerfile
├── main.bicep
└── README.md
```

Workflow:

```powershell
az acr build --registry <acr> --image agentops-watchdog:1.0.0 `
   --file Dockerfile .

az deployment group create `
   --resource-group <rg> `
   --template-file main.bicep `
   --parameters `
       environmentName=<aca-env> `
       image=<acr>.azurecr.io/agentops-watchdog:1.0.0 `
       userAssignedIdentityId=<umi-id> `
       appInsightsResourceId=<appi-id> `
       foundryProjectEndpoint=<https://...>
```

The user-assigned identity needs `Monitoring Reader` on the App
Insights resource and `Azure AI Developer` on the Foundry project.

## What the report looks like

```
# AgentOps Watchdog Report

## Verdict: 🚨 CRITICAL issues found

## Summary
| Severity | Count |
|---|---|
| 🚨 Critical | 1 |
| ⚠️  Warning  | 1 |
| ℹ️  Info     | 0 |

## Sources
| Source | Status | Detail |
|---|---|---|
| `results_history` | `ok` | 7 |
| `azure_monitor`   | `ok` |  |
| `foundry_control` | `skipped` | no project_endpoint configured |

## Findings
| Severity | ID | Title | Source |
|---|---|---|---|
| 🚨 `critical` | `regression.coherence` | Regression detected on `coherence` | results_history |
| ⚠️  `warning`  | `latency.p95_production` | Production p95 latency exceeds threshold | azure_monitor |
```

Each finding has its own *Details* section with a recommendation and
an *Evidence* JSON block — that is the bit you copy/paste into a PR
or an incident.
