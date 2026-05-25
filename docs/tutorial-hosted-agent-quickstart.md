# Quickstart: Foundry Hosted Agent or HTTP Agent

Use this quickstart when the agent is reachable as an endpoint URL. This covers
Foundry hosted agents, Azure Container Apps, AKS, LangGraph, LangChain, and
custom HTTP/JSON agents.

This path validates the AgentOps local route:

- Foundry or your app platform owns hosting and runtime operations.
- AgentOps invokes the endpoint from CI, applies repo thresholds, writes
  normalized `results.json`, and produces release evidence.

## Journey you will exercise

| Step | Main tool | What you do | AgentOps role |
|---|---|---|---|
| Build the hosted agent | VS Code, Foundry Toolkit, Agent Framework, Agent Inspector, or `microsoft-foundry` skill | Build, debug, and deploy the code-based agent. | No ownership of scaffold/deploy. |
| Observe runtime | Foundry Operate, Azure Monitor, Application Insights | Confirm traces, latency, errors, and metrics exist. | Checks whether telemetry is wired. |
| Evaluate endpoint | AgentOps local runner | Invoke the URL and normalize results. | Primary eval path for hosted endpoints. |
| Review readiness | AgentOps Doctor and Cockpit | Check CI, eval, telemetry, evidence, and links. | Primary owner of repo-side release proof. |

## 1. Build or select a Hosted Agent

Before running AgentOps, use the official development tools to get a working
endpoint:

1. Build the agent in VS Code with your framework of choice.
2. Use Foundry Toolkit or the `microsoft-foundry` skill to deploy it when that
   is the right lifecycle for the project.
3. Use Agent Inspector, local debugging, or the Foundry playground to verify
   tool calls and responses.
4. Confirm you have an endpoint URL and the request/response JSON shape.

AgentOps starts after the endpoint exists. Its job is to evaluate and prove
release readiness, not to replace the hosted-agent build/deploy tools.

## 2. Create a clean workspace

```powershell
mkdir C:\Users\paulolacerda\workspace\test-agentops-hosted
cd C:\Users\paulolacerda\workspace\test-agentops-hosted
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e "C:\Users\paulolacerda\workspace\agentops[foundry,agent]"
agentops --version
```

For a public install before the next package release, use:

```powershell
python -m pip install "agentops-toolkit[foundry,agent] @ git+https://github.com/Azure/agentops.git@main"
```

## 3. Capture endpoint values

You need:

| Value | Example |
|---|---|
| Agent endpoint | `https://my-agent.example.com/chat` |
| Request field | `message` |
| Response field | `text` |
| Bearer token env var | optional, for example `HOSTED_AGENT_TOKEN` |
| Foundry project endpoint | optional, but recommended for links and evaluators |
| Application Insights connection string | optional, but recommended |

If the endpoint needs a bearer token:

```powershell
$env:HOSTED_AGENT_TOKEN = "<token>"
```

## 4. Initialize AgentOps

```powershell
agentops init `
  --dir . `
  --azd-env dev `
  --project-endpoint "https://<resource>.services.ai.azure.com/api/projects/<project>" `
  --agent "https://my-agent.example.com/chat" `
  --dataset ".agentops/data/smoke.jsonl" `
  --no-prompt
```

Then edit `agentops.yaml` so AgentOps knows how to call your endpoint:

```yaml
version: 1
agent: https://my-agent.example.com/chat
dataset: .agentops/data/smoke.jsonl
project_endpoint: https://<resource>.services.ai.azure.com/api/projects/<project>
protocol: http-json
request_field: message
response_field: text
auth_header_env: HOSTED_AGENT_TOKEN
```

For a Foundry hosted endpoint that already follows the Responses API shape, use:

```yaml
protocol: responses
```

For a raw Foundry invocations endpoint, use:

```yaml
protocol: invocations
```

## 5. Check the selected eval runner

```powershell
agentops workflow analyze --format text
```

For hosted endpoints, AgentOps should recommend:

```text
recommended_eval_runner: agentops-local
```

That is expected. The official Microsoft AI Agent Evaluation runner is used for
Foundry prompt agents. Hosted agents use AgentOps local eval so the repo can
invoke the endpoint, normalize results, apply thresholds, and keep a stable
`results.json` contract.

## 6. Run a local eval

```powershell
agentops eval analyze
agentops eval run --output .agentops/results/manual-hosted-smoke
code .agentops/results/manual-hosted-smoke/report.md
```

The run writes:

```text
.agentops/results/manual-hosted-smoke/results.json
.agentops/results/manual-hosted-smoke/report.md
.agentops/results/latest/
```

## 7. Generate CI and Doctor evidence

```powershell
agentops workflow generate --kinds pr,watchdog --force
agentops doctor --workspace . --evidence-pack
code .agentops/release/latest/evidence.md
```

The generated PR gate runs `agentops eval run`. The watchdog workflow runs
Doctor on a schedule so release evidence can include recent readiness signals.

## 8. Open Cockpit

```powershell
agentops cockpit --workspace .
```

Cockpit shows the endpoint readiness, eval history, Doctor findings, telemetry
status, release evidence, CI/CD, and next actions.

## Success criteria

You are done when:

- `agentops workflow analyze` selects `agentops-local`.
- `agentops eval run` writes `results.json` and `report.md`.
- `agentops doctor --evidence-pack` writes
  `.agentops/release/latest/evidence.md`.
- Cockpit opens and shows the local eval history plus Doctor readiness.
