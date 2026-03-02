# AgentOps Toolkit

AgentOps Toolkit is an open-source project that makes agent evaluation easier to run and maintain. It gives you reusable templates and a simple workflow so teams can run the same checks in local development and CI. In short, it helps make quality checks repeatable and easier to trust.

## Quick install (pip)

```bash
python -m venv .venv
# activate the virtual environment in your shell
python -m pip install -U pip
python -m pip install agentops-toolkit
```

## Quickstart

This quickstart is the minimal path to test a single **Foundry agent** end-to-end.
AgentOps also supports other evaluation setups, but this section intentionally keeps the flow simple.

From your project root:

```bash
agentops init
```

Set the minimum configuration:

- In `.agentops/run.yaml`:
  - `backend.type: foundry`
  - `backend.target: agent`
  - `backend.agent_id: <your-agent-id>` (example: `my-agent:2`)
- Environment variable:
  - `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>`

Create the dataset file expected by `.agentops/datasets/smoke-agent.yaml`:

Create this file manually in your project:

- `eval/datasets/smoke-agent.jsonl`

With this content:

```jsonl
{"id":"1","input":"What is 7 + 5?","expected":"12"}
{"id":"2","input":"What is 9 * 6?","expected":"54"}
```

If this file is missing, `agentops eval run` fails with a dataset file not found error.

Authentication is automatic via `DefaultAzureCredential`:
- Local dev: `az login`
- CI/CD: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET`
- Azure hosted: managed identity

Run:

```bash
agentops eval run
```

This uses `.agentops/run.yaml` by default and generates `results.json` + `report.md`.

Evaluation model:
- `evaluators` define which scores are produced (for example, `exact_match`, `avg_latency_seconds`, `GroundednessEvaluator`).
- `thresholds` validate evaluator scores using `evaluator` + `criteria` (+ `value` when numeric).
- Every enabled evaluator in the bundle must produce scores in `row_metrics`.
- Each item gets a threshold verdict per evaluator, and an item final verdict (`passed_all`).
- Each run also generates consolidated `run_metrics` (for example `run_pass`, `threshold_pass_rate`, `items_pass_rate`, `accuracy`, plus avg/stddev metrics).

Starter bundles created by `agentops init`:
- `rag_baseline` (groundedness + latency)
- `classifier_baseline` (exact_match + latency)

Default behavior:
- `agentops eval run`
  - `--config` default: `.agentops/run.yaml`
  - `--output` default: timestamp folder under `.agentops/results/`

## Exit codes

- `0`: execution succeeded and thresholds passed
- `2`: execution succeeded, but one or more thresholds failed
- `1`: runtime/configuration error

## Tutorials

[Foundry Agent Evaluation](docs/tutorial-basic-foundry-agent.md)

## How it works

For architecture, configuration model, runtime flow, and output behavior, see: [How it works](docs/how-it-works.md)
