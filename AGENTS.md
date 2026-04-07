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
- Support a generic subprocess backend for custom evaluator pipelines

Public CLI contract:
- `agentops init`
- `agentops eval run --config <run.yaml> [--output <dir>]`
- `agentops eval compare --runs <baseline>,<current>`
- `agentops report --in <results.json> [--out <report.md>]`

Planned CLI stubs (not implemented in this release):
- `agentops run list|show`
- `agentops run view <id> [--entry N]`
- `agentops report show|export`
- `agentops bundle list|show`
- `agentops dataset validate|describe|import`
- `agentops config validate|show|cicd`
- `agentops trace init`
- `agentops monitor setup|dashboard|alert`
- `agentops model list`
- `agentops agent list`

Exit code contract:
- `0` = execution succeeded and all thresholds passed
- `2` = execution succeeded but one or more thresholds failed
- `1` = runtime or configuration error

---

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
- **Subprocess backend**: Generic execution path for custom pipelines that emit `backend_metrics.json`

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

---

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
    │   └── foundry_evals.py           # Foundry evaluation publishing helpers
    │
    ├── backends/
    │   ├── base.py                    # Backend protocol and shared types
    │   ├── foundry_backend.py         # Foundry cloud/local execution
    │   └── subprocess_backend.py      # Generic subprocess integration
    │
    ├── utils/
    │   ├── yaml.py                    # YAML IO and interpolation helpers
    │   ├── logging.py                 # Logging setup
    │   └── telemetry.py               # Optional OTLP tracing (lazy imports)
    │
    └── templates/
        ├── config.yaml                # Seed workspace config
        ├── run.yaml                   # Seed run config
        ├── .gitignore                 # Seed `.agentops/.gitignore`
        ├── bundles/                   # Starter bundle YAML files
        ├── datasets/                  # Starter dataset YAML configs
        └── data/                      # Starter dataset JSONL rows
```

### Tests

```
tests/
├── fixtures/
│   └── fake_eval_runner.py            # Fake backend used by integration tests
├── integration/
│   └── test_eval_run_integration.py   # End-to-end subprocess workflow
└── unit/
    ├── test_models.py                 # Schema validation
    ├── test_yaml_loader.py            # YAML loading and workspace config checks
    ├── test_reporter.py               # Report generation and threshold output
    ├── test_foundry_backend.py        # Foundry backend helpers
    ├── test_subprocess_backend.py     # Subprocess backend behavior
    └── test_initializer.py            # `.agentops/` scaffold behavior
```

### Documentation

```
docs/
├── how-it-works.md                            # Architecture and request flow
├── tutorial-basic-foundry-agent.md           # Foundry agent tutorial
├── tutorial-model-direct.md                  # Model-direct tutorial
├── tutorial-rag.md                           # RAG tutorial
└── foundry-evaluation-sdk-built-in-evaluators.md
```

---

## Workspace Layout

Running `agentops init` creates the project-local evaluation workspace:

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

---

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
- Connects one bundle, one dataset, and one backend execution target

Foundry backend fields:
- `type: foundry`
- `target: agent | model`
- `agent_id`
- `model`
- `project_endpoint`
- `project_endpoint_env`
- `api_version`
- `poll_interval_seconds`
- `max_poll_attempts`
- `timeout_seconds`

Subprocess backend fields:
- `type: subprocess`
- `command`
- `args`
- `env`
- `timeout_seconds`

---

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
- Supports `target: agent` and `target: model`
- Cloud mode is the default
- Local fallback mode is activated with `AGENTOPS_FOUNDRY_MODE=local`

Important runtime rules:
- Do not hardcode `api_version` in `get_openai_client()` calls
- Prefer `DefaultAzureCredential(exclude_developer_cli_credential=True)`
- Azure OpenAI endpoint is derived automatically when possible

#### Subprocess Backend
- Executes an external command
- Expects the subprocess to write `backend_metrics.json`
- Useful when integrating a custom scoring pipeline into the normalized AgentOps result contract

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

---

## Evaluation Scenarios

### Model-Direct
- Target: model deployment
- Bundle: `model_direct_baseline.yaml`
- Typical row fields: `input`, `expected`
- Primary evaluator pattern: semantic similarity + latency

### RAG
- Target: Foundry agent with retrieval
- Bundle: `rag_retrieval_baseline.yaml`
- Typical row fields: `input`, `expected`, `context`
- Primary evaluator pattern: groundedness + latency

### Agent with Tools
- Target: Foundry agent
- Bundle: `agent_tools_baseline.yaml`
- Current status: placeholder baseline ready for expansion

---

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
- `AGENTOPS_OTLP_ENDPOINT` — OTLP collector URL for evaluation tracing (opt-in, e.g. `http://localhost:4318`)

Recommended default behavior:
- Keep Foundry cloud mode as the default path
- Install Azure runtime dependencies separately from the base package
- Keep Azure SDK imports inside functions in `backends/` and `services/`
- Configure model deployments explicitly per project; do not assume a universally available default deployment name in Foundry

---

## OTLP Telemetry

AgentOps can optionally emit OpenTelemetry (OTLP) traces during evaluation runs. Set `AGENTOPS_OTLP_ENDPOINT` to enable.

```bash
# Enable tracing (e.g. AI Toolkit collector, Azure Monitor, Jaeger)
export AGENTOPS_OTLP_ENDPOINT=http://localhost:4318
agentops eval run
```

Span schema uses three OTel semantic convention layers:

| Layer | Namespace | Purpose |
|---|---|---|
| CICD | `cicd.pipeline.*` | Eval run as pipeline, items as tasks |
| GenAI | `gen_ai.*` | Agent/model invocation (future Layer 2) |
| AgentOps | `agentops.eval.*` | Evaluator scores, thresholds, pass/fail |

Design rules:
- All OpenTelemetry imports are **lazy** (inside `utils/telemetry.py` functions)
- Zero overhead when `AGENTOPS_OTLP_ENDPOINT` is unset
- Graceful no-op when `opentelemetry-sdk` is not installed
- `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http` are optional runtime dependencies (not in `pyproject.toml`)

---

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

---

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

---

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
agentops report
python -m pytest tests/ -x -q
```