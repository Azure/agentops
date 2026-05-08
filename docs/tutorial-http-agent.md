# Tutorial: HTTP Agent Evaluation

This tutorial shows how to evaluate an agent that is exposed as an
HTTP/JSON endpoint. That endpoint can be a local development server,
Azure Container Apps, AKS, App Service, FastAPI, Express, Microsoft Agent
Framework, LangGraph, or any service that accepts a prompt and returns a
text response.

AgentOps treats HTTP agents the same way it treats Foundry agents after
the call succeeds: it loads JSONL rows, POSTs one row at a time, extracts
the answer, runs evaluators, and writes `results.json` plus `report.md`.

## What you will build

- A tiny local HTTP agent so you can run the tutorial without deploying
  anything.
- A flat `agentops.yaml` that points to the HTTP URL.
- A JSONL dataset with deterministic support-style questions.
- One `agentops eval run` producing a passing report.

Use the same pattern later by changing only the `agent:` URL and field
mapping for your real deployed agent.

## Prerequisites

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install "agentops-toolkit @ git+https://github.com/Azure/agentops.git@develop"
```

If you use AI-assisted evaluators such as Similarity or Fluency, also set
the judge model and sign in to Azure:

```powershell
az login
$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://<resource>.services.ai.azure.com/api/projects/<project>"
$env:AZURE_OPENAI_ENDPOINT             = "https://<resource>.openai.azure.com"
$env:AZURE_OPENAI_DEPLOYMENT           = "gpt-4o-mini"
```

## 1. Create a local HTTP agent

Create `http_agent.py`:

```python
from http.server import BaseHTTPRequestHandler, HTTPServer
import json


ANSWERS = {
    "Where is my order ORD-12345?": {
        "text": "Order ORD-12345 is in transit and expected to arrive tomorrow.",
        "tool_calls": [{"type": "tool_call", "tool_call_id": "c1", "name": "lookup_order", "arguments": {"order_id": "ORD-12345"}}],
    },
    "I want a refund for ORD-77821, it arrived broken.": {
        "text": "I started a refund for ORD-77821 because it arrived broken.",
        "tool_calls": [{"type": "tool_call", "tool_call_id": "c2", "name": "refund_order", "arguments": {"order_id": "ORD-77821", "reason": "arrived broken"}}],
    },
    "Hi there!": {
        "text": "Hello! I can help with order status, refunds, or connecting you to a human support agent.",
        "tool_calls": [],
    },
}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length))
        message = body.get("message", "")
        response = ANSWERS.get(message, {"text": "I do not know yet.", "tool_calls": []})

        payload = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


HTTPServer(("127.0.0.1", 8787), Handler).serve_forever()
```

Start it in a second terminal:

```powershell
.\.venv\Scripts\Activate.ps1
python http_agent.py
```

Why this local server? It lets you prove the AgentOps HTTP contract before
you involve Container Apps, auth, networking, or deployment variables.
When this passes locally, a remote HTTP target is just a URL swap.

## 2. Initialize AgentOps

Back in your first terminal:

```powershell
agentops init
```

This creates:

```text
agentops.yaml
.agentops/
  data/smoke.jsonl
  results/
.github/skills/
```

AgentOps 1.0 uses one flat config file at the project root. You do not
need legacy `run-http.yaml`, bundle YAML, or dataset YAML files.

## 3. Configure the HTTP endpoint

Replace `agentops.yaml` with:

```yaml
version: 1
agent: "http://127.0.0.1:8787/"
dataset: .agentops/data/http-support.jsonl

request_field: message
response_field: text
tool_calls_field: tool_calls

thresholds:
  coherence: ">=3"
  fluency: ">=3"
  tool_call_accuracy: ">=0.8"
  intent_resolution: ">=3"
  task_adherence: ">=0.8"
  avg_latency_seconds: "<=2"
