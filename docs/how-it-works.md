# How It Works

This document is the single source of truth for understanding the AgentOps architecture. Read it before making any changes.

---

## What Is AgentOps?

AgentOps is a **standalone Python CLI** that runs **standardized evaluation workflows** for AI agents and models. It:

1. Reads YAML configuration (bundles, datasets, run specs).
2. Executes evaluation against a target (Foundry agent, model deployment, or custom subprocess).
3. Produces normalized outputs: `results.json` (machine-readable) and `report.md` (human-readable).
4. Returns **CI-friendly exit codes** (`0` = pass, `2` = threshold failure, `1` = error) so pipelines can gate on quality.

### Key Principles

| Principle | What It Means in Practice |
|---|---|
| **Thin CLI** | `cli/app.py` only parses args and calls services. No business logic here. |
| **Core is pure** | `core/` has zero Azure imports, zero network calls. It only transforms data. |
| **Lazy Azure imports** | All `azure-*` SDK imports happen inside functions in `backends/` and `services/`, never at the module top level. This keeps the CLI fast and allows tests to run without Azure credentials. |
| **Pydantic v2 everywhere** | Every YAML config and every JSON output is validated by a Pydantic model in `core/models.py`. |
| **pathlib.Path only** | No raw string paths anywhere in the codebase. |
| **No global state** | No singletons, no module-level side effects. |

---

## Source Code Layout (src layout)

```
src/
└── agentops/
    ├── __init__.py            # Package root (version only)
    ├── __main__.py            # Enables `python -m agentops`
    │
    ├── cli/
    │   └── app.py             # Typer CLI definition (init, eval run, report)
    │
    ├── core/                  # Pure data logic — ADD models, loaders, threshold rules here
    │   ├── models.py          # All Pydantic schemas
    │   ├── config_loader.py   # YAML → Pydantic model
    │   ├── thresholds.py      # Threshold pass/fail evaluation
    │   └── reporter.py        # Markdown report generation
    │
    ├── services/              # Orchestration — ADD workflows here
    │   ├── runner.py          # Main evaluation orchestrator
    │   ├── reporting.py       # Report regeneration service
    │   ├── initializer.py     # Workspace scaffolding (agentops init)
    │   └── foundry_evals.py   # Foundry Evaluations panel publishing
    │
    ├── backends/              # Execution engines — ADD new backends here
    │   ├── base.py            # Backend Protocol + shared dataclasses
    │   ├── foundry_backend.py # Foundry Agent Service (cloud + local)
    │   └── subprocess_backend.py # Generic subprocess runner
    │
    ├── utils/                 # Shared helpers
    │   ├── yaml.py            # YAML load + env-var interpolation
    │   └── logging.py         # Logger factory and setup
    │
    └── templates/             # Starter files for `agentops init`
        ├── config.yaml
        ├── run.yaml
        ├── run-rag.yaml
        ├── run-agent.yaml
        ├── bundles/           # Pre-built evaluation bundles
        ├── datasets/         # Dataset definitions (.yaml)
        └── data/             # Sample dataset rows (.jsonl)
```

### Where to Add New Code

| I want to… | Directory / File |
|---|---|
| Add a new Pydantic model or schema field | `core/models.py` |
| Add a new config file type | `core/config_loader.py` (new loader) + `core/models.py` (new model) |
| Add a new local evaluator | `backends/foundry_backend.py` (local eval path) + update bundle docs |
| Add a new execution backend | `backends/` (new file implementing `Backend` protocol from `base.py`) + register in `services/runner.py` |
| Add a new CLI command | `cli/app.py` (keep it thin — delegate to `services/`) |
| Add a new workflow/service | `services/` (new file) |
| Add a starter template | `templates/` + update `pyproject.toml` package-data |

---

## Request Flow (eval run)

When you run `agentops eval run`, the following happens step by step:

```
1. CLI parses args               (cli/app.py → cmd_eval_run)
2. Runner loads config            (services/runner.py → load_run_config, load_bundle_config, load_dataset_config)
3. Runner selects backend         (FoundryBackend or SubprocessBackend based on run.yaml)
4. Backend executes evaluation    (backends/ → invokes agent/model, collects responses)
5. Backend writes backend_metrics.json  (raw scores per row)
6. Runner loads backend metrics   (services/runner.py → _load_backend_metrics)
7. Runner evaluates thresholds    (core/thresholds.py → pass/fail per metric per row)
8. Runner consolidates results    (services/runner.py → builds RunResult)
9. Runner writes results.json     (normalized, versioned output)
10. Runner generates report.md    (core/reporter.py → Markdown from RunResult)
11. Runner syncs latest/ dir      (copies to .agentops/results/latest/)
12. CLI returns exit code         (0 = pass, 2 = threshold fail, 1 = error)
```

