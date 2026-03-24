# Tutorial: Model-Direct Evaluation

This tutorial runs an evaluation against a model deployment directly — no agent, no retrieval, no tools. The model receives each prompt in isolation and responds. You evaluate those responses using SimilarityEvaluator, which compares the model's answer against an expected reference on an ordinal scale of 1 to 5.

Model-direct evaluation is the simplest starting point. It tells you what the raw model can do before you add the complexity of an agent layer, and it serves as a quality floor for anything you build on top.

## When model-direct makes sense

Use this when you want to:

- **Benchmark a model deployment** before building an agent. If the model itself cannot answer basic QA correctly, no amount of agent instructions will fix that.
- **Detect model-level regressions** after Azure deploys a new model version or you switch deployments. Run the same dataset, compare results, and see if quality held.
- **Compare model deployments** side by side. Run the same dataset against `gpt-4o` and `gpt-5.1`, then use `agentops eval compare` to see which scores higher.
- **Establish a quality baseline** before investing in agent development. If model-direct scores 5.0 on your dataset and your agent scores 3.4, the gap tells you how much the agent layer is reshaping responses.

Model-direct evaluations typically produce the **highest similarity scores** because the model responds concisely and directly. There is no agent personality rewriting the answer, no tool calls injecting extra context, and no system instructions shaping the tone. If your model-direct score is already low, the problem is either the dataset, the model, or the evaluator — not the agent.

### What model-direct does *not* tell you

Model-direct sends isolated prompts with no conversation history, no system instructions, and no memory of prior turns. It cannot evaluate:

- Whether your agent handles multi-turn conversations correctly
- Whether tool calls execute and return useful results
- Whether retrieval augmentation improves groundedness
- Whether the agent's personality and guardrails work as intended

For those, you need agent evaluation. See the [Foundry Agent Tutorial](tutorial-basic-foundry-agent.md).

## Prerequisites

- Python 3.11+
- Azure CLI (`az login`)
- A Foundry project with at least one model deployment (e.g., `gpt-4o`, `gpt-5.1`)
- `pip install agentops-toolkit`

## Part 1: Set up

### 1) Azure login

```bash
az login
```

AgentOps uses `DefaultAzureCredential` — no API keys, no manual token management. For local development, `az login` is all you need. In CI, use a service principal or managed identity.

### 2) Set the project endpoint

This is the only required environment variable. You can find it in the Foundry portal under your project settings.

PowerShell:
```powershell
$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://<resource>.services.ai.azure.com/api/projects/<project>"
```

Bash/zsh:
```bash
export AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="https://<resource>.services.ai.azure.com/api/projects/<project>"
```

### 3) Initialize the workspace

```bash
agentops init
```

This creates `.agentops/` with starter configs, bundles, datasets, and sample data. The default `run.yaml` is already configured for model-direct evaluation.

## Part 2: Configure the run

Open `.agentops/run.yaml`. The only thing you need to change is the model deployment name:

```yaml
version: 1
bundle:
  path: bundles/model_direct_baseline.yaml
dataset:
  path: datasets/smoke-model-direct.yaml
backend:
  type: foundry
  target: model
  model: gpt-5.1    # ← replace with your actual deployment name
  project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
  api_version: "2025-05-01"
  poll_interval_seconds: 2
  max_poll_attempts: 120
  timeout_seconds: 1800
output:
  write_report: true
```

The key fields:
- `target: model` — this is what makes it model-direct (as opposed to `target: agent`)
- `model` — must match an existing deployment in your Foundry project. AgentOps will fail with a clear error if the deployment does not exist.
- No `agent_id` — not needed for model-direct

### What the bundle evaluates

The `model_direct_baseline` bundle uses two evaluators:
- **SimilarityEvaluator** (source: foundry) — AI-assisted comparison of the model's response against the expected answer. Scores 1-5, threshold ≥ 3.
- **avg_latency_seconds** (source: local) — average response time per row, threshold ≤ 10 seconds.

## Part 3: Review the dataset

The sample dataset at `.agentops/data/smoke-model-direct.jsonl` contains five simple QA pairs:

```jsonl
{"id":"1","input":"What is the capital of France?","expected":"Paris is the capital of France."}
{"id":"2","input":"Which planet is known as the Red Planet?","expected":"Mars is known as the Red Planet."}
```

Each row has:
- `input` — the prompt sent to the model
- `expected` — the reference answer that SimilarityEvaluator compares against

For model-direct evaluation, these prompts are sent raw with no system instructions. The model sees only the `input` text. This is intentional — it isolates the model's capability from any agent configuration.

### Writing your own dataset

When you create your own dataset, keep the expected answers in the same style as the model. If the model tends to start with "The answer is..." but your expected answers are terse one-word responses, SimilarityEvaluator will penalize the style mismatch even though the content is correct. Match the level of detail you expect from the model.

## Part 4: Run the evaluation

```bash
agentops eval run
```

By default this uses `.agentops/run.yaml`. If you want to point to a different config:

```bash
agentops eval run -c .agentops/run.yaml
```

AgentOps will:
1. Send each `input` to the model deployment via the Foundry Cloud Evaluation API
2. Run SimilarityEvaluator on each response against the `expected` answer
3. Check thresholds: SimilarityEvaluator ≥ 3 and avg_latency ≤ 10s
4. Write `results.json` and `report.md` under `.agentops/results/latest/`

### Understanding the output

Open `.agentops/results/latest/report.md` for the human-readable summary. You will see:

- **Overall status** — PASS or FAIL based on all thresholds
- **Metrics** — aggregate SimilarityEvaluator score and average latency
- **Item verdicts** — per-row pass/fail showing which specific questions the model handled well or poorly
- **Threshold checks** — which thresholds passed and which failed, with item counts

A SimilarityEvaluator score of 5.0 means the model's response is semantically equivalent to the expected answer. Scores of 3-4 mean the response captures the core meaning but may differ in phrasing or detail. Below 3 indicates a meaningful divergence — the model may have missed the point, hallucinated, or provided an unrelated answer.

## Part 5: Compare against a future run

After you change model deployments, update the dataset, or modify any configuration, run the evaluation again and compare:

```bash
agentops eval run
agentops eval compare --runs <previous-timestamp>,latest
```

The comparison report shows exactly what changed — which metrics moved, which thresholds flipped, and which rows started failing. See the [Baseline Comparison Tutorial](tutorial-baseline-comparison.md) for the full workflow.

## Transitioning to agent evaluation

Once you are satisfied with model-direct quality, the next step is usually to build an agent and evaluate it. The transition is straightforward:

1. Create an agent in the Foundry portal with system instructions and (optionally) tools
2. Copy `run.yaml` to a new file and change `target: model` to `target: agent`, add the `agent_id`
3. Run the same dataset through the agent
4. Compare model-direct vs agent results with `agentops eval compare`

Expect similarity scores to drop somewhat — the agent rephrases answers in its own style and may add contextual information. A drop from 5.0 to 3.5 is typical and usually acceptable. A drop to 1.0 suggests the agent is not functioning correctly.

See the [Foundry Agent Tutorial](tutorial-basic-foundry-agent.md) for the full guide.

## Notes

- Cloud evaluation (default mode) runs the model and evaluators server-side in Foundry. Results appear in the Foundry portal under **Build > Evaluations**.
- Set `AGENTOPS_FOUNDRY_MODE=local` to run evaluators locally instead of via the cloud API. This requires `pip install azure-ai-evaluation`.
- Exit codes: `0` = all thresholds passed, `2` = one or more thresholds failed, `1` = error.
