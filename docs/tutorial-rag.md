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

### 1) Install and sign in

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install --upgrade "agentops-toolkit[foundry] @ git+https://github.com/Azure/agentops.git@feature/test-tutorials"
az login
```

### 2) Configure Foundry and judge-model endpoints

```powershell
$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://<resource>.services.ai.azure.com/api/projects/<project>"
$env:AZURE_OPENAI_ENDPOINT = "https://<openai-resource>.openai.azure.com"
$env:AZURE_OPENAI_DEPLOYMENT = "gpt-4o-mini"
```

Use the Azure OpenAI data-plane endpoint for `AZURE_OPENAI_ENDPOINT` (`*.openai.azure.com`, no `/api/projects/...` path).

### 3) Initialize AgentOps

```powershell
agentops init
```

## Part 3: Configure the run

Edit `agentops.yaml` at your project root for RAG evaluation:

```yaml
version: 1
agent: "<your-agent-name>:<version>"  # e.g. "rag-helper:3"
dataset: .agentops/data/smoke.jsonl
thresholds:
  groundedness: ">=3"
  relevance: ">=3"
  retrieval: ">=3"
```

That is the entire config. AgentOps:

- Classifies `<name>:<version>` as a Foundry **prompt** agent.
- Auto-selects the RAG evaluators (`Groundedness`, `Relevance`,
  `Retrieval`, `ResponseCompleteness`) because dataset rows include a
  `context` field (see [Part 4](#part-4-verify-the-dataset)).
- Reads the project endpoint from
  `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` (set in [Part 2](#2-configure-the-project-endpoint)).
- Reads the judge-model deployment from `AZURE_OPENAI_DEPLOYMENT`.

## Part 4: Verify the dataset

`agentops init` already created `.agentops/data/smoke.jsonl`. For RAG
you want each row to include a `context` column — that is what
triggers the auto-selection of `GroundednessEvaluator`. Replace the
seed file with something like:

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
- `context` — the retrieved document context that `GroundednessEvaluator` uses

When any row has a `context` field, the RAG evaluator set is added
automatically.

> **Tip**: For a real RAG scenario, populate the `context` field with
> actual retrieved passages from your knowledge base.

## Part 5: Run evaluation

```powershell
agentops eval run
```

This will:
1. Send each `input` to the Foundry agent
2. Evaluate groundedness of the response against the provided context
3. Check the threshold: `GroundednessEvaluator >= 3` (ordinal scale 1–5)

### Check results

Under `.agentops/results/latest/` (mirrored from the timestamped run):

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

- `Groundedness`, `Relevance`, `Retrieval`, and `ResponseCompleteness`
  are AI-assisted evaluators — they use a judge model.
- Set `AZURE_AI_MODEL_DEPLOYMENT_NAME` to a deployment that exists in
  your Foundry project for the judge model. If your project only has
  one deployment, this is optional.
- Authentication is automatic via `DefaultAzureCredential`.
- For local development, `az login` is enough.
