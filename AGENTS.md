## Solution Overview

AgentOps Toolkit is a standalone Python CLI for standardized evaluation workflows targeting AI agents and model deployments, with first-class support for Microsoft Foundry Agent Service.

The repository provides:
- Reusable YAML-based evaluation configuration
- A thin CLI for workspace initialization, evaluation execution, and report regeneration
- Native Foundry execution with cloud evaluation and local fallback modes
- A normalized output contract for CI pipelines and human review

Primary capabilities:
- Evaluate Foundry agents and direct model deployments
- Run reusable bundle + dataset + run-config workflows from a local project root
- Produce machine-readable `results.json` and human-readable `report.md`
- Enforce CI-friendly exit codes for threshold gating
- Support a local adapter backend for custom evaluator pipelines via stdin/stdout JSON protocol

Public CLI contract:
- `agentops init [--prompt]`
- `agentops eval run --config <run.yaml> [--output <dir>]`
- `agentops eval compare --runs <baseline>,<current>`
- `agentops report generate --in <results.json> [--out <report.md>]`
- `agentops workflow generate [--force] [--dir <path>]`
- `agentops skills install [--platform <p>] [--prompt] [--force]`

Planned CLI stubs (not implemented in this release):
- `agentops run list|show`
- `agentops run view <id> [--entry N]`
- `agentops report show|export`
- `agentops bundle list|show`
- `agentops dataset validate|describe|import`
- `agentops config validate|show`
- `agentops trace init`
- `agentops monitor setup|show|configure`
- `agentops model list`
- `agentops agent list`

Exit code contract:
- `0` = execution succeeded and all thresholds passed
- `2` = execution succeeded but one or more thresholds failed
- `1` = runtime or configuration error

## Technical Stack

### Core Technologies

#### Language and Packaging
- **Python 3.11+**: Primary language for CLI, orchestration, schema validation, and reporting
- **setuptools + wheel**: Packaging and editable installation
- **src layout**: Package code lives under `src/agentops/`

#### CLI and Configuration
- **Typer**: Command-line interface framework
- **Pydantic v2**: Validation for YAML configs and JSON outputs
- **ruamel.yaml**: YAML parsing and serialization
- **pathlib.Path**: Canonical path handling throughout the codebase

#### Execution Backends
- **Foundry backend**: Native execution path for Microsoft Foundry Agent Service
- **HTTP backend**: Execution path for HTTP-deployed agents (LangGraph, LangChain, OpenAI, ACA, custom REST)
- **Local adapter backend**: Execution path for custom pipelines via stdin/stdout JSON protocol

### Azure and AI Runtime Integration

These dependencies are runtime integrations used by the Foundry backend and are intentionally not declared in `pyproject.toml`.

- **azure-ai-projects**: Foundry project client and `get_openai_client()` access
- **azure-ai-evaluation**: Local evaluator classes such as `SimilarityEvaluator` and `GroundednessEvaluator`
- **azure-identity**: `DefaultAzureCredential` authentication flow
- **openai**: OpenAI Evals API types used by cloud evaluation flows

Execution modes in the Foundry backend:
- **Cloud evaluation**: Uses the OpenAI Evals API through Foundry and writes `cloud_evaluation.json`
- **Local evaluation**: Uses `azure.ai.evaluation` locally when `AGENTOPS_FOUNDRY_MODE=local`

### Testing and Quality
- **pytest**: Unit and integration testing
- **Mocked Azure SDK interactions**: Tests run without Azure credentials
- **Normalized result contract**: `results.json`, `report.md`, and optional `cloud_evaluation.json`

## Repository Structure

### Root Level

```
README.md                 # Project overview and quickstart
CHANGELOG.md              # Keep a Changelog release notes
CONTRIBUTING.md           # Contribution and architecture guidance
LICENSE                   # License
SECURITY.md               # Security policy
pyproject.toml            # Python package metadata and packaged template assets
AGENTS.md                 # Project architecture and usage reference
```

### Source Layout

