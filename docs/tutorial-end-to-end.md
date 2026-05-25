# End-to-end workshop: release readiness for Foundry agents

This workshop is the full path. Use it after one of the quickstarts when you
want to validate the complete build -> evaluate -> release -> observe loop.

It is inspired by the Azure Samples workshop
[Mind the Gap In Your AI Agent Observability](https://github.com/Azure-Samples/microsoft-foundry-e2e-agent-observability-workshop/tree/2026-04-aie-europe).
That workshop goes deep on Foundry SDK notebooks, tracing, evaluation, and
red-team scans. This AgentOps workshop does not copy those labs. It shows where
AgentOps fits around the same lifecycle as the repo-side readiness and evidence
layer.

![Foundry Control Plane](media/foundry-control-plane.png)

Foundry gives you the control plane: fleet management, observability, security,
and compliance. AgentOps adds the repo contract around that control plane:
repeatable CI gates, Doctor checks, release evidence, and trace-to-regression
review.

## What you will validate

| Stage | Activity | Main tools | AgentOps role | Output |
|---|---|---|---|---|
| 1 | Define the agent goal and risks | Foundry docs, VS Code, Copilot | Helps define what must be proven before release. | Success criteria and risk list |
| 2 | Choose Prompt Agent or Hosted Agent | Foundry portal, Foundry Toolkit, team architecture | Later references the target as `name:version` or URL. | Target type decision |
| 3 | Create or deploy the agent | Foundry portal, Foundry SDK, Foundry Toolkit, Agent Framework, `microsoft-foundry` skill | No ownership of create/deploy. | Agent version or endpoint |
| 4 | Test and debug | Foundry playground, VS Code debugger, Agent Inspector, Copilot Chat | Optional quick eval after target exists. | Working dev-loop agent |
| 5 | Configure release checks | AgentOps CLI and skills | Creates `agentops.yaml` and repo-side release contract. | Release checklist in repo |
| 6 | Evaluate | Official AI Agent Evaluation or AgentOps local runner | Routes to the right runner and normalizes proof. | Eval gate signal |
| 7 | Create operations workflow | GitHub Actions, Azure Pipelines, azd | Generates PR, environment, and watchdog workflows. | CI/CD gates |
| 8 | Observe production | Foundry Operate, Azure Monitor, Application Insights | Checks wiring and links to official dashboards. | Traces, metrics, health |
| 9 | Review readiness | AgentOps Doctor, Cockpit, evidence pack | Answers "can we ship it, and where is the proof?" | `evidence.md` |
| 10 | Learn from traces | Foundry/App Insights exports, AgentOps trace promotion | Turns reviewed traces into regression candidates. | Future eval rows |

## Prerequisites

- Azure CLI signed in with access to a Foundry project.
- A Foundry project endpoint.
- One agent target:
  - Prompt agent: `name:version`, or
  - Hosted/HTTP endpoint: `https://...`.
- One Azure OpenAI deployment for evaluator calls.
- Application Insights connected to the Foundry project or agent runtime.

Install from the local repo while validating changes:

```powershell
mkdir C:\Users\paulolacerda\workspace\test-agentops-workshop
cd C:\Users\paulolacerda\workspace\test-agentops-workshop
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e "C:\Users\paulolacerda\workspace\agentops[foundry,agent]"
az login
```

Set the evaluator deployment:

```powershell
$env:AZURE_OPENAI_DEPLOYMENT = "gpt-4o-mini"
```

## 1. Build or select the agent

Use the official tool that matches the agent lifecycle:

| Agent type | Recommended creation path | AgentOps role |
|---|---|---|
| Prompt agent | Foundry portal, Foundry SDK, Foundry Toolkit, or Foundry Skills | Track `agent: name:version` and route CI to official eval |
| Hosted agent | Foundry Toolkit, azd, Docker, ACA, AKS, or custom platform | Track endpoint URL and run local eval gates |

If you want the notebook-style Foundry build path, follow the Azure Samples
workshop labs for creating agents, tools, tracing, evaluation, and red-team
scans:

```text
https://github.com/Azure-Samples/microsoft-foundry-e2e-agent-observability-workshop/tree/2026-04-aie-europe
```

Return here once you have an agent reference or endpoint.

## 2. Initialize the repo-side release contract

Prompt agent:

```powershell
agentops init `
  --dir . `
  --azd-env dev `
  --project-endpoint "https://<resource>.services.ai.azure.com/api/projects/<project>" `
  --agent "travel-agent:1" `
  --dataset ".agentops/data/smoke.jsonl" `
  --no-prompt
```

Hosted agent:

```powershell
agentops init `
  --dir . `
  --azd-env dev `
  --project-endpoint "https://<resource>.services.ai.azure.com/api/projects/<project>" `
  --agent "https://my-agent.example.com/chat" `
  --dataset ".agentops/data/smoke.jsonl" `
  --no-prompt
```

For hosted agents, add the endpoint protocol fields:

```yaml
protocol: http-json
request_field: message
response_field: text
auth_header_env: HOSTED_AGENT_TOKEN
```

## 3. Decide the eval runner

```powershell
agentops workflow analyze --format text
```

Expected result:

| Agent target | Runner |
|---|---|
| `agent: name:version` | `official-ai-agent-evaluation` |
| `agent: https://...` | `agentops-local` |
| `agent: model:<deployment>` | `agentops-local` |

This is the key alignment rule. Foundry-native prompt agents use the official
runner where possible. AgentOps keeps the local path for hosted endpoints,
models, unsupported evaluator mappings, and repo-specific threshold evidence.

## 4. Run the first eval

For hosted agents or local fallback:

```powershell
agentops eval analyze
agentops eval run --output .agentops/results/manual-smoke
code .agentops/results/manual-smoke/report.md
```

For prompt agents, generate the workflow and let CI call the official runner:

```powershell
agentops workflow generate --kinds pr --force
```

The generated workflow prepares official eval input under:

```text
.agentops/official-eval/
```

and records release evidence after the gate.

## 5. Add CI/CD gates

Generate the common release path:

```powershell
agentops workflow generate --kinds pr,dev,qa,prod,watchdog --force
```

The generated workflows are intentionally boring:

- PR gate: evaluate and publish report/evidence.
- Dev/QA/Prod: deploy with azd or placeholders, then run readiness checks.
- Watchdog: run Doctor on a schedule and upload the report.

## 6. Wire observability

Foundry and Azure Monitor own live observability. AgentOps only checks whether
the repo and runtime are wired to those signals.

Set the Application Insights connection string in the active azd env:

```powershell
agentops init show --reveal-secrets
notepad .azure\dev\.env
```

The env file should include:

```text
APPLICATIONINSIGHTS_CONNECTION_STRING=InstrumentationKey=...
```

For custom hosted runtimes, install the `[agent]` extra and configure Azure
Monitor OpenTelemetry in the app startup. In Foundry, use the Observability
pages for trace drilldown, metrics, and Ask AI analysis.

## 7. Run Doctor and create release evidence

```powershell
agentops doctor --workspace . --evidence-pack
code .agentops\agent\report.md
code .agentops\release\latest\evidence.md
```

The evidence pack is not a second gate. It is a release summary over existing
signals:

- eval gate status;
- Doctor findings;
- CI/CD readiness;
- telemetry readiness;
- trace-regression status;
- links back to Foundry and Azure Monitor.

## 8. Run Foundry red-team scans

Red-team scans are a Foundry capability. Run them from Foundry Observability /
Red Teaming or the official Foundry SDK path. AgentOps does not create or run
managed red-team scans.

Use AgentOps for the repo-side follow-through:

1. Add safety/adversarial rows to your eval dataset when there are repeatable
   cases worth gating in CI.
2. Keep the Foundry red-team scan URL or summary with the release review.
3. Re-run Doctor and evidence:

```powershell
agentops doctor --workspace . --evidence-pack
```

Cockpit links back to Foundry Red Teaming so reviewers can drill into the
managed scan results.

## 9. Promote production traces into regression candidates

Export reviewed Foundry or Application Insights traces to JSON/JSONL. Preview
the conversion first:

```powershell
agentops eval promote-traces --source .agentops\traces\candidate-traces.jsonl
```

If the rows look useful, apply them:

```powershell
agentops eval promote-traces `
  --source .agentops\traces\candidate-traces.jsonl `
  --apply
```

This writes reviewable regression candidates under `.agentops/data/`. AgentOps
does not claim they are human-approved truth. They are candidates until the team
reviews and accepts them.

## 10. Open Cockpit

```powershell
agentops cockpit --workspace .
```

Use Cockpit as the local command center:

- Foundry connection and deep links;
- official eval or local eval gate status;
- Doctor findings;
- release evidence;
- local eval history;
- production telemetry snapshot;
- CI/CD workflow status;
- next actions.

## Completion checklist

You are ready for a release review when:

- The agent target is explicit in `agentops.yaml`.
- CI uses the expected runner for the target.
- Eval results or official eval metadata are attached to the workflow artifact.
- `agentops doctor --evidence-pack` writes `evidence.md`.
- Application Insights is connected or the evidence clearly says it is missing.
- Foundry red-team scans are linked or tracked as a release action.
- Trace learnings have a path back into regression candidates.
