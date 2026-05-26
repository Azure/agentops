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
projects that make the Operate story complete. The forks give the recording a
stable set of references while the official Foundry extension, Azure services,
and AgentOps workflow remain the actual runtime path.

| Repository | Workshop fork | Role in the journey |
|---|---|---|
| `Azure/agentops` | `placerda/agentops` | Provides endpoint evaluation, thresholds, `results.json`, Doctor, Cockpit, and evidence. |
| `microsoft/ai-agent-evals` | `placerda/ai-agent-evals` | Provides the Foundry prompt-agent gate contract used as contrast for why hosted endpoints use AgentOps local eval. |
| `microsoft/foundry-toolkit` | `placerda/foundry-toolkit` | Frames the Hosted Agent create/debug/deploy flow and the Operate handoff in VS Code. |
| `microsoft/azure-skills` | `placerda/azure-skills` | Shows where the Microsoft Foundry skill can guide hosted-agent CI/CD, observe, and trace-regression follow-through. |

## Journey you will exercise

| Step | Main tool | What you do | AgentOps role |
|---|---|---|---|
| Build the hosted agent | VS Code, Foundry Toolkit, Agent Framework, Agent Inspector, or `microsoft-foundry` skill | Build, debug, and expose a Travel Agent endpoint. | No ownership of scaffold/deploy. |
| Observe runtime | Foundry Operate, Azure Monitor, Application Insights | Confirm traces, latency, errors, and metrics exist. | Checks whether telemetry is wired. |
| Evaluate endpoint | AgentOps local runner | Invoke the URL and normalize results. | Primary eval path for hosted endpoints. |
| Review readiness | AgentOps Doctor and Cockpit | Check CI, eval, telemetry, evidence, and links. | Primary owner of repo-side release proof. |

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

For normal usage, prefer the published package above. For the recorded workshop
path, install from Paulo's fork so the CLI, generated workflows, and tutorial
steps stay in sync:

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

For the workshop narrative, keep
`https://github.com/placerda/foundry-toolkit` open alongside the official
extension. You do not install the extension from that fork; use it as the
reference point for the Operate handoff after Hosted Agent deploy: evaluation
gate, telemetry readiness, trace links, and release evidence.

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
| Application Insights connection string | optional later, for observability |

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

The wizard does not ask for App Insights. Later runtime commands such as eval,
Doctor, and Cockpit use the Foundry project endpoint to ask the Azure AI
Projects SDK for the App Insights resource attached to that Foundry project. If
discovery is unavailable and you want to force a value, run
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

The Foundry project endpoint lives in `.azure/dev/.env`, not in source control.
If you force an App Insights connection string later, it is saved there too.

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

## 6. Check the selected eval runner

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

## 7. Run a local eval

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

## 8. Force an endpoint regression, compare, then fix it

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

## 9. Generate CI and Doctor evidence

```powershell
agentops workflow generate --kinds pr,watchdog --force
agentops doctor --workspace . --evidence-pack
code .agentops\agent\report.md
code .agentops\release\latest\evidence.md
```

The generated PR gate runs `agentops eval run`. Before using that workflow in
GitHub Actions or Azure Pipelines, replace any localhost agent URL with the
deployed Foundry Hosted or cloud endpoint.

Open both Doctor outputs. The report explains the findings; the evidence pack
summarizes what a reviewer needs to decide whether the endpoint is releasable.
In a fresh quickstart, warnings about production telemetry, CI history, or trace
regression history are expected and useful: they show what remains before this
local endpoint becomes an operated service.

The watchdog workflow runs Doctor on a schedule so release evidence can include
recent readiness signals.

This is also where the `placerda/azure-skills` fork fits the story. AgentOps
generates the repo-side gate and evidence; the Microsoft Foundry skill is the
natural guidance layer to teach Copilot/agents how to connect Foundry Toolkit,
Azure Monitor, trace regression, and CI/CD readiness without making the tutorial
look self-contained inside AgentOps.

## 10. Open Cockpit

```powershell
agentops cockpit --workspace .
```

Cockpit shows the endpoint readiness, eval history, Doctor findings, telemetry
status, release evidence, CI/CD, and next actions.

## Success criteria

You are done when:

- The Travel Agent endpoint responds to `POST /chat`.
- `agentops workflow analyze` selects `agentops-local`.
- `agentops eval run` writes `results.json` and `report.md`.
- You forced the endpoint into regressed mode, compared it with the baseline,
  fixed it, and reran the comparison.
- `agentops doctor --evidence-pack` writes
  `.agentops/release/latest/evidence.md`.
- Cockpit opens and shows the local eval history plus Doctor readiness.
