# Tutorial - AgentOps Doctor

> **New here?** Read [`doctor-explained.md`](doctor-explained.md)
> first - it's a 10-minute conceptual tour of what the Doctor checks
> and how to read a report. This tutorial is task-driven and assumes
> Azure access.

The Doctor agent gives the GenAIOps / DevOps engineer a single command
(and a Copilot Chat extension) that answers the question *"are my
agents healthy in production?"* by combining four signal sources:

1. **AgentOps eval history** - local `.agentops/results/*/results.json`
   first, with Foundry cloud evaluation runs as fallback when local
   history is missing or too short.
2. **Azure Monitor / Application Insights** - Foundry agent telemetry
   queried via KQL.
3. **Foundry control plane** - agent metadata, recent runs, and
   continuous-evaluation rules read through `azure-ai-projects`.
4. **Azure resource posture** - a read-only WAF-AI Security pillar audit
   of the Cognitive Services / Azure OpenAI account that hosts the agent
   and judge model.

> **Naming.** Earlier versions called this surface "Watchdog". The
> CLI command (`agentops doctor`), the cockpit label, and this
> tutorial were renamed to "Doctor" because that better matches the
> mental model of a regular check-up: run it on a schedule, get a
> verdict, act on findings. Function and module names inside the
> codebase still use the legacy `watchdog` spelling for
> backwards-compatible imports - the user-facing surface is what
> changed.

## Checks at a glance

The Doctor runs **eight** check modules and reports findings in
**six categories** that mirror the **Microsoft Well-Architected
Framework for AI** pillars. Categories are independent of severity
(a `quality` finding can be `critical`, `warning`, or `info`).

| Check | Category | What it observes |
|---|---|---|
| `regression` | `quality` | Metric drops in the latest eval run vs the rolling baseline. |
| `latency` | `performance` | p95 latency above threshold (Azure Monitor + eval history). |
| `errors` | `reliability` | Production error rate, Foundry run failures, **and** missing runtime telemetry. |
| `safety` | `responsible_ai` | Three layers: eval content-safety hits, runtime content-filter triggers, missing/disabled continuous-eval rules. |
| `posture` | `security` | WAF-AI Security pillar - local-auth, managed identity, diagnostic settings. |
| `opex_workspace` | `operational_excellence` | Workspace hygiene - agent pinning, thresholds, PR gate, deploy workflow, results-gitignore, dataset versioning. |
| `opex` | `operational_excellence` | Time-based hygiene - stale evaluation runs + flaky-metric drift. |
| `spec_conformance` | `operational_excellence` | Spec-vs-implementation drift (spec-kit `.specify/`, `AGENTS.md`, Copilot instructions). |

Findings produced by these checks are sorted by severity and grouped
by category in the report.

The Doctor runs in three form factors:

| Form factor | Use it when… |
|---|---|
| `agentops doctor` (CLI) | You want a Markdown report locally or in CI. |
| `agentops agent serve` (FastAPI Copilot Extension) | You want a chat-driven Doctor inside GitHub Copilot Chat. |
| Container Apps deploy (`templates/agent-server/`) | You want the same Copilot Extension hosted publicly. |

## 1. Local dry-run

```powershell
pip install "agentops-toolkit[agent]"
agentops init                     # if you don't already have .agentops/

# Optional: drop a starter agent.yaml into the workspace.
$template = python -c "import agentops, pathlib; print(pathlib.Path(agentops.__file__).parent / 'templates' / 'agent.yaml')"
Copy-Item $template .agentops\agent.yaml

agentops doctor
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
pip install "agentops-toolkit[agent]"
az login
agentops doctor --severity-fail critical
```

Exit codes are CI-friendly:

- `0` - analyzer ran clean
- `2` - a finding meets the configured `--severity-fail` floor
- `1` - runtime / configuration error

### When Application Insights is reachable but quiet

The Doctor warns explicitly when the App Insights workspace is
reachable but reports zero requests over the lookback window
(`errors.no_runtime_telemetry`). That state almost always means the
agent runtime is not wired to telemetry, so the cockpit, latency,
errors, and runtime-safety checks have nothing to grade. The
finding's recommendation walks you to the OpenTelemetry setup in
[`tutorial-basic-foundry-agent.md`](tutorial-basic-foundry-agent.md).

## 3. The three-layer safety check