```
src/
└── agentops/
    ├── __init__.py
    ├── __main__.py
    │
    ├── cli/
    │   └── app.py                     # Typer CLI entrypoints
    │
    ├── core/
    │   ├── models.py                  # Pydantic schemas for configs and outputs
    │   ├── config_loader.py           # YAML -> model loading
    │   ├── thresholds.py              # Threshold evaluation rules
    │   └── reporter.py                # Markdown report generation
    │
    ├── services/
    │   ├── runner.py                  # Main evaluation orchestration
    │   ├── initializer.py             # `.agentops/` workspace scaffolding
    │   ├── reporting.py               # `results.json` -> `report.md`
    │   ├── skills.py                  # Coding agent skills installation
    │   └── foundry_evals.py           # Foundry evaluation publishing helpers
    │
    ├── backends/
    │   ├── base.py                    # Backend protocol and shared types
    │   ├── eval_engine.py             # Shared evaluation engine (evaluators, scoring, dataset utils)
    │   ├── foundry_backend.py         # Foundry cloud/local execution
    │   ├── http_backend.py            # HTTP endpoint execution (LangGraph, LangChain, OpenAI, ACA)
    │   └── local_adapter_backend.py   # Local adapter (subprocess + callable modes)
    │
    ├── utils/
    │   ├── yaml.py                    # YAML IO and interpolation helpers
    │   └── logging.py                 # Logging setup
    │
    └── templates/
        ├── config.yaml                # Seed workspace config
        ├── run.yaml                   # Seed run config (model-direct, Foundry)
        ├── run-rag.yaml               # Seed run config (RAG, Foundry)
        ├── run-agent.yaml             # Seed run config (agent-with-tools, Foundry)
        ├── run-http-model.yaml        # Seed run config (model-direct, HTTP)
        ├── run-http-rag.yaml          # Seed run config (RAG, HTTP)
        ├── run-http-agent-tools.yaml  # Seed run config (agent-with-tools, HTTP)
        ├── run-callable.yaml          # Seed run config (callable adapter)
        ├── callable_adapter.py        # Seed callable adapter function
        ├── .gitignore                 # Seed `.agentops/.gitignore`
        ├── bundles/                   # Starter bundle YAML files
        ├── datasets/                  # Starter dataset YAML configs
        ├── data/                      # Starter dataset JSONL rows
        ├── skills/                    # Coding agent skill templates
        └── workflows/                 # CI/CD workflow templates
            └── agentops-eval.yml      # GitHub Actions evaluation workflow
```

### Tests

```
tests/
├── fixtures/
│   ├── fake_eval_runner.py            # Fake backend used by integration tests
│   └── fake_adapter.py                # Fake local adapter (stdin/stdout JSON echo + callable)
├── integration/
│   └── test_eval_run_integration.py   # End-to-end via local adapter backend
└── unit/
    ├── test_models.py                 # Schema validation
    ├── test_yaml_loader.py            # YAML loading and workspace config checks
    ├── test_reporter.py               # Report generation and threshold output
    ├── test_foundry_backend.py        # Foundry backend helpers
    ├── test_http_backend.py           # HTTP backend helpers
    ├── test_initializer.py            # `.agentops/` scaffold behavior
    ├── test_local_adapter_callable.py # Callable adapter unit tests
    ├── test_cicd.py                   # CI/CD generation tests
    ├── test_cli_commands.py           # CLI command surface tests
    ├── test_comparison.py             # Run comparison tests
    ├── test_skills.py                 # Skills installation tests
    └── test_subprocess_backend.py     # Subprocess backend tests
```

### Documentation

```
docs/
├── concepts.md                                # Core concepts, ASCII diagram, evaluation scenarios
├── how-it-works.md                            # Architecture and request flow
├── bundles.md                                 # Bundle authoring guide
├── ci-github-actions.md                       # GitHub Actions CI/CD setup
├── release-process.md                         # Release and versioning process
├── tutorial-model-direct.md                  # Model-direct tutorial
├── tutorial-basic-foundry-agent.md           # Foundry agent tutorial
├── tutorial-rag.md                           # RAG tutorial
├── tutorial-http-agent.md                    # HTTP-deployed agent tutorial
├── tutorial-conversational-agent.md          # Conversational agent (Agent Framework) tutorial
├── tutorial-agent-workflow.md                # Agent workflow with tools (Agent Framework) tutorial
├── tutorial-baseline-comparison.md           # Baseline comparison tutorial
├── tutorial-copilot-skills.md                # Copilot skills tutorial
└── foundry-evaluation-sdk-built-in-evaluators.md
```

## Workspace Layout

Running `agentops init` creates the project-local evaluation workspace and installs coding agent skills.

The `.agentops/` directory:

```
.agentops/
├── config.yaml                 # Workspace defaults
├── run.yaml                    # Default run configuration
├── .gitignore
├── bundles/                    # Bundle YAML files
├── datasets/                   # Dataset YAML configs
├── data/                       # Dataset JSONL rows
└── results/                    # Timestamped history + latest pointer
```

Coding agent skills (installed by `init` and `skills install`):

```
.github/skills/                 # GitHub Copilot (default platform)
├── agentops-eval/SKILL.md
├── agentops-config/SKILL.md
├── agentops-dataset/SKILL.md
├── agentops-report/SKILL.md
├── agentops-regression/SKILL.md
├── agentops-trace/SKILL.md
├── agentops-monitor/SKILL.md
└── agentops-workflow/SKILL.md

.claude/commands/               # Claude Code (when detected or explicit)
├── agentops-eval.md
├── agentops-config.md
├── agentops-dataset.md
├── agentops-report.md
├── agentops-regression.md
├── agentops-trace.md
├── agentops-monitor.md
└── agentops-workflow.md
```

Platform auto-detection: `init` checks for `.github/copilot-instructions.md`, `.github/skills/`, `.claude/`, or `CLAUDE.md`. If no platform is detected, GitHub Copilot is used as the silent default. Pass `--prompt` to ask before installing.

Layout conventions:
- `bundles/` defines evaluation policy and enabled evaluators
- `datasets/` stores dataset YAML configs
- `data/` stores JSONL rows referenced by dataset configs
- `results/` stores immutable run outputs and `latest/`

Starter dataset configs reference JSONL files with relative paths such as:

```yaml
source:
  type: file
  path: ../data/smoke-model-direct.jsonl
```

## Configuration Model

The configuration model is layered and YAML-first.

### 1. Workspace Config
File: `.agentops/config.yaml`

Purpose:
- Stores workspace-level paths and default behavior

Key sections:
- `paths.bundles_dir`
- `paths.datasets_dir`
- `paths.data_dir`
- `paths.results_dir`
- `defaults.backend`
- `defaults.timeout_seconds`
- `report.generate_markdown`

### 2. Bundle Config
File pattern: `.agentops/bundles/*.yaml`

Purpose:
- Defines evaluators and threshold policy

Key sections:
- `evaluators[]`
- `thresholds[]`
- `metadata`

Supported evaluator sources:
- `local`
- `foundry`

### 3. Dataset Config
File pattern: `.agentops/datasets/*.yaml`

Purpose:
- Defines dataset metadata, schema mapping, and the JSONL file path

Key sections:
- `source.type`
- `source.path`
- `format.type`
- `format.input_field`
- `format.expected_field`

Dataset rows live separately in `.agentops/data/*.jsonl`.

### 4. Run Config
File: `.agentops/run.yaml`

Purpose:
- Connects one bundle, one dataset, and one target execution specification

Top-level structure:
- `version: 1` — Required
- `run` — Optional metadata (`name`, `description`)
- `target` — What is being evaluated and how (required)
- `bundle` — Evaluator bundle reference (required)
- `dataset` — Dataset reference (required)
- `execution` — Execution settings (optional)
- `output` — Output settings (optional)

`target` section:
- `type` — `agent` or `model`
- `hosting` — `local`, `foundry`, `aks`, or `containerapps`
- `execution_mode` — `local` or `remote`
- `agent_mode` — `prompt` or `hosted` (Foundry-only, optional)
- `framework` — `agent_framework`, `langgraph`, or `custom` (agent-only, optional)
- `endpoint` — Remote endpoint config (required when `execution_mode: remote`)
- `local` — Local adapter config (required when `execution_mode: local`)

`target.endpoint` fields (remote execution):
- `kind` — `foundry_agent` or `http`

Foundry agent endpoint fields:
- `agent_id` — Agent identifier
- `project_endpoint` — Foundry project URL (inline value)
- `project_endpoint_env` — Env var name holding the project URL
- `api_version` — Agent Service API version
- `poll_interval_seconds` — Polling interval for cloud eval
- `max_poll_attempts` — Max polling attempts
- `model` — Deployment name for evaluators

HTTP endpoint fields:
- `kind: http`
- `url` — Direct URL to the agent endpoint
- `url_env` — Environment variable name holding the URL (default: `AGENT_HTTP_URL`)
- `request_field` — JSON key for the user prompt (default: `message`)
- `response_field` — Dot-path to extract response text (default: `text`)
- `headers` — Static extra HTTP headers
- `auth_header_env` — Environment variable for Bearer token
- `tool_calls_field` — Dot-path to extract tool calls from response
- `extra_fields` — JSONL row field names to forward in the request body

`target.local` fields (local execution):
- `adapter` — Command string to spawn the local adapter process (subprocess mode)
- `callable` — Python function path as `module:function` (callable mode)