---

## CLI Commands

| Command | Purpose | Status |
|---|---|---|
| `agentops init [--path DIR]` | Scaffold `.agentops/` workspace with starter config, bundles, datasets, and data | Available |
| `agentops eval run` | Execute an evaluation (main command) | Available |
| `agentops eval compare --runs ID1,ID2` | Compare two past evaluation runs | Available |
| `agentops run list\|show` | List or inspect past runs | Planned (stub) |
| `agentops run view <id> [--entry N]` | Deep-inspect a run | Planned (stub) |
| `agentops report [--in <path>] [--out <path>]` | Regenerate `report.md` from `results.json` | Available |
| `agentops report show\|export` | View or export reports | Planned (stub) |
| `agentops bundle list\|show` | Browse bundle definitions | Planned (stub) |
| `agentops dataset validate\|describe\|import` | Validate, describe, and import datasets | Planned (stub) |
| `agentops config validate\|show\|cicd` | Validate config and CI/CD scaffolding | Planned (stub) |
| `agentops trace init` | Initialize tracing setup | Planned (stub) |
| `agentops monitor setup\|dashboard\|alert` | Monitoring setup and operations | Planned (stub) |
| `agentops model list` | List model deployments from Foundry project | Planned (stub) |
| `agentops agent list` | List agent deployments from Foundry project | Planned (stub) |

---

## Exit Code Contract

Exit codes are part of the public API. **Do not change their meaning.**

| Code | Meaning |
|---|---|
| `0` | Execution succeeded **and** all thresholds passed |
| `2` | Execution succeeded **but** one or more thresholds failed |
| `1` | Runtime or configuration error |

---

## User Workspace Structure (`.agentops/`)

The `.agentops/` directory lives in your project root and stores all evaluation configuration and outputs.

```
.agentops/
├── config.yaml                # Workspace-level defaults
├── run.yaml                   # Default model-direct run specification
├── run-rag.yaml               # Example run for RAG scenario
├── run-agent.yaml             # Example run for Agent-with-tools scenario
├── bundles/
│   ├── rag_retrieval_baseline.yaml
│   ├── model_direct_baseline.yaml
│   └── agent_tools_baseline.yaml
├── datasets/
│   ├── smoke-rag.yaml         # Dataset metadata and source mapping
│   └── ...
├── data/
│   ├── smoke-rag.jsonl        # Actual data rows
│   └── ...
└── results/
    ├── 2026-03-03_143022/     # Timestamped run (immutable)
    │   ├── results.json
    │   ├── report.md
    │   └── backend_metrics.json
    └── latest/                # Always points to the most recent run
        ├── results.json
        └── report.md
```

## Bundle (`.agentops/bundles/*.yaml`)

- Defines *what quality means* for a scenario.
- Contains evaluators and threshold rules.
- Evaluators are explicit score producers:
  - `source: local` for AgentOps-native evaluators (for example `exact_match`, `avg_latency_seconds`)
  - `source: foundry` for Foundry SDK evaluators (name must match evaluator class name, for example `GroundednessEvaluator`)
- Supported local evaluators are explicit: `exact_match`, `latency_seconds`, `avg_latency_seconds`.
- AgentOps does not emulate Foundry evaluators locally; if you configure `SimilarityEvaluator`/`GroundednessEvaluator`, use `source: foundry`.
- Foundry evaluators support generic configuration via `evaluators[].config`:
  - `kind`: `builtin` (default) or `custom`
  - `class_name`: built-in class name from `azure.ai.evaluation` (optional; defaults to evaluator `name`)
  - `callable_path`: required when `kind: custom`, format `<module>:<symbol>`
  - `init`: constructor kwargs (supports `${env:VAR}` placeholders)
  - `input_mapping`: maps evaluator args to runtime values (for example `$prompt`, `$prediction`, `$expected`, `$row.<field>`, `${env:VAR}`)
  - `score_keys`: ordered list of candidate keys used to extract numeric score from evaluator output
- Create a new bundle when you need a different quality policy (for example: stricter production gate vs. smoke gate).
- Minimal shape:

