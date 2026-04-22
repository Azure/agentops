# run.yaml Schema Reference

Complete reference for the `run.yaml` configuration file used by `agentops eval run`.

## Top-Level Structure

```yaml
version: 1                  # Required — schema version
run:                         # Optional — run metadata
  name: "my evaluation"
  description: "..."
target:                      # Required — what is being evaluated
  ...
bundle:                      # Required — evaluator bundle reference
  ...
dataset:                     # Required — dataset reference
  ...
execution:                   # Optional — execution settings
  ...
output:                      # Optional — output settings
  ...
```

> **IMPORTANT:** Do NOT include a `backend:` key at the top level. The backend is determined by `target.hosting` and `target.execution_mode`. A `backend:` key will cause a runtime error.

---

## `target` Section (required)

Defines what is being evaluated and how the toolkit connects to it.

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `type` | `"agent"` \| `"model"` | Yes | — | What is being evaluated |
| `hosting` | `"local"` \| `"foundry"` \| `"aks"` \| `"containerapps"` | Yes | — | Where the target is hosted |
| `execution_mode` | `"local"` \| `"remote"` | Yes | — | How the toolkit connects to the target |
| `agent_mode` | `"prompt"` \| `"hosted"` | No | — | Foundry-only: agent interaction mode |
| `framework` | `"agent_framework"` \| `"langgraph"` \| `"custom"` | No | — | Agent-only: agent framework |
| `endpoint` | object | When `execution_mode: remote` | — | Remote endpoint configuration |
| `local` | object | When `execution_mode: local` | — | Local adapter configuration |

### Validation Rules

- `agent_mode` is only valid when `hosting == "foundry"`
- `framework` is only valid when `type == "agent"`
- `endpoint` is required when `execution_mode == "remote"`
- `local` is required when `execution_mode == "local"`

### Backend Resolution

The execution backend is determined automatically:

| `execution_mode` | `endpoint.kind` | Backend |
|---|---|---|
| `local` | — | `LocalAdapterBackend` |
| `remote` | `foundry_agent` | `FoundryBackend` |
| `remote` | `http` | `HttpBackend` |

---

## `target.endpoint` Section (remote execution)

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `kind` | `"foundry_agent"` \| `"http"` | Yes | — | Endpoint type |

### Foundry Agent Endpoint Fields (`kind: foundry_agent`)

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `agent_id` | string | No | — | Agent identifier (e.g., `my-agent:3`) |
| `project_endpoint` | string | No | — | Foundry project URL (inline value) |
| `project_endpoint_env` | string | No | `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` | Env var name holding the project URL |
| `api_version` | string | No | `"2025-05-01"` | Agent Service API version |
| `poll_interval_seconds` | float | No | — | Polling interval for cloud eval |
| `max_poll_attempts` | int | No | — | Max polling attempts |
| `model` | string | No | — | Model deployment name for evaluators |

> **Evaluator Model:** When using AI-assisted evaluators (Groundedness, Relevance, Coherence, etc.), set `model` to an instruction-following deployment like `gpt-4o-mini` or `gpt-4.1-mini`. Avoid reasoning models (`o1`, `o3`, `o4`, `gpt-5`) — they are slower, more expensive, and may not follow evaluator prompts reliably.

### HTTP Endpoint Fields (`kind: http`)

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `url` | string | No* | — | Direct URL to the agent endpoint |
| `url_env` | string | No* | `AGENT_HTTP_URL` | Env var name holding the URL |
| `request_field` | string | No | `"message"` | JSON key for the user prompt |
| `response_field` | string | No | `"text"` | Dot-path to extract response text |
| `headers` | object | No | `{}` | Static extra HTTP headers |
| `auth_header_env` | string | No | — | Env var for Bearer token |
| `tool_calls_field` | string | No | — | Dot-path to extract tool calls |
| `extra_fields` | list[string] | No | — | JSONL row fields to forward in request |

*At least one of `url` or `url_env` is required.

---

## `target.local` Section (local execution)

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `adapter` | string | No* | — | Command string for subprocess adapter |
| `callable` | string | No* | — | Python function as `module:function` |