Exactly one of `adapter` or `callable` must be provided.

Adapter protocol: subprocess receives JSON on stdin per row, emits JSON on stdout.
Callable protocol: `fn(input_text: str, context: dict) -> dict` returning `{"response": "..."}`.

`bundle` and `dataset` references:
- `name` — Convention-based: resolves to `<workspace>/bundles/<name>.yaml` or `<workspace>/datasets/<name>.yaml`
- `path` — Explicit path (relative to config file directory)

`execution` section:
- `concurrency` — Max parallel evaluations (schema-only, default: `1`)
- `timeout_seconds` — Overall timeout (default: `300`)

`output` section:
- `path` — Output directory
- `write_report` — Generate `report.md` (default: `true`)
- `publish_foundry_evaluation` — Publish results to Foundry (default: `false`)
- `fail_on_foundry_publish_error` — Fail if Foundry publish fails (default: `false`)

Backend resolution:
- `execution_mode: local` → `LocalAdapterBackend`
- `execution_mode: remote` + `endpoint.kind: foundry_agent` → `FoundryBackend`
- `execution_mode: remote` + `endpoint.kind: http` → `HttpBackend`

Configs missing a `version` field or containing a legacy `backend` key are rejected with an actionable error message.

## Execution Model

### Main Flow

`agentops eval run` follows this sequence:

1. Load run config
2. Load referenced bundle and dataset configs
3. Resolve the backend
4. Execute evaluation
5. Read or generate backend metrics
6. Evaluate thresholds per row
7. Build normalized `results.json`
8. Generate `report.md`
9. Sync `.agentops/results/latest/`
10. Return exit code `0`, `1`, or `2`

### Backend Behavior

#### Foundry Backend
- Native support for Foundry Agent Service
- Selected when `execution_mode: remote` and `endpoint.kind: foundry_agent`
- Supports `target.type: agent` and `target.type: model`
- Cloud mode is the default
- Local fallback mode is activated with `AGENTOPS_FOUNDRY_MODE=local`

Important runtime rules:
- Do not hardcode `api_version` in `get_openai_client()` calls
- Prefer `DefaultAzureCredential(exclude_developer_cli_credential=True)`
- Azure OpenAI endpoint is derived automatically when possible

#### HTTP Backend
- Selected when `execution_mode: remote` and `endpoint.kind: http`
- Calls any HTTP-deployed agent endpoint row by row
- Supports agents deployed outside Foundry: LangGraph, LangChain, OpenAI, ACA, custom REST
- POSTs each dataset row as JSON using `request_field` as the prompt key
- Extracts model response via `response_field` (supports dot-path notation)
- Extracts tool calls via `tool_calls_field` for agent-with-tools evaluators
- Forwards extra JSONL row fields via `extra_fields` for session state, user context, etc.
- Runs local and AI-assisted evaluators using the same evaluation engine as Foundry local mode
- Produces `backend_metrics.json` with per-row scores

#### Local Adapter Backend
- Selected when `execution_mode: local`
- Spawns a local adapter process per dataset row
- Sends JSON on stdin, reads JSON on stdout
- Runs local evaluators on the adapter response
- Useful for custom evaluation pipelines integrated into the normalized AgentOps result contract

### Output Contract

Each run produces:
- `results.json`
- `report.md`

Cloud Foundry runs may also produce:
- `cloud_evaluation.json`

`results.json` contains:
- `metrics`
- `row_metrics`
- `item_evaluations`
- `run_metrics`
- `thresholds`
- `summary`

Common derived run metrics:
- `run_pass`
- `threshold_pass_rate`
- `items_total`
- `items_passed_all`
- `items_pass_rate`
- per-metric averages and standard deviations

## Evaluation Scenarios

### Model Quality
- Target: model deployment (Foundry model, HTTP endpoint, or local adapter)
- Bundle: `model_quality_baseline.yaml`
- Typical row fields: `input`, `expected`
- Evaluators: `SimilarityEvaluator`, `CoherenceEvaluator`, `FluencyEvaluator`, `F1ScoreEvaluator`, `avg_latency_seconds`

### RAG Quality
- Target: agent with retrieval (Foundry agent, HTTP endpoint, or local adapter)
- Bundle: `rag_quality_baseline.yaml`
- Typical row fields: `input`, `expected`, `context`
- Evaluators: `GroundednessEvaluator`, `RelevanceEvaluator`, `RetrievalEvaluator`, `ResponseCompletenessEvaluator`, `CoherenceEvaluator`, `avg_latency_seconds`