```yaml
version: 1
name: rag_strict
evaluators:
  - name: GroundednessEvaluator
    source: foundry
    enabled: true
  - name: avg_latency_seconds
    source: local
    enabled: true
thresholds:
  - evaluator: GroundednessEvaluator
    criteria: ">="
    value: 3
  - evaluator: avg_latency_seconds
    criteria: "<="
    value: 10.0
```

Example with explicit Foundry evaluator config:

```yaml
version: 1
name: qa_similarity
evaluators:
  - name: SimilarityEvaluator
    source: foundry
    enabled: true
    config:
      kind: builtin
      class_name: SimilarityEvaluator
      init:
        model_config:
          azure_endpoint: ${env:AZURE_OPENAI_ENDPOINT}
          azure_deployment: ${env:AZURE_OPENAI_DEPLOYMENT}
      input_mapping:
        query: $prompt
        response: $prediction
        ground_truth: $expected
      score_keys:
        - similarity
        - score
  - name: avg_latency_seconds
    source: local
    enabled: true
thresholds:
  - evaluator: SimilarityEvaluator
    criteria: ">="
    value: 3
  - evaluator: avg_latency_seconds
    criteria: "<="
    value: 10.0
```

For built-in Foundry evaluators, AgentOps uses `DefaultAzureCredential` by default (passwordless). Prefer managed identity in Azure environments and avoid API keys.

- Recommended evaluation scenario bundles:
  - `model_direct_baseline`: Model-Only — SimilarityEvaluator (no retrieval, no tools)
  - `rag_retrieval_baseline`: RAG — GroundednessEvaluator (retrieval-augmented)
  - `agent_tools_baseline`: Agent with Tools — placeholder (to be expanded)

- Threshold criteria:
  - Numeric: `>=`, `>`, `<=`, `<`, `==` (requires `value`)
  - Boolean: `true`, `false` (do not set `value`)

## Dataset (`.agentops/datasets/*.yaml`)

- Describes the dataset source and format metadata used in evaluation.
- Create a new dataset config when you want to evaluate another file/source (for example: regression set, domain-specific set).
- Minimal shape:

```yaml
version: 1
name: regression_set
source:
  type: file
  path: ../data/regression.jsonl
format:
  type: jsonl
  input_field: input
  expected_field: expected
```

- `path` is resolved relative to the dataset config file location.
- Keep dataset YAML definitions in `.agentops/datasets/` and `.jsonl` rows in `.agentops/data/` so definitions and data stay separate.

## Run config (`.agentops/run.yaml`)

- Connects one bundle + one dataset + backend execution details.
- This is the default run file loaded by `agentops eval run`.
- This is the file you change most often to point to your target (Foundry agent service, or subprocess app).
- Create additional run files when you need different execution modes (for example: local vs CI backend args).

`agentops init` seeds three scenario-oriented run files:
- `.agentops/run.yaml` (model-direct, default)
- `.agentops/run-rag.yaml` (agent + rag baseline)
- `.agentops/run-agent.yaml` (agent + tools baseline)
- Minimal shape:

```yaml
version: 1
bundle:
  path: bundles/rag_strict.yaml
dataset:
  path: datasets/regression_set.yaml
backend:
  type: foundry
  target: agent
  agent_id: asst_abc123
  model: <replace-with-your-foundry-model-deployment-name>
  project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
  api_version: "2025-05-01"
  poll_interval_seconds: 2
  max_poll_attempts: 120
output:
  write_report: true
```

### Output options

| Option | Default | Description |
|---|---|---|
| `write_report` | `true` | Generate `report.md` alongside `results.json` |
| `publish_foundry_evaluation` | `false` | Publish results to Foundry v2 Evaluations panel (Classic Experience) |
| `fail_on_foundry_publish_error` | `false` | Return exit code `1` if Foundry publish fails |

## Evaluation scenarios

AgentOps supports three evaluation scenarios:

### Model-Only (no retrieval, no tools)

- Sends prompts directly to a model deployment
- No agent involved — the model is the target
- Uses `SimilarityEvaluator` to compare model responses against expected answers
- Bundle: `model_direct_baseline.yaml`
- Dataset: rows with `input` and `expected` fields
- Backend config: `target: model`

### RAG (Retrieval-Augmented Generation)

- Evaluates grounding of responses against context/retrieved documents
- Uses `GroundednessEvaluator` to check that responses are grounded in the provided context
- Bundle: `rag_retrieval_baseline.yaml`
- Dataset: rows with `input`, `expected`, and `context` fields
- Backend config: `target: agent` (agent with knowledge base / retrieval)

### Agent with Tools (placeholder)

