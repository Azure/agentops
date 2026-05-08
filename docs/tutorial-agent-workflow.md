# Tutorial: Build and evaluate a real tool-calling agent

This tutorial is the tool-calling companion to the HTTP tutorial. You
will build an agent that chooses between support tools, deploy it to
Azure Container Apps, evaluate both the final answer and the tool trace,
and add a CI gate.

Use this tutorial when you care about questions such as:

- Did the agent call the right tool?
- Did it pass the right arguments?
- Did it avoid tools when the user only said hello?
- Did tool quality regress in a pull request?

## How AgentOps grades tool workflows

AgentOps uses normal answer-quality metrics plus tool-specific metrics
when the dataset includes `tool_calls` or `tool_definitions`.

| Dataset field | Purpose |
|---|---|
| `tool_definitions` | Tool catalogue available to the agent. Include it on every JSONL row so each row is self-contained. |
| `tool_calls` | Expected tool trace: tool name, call id, and arguments. |
| `input` | User message sent to the agent. |
| `expected` | Reference final answer. |

For HTTP agents, the response also needs a field that contains the
actual tool trace. In this tutorial that field is `tool_calls`.

## 1. Create the support-agent project

```powershell
mkdir support-tools-agent
Set-Location support-tools-agent

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install "agentops-toolkit[foundry,agent] @ git+https://github.com/Azure/agentops.git@develop"
```

Create the same FastAPI tool-calling agent used by the HTTP tutorial:

```powershell
@'
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
'@ | Set-Content app.py -Encoding utf8

@'
fastapi==0.115.14
uvicorn[standard]==0.35.0
pydantic==2.11.9
'@ | Set-Content requirements.txt -Encoding utf8

@'
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
'@ | Set-Content Dockerfile -Encoding utf8
```

The implementation is simple enough to inspect but still has the core
production contract: request JSON, business tools, final answer, and
structured tool trace.

## 2. Deploy the agent to Azure

```powershell
az login

$env:AZURE_LOCATION = "eastus2"
$env:AZURE_RESOURCE_GROUP = "rg-agentops-tools-tutorial"
$env:ACA_NAME = "agentops-tools-$((Get-Date).ToString('MMddHHmm'))"

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
Invoke-RestMethod -Uri "https://$fqdn/health"
```

This step matters because a tool workflow eval should exercise the same
HTTP boundary your production clients use, not a local-only shortcut.

## 3. Initialize AgentOps

```powershell
agentops init

$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://<resource>.services.ai.azure.com/api/projects/<project>"
$env:AZURE_OPENAI_ENDPOINT             = "https://<resource>.openai.azure.com"
$env:AZURE_OPENAI_DEPLOYMENT           = "gpt-4o-mini"
$env:AZURE_AI_MODEL_DEPLOYMENT_NAME    = "gpt-4o-mini"
```

## 4. Write `agentops.yaml`

```powershell
@"
version: 1
agent: "$agentUrl"
dataset: .agentops/data/support-tools.jsonl

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

Why each threshold exists:

| Threshold | What it protects |
|---|---|
| `coherence`, `fluency` | The final answer remains readable. |
| `tool_call_accuracy` | The tool name and arguments match the expected trace. |
| `intent_resolution` | The agent understood the user's task. |
| `task_adherence` | The agent did not drift away from the requested action. |
| `avg_latency_seconds` | The deployed endpoint stays responsive. |

## 5. Create the dataset

```powershell
New-Item -ItemType Directory -Force .agentops/data | Out-Null
@'
{"input":"Where is my order ORD-12345?","expected":"Order ORD-12345 is in transit and expected to arrive tomorrow.","tool_definitions":[{"type":"function","name":"lookup_order","description":"Look up an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"}},"required":["order_id"]}},{"type":"function","name":"refund_order","description":"Refund an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"},"reason":{"type":"string"}},"required":["order_id","reason"]}}],"tool_calls":[{"type":"tool_call","tool_call_id":"lookup_1","name":"lookup_order","arguments":{"order_id":"ORD-12345"}}]}
{"input":"I want a refund for ORD-77821, it arrived broken.","expected":"A refund is started for ORD-77821 because it arrived broken.","tool_definitions":[{"type":"function","name":"lookup_order","description":"Look up an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"}},"required":["order_id"]}},{"type":"function","name":"refund_order","description":"Refund an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"},"reason":{"type":"string"}},"required":["order_id","reason"]}}],"tool_calls":[{"type":"tool_call","tool_call_id":"refund_1","name":"refund_order","arguments":{"order_id":"ORD-77821","reason":"arrived broken"}}]}
{"input":"Hi there!","expected":"The assistant replies with a clear greeting and offers support options without calling a tool.","tool_definitions":[{"type":"function","name":"lookup_order","description":"Look up an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"}},"required":["order_id"]}},{"type":"function","name":"refund_order","description":"Refund an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"},"reason":{"type":"string"}},"required":["order_id","reason"]}}],"tool_calls":[]}
'@ | Set-Content .agentops/data/support-tools.jsonl -Encoding utf8
```

The third row is as important as the first two. It asserts that greeting
messages should not call a business tool.

## 6. Run and inspect the eval

```powershell
agentops eval run
code .agentops/results/latest/report.md
```

The report should include:

- Aggregate metric values.
- Threshold pass/fail status.
- Per-row tool traces.
- The latency of calls to the deployed Container App.

If the tool-call metrics fail, inspect the row in the report before
changing thresholds. Usually the bug is an incorrect tool name, missing
argument, or response mapping mismatch.

## 7. Add a PR gate

```powershell
agentops workflow generate --kinds pr --force
```

Use PR-only first. Generate DEV/QA/PROD deploy workflows only after you
have configured GitHub Environments, OIDC federated credentials, and real
build/deploy commands. Otherwise a push to `main` will create a red
workflow that proves nothing about agent quality.

Configure the `dev` environment variables and OIDC credential as shown in
[tutorial-http-agent.md](tutorial-http-agent.md#8-add-a-pr-evaluation-gate).

## 8. Run Watchdog

```powershell
agentops agent analyze --severity-fail critical
code .agentops/agent/report.md
```

Watchdog reads `.agentops/results/*/results.json` and looks for quality,
latency, error, and safety findings. If you configure
`.agentops/agent.yaml` with an Application Insights resource id, it also
queries Azure Monitor. The coding-agent skill `agentops-agent` is just a
guided way to run these commands; it is not the runtime analyzer itself.

## 9. Expand the scenario

After this tutorial passes, make the dataset closer to production:

- Add a row for an unknown order and expect a safe escalation.
- Add a refund row without an order id and expect no `refund_order` call.
- Add negative rows where the user asks for unrelated help.
- Save one passing `results.json` as a baseline and compare future runs
  with `agentops eval run --baseline <path>`.

## Cleanup

```powershell
az group delete --name $env:AZURE_RESOURCE_GROUP --yes --no-wait
```
