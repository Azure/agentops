# Tutorial (Basic): Foundry Agent + Similarity Evaluation

Goal: create a **Foundry** QA agent and run a minimal AgentOps evaluation using **SimilarityEvaluator** end-to-end.

> **New to AgentOps?** This tutorial uses the **agent** target. If you want to
> evaluate a model deployment directly (no agent), see the
> [Model-Direct Tutorial](tutorial-model-direct.md). For RAG evaluation,
> see the [RAG Tutorial](tutorial-rag.md).

## Prerequisites

- Python 3.11+
- Azure CLI
- Access to Azure AI Foundry

## Part 1: Create the agent in Foundry

### 1) Create or open a Foundry project

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
> API version to `2025-05-01`. For AI-assisted evaluators, explicitly configure a
> model deployment that exists in your project via `backend.model` or
> `AZURE_AI_MODEL_DEPLOYMENT_NAME`. No additional OpenAI env vars are needed
> unless you want to override the defaults.

#### Optional overrides

| Variable | Purpose | Default |
|---|---|---|
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Set the judge model used by AI-assisted evaluators when `backend.model` is not provided | No project-universal default deployment |

### 3) Initialize AgentOps

```bash
agentops init
```

This creates the `.agentops/` workspace with the following structure:

```
.agentops/
├── config.yaml                              # workspace defaults
├── run.yaml                                 # default model-direct run
├── run-rag.yaml                             # example run for RAG scenario
├── run-agent.yaml                           # example run for agent scenario
├── .gitignore
├── bundles/
│   ├── model_direct_baseline.yaml           # Model-Only: SimilarityEvaluator >= 3
│   ├── rag_retrieval_baseline.yaml          # RAG: GroundednessEvaluator >= 3
│   └── agent_tools_baseline.yaml            # Agent with Tools (placeholder)
├── datasets/
│   ├── smoke-model-direct.yaml              # simple QA definition for model-direct
│   ├── smoke-rag.yaml                       # QA + context definition for RAG
│   └── smoke-agent-tools.yaml               # placeholder definition for tools
├── data/
│   ├── smoke-model-direct.jsonl             # sample data (5 rows)
│   ├── smoke-rag.jsonl                      # sample data with context field
│   └── smoke-agent-tools.jsonl              # sample tool-calling data
└── results/
```

If the workspace already exists, existing files are **not** overwritten (use `agentops init --force` to reset).

### 4) Update `.agentops/run-agent.yaml`

For this tutorial, use the `smoke-model-direct.yaml` dataset spec with the agent target. Update `run-agent.yaml` to:

```yaml
version: 1
bundle:
  path: bundles/model_direct_baseline.yaml
dataset:
  path: datasets/smoke-model-direct.yaml
backend:
  type: foundry
  target: agent
  agent_id: <your-agent-id>
  model: <replace-with-your-foundry-model-deployment-name>
  project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
  api_version: "2025-05-01"
  poll_interval_seconds: 2
  max_poll_attempts: 120
  timeout_seconds: 1800
output:
  write_report: true
```

### 5) Verify the sample dataset

`agentops init` already created `.agentops/data/smoke-model-direct.jsonl` with sample data:

```jsonl
{"id":"1","input":"What is the capital of France?","expected":"Paris is the capital of France."}
{"id":"2","input":"Which planet is known as the Red Planet?","expected":"Mars is known as the Red Planet."}
{"id":"3","input":"What is the chemical symbol for water?","expected":"The chemical symbol for water is H2O."}
{"id":"4","input":"Who wrote Romeo and Juliet?","expected":"William Shakespeare wrote Romeo and Juliet."}
{"id":"5","input":"What is the largest ocean on Earth?","expected":"The Pacific Ocean is the largest ocean on Earth."}
```

This tutorial uses `model_direct_baseline`, which applies:
- `SimilarityEvaluator >= 3` (ordinal scale 1–5)

## Part 3: Run evaluation

### 1) Run

```bash
agentops eval run --config .agentops/run-agent.yaml
```

### 2) Check results

- `.agentops/results/latest/results.json`
- `.agentops/results/latest/report.md`

## Evaluation scenarios

AgentOps supports three evaluation scenarios:

| Scenario | Bundle | Target | Description |
|---|---|---|---|
| **Model-Only** | `model_direct_baseline.yaml` | `model` | Direct model calls, SimilarityEvaluator |
| **RAG** | `rag_retrieval_baseline.yaml` | `agent` | Agent with retrieval, GroundednessEvaluator |
| **Agent with Tools** | `agent_tools_baseline.yaml` | `agent` | Placeholder for tool-calling agents |

- [Model-Direct Tutorial](tutorial-model-direct.md) — evaluate a model without an agent
- [RAG Tutorial](tutorial-rag.md) — evaluate groundedness of RAG responses

## Notes

- Authentication is automatic via `DefaultAzureCredential`.
- For local development, `az login` is enough.
- AgentOps defaults the OpenAI API version to `2025-05-01`.
- For AI-assisted evaluators, set `backend.model` or `AZURE_AI_MODEL_DEPLOYMENT_NAME` to a deployment that exists in your Foundry project.
- This tutorial intentionally keeps the flow minimal.
