---
name: agentops-config
description: Infer evaluation scenario from codebase and generate run.yaml. Trigger when users ask to configure an evaluation, create a run config, detect the evaluation scenario, or choose a bundle. Common phrases include "configure", "run.yaml", "which bundle", "set up eval", "scenario", "endpoint", "agentops config", "create run config", "what should I evaluate". Install agentops-toolkit via pip.
---

# AgentOps Config

Generate a complete `.agentops/run.yaml` by inspecting the workspace. Infer everything possible — ask only for values that cannot be found.

## Step 0 — Prerequisites

1. Run `pip install agentops-toolkit` if `agentops` command is not available.
2. Run `agentops init` if `.agentops/` directory does not exist.

## Step 1 — Detect scenario

Analyze the codebase holistically to understand the agent's **primary purpose**:

1. Read the README, system prompt, main entry point, and tool/function definitions.
2. Identify which patterns are present:
   - **Tool use**: `@tool`, `tool_definitions`, `function_call`, MCP tools, tool schemas
   - **Retrieval**: search client, vector store, retriever, embeddings, index references, context fetching
   - **Conversation**: chat history, multi-turn, session management, assistant persona
   - **Direct model call**: completion API, no orchestration logic

3. Pick the scenario that best matches the agent's **primary job** — not just the first signal found:

| Primary purpose | `bundle.name` |
|---|---|
| Agent that orchestrates tools to complete tasks | `agent_workflow_baseline` |
| Agent that retrieves context to answer questions | `rag_quality_baseline` |
| Conversational assistant (chat, Q&A, persona) | `conversational_agent_baseline` |
| Direct model call with no agent logic | `model_quality_baseline` |

> A RAG agent that uses a search tool is still primarily RAG — pick `rag_quality_baseline`, not `agent_workflow_baseline`. The test is: *what is the agent's main job?*

4. State what you found: *"Detected RAG scenario — the agent's primary purpose is answering questions using retrieved context (found retriever logic in retriever.py)."*

5. **Responsible AI (optional)**: Ask *"Do you also want to include safety evaluators (violence, hate/unfairness, self-harm, protected material)?"* If yes, add the safety evaluators from `safe_agent_baseline` to the selected bundle.

## Step 2 — Detect endpoint type

| Search for | `endpoint.kind` | `hosting` | `execution_mode` |
|---|---|---|---|
| `AIProjectClient`, `azure-ai-projects`, Foundry URL | `foundry_agent` | `foundry` | `remote` |
| FastAPI, Flask, Django, Express — JSON POST/response | `http` | `containerapps` / `aks` / `local` | `remote` |
| SSE/streaming, non-standard body, custom auth, no server | — | `local` / `containerapps` / `aks` | `local` (callable) |

Also check: `agent_id` references, Dockerfile, bicep, ACA manifests, `.env` files.

**Discover the endpoint URL** — search in this order, stop when found:
1. Env vars: `$env:AGENT_HTTP_URL`, `$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`
2. `.env` / `.env.local` in project root
3. `.azure/<env>/.env` files
4. Azure CLI (if hosting is `containerapps` or ACA-deployed):
   ```bash
   az containerapp list -g $RG --subscription $SUB --query "[].{name:name, url:properties.configuration.ingress.fqdn}" -o json
   ```
5. Azure CLI (if hosting is App Service / webapp):
   ```bash
   az webapp list -g $RG --subscription $SUB --query "[].{name:name, url:defaultHostName}" -o json
   ```

**Detect auth pattern** — search the codebase:
- `dapr-api-token` / `APP_API_TOKEN` → Dapr auth
- `X-API-KEY` / `api_key` / `API_KEY` → API key auth
- `Authorization` / `Bearer` → Bearer token auth
- Nothing found → assume no auth needed

## Step 3 — Discover Azure values

Search these locations **in order** — stop as soon as each value is found:

1. Shell environment variables (`$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`, etc.)
2. `.env`, `.env.local` in project root
3. `.azure/<env>/.env` files (azd environments) — also read `AZURE_RESOURCE_GROUP`, `AZURE_SUBSCRIPTION_ID`
4. `.azure/config.json` for `defaultEnvironment` to pick the right env folder

If values are **not found** in any file, run Azure CLI discovery:
```bash
# 1. Confirm auth and get subscription
az account show --query "{sub:id, tenant:tenantId}" -o json

# 2. Find AI Services / Foundry accounts and endpoints
az cognitiveservices account list -o json --query "[].{name:name, rg:resourceGroup, endpoint:properties.endpoint, kind:kind}"

# 3. Find model deployments
az cognitiveservices account deployment list --name $ACCOUNT -g $RG --subscription $SUB --query "[].{name:name, model:properties.model.name, version:properties.model.version}" -o json

# 4. Find Foundry projects
az resource list -g $RG --subscription $SUB --resource-type "Microsoft.CognitiveServices/accounts/projects" --query "[].name" -o tsv

# 5. Build endpoints from discovered names
# Foundry: https://<account>.services.ai.azure.com/api/projects/<project>
# OpenAI:  https://<account>.openai.azure.com/
```

**Pre-warm Azure token** (prevents intermittent `AzureCliCredential.get_token failed` errors):
```bash
az account get-access-token --resource "https://cognitiveservices.azure.com" --query accessToken -o tsv
```
If this fails, Azure CLI auth is not active — ask the user to run `az login`.

**Only ask the user** if no `.azure/` dir exists AND no env vars are set.

## Step 4 — Pick evaluator model

Read the bundle YAML from `.agentops/bundles/<bundle-name>.yaml`. If it contains **any** evaluator with `source: foundry`, then an evaluator model is required.