```

The HTTP field mapping controls the JSON protocol:

| Config field | Meaning |
|---|---|
| `request_field: message` | AgentOps sends `{"message": "<row input>"}`. |
| `response_field: text` | AgentOps reads the final answer from `response.text`. Dot paths such as `output.text` are supported. |
| `tool_calls_field: tool_calls` | AgentOps reads structured tool calls from `response.tool_calls` so tool metrics can run. |

For a deployed endpoint that requires a Bearer token, add:

```yaml
auth_header_env: AGENT_TOKEN
```

Then set `$env:AGENT_TOKEN` before running the eval.

## 4. Create the dataset

Create `.agentops/data/http-support.jsonl`:

```jsonl
{"input":"Where is my order ORD-12345?","expected":"Order ORD-12345 is in transit and expected to arrive tomorrow.","tool_definitions":[{"type":"function","name":"lookup_order","description":"Look up an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"}},"required":["order_id"]}},{"type":"function","name":"refund_order","description":"Refund an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"},"reason":{"type":"string"}},"required":["order_id","reason"]}}],"tool_calls":[{"type":"tool_call","tool_call_id":"c1","name":"lookup_order","arguments":{"order_id":"ORD-12345"}}]}
{"input":"I want a refund for ORD-77821, it arrived broken.","expected":"A refund is started for ORD-77821 because it arrived broken.","tool_definitions":[{"type":"function","name":"lookup_order","description":"Look up an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"}},"required":["order_id"]}},{"type":"function","name":"refund_order","description":"Refund an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"},"reason":{"type":"string"}},"required":["order_id","reason"]}}],"tool_calls":[{"type":"tool_call","tool_call_id":"c2","name":"refund_order","arguments":{"order_id":"ORD-77821","reason":"arrived broken"}}]}
{"input":"Hi there!","expected":"The assistant replies with a clear greeting and offers support options without calling a tool.","tool_definitions":[{"type":"function","name":"lookup_order","description":"Look up an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"}},"required":["order_id"]}},{"type":"function","name":"refund_order","description":"Refund an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"},"reason":{"type":"string"}},"required":["order_id","reason"]}}],"tool_calls":[]}
```

Each row has:

- `input` — what AgentOps sends to the HTTP service.
- `expected` — the reference answer for text-quality metrics.
- `tool_calls` — the expected structured tool behavior. Omit this field
  if your HTTP endpoint does not expose tool calls.
- `tool_definitions` — the function-tool schema available to the agent.
  Tool-call accuracy evaluators need this catalogue on each row.

## 5. Run the evaluation

```powershell
agentops eval run
```

The CLI should print a passing threshold summary and write:

```text
.agentops/results/<timestamp>/results.json
.agentops/results/<timestamp>/report.md
.agentops/results/latest/
```

Open the Markdown report:

```powershell
code .agentops/results/latest/report.md
```

The report shows the aggregate metrics, threshold table, and per-row
details. For the first two rows, the per-row section should include the
tool calls returned by the HTTP server.

## 6. Point it at a real service

When you deploy the agent, keep the dataset and thresholds but change the
URL and field mapping:

```yaml
version: 1
agent: "https://your-agent.region.azurecontainerapps.io/chat"
dataset: .agentops/data/http-support.jsonl

request_field: message
response_field: output.text
tool_calls_field: output.tool_calls
auth_header_env: AGENT_TOKEN
```

Run the same command:

```powershell
agentops eval run
```

If the local server passed but the remote service fails, the issue is
usually deployment reachability, auth, or a response-field mismatch rather
than evaluator logic.

## Troubleshooting

| Symptom | What to check |
|---|---|
| `connection refused` | The server is not running or the URL/port is wrong. |
| `Response field 'text' not found` | Update `response_field` to match your JSON response shape. |
| `tool_call_accuracy` is missing | Add `tool_calls_field` and make sure the response includes structured tool calls. |
| AI evaluator auth error | Run `az login` and set the Azure OpenAI / Foundry environment variables. |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Evaluation succeeded and all thresholds passed. |
| `2` | Evaluation succeeded but at least one threshold failed. |
| `1` | Runtime or configuration error. |

## CI/CD integration

After the local run passes, generate workflow files with:

```powershell
agentops workflow generate
```

The generated PR workflow uses the same `agentops eval run` exit codes to
gate pull requests. See [ci-github-actions.md](ci-github-actions.md) for
the GitHub environment and OIDC setup.
