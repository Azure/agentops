# Copilot Instructions for AgentOps

## Project Overview

AgentOps is a **standalone Python CLI** that helps developers run **standardized evaluation workflows** for **Microsoft Foundry agents** using reusable **evaluation bundles**.

The CLI:
- Is installed via `pip`
- Uses YAML configuration
- Executes evaluations against Foundry Agent Service agents
- Supports **cloud evaluation** (New Foundry Experience) and **local evaluation** (fallback)
- Produces normalized outputs:
  - `results.json` (machine-readable)
  - `report.md` (human-readable, PR-friendly)
- Returns **CI-friendly exit codes** to gate pipelines on quality thresholds

Design documentation lives in `docs/`:
- `docs/how-it-works.md` — Architecture, source code layout, config schema, request flow
- `docs/tutorial-basic-foundry-agent.md` — End-to-end tutorial (agent target)
- `docs/tutorial-model-direct.md` — Model-direct evaluation tutorial
- `docs/tutorial-rag.md` — RAG evaluation tutorial
- `docs/foundry-evaluation-sdk-built-in-evaluators.md` — Evaluator reference

Contribution guidelines live in `CONTRIBUTING.md` at the repo root.

---

## Technology Choices

- **Language**: Python 3.11+
- **CLI framework**: Typer
- **Config & schema validation**: Pydantic v2
- **Configuration format**: YAML
- **Primary backend**: Microsoft Foundry Agent Service (native)
  - Cloud evaluation via OpenAI Evals API (New Foundry Experience)
  - Local evaluation via `azure-ai-evaluation` SDK (fallback)
- **Secondary backend**: subprocess-based (generic)
- **Azure SDK dependencies** (runtime, for Foundry backend):
  - `azure-ai-projects>=2.0.0b1` — Foundry project client, `get_openai_client()`
  - `azure-ai-evaluation` — Local evaluator classes (SimilarityEvaluator, etc.)
  - `azure-identity` — `DefaultAzureCredential` authentication
  - `openai` — Evals API types (`DataSourceConfigCustom`, etc.)
- **Installation**: agentops is intended to be installed within the project's virtual environment, following the same usage pattern as tools like pytest or MkDocs. This ensures versions are pinned to the project and runs are fully reproducible. Once installed via pip in the project environment, it can be executed either through the `agentops` command or using `python -m agentops`, and all commands (`init`, `run`) are expected to be run from the project root.

Azure SDK dependencies are **not** declared in `pyproject.toml` — they are runtime dependencies that users install separately (documented in the tutorial).

---

## CLI Command Surface (fixed contract)

The CLI command name is `agentops`.

Only the following commands are in scope:

- `agentops init`
- `agentops eval run --config <run.yaml> [--output <dir>]`
- `agentops report --in <results.json> [--out <report.md>]`

Do not add new commands or flags unless explicitly discussed.

---

## Exit Code Contract (critical)

Exit codes are part of the public API and **must be respected everywhere**:

- `0` → execution succeeded **and** all thresholds passed
- `2` → execution succeeded **but** one or more thresholds failed
- `1` → runtime or configuration error

Do not overload or reinterpret these codes.

---

## Architecture Rules

See `docs/how-it-works.md` for the full source-code map and architecture diagrams.

- Use **Python src layout** (`src/agentops/`)
- Keep CLI command handlers **thin** (`cli/app.py`) — only parse args and call `services/`
- Place business logic in:
  - `core/` — config loading, Pydantic models, thresholds, report generation. **Must have zero Azure SDK imports and zero network calls.**
  - `services/` — orchestration (runner), Foundry publishing, workspace init, report regen
  - `backends/` — execution backends (Foundry, subprocess). Each implements the `Backend` protocol from `base.py`.
- Use `pathlib.Path` everywhere (no raw string paths)
- No side effects at import time
- No hidden global state
- Azure SDK imports are **lazy** (`import` inside functions), not top-level
- Prefer small, focused functions
- Explicit, user-friendly error messages

### Where to add new code

| I want to… | Directory / File |
|---|---|
| Add a new Pydantic model or schema field | `core/models.py` |
| Add a new config file type | `core/config_loader.py` + `core/models.py` |
| Add a new local evaluator | `backends/foundry_backend.py` (local eval path) |
| Add a new execution backend | `backends/` (new file implementing `Backend` protocol) + register in `services/runner.py` |
| Add a new CLI command | `cli/app.py` (thin handler) + `services/` (logic) |
| Add a new workflow/service | `services/` (new file) |
| Add starter templates | `templates/` + update `pyproject.toml` package-data |

---

## Foundry Backend Architecture (critical)

The Foundry backend (`backends/foundry_backend.py`) is the largest and most complex module. Key architecture:

### Execution Modes

1. **Cloud evaluation** (default) — Uses the OpenAI Evals API via Foundry:
   - `project_client.get_openai_client()` — **never pass `api_version`** (SDK picks the correct one)
   - `client.evals.create()` with `azure_ai_evaluator` testing criteria
   - `client.evals.runs.create()` with `azure_ai_target_completions` data source
   - Results appear in the **New Foundry Experience** Evaluations page
   - Writes `cloud_evaluation.json` with `report_url` for downstream reporting
   - Reference: https://learn.microsoft.com/azure/foundry/how-to/develop/cloud-evaluation

2. **Local evaluation** (fallback) — Set `AGENTOPS_FOUNDRY_MODE=local`:
   - Invokes the agent via REST API (Agent Service responses/threads endpoint)
   - Runs `azure.ai.evaluation` evaluator classes locally
   - Publishes results to Foundry via OneDP (`_log_metrics_and_instance_results_onedp`)
   - Results appear in the **Classic Foundry Experience**

### Key Rules

- **Never hardcode `api_version`** when calling `get_openai_client()` — the SDK handles this. Previous 404 errors were caused by explicit `api_version` parameters.
- Use `DefaultAzureCredential(exclude_developer_cli_credential=True)` for authentication.
- Auto-derive Azure OpenAI endpoint from the project endpoint via `_derive_openai_endpoint_from_project()` — users should not need to set `AZURE_OPENAI_ENDPOINT` manually.
- Agent invocation supports both reference-based and threads-based API calls.
- Evaluator names map from class names to builtins: `SimilarityEvaluator` → `builtin.similarity`.

### Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `AGENTOPS_FOUNDRY_MODE` | `cloud` (New Experience) or `local` (Classic) | `cloud` |
| `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` | Foundry project endpoint URL | Required |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint (auto-derived if absent) | Auto-derived |
| `AZURE_OPENAI_DEPLOYMENT` | Model deployment name (auto-derived if absent) | Auto-derived |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Explicit model deployment name override | No project-universal default deployment |
| `AZURE_OPENAI_API_VERSION` | OpenAI API version for local evaluators | SDK default |

---

## Configuration Model

Configuration is **YAML-first** and layered:

- `.agentops/config.yaml` → workspace defaults
- bundle YAML → evaluators + thresholds (see `docs/how-it-works.md` for schema)
- dataset YAML config (`.yaml`) → dataset reference and metadata, including the path to JSONL rows
- dataset JSONL → evaluation rows, typically stored separately under `.agentops/data/`
- run YAML → concrete run specification (backend, agent, model, dataset, bundle)
- CLI flags override YAML

By default, `agentops init` keeps dataset YAML configs in `.agentops/datasets/` and dataset rows in `.agentops/data/`.

Schemas are validated using **Pydantic v2 models** (`core/models.py`).

Both config files and results files must include a `version` field.

### Foundry-specific run.yaml fields

The `backend` section for Foundry runs uses `type: foundry` (not `name`). Fields:
- `type: foundry` — Backend type selector (must be `foundry` or `subprocess`)
- `target` — `agent` (default) or `model`
- `agent_id` — Agent identifier, e.g. `my-agent:3` (name:version); required when `target: agent`
- `model` — Deployment name that already exists in the Foundry project; used when `target: model` or for evaluators
- `project_endpoint` — Foundry project URL (inline value)
- `project_endpoint_env` — Env var name holding the project URL (default: `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`)
- `api_version` — Agent Service API version (default: `2025-05-01`)
- `poll_interval_seconds` — Polling interval for cloud eval (default: 2.0)
- `max_poll_attempts` — Max polling attempts (default: 120)
- `timeout_seconds` — Overall timeout for the backend execution

The backend resolves the project endpoint by checking `project_endpoint` first, then falling back to `os.getenv(project_endpoint_env)`.

---

## Outputs

Every evaluation run must produce:

- `results.json`
  - normalized, versioned schema
  - stable and machine-readable
- `report.md`
  - human-readable summary
  - suitable for PR reviews

`agentops report` must be able to regenerate `report.md` from `results.json`.

When cloud evaluation is used, a `cloud_evaluation.json` is also produced containing:
- `eval_id`, `run_id` — OpenAI Evals API identifiers
- `report_url` — Deep-link to the New Foundry Experience Evaluations page

---

## Testing Expectations

- Unit tests for:
  - config parsing and validation (`test_models.py`)
  - threshold evaluation (`test_reporter.py`)
  - YAML loading (`test_yaml_loader.py`)
  - report generation
  - Foundry backend helpers (`test_foundry_backend.py`)
  - Subprocess backend (`test_subprocess_backend.py`)
  - Initializer (`test_initializer.py`)
- Integration test for:
  - `agentops eval run` end-to-end using a fake subprocess backend (`test_eval_run_integration.py`)
- Tests must assert correct **exit codes**
- Azure SDK calls in tests should be **mocked** — tests must run without Azure credentials
- Run all tests: `python -m pytest tests/ -x -q`

---

## Out of Scope

Do not implement the following unless explicitly discussed:

- Remote bundle registries
- Dataset ingestion pipelines
- Interactive prompts
- Web UI or dashboards

---

## Copilot Guidance

## Workflow Skills

This repository also defines workflow-oriented Copilot skills under `.github/skills/`.

- Use these skills for operational guidance on running evaluations, investigating regressions, observability triage, and release management workflows.
- Treat the CLI as the source of truth and keep planned/stubbed commands clearly marked as not yet implemented.
- Do not duplicate architecture or code-structure guidance from this file inside workflow skills.

When generating or modifying code:

- **Read `docs/how-it-works.md` first** — it is the single source of truth for architecture
- **Read `CONTRIBUTING.md`** for contribution rules and workflow
- Do not invent new concepts or commands
- Prefer clarity and determinism over cleverness
- Optimize for maintainability and CI usage
- Azure SDK imports must be **lazy** (inside functions, not top-level)
- Never hardcode Azure API versions — let the SDK handle versioning
- Keep user-facing log output clean — no warning cascades or retry noise
- When adding evaluator support, update both cloud (`_cloud_evaluator_data_mapping` + `_cloud_evaluator_needs_model`) and local paths
- All new logic must have corresponding unit tests in `tests/unit/`
- Always mock Azure SDK calls in tests — tests must run without credentials
- The `core/` package must remain free of Azure imports and I/O
- Follow the request flow: CLI → Services → Backends → Core (never skip layers)
- If a change is user-visible, add an entry to `CHANGELOG.md` under `[Unreleased]` (Keep a Changelog format)
