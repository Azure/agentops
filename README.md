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

This creates the `.agentops/` workspace with the following structure:

```
.agentops/
├── config.yaml
├── run.yaml
├── .gitignore
├── bundles/
│   ├── qa_similarity_baseline.yaml   (default bundle)
│   ├── rag_baseline.yaml
│   └── classifier_baseline.yaml
├── datasets/
│   ├── sample-dataset.yaml           (placeholder — edit for your project)
│   └── smoke-agent.yaml              (ready-to-use smoke test)
└── results/
```

### Configure

Edit `.agentops/run.yaml` to set your agent:

- `backend.agent_id: <your-agent-id>` (example: `my-agent:2`)

Set the environment variable for your Foundry project:

```bash
export AZURE_AI_FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
```

### Create a dataset

The default `run.yaml` uses `datasets/sample-dataset.yaml`, which points to `../../eval/datasets/your-dataset.jsonl` as a placeholder.

Create your dataset file at `eval/datasets/your-dataset.jsonl` (or choose any path and update the `source.path` field in `sample-dataset.yaml`):

```jsonl
{"id":"1","input":"What is the capital of France?","expected":"Paris is the capital of France."}
{"id":"2","input":"Which planet is known as the Red Planet?","expected":"Mars is known as the Red Planet."}
```

If the `.jsonl` file is missing, `agentops eval run` fails with a dataset-not-found error.

### Authenticate

Authentication is automatic via `DefaultAzureCredential`:
- Local dev: `az login`
- CI/CD: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET`
- Azure hosted: managed identity

### Run

```bash
agentops eval run
```

This uses `.agentops/run.yaml` by default and generates `results.json` + `report.md`.

### Evaluation model

- `evaluators` define which scores are produced (for example, `exact_match`, `avg_latency_seconds`, `SimilarityEvaluator`).
- `thresholds` validate evaluator scores using `evaluator` + `criteria` (+ `value` when numeric).
- Every enabled evaluator in the bundle must produce scores in `row_metrics`.
- Each item gets a threshold verdict per evaluator, and an item final verdict (`passed_all`).
- Each run also generates consolidated `run_metrics` (for example `run_pass`, `threshold_pass_rate`, `items_pass_rate`, `accuracy`, plus avg/stddev metrics).

### Starter bundles

Bundles created by `agentops init`:

| Bundle | Evaluators | Use case |
|---|---|---|
| `qa_similarity_baseline` (default) | `SimilarityEvaluator` | QA scenarios with semantic similarity scoring |
| `rag_baseline` | `GroundednessEvaluator` | RAG scenarios with groundedness scoring |
| `classifier_baseline` | `exact_match` + `avg_latency_seconds` | Classifier-style exact-answer scenarios |

### Default behavior

- `agentops eval run`
  - `--config` default: `.agentops/run.yaml`
  - `--output` default: timestamp folder under `.agentops/results/`

## Commands

### `agentops eval run`

Run an evaluation defined in a `run.yaml` file.

```bash
agentops eval run [--config <path>] [--output <dir>]
```

### `agentops report`

Regenerate `report.md` from an existing `results.json`.

```bash
agentops report [--in <results.json>] [--out <report.md>]
```

Defaults: `--in .agentops/results/latest/results.json`

## Exit codes

- `0`: execution succeeded and thresholds passed
- `2`: execution succeeded, but one or more thresholds failed
- `1`: runtime/configuration error

## Tutorials

[Foundry Agent Evaluation](docs/tutorial-basic-foundry-agent.md)

## How it works

For architecture, configuration model, runtime flow, and output behavior, see: [How it works](docs/how-it-works.md)