Pick from available deployments (discovered in Step 3): `gpt-4.1-mini` > `gpt-4o-mini` > `gpt-4o` > `gpt-4.1`. **Never** use reasoning models (`o1`, `o3`, `o4`, `gpt-5`, `gpt-5-nano`).

If no suitable deployment was found, ask: *"Which model deployment should score your agent's responses? (e.g. gpt-4o-mini)"*

## Step 4.5 — Verify evaluator compatibility

After selecting the bundle, **verify every evaluator is importable** before writing run.yaml.

1. Read `.agentops/bundles/<bundle-name>.yaml` and extract all `class_name` values.
2. Run the import probe:
   ```bash
   python -c "
   evaluators = []
   missing = []
   for name in [<comma-separated class names as strings>]:
       try:
           getattr(__import__('azure.ai.evaluation', fromlist=[name]), name)
           evaluators.append(name)
       except (ImportError, AttributeError):
           missing.append(name)
   print('available:', evaluators)
   print('missing:', missing)
   "
   ```
3. If any evaluators are missing, set `enabled: false` on them in the bundle and remove matching thresholds.
4. Warn the user: *"Disabled [X] — not available in your azure-ai-evaluation SDK version."*

**Key compatibility facts:**
- `F1ScoreEvaluator`, `BleuScoreEvaluator`, `RougeScoreEvaluator` are local text-overlap — they do not need Azure credentials.
- `TaskCompletionEvaluator`, `ToolCallAccuracyEvaluator`, `IntentResolutionEvaluator` are SDK-version-dependent — always verify.

## Step 5 — Write run.yaml

Write `.agentops/run.yaml` using the exact structure below. Fill **every** value — no placeholders.

**Remote (Foundry agent):**
```yaml
version: 1
target:
  type: agent
  hosting: foundry
  execution_mode: remote
  endpoint:
    kind: foundry_agent
    agent_id: <DISCOVERED_OR_ASK>
    model: <DISCOVERED_MODEL>
    project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
bundle:
  name: <DETECTED_BUNDLE>
dataset:
  name: dataset
output:
  write_report: true
```

**Remote (HTTP):**
```yaml
version: 1
target:
  type: agent
  hosting: containerapps
  execution_mode: remote
  endpoint:
    kind: http
    url_env: AGENT_HTTP_URL
    request_field: message
    response_field: text
bundle:
  name: <DETECTED_BUNDLE>
dataset:
  name: dataset
output:
  write_report: true
```

**Local (callable adapter):**
```yaml
version: 1
target:
  type: agent
  hosting: local
  execution_mode: local
  local:
    callable: callable_adapter:run_evaluation
bundle:
  name: <DETECTED_BUNDLE>
dataset:
  name: dataset
output:
  write_report: true
```

## Step 6 — Write callable adapter (if execution_mode is local)

Create `callable_adapter.py` at the **project root**. Use ONLY stdlib (`urllib.request`, `json`, `os`).

```python
import json
import os
import urllib.request

ENDPOINT = os.environ["AGENT_HTTP_URL"]
# Auth: set APP_API_TOKEN, API_KEY, or remove the auth lines below.
AUTH_TOKEN = os.environ.get("APP_API_TOKEN", "")

def run_evaluation(input_text: str, context: dict) -> dict:
    body = json.dumps({"message": input_text}).encode()
    headers = {"Content-Type": "application/json"}
    if AUTH_TOKEN:
        headers["dapr-api-token"] = AUTH_TOKEN  # Change header name if using API_KEY or Bearer
    req = urllib.request.Request(ENDPOINT, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return {"response": data.get("text", data.get("response", ""))}
```

After writing the file, run: `python -c "from callable_adapter import run_evaluation; print('OK')"`

**Auth detection:** Search codebase for `dapr-api-token`/`APP_API_TOKEN` → Dapr header. `X-API-KEY`/`api_key`/`API_KEY` → API key header. `Authorization`/`Bearer` → recommend HTTP backend with `auth_header_env` instead. Nothing found → remove auth lines.

## Step 7 — Present and confirm

Present a **confirmation table** with all discovered values (do not ask each one separately):
```
┌─────────────────────────┬──────────────────────────────────────────┬────────┐
│ Setting                 │ Value                                    │ Source │
├─────────────────────────┼──────────────────────────────────────────┼────────┤
│ Scenario                │ RAG                                      │ code   │
│ Bundle                  │ rag_quality_baseline                     │ auto   │
│ Endpoint kind           │ http                                     │ code   │
│ Endpoint URL            │ https://myapp.azurecontainerapps.io/chat │ .env   │
│ Auth                    │ dapr-api-token (APP_API_TOKEN)           │ code   │
│ Evaluator model         │ gpt-4o-mini                              │ Azure  │
│ Project endpoint        │ https://acct.services.ai.azure.com/...   │ .env   │
└─────────────────────────┴──────────────────────────────────────────┴────────┘
```

Ask: *"Everything look correct? (yes / edit)"*

Explain: scenario detected, endpoint type, evaluator model chosen, and any assumptions made.

## Rules

- **NEVER** include `backend:` key in run.yaml — it causes a runtime error.
- **NEVER** leave `<replace-...>` placeholders in run.yaml.
- **NEVER** fabricate `agent_id`, model names, or endpoint URLs.
- **NEVER** use dotted import paths like `.agentops.callable_adapter` — they fail.
- **NEVER** use a bundle without running the evaluator import probe first (Step 4.5).
- Do not generate datasets — delegate to `/agentops-dataset`.
- Do not run evaluations — delegate to `/agentops-eval`.
- Always state what you detected and what you assumed.