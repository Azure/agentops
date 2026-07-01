## Solution Overview

AgentOps Toolkit is a CLI, local Cockpit, and agent skills that help teams
answer two release questions for Microsoft Foundry agents: can we ship it, and
where is the proof? Foundry runs the agent. AgentOps proves the release is ready
with standardized evaluation gates, readiness diagnostics, release evidence, and
trace-driven regression loops.

The repository provides:
- A single flat `agentops.yaml` configuration file at the project root
- An interactive `agentops init` wizard that bootstraps the workspace and local
  AgentOps environment values, with azd environment support when requested
- Native Foundry execution with cloud (OpenAI Evals API) and local fallback
- A normalized output contract (`results.json`, `report.md`) for CI and PRs
- A release evidence contract (`evidence.json`, `evidence.md`) for production promotion reviews
- A local Cockpit (`agentops cockpit`) that links out to Foundry for runtime
  observability and surfaces Doctor findings AgentOps handles end-to-end
- A Doctor (`agentops doctor`) for readiness, regression, and OpEx checks
- AI Landing Zone deployment readiness checks that connect official preflight,
  azd/Bicep workflow deployment, AgentOps eval gates, and private-network runner
  planning
- Coding-agent skills (Copilot, Claude Code) installed alongside the workspace

Primary capabilities:
- Evaluate Foundry prompt agents, Foundry hosted agents, raw model deployments,
  and any HTTP/JSON agent (LangGraph, LangChain, ACA, AKS, custom REST)
- Auto-infer evaluators from the agent type and dataset columns
- Produce machine-readable `results.json` and human-readable `report.md`
- Enforce CI-friendly exit codes for threshold gating
- Publish results to the New Foundry (cloud) or Classic Foundry (local) panels
- Generate azd-first CI/CD workflows that run the official AI Landing Zone
  preflight when present, then provision/deploy through azd and validate with
  Doctor/eval gates
- Promote reviewed production trace exports into regression dataset candidates
  so runtime learnings become future eval gates

Public CLI contract:
- `agentops --version`
- `agentops explain [COMMAND...] [--no-pager] [--format text|markdown|html] [--out PATH] [--open]`
- `agentops init [--force] [--dir PATH] [--no-prompt] [--no-appinsights] [--azd-env NAME] [--project-endpoint URL] [--agent REF] [--dataset PATH] [--appinsights-connection-string STR]`
- `agentops init show [--dir PATH] [--reveal-secrets]`
- `agentops init explain [--no-pager] [--format text|markdown|html] [--out PATH] [--open]`
- `agentops prompt pull [--config PATH] [--out PATH] [--project-endpoint URL] [--force] [--update-config|--no-update-config]`
- `agentops eval analyze [--dir PATH] [--format text|markdown|json] [--out PATH]`
- `agentops eval init [--dir PATH] [--force]`
- `agentops eval run [--config PATH] [--baseline PATH] [--output DIR]`
- `agentops eval promote-traces --source PATH [--out PATH] [--max-rows N] [--label-mode self-similarity|pending] [--apply]`
- `agentops report generate [--in PATH] [--out PATH]`
- `agentops workflow analyze [--dir PATH] [--format text|markdown|json] [--out PATH]`
- `agentops workflow generate [--force] [--dir PATH] [--kinds pr,dev,qa,prod,doctor] [--platform github|azure-devops] [--deploy-mode auto|placeholder|azd|prompt-agent] [--doctor-gate critical|warning|none]`
- `agentops skills install [--platform copilot|claude] [--from SOURCE] [--prompt] [--force] [--dir PATH]`
- `agentops mcp serve`
- `agentops doctor [--workspace PATH] [--config PATH] [--out PATH] [--lookback-days N] [--severity-fail SEVERITY] [--evidence-pack] [--evidence-out PATH]`
- `agentops doctor explain [--no-pager] [--format text|markdown|html] [--out PATH] [--open]`
- `agentops cockpit [--host HOST] [--port PORT] [--workspace PATH] [--no-preflight]`
- `agentops agent serve [--host HOST] [--port PORT] [--config PATH] [--no-verify] [--workers N]`

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

#### Execution Engines
- **Local runtime** (`pipeline/runtime.py`): Invokes the agent row-by-row and
  runs `azure.ai.evaluation` evaluators locally. Default for HTTP agents,
  Foundry hosted agents, and raw model deployments.
- **Cloud runner** (`pipeline/cloud_runner.py`): Submits a Foundry prompt
  agent run to the OpenAI Evals API via the New Foundry experience.

### Azure and AI Runtime Integration

These dependencies are runtime integrations used by the Foundry backend and are intentionally not declared in `pyproject.toml`.

- **azure-ai-projects**: Foundry project client and `get_openai_client()` access
- **azure-ai-evaluation**: Local evaluator classes such as `SimilarityEvaluator` and `GroundednessEvaluator`
- **azure-identity**: `DefaultAzureCredential` authentication flow
- **openai**: OpenAI Evals API types used by cloud evaluation flows

Execution modes:
- **Cloud evaluation**: Uses the OpenAI Evals API through Foundry and writes
  `cloud_evaluation.json` with a deep-link to the New Foundry Evaluations page
- **Local evaluation**: Uses `azure.ai.evaluation` locally and optionally
  publishes to the Classic Foundry Evaluations panel (`publish: true`)

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
    │   └── app.py                     # Typer CLI entry points (init, eval, report,
    │                                   #  workflow, skills, mcp, doctor, cockpit,
    │                                   #  agent serve, explain)
    │
    ├── core/
│   ├── agentops_config.py         # Flat 1.0 `agentops.yaml` Pydantic model
│   ├── config_loader.py           # YAML → model loading and validation
│   ├── evaluators.py              # Evaluator auto-selection rules
│   ├── release_evidence.py        # Stable release evidence schema
│   └── results.py                 # `results.json` schema and helpers
    │
    ├── services/
    │   ├── initializer.py             # `agentops.yaml` + `.agentops/` scaffolding
    │   ├── setup_wizard.py            # azd-style interactive wizard (init flow)
    │   ├── preflight.py               # Pre-flight checks shared by doctor / cockpit
    │   ├── skills.py                  # Coding agent skill installation
│   ├── cicd.py                    # GitHub Actions / Azure DevOps templates
│   ├── evidence_pack.py           # `doctor --evidence-pack` writer
│   └── trace_promotion.py         # Trace export → reviewable dataset candidates
    │
    ├── pipeline/
    │   ├── runtime.py                 # Local-execution engine for one run
    │   ├── cloud_runner.py            # Cloud-execution (OpenAI Evals API) engine
    │   ├── orchestrator.py            # End-to-end `eval run` orchestration
    │   ├── invocations.py             # Foundry/HTTP/model invocation adapters
    │   ├── publisher.py               # Foundry Evaluations publishing
    │   ├── reporter.py                # `results.json` → `report.md`
    │   ├── comparison.py              # Baseline-vs-current diffing
    │   ├── thresholds.py              # Threshold expression evaluation
    │   └── diagnostics.py             # Pipeline-level diagnostics
    │
    ├── agent/                         # AgentOps Doctor + Cockpit
    │   ├── analyzer.py                # Doctor analyzer (severity rollup)
    │   ├── cockpit.py                 # FastAPI cockpit server (read-only UI)
    │   ├── history.py                 # `.agentops/agent/history.jsonl` writer/reader
    │   ├── report.py                  # Doctor Markdown report
    │   ├── production_telemetry.py    # App Insights production card
    │   ├── checks/                    # Individual doctor checks (foundry config,
    │   │                              #  regression, opex, spec conformance, …)
    │   ├── sources/                   # Data sources (results history, Azure
    │   │                              #  resources, Azure Monitor, Foundry CP)
    │   ├── llm_assist/                # Optional LLM-assisted findings
    │   └── knowledge/                 # WAF checklist + curated knowledge
    │
    ├── mcp/                           # MCP server (`agentops mcp serve`)
    │
    ├── utils/
    │   ├── azd_env.py                 # Filesystem-only `.azure/<env>/` management
    │   ├── dotenv_loader.py           # Auto-load `.agentops/.env` at import time
    │   ├── foundry_discovery.py       # Foundry endpoint / App Insights discovery
    │   ├── colors.py                  # ANSI styling helpers
    │   ├── telemetry.py               # OpenTelemetry exporter wiring
    │   ├── yaml.py                    # YAML IO and interpolation helpers
    │   └── logging.py                 # Logging setup
    │
    └── templates/
        ├── agentops.yaml              # Seed flat 1.0 config
        ├── smoke.jsonl                # Seed JSONL dataset (copied to .agentops/data/)
        ├── agent.yaml                 # Seed Doctor config
        ├── waf-checklist.csv          # Doctor WAF AI-security checklist seed
        ├── waf-checklist.README.md
        ├── .gitignore                 # Seed workspace `.gitignore`
        ├── project.gitignore          # Seed project-root `.gitignore` snippet
        ├── icon.png                   # Cockpit favicon
        ├── foundry.svg                # Cockpit Foundry mark
        ├── skills/                    # Coding-agent skill templates
        ├── workflows/                 # GitHub Actions templates (PR + 3 deploys
        │                              #  + scheduled Doctor)
        ├── pipelines/                 # Azure DevOps pipeline templates
        └── agent-server/              # Doctor-as-Copilot-Extension deploy scaffold
            ├── Dockerfile
            ├── main.bicep
            └── README.md
