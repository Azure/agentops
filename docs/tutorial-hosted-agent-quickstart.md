# Tutorial: Foundry Hosted Agent or HTTP Agent (sandbox → dev with PR gate)

Use this tutorial when the agent is reachable as an endpoint URL. The
example creates a small **Travel Agent** HTTP endpoint locally (your
**sandbox**), then shows how to swap in a deployed Foundry Hosted Agent
or cloud-hosted URL (your **dev** environment) for CI.

This path validates the AgentOps local route in a two-environment
arrangement:

- Foundry or your app platform owns hosting and runtime operations in
  each environment.
- AgentOps invokes the endpoint from CI, applies repo thresholds, writes
  normalized `results.json`, runs Doctor with `--severity-fail critical`
  so regressions block the PR, and produces release evidence.

The toolkit benefit is the same as the prompt-agent tutorial, adapted
for endpoint-based agents: you author and iterate against a local
sandbox, then let CI verify the deployed dev environment is still
healthy on every PR. Production-readiness gates (eval thresholds plus
Doctor critical findings) sit between you and a merge.

## Repository set used in this tutorial

This tutorial intentionally connects the hosted-agent path to the
Microsoft projects that make the Operate story complete. The official
Foundry extension, Azure services, and AgentOps workflow remain the
actual runtime path.

| Repository / skill | Role in the journey |
|---|---|
| `Azure/agentops` | Provides endpoint evaluation, thresholds, `results.json`, Doctor, Cockpit, and evidence. |
| `microsoft-foundry` skill (Copilot Chat) | External, not bundled with AgentOps. Demonstrates how a skill outside the AgentOps toolkit can guide Foundry hosted agent creation and Operate wiring. The tutorial gives a portal-first fallback because the skill is optional. |
| `microsoft/ai-agent-evals` | Reference for Foundry prompt-agent eval behavior; hosted endpoints use AgentOps local eval because CI must invoke your endpoint directly. |
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
| You can push to the tutorial GitHub repository and run GitHub Actions or Azure Pipelines. | The PR gate only runs after the repo is published. |
| GitHub CLI is authenticated with `gh auth login` if you use GitHub PR commands while testing CI. | The workflow handoff is smoother when repo, PR, and Actions access are already confirmed. |
| You can create a GitHub environment named `dev` and add Actions variables/secrets. | The generated workflow uses that environment for Azure auth, endpoint settings, and evaluator settings. |
| You can create an Entra app registration with federated credentials, or an admin is ready to provide the client ID, tenant ID, and subscription ID. | The workflow skill can wire OIDC cleanly; without this, CI cannot authenticate to Azure. |
| Copilot or your coding-agent CLI is signed in before you ask it to run AgentOps skills. | The skill handoff assumes an authenticated coding-agent session that can read the repo and propose GitHub/Azure setup steps. |

Unlike the Prompt Agent tutorial, this endpoint tutorial does not point the
generated PR workflow at `ai-agent-evals`. Hosted and HTTP agents are evaluated
through the AgentOps local runner because CI must invoke your endpoint, extract
the response, apply repo thresholds, and write the normalized `results.json`.

## Mental model: sandbox vs dev for hosted endpoints

Even though hosted/HTTP agents don't have Foundry-managed prompt versions
the way prompt agents do, the same **sandbox → dev → qa → prod** separation
applies. For this tutorial you will work with two of them:

| Environment | What it is in this tutorial | Purpose |
|---|---|---|
| **sandbox** | The local FastAPI endpoint on your machine (`http://127.0.0.1:8000`). For a more realistic setup, this can also be a Foundry Hosted Agent or ACA revision shared by the team (or per-stream/per-developer if your team prefers that). | Author-side experimentation. Iterate, regress, fix, and validate with `agentops eval run` locally. No shared-with-CI blast radius. |
| **dev** | A deployed Foundry Hosted Agent, Azure Container Apps revision, AKS service, or any HTTPS endpoint reachable from CI. | Team-shared environment. The PR workflow evaluates this URL to verify it is still healthy. Deploy workflows (or your existing CI) update it on merge. |

Each environment maps to its own `.azure/<env>/.env` file with its own
`TRAVEL_AGENT_ENDPOINT` (and optional Foundry project endpoint for
observability). The sandbox is the default; dev is added once the tutorial
moves into CI.

### The promotion identity for hosted agents

The prompt-agent tutorial uses **prompt SHA-256** + **git SHA** as the
cross-environment identity. Hosted agents don't have a `prompt_file`, so
the identity story is even simpler:

```
git commit SHA (and container image tag, if you containerize)
   │
   └─ cross-environment identity
        │
        ├── sandbox endpoint (your localhost or dev-machine deploy)
        ├── dev endpoint     (https://travel-agent-dev.example.com)
        ├── qa endpoint      (https://travel-agent-qa.example.com)
        └── prod endpoint    (https://travel-agent.example.com)
```

> **The cross-environment identifier for hosted agents is the git commit
> SHA, and (when you containerize) the image tag derived from it.** Each
> environment's endpoint URL changes; what you cite when traceability
> matters is the SHA that produced the deployed code. AgentOps records
> the git SHA in `.agentops/results/<timestamp>/results.json` and in
> release evidence, so the eval result and the source code stay linked
> across environments.

## Journey you will exercise

```
sandbox (local FastAPI)
   │  iterate, regress, fix locally with `agentops eval run`
   ▼
PR opened with code change
   │  PR workflow evaluates dev URL with --doctor-gate critical
   ▼
PR green ── merge ── deploy workflow updates dev endpoint
   │
   ▼
deploy workflow re-runs eval + Doctor against the freshly updated dev URL
   │
   ▼
green dev → ready for promotion to qa / prod
```

| Stage | Main tool | What you do | AgentOps role |
|---|---|---|---|
| Author + iterate | Your code editor + local FastAPI | Change endpoint behavior, run `agentops eval run` against `localhost`. | Local runner; baseline comparison. |
| Open PR | GitHub or Azure DevOps + generated PR workflow | PR workflow runs eval against the **dev URL** and Doctor with `--severity-fail critical`. | PR gate (eval thresholds + critical Doctor findings block merge). |
| Merge + deploy to dev | Your existing deploy pipeline (Foundry Toolkit, azd, ACA, AKS) + generated dev deploy workflow | Update the dev endpoint with the new commit and re-evaluate. | Deploy-time gate with the same `--severity-fail critical` (always strict on deploy). |
| Observe runtime | Foundry Operate, Azure Monitor, Application Insights | Confirm traces, latency, errors, and metrics exist. | Checks whether telemetry is wired. |
| Review readiness | AgentOps Doctor and Cockpit | Check CI, eval, telemetry, evidence, and links. | Primary owner of repo-side release proof. |

> **Architectural note.** For hosted endpoints the natural regression
> gate runs at **deploy time** (post-merge), not PR time. The PR
> workflow's eval verifies the dev URL is still healthy; it cannot
> evaluate the PR's *unmerged* code unless your CI does a per-PR
> ephemeral deploy. If you need PR-time regression catching for hosted
> agents, the workflow skill can guide you through adding a per-PR
> ephemeral deploy step (out of scope for this tutorial). The
> sandbox loop (local FastAPI + `agentops eval run`) is the
> equivalent author-side gate.

Observability needs an App Insights resource connected to the Foundry project or
agent runtime. If you ask Foundry to create or attach that resource from the
Traces view, your identity must have the required Azure permissions. The local
FastAPI sample below emits custom OpenTelemetry spans only after you enable the
observability step; a real Foundry Hosted Agent emits richer Foundry runtime
spans.

> **Name the Azure container resources up front.** If you use the
> `microsoft-foundry` skill or Foundry Toolkit to create a hosted-agent project,
> tell it the resource group, Foundry / AI Services resource name, region, and
> model deployment you want, for example `rg-agentops-travel-<your-alias>`,
> `foundry-agentops-travel-<your-alias>`, `East US 2`, and `gpt-4o-mini`.
> Replace `<your-alias>` with a short unique suffix when multiple people share
> the same subscription. Resource group names are unique within a subscription;
> Foundry / AI Services resource names should also be unique enough to avoid
> Azure naming conflicts. For a recorded tutorial, one shared resource group is
> easiest because RBAC and cleanup happen in one place; production teams may
> split resource groups by environment.

## 1. Create a clean workspace and install dependencies

```powershell
mkdir agentops-hosted-quickstart
cd agentops-hosted-quickstart
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install "agentops-accelerator[foundry,agent]" fastapi "uvicorn[standard]"
agentops --version
```

For normal usage, prefer the published package above. For this tutorial path,
install the aligned reference branch so the CLI, generated workflows, and
tutorial steps stay in sync:

```powershell
python -m pip install "agentops-accelerator[foundry,agent] @ git+https://github.com/Azure/agentops.git@develop"
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

### Grant data-plane access to your identity and Foundry managed identities

The local AI-assisted evaluators that AgentOps runs in step 8 call
chat-completions on the AI Services account that backs your Foundry
project. Creating a project through the portal only assigns you
`Foundry User` **at the project scope**, which does not cover the
OpenAI data-plane action on the parent account. Even subscription
`Owner` is insufficient: the built-in `Owner` role has `actions: ["*"]`
but `dataActions: []`. Skipping this once causes the eval to fail with
`PermissionDenied` on `Microsoft.CognitiveServices/accounts/OpenAI/
deployments/chat/completions/action`.

Run these assignments once per resource group hosting a Foundry account
you will evaluate against. Local AI-assisted evaluators use your identity,
while Foundry-hosted/server-side eval paths may use Azure AI managed
identities from the same resource group. Assigning only the user can still
leave server-side graders failing with `AuthenticationError`.

```powershell
$subscriptionId = az account show --query id -o tsv
$resourceGroup = "<resource-group>"
$scope = "/subscriptions/$subscriptionId/resourceGroups/$resourceGroup"
$userObjectId = az ad signed-in-user show --query id -o tsv

az role assignment create `
  --assignee $userObjectId `
  --role "Cognitive Services OpenAI User" `
  --scope $scope

az resource list -g $resourceGroup `
  --query "[?identity.principalId!=null].identity.principalId" -o tsv |
  ForEach-Object {
    az role assignment create `
      --assignee-object-id $_ `
      --assignee-principal-type ServicePrincipal `
      --role "Cognitive Services OpenAI User" `
      --scope $scope
  }
```

> **Give the assignment a few minutes to propagate.** Data-plane role
> assignments on the AI Services account do **not** take effect
> instantly — propagation to the local/Foundry evaluator workers can
> take several minutes (occasionally up to ~15). Evaluators authenticate
> per call, so the **first eval right after granting the role may show
> intermittent `AuthenticationError` on a subset of graders and report
> `Threshold status: FAILED` even when every threshold is green**. This
> is a grader execution failure, not a quality regression — wait a few
> minutes and re-run the eval.

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
`APPLICATIONINSIGHTS_CONNECTION_STRING` manually in `.agentops/.env`.

If the first run shows starter defaults such as `Agent [my-agent:1]` or
`Dataset path [.agentops/data/smoke.jsonl]`, replace them with the hosted Travel
Agent values above. Those defaults only come from the scaffolded starter file.

By default, local Azure values go to `.agentops/.env`. If this repo already uses
`azd`, or you want AgentOps to write to an azd env, run
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

The `.agentops/.env` file is intentional: AgentOps keeps local Azure values out
of source control while eval, Doctor, and Cockpit commands resolve the same
workspace environment. The Foundry project endpoint lives there instead of in
`agentops.yaml`; if you force an App Insights connection string later, it is
saved there too. Existing azd workspaces keep using `.azure/<env>/.env`.

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

Make sure the AgentOps local env has an App Insights connection string. If it
is not present yet, store the value once:

```powershell
agentops init --appinsights-connection-string "<connection-string>"
```

Load that value into the terminal that will run `uvicorn`:

```powershell
$env:APPLICATIONINSIGHTS_CONNECTION_STRING = (
  Get-Content .agentops\.env |
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

If you are demonstrating a real Foundry Hosted Agent instead of the local
FastAPI sample, spend a minute in the Foundry observability panels too:

| Foundry / Azure surface | What to show | Why it matters |
|---|---|---|
| Hosted agent / endpoint page | The deployed endpoint or agent reference that `agentops.yaml` calls. | Connects the repo target to the runtime being observed. |
| Agent Traces | A recent request, Trace ID, spans, input/output, metadata, latency, model call, tool calls, and conversation context when present. | Shows the richer Foundry-managed runtime trace that the local sample cannot emit. |
| Operate overview | Aggregate latency, failures, usage, and Ask AI when available. | Shows service health beyond one request. |
| Application Insights Logs | KQL for the same operation ID or trace ID. | Gives the raw Azure Monitor drilldown path. |

The transition is the same as the prompt-agent tutorial: Foundry and Azure
Monitor own live observability; AgentOps checks whether those signals are wired
into eval gates, Doctor findings, Cockpit, and release evidence.

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

That is expected. Foundry prompt agents can use AgentOps cloud eval in Foundry;
hosted endpoints use AgentOps local eval so the repo can invoke the endpoint,
normalize results, apply thresholds, and keep a stable `results.json` contract.

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

Generate both the PR and dev deploy workflows with `--doctor-gate critical`
so the PR template fails when Doctor reports critical regression findings.
For hosted agents, the auto-detection path resolves to a placeholder deploy
workflow (or `azd` if `azure.yaml` exists); you customize it with your
existing deploy steps later.

```powershell
agentops workflow generate `
  --kinds pr,dev `
  --doctor-gate critical `
  --force
agentops doctor --workspace . --evidence-pack
code .agentops\agent\report.md
code .agentops\release\latest\evidence.md
```

> **`--deploy-mode prompt-agent` does not apply to hosted endpoints.**
> That mode is specific to Foundry prompt agents (the stage-prompt-as-
> candidate flow). For hosted endpoints, `agentops workflow generate`
> auto-detects `azd` or falls back to a placeholder you customize with
> your existing deploy steps (Foundry Toolkit deploy, `azd deploy`,
> ACA revision update, AKS rollout, etc.).

> **`--doctor-gate critical` is the default and what this tutorial uses.**
> The PR workflow runs `agentops doctor --severity-fail critical`, which
> exits non-zero (and fails the PR check) when Doctor reports any
> critical finding. Use `--doctor-gate warning` to also fail on warnings
> during hardening sprints. Use `--doctor-gate none` to make Doctor
> advisory-only (the pre-`--doctor-gate` behavior). The deploy workflow
> already runs Doctor with `--severity-fail critical`; that part is not
> configurable because production gates should be strict.

`agentops doctor` can take a few minutes because it checks Azure auth, Foundry
discovery, Azure Monitor/App Insights, local eval history, and repo workflow
evidence. The terminal progress line should keep moving while those sources are
collected.

Read the output in this order: `AgentOps pre-flight` lists the local access and
telemetry-discovery checks, `Release readiness` is the readiness verdict,
`Findings` / `Finding summary` names the blocking or warning items, and the
evidence paths are the files to open. Warnings are advisory unless strict
pre-flight is enabled; `blocked` means the report has findings to review, not
that Doctor failed. If App Insights is already connected but AgentOps cannot
discover it, run `az login`, confirm Reader on the Foundry project resource
group, or set `APPLICATIONINSIGHTS_CONNECTION_STRING` explicitly.

Use this quick readout while presenting the terminal output:

| Output | How to explain it |
|---|---|
| `AgentOps pre-flight   4 ok` | The workspace, Azure auth, Foundry project, and App Insights discovery checks are all usable. |
| `Wrote` | The local Doctor diagnostic report was generated. |
| `Release readiness: blocked` | The command succeeded, but the current evidence has findings that block release readiness. |
| `Evidence pack` / `Evidence report` | These are the release-review artifacts to open or attach to the PR/release discussion. |
| `Findings: ...` | This is the severity rollup; critical items are what you discuss first. |
| `Finding summary` | This is the terminal triage list. For hosted endpoints, explain production latency/errors and eval regressions first, then treat workflow, threshold, RAI, and trace-regression warnings as hardening follow-ups. |

The useful story is the insight list, not the fact that a file was written.
For hosted endpoints, Doctor connects runtime signals and repo readiness: latency
or error findings point to production behavior, regression findings point to eval
quality loss, and operational findings point to the missing release machinery
such as deploy workflows, thresholds, continuous eval, action SHA pinning, and
trace-to-regression feedback. Use critical findings as release blockers and
warnings as the hardening backlog.

The generated PR gate runs `agentops eval run` against the dev endpoint URL.
Before using that workflow in GitHub Actions or Azure Pipelines, replace any
localhost agent URL with the deployed Foundry Hosted or cloud endpoint (set
`AGENTOPS_AGENT_ENDPOINT` as an Actions variable on the `dev` GitHub environment).
Have the Entra app-registration permission or the admin-provided OIDC values
ready before using a workflow skill to connect the repo to Azure.

With `--doctor-gate critical` set during workflow generation, the PR workflow's
Doctor step blocks the PR on critical findings (eval thresholds are *also* a
hard gate via the `agentops eval run` exit code). A green PR run means: the dev
endpoint passed eval thresholds **and** Doctor found nothing critical. A
blocked PR means one of those two gates flagged a problem; the PR comment and
the run summary include the Doctor finding summary so the author knows exactly
which findings to address. Use `--doctor-gate warning` if you want warnings to
block too, or `--doctor-gate none` to revert to the pre-`--doctor-gate`
advisory-only behavior. Production deploy workflows always run Doctor as a
critical release gate regardless of the PR setting.

### Add the dev environment to azd

The seed workspace created by `agentops init` lives under
`.azure/<sandbox-env>/.env`. For the CI flow to use a separate dev project (or
dev observability target), add a sibling env. AgentOps does this entirely on
the filesystem; no `azd` CLI required:

```powershell
New-Item -ItemType Directory -Force .azure\dev | Out-Null
@'
AZURE_AI_FOUNDRY_PROJECT_ENDPOINT=https://<dev-resource>.services.ai.azure.com/api/projects/<dev-project>
APPLICATIONINSIGHTS_CONNECTION_STRING=<dev-app-insights-connection-string>
AZURE_OPENAI_ENDPOINT=https://<dev-openai-resource>.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini
'@ | Set-Content -Encoding utf8 .azure\dev\.env
```

Keep `.azure/config.json` pointed at the sandbox env (`defaultEnvironment`) so
local commands default to sandbox; CI passes `--azd-env dev` (or sets the env
explicitly) so it uses dev. The Foundry project endpoint plus the agent URL
(`AGENTOPS_AGENT_ENDPOINT` set as an Actions variable on the `dev` GitHub
environment) together let CI evaluate the deployed dev endpoint and land
results in the dev observability target.

Use the same workflow-skill handoff pattern as the Prompt Agent tutorial, but
keep the scope to the hosted endpoint:

```powershell
agentops skills install --platform copilot --force
```

Then ask Copilot:

```text
Use the AgentOps workflow skill to get the generated PR gate running for this
hosted-agent project.

Create or connect the GitHub repo if needed, set AGENTOPS_AGENT_ENDPOINT in the
`dev` environment to the deployed HTTPS endpoint, wire Azure OIDC and required
Actions variables in the `dev` environment, and set any required endpoint token
as a secret. The PR gate uses --doctor-gate critical so the workflow blocks on
critical Doctor findings (regressions or other strict signals). Do not add
scheduled Doctor, QA, or production workflows yet. Show me the plan before
changing GitHub or Azure, and call out anything that needs owner/admin
permission.
```

Open both Doctor outputs. The report explains the findings; the evidence pack
summarizes what a reviewer needs to decide whether the endpoint is releasable.
In a fresh tutorial workspace, warnings about production telemetry, CI history, or trace
regression history are expected and useful: they show what remains before this
local endpoint becomes an operated service.

If you later want a separate cadence outside PRs, generate the optional Doctor
workflow with `agentops workflow generate --kinds doctor --force`.


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

- The Travel Agent endpoint responds to `POST /chat` in the sandbox
  (local FastAPI) and the dev environment (your deployed endpoint or a
  placeholder URL you plan to wire to a deploy workflow).
- At least one sandbox endpoint request appears in App Insights Logs
  with the `travel-agent.chat` operation. If you deploy as a real
  Foundry Hosted Agent in the dev project, its richer runtime spans can
  also appear in Foundry Traces.
- `agentops workflow analyze` selects `agentops-local`.
- `agentops eval run` writes `results.json` and `report.md`, and you
  forced the endpoint into regressed mode, compared it with the
  baseline, fixed it, and reran the comparison locally — proving the
  author-side gate works before opening a PR.
- The generated PR workflow uses `--severity-fail critical` for the
  `agentops doctor` step (set by `--doctor-gate critical` during
  `agentops workflow generate`), so a regression that lands in dev
  blocks the next PR until it is fixed.
- `.azure/` contains both a sandbox env (default) and a dev env, each
  with its own `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` and
  `APPLICATIONINSIGHTS_CONNECTION_STRING`.
- `agentops doctor --evidence-pack` writes
  `.agentops/release/latest/evidence.md`, and the workflow summary
  surfaces its Doctor finding summary.
- Cockpit opens and shows the local eval history plus Doctor readiness.
- Optional ASSERT, ACS, and red-team evidence artifacts are either absent
  (Doctor stays silent) or wired through `assert_path`, `acs_path`, and
  `redteam_path` in `agentops.yaml`. AgentOps cites their status/hash in release
  evidence; it does not execute ASSERT, apply ACS controls, or run red-team
  campaigns.

## Where to go next

- **Add per-PR regression catching for hosted agents.** Per-PR ephemeral
  deploys (e.g., ACA revision per PR, dedicated Foundry Hosted Agent per
  PR) are the architectural answer if you want PR-time eval to catch
  endpoint regressions before merge. The workflow skill can scaffold
  this.
- **Promote to qa and prod.** Mirror the dev pattern: create
  `.azure/qa/.env` and `.azure/prod/.env`, set GitHub Environments with
  the right `AGENTOPS_AGENT_ENDPOINT`, and use `agentops workflow
  generate --kinds qa,prod --force`.
- **Walk through the prompt-agent tutorial** at
  [tutorial-prompt-agent-quickstart.md](tutorial-prompt-agent-quickstart.md)
  to see the full prompt-as-code regression journey (stage-then-eval
  at PR time, no per-PR deploys required) and contrast the two
  architectures.