*Exactly one of `adapter` or `callable` must be provided.

### Callable Adapter

The `callable` field references a Python function using `module:function` syntax. The module must be importable from the project root or from `.agentops/`.

```yaml
local:
  callable: callable_adapter:run_evaluation
```

The function signature must be:
```python
def run_evaluation(input_text: str, context: dict) -> dict:
    return {"response": "the model/agent output text"}
```

### Subprocess Adapter

The `adapter` field specifies a shell command. The subprocess receives JSON on stdin per row and emits JSON on stdout.

```yaml
local:
  adapter: "python my_adapter.py"
```

---

## `bundle` Section (required)

References the evaluator bundle. At least one of `name` or `path` is required.

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | string | No* | — | Resolves to `<workspace>/bundles/<name>.yaml` |
| `path` | path | No* | — | Explicit path (relative to config file directory) |

---

## `dataset` Section (required)

References the evaluation dataset. At least one of `name` or `path` is required.

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | string | No* | — | Resolves to `<workspace>/datasets/<name>.yaml` |
| `path` | path | No* | — | Explicit path (relative to config file directory) |

---

## `execution` Section (optional)

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `concurrency` | int | No | `1` | Max parallel evaluations (schema-only for now) |
| `timeout_seconds` | int | No | `300` | Overall timeout in seconds |

---

## `output` Section (optional)

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `path` | path | No | — | Output directory override |
| `write_report` | bool | No | `true` | Generate `report.md` |
| `publish_foundry_evaluation` | bool | No | `true` | Publish results to Foundry |
| `fail_on_foundry_publish_error` | bool | No | `false` | Fail if Foundry publish fails |

---

## Environment Variables

### Required for Foundry Backend

| Variable | Purpose | Default |
|---|---|---|
| `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` | Foundry project endpoint URL | Required |

### Evaluator Model (for AI-assisted evaluators)

| Variable | Purpose | Default |
|---|---|---|
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint | Auto-derived from project endpoint |
| `AZURE_OPENAI_DEPLOYMENT` | Model deployment name | — |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Explicit deployment name override | — |
| `AZURE_OPENAI_API_VERSION` | OpenAI API version | SDK default |

### Execution Mode

| Variable | Purpose | Default |
|---|---|---|
| `AGENTOPS_FOUNDRY_MODE` | `cloud` or `local` execution | `cloud` |

### Authentication

| Variable | Purpose |
|---|---|
| `AZURE_CLIENT_ID` | Service principal client ID |
| `AZURE_TENANT_ID` | Service principal tenant ID |
| `AZURE_CLIENT_SECRET` | Service principal secret |
| `AZURE_OPENAI_API_KEY` | API key (alternative to credential) |

---

## Examples

### Model Quality (Foundry remote)

```yaml
version: 1
target:
  type: model
  hosting: foundry
  execution_mode: remote
  endpoint:
    kind: foundry_agent
    model: gpt-4o-mini
    project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
bundle:
  name: model_quality_baseline
dataset:
  name: smoke-model-direct
output:
  write_report: true
```

### RAG Quality (callable adapter)

```yaml
version: 1
target:
  type: agent
  hosting: containerapps
  execution_mode: local
  local:
    callable: callable_adapter:run_evaluation
bundle:
  name: rag_quality_baseline
dataset:
  path: .agentops/datasets/dataset.yaml
output:
  write_report: true
```

### HTTP Agent with Tools

```yaml
version: 1
target:
  type: agent
  hosting: aks
  execution_mode: remote
  endpoint:
    kind: http
    url_env: AGENT_HTTP_URL
    request_field: message
    response_field: response.text
    tool_calls_field: response.tool_calls
    auth_header_env: AGENT_API_KEY
bundle:
  name: agent_workflow_baseline
dataset:
  path: .agentops/datasets/dataset.yaml
output:
  write_report: true
```

### azd Integration

If you deployed your resources with `azd` (Azure Developer CLI), your `.azure/<env>/.env` file contains resource metadata (subscription, resource group, resource names) that can be used to auto-configure endpoints via Azure CLI queries. The skills (`/agentops-config`, `/agentops-eval`) can auto-discover these values.
