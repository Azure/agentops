# Tutorial — model-direct evaluation

Evaluate a Foundry **model deployment** (`gpt-4o`, `gpt-5.1`, …) with
no agent layer in between. Use this as your quality floor: if the raw
model can't answer your dataset, no agent prompt will save it.

## Prerequisites

- Python 3.11+ and `pip install "agentops-toolkit[foundry] @ git+https://github.com/Azure/agentops.git@develop"`
- A Foundry project with at least one model deployment
- `az login` (AgentOps uses `DefaultAzureCredential`)

## 1. Bootstrap

```bash
agentops init
export AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="https://<resource>.services.ai.azure.com/api/projects/<project>"
```

Use `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` for the Foundry project that hosts
the target deployment. For model-direct Foundry runs, AgentOps also uses this
project endpoint to configure AI-assisted evaluators by default: the judge
deployment defaults to the same deployment named in `agent: "model:<deployment>"`,
and the evaluator endpoint is derived from the project URL.

If you want a separate judge deployment, set both optional overrides before
running:

```bash
export AZURE_OPENAI_ENDPOINT="https://<judge-resource>.openai.azure.com"
export AZURE_OPENAI_DEPLOYMENT="gpt-4o-mini"
# Or, if your environment uses the Foundry deployment variable:
# export AZURE_AI_MODEL_DEPLOYMENT_NAME="gpt-4o-mini"
```

AgentOps automatically enables the `azure-ai-evaluation` reasoning-model token
path for evaluator deployments whose names begin with `gpt-5`, `o1`, `o3`, or
`o4`, so deployments such as `gpt-5.1` can be used for judging.

## 2. Edit `agentops.yaml`

```yaml
version: 1
agent: "model:gpt-4o"           # target deployment; key part is `model:`
dataset: .agentops/data/smoke.jsonl
```

`agent: "model:<deployment>"` is the model-direct shape — AgentOps
classifies it as `model_direct`, sends each row's `input` straight to
the target deployment, and skips agent infrastructure entirely. Unless you set
the optional evaluator overrides, AI-assisted evaluators such as Coherence,
Fluency, and Similarity use this same deployment as the judge.

## 3. Dataset shape

`.agentops/data/smoke.jsonl` (one JSON object per line):

```jsonl
{"id":"1","input":"Answer with exactly this sentence: Paris is the capital of France and one of Europe's major cultural centers.","expected":"Paris is the capital of France and one of Europe's major cultural centers."}
{"id":"2","input":"Answer with exactly this sentence: Mars is known as the Red Planet because iron-rich dust gives its surface a reddish color.","expected":"Mars is known as the Red Planet because iron-rich dust gives its surface a reddish color."}
{"id":"3","input":"Answer with exactly this sentence: Water has the chemical formula H2O because each molecule contains two hydrogen atoms and one oxygen atom.","expected":"Water has the chemical formula H2O because each molecule contains two hydrogen atoms and one oxygen atom."}
```

The first model-direct smoke test intentionally uses short factual
sentences with exact-answer instructions. That makes the default
Similarity, F1, and Fluency thresholds meaningful: if this fails, you
likely have a configuration/auth problem rather than a subjective-answer
mismatch. Once the loop is working, replace these rows with realistic
prompts for your application.

The dataset has only `input` and `expected`, so AgentOps auto-selects
the **model quality** evaluators: Coherence, Fluency, Similarity,
F1Score, plus average latency.

## 4. Run

```bash
agentops eval run
```

Outputs land in `.agentops/results/<timestamp>/` and are mirrored to `.agentops/results/latest/`:

- `results.json` — machine-readable
- `report.md` — Markdown summary with thresholds, per-row metrics,
  and aggregate scores.

Exit code `0` = all thresholds passed, `2` = at least one failed,
`1` = configuration / runtime error.

## 5. Troubleshooting

- **Project endpoint shape errors**: for the default judge configuration,
  `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` should look like
  `https://<resource>.services.ai.azure.com/api/projects/<project>`. AgentOps
  strips `/api/projects/<project>` to derive the evaluator data-plane endpoint.
  If your Foundry endpoint has a different shape, set `AZURE_OPENAI_ENDPOINT`
  and `AZURE_OPENAI_DEPLOYMENT` explicitly for the judge deployment.
- **`max_tokens` / `max_completion_tokens` errors**: AgentOps automatically
  treats evaluator deployments whose names begin with `gpt-5`, `o1`, `o3`, or
  `o4` as reasoning models. If your deployment uses an opaque alias for one of
  those models and `azure-ai-evaluation` still sends legacy `max_tokens`, set
  `AGENTOPS_EVALUATOR_REASONING_MODEL=true`. If an alias is incorrectly
  detected as reasoning, set `AGENTOPS_EVALUATOR_REASONING_MODEL=false`.
  Accepted values are `1`, `true`, `yes`, `on`, `0`, `false`, `no`, and `off`.
- **Separate judge override errors**: if you set `AZURE_OPENAI_ENDPOINT`, also
  set `AZURE_OPENAI_DEPLOYMENT` or `AZURE_AI_MODEL_DEPLOYMENT_NAME`; partial
  overrides are rejected so AgentOps does not silently judge with the wrong
  deployment.

## 6. Compare two model deployments

```bash
# Baseline run on gpt-4o
agentops eval run

# Switch agentops.yaml to agent: "model:gpt-5.1", run again, then:
agentops eval run --baseline .agentops/results/latest/results.json
```

AgentOps loads the baseline before refreshing `latest/`, so
`latest/results.json` always means "the run before this one". For a
stable reference, point at a specific timestamp folder instead.

`report.md` now includes a *Comparison vs Baseline* table with
per-metric deltas (🟢 improved / 🔴 regressed / ⚪ unchanged).

## What model-direct does **not** evaluate

- Multi-turn conversation behaviour
- Tool calling
- Retrieval-augmented generation (RAG)

For those, see:

- [tutorial-basic-foundry-agent.md](tutorial-basic-foundry-agent.md) — Foundry prompt agent
- [tutorial-rag.md](tutorial-rag.md) — RAG agent (rows with `context`)
- [tutorial-http-agent.md](tutorial-http-agent.md) — agent deployed as an HTTP service
- [tutorial-agent-workflow.md](tutorial-agent-workflow.md) — agent with tool calling
