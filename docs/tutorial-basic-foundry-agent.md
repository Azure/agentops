# Tutorial (Basic): New Foundry QA Agent + Similarity Evaluation

Goal: create a **New Foundry** QA agent and run a minimal AgentOps evaluation using **SimilarityEvaluator** end-to-end.

## Prerequisites

- Python 3.11+
- Azure CLI
- Access to Azure AI Foundry

## Part 1: Create the agent in New Foundry

### 1) Create or open a New Foundry project

1. Open `https://ai.azure.com`.
2. Create a new Foundry project (or open an existing one).

### 2) Create an agent

1. In the project, go to **Build > Agents**.
2. Click **New agent**.

### 3) Add agent instructions

Paste the following instructions into the agent configuration:

```text
You are a factual question-answering assistant.

Mandatory rules:
1. Answer short factual questions clearly and directly.
2. Keep answers concise (one short sentence when possible).
3. Do not invent facts. If uncertain, say you are not sure.
4. Do not include markdown lists or extra formatting.
5. Prefer canonical names and objective wording.
```

### 4) Save and collect values

After saving the agent, copy these values from the Foundry project/agent details:

- **Project endpoint**: `https://<resource>.services.ai.azure.com/api/projects/<project>`
- **Agent ID**: use the exact value shown in your Foundry agent details.

## Part 2: Set up AgentOps locally

### 1) Azure login

```bash
az login
```

### 2) Configure the project endpoint

PowerShell:

```powershell
$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://<resource>.services.ai.azure.com/api/projects/<project>"
```

Bash/zsh:

```bash
export AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="https://<resource>.services.ai.azure.com/api/projects/<project>"
```

Authentication is passwordless via `DefaultAzureCredential` (local `az login`, or Managed Identity in Azure). Do not use API keys.

> **Minimal setup:** For cloud evaluation, the only required environment variable is
> `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`. AgentOps automatically defaults the OpenAI
> API version to `2025-05-01` and resolves the evaluator model from `backend.model`
> in `run.yaml` (default `gpt-5-mini`). No additional OpenAI env vars are needed
> unless you want to override the defaults.

#### Optional overrides

| Variable | Purpose | Default |
|---|---|---|
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Override the judge model used by AI-assisted evaluators | `gpt-5-mini` (from `backend.model` in `run.yaml`) |

### 3) Initialize AgentOps

```bash
agentops init
```

This creates the `.agentops/` workspace with the following structure:

```
.agentops/
├── config.yaml                          # workspace defaults (paths, timeout, report settings)
├── run.yaml                             # run specification (bundle, dataset, backend config)
├── .gitignore                           # ignores results/ from version control
├── bundles/
│   ├── qa_similarity_baseline.yaml      # SimilarityEvaluator >= 3
│   ├── rag_baseline.yaml                # RAG evaluators (Groundedness, Relevance, etc.)
│   └── classifier_baseline.yaml         # Classification evaluators
├── datasets/
│   └── sample-dataset.yaml              # placeholder dataset config (replace with your own)
└── results/                             # evaluation output (auto-generated)
```

If the workspace already exists, existing files are **not** overwritten (use `agentops init --force` to reset).

### 4) Update `.agentops/run.yaml`

Use this minimal config (replace `agent_id` with your real value):

```yaml
version: 1
bundle:
  path: bundles/qa_similarity_baseline.yaml
dataset:
  path: datasets/smoke-agent.yaml
backend:
  type: foundry
  target: agent
  agent_id: <your-agent-id>
  project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
  api_version: "2025-05-01"
  poll_interval_seconds: 2
  max_poll_attempts: 120
  timeout_seconds: 1800
output:
  write_report: true
```

### 5) Create `.agentops/datasets/smoke-agent.yaml`

For this tutorial, create a dedicated dataset config file:

- `.agentops/datasets/smoke-agent.yaml`

You can copy `.agentops/datasets/sample-dataset.yaml` and adapt it, or create it directly with:

```yaml
version: 1
name: smoke
description: Small smoke dataset for local validation.
source:
  type: file
  path: ../../eval/datasets/smoke-agent.jsonl
format:
  type: jsonl
  input_field: input
  expected_field: expected
metadata:
  size_hint: 20
  owner: local
```

### 6) Create `eval/datasets/smoke-agent.jsonl`

Create this file manually:

- `eval/datasets/smoke-agent.jsonl`

With this content:

```jsonl
{"id":"1","input":"What is the capital of France?","expected":"Paris is the capital of France."}
{"id":"2","input":"Which planet is known as the Red Planet?","expected":"Mars is known as the Red Planet."}
{"id":"3","input":"What is the chemical symbol for water?","expected":"The chemical symbol for water is H2O."}
{"id":"4","input":"Who wrote Romeo and Juliet?","expected":"William Shakespeare wrote Romeo and Juliet."}
{"id":"5","input":"What is the largest ocean on Earth?","expected":"The Pacific Ocean is the largest ocean on Earth."}
```

This tutorial uses `qa_similarity_baseline`, which applies:
- `SimilarityEvaluator >= 3` (ordinal scale 1-5)

## Part 3: Run evaluation

### 1) Run

```bash
agentops eval run
```

### 2) Check results

- `.agentops/results/latest/results.json`
- `.agentops/results/latest/report.md`

## Notes

- Authentication is automatic via `DefaultAzureCredential`.
- For local development, `az login` is enough.
- AgentOps defaults the OpenAI API version (`2025-05-01`) and judge model (`gpt-5-mini`) automatically — no extra env vars needed beyond `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`.
- This tutorial intentionally keeps the flow minimal.