```

### Tests

```
tests/
├── fixtures/                          # Shared fakes for evaluator + adapter flows
├── integration/                       # End-to-end eval and cockpit flows
└── unit/                              # Pure-Python unit tests (no Azure creds)
```

Coverage highlights:
- `core/` — `agentops.yaml` schema validation, config loader, evaluator inference
- `pipeline/` — local + cloud runner, comparison, thresholds, reporter
- `services/` — initializer, setup wizard, skills, CI/CD generators, preflight
- `agent/` — Doctor checks, history rollup, cockpit endpoints, telemetry sources
- `cli/` — Typer command surface, explain pages, init banner + wizard
- `utils/` — azd env bootstrap, dotenv loader, foundry discovery

### Documentation

```
docs/
├── concepts.md                                # Core concepts and evaluation scenarios
├── how-it-works.md                            # Architecture and request flow
├── tutorial-prompt-agent.md                   # Tutorial for Foundry Prompt Agents
├── tutorial-hosted-agent.md                   # Tutorial for Foundry hosted / HTTP agents
├── tutorial-end-to-end.md                     # Full end-to-end release-readiness tutorial (Foundry + AgentOps)
├── doctor-explained.md                        # Doctor checks, history, and operations model
├── ci-github-actions.md                       # CI/CD setup
├── release-process.md                         # Release and versioning
└── foundry-evaluation-sdk-built-in-evaluators.md
```

## Workspace Layout

`agentops init` is the single onboarding command. It is idempotent and combines four phases:

1. **Scaffold** the `.agentops/` workspace and copy seed templates
2. **Wizard** — azd-style interactive prompts (suppressed with `--no-prompt`
   or when every required value is supplied via flags)
3. **Skills** — install coding-agent skills on demand with `agentops skills install`

Each wizard answer is persisted immediately to disk (`.agentops/.env` for
AgentOps-owned local Azure values, or `.azure/<env>/.env` when an azd env is
already active or `--azd-env` is explicit; `agentops.yaml` for the agent/dataset
reference). Re-running `init` re-uses existing values and only re-prompts for
blanks.

The `.agentops/` directory:

```
.agentops/
├── data/                       # JSONL dataset rows (seed: smoke.jsonl)
├── .env                        # Local Azure values (ignored)
├── results/                    # Timestamped run history + `latest/` pointer
├── agent/                      # Doctor history (history.jsonl + per-run dirs)
└── .gitignore                  # Local-only artifacts (results, secrets, …)
```

The project root after `init`:

```
<project>/
├── agentops.yaml               # Flat 1.0 config (single source of truth)
├── .agentops/                  # Local-only run history, datasets, Doctor cache
├── .azure/                     # Optional azd-compatible env folder
│   ├── config.json             # `defaultEnvironment` pointer
│   ├── .gitignore              # Excludes every <env>/.env
│   └── <env>/
│       └── .env                # AZURE_AI_FOUNDRY_PROJECT_ENDPOINT,
│                                #  APPLICATIONINSIGHTS_CONNECTION_STRING,
│                                #  AZURE_OPENAI_ENDPOINT, …
└── (one of) .github/skills/  or  .claude/commands/
```

Coding agent skills (installed by `skills install`):

```
.github/skills/                 # GitHub Copilot (default platform)
├── agentops-eval/SKILL.md
├── agentops-config/SKILL.md
├── agentops-dataset/SKILL.md
├── agentops-report/SKILL.md
├── agentops-regression/SKILL.md
├── agentops-trace/SKILL.md
├── agentops-monitor/SKILL.md
├── agentops-workflow/SKILL.md
└── agentops-agent/SKILL.md

.claude/commands/               # Claude Code (when detected or explicit)
├── agentops-eval.md
├── …
└── agentops-agent.md
```

Platform auto-detection: `init` checks for `.github/copilot-instructions.md`,
`.github/skills/`, `.claude/`, or `CLAUDE.md`. If no platform is detected,
GitHub Copilot is used as the silent default. Pass `--prompt` to ask before
installing; pass `agentops skills install --platform copilot|claude` to install
explicitly later.

## Configuration Model

AgentOps 1.0 uses a single flat `agentops.yaml` file at the project root —
there are no separate bundles, dataset YAMLs, or run files. Evaluators and
thresholds are inferred from the agent type and dataset columns, with
sensible defaults that can be overridden field-by-field.

### `agentops.yaml`

Minimal example:

```yaml
version: 1
agent: "my-agent:1"
dataset: .agentops/data/smoke.jsonl
```

Full schema:

| Field | Required | Description |
|---|---|---|
| `version` | yes | Schema version. Must be `1`. |
| `agent` | yes | One of: `name:version` (Foundry prompt agent), `https://...` (Foundry hosted endpoint or any HTTP/JSON agent), `model:<deployment>` (raw Foundry model deployment). |
| `dataset` | yes | Relative path to a JSONL file with one row per evaluation example. |
| `thresholds` | no | Map of metric → expression (e.g. `coherence: ">=3"`, `avg_latency_seconds: "<=30"`). Missing keys fall back to auto-defaults. |
| `evaluators` | no | Advanced escape hatch: explicit list of evaluator names that overrides auto-selection. |
| `project_endpoint` | no | Foundry project endpoint. Wins over `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` when both are set. |
| `execution` | no | `local` (default) — AgentOps runs the agent row-by-row locally. `cloud` — Foundry runs the agent + evaluators server-side (only valid for `name:version` agents). `azd` — delegates execution to `azd ai agent eval` and normalizes the emitted metrics. |
| `eval_recipe` | no | Optional path to azd `eval.yaml` when `execution: azd`; omitted values auto-discover a single recipe. |
| `publish` | no | When `execution: local`, set to `true` to also upload results to the Classic Foundry Evaluations panel. With `execution: cloud` publishing is implicit. |
| `protocol` | no | URL-based agents only. `responses` (default Foundry hosted), `invocations` (Foundry hosted with raw JSON), or `http-json` (default generic HTTP). |
| `request_field` | no | HTTP / invocations only. JSON key that carries the user prompt (default: `message`). |
| `response_field` | no | HTTP / invocations only. Dot-path to extract the response text (default: `text`). |
| `tool_calls_field` | no | HTTP / invocations only. Dot-path to extract tool calls for agent-workflow evaluators. |
| `headers` | no | HTTP / invocations only. Static extra HTTP headers. |
| `auth_header_env` | no | HTTP / invocations only. Environment variable that holds the auth token. |
| `auth_header_name` | no | HTTP / invocations only. Header name for the auth token (default: `Authorization`). |
| `auth_value_template` | no | HTTP / invocations only. Template for the auth header value; `{token}` is replaced by the `auth_header_env` value (default: `Bearer {token}`). |
| `response_mode` | no | HTTP / invocations only. `json` (default, single `json.loads` of the body), `sse` (parse `data:` lines), or `text` (concatenate raw streamed text). |
| `stream` | no | HTTP / invocations only. Streaming aggregation block for `response_mode: sse|text`: `text_field` (dot-path to token text in JSON `data:` lines), `done_marker` (stop token, e.g. `[DONE]`), `strip_leading_token` (drop the first whitespace-delimited token, e.g. a `conversation_id` prefix). |
| `assert_path` | no | Optional ASSERT policy/results file or directory referenced by Doctor/evidence. AgentOps does not execute ASSERT. |
| `acs_path` | no | Optional Agent Control Specification contract file or directory referenced by Doctor/evidence. AgentOps does not apply ACS controls. |
| `redteam_path` | no | Optional red-team plan/results evidence path. AgentOps records metadata and never exposes payload text. |

### Agent Type Inference

| `agent:` value | Resolved as |
|---|---|
| `my-agent:3` | Foundry prompt agent (name + version) |
| `https://<resource>.services.ai.azure.com/api/projects/<project>/agents/...` | Foundry hosted agent (REST) |
| `https://api.example.com/chat` | Generic HTTP/JSON agent (ACA / AKS / custom) |
| `model:gpt-4o` | Raw Foundry model deployment |

### Dataset Format

Datasets are plain JSONL files. Each row is a JSON object; field names are
free-form and AgentOps adapts evaluators based on which columns are present:

| Column | Used by |
|---|---|
| `input` (required) | Prompt sent to the agent |
| `expected` | Ground-truth response or acceptance criteria (similarity/completeness; F1 only for exact-reference checks) |
| `context` | Retrieval context (RAG evaluators) |
| `tool_definitions`, `tool_calls` | Agent-workflow evaluators |

### Environment Variables and `.agentops/.env`

`agentops init` writes the canonical Azure variables to `.agentops/.env` by
default. Existing azd workspaces, or runs that pass `--azd-env`, use
`.azure/<active-env>/.env` instead. These names match what the Azure SDKs and
`azd` read literally — no renaming.

| Variable | Purpose |
|---|---|
| `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` | Foundry project endpoint URL |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | App Insights for production telemetry |
| `AZURE_OPENAI_ENDPOINT` | Auto-derived from project endpoint when absent |
| `AZURE_OPENAI_DEPLOYMENT` | Default deployment for evaluators |
| `AZURE_OPENAI_API_VERSION` | OpenAI API version override |

AgentOps-specific knobs use the `AGENTOPS_` prefix to avoid clashing with
Azure SDK / azd conventions (`AGENTOPS_FOUNDRY_MODE`,
`AGENTOPS_NO_UNICODE_BANNER`, `AGENTOPS_NO_COLOR`, …).

At import time, `utils/dotenv_loader.py` loads the active
`.azure/<active-env>/.env` first when present, then `.agentops/.env`, so every
CLI command sees the same environment without overriding azd-managed values.

## Execution Model

### Main Flow

`agentops eval run` follows this sequence:

1. Load `agentops.yaml`
2. Resolve the agent target (Foundry prompt / Foundry hosted / HTTP / model)
3. Infer evaluators from agent type + dataset columns; apply user thresholds
4. Dispatch to the runner:
   - `execution: cloud` → `pipeline/cloud_runner.py` (OpenAI Evals API)
   - `execution: local` (default) → `pipeline/runtime.py`