- Evaluates agents that use tool calls (function calling)
- Bundle: `agent_tools_baseline.yaml` (placeholder — will be expanded with tool-call evaluators)
- Backend config: `target: agent`

## Backend behavior

- AgentOps Toolkit provides backend orchestration with native `foundry` support.
- In `foundry` mode, AgentOps uses **Foundry Cloud Evaluation** (project-native eval/run lifecycle).
- Cloud runs are persisted in the Foundry project and visible in **Build > Evaluations** (New Foundry Experience).
- AgentOps writes `backend_metrics.json` automatically.
- AgentOps then writes normalized `results.json` (stable contract for CI/reporting).
- `subprocess` is still supported if you want to use a custom evaluator pipeline.

## Foundry target mode

- `target: agent` (default for `backend.type: foundry`)
  - Required in backend config: `agent_id`
  - Required env: `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`
  - Authentication: automatic via `DefaultAzureCredential` (supports `az login`, managed identity, service principal)
  - Optional tuning: `poll_interval_seconds`, `max_poll_attempts`

- `target: model`
  - Sends prompts directly to a model deployment (no agent involved)
  - Required in backend config: `model` (deployment name that already exists in the Foundry project)
  - Required env: `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`
  - Does **not** require `agent_id`
  - Cloud evaluation uses `completions` data source type
  - Local evaluation uses OpenAI chat completions API via the Foundry project client

## Main Foundry testing flow

- Authenticate (pick one):
  - Local dev: `az login`
  - CI/CD: set `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET`
  - Azure hosted: managed identity (no config needed)
- Set project endpoint:
  - `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>`
- Configure the run file for your scenario (`.agentops/run.yaml`, `.agentops/run-rag.yaml`, or `.agentops/run-agent.yaml`):

Example for agent target:

```yaml
version: 1
bundle:
  path: bundles/rag_retrieval_baseline.yaml
dataset:
  path: datasets/smoke-rag.yaml
backend:
  type: foundry
  target: agent
  agent_id: my-agent:1
  model: <replace-with-your-foundry-model-deployment-name>
  project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
  api_version: "2025-05-01"
  poll_interval_seconds: 2
  max_poll_attempts: 120
output:
  write_report: true
```

Example for model-direct target:

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
output:
  write_report: true
