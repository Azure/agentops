# Tutorial: HTTP Agent Evaluation (Agent Framework / ACA)

This tutorial shows how to evaluate an AI agent deployed as an HTTP endpoint — for example, a [Microsoft Agent Framework](https://learn.microsoft.com/azure/ai-agent-service/) application running on Azure Container Apps (ACA). No Foundry Agent Service is required.

The HTTP backend sends each dataset row as a JSON POST request to your agent endpoint, extracts the response, runs local and AI-assisted evaluators, and produces the standard `results.json` and `report.md` outputs.

## When HTTP backend makes sense

Use `type: http` when:

- Your agent is **deployed outside Foundry Agent Service** — for example, a multi-agent orchestrator on ACA or a custom FastAPI service.
- You use **Microsoft Agent Framework** (or any other framework) and expose an HTTP chat endpoint.
- You want **CI/CD gating** for any HTTP-accessible agent without Foundry dependency.
- You need to evaluate a **local development server** before deploying.

The HTTP backend works for multi-agent scenarios transparently — evaluation always hits the orchestrator endpoint; internal agent routing and tool calls are invisible to AgentOps at this level.

## Prerequisites

- Python 3.11+
- An agent running and accessible via HTTP (local or remote).
- *(Optional)* Azure CLI for AI-assisted evaluators (`az login`).
- `pip install agentops-toolkit`

## Part 1: Set up

### 1) Initialize the workspace

```bash
agentops init
```

This creates `.agentops/` with all starter files, including the HTTP scenario templates:

```
.agentops/
├── run-http-model.yaml                  ← HTTP run config
├── bundles/model_quality_baseline.yaml  ← baseline evaluators
├── datasets/smoke-model-direct.yaml     ← smoke dataset config
└── data/smoke-model-direct.jsonl        ← 5 generic Q&A rows
```

### 2) Set the agent URL

The recommended approach is to set an environment variable so the URL stays out of your run config:

PowerShell:
```powershell
$env:AGENT_HTTP_URL = "https://your-agent.region.azurecontainerapps.io/chat"
```

Bash/zsh:
```bash
export AGENT_HTTP_URL="https://your-agent.region.azurecontainerapps.io/chat"
```

For a local agent running during development:
```bash
export AGENT_HTTP_URL="http://localhost:8080/chat"
```

### 3) *(Optional)* Configure AI-assisted evaluators

If your bundle includes `SimilarityEvaluator` or other AI-assisted evaluators, set the judge model:

```bash
export AZURE_OPENAI_ENDPOINT="https://your-resource.openai.azure.com/"
export AZURE_AI_MODEL_DEPLOYMENT_NAME="gpt-4o"
```

Run `az login` if you are using `DefaultAzureCredential` locally.

## Part 2: Customize the run config

Open `.agentops/run-http-model.yaml`. The starter config already points at the baseline bundle and smoke dataset:

```yaml
version: 1
target:
  type: model
  hosting: aks
  execution_mode: remote
  endpoint:
    kind: http
    url_env: AGENT_HTTP_URL      # reads the URL from your environment
    request_field: message        # JSON field to send the prompt in
    response_field: text          # JSON field to extract the response from
bundle:
  name: model_quality_baseline
dataset:
  name: smoke-model-direct
execution:
  timeout_seconds: 60
output:
  write_report: true
```

### Adapting to your agent's API

Every agent has its own request/response format. Adjust these fields:

| Field | Default | Description |
|---|---|---|
| `request_field` | `message` | JSON key for the prompt text |
| `response_field` | `text` | JSON key for the response (supports dot-path) |
| `auth_header_env` | — | Env var containing a Bearer token |
| `headers` | `{}` | Static extra headers |

**Examples:**

Agent that expects `{"query": "..."}` and returns `{"answer": "..."}`: 
```yaml
target:
  endpoint:
    kind: http
    url_env: AGENT_HTTP_URL
    request_field: query
    response_field: answer
```

Agent that returns `{"output": {"text": "..."}}` (nested):
```yaml
target:
  endpoint:
    kind: http
    url_env: AGENT_HTTP_URL
    response_field: output.text   # dot-path into nested object
```

Agent requiring Bearer token authentication:
```yaml
target:
  endpoint:
    kind: http
    url_env: AGENT_HTTP_URL
    auth_header_env: AGENT_TOKEN    # reads Bearer token from env
```

Banking assistant (Agent Framework default):
```yaml
target:
  endpoint:
    kind: http
    url_env: AGENT_HTTP_URL
    request_field: message
    response_field: text
    auth_header_env: AGENT_TOKEN

## Part 3: Prepare the dataset

The smoke dataset has 5 generic Q&A rows. For real evaluations, replace `data/smoke-http.jsonl` with domain-specific queries:

```json
{"id":"1","input":"What is the balance on account 12345?","expected":"The balance on account 12345 is $1,234.56."}
{"id":"2","input":"What are the last 3 transactions on my savings account?","expected":"The last 3 transactions are: ..."}
```

Update `datasets/smoke-http.yaml` to point at your file:

```yaml
source:
  type: file
  path: ../data/your-dataset.jsonl
```

## Part 4: Run the evaluation

```bash
agentops eval run --config .agentops/run-http.yaml
```

The backend:
1. Loads the dataset rows from the JSONL file.
2. POSTs each row to your agent via HTTP.
3. Extracts the response text.
4. Runs evaluators (`SimilarityEvaluator`, `avg_latency_seconds`).
5. Writes `backend_metrics.json`, then `results.json` and `report.md`.

Output lands in `.agentops/results/<timestamp>/` and is also synced to `.agentops/results/latest/`.

## Part 5: Review results

**Console:** AgentOps prints a summary with pass/fail per threshold.

**Report:** Open `.agentops/results/latest/report.md` for a human-readable summary.

**JSON:** Parse `.agentops/results/latest/results.json` for machine-readable scores.

## Troubleshooting

**`connection refused` / `URL error`** — Your agent is not reachable. Check that `AGENT_HTTP_URL` is correct and the server is running.

**`Response field 'text' not found`** — Your agent returns a different key. Inspect the raw response and update `response_field` in your run config.

**`SimilarityEvaluator` fails** — Set `AZURE_OPENAI_ENDPOINT` and `AZURE_AI_MODEL_DEPLOYMENT_NAME`, then run `az login`.

**All rows error, exit code 1** — Check `.agentops/results/latest/backend.stderr.log` for per-row error details.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | All rows succeeded and all thresholds passed |
| `2` | Evaluation succeeded but one or more thresholds failed |
| `1` | Runtime error (HTTP failure, config error) |

## CI/CD integration

See [docs/ci-github-actions.md](ci-github-actions.md) for how to gate on the exit code in a GitHub Actions workflow. The HTTP backend works identically to other backends from a CI perspective.
