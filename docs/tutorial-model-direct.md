# Tutorial: Model-Direct Evaluation (No Agent)

Goal: evaluate a **model deployment** directly using **SimilarityEvaluator** — no agent, no retrieval, no tools.

## Prerequisites

- Python 3.11+
- Azure CLI
- Access to Azure AI Foundry with at least one deployed model in your project

## Part 1: Set up

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

Authentication is passwordless via `DefaultAzureCredential`. No API keys needed.

### 3) Initialize AgentOps

```bash
agentops init
```

This creates the `.agentops/` workspace:

```
.agentops/
├── config.yaml
├── run.yaml                                  # defaults to model-direct scenario
├── run-rag.yaml                              # example run for RAG scenario
├── run-agent.yaml                            # example run for agent scenario
├── .gitignore
├── bundles/
│   ├── model_direct_baseline.yaml            # SimilarityEvaluator >= 3
│   ├── rag_retrieval_baseline.yaml           # GroundednessEvaluator >= 3
│   └── agent_tools_baseline.yaml             # placeholder (Agent with Tools)
├── datasets/
│   ├── smoke-model-direct.yaml               # model-direct dataset definition
│   ├── smoke-rag.yaml                        # RAG dataset definition
│   └── smoke-agent-tools.yaml                # tools dataset definition
├── data/
│   ├── smoke-model-direct.jsonl              # simple QA dataset for model-direct
│   ├── smoke-rag.jsonl                       # QA + context for RAG
│   └── smoke-agent-tools.jsonl               # placeholder dataset for tools
└── results/
```

## Part 2: Configure the run

The default `run.yaml` is already set up for model-direct evaluation:

```yaml
version: 1
bundle:
  path: bundles/model_direct_baseline.yaml
dataset:
  path: datasets/smoke-model-direct.yaml
backend:
  type: foundry
  target: model
  model: <replace-with-your-foundry-model-deployment-name>
  project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
  api_version: "2025-05-01"
  poll_interval_seconds: 2
  max_poll_attempts: 120
  timeout_seconds: 1800
output:
  write_report: true
```

Key differences from agent evaluation:
- `target: model` — calls the model deployment directly (no agent)
- `model` — the deployment name to use, and it must match a model already deployed in your Foundry project
- No `agent_id` needed

### Update the model name

Replace the placeholder with your actual deployment name, for example:

```yaml
backend:
  model: gpt-4o
```

## Part 3: Verify the dataset

`agentops init` already created `.agentops/data/smoke-model-direct.jsonl` with sample data:

```jsonl
{"id":"1","input":"What is the capital of France?","expected":"Paris is the capital of France."}
{"id":"2","input":"Which planet is known as the Red Planet?","expected":"Mars is known as the Red Planet."}
{"id":"3","input":"What is the chemical symbol for water?","expected":"The chemical symbol for water is H2O."}
{"id":"4","input":"Who wrote Romeo and Juliet?","expected":"William Shakespeare wrote Romeo and Juliet."}
{"id":"5","input":"What is the largest ocean on Earth?","expected":"The Pacific Ocean is the largest ocean on Earth."}
```

Each row has:
- `input` — the prompt sent directly to the model
- `expected` — the reference answer for similarity comparison

## Part 4: Run evaluation

```bash
agentops eval run
```

This will:
1. Send each `input` directly to the model deployment
2. Evaluate response quality with `SimilarityEvaluator` (ordinal scale 1–5)
3. Check the threshold: `SimilarityEvaluator >= 3`

### Check results

- `.agentops/results/latest/results.json` — machine-readable results
- `.agentops/results/latest/report.md` — human-readable summary

## When to use Model-Direct

Use this scenario when you want to:
- Evaluate a model deployment without any agent orchestration
- Benchmark raw model quality on QA tasks
- Compare different model deployments on the same dataset
- Run quick smoke tests on model responses

For RAG evaluation (with retrieval context), see the [RAG Tutorial](tutorial-rag.md).

## Notes

- Authentication is automatic via `DefaultAzureCredential`.
- For local development, `az login` is enough.
- Replace the placeholder in `backend.model` with a deployment name that already exists in your Foundry project.
- If you configure AI-assisted evaluators separately, you can also set `AZURE_AI_MODEL_DEPLOYMENT_NAME` to a deployment that exists in the same project.