```

- Run `agentops eval run` for the default model-direct config, or `agentops eval run --config .agentops/run-rag.yaml` / `agentops eval run --config .agentops/run-agent.yaml` for scenario-specific files.
- AgentOps creates one thread/run per dataset row, fetches the assistant response, computes metrics, and writes artifacts.

## Foundry backend inputs

- Dataset config must point to a JSONL file.
- Each row must include the fields configured in dataset format (`input_field`, `expected_field`).
- For `target: agent`, each row input is sent as a user message to the configured Foundry agent.
- The backend computes only the metrics configured in the bundle:
  - **Foundry evaluators** (`source: foundry`) are executed by the cloud evaluation API.
  - **Local evaluators** (`source: local`) such as `exact_match`, `latency_seconds`, and `avg_latency_seconds` are computed by AgentOps only when explicitly enabled in the bundle.
  - `samples_evaluated` is always emitted.

## Backend metrics contract (`backend_metrics.json`)

- This is the file consumed by AgentOps to build `results.json`.
- In `foundry` mode AgentOps generates it automatically.
- In `subprocess` mode your custom backend must generate it with this shape:

```json
{
  "metrics": [
    { "name": "exact_match", "value": 0.84 },
    { "name": "avg_latency_seconds", "value": 1.21 }
  ],
  "row_metrics": [
    {
      "row_index": 1,
      "metrics": [
        { "name": "exact_match", "value": 1.0 },
        { "name": "avg_latency_seconds", "value": 1.21 }
      ]
    },
    {
      "row_index": 2,
      "metrics": [
        { "name": "exact_match", "value": 0.0 },
        { "name": "avg_latency_seconds", "value": 0.98 }
      ]
    }
  ]
}
```

- Required rules:
  - root JSON object
  - `metrics` must be a list
  - each metric entry must include `name` (string) and `value` (number)
- `row_metrics` is optional, but recommended for dataset-native consolidation.
- when present, each row entry must include:
  - `row_index` (1-based)
  - `metrics` list with `{name, value}` entries
- Each metric `name` must match the evaluator `name` referenced in bundle thresholds.
- AgentOps applies thresholds per item and then consolidates item verdicts into run-level outputs.
- AgentOps validates that every enabled evaluator in the bundle has produced scores in `row_metrics`.

## How evaluators and metrics work

- Evaluator execution is row-first:
  - each dataset row is evaluated and can produce one or more row scores.
- Threshold evaluation is bundle-driven:
  - each threshold references one evaluator score (`thresholds[].evaluator`)
  - each row receives a threshold verdict per evaluator
  - if a row passes all threshold rules, the row verdict is PASS
  - run-level threshold status is consolidated from item verdicts.
- Metrics have three levels in `results.json`:
  - `metrics`: backend/global metrics (already aggregated by backend)
  - `row_metrics`: per-row evaluator outputs (`row_index` + metric list)
  - `item_evaluations`: per-row threshold verdicts (per evaluator + final row PASS/FAIL)
  - `run_metrics`: consolidated execution metrics derived by AgentOps

In short:
- evaluator computes score per item
- threshold validates expected quality policy per item and per run
- AgentOps consolidates visibility for CI and reporting

## Consolidated run metrics

- AgentOps derives consolidated run metrics for each execution in `results.json` under `run_metrics`.
- Derived by default:
  - `run_pass` (`1.0` pass, `0.0` fail)
  - `threshold_pass_rate` (`thresholds_passed / thresholds_count`)
  - `items_total`
  - `items_passed_all`
  - `items_failed_any`
  - `items_pass_rate`
  - per-metric aggregates from row data, for example:
    - `groundedness_avg`
    - `groundedness_stddev`
    - `latency_seconds_avg`
    - `latency_seconds_stddev`
  - `accuracy` (from row-level `exact_match` average when available)

## Outputs and history

- Every run stores artifacts in `.agentops/results/<timestamp>/`.
- AgentOps also refreshes `.agentops/results/latest/` with a copy of the most recent run.
- `results.json`: normalized, machine-readable result for CI/automation.
- `report.md`: human-readable summary for review.

When you run:

```bash
agentops eval run
```

AgentOps writes to both:

- `.agentops/results/YYYY-MM-DD_HHMMSS/` (immutable history of that run)
- `.agentops/results/latest/` (convenient pointer to last run content)

If you pass `--output`, AgentOps writes to that directory and still updates `.agentops/results/latest/` with the newest run content.

---

## Testing

Tests live in `tests/` and are organized as:

```
tests/
├── fixtures/
│   └── fake_eval_runner.py          # Fake backend for integration tests
├── integration/
│   └── test_eval_run_integration.py # End-to-end via subprocess backend
└── unit/
    ├── test_models.py               # Pydantic model validation
    ├── test_reporter.py             # Threshold evaluation + report
    ├── test_yaml_loader.py          # YAML loading + env-var interpolation
    ├── test_foundry_backend.py      # Foundry backend helpers (mocked)
    ├── test_subprocess_backend.py   # Subprocess backend
    └── test_initializer.py          # Workspace scaffolding
```

Run all tests:

```bash
python -m pytest tests/ -x -q
```

Key testing rules:
- All Azure SDK calls must be **mocked** — tests run without Azure credentials.
- Tests must assert correct **exit codes** (0, 1, 2).
- Unit tests go in `tests/unit/`, integration tests in `tests/integration/`.

---

## Dependencies

Declared in `pyproject.toml`:

| Package | Purpose |
|---|---|
| `typer` | CLI framework |
| `pydantic` (v2) | Config and results schema validation |
| `ruamel.yaml` | YAML parsing with env-var interpolation |

**Runtime Azure dependencies** (installed by the user, not declared in `pyproject.toml`):

| Package | Purpose |
|---|---|
| `azure-ai-projects` | Foundry project client, `get_openai_client()` |
| `azure-ai-evaluation` | Local evaluator classes (SimilarityEvaluator, etc.) |
| `azure-identity` | `DefaultAzureCredential` authentication |
| `openai` | OpenAI Evals API types |

Azure SDK dependencies are kept separate so the CLI stays lightweight and tests can run without cloud credentials.

---

## Quick Reference for New Contributors

1. **Install in dev mode**: `pip install -e ".[dev]"` or `pip install -e .` then `pip install pytest`
2. **Run tests**: `python -m pytest tests/ -x -q`
3. **Try it out**: `agentops init` then explore `.agentops/`
4. **Read the models**: `core/models.py` is the best single file to understand all data structures
5. **Follow the flow**: `cli/app.py` → `services/runner.py` → `backends/` → `core/`
6. **Keep CLI thin**: never put logic in `cli/app.py` — delegate to `services/`
7. **Keep core pure**: never import Azure SDK in `core/` — that belongs in `backends/` and `services/`
