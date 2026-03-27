# Tutorial: RAG Evaluation (Retrieval-Augmented Generation)

Goal: evaluate a **Foundry agent with retrieval** using **GroundednessEvaluator** to check that responses are grounded in the retrieved context.

## Prerequisites

- Python 3.11+
- Azure CLI
- Access to Azure AI Foundry
- A Foundry agent configured with a knowledge base or retrieval tool

## Part 1: Create the agent in Foundry

### 1) Create or open a Foundry project

1. Open `https://ai.azure.com`.
2. Create a new Foundry project (or open an existing one).

### 2) Create an agent with retrieval

1. In the project, go to **Build > Agents**.
2. Click **New agent**.
3. Add a **knowledge base** or **file search** tool so the agent retrieves documents.

### 3) Add agent instructions

Paste the following instructions into the agent configuration:

```text
You are a factual question-answering assistant with access to a knowledge base.

Mandatory rules:
1. Always ground your answers in the retrieved documents.
2. If no relevant documents are found, say you cannot answer.
3. Keep answers concise and factual.
4. Do not invent information beyond what the documents contain.
```

### 4) Save and collect values

After saving the agent, copy:

- **Project endpoint**: `https://<resource>.services.ai.azure.com/api/projects/<project>`
- **Agent ID**: the exact value shown in your Foundry agent details

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

### 3) Initialize AgentOps

```bash
agentops init
```

## Part 3: Configure the run

Update `.agentops/run-rag.yaml` for RAG evaluation:

```yaml
version: 1
target:
  type: agent
  hosting: foundry
  execution_mode: remote
  endpoint:
    kind: foundry_agent
    agent_id: <your-agent-id>
    model: <replace-with-your-foundry-model-deployment-name>
    project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
    api_version: "2025-05-01"
    poll_interval_seconds: 2
    max_poll_attempts: 120
bundle:
  name: rag_quality_baseline
dataset:
  name: smoke-rag
execution:
  timeout_seconds: 1800
output:
  write_report: true
```

Key settings:
- `bundle.name: rag_quality_baseline` — uses `GroundednessEvaluator`
- `target.type: agent` — sends prompts to the Foundry agent
- `target.endpoint.agent_id` — your agent's ID

## Part 4: Verify the dataset

`agentops init` already created `.agentops/data/smoke-rag.jsonl` with sample data:

```jsonl
{"id":"1","input":"What is the capital of France?","expected":"Paris is the capital of France.","context":"France is a country in Western Europe. Its capital city is Paris, which is also the largest city in France."}
{"id":"2","input":"Which planet is known as the Red Planet?","expected":"Mars is known as the Red Planet.","context":"Mars is the fourth planet from the Sun. It is often called the Red Planet because of its reddish appearance."}
{"id":"3","input":"What is the chemical symbol for water?","expected":"The chemical symbol for water is H2O.","context":"Water is a chemical substance with the formula H2O. Each molecule contains one oxygen and two hydrogen atoms."}
{"id":"4","input":"Who wrote Romeo and Juliet?","expected":"William Shakespeare wrote Romeo and Juliet.","context":"Romeo and Juliet is a tragedy written by William Shakespeare early in his career, first published in 1597."}
{"id":"5","input":"What is the largest ocean on Earth?","expected":"The Pacific Ocean is the largest ocean on Earth.","context":"The Pacific Ocean is the largest and deepest ocean, covering more than 63 million square miles."}
```

Each row has:
- `input` — the question sent to the agent
- `expected` — the reference answer
- `context` — the retrieved document context used by `GroundednessEvaluator`

The `GroundednessEvaluator` checks whether the agent's response is grounded in the `context` (mapped via `expected_field` → `context` in the evaluator's data mapping).

> **Tip**: For a real RAG scenario, populate the `context` field with actual retrieved passages from your knowledge base.

## Part 5: Run evaluation

```bash
agentops eval run --config .agentops/run-rag.yaml
```

This will:
1. Send each `input` to the Foundry agent
2. Evaluate groundedness of the response against the provided context
3. Check the threshold: `GroundednessEvaluator >= 3` (ordinal scale 1–5)

### Check results

- `.agentops/results/latest/results.json`
- `.agentops/results/latest/report.md`

## When to use RAG evaluation

Use this scenario when you want to:
- Evaluate whether your agent's responses are grounded in retrieved documents
- Measure retrieval quality for knowledge-base agents
- Gate CI pipelines on groundedness thresholds
- Compare retrieval configurations or knowledge base updates

For model-only evaluation (no retrieval), see the [Model-Direct Tutorial](tutorial-model-direct.md).

## Notes

- The `GroundednessEvaluator` is an AI-assisted evaluator — it uses a judge model to score groundedness.
- Set `backend.model` or `AZURE_AI_MODEL_DEPLOYMENT_NAME` to a deployment that exists in your Foundry project for the judge model.
- Authentication is automatic via `DefaultAzureCredential`.
- For local development, `az login` is enough.
