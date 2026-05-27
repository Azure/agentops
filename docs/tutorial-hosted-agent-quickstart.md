# Quickstart: Foundry Hosted Agent or HTTP Agent

Use this quickstart when the agent is reachable as an endpoint URL. The example
creates a small **Travel Agent** HTTP endpoint locally, then shows how to swap in
a Foundry-hosted or cloud-hosted URL for CI.

This path validates the AgentOps local route:

- Foundry or your app platform owns hosting and runtime operations.
- AgentOps invokes the endpoint from CI, applies repo thresholds, writes
  normalized `results.json`, and produces release evidence.

## Repository set used in this tutorial

This tutorial intentionally connects the hosted-agent path to the Microsoft
projects that make the Operate story complete. The official Foundry extension,
Azure services, and AgentOps workflow remain the actual runtime path.

| Repository | Role in the journey |
|---|---|
| `Azure/agentops` | Provides endpoint evaluation, thresholds, `results.json`, Doctor, Cockpit, and evidence. |
| `microsoft/ai-agent-evals` | Provides the Foundry prompt-agent gate contract used as contrast for why hosted endpoints use AgentOps local eval. |
| `microsoft/foundry-toolkit` | Frames the Hosted Agent create/debug/deploy flow and the Operate handoff in VS Code. |
| `microsoft/azure-skills` | Shows where the Microsoft Foundry skill can guide hosted-agent CI/CD, observe, and trace-regression follow-through. |
| `Azure-Samples/microsoft-foundry-e2e-agent-observability-workshop` | Reference for the Foundry Observe/Optimize/Protect loop: OpenTelemetry traces, App Insights, Operate Ask AI, evaluations, and red-team follow-through. |

## Before you run the tutorial

Do this once before a live walkthrough or guided session. The goal is to keep the
demo focused on the hosted-agent, observability, and AgentOps flow, not on
unexpected permission prompts.

| Check | Why it matters |
|---|---|
| Azure CLI is installed and `az login` succeeds with the tenant that owns the Foundry project. | AgentOps discovery, Doctor, Cockpit, and telemetry setup all use that Azure context. |
| You can create or use a Foundry project and a chat-capable Azure OpenAI deployment. | Local endpoint evals still need a judge model for quality scoring. |
| You can create or attach Application Insights, or you already have an App Insights connection string. | The local FastAPI sample emits OpenTelemetry spans only after telemetry is configured. |
| You can deploy or expose the hosted endpoint that CI will call. | `localhost` is fine for local eval, but GitHub Actions or Azure Pipelines need a reachable HTTPS URL. |
| You can push to the tutorial GitHub repository and run GitHub Actions or Azure Pipelines. | The PR gate and scheduled Doctor workflow only run after the repo is published. |
| GitHub CLI is authenticated with `gh auth login` if you use GitHub PR commands while testing CI. | The workflow handoff is smoother when repo, PR, and Actions access are already confirmed. |
| You can create a GitHub environment named `dev` and add Actions variables/secrets. | The generated workflow uses that environment for Azure auth, endpoint settings, and evaluator settings. |
| You can create an Entra app registration with federated credentials, or an admin is ready to provide the client ID, tenant ID, and subscription ID. | The workflow skill can wire OIDC cleanly; without this, CI cannot authenticate to Azure. |
| Copilot or your coding-agent CLI is signed in before you ask it to run AgentOps skills. | The skill handoff assumes an authenticated coding-agent session that can read the repo and propose GitHub/Azure setup steps. |

Unlike the Prompt Agent quickstart, this endpoint tutorial does not point the
generated PR workflow at `ai-agent-evals`. Hosted and HTTP agents are evaluated
through the AgentOps local runner because CI must invoke your endpoint, extract
the response, apply repo thresholds, and write the normalized `results.json`.

## Journey you will exercise

| Step | Main tool | What you do | AgentOps role |
|---|---|---|---|
| Create/deploy the hosted agent | VS Code, Foundry Toolkit, Agent Framework, Agent Inspector, or `microsoft-foundry` skill | Create, debug, and expose a Travel Agent endpoint. | No ownership of scaffold/deploy. |
| Observe runtime | Foundry Operate, Azure Monitor, Application Insights | Confirm traces, latency, errors, and metrics exist. | Checks whether telemetry is wired. |
| Evaluate endpoint | AgentOps local runner | Invoke the URL and normalize results. | Primary eval path for hosted endpoints. |
| Review readiness | AgentOps Doctor and Cockpit | Check CI, eval, telemetry, evidence, and links. | Primary owner of repo-side release proof. |