The `safety` check produces findings under `Category.RESPONSIBLE_AI`
from three independent layers. Each layer fails open - if its
source did not produce a payload, that layer simply emits nothing.

| Layer | Finding id | Source | Triggered by |
|---|---|---|---|
| **Eval** | `safety.<metric>` | `results_history` | A row in the latest eval scored at or above `severity_floor` on a content-safety evaluator (Violence / SelfHarm / Sexual / HateUnfairness). |
| **Runtime** | `safety.runtime.<signal>` | `azure_monitor` | App Insights observed `content_filter` triggers in `gen_ai.response.finish_reasons` over the lookback window. |
| **Config** | `safety.config.continuous_eval_missing` / `safety.config.continuous_eval_disabled` | `foundry_control` | Foundry project has agents but no continuous-evaluation rules - or rules exist but are disabled. |

Tune the eval layer's sensitivity in `agent.yaml`:

```yaml
checks:
  safety:
    severity_floor: Medium       # Low | Medium | High
    min_runtime_hits: 1          # ignore single accidental hits
    runtime_critical_hits: 10    # promote to critical above this
```

## 4. Security posture audit (WAF-AI)

The Doctor also runs a **read-only audit of the Azure footprint** hosting
your agent against the [Microsoft Well-Architected Framework for AI
workloads - Security pillar][waf-ai]. The `azure_resources` source and
the `posture` check are enabled by default. Doctor first tries to infer
the deployed resources from AZD metadata in `.azure/<env>/.env`, then
from the Foundry project endpoint, and finally from explicit
`.agentops/agent.yaml` values.

This source still fails open: if the Azure SDK is missing, authentication
is unavailable, RBAC is insufficient, or the resource match is ambiguous,
Doctor records a diagnostic explaining what was skipped and how to
configure it. It does not fail the whole analysis just because one Azure
source could not be read.

The audit ships three rules against the Cognitive Services /
Azure OpenAI account. **Public-network access, private endpoints,
custom subdomain, and per-deployment content-filter (RAI) policies
were intentionally retired** - Foundry's Operate → Compliance
surface now covers them natively, and the Doctor's mandate is the
complementary half (runtime telemetry, identity scope, pipeline
hygiene).

| Rule id | Severity | What it checks |
|---|---|---|
| `waf.security.local_auth_disabled` | critical | `disableLocalAuth: true` (Entra ID only, no API keys) |
| `waf.security.managed_identity` | warning | System- or user-assigned MI present on the account |
| `waf.security.diagnostic_settings` | warning | Diagnostic logs flowing to Log Analytics / storage / event hub |

Required RBAC: **Reader** on the resource group (or on each individual
resource), granted to whoever runs `agentops doctor` (your local
identity locally, or the OIDC-federated identity in CI).

If automatic discovery is ambiguous, find the account to audit:

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

Pin the source explicitly in `.agentops/agent.yaml`:

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
  opex:
    enabled: true
    stale_after_days: 14
"@ | Set-Content .agentops/agent.yaml -Encoding utf8
```

Run only the security category first:

```powershell
agentops doctor --categories security --severity-fail critical
code .agentops/agent/report.md
```

Run only the security category, or skip a specific rule from the CLI:

```powershell
# Run every check, including the WAF audit (the default once enabled).
agentops doctor

# Only run the security audit.
agentops doctor --categories security

# Skip a specific rule on top of any YAML excludes.
agentops doctor --exclude-rules waf.security.diagnostic_settings

# Skip multiple rules.
agentops doctor --exclude-rules waf.security.diagnostic_settings,waf.security.managed_identity
```

The Markdown report groups findings by category, so security findings
appear under their own `### Security posture (WAF-AI - Security pillar)`
heading with a footer link back to the WAF-AI guidance.

[waf-ai]: https://learn.microsoft.com/azure/well-architected/ai/security

### WAF knowledge base (editable CSV)

Every finding the Doctor emits is mapped to a row in
[`src/agentops/agent/knowledge/waf-checklist.csv`](../src/agentops/agent/knowledge/waf-checklist.csv).
The CSV is the source of truth for the WAF pillar / area / reference
link the reporter annotates next to a finding (e.g. `WAF: Security /
Identity - waf.security.local_auth_disabled`).

The file follows a strict rule: **only items the Doctor can actually
check are listed**. Human-eyeball checklist items (e.g. "review the
threat model with the security team") are intentionally excluded. To
add or remove a row, edit the CSV directly - the Doctor reads it at
runtime, no re-install required when working from a source checkout.

The CSV's `doctor_check_id` column accepts either a fully qualified
finding id (`opex.no_pr_gate`) or a dotted prefix (`regression`
covers `regression.coherence`, `regression.fluency`, …). The
reporter walks segments longest-first, so a more specific row always
wins over a prefix row.

## 5. Operational Excellence hygiene checks

The Doctor runs workspace-level rules that flag operational gaps
which never show up in eval results or in Azure telemetry. They live
under `Category.OPERATIONAL_EXCELLENCE` and only fire when there's
something concrete to check - running the Doctor on a brand-new repo
stays quiet on purpose.

| Rule id | Severity | What it checks |
|---|---|---|
| `opex.unpinned_agent` | warning | `agentops.yaml` has `agent: my-agent` (no `:version` suffix). |
| `opex.no_thresholds` | warning | `agentops.yaml` has no `thresholds:` block - gate relies on auto-defaults. |
| `opex.no_pr_gate` | warning | `.github/workflows/` exists but no `agentops-pr.yml`. |
| `opex.no_deploy_workflow` | warning | PR gate exists but no `agentops-deploy-*.yml` - evals never re-run after deployment. |
| `opex.results_not_gitignored` | warning | `.agentops/results/` is not excluded from any reachable `.gitignore`. |
| `opex.unversioned_dataset` | warning | One or more dataset YAML files in `.agentops/datasets/` lack a top-level `version:`. |
| `opex.stale_evaluation` | warning / critical | The most recent eval run is older than `checks.opex.stale_after_days` (default 14). Promoted to critical at 2× the threshold. |

These are the checks `agentops workflow generate` plus
`agentops init` are designed to keep green by default. If you fork
the templates, expect to see findings here until the fork matches
the same defaults.

## 6. CI scheduled run

Pair the analyzer with a GitHub Actions schedule:

```yaml
on:
  schedule: [{ cron: "0 7 * * *" }]
  workflow_dispatch:
jobs:
  doctor:
    runs-on: ubuntu-latest
    permissions: { id-token: write, contents: read }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install "agentops-toolkit[agent]"
      - uses: azure/login@v2
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
      - run: agentops doctor --severity-fail critical
      - uses: actions/upload-artifact@v4
        with:
          name: agentops-doctor-report
          path: .agentops/agent/report.md
```

## 7. Copilot Chat extension (local)

```powershell
pip install "agentops-toolkit[agent]"
agentops agent serve --no-verify --port 8080
```

Then point a GitHub App's Copilot Extension webhook at
`http://localhost:8080/agents/messages`. **`--no-verify` is
local-only** - never expose that endpoint publicly without signature
validation.

## 8. Hosted Copilot Extension on Azure Container Apps

The repo ships a minimal scaffold:

```
src/agentops/templates/agent-server/
├── Dockerfile
├── main.bicep
└── README.md
```

Workflow:

```powershell
az acr build --registry <acr> --image agentops-doctor:1.0.0 `
   --file Dockerfile .

az deployment group create `
   --resource-group <rg> `
   --template-file main.bicep `
   --parameters `
       environmentName=<aca-env> `
       image=<acr>.azurecr.io/agentops-doctor:1.0.0 `
       userAssignedIdentityId=<umi-id> `
       appInsightsResourceId=<appi-id> `
       foundryProjectEndpoint=<https://...>
```

The user-assigned identity needs `Monitoring Reader` on the App
Insights resource and `Azure AI Developer` on the Foundry project.

## What the report looks like

```
# AgentOps Doctor Report

## Verdict: 🚨 CRITICAL issues found

## Summary
| Severity | Count |
|---|---|
| 🚨 Critical | 1 |
| ⚠️  Warning  | 2 |
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
| ⚠️  `warning`  | `opex.no_deploy_workflow` | Repository has a PR gate but no deploy workflow | opex_workspace |
```

Each finding has its own *Details* section with:

- Severity, Category, Source - and, when the finding id is in
  `waf-checklist.csv`, a **WAF** line showing the pillar / area / link.
- A summary paragraph.
- A recommendation.
- An *Evidence* JSON block - that is the bit you copy/paste into a PR
  or an incident.