### Conversational Agent
- Target: chatbots, assistants, Q&A agents (Foundry agent, HTTP endpoint, or local adapter)
- Bundle: `conversational_agent_baseline.yaml`
- Typical row fields: `input`, `expected`
- Evaluators: `CoherenceEvaluator`, `FluencyEvaluator`, `RelevanceEvaluator`, `SimilarityEvaluator`, `avg_latency_seconds`

### Agent Workflow (Tools)
- Target: agent with tool calling (Foundry agent, HTTP endpoint, or local adapter)
- Bundle: `agent_workflow_baseline.yaml`
- Typical row fields: `input`, `expected`, `tool_definitions`, `tool_calls`
- Evaluators: `TaskCompletionEvaluator`, `ToolCallAccuracyEvaluator`, `IntentResolutionEvaluator`, `TaskAdherenceEvaluator`, `ToolSelectionEvaluator`, `ToolInputAccuracyEvaluator`, `avg_latency_seconds`

### Content Safety
- Target: any agent or model (Foundry agent, Foundry model, HTTP endpoint, or local adapter)
- Bundle: `safe_agent_baseline.yaml`
- Typical row fields: `input`, `expected`
- Evaluators: `ViolenceEvaluator`, `SexualEvaluator`, `SelfHarmEvaluator`, `HateUnfairnessEvaluator`, `ProtectedMaterialEvaluator`, `avg_latency_seconds`
- Requirements: `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` (safety evaluators use `azure_ai_project`, not `model_config`)

### Scenario × Target Matrix

| Scenario | Foundry Agent | Foundry Model | HTTP (LangGraph/LangChain/OpenAI/ACA) | Local Adapter |
|---|---|---|---|---|
| Model Quality | — | ✓ run.yaml | ✓ run-http-model.yaml | ✓ (custom) |
| RAG Quality | ✓ run-rag.yaml | — | ✓ run-http-rag.yaml | ✓ (custom) |
| Agent Workflow | ✓ run-agent.yaml | — | ✓ run-http-agent-tools.yaml | ✓ (custom) |
| Content Safety | ✓ (custom) | ✓ (custom) | ✓ (custom) | ✓ (custom) |

## Azure Runtime Notes

Authentication:
- Local development: `az login`
- CI/CD: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET`
- Azure-hosted environments: managed identity

Important environment variables:
- `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`
- `AGENTOPS_FOUNDRY_MODE`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_DEPLOYMENT`
- `AZURE_AI_MODEL_DEPLOYMENT_NAME`
- `AZURE_OPENAI_API_VERSION`

Recommended default behavior:
- Keep Foundry cloud mode as the default path
- Install Azure runtime dependencies separately from the base package
- Keep Azure SDK imports inside functions in `backends/` and `services/`
- Configure model deployments explicitly per project; do not assume a universally available default deployment name in Foundry

## Architectural Constraints

### Code Organization
- Keep `cli/app.py` thin
- Keep `core/` pure: no Azure SDK imports and no network calls
- Put orchestration in `services/`
- Put execution engines in `backends/`
- Use `pathlib.Path` consistently
- Avoid module-level side effects and hidden global state

### Public Contracts
- Do not change exit code meaning
- Do not add CLI commands or flags unless intentionally expanding the product contract
- Preserve `results.json` and `report.md` as stable outputs

### Foundry-Specific Rules
- Avoid passing explicit `api_version` into `get_openai_client()`
- Keep Azure imports lazy
- Preserve support for both cloud evaluation and local fallback

## Testing

Recommended commands:

```bash
python -m pip install -e .
python -m pip install pytest
python -m pytest tests/ -x -q
```

Additional useful commands:

```bash
python -m pytest tests/unit -q
python -m pytest tests/integration -q
python -m pytest tests/unit/test_models.py -q
```

Testing rules:
- Azure SDK calls should be mocked in tests
- Unit tests go in `tests/unit/`
- Integration tests go in `tests/integration/`
- Tests should verify exit code behavior when relevant

## Quick Reference

Read first:
- `docs/how-it-works.md`
- `CONTRIBUTING.md`
- `README.md`

Key source files:
- `src/agentops/core/models.py`
- `src/agentops/services/runner.py`
- `src/agentops/backends/foundry_backend.py`
- `src/agentops/services/initializer.py`

Most common local flow:

```bash
python -m pip install -e .
python -m pip install pytest
agentops init
agentops eval run
agentops report generate
python -m pytest tests/ -x -q
```