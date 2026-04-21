---
name: agentops-eval
description: Guide users through running AgentOps evaluations end to end — codebase analysis, dataset generation, config creation, single runs, multi-model benchmarks, and N-run comparisons. Trigger when users ask to run an evaluation, compare runs, benchmark models, create eval config, generate datasets, or summarize results. Common phrases include "run eval", "evaluate", "start agentops", "compare models", "benchmark agents", "run.yaml", "report", "evaluation results", "which model is best", "set up eval", "create dataset". Install agentops-toolkit via pip. Commands are agentops init, agentops eval run, agentops eval compare, and agentops report generate.
---

# AgentOps Eval

End-to-end evaluation workflow: analyze codebase → generate dataset → configure run → validate → execute → summarize.

## Step 0 — Verify setup

1. Run `pip install agentops-toolkit` if `agentops` command is not available.
2. Run `agentops init` if `.agentops/` directory does not exist.

Then proceed to analyze the codebase. Only ask questions about things you cannot find in the code.

## Step 1 — Detect evaluation scenario

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

4. State your reasoning: *"Detected RAG scenario — the agent's primary purpose is answering questions using retrieved context (found retriever logic in retriever.py)."*

5. **Responsible AI (optional)**: Ask *"Do you also want to include safety evaluators (violence, hate/unfairness, self-harm, protected material)? These can be added alongside your main bundle."* If yes, add the safety evaluators from `safe_agent_baseline` to the selected bundle.

6. **Unit tests (optional)**: Only ask this if **all** of the following are true: (a) the codebase has testable agent code in Python, JavaScript, or TypeScript (endpoint handlers, tool definitions, orchestration logic), (b) no existing test directory or test files are detected (e.g., `tests/`, `test_*.py`, `*_test.py`, `*.test.ts`, `*.test.js`, `__tests__/`). If both conditions are met, ask: *"Would you also like me to generate unit tests for your agent code? (e.g., mocked HTTP calls, response parsing, error handling)"*. If the user declines or if conditions are not met, skip silently. See the **Unit Test Generation** section at the end of this skill for details.

## Step 2 — Detect endpoint type

| Search for | `endpoint.kind` | `hosting` | `execution_mode` |
|---|---|---|---|
| `AIProjectClient`, `azure-ai-projects`, Foundry URL | `foundry_agent` | `foundry` | `remote` |
| FastAPI/Flask/Django — JSON POST → JSON response | `http` | `containerapps`/`aks`/`local` | `remote` |
| SSE/streaming, custom auth, non-standard body, no server | — | `local`/`containerapps`/`aks` | `local` (callable) |

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

**Detect auth pattern** — search the codebase for auth headers used in requests:
- `dapr-api-token` / `APP_API_TOKEN` → Dapr auth (use in callable adapter)
- `X-API-KEY` / `api_key` / `API_KEY` → API key auth (set `auth_header_env`)
- `Authorization` / `Bearer` → Bearer token (set `auth_header_env`)
- No auth headers found → assume no auth needed

Only ask *"What is the URL where your agent is running?"* if discovery finds nothing.

## Step 3 — Generate dataset

**Never offer starter datasets** — always generate a custom one.

1. Read the codebase: system prompt, tools, domain, README.
2. Ask the user what topics the test data should cover.
3. Ask how many rows (suggest 5–10).
4. Write `.agentops/data/data.jsonl` with the correct fields:

| Scenario | JSONL fields |
|---|---|
| Model quality | `input`, `expected` |
| Conversational | `input`, `expected` |
| RAG | `input`, `expected`, `context` |
| Agent with tools | `input`, `expected`, `tool_definitions`, `tool_calls` |

5. Write `.agentops/datasets/dataset.yaml` using this **exact** structure (no alternatives):
```yaml
version: 1
name: dataset
description: <one-line description>
source:
  type: file
  path: ../data/data.jsonl
format:
  type: jsonl
  input_field: input
  expected_field: expected
metadata:
  scenario: <scenario>
  size_hint: <row_count>
```
**NEVER** use `path:` or `fields:` at the top level — the correct keys are `source:` and `format:`. If unsure, read an existing starter config from `.agentops/datasets/` as a reference template first.

6. Show the generated rows to the user for review.

### RAG context enrichment

If the scenario is **RAG** and the dataset has no `context` field:

1. **Find the project's retrieval logic** — search the codebase for how it fetches context today:
   - Look for search/retrieval client initialization, index or collection names, embedding calls
   - Check `.env` files and code for endpoint URLs, API keys, index names used by the retriever
   - The project may use Azure AI Search, Cosmos DB vector search, FAISS, Pinecone, or any other store — read the code to find out

2. **Build a retrieval script** at `.agentops/rag_context.py` (**never** in `src/`) that:
   - Reads the project's own retrieval config (env vars, endpoint, index name) from whatever the project uses
   - For each row in the JSONL, queries the retrieval backend with `row["input"]` and writes the result into `row["context"]`
   - Uses only stdlib (`urllib.request`, `json`, `os`, `subprocess`, `sys`, `shutil`) — no third-party dependencies
   - Accepts the JSONL file path as a CLI argument: `python .agentops/rag_context.py .agentops/data/data.jsonl`
   - **Must be cross-platform** (Windows + Linux/macOS) — when calling external CLIs (e.g. `az`), use the following pattern:
     ```python
     import shutil
     import subprocess
     import sys

     def _run_cli(args: list[str], **kwargs) -> subprocess.CompletedProcess:
         """Run an external CLI command, cross-platform."""
         exe = shutil.which(args[0])
         if exe is None:
             raise FileNotFoundError(
                 f"'{args[0]}' not found in PATH. "
                 "Make sure it is installed and available."
             )
         return subprocess.run(
             [exe] + args[1:],
             **kwargs,
             shell=(sys.platform == "win32"),
         )
     ```
   - This avoids `FileNotFoundError` on Windows where `subprocess.run(["az", ...])` fails without `shell=True`

3. Update dataset YAML to include `context_field: context` under `format:`.
4. Now `rag_quality_baseline` with GroundednessEvaluator and RetrievalEvaluator can be used.

If no retrieval backend can be identified, fall back to `model_quality_baseline` and explain why.

## Step 4 — Discover Azure values

Search these locations in order — stop as soon as each value is found:

1. Shell env vars (`$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`, `$env:AZURE_OPENAI_ENDPOINT`, `$env:AZURE_OPENAI_DEPLOYMENT`)
2. `.env` / `.env.local` in project root
3. `.azure/<env>/.env` (azd environments) — also read `AZURE_RESOURCE_GROUP`, `AZURE_SUBSCRIPTION_ID`
4. `.azure/config.json` for `defaultEnvironment` to pick the right env folder

### Validate azd environment (if using `.azure/<env>/.env`)

Before trusting values from `.azure/<env>/.env`, verify the environment is still valid:

1. **Check if the environment is current** — run `azd env list` and confirm the selected environment appears in the output. If multiple environments exist, list them and ask the user which one to use.
2. **Verify the resource group exists** — after reading `AZURE_RESOURCE_GROUP` and `AZURE_SUBSCRIPTION_ID` from the env file, run:
   ```bash
   az group exists --name $RG --subscription $SUB
   ```
   If this returns `false`, the environment is stale (resources were deleted). Warn the user: *"The resource group '$RG' no longer exists. Your azd environment may be outdated. Please re-run `azd up` or provide current Azure values."*
3. **If validation fails**, do not silently proceed with stale values — ask the user for correct values or to select a different environment.

If values are **not found** in files, use Azure CLI to discover them:

```bash
# 1. Confirm auth and get subscription
az account show --query "{sub:id, tenant:tenantId}" -o json

# 2. Find AI Services / Foundry accounts and endpoints
az cognitiveservices account list -o json --query "[].{name:name, rg:resourceGroup, endpoint:properties.endpoint, kind:kind}"
# Or scope to a known RG:
az cognitiveservices account list -g $RG --subscription $SUB --query "[].{name:name, endpoint:properties.endpoint}" -o json

# 3. Find model deployments (chat, embedding)
az cognitiveservices account deployment list --name $ACCOUNT -g $RG --subscription $SUB --query "[].{name:name, model:properties.model.name, version:properties.model.version}" -o json

# 4. Find Foundry projects
az resource list -g $RG --subscription $SUB --resource-type "Microsoft.CognitiveServices/accounts/projects" --query "[].name" -o tsv

# 5. Build endpoints from discovered names
# Foundry: https://<account>.services.ai.azure.com/api/projects/<project>
# OpenAI:  https://<account>.openai.azure.com/
```

For evaluator model, pick from available deployments: `gpt-4.1-mini` > `gpt-4o-mini` > `gpt-4o` > `gpt-4.1`. **Never** reasoning models (`o1`, `o3`, `o4`, `gpt-5`, `gpt-5-nano`).

**Pre-warm Azure token** (prevents intermittent `AzureCliCredential.get_token failed` errors):
```bash
az account get-access-token --resource "https://cognitiveservices.azure.com" --query accessToken -o tsv
```
If this fails, Azure CLI auth is not active — ask the user to run `az login`.

Check Azure auth: `az account show`. If not logged in, ask the user to run `az login` or set API key.

## Step 4.5 — Evaluator compatibility check (optional)

This step is **optional** — skip it if you are confident the bundle evaluators match the installed SDK. If the evaluation fails later due to a missing evaluator, come back here.

Use the reference table below to decide whether the selected bundle is safe to use **without running any probes**. Evaluators marked "Widely available" work on all recent `azure-ai-evaluation` versions. Only the SDK-version-dependent ones need caution.

### Evaluator compatibility reference

| Evaluator | Category | Needs credentials | Availability |
|---|---|---|---|
| `SimilarityEvaluator` | AI-assisted | Yes | Widely available |
| `CoherenceEvaluator` | AI-assisted | Yes | Widely available |
| `FluencyEvaluator` | AI-assisted | Yes | Widely available |
| `RelevanceEvaluator` | AI-assisted | Yes | Widely available |
| `GroundednessEvaluator` | AI-assisted | Yes | Widely available |
| `F1ScoreEvaluator` | Local text-overlap | No | Widely available |
| `BleuScoreEvaluator` | Local text-overlap | No | Widely available |
| `RougeScoreEvaluator` | Local text-overlap | No | Widely available |
| `GleuScoreEvaluator` | Local text-overlap | No | Widely available |
| `TaskCompletionEvaluator` | AI-assisted | Yes | SDK version dependent |
| `ToolCallAccuracyEvaluator` | AI-assisted | Yes | SDK version dependent |
| `IntentResolutionEvaluator` | AI-assisted | Yes | SDK version dependent |
| `TaskAdherenceEvaluator` | AI-assisted | Yes | SDK version dependent |
| `ToolSelectionEvaluator` | AI-assisted | Yes | SDK version dependent |
| `ToolInputAccuracyEvaluator` | AI-assisted | Yes | SDK version dependent |
| `ResponseCompletenessEvaluator` | AI-assisted | Yes | SDK version dependent |

### When to verify

- If the bundle only uses **widely available** evaluators → proceed directly, no verification needed.
- If the bundle uses **SDK-version-dependent** evaluators → verify they exist before running. You may check `pip show azure-ai-evaluation` for version, read SDK release notes, or use any approach you find efficient. Do **not** get stuck in environment path issues — if a quick check fails, just proceed and let the evaluation surface any import errors.

### If an evaluator is missing

- Disable it in the bundle (`enabled: false`) and remove its threshold.
- Tell the user: *"Disabled [X] — not available in your SDK version."*

## Step 5 — Write run.yaml

Update `.agentops/run.yaml` (the default config). Do **not** create a custom-named file.

**Remote Foundry agent:**
```yaml
version: 1
target:
  type: agent
  hosting: foundry
  execution_mode: remote
  endpoint:
    kind: foundry_agent
    agent_id: <value>
    model: <evaluator-model>
    project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
bundle:
  name: <detected-bundle>
dataset:
  name: dataset
output:
  write_report: true
```

**Remote HTTP:**
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
  name: <detected-bundle>
dataset:
  name: dataset
output:
  write_report: true
```

**Local callable adapter:**
```yaml
version: 1
target:
  type: agent
  hosting: local
  execution_mode: local
  local:
    callable: callable_adapter:run_evaluation
bundle:
  name: <detected-bundle>
dataset:
  name: dataset
output:
  write_report: true
```

Fill **every** `<value>` with a real discovered value. If any value cannot be found, ask the user for just that value.

## Step 5.5 — Write callable adapter (if execution_mode is local)

Create `.agentops/callable_adapter.py`. Use ONLY stdlib. All generated files must live inside `.agentops/` to avoid polluting the project root.

First, examine the agent's response format by reading the endpoint handler code:
- Look for `yield`, `StreamingResponse`, `EventSourceResponse` → SSE/streaming
- Look for `JSONResponse`, `return {"text": ...}` → standard JSON
- Look for conversation ID prefixes, UUID patterns in responses

**Standard JSON adapter:**
```python
import json
import os
import urllib.request

ENDPOINT = os.environ["AGENT_HTTP_URL"]
# Auth: set AGENT_AUTH_HEADER and AGENT_AUTH_TOKEN env vars if your endpoint requires auth.
# Example: AGENT_AUTH_HEADER=dapr-api-token  AGENT_AUTH_TOKEN=dev-token
#          AGENT_AUTH_HEADER=X-API-KEY        AGENT_AUTH_TOKEN=my-key
AUTH_HEADER = os.environ.get("AGENT_AUTH_HEADER", "")
AUTH_TOKEN = os.environ.get("AGENT_AUTH_TOKEN", "")

def run_evaluation(input_text: str, context: dict) -> dict:
    body = json.dumps({"message": input_text}).encode()
    headers = {"Content-Type": "application/json"}
    if AUTH_HEADER and AUTH_TOKEN:
        headers[AUTH_HEADER] = AUTH_TOKEN
    req = urllib.request.Request(ENDPOINT, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return {"response": data.get("text", data.get("response", ""))}
```

**SSE/streaming adapter** (use when agent uses `StreamingResponse`, `yield`, or SSE):
```python
import json
import os
import urllib.request

ENDPOINT = os.environ["AGENT_HTTP_URL"]
# Auth: set AGENT_AUTH_HEADER and AGENT_AUTH_TOKEN env vars if your endpoint requires auth.
AUTH_HEADER = os.environ.get("AGENT_AUTH_HEADER", "")
AUTH_TOKEN = os.environ.get("AGENT_AUTH_TOKEN", "")

def run_evaluation(input_text: str, context: dict) -> dict:
    body = json.dumps({"message": input_text}).encode()
    headers = {"Content-Type": "application/json"}
    if AUTH_HEADER and AUTH_TOKEN:
        headers[AUTH_HEADER] = AUTH_TOKEN
    req = urllib.request.Request(ENDPOINT, data=body, headers=headers, method="POST")
    chunks = []
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):   # SSE comment or keep-alive
                    continue
                if line.startswith("event:"):          # SSE event type — skip
                    continue
                if line.startswith("data: "):
                    payload = line[6:]
                    if payload == "[DONE]":
                        break
                    try:
                        event = json.loads(payload)
                        # Adapt field extraction to match the project's SSE format
                        chunk = event.get("content", event.get("text", ""))
                        if chunk:
                            chunks.append(chunk)
                    except json.JSONDecodeError:
                        chunks.append(payload)         # plain text SSE
                else:
                    chunks.append(line)                # raw text line
    except Exception as e:
        return {"response": f"ERROR: {e}"}
    response_text = "".join(chunks).strip()
    return {"response": response_text}
```

Customize the adapter:
- **Apply the auth pattern detected in Step 2.** Use the table below to wire the correct header and env var into the adapter:

| Auth detected in Step 2 | Adapter env var | Header line in adapter |
|---|---|---|
| `dapr-api-token` / `APP_API_TOKEN` | `AGENT_AUTH_TOKEN` (tell user to set it to their Dapr token) | `headers["dapr-api-token"] = AUTH_TOKEN` |
| `X-API-KEY` / `api_key` / `API_KEY` | `AGENT_AUTH_TOKEN` (tell user to set it to their API key) | `headers["X-API-KEY"] = AUTH_TOKEN` |
| `Authorization: Bearer` | Recommend HTTP backend with `auth_header_env` instead of callable adapter | N/A |
| No auth detected | Remove `AUTH_TOKEN` and auth header lines entirely | N/A |

  **Important**: Do NOT generate the adapter with auth lines commented out or using hardcoded tokens. If auth was detected, the adapter must include the correct header from the start — otherwise the smoke test will fail with 401.

- **Choose the right template:** If the agent code uses `yield`, `StreamingResponse`, `EventSourceResponse`, or `text/event-stream` content type, use the **SSE/streaming adapter** template. Otherwise use the **standard JSON adapter**.
- **Customize the request field:** If the agent expects a different key than `"message"` (e.g. `"ask"`, `"question"`, `"input"`), change the `json.dumps({"message": ...})` line to match.
- **Customize the response extraction:** If the agent returns a different key than `"text"` or `"response"`, update the `.get()` call accordingly.

### Context sanitization (RAG scenarios)

If the dataset has a `context` field populated from Azure AI Search or similar document stores, the raw content often includes HTML comments (`<!-- PageNumber: 122 -->`), document source tags (`[Copy 002 ...]`), and OCR artifacts. Add this helper to the adapter and call it when enriching context:

```python
import re

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_MULTI_BLANK_RE = re.compile(r"\n{3,}")

def _sanitize_context(text: str) -> str:
    """Strip HTML comments, document metadata, and collapse blank lines."""
    text = _HTML_COMMENT_RE.sub("", text)
    text = re.sub(r"^\[.*?\]\s*$", "", text, flags=re.MULTILINE)
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    return text.strip()
```

Apply it to the `context` field in JSONL rows before writing or in the adapter before returning:
```python
ctx = context.get("context", "")
if ctx:
    context["context"] = _sanitize_context(ctx)
```

After writing the file: `python -c "import sys; sys.path.insert(0, '.agentops'); from callable_adapter import run_evaluation; print('OK')"`

## Step 6 — Pre-flight validation

Check **all** of these **before** running. Fix any failures first. Do NOT run-fail-fix iteratively.

- [ ] run.yaml has no `backend:` key (causes runtime error)
- [ ] No `<replace-...>` placeholders in run.yaml
- [ ] Bundle file exists: `.agentops/bundles/<name>.yaml`
- [ ] Dataset file exists: `.agentops/datasets/dataset.yaml`
- [ ] Dataset YAML has `source:` and `format:` keys (NOT `path:` or `fields:` at top level)
- [ ] JSONL file exists: `.agentops/data/data.jsonl`
- [ ] If RAG: JSONL rows have `context` field; dataset YAML has `context_field: context`
- [ ] If bundle uses SDK-version-dependent evaluators: verified availability (see Step 4.5)
- [ ] If callable: `python -c "import sys; sys.path.insert(0, '.agentops'); from callable_adapter import run_evaluation; print('OK')"` succeeds
- [ ] If callable: `AGENT_HTTP_URL` env var is set
- [ ] If callable with auth: auth token env var is set (`APP_API_TOKEN`, `API_KEY`, etc.)
- [ ] **Callable smoke test**: one real call succeeds (see subsection below)
- [ ] If Foundry: `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` env var is set
- [ ] If bundle has `source: foundry` evaluators: evaluator model is configured (`endpoint.model` or `AZURE_OPENAI_ENDPOINT` + `AZURE_OPENAI_DEPLOYMENT`)
- [ ] Azure auth: `az account show` succeeds OR `AZURE_OPENAI_API_KEY` is set
- [ ] Endpoint reachable: `curl -s -o /dev/null -w "%{http_code}" <URL>` returns 200/401/405 (not connection refused)
- [ ] Evaluator model responds: `az cognitiveservices account deployment list --name <ACCOUNT> -g <RG>` confirms deployment exists

Present a **confirmation table** with all discovered values (do not ask each one separately):
```
┌─────────────────────────┬──────────────────────────────────────────┬────────┐
│ Setting                 │ Value                                    │ Source │
├─────────────────────────┼──────────────────────────────────────────┼────────┤
│ Scenario                │ RAG                                      │ code   │
│ Bundle                  │ rag_quality_baseline                     │ auto   │
│ Endpoint URL            │ https://myapp.azurecontainerapps.io/chat │ .env   │
│ Auth                    │ dapr-api-token (APP_API_TOKEN)           │ code   │
│ Evaluator model         │ gpt-4o-mini                              │ Azure  │
│ Project endpoint        │ https://acct.services.ai.azure.com/...   │ .env   │
│ Azure auth              │ az login active                          │ CLI    │
│ Endpoint reachable      │ ✔ (200)                                  │ check  │
│ Dataset rows            │ 8                                        │ file   │
└─────────────────────────┴──────────────────────────────────────────┴────────┘
```

Ask: *"Everything look correct? (yes / edit)"*

### Callable smoke test

A single real end-to-end call catches auth issues (401), wrong request body fields (400/422), and response parsing problems BEFORE wasting an entire evaluation run.

```bash
python -c "
import sys; sys.path.insert(0, '.agentops')
from callable_adapter import run_evaluation
result = run_evaluation('hello', {})
assert 'response' in result, f'Missing response key: {result}'
resp = result['response']
assert not resp.startswith('ERROR:'), f'Adapter error: {resp}'
assert len(resp.strip()) > 0, 'Empty response — check endpoint and request format'
print('Smoke test PASSED')
print(f'Response length: {len(resp)} chars')
print('Response preview:', resp[:200])
"
```

If the smoke test fails:
- **Connection refused** → the agent endpoint is not running. Start it first.
- **401 Unauthorized** → auth token is missing or wrong. Check `AGENT_AUTH_HEADER` and `AGENT_AUTH_TOKEN` env vars.
- **400/422** → the request body format doesn't match the endpoint. Check the `json.dumps({"message": ...})` field name in the adapter — the endpoint may expect a different key (e.g. `"ask"`, `"question"`, `"input"`).
- **Response starts with `ERROR:`** → the adapter caught an exception. Read the error message.
- **Empty response** → the endpoint returned successfully but the adapter extracted no text. Check `response_field` / `.get()` key in the adapter.
- **Response contains unexpected prefix** (UUID, metadata, HTML) → add a post-processing step to the adapter to strip it. Common pattern: `re.sub(r'^[0-9a-f-]{36}\s*', '', response_text)` for UUID prefixes.

### Smoke test response format verification

After the basic smoke test passes, verify the response format matches expectations:
1. If the response contains HTML tags (`<html>`, `<div>`, etc.) but the adapter expects plain text → the endpoint may be returning an error page, not agent output.
2. If the response is very short (< 10 chars) for a conversational prompt like "hello" → warn the user: *"Response seems unusually short. Verify the endpoint is returning the full agent response."*
3. If the response starts with `data:` or contains SSE markers but the adapter uses the standard JSON template → switch to the SSE/streaming adapter template.

Do NOT proceed to Step 7 until the smoke test passes.

## Step 7 — Execute

Ask the user: *"Ready to run the evaluation?"*

If yes:
```bash
agentops eval run -f all
```

After it completes, read `.agentops/results/latest/report.md` and summarize the results.

## Comparing Runs

For multi-model benchmarks, create one run.yaml per model:
```bash
agentops eval run -c .agentops/run-modelA.yaml
agentops eval run -c .agentops/run-modelB.yaml
agentops eval compare --runs <id1>,<id2> -f html
```

For agent version comparison, change `agent_id` per run.

## Commands Reference

```bash
agentops init                                           # Scaffold workspace
agentops eval run [-c run.yaml] [-f md|html|all]       # Run evaluation
agentops eval compare --runs id1,id2 [-f md|html|all]  # Compare runs
agentops report generate [--in results.json]            # Regenerate report
```

## Exit Codes

- `0` — all thresholds passed
- `2` — threshold(s) failed
- `1` — runtime or configuration error

## Rules

- **NEVER** include `backend:` key in run.yaml — it causes a runtime error.
- **NEVER** leave `<replace-...>` placeholders in any generated file.
- **NEVER** fabricate `agent_id`, model names, or endpoint URLs.
- **NEVER** edit `.agentops/` template files (`run-callable.yaml`, `run-http-rag.yaml`, etc.) — always update `.agentops/run.yaml`.
- **NEVER** use dotted import paths like `.agentops.callable_adapter` — they fail.
- **NEVER** create files outside `.agentops/` — all generated artifacts (adapters, datasets, configs, scripts) belong in `.agentops/`. Exception: unit tests go in the project's existing test directory.
- **NEVER** try `az login` automatically — ask the user to authenticate.
- **NEVER** use `requests` or `httpx` in callable adapters — use only stdlib (`urllib.request`, `json`, `os`).
- If a bundle uses SDK-version-dependent evaluators, verify availability before running (Step 4.5). Don't block on this — if verification is hard, proceed and fix on failure.
- Always update `.agentops/run.yaml` — do not create custom-named files except for multi-model benchmarks.
- Use generic file names: `dataset.yaml`, `data.jsonl` — not project-specific prefixes.
- Use plain language in questions — not technical jargon ("callable adapter", "SSE", "POST").
- Always run pre-flight (Step 6) before executing. Fix all issues first.

## Unit Test Generation (Optional)

This section is only executed if the user accepted the unit test offer in Step 1.

### When to generate

- The codebase has Python, JavaScript, or TypeScript agent code with testable logic (endpoint handlers, tool definitions, response parsing, orchestration).
- No existing test files or test directories were detected.

### What to generate

Create tests in the project's conventional test directory (e.g. `tests/test_agent.py` for Python, `__tests__/agent.test.ts` for TypeScript). Use only standard testing libraries — no extra dependencies.

**For Python agents**, generate `pytest` tests using `unittest.mock`:

1. **Endpoint handler test** — mock the HTTP framework (FastAPI `TestClient`, Flask `test_client`) and verify the handler returns expected response format.
2. **Response parsing test** — if the agent has response parsing logic (JSON extraction, SSE chunk assembly, UUID stripping), test it with known inputs/outputs.
3. **Error handling test** — verify the agent handles timeouts, 4xx/5xx from downstream services, and malformed inputs gracefully.
4. **Tool schema test** (if applicable) — if the agent defines tools with schemas, validate the schema structure is correct (required fields, types).

**Template pattern** (adapt to the detected code):
```python
"""Unit tests for agent endpoint — generated by AgentOps."""
import json
from unittest.mock import MagicMock, patch

import pytest


class TestAgentEndpoint:
    """Tests for the agent's HTTP endpoint handler."""

    def test_returns_valid_response_format(self):
        # Mock the downstream model/service call
        # Call the endpoint handler directly
        # Assert response has expected keys and types
        ...

    def test_handles_empty_input(self):
        # Verify the agent handles empty or whitespace-only input
        ...

    def test_handles_downstream_timeout(self):
        # Mock the downstream call to raise a timeout
        # Assert the agent returns an error response (not a crash)
        ...
```

### Rules for generated tests

- Tests must run **without** Azure credentials or live services — all external calls must be mocked.
- Do not generate tests that duplicate what AgentOps evaluations already cover (response quality, groundedness, coherence).
- Focus on **functional correctness**: does the code do what it's supposed to do?
- Place tests in the project's existing test directory structure, not in `.agentops/`.
- If the project uses a specific test runner or framework (detected via `pyproject.toml`, `package.json`, `conftest.py`), follow its conventions.
