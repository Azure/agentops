# Tutorial: HTTP agent on Azure Container Apps

This tutorial builds a real HTTP tool-calling agent, deploys it to Azure
Container Apps, evaluates it with AgentOps, adds a GitHub Actions PR gate,
and runs the Watchdog analyzer over the produced eval history.

The important idea is that AgentOps does not care which framework hosts
your agent. For HTTP targets it needs only:

1. A URL to call.
2. The JSON field that receives the user message.
3. The JSON field that contains the final response.
4. Optionally, the JSON field that contains structured tool calls.

## What you will build

- A FastAPI customer-support agent with two real Python tools:
  `lookup_order` and `refund_order`.
- A Docker image deployed to Azure Container Apps.
- An `agentops.yaml` pointing to the public Container Apps URL.
- A JSONL dataset that checks both final answers and tool-call behavior.
- A passing local eval, a PR workflow, and a Watchdog report.

## Prerequisites

```powershell
az login
gh auth login

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install "agentops-toolkit[foundry,agent]"

$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://<resource>.services.ai.azure.com/api/projects/<project>"
$env:AZURE_OPENAI_ENDPOINT             = "https://<resource>.openai.azure.com"
$env:AZURE_OPENAI_DEPLOYMENT           = "gpt-4o-mini"
```

`AZURE_AI_MODEL_DEPLOYMENT_NAME` is accepted as a fallback name for the
judge deployment. Set only one of the two — `AZURE_OPENAI_DEPLOYMENT`
wins when both are set.

AgentOps is installed from the `develop` branch in this tutorial because
the 1.0 tutorial surface is still being tested before the PyPI release.

## 1. Create the HTTP agent

Create `app.py`:

```python
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel


app = FastAPI(title="AgentOps Support Tools Agent")


class ChatRequest(BaseModel):
    message: str


def lookup_order(order_id: str) -> dict[str, str]:
    status = {
        "ORD-12345": "in transit and expected to arrive tomorrow",
        "ORD-99001": "shipped yesterday and is waiting for carrier pickup",
    }.get(order_id, "not found")
    return {"order_id": order_id, "status": status}


def refund_order(order_id: str, reason: str) -> dict[str, str]:
    return {"order_id": order_id, "status": "refund_started", "reason": reason}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat")
def chat(request: ChatRequest) -> dict[str, object]:
    message = request.message

    if "ORD-12345" in message or "ORD-99001" in message:
        order_id = "ORD-12345" if "ORD-12345" in message else "ORD-99001"
        result = lookup_order(order_id)
        return {
            "text": f"Order {order_id} is {result['status']}.",
            "tool_calls": [
                {
                    "type": "tool_call",
                    "tool_call_id": "lookup_1",
                    "name": "lookup_order",
                    "arguments": {"order_id": order_id},
                }
            ],
        }

    if "refund" in message.lower() and "ORD-77821" in message:
        result = refund_order("ORD-77821", "arrived broken")
        return {
            "text": "I started a refund for ORD-77821 because it arrived broken.",
            "tool_calls": [
                {
                    "type": "tool_call",
                    "tool_call_id": "refund_1",
                    "name": "refund_order",
                    "arguments": {
                        "order_id": result["order_id"],
                        "reason": result["reason"],
                    },
                }
            ],
        }

    return {
        "text": "Hello! I can help with order status, refunds, or connecting you to support.",
        "tool_calls": [],
    }
```

This is intentionally small but not fake: the agent has a real request
contract, real tool functions, and returns the structured tool trace that
AgentOps can grade.

Create `requirements.txt`:

```text
fastapi==0.115.14
uvicorn[standard]==0.35.0
pydantic==2.11.9
```

Create `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

## 2. Deploy to Azure Container Apps

Choose names once:

```powershell
$env:AZURE_LOCATION = "eastus2"
$env:AZURE_RESOURCE_GROUP = "rg-agentops-http-tutorial"
$env:ACA_NAME = "agentops-http-agent-$((Get-Date).ToString('MMddHHmm'))"
```

Deploy the container:

```powershell
az group create `
  --name $env:AZURE_RESOURCE_GROUP `
  --location $env:AZURE_LOCATION

az containerapp up `
  --name $env:ACA_NAME `
  --resource-group $env:AZURE_RESOURCE_GROUP `
  --location $env:AZURE_LOCATION `
  --source . `
  --target-port 8000 `
  --ingress external

$fqdn = az containerapp show `
  --name $env:ACA_NAME `
  --resource-group $env:AZURE_RESOURCE_GROUP `
  --query properties.configuration.ingress.fqdn `
  -o tsv

$agentUrl = "https://$fqdn/chat"
$agentUrl
```

Smoke-test the deployed service:

```powershell
Invoke-RestMethod -Uri "https://$fqdn/health"
Invoke-RestMethod `
  -Uri $agentUrl `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"message":"I want a refund for ORD-77821, it arrived broken."}'
```

## 3. Initialize AgentOps

```powershell
agentops init
```

This creates `.agentops/`, a starter `agentops.yaml`, and coding-agent
skills under `.github/skills/`. The skills are guidance for Copilot or
another coding agent; they are not the Watchdog runtime.

## 4. Configure the HTTP eval

Replace `agentops.yaml`:

```powershell
@"
version: 1
agent: "$agentUrl"
dataset: .agentops/data/http-support-tools.jsonl

request_field: message
response_field: text
tool_calls_field: tool_calls

thresholds:
  coherence: ">=3"
  fluency: ">=3"
  tool_call_accuracy: ">=0.8"
  intent_resolution: ">=3"
  task_adherence: ">=0.6"
  avg_latency_seconds: "<=30"
"@ | Set-Content agentops.yaml -Encoding utf8
```

The field mapping is the HTTP contract:

| Config field | Meaning |
|---|---|
| `request_field: message` | AgentOps sends `{"message": "<row input>"}`. |
| `response_field: text` | AgentOps reads the natural-language answer from `response.text`. |
| `tool_calls_field: tool_calls` | AgentOps reads the structured tool trace from `response.tool_calls`. |

If your real endpoint is protected, add `auth_header_env: AGENT_TOKEN`
and set that environment variable before running the eval.

## 5. Create the tool-calling dataset

Create `.agentops/data/http-support-tools.jsonl`:

```powershell
New-Item -ItemType Directory -Force .agentops/data | Out-Null
@'
{"input":"Where is my order ORD-12345?","expected":"Order ORD-12345 is in transit and expected to arrive tomorrow.","tool_definitions":[{"type":"function","name":"lookup_order","description":"Look up an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"}},"required":["order_id"]}},{"type":"function","name":"refund_order","description":"Refund an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"},"reason":{"type":"string"}},"required":["order_id","reason"]}}],"tool_calls":[{"type":"tool_call","tool_call_id":"lookup_1","name":"lookup_order","arguments":{"order_id":"ORD-12345"}}]}
{"input":"I want a refund for ORD-77821, it arrived broken.","expected":"A refund is started for ORD-77821 because it arrived broken.","tool_definitions":[{"type":"function","name":"lookup_order","description":"Look up an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"}},"required":["order_id"]}},{"type":"function","name":"refund_order","description":"Refund an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"},"reason":{"type":"string"}},"required":["order_id","reason"]}}],"tool_calls":[{"type":"tool_call","tool_call_id":"refund_1","name":"refund_order","arguments":{"order_id":"ORD-77821","reason":"arrived broken"}}]}
{"input":"Hi there!","expected":"The assistant replies with a clear greeting and offers support options without calling a tool.","tool_definitions":[{"type":"function","name":"lookup_order","description":"Look up an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"}},"required":["order_id"]}},{"type":"function","name":"refund_order","description":"Refund an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"},"reason":{"type":"string"}},"required":["order_id","reason"]}}],"tool_calls":[]}
'@ | Set-Content .agentops/data/http-support-tools.jsonl -Encoding utf8
```

Each row is self-contained. The expected `tool_calls` define what the
agent should do, and `tool_definitions` define the tool catalogue the
evaluator uses to judge selection and arguments.

## 6. Run the eval

```powershell
agentops eval analyze
agentops eval run
```

For HTTP agents, `eval analyze` is useful before the first run because it calls
out response-field mapping and dataset-shape issues before the endpoint becomes
a CI gate.

Expected outputs:

```text
.agentops/results/<timestamp>/results.json
.agentops/results/<timestamp>/report.md
.agentops/results/latest/results.json
.agentops/results/latest/report.md
```

Open the report:

```powershell
code .agentops/results/latest/report.md
```

The report should show text-quality metrics plus tool metrics such as
`tool_call_accuracy`, `intent_resolution`, and `task_adherence`.

## 7. Add a PR evaluation gate

For a tutorial or a new repo, generate only the PR gate until Azure OIDC
and either `azure.yaml` or real deploy commands are configured. This
avoids the common mistake of pushing DEV/QA/PROD deploy workflows that
immediately fail on `main`.

```powershell
agentops workflow analyze
agentops workflow generate --kinds pr --force
```

Configure the `dev` GitHub Environment variables used by
`.github/workflows/agentops-pr.yml`:

```powershell
$repo = "<owner>/<repo>"