Observability needs an App Insights resource connected to the Foundry project or
agent runtime. If you ask Foundry to create or attach that resource from the
Traces view, your identity must have the required Azure permissions. The local
FastAPI sample below emits custom OpenTelemetry spans only after you enable the
observability step; a real Foundry Hosted Agent emits richer Foundry runtime
spans.

## 1. Create a clean workspace and install dependencies

```powershell
mkdir agentops-hosted-quickstart
cd agentops-hosted-quickstart
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install "agentops-toolkit[foundry,agent]" fastapi "uvicorn[standard]"
agentops --version
```

For normal usage, prefer the published package above. For this tutorial path,
install the aligned reference branch so the CLI, generated workflows, and
tutorial steps stay in sync:

```powershell
python -m pip install "agentops-toolkit[foundry,agent] @ git+https://github.com/placerda/agentops.git@develop"
```

## 2. Create the Travel Agent endpoint

Create a minimal HTTP agent with the same travel behavior you would later deploy
with Foundry Toolkit, Azure Container Apps, AKS, or another hosting path.

```powershell
@'
import os

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Travel Agent")


class ChatRequest(BaseModel):
    message: str


def plan_trip(message: str) -> str:
    if os.getenv("TRAVEL_AGENT_MODE") == "regressed":
        return "Travel depends on your preference. Search online and pick what looks best."

    text = message.lower()
    if "lisbon" in text:
        return (
            "Summary: Lisbon is a strong 3-day food and history trip. "
            "Day 1: Baixa, Chiado, and a sunset viewpoint. "
            "Day 2: Alfama, Sao Jorge Castle, and fado. "
            "Day 3: Belem, pastries, and a riverside walk. "
            "Notes: use transit, reserve popular restaurants early, and I cannot make live bookings."
        )
    if "seattle" in text:
        return (
            "Summary: Seattle can work well for a low-budget coffee and museum weekend. "
            "Day 1: Pike Place, waterfront, and independent coffee shops. "
            "Day 2: Museum of Pop Culture or Seattle Art Museum plus Capitol Hill. "
            "Notes: use transit, plan for rain, choose free viewpoints, and I cannot make live bookings."
        )
    if "tokyo" in text:
        return (
            "Summary: Tokyo with kids works best with short travel hops and flexible pacing. "
            "Plan: mix Ueno, Asakusa, Shibuya, teamLab or a science museum, parks, and one easy day trip. "
            "Notes: use IC transit cards, avoid overpacking each day, and I cannot make live bookings."
        )
    return (
        "Summary: I can help plan a short leisure trip. "
        "Please share the destination, trip length, budget, and traveler preferences. "
        "I cannot make live bookings."
    )


@app.post("/chat")
def chat(request: ChatRequest) -> dict[str, str]:
    return {"text": plan_trip(request.message)}
'@ | Set-Content -Encoding utf8 app.py
```

Start the endpoint in a second terminal:

```powershell
cd agentops-hosted-quickstart
.\.venv\Scripts\Activate.ps1
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

From the first terminal, test it:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/chat" `
  -ContentType "application/json" `
  -Body '{"message":"Plan a 3-day first-time trip to Lisbon for a couple who likes food and history."}'
```

For local validation, use:

```powershell
$env:TRAVEL_AGENT_ENDPOINT = "http://127.0.0.1:8000/chat"
```

### Make it a real Foundry Hosted Agent

For CI or a real Foundry Hosted Agent flow, deploy through the official Foundry
Toolkit path instead of leaving the endpoint on localhost:

1. Install the
   [Foundry Toolkit for Visual Studio Code](https://marketplace.visualstudio.com/items?itemName=TeamsDevApp.vscode-ai-foundry).
2. Confirm the Foundry project has a deployed model and the required Hosted
   Agent permissions for your user or project identity.
3. In VS Code, open the command palette and run
   `Microsoft Foundry: Create a New Hosted Agent`.
4. Choose a single-agent template, Python or C#, and the model deployment.
5. Replace the generated agent instructions or source logic with the Travel
   Agent behavior from this tutorial.
6. Press F5 to debug locally with Agent Inspector.
7. Run `Microsoft Foundry: Deploy Hosted Agent` from the command palette.
8. Copy the deployed endpoint URL from the Foundry Toolkit or Foundry portal.
9. Set:

   ```powershell
   $env:TRAVEL_AGENT_ENDPOINT = "https://<your-foundry-hosted-travel-agent-endpoint>"
   ```

The endpoint used in CI must be reachable by the CI runner. If the deployed
Foundry Hosted Agent follows the Responses API shape, use `protocol: responses`
later in `agentops.yaml`.

For the tutorial narrative, keep
`https://github.com/placerda/foundry-toolkit` open alongside the official
extension. You do not install the extension from that repository reference; use
it as the reference point for the Operate handoff after Hosted Agent deploy:
evaluation gate, telemetry readiness, trace links, and release evidence.

## 3. Create the travel eval dataset

```powershell
New-Item -ItemType Directory -Force .agentops\data | Out-Null
@'
{"input":"Plan a 3-day first-time trip to Lisbon for a couple who likes food and history.","expected":"A concise 3-day Lisbon itinerary with food, history, neighborhoods such as Baixa, Alfama, and Belem, practical notes, and no claim to make live bookings."}
{"input":"Suggest a low-budget weekend in Seattle for a solo traveler who likes coffee and museums.","expected":"A practical weekend Seattle plan with low-budget choices, coffee and museum suggestions, transit or weather notes, and no claim to make live bookings."}
{"input":"I want to visit Tokyo for 5 days with two kids. What should we do?","expected":"A family-friendly 5-day Tokyo itinerary with kid-appropriate activities, transit and pacing notes, and no claim to make live bookings."}
'@ | Set-Content -Encoding utf8 .agentops\data\travel-smoke.jsonl
```

## 4. Capture Foundry and endpoint values

You need:

| Value | Example |
|---|---|
| Agent endpoint | `http://127.0.0.1:8000/chat` for local validation, or `https://<your-hosted-agent>/chat` for CI |
| Request field | `message` |
| Response field | `text` |
| Bearer token env var | optional, for example `HOSTED_AGENT_TOKEN` |
| Foundry project endpoint | optional, but recommended for links and evaluators |
| Azure OpenAI endpoint | `https://<resource>.openai.azure.com`, used later by local AI-assisted evaluators |
| Evaluator model deployment | `gpt-4o-mini`, used later by local AI-assisted evaluators |
| Application Insights connection string | recommended for observability and Doctor links |

If the deployed endpoint needs a bearer token:

```powershell
$env:HOSTED_AGENT_TOKEN = "<token>"
```

## 5. Initialize AgentOps interactively

```powershell
agentops init
```

Answer the prompts as the wizard asks them:

| Prompt | Answer |
|---|---|
| Foundry project endpoint | `https://<resource>.services.ai.azure.com/api/projects/<project>`, or press Enter if you are only testing the local endpoint |
| Agent | The value in `$env:TRAVEL_AGENT_ENDPOINT`, for example `http://127.0.0.1:8000/chat` |
| Dataset path | `.agentops/data/travel-smoke.jsonl` |

The wizard does not ask for App Insights. Later runtime commands try to discover
the connected App Insights resource through the Azure AI Projects SDK. If the
project has no resource attached, or your identity cannot read it, run
`agentops init --appinsights-connection-string "<connection-string>"` or set
`APPLICATIONINSIGHTS_CONNECTION_STRING` manually in `.azure/dev/.env`.

If the first run shows starter defaults such as `Agent [my-agent:1]` or
`Dataset path [.agentops/data/smoke.jsonl]`, replace them with the hosted Travel
Agent values above. Those defaults only come from the scaffolded starter file.

If you want an azd environment name other than the default `dev`, run
`agentops init --azd-env <name>`.

Then edit `agentops.yaml` so AgentOps knows how to call the endpoint:

```yaml
version: 1
agent: http://127.0.0.1:8000/chat
dataset: .agentops/data/travel-smoke.jsonl
protocol: http-json
request_field: message
response_field: text
```

The `.azure/dev/.env` file is intentional: AgentOps uses the same environment
layout as `azd`, so local Azure values stay out of source control while eval,
Doctor, and Cockpit commands resolve the same active environment. The Foundry
project endpoint lives there instead of in `agentops.yaml`; if you force an App
Insights connection string later, it is saved there too.

For a deployed endpoint protected by a bearer token, add:

```yaml
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

## 6. Observe the endpoint in App Insights

The local FastAPI endpoint is useful for the AgentOps eval loop, but it is not a
Foundry-managed runtime. To make the observability story concrete, add
OpenTelemetry spans that flow to the same App Insights backend Foundry uses for
trace drilldown.

Install the Azure Monitor OpenTelemetry distro when you reach this step:

```powershell
python -m pip install azure-monitor-opentelemetry
```

Make sure the active azd env has an App Insights connection string. If it is not
present yet, store the value once:

```powershell
agentops init --appinsights-connection-string "<connection-string>"
```

Load that value into the terminal that will run `uvicorn`:

```powershell
$env:APPLICATIONINSIGHTS_CONNECTION_STRING = (
  Get-Content .azure\dev\.env |
  Where-Object { $_ -like "APPLICATIONINSIGHTS_CONNECTION_STRING=*" } |
  Select-Object -First 1
) -replace "^APPLICATIONINSIGHTS_CONNECTION_STRING=", ""
```

Open `app.py` and add these imports after `import os`:

```python
from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import trace
```

Add this after `app = FastAPI(title="Travel Agent")`:

```python
if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    configure_azure_monitor()

tracer = trace.get_tracer("agentops.travel-agent")
```

Replace the `/chat` handler with:

```python
@app.post("/chat")
def chat(request: ChatRequest) -> dict[str, str]:
    with tracer.start_as_current_span("travel-agent.chat") as span:
        mode = os.getenv("TRAVEL_AGENT_MODE", "normal")
        span.set_attribute("travel.agent.mode", mode)
        span.set_attribute("travel.query.length", len(request.message))
        response_text = plan_trip(request.message)
        span.set_attribute("travel.response.length", len(response_text))
        return {"text": response_text}
```

Restart the server and replay the dataset prompts:

```powershell
@(
  "Plan a 3-day first-time trip to Lisbon for a couple who likes food and history.",
  "Suggest a low-budget weekend in Seattle for a solo traveler who likes coffee and museums.",
  "I want to visit Tokyo for 5 days with two kids. What should we do?"
) | ForEach-Object {
  Invoke-RestMethod `
    -Method Post `
    -Uri $env:TRAVEL_AGENT_ENDPOINT `
    -ContentType "application/json" `
    -Body (@{ message = $_ } | ConvertTo-Json)
}
```

Then open Application Insights **Logs** and wait 2-5 minutes if the telemetry is
not visible immediately. For the local FastAPI sample, look for the
`travel-agent.chat` operation and the custom attributes in `customDimensions`:

```kusto
union traces, requests, dependencies
| where timestamp > ago(1h)
| where operation_Name has "travel-agent" or tostring(customDimensions["travel.agent.mode"]) != ""
| project timestamp, itemType, operation_Id, operation_Name, message, customDimensions
| order by timestamp desc
```

Those attributes are tutorial conventions, not special Foundry fields. A
deployed Foundry Hosted Agent uses the same App Insights backend and Foundry
trace surface, but its runtime spans include richer agent, tool, model, and
conversation semantics that the local FastAPI sample does not produce.

## 7. Check the selected eval runner

```powershell
agentops workflow analyze --format text
```

For hosted endpoints, AgentOps should recommend:

```text
Recommendation
  deploy          placeholder
  evaluate        AgentOps local eval
  workflow edits  needed - review project-specific build/deploy steps
  Copilot skills  installed - available for workflow adaptation handoff
```

That is expected. The official Microsoft AI Agent Evaluation runner is used for
Foundry prompt agents. Hosted agents use AgentOps local eval so the repo can
invoke the endpoint, normalize results, apply thresholds, and keep a stable
`results.json` contract.

## 8. Run a local eval

Local AI-assisted evaluators need a judge model deployment. This is separate
from `agentops init`: initialization captures the workspace target, while this
environment configuration tells the evaluator which model to use.

```powershell
$env:AZURE_OPENAI_ENDPOINT = "https://<resource>.openai.azure.com"
$env:AZURE_OPENAI_DEPLOYMENT = "gpt-4o-mini"
```

```powershell
agentops eval analyze
agentops eval run --output .agentops\results\manual-hosted-smoke
code .agentops\results\manual-hosted-smoke\report.md
```

The run writes:

```text
.agentops/results/manual-hosted-smoke/results.json
.agentops/results/manual-hosted-smoke/report.md
.agentops/results/latest/
```

## 9. Force an endpoint regression, compare, then fix it

The sample endpoint includes a deliberate regression switch. Stop the server in
the second terminal, restart it in regressed mode, and run a comparison against
the good baseline:

```powershell
$env:TRAVEL_AGENT_MODE = "regressed"
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

From the first terminal:

```powershell
agentops eval run `
  --baseline .agentops\results\manual-hosted-smoke `
  --output .agentops\results\regressed-hosted
code .agentops\results\regressed-hosted\report.md
```

The report should show that the vague response lost quality against the travel
dataset. Now stop the server, remove the regression switch, restart it, and run
the comparison again:

```powershell
Remove-Item Env:\TRAVEL_AGENT_MODE -ErrorAction SilentlyContinue
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

```powershell
agentops eval run `
  --baseline .agentops\results\regressed-hosted `
  --output .agentops\results\fixed-hosted
code .agentops\results\fixed-hosted\report.md
```

This is the core AgentOps loop for hosted endpoints: keep a stable dataset,
compare a changed runtime against the last known result, fix the agent, and
rerun the same gate before a PR or release.

## 10. Generate CI and Doctor evidence

```powershell
agentops workflow generate --kinds pr,watchdog --force
agentops doctor --workspace . --evidence-pack
code .agentops\agent\report.md
code .agentops\release\latest\evidence.md
```

The generated PR gate runs `agentops eval run`. Before using that workflow in
GitHub Actions or Azure Pipelines, replace any localhost agent URL with the
deployed Foundry Hosted or cloud endpoint. Have the Entra app-registration
permission or the admin-provided OIDC values ready before using a workflow skill
to connect the repo to Azure.

Use the same workflow-skill handoff pattern as the Prompt Agent quickstart, but
keep the scope to the hosted endpoint:

```powershell
agentops skills install --platform copilot
```

Then ask Copilot:

```text
Use the AgentOps workflow skill to get the generated PR gate and scheduled
Doctor workflow running for this hosted-agent project.

Create or connect the GitHub repo if needed, replace the localhost agent URL
with the deployed HTTPS endpoint, wire Azure OIDC and required Actions variables
in the `dev` environment, and set any required endpoint token as a secret. Show
me the plan before changing GitHub or Azure, and call out anything that needs
owner/admin permission.
```

Open both Doctor outputs. The report explains the findings; the evidence pack
summarizes what a reviewer needs to decide whether the endpoint is releasable.
In a fresh quickstart, warnings about production telemetry, CI history, or trace
regression history are expected and useful: they show what remains before this
local endpoint becomes an operated service.

The scheduled Doctor workflow runs on a cadence so release evidence can include
recent readiness signals.

This is also where `placerda/azure-skills` fits the story. AgentOps
generates the repo-side gate and evidence; the Microsoft Foundry skill is the
natural guidance layer to teach Copilot/agents how to connect Foundry Toolkit,
Azure Monitor, trace regression, and CI/CD readiness without making the tutorial
look self-contained inside AgentOps.

## 11. Open Cockpit

```powershell
agentops cockpit --workspace .
```

Cockpit shows the endpoint readiness, eval history, Doctor findings, telemetry
status, release evidence, CI/CD, and next actions.

## Success criteria

You are done when:

- The Travel Agent endpoint responds to `POST /chat`.
- At least one local endpoint request appears in App Insights Logs with the
  `travel-agent.chat` operation. If you deploy as a real Foundry Hosted Agent,
  its richer runtime spans can also appear in Foundry Traces.
- `agentops workflow analyze` selects `agentops-local`.
- `agentops eval run` writes `results.json` and `report.md`.
- You forced the endpoint into regressed mode, compared it with the baseline,
  fixed it, and reran the comparison.
- `agentops doctor --evidence-pack` writes
  `.agentops/release/latest/evidence.md`.
- Cockpit opens and shows the local eval history plus Doctor readiness.