5. Stream per-row results into `.agentops/results/<timestamp>/`
6. Evaluate thresholds and compute aggregate run metrics
7. Write `results.json`, `report.md`, optionally `cloud_evaluation.json`
8. Optionally publish to Foundry Evaluations (`publish: true` or cloud mode)
9. Sync `.agentops/results/latest/`
10. Return exit code `0`, `1`, or `2`

### Execution Targets

| Target | When | How it executes |
|---|---|---|
| Foundry prompt agent (`name:version`) | Default for Foundry-hosted agents | `cloud` runs in OpenAI Evals API; `local` invokes the agent via Agent Service REST and runs `azure.ai.evaluation` evaluators locally |
| Foundry hosted endpoint (`https://...`) | Container-hosted agents in Foundry | Always `local`; calls the agent HTTP endpoint and runs evaluators locally |
| Generic HTTP agent (`https://...`) | LangGraph, LangChain, ACA, AKS, custom REST | Always `local`; POSTs each row, extracts `response`, runs local evaluators |
| Raw model deployment (`model:<name>`) | Direct evaluation of a model deployment | Always `local`; calls the Azure OpenAI deployment, runs evaluators locally |

### Output Contract

Every run produces:
- `results.json` — versioned, machine-readable result
- `report.md` — human-readable summary suitable for PR review

Cloud Foundry runs also produce:
- `cloud_evaluation.json` — includes `eval_id`, `run_id`, and `report_url`
  (deep-link to the New Foundry Experience Evaluations page)

`results.json` contains:
- `run_metrics` — aggregate metrics (`run_pass`, `items_total`,
  `threshold_pass_rate`, per-metric averages and std-devs)
- `row_metrics` / `item_evaluations` — per-row scores
- `thresholds` — pass/fail per threshold
- `summary` — run-level pass/fail summary

`report.md` includes the same data in PR-friendly Markdown plus links to
Foundry when applicable.

## Evaluation Scenarios

Evaluator selection is **automatic** based on agent type and dataset columns.
The matrix below documents what AgentOps will pick by default; thresholds
can always be overridden in `agentops.yaml`.

| Scenario | Agent value | Required dataset columns | Auto-selected evaluators |
|---|---|---|---|
| Model quality | `model:<deployment>` | `input`, `expected` | Similarity, Coherence, Fluency, F1Score, `avg_latency_seconds` |
| RAG quality | Any agent with `context` rows | `input`, `expected`, `context` | Groundedness, Relevance, Retrieval, ResponseCompleteness, Coherence, Fluency, `avg_latency_seconds` |
| Conversational agent | Any prompt/hosted agent | `input`, `expected` | Coherence, Fluency, Similarity, ResponseCompleteness, `avg_latency_seconds` |
| Agent workflow (tools) | Any agent with tool rows | `input`, `expected`, `tool_definitions`, `tool_calls` | TaskCompletion, ToolCallAccuracy, IntentResolution, TaskAdherence, ToolSelection, ToolInputAccuracy, `avg_latency_seconds` |
| Content safety | Any agent or model | `input`, `expected` | Violence, Sexual, SelfHarm, HateUnfairness, ProtectedMaterial (requires `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`) |

## Azure Runtime Notes

Authentication:
- Local development: `az login` (Default Azure CLI credential)
- CI/CD: federated identity via `azure/login` action, or
  `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_CLIENT_SECRET`
- Azure-hosted environments: managed identity

Important environment variables (canonical names, not prefixed):
- `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`
- `APPLICATIONINSIGHTS_CONNECTION_STRING`
- `AZURE_OPENAI_ENDPOINT` (auto-derived when absent)
- `AZURE_OPENAI_DEPLOYMENT`
- `AZURE_OPENAI_API_VERSION`

AgentOps-specific environment variables (`AGENTOPS_` prefix):
- `AGENTOPS_FOUNDRY_MODE` — `cloud` (default) or `local`
- `AGENTOPS_NO_UNICODE_BANNER` / `AGENTOPS_UNICODE_BANNER` — banner override
- `AGENTOPS_NO_COLOR` — disable ANSI styling
- `AGENTOPS_DEBUG` — verbose logging