gh api -X PUT "repos/$repo/environments/dev" | Out-Null
gh variable set AZURE_CLIENT_ID --repo $repo --env dev --body "<app-registration-client-id>"
gh variable set AZURE_TENANT_ID --repo $repo --env dev --body "<tenant-id>"
gh variable set AZURE_SUBSCRIPTION_ID --repo $repo --env dev --body "<subscription-id>"
gh variable set AZURE_AI_FOUNDRY_PROJECT_ENDPOINT --repo $repo --env dev --body $env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
gh variable set AZURE_OPENAI_ENDPOINT --repo $repo --env dev --body $env:AZURE_OPENAI_ENDPOINT
gh variable set AZURE_OPENAI_DEPLOYMENT --repo $repo --env dev --body $env:AZURE_OPENAI_DEPLOYMENT
```

On the Azure app registration, add a federated credential for:

```text
repo:<owner>/<repo>:environment:dev
```

Then open a PR and verify the `AgentOps PR` workflow is green before
merging.

## 8. Run the Watchdog analyzer

Watchdog is a runtime analyzer, not a coding-agent skill. The
`agentops-agent` skill only tells Copilot how to call it.

Start with results-history analysis:

```powershell
agentops doctor --severity-fail critical
code .agentops/agent/report.md
```

If you also want Azure Monitor data, create an Application Insights
resource and point `.agentops/agent.yaml` at it:

```powershell
$appInsightsName = "$env:ACA_NAME-ai"
$appInsightsId = az monitor app-insights component create `
  --app $appInsightsName `
  --location $env:AZURE_LOCATION `
  --resource-group $env:AZURE_RESOURCE_GROUP `
  --application-type web `
  --query id `
  -o tsv

@"
version: 1
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
"@ | Set-Content .agentops/agent.yaml -Encoding utf8

agentops doctor --severity-fail critical
```

The Watchdog report lists which sources ran and which were skipped. Do
not treat skipped telemetry sources as success; wire them before relying
on production-health conclusions.

## Troubleshooting

| Symptom | What to check |
|---|---|
| `connection refused` | You are still pointing at a local URL or the container app has no external ingress. |
| `Response field 'text' not found` | Update `response_field` to match the JSON response. |
| Tool metrics are missing | Add `tool_calls_field` and return structured tool calls from the endpoint. |
| GitHub Action fails in `azure/login` | Create the GitHub `dev` environment variables and the Azure federated credential before pushing the workflow. |
| AI evaluator auth fails | Confirm OIDC role assignments or run `az login` locally. |

## Cleanup

```powershell
az group delete --name $env:AZURE_RESOURCE_GROUP --yes --no-wait
```
