# How It Works

This document is the single source of truth for understanding the AgentOps architecture. Read it before making any changes.

## What Is AgentOps?

AgentOps is a **standalone Python CLI** that runs **standardized evaluation workflows** for AI agents and models. It:

1. Reads YAML configuration (bundles, datasets, run specs).
2. Executes evaluation against a target (Foundry agent, model deployment, HTTP endpoint, or local adapter).
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

## Source Code Layout (src layout)

```
src/
└── agentops/
    ├── __init__.py            # Package root (version only)
    ├── __main__.py            # Enables `python -m agentops`
    │
    ├── cli/
    │   └── app.py             # Typer CLI definition (init, eval run, report,
    │                              # workflow, skills, mcp, agent)
    │
    ├── core/                  # Pure data layer — no Azure imports, no I/O
    │   ├── agentops_config.py # Flat 1.0 `agentops.yaml` Pydantic schema
    │   ├── config_loader.py   # YAML → AgentOpsConfig
    │   ├── evaluators.py      # Evaluator catalog (presets + auto-selection)
    │   └── results.py         # RunResult / RowResult / TargetInfo / RunSummary
    │
    ├── pipeline/              # Run orchestration — ADD execution flows here
    │   ├── orchestrator.py    # End-to-end `eval run` driver
    │   ├── runtime.py         # Pre-flight checks (deps, creds, endpoints)
    │   ├── invocations.py     # Per-row agent / model invocation strategies
    │   ├── thresholds.py      # Threshold pass/fail evaluation
    │   ├── reporter.py        # Markdown report generation
    │   ├── comparison.py      # `eval compare` two runs
    │   ├── publisher.py       # Classic Foundry publish (OneDP upload of metrics)
    │   └── cloud_publisher.py # New Foundry publish (server-side via OpenAI Evals API)
    │
    ├── services/              # Workspace / project tooling
    │   ├── initializer.py     # `agentops init` workspace scaffolding
    │   ├── skills.py          # Coding agent skill installation
    │   └── cicd.py            # CI/CD workflow generation
    │
    ├── agent/                 # `agentops agent analyze|serve` watchdog
    ├── mcp/                   # `agentops mcp serve` Model Context Protocol server
    │
    ├── utils/                 # Shared helpers (yaml load, logging, colors)
    │
    └── templates/             # Starter files for `agentops init`
        ├── agentops.yaml      # Minimal flat config (the single config file)
        ├── callable_adapter.py
        ├── data/              # Sample dataset rows (.jsonl)
        ├── skills/            # Coding agent skill templates
        └── workflows/         # CI/CD workflow templates
```

### Where to Add New Code

| I want to… | Directory / File |
|---|---|
| Add a field to `agentops.yaml` | `core/agentops_config.py` |
| Add a new evaluator preset | `core/evaluators.py` (catalog) |
| Change pre-flight checks | `pipeline/runtime.py` |
| Add a new invocation strategy (new target kind) | `pipeline/invocations.py` + `core/agentops_config.py::classify_agent` |
| Tweak the report layout | `pipeline/reporter.py` |
| Add or change a publish destination | `pipeline/publisher.py` (Classic) or `pipeline/cloud_publisher.py` (New Foundry); register in `pipeline/orchestrator.py` |
| Add a new CLI command | `cli/app.py` (keep it thin — delegate to `pipeline/` or `services/`) |
| Add a starter template | `templates/` + update `pyproject.toml` package-data |
| Add a coding agent skill | `templates/skills/<name>/SKILL.md` + sync to `plugins/agentops/skills/` (`scripts/sync-skills.{sh,ps1}`) |

## Request Flow (eval run)

When you run `agentops eval run`, the following happens step by step:

```
 1. CLI parses args               (cli/app.py → cmd_eval_run)
 2. Loader parses agentops.yaml   (core/config_loader.py → AgentOpsConfig)
 3. classify_agent resolves kind  (foundry_prompt | foundry_hosted | http_json | model_direct)
 4. Pre-flight checks run         (pipeline/runtime.py — deps, creds, endpoint reachability)
 5. Orchestrator iterates dataset (pipeline/orchestrator.py)
 6. Per row: invoke target        (pipeline/invocations.py — picks Foundry / HTTP / model API)
 7. Per row: run evaluators       (core/evaluators.py — auto-selected from row shape)
 8. Aggregate metrics             (orchestrator builds RunResult)
 9. Evaluate thresholds           (pipeline/thresholds.py — pass/fail per metric)
10. Write results.json + report.md (pipeline/reporter.py)
11. Sync .agentops/results/latest/
12. (Optional) Publish to Foundry (pipeline/publisher.py or cloud_publisher.py)
13. CLI returns exit code         (0 = pass, 2 = threshold fail, 1 = error)
```

## CLI Commands

| Command | Purpose | Status |
|---|---|---|
| `agentops init [--path DIR]` | Scaffold `.agentops/` workspace with starter config, bundles, datasets, and data. Also installs coding agent skills. | Available |
| `agentops eval run` | Execute an evaluation (main command) | Available |
| `agentops eval compare --runs ID1,ID2` | Compare two past evaluation runs | Available |
| `agentops skills install` | Install AgentOps coding agent skills (Copilot, Claude) into the target project | Available |
| `agentops run list\|show` | List or inspect past runs | Planned (stub) |
| `agentops run view <id> [--entry N]` | Deep-inspect a run | Planned (stub) |
| `agentops report generate [--in <path>] [--out <path>]` | Regenerate `report.md` from `results.json` | Available |
| `agentops report show\|export` | View or export reports | Planned (stub) |
| `agentops bundle list\|show` | Browse bundle definitions | Planned (stub) |
| `agentops dataset validate\|describe\|import` | Validate, describe, and import datasets | Planned (stub) |
| `agentops config validate\|show` | Validate and inspect configuration | Planned (stub) |
| `agentops workflow generate` | Generate CI/CD workflow file | Available |
| `agentops trace init` | Initialize tracing setup | Planned (stub) |
| `agentops monitor setup\|show\|configure` | Monitoring setup and operations | Planned (stub) |
| `agentops model list` | List model deployments from Foundry project | Planned (stub) |
| `agentops agent list` | List agent deployments from Foundry project | Planned (stub) |

## Exit Code Contract

Exit codes are part of the public API. **Do not change their meaning.**

| Code | Meaning |
|---|---|
| `0` | Execution succeeded **and** all thresholds passed |
| `2` | Execution succeeded **but** one or more thresholds failed |
| `1` | Runtime or configuration error |

## User Workspace Structure (`agentops.yaml` + `.agentops/`)

The flat 1.0 schema places **one config file** at the project root and a
small directory for datasets, run history, and (optionally) skills.

```
<project root>/
├── agentops.yaml              # Single source of truth (flat 1.0 schema)
├── .agentops/
│   ├── data/
│   │   └── smoke.jsonl        # Sample dataset (created by `agentops init`)
│   └── results/
│       ├── 2026-05-06T14-30-22Z/  # Timestamped run (immutable history)
│       │   ├── results.json
│       │   ├── report.md
│       │   └── cloud_evaluation.json   # only when `publish:` was set
│       └── latest/                # Mirror of the most recent run
└── .github/skills/            # Coding agent skills (Copilot)
    ├── agentops-config/SKILL.md
    ├── agentops-eval/SKILL.md
    └── ...
```

The legacy layered layout (`.agentops/config.yaml` + `bundles/` +
`datasets/*.yaml` + `run.yaml`) **no longer exists**. The new schema is
declared by [src/agentops/core/agentops_config.py](../src/agentops/core/agentops_config.py)
and rejects any of the legacy top-level keys (`target`, `bundle`,
`execution`, `output`, `scenario`, `backend`, `run`) at parse time with
an actionable error.

## `agentops.yaml` (flat 1.0 schema)

### Minimal config

The minimum is three lines:

```yaml
version: 1
agent: my-rag:3
dataset: ./qa.jsonl
```

That's a complete config. AgentOps:

* Resolves `agent` into one of four target kinds (see below).
* Auto-selects evaluators from the dataset row shape (presence of
  `context`, `tool_calls`, `tool_definitions`).
* Applies sensible default thresholds from the evaluator catalog.

### Top-level fields

| Field | Required | Description |
|---|---|---|
| `version` | yes | Schema version. Must be `1`. |
| `agent` | yes | Target identifier. See "Target kinds" below. |
| `dataset` | yes | Relative path to a JSONL file with one evaluation row per line. |
| `thresholds` | no | Dict of `metric_name: criteria_expression`. Examples: `">=3"`, `"<=10"`, `"true"`, raw number `3` (treated as `>=3`). Defaults from catalog if omitted. |
| `protocol` | no | Wire protocol for URL-based agents: `responses` (Foundry hosted), `invocations` (Knative), `http-json` (default for arbitrary HTTPS). |
| `request_field` / `response_field` / `tool_calls_field` | no | JSON keys / dot-paths used to marshal each row into the request and extract the response. Defaults are sensible for OpenAI-compatible / ACA endpoints. |
| `headers` | no | Static HTTP headers (dict). |
| `auth_header_env` | no | Env var name holding a Bearer token. |
| `evaluators` | no | Escape-hatch list of evaluator names that overrides auto-selection. |
| `publish` | no | `foundry` (Classic) or `foundry_cloud` (preview, server-side). See [Publishing](#publishing-to-foundry-evaluations). |
| `project_endpoint` | no | Foundry project URL used by `publish:`. Falls back to `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`. |

### Target kinds

`classify_agent()` resolves `agent` into one of four kinds based on shape:

| Kind | Trigger | Example `agent` value |
|---|---|---|
| `foundry_prompt` | `name:version` | `my-rag:3` |
| `foundry_hosted` | URL on a Foundry domain | `https://contoso.services.ai.azure.com/.../agents/<id>` |
| `http_json` | Any other HTTPS URL | `https://my-aca-app.eastus2.azurecontainerapps.io/chat` |
| `model_direct` | `model:<deployment>` | `model:gpt-4o-mini` |

The kind drives both invocation strategy (`pipeline/invocations.py`) and
which fields make sense (e.g. `protocol` is rejected for
`foundry_prompt` and `model_direct`).

### Examples

**Foundry prompt agent (RAG bundle auto-selected from dataset rows):**

```yaml
version: 1
agent: my-rag:3
dataset: .agentops/data/qa.jsonl
thresholds:
  groundedness: ">=3"
  coherence: ">=3"
  avg_latency_seconds: "<=10"
publish: foundry      # Classic Foundry Evaluations panel (best-effort)
```

**HTTP-deployed agent (LangGraph / ACA / custom REST):**

```yaml
version: 1
agent: https://my-aca-app.eastus2.azurecontainerapps.io/chat
dataset: .agentops/data/qa.jsonl
request_field: message            # default is "message"
response_field: text              # dot-path; default is "text"
auth_header_env: APP_API_TOKEN    # value used as Bearer token
```

**Raw model deployment:**

```yaml
version: 1
agent: model:gpt-4o-mini
dataset: .agentops/data/qa.jsonl
thresholds:
  similarity: ">=4"
  avg_latency_seconds: "<=8"
```

**New Foundry server-side run (preview):**

```yaml
version: 1
agent: my-rag:3                   # name:version is required for cloud mode
dataset: .agentops/data/qa.jsonl
publish: foundry_cloud
# project_endpoint: "https://<resource>.services.ai.azure.com/api/projects/<p>"
```

## Datasets

A dataset is a plain JSONL file. One row per line. No companion YAML.

Required fields:

| Field | Type | Notes |
|---|---|---|
| `input` | string | The prompt sent to the target. |
| `expected` | string | Ground-truth response used by reference-based evaluators. |

Optional fields drive evaluator auto-selection:

| Field | Triggers |
|---|---|
| `context` | RAG evaluators (`GroundednessEvaluator`, `RelevanceEvaluator`, `RetrievalEvaluator`, `ResponseCompletenessEvaluator`) |
| `tool_calls` + `tool_definitions` | Tool-use evaluators (`ToolCallAccuracyEvaluator`, `IntentResolutionEvaluator`, `TaskAdherenceEvaluator`, …) |

Example RAG row:

```json
{"input": "What is the refund policy?", "expected": "Refunds within 30 days.", "context": "Our policy: refunds available within 30 days of purchase."}
```

## Evaluator auto-selection

The catalog is defined in [src/agentops/core/evaluators.py](../src/agentops/core/evaluators.py).
Selection rules (in order):

1. If `evaluators:` is set in `agentops.yaml`, use it verbatim (escape hatch).
2. Otherwise, start from the **quality baseline** for the resolved target
   kind (e.g. `Coherence + Fluency + Similarity + F1Score` for chat-style agents).
3. If dataset rows include `context`, add the **RAG bundle**
   (`Groundedness`, `Relevance`, `Retrieval`, `ResponseCompleteness`).
4. If rows include `tool_calls` + `tool_definitions`, add the **tool-use
   bundle** (`ToolCallAccuracy`, `IntentResolution`, `TaskAdherence`, …).
5. `avg_latency_seconds` is always included as a runtime metric.

### Recommended judge models

AI-assisted evaluators use an LLM as a judge. Use instruction-following
models like `gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, `gpt-4.1-mini`. **Avoid
reasoning models** (`o1`, `o3`, `o4`, `gpt-5`, `gpt-5-nano`) — they are
slower, more expensive, and may not follow the evaluator prompt format.

Set the deployment via env vars before running:

```bash
export AZURE_OPENAI_ENDPOINT="https://<account>.openai.azure.com/"
export AZURE_OPENAI_DEPLOYMENT="gpt-4o-mini"
```

## Thresholds

Threshold expressions accept:

| Form | Meaning |
|---|---|
| `">=3"`, `">3"`, `"<=10"`, `"<10"`, `"==1"` | Numeric comparison |
| `"true"` / `"false"` | Boolean expectation (used by safety evaluators) |
| Raw number `3` | Shorthand for `>=3` |

Each row is judged against every applicable threshold. A row passes only
if every threshold passes. The run passes only if every row passes
(this is the only condition that maps to exit code `0`; otherwise `2`).

## Publishing to Foundry Evaluations

`publish:` is opt-in. Both modes are best-effort: if publish fails, the
local `results.json` and `report.md` remain the canonical record and the
exit code reflects only thresholds, not publish failures.

| Mode | What it does | Where results land | Target restriction |
|---|---|---|---|
| `publish: foundry` | Uploads metrics computed locally via OneDP. | **Classic** Foundry Evaluations panel. | Any target kind. |
| `publish: foundry_cloud` (preview) | Re-runs the agent + builtin evaluators **server-side** via the OpenAI Evals API. | **New** Foundry Evaluations panel. | `foundry_prompt` only (`name:version` Foundry agents). |

Both modes:

* Require either `project_endpoint` in `agentops.yaml` or
  `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` in the environment.
* Authenticate with `DefaultAzureCredential` (passwordless: `az login`,
  managed identity, or service principal).
* Write `cloud_evaluation.json` next to `results.json` containing
  `mode` (`classic` or `cloud`), `evaluation_name`, `report_url`, and
  (for `foundry_cloud`) the `eval_id` / `run_id` / terminal `status`.

The cloud-mode trade-offs (so you can decide consciously):

* Foundry-side latency replaces the locally-measured wall-clock latency.
* Judges are opaque (Foundry-managed); custom evaluators are skipped.
* The dataset is uploaded as an OpenAI file (egress + transient storage
  in your project).
* Evaluator runs cost against your Azure OpenAI deployment.
* Polling adds ~5 s × N to the total wall-clock time.

Implementation lives in [src/agentops/pipeline/publisher.py](../src/agentops/pipeline/publisher.py)
(Classic) and [src/agentops/pipeline/cloud_publisher.py](../src/agentops/pipeline/cloud_publisher.py)
(New Foundry). Dispatch happens in
[src/agentops/pipeline/orchestrator.py](../src/agentops/pipeline/orchestrator.py).

## Pre-flight checks

Before any agent invocation, [pipeline/runtime.py](../src/agentops/pipeline/runtime.py)
runs a short series of checks and reports **all** failures at once:

* Required Python packages installed (`azure-identity`,
  `azure-ai-evaluation` for AI-assisted evaluators, `azure-ai-projects`
  if `publish: foundry_cloud`).
* Required env vars set (`AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`,
  `AZURE_OPENAI_*` deployment fields).
* Azure CLI credential acquires a token within 30 s
  (`process_timeout=30` is set everywhere `DefaultAzureCredential` is
  instantiated to absorb Windows `az.cmd` cold starts).
* For URL agents, the endpoint resolves and accepts a TCP connection.

`agentops eval run --dry-run` runs only the pre-flight phase and exits
`0` (all clear) or `1` (something to fix). Useful for CI gating.

## Invocation strategies (target kind → wire call)

There is no longer a free-form `backend:` field. The invocation
strategy is derived from the target kind resolved by `classify_agent()`:

| Target kind | Invocation strategy |
|---|---|
| `foundry_prompt` | Foundry Agent Service threads/runs API via `AIProjectClient` |
| `foundry_hosted` | Direct call to the hosted endpoint with the configured `protocol` |
| `http_json` | POST `{request_field: input, ...}` and extract `response_field` (dot-path) |
| `model_direct` | Azure OpenAI chat completions via `AIProjectClient.get_openai_client()` |

`AIProjectClient.get_openai_client()` is **always called without
`api_version`** — passing one explicitly has historically caused 404s
in this codebase.

## How evaluators and metrics work

- Evaluator execution is row-first:
  - each dataset row is evaluated and can produce one or more row scores.
- Threshold evaluation is config-driven:
  - each entry in `thresholds:` maps an evaluator's score key to a comparison expression
  - each row receives a verdict per threshold
  - a row passes only if every applicable threshold passes
  - run-level threshold status is consolidated from item verdicts.
- Metrics have three levels in `results.json`:
  - `metrics`: backend/global metrics (already aggregated)
  - `row_metrics`: per-row evaluator outputs (`row_index` + metric list + optional `input`/`response` text)
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

- Every run writes its artifacts to `.agentops/results/<timestamp>/` (immutable history).
- AgentOps then refreshes `.agentops/results/latest/` with a copy of that run, so `latest/` always points at the most recent results.
- Pass `--output <dir>` to skip the default layout and write only to that path (useful for named baselines or CI artifacts).
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

## Testing

Tests live in `tests/` and are organized as:

```
tests/
├── fixtures/
│   ├── fake_eval_runner.py          # Fake backend for integration tests
│   └── fake_adapter.py              # Fake local adapter (stdin/stdout JSON echo)
├── integration/
│   └── test_eval_run_integration.py # End-to-end via local adapter backend
└── unit/
    ├── test_models.py               # Pydantic model validation
    ├── test_reporter.py             # Threshold evaluation + report
    ├── test_yaml_loader.py          # YAML loading + env-var interpolation
    ├── test_foundry_backend.py      # Foundry backend helpers (mocked)
    ├── test_http_backend.py         # HTTP backend helpers
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

## Quick Reference for New Contributors

1. **Install in dev mode**: `pip install -e ".[dev]"` or `pip install -e .` then `pip install pytest`
2. **Run tests**: `python -m pytest tests/ -x -q`
3. **Try it out**: `agentops init` then explore `.agentops/`
4. **Read the models**: `core/models.py` is the best single file to understand all data structures
5. **Follow the flow**: `cli/app.py` → `services/runner.py` → `backends/` → `core/`
6. **Keep CLI thin**: never put logic in `cli/app.py` — delegate to `services/`
7. **Keep core pure**: never import Azure SDK in `core/` — that belongs in `backends/` and `services/`