Authentication rule (Windows-friendly):
> Every `DefaultAzureCredential` instantiation passes `process_timeout=30`;
> the 10s default times out the `az.cmd` cold start on Windows.

Recommended default behavior:
- Keep Foundry cloud mode as the default for `name:version` agents
- Install AgentOps normally; Foundry runtime dependencies are part of the default package
- Keep Azure SDK imports inside functions (lazy) in `pipeline/` and `agent/`
- Do not hardcode `api_version` in `get_openai_client()` — the SDK picks it

## Architectural Constraints

### Code Organization
- Keep `cli/app.py` thin: each command is a Typer entrypoint that parses
  arguments and delegates to `services/` or `pipeline/`
- Keep `core/` pure: no Azure SDK imports, no network calls, no FS writes
- Put orchestration in `services/` (initializer, wizard, skills, cicd)
- Put execution engines in `pipeline/` (runtime, cloud_runner, orchestrator)
- Put Doctor and Cockpit in `agent/`
- Use `pathlib.Path` consistently; no raw string paths
- Avoid module-level side effects and hidden global state
- Lazy-import Azure SDKs from inside functions

### Public Contracts
- Do not change exit code meaning (`0`, `1`, `2`)
- Do not add new top-level commands without explicit discussion
- Preserve `results.json` and `report.md` as stable outputs
- Preserve the flat `agentops.yaml` 1.0 schema (additive changes only)

### CLI Help & Explain Convention
- `--help` stays terse: one-sentence purpose, syntax, parameters, defaults
- Every public command has an `explain` subcommand or is reachable via the
  universal dispatcher `agentops explain [command path...]`
- `explain` is the long-form, paged manual with `--no-pager`, `--format
  markdown|html`, `--out`, and `--open` options
- Do not add separate `list` / `docs` commands for explanatory content

### Foundry-Specific Rules
- Avoid passing explicit `api_version` into `get_openai_client()`
- Keep Azure imports lazy
- Preserve support for both cloud evaluation and local fallback
- Doctor and Cockpit are **read-only by design** — they never create,
  deploy, mutate, or delete cloud resources

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
python -m pytest tests/unit/test_agentops_config.py -q
```

Testing rules:
- Azure SDK calls must be mocked; tests run without Azure credentials
- Unit tests go in `tests/unit/`
- Integration tests go in `tests/integration/`
- Tests should verify exit code behavior when relevant
- Use `CliRunner.invoke(...).output` (not `.stdout`) to capture combined
  stdout+stderr — newer Click separates the streams by default

## Quick Reference

Read first:
- `README.md`
- `docs/concepts.md`
- `docs/how-it-works.md`
- `CONTRIBUTING.md`

Key source files:
- `src/agentops/cli/app.py` — CLI commands + explain pages
- `src/agentops/core/agentops_config.py` — flat 1.0 schema model
- `src/agentops/services/initializer.py` — `agentops init` scaffold logic
- `src/agentops/services/setup_wizard.py` — interactive wizard
- `src/agentops/utils/azd_env.py` — `.azure/<env>/` filesystem helpers
- `src/agentops/pipeline/runtime.py` — local evaluation runner
- `src/agentops/pipeline/cloud_runner.py` — cloud evaluation runner
- `src/agentops/agent/cockpit.py` — Cockpit FastAPI server
- `src/agentops/agent/analyzer.py` — Doctor analyzer

Most common local flow:

```bash
python -m pip install -e .
agentops init                          # scaffold + wizard
agentops skills install --platform copilot
agentops eval analyze                  # inspect eval setup before first run
agentops eval run                      # run evaluation
agentops report generate               # regenerate report.md
agentops doctor                        # readiness + risk + history
agentops cockpit                       # localhost UI for the workspace
python -m pytest tests/ -x -q          # full test suite
```
