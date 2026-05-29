---
name: agentops-agent
description: AgentOps Doctor - surface release-readiness findings, regressions, latency spikes, error rates, and safety hits across AgentOps eval history, Azure Monitor traces, and Foundry control plane.
---

# `agentops-agent` - Doctor skill

Use this skill when the user asks any of:

- *"Are my agents healthy in production?"*
- *"Run the doctor"*
- *"Anything regressed in our last evals?"*
- *"Show latency / error spikes from Azure Monitor"*
- *"Open the AgentOps doctor report"*

This skill is the front door to `agentops doctor` and the
`agentops agent serve` Copilot Extension. It does **not** invent
findings - it shells out to the CLI which reads real data from:

1. `.agentops/results/*/results.json` (eval history)
2. Application Insights traces emitted by Foundry agents
3. Foundry control plane (`azure-ai-projects`)

Doctor and Cockpit are read-only readiness tools. They help answer "can we ship
it, and where is the proof?" They do not create, deploy, mutate, or delete
Foundry or Azure resources.

## Workflow

### 1. Validate the workspace

Look for `.agentops/agent.yaml`. If absent, copy the template:

```bash
mkdir -p .agentops
cp $(python -c "import agentops, os, pathlib;
print(pathlib.Path(agentops.__file__).parent / 'templates' / 'agent.yaml')") .agentops/agent.yaml
```

Edit `app_insights_resource_id` and `project_endpoint_env` if the user
wants the Azure Monitor / Foundry sources to be live. Without those
values the sources skip gracefully.

### 2. Run the analyzer

```bash
agentops doctor --severity-fail critical
```

The command writes `.agentops/agent/report.md`. Exit codes:

- `0` - no findings at or above the configured severity floor
- `2` - at least one finding meets the severity floor (use this in CI)
- `1` - runtime / configuration error

### 3. Read and summarize

Open `.agentops/agent/report.md`. The report has:

- **Verdict banner** - overall pass / warning / critical
- **Summary** - counts by severity
- **Sources** - which sources ran, which were skipped and why
- **Findings** - sorted by severity, each with a recommendation
- **Recent runs** - appendix of the last `lookback_runs` evals

When summarising for the user, lead with the verdict, then the top
3 findings, each with the recommendation. Always cite the finding `id`
so the user can grep them later.

### 4. Drive remediation, do not invent it

For each finding the report includes a `Recommendation`. Follow it
verbatim - for example, if the finding says "compare the latest run
against the baseline runs in `.agentops/results/`", actually open
those folders.

## Local cockpit (`agentops cockpit`)

For a workspace-level operations view the user can open a local
Cockpit:

```bash
pip install "agentops-accelerator[agent] @ git+https://github.com/Azure/agentops.git@main"
agentops cockpit
# → http://127.0.0.1:8090
```

Cockpit reads local AgentOps artifacts first: `.agentops/results/`,
generated reports, `.agentops/agent/history.jsonl`, and workflow files.
When a Foundry project is configured, it also resolves telemetry
readiness and links to the matching Foundry and Azure Monitor views.
It is read-only and bound to localhost.

When telemetry is enabled the analyzer **also** emits OpenTelemetry
spans for the Doctor run with per-severity / per-category counters,
useful for long-term retention in App Insights or any OTel backend.
Resolution order:

1. `APPLICATIONINSIGHTS_CONNECTION_STRING` (or the AgentOps-prefixed
   variant) - explicit user configuration always wins.
2. `AGENTOPS_OTLP_ENDPOINT` - generic OTLP/HTTP exporter.
3. **Auto-discovery** - when `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` is
   set but no explicit env var is, AgentOps asks the Foundry project
   for the connection string of the Application Insights resource
   attached to it. Zero configuration when the user is already on
   Foundry.

## Copilot Extension server

If the user wants the Doctor inside Copilot Chat, they can:

```bash
pip install "agentops-accelerator[agent] @ git+https://github.com/Azure/agentops.git@main"
agentops agent serve --no-verify       # local dev
```

For production, point them at:

- `src/agentops/templates/agent-server/Dockerfile`
- `src/agentops/templates/agent-server/main.bicep`
- `src/agentops/templates/agent-server/README.md`

These are the deploy scaffold for hosting the Doctor as a Copilot
Extension on Azure Container Apps.

## Guardrails

- Do **not** fabricate findings, metric values, or recommendations.
- Do **not** invent CLI flags. The contract is exactly:
  - `agentops doctor [--workspace] [--config] [--out] [--lookback-days] [--severity-fail]`
  - `agentops agent serve [--host] [--port] [--config] [--no-verify] [--workers]`
  - `agentops cockpit [--host] [--port] [--workspace]`
- If a source is `skipped` or `error`, surface that as the *first*
  thing in the user-facing summary so they know the analyzer ran with
  partial data.
- Never suggest disabling content-safety checks - recommend filtering
  the offending row or tightening the system prompt instead.
