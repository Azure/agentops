# AgentOps Toolkit Runner CLI — Specification

## Project purpose

Build a **standalone Python CLI** that helps developers run **standardized evaluation workflows** using reusable **Evaluation Bundles** (for example, `rag_baseline`, `tool_agent_baseline`) and generate consistent output artifacts for local development and CI/CD.

The CLI is an orchestration and standardization layer. It is **not** a reimplementation of Foundry Evaluations. Its job is to:

* resolve configuration (bundles, datasets, thresholds)
* validate inputs
* trigger an evaluation run (initially via a pluggable backend or subprocess)
* collect normalized results
* generate a readable report (Markdown)
* return CI-friendly exit codes

## MVP goals

The MVP is successful if a developer can:

1. Install the CLI with one command (`pipx install ...`)
2. Initialize a repo-local evaluation workspace (`agentops init`)
3. Run an evaluation bundle against a dataset config (`agentops eval run ...`)
4. Get:

   * `results.json` (machine-readable)
   * `report.md` (human-readable)
5. Use the CLI in CI/CD with predictable exit codes:

   * `0` success and thresholds passed
   * `2` execution succeeded but thresholds failed
   * `1` runtime/configuration error

---

## Core product principles

1. **Low friction**

   * Works as a normal developer CLI
   * No manual venv management required for end users
   * Clear commands and helpful errors

2. **Config-driven**

   * YAML-first workflow
   * Bundles and runs are explicit and reproducible
   * Easy to version in Git

3. **CI-friendly**

   * Non-interactive by default
   * Stable exit codes
   * JSON outputs for automation

4. **Extensible by design**

   * Pluggable execution backend (subprocess first, direct SDK/API later)
   * Bundle schema supports future evaluators and metrics
   * Report generation can evolve without changing run inputs

5. **Safe and predictable**

   * No side effects on import
   * No hidden environment mutations
   * No implicit installs at runtime in MVP

---

## Target audience

* AI engineers
* ML engineers
* Platform teams
* Dev teams running evaluation checks in local dev and CI pipelines

Assume users are comfortable with:

* command line usage
* YAML files
* Python tooling (`pipx`, `pip`)

Do not assume users want to write Python code to use the CLI.

---

## MVP scope

### In scope

* Standalone Python CLI using **Typer**
* Config validation using **Pydantic v2**
* YAML config parsing (`ruamel.yaml` preferred, `PyYAML` acceptable)
* Workspace initialization command
* Evaluation run command
* Report generation command
* JSON + Markdown outputs
* CI-friendly exit codes
* Structured logging (human-readable console + optional JSON mode later)
* Minimal tests for core flows

### Out of scope for MVP

* Direct integration with Foundry SDK inside the CLI core (can be added later)
* Rich web UI / dashboards
* Remote bundle registry sync
* Dataset ingestion pipelines
* Authentication flows
* Azure resource provisioning
* Deep result analytics / trend reports across many runs
* Interactive wizards (optional later)

---

## Packaging and installation requirements

### Python version

Use **Python 3.11+** for MVP (aligns with modern tooling and your preference).

If needed later, broaden to 3.10+.

### Packaging

* Use `pyproject.toml` as the single source of truth
* Publish as a standard Python package
* Provide a console script entry point

### Installation

Primary path:

* `pipx install <package-name>`

Also support:

* `pip install <package-name>` (inside a venv)
* optional `uv tool install <package-name>` later

### Naming guidance

There may already be packages named `agentops` in PyPI. To avoid collision:

* **Package name** (PyPI): use something unique, for example:

  * `foundry-agentops-toolkit`
  * `foundry-agentops-cli`
  * `agentops-toolkit-cli`
* **CLI command**: ideally `agentops` (if no conflict on user PATH), otherwise:

  * `foundry-agentops`
  * `faops`

MVP spec assumes the CLI command is `agentops`.

---

## Command-line interface design (MVP)

## Top-level command

```bash
agentops --help
agentops --version
```

### Global behavior

* Clear help text
* No side effects on import
* Human-readable errors by default
* Exit codes consistent and documented

---

## Command 1: `agentops init`

Initialize a local evaluation workspace in the current project.

### Example

```bash
agentops init
```

### Expected behavior

Creates a local `.agentops/` folder with starter files:

* `.agentops/config.yaml`
* `.agentops/bundles/rag_baseline.yaml`
* `.agentops/datasets/smoke.yaml`
* `.agentops/results/` (empty folder or created lazily)
* `.agentops/.resolved/` (optional, created lazily)
* `.agentops/.gitignore` (optional)

### Flags (MVP)

* `--force`: overwrite starter files if they already exist
* `--dir PATH`: initialize workspace in a custom directory (default: current working directory)

### Notes

`init` should be idempotent by default:

* if files exist, do not overwrite unless `--force` is used

---

## Command 2: `agentops eval run`

Run an evaluation using a config file and produce normalized outputs.

### Example

```bash
agentops eval run --config .agentops/run.yaml --output .agentops/results/latest
```

### Alternate examples

```bash
agentops eval run --config .agentops/run.yaml
agentops eval run --config .agentops/run.yaml --output .agentops/results/2026-02-23_091500
```

### Expected behavior

1. Load and validate `run.yaml`
2. Resolve bundle and dataset references
3. Execute the configured backend (subprocess backend in MVP)
4. Collect backend outputs (or simulated outputs in tests)
5. Normalize results into a stable `results.json`
6. Generate `summary.md`
7. Evaluate thresholds
8. Exit with:

   * `0` if run succeeded and thresholds passed
   * `2` if run succeeded but thresholds failed
   * `1` on runtime/config errors

### Required flags (MVP)

* `--config PATH`: path to run config YAML
* `--output PATH` (optional): output directory; if omitted, create a timestamped directory under `.agentops/results/`

### Optional flags (MVP)

* `--verbose`: more detailed logs
* `--dry-run`: validate and resolve config only, do not execute backend
* `--no-report`: skip Markdown summary generation (still write JSON)
* `--backend-timeout SECONDS`: optional execution timeout override

---

## Command 3: `agentops report`

Generate a Markdown report from a prior `results.json`.

### Example

```bash
agentops report --in .agentops/results/latest/results.json --out .agentops/results/latest/report.md
```

### Alternate example

```bash
agentops report --in .agentops/results/latest/results.json
```

If `--out` is omitted, default to `report.md` next to `results.json`.

### Expected behavior

* Load and validate `results.json`
* Generate a readable Markdown report with:

  * run metadata
  * bundle used
  * dataset used
  * metric values
  * thresholds
  * pass/fail summary
  * backend command summary (if available)
* Exit `0` on success, `1` on error

---

## Configuration model

The CLI should use a **layered configuration model**:

1. `config.yaml` — workspace defaults
2. `bundle` file — evaluator definitions and thresholds
3. `dataset` file — dataset reference and metadata
4. `run.yaml` — concrete run specification (references bundle + dataset, backend config)
5. CLI flags — final overrides

For MVP, keep this simple:

* `run.yaml` references a bundle file and a dataset file
* `config.yaml` stores default paths and settings

---

## Workspace file structure (MVP)

```text
<repo-root>/
  .agentops/
    config.yaml
    bundles/
      rag_baseline.yaml
    datasets/
      smoke.yaml
    runs/                    # optional place for named run specs
    .resolved/               # generated resolved configs (optional)
    results/
      2026-02-23_091500/
        results.json
        summary.md
        report.md
```

You may choose `summary.md` and `report.md` to be the same file in MVP. If so, standardize on one name (`report.md` recommended).

---

## YAML schemas (MVP)

These are not strict JSON Schema files yet. They are **Pydantic-backed YAML contracts**.

## `.agentops/config.yaml` (workspace defaults)

```yaml
version: 1

paths:
  bundles_dir: ".agentops/bundles"
  datasets_dir: ".agentops/datasets"
  results_dir: ".agentops/results"

defaults:
  backend: "subprocess"
  timeout_seconds: 1800

report:
  generate_markdown: true
```

Purpose:

* centralize default directories and behavior
* keep `run.yaml` small

---

## Bundle file schema (example: `.agentops/bundles/rag_baseline.yaml`)

```yaml
version: 1
name: "rag_baseline"
description: "Baseline evaluation bundle for retrieval-augmented generation agents."

evaluators:
  - name: "groundedness"
    enabled: true
  - name: "relevance"
    enabled: true
  - name: "coherence"
    enabled: true
  - name: "fluency"
    enabled: true

thresholds:
  - metric: "groundedness"
    operator: ">="
    value: 0.80
  - metric: "relevance"
    operator: ">="
    value: 0.80
  - metric: "coherence"
    operator: ">="
    value: 0.75
  - metric: "fluency"
    operator: ">="
    value: 0.75

metadata:
  category: "rag"
  tags: ["baseline", "mvp"]
```

### Notes

* Evaluator names are strings in MVP (no hardcoded enum requirement initially)
* Threshold operators should support:

  * `>=`, `>`, `<=`, `<`, `==`
* Threshold values are numeric in MVP

---

## Dataset file schema (example: `.agentops/datasets/smoke.yaml`)

```yaml
version: 1
name: "smoke"
description: "Small smoke dataset for local validation."

source:
  type: "file"
  path: "./eval/datasets/smoke.jsonl"

format:
  type: "jsonl"
  input_field: "input"
  expected_field: "expected"

metadata:
  size_hint: 20
  owner: "local"
```

### Notes

MVP does not need to parse the dataset file content deeply. The dataset file can be a **reference** only.

---

## Run config schema (example: `.agentops/run.yaml`)

```yaml
version: 1

bundle:
  path: ".agentops/bundles/rag_baseline.yaml"

dataset:
  path: ".agentops/datasets/smoke.yaml"

backend:
  type: "subprocess"
  command: "python"
  args:
    - "-m"
    - "my_eval_runner"
    - "--bundle"
    - "{bundle_path}"
    - "--dataset"
    - "{dataset_path}"
    - "--output"
    - "{backend_output_dir}"
  env:
    FOUNDRY_PROJECT: "my-project"
  timeout_seconds: 1800

output:
  write_report: true
```

### Placeholder substitution (MVP)

The backend subprocess runner should support placeholders in args:

* `{bundle_path}`
* `{dataset_path}`
* `{backend_output_dir}`
* `{results_json_path}` (optional future)
* `{cwd}` (optional future)

This makes the run config reusable and backend-agnostic.

---

## Pydantic domain models (required)

Implement strongly typed models for these concepts.

### Core models

* `WorkspaceConfig`
* `BundleConfig`
* `EvaluatorConfig`
* `ThresholdRule`
* `DatasetConfig`
* `RunConfig`
* `BackendConfig`
* `RunResult`
* `MetricResult`
* `ThresholdEvaluationResult`

### Validation rules

At minimum:

* `version` fields required and integer
* bundle names non-empty
* evaluator names non-empty
* threshold operators in allowed set
* dataset source path non-empty
* backend command non-empty for subprocess backend
* run config file paths exist (unless `--dry-run` and explicitly allowed to skip backend)

Use Pydantic validators for:

* operator validation
* normalized path handling
* numeric threshold values

---

## Execution backend design (important)

The CLI should not hardcode Foundry SDK behavior. Instead, use a **backend abstraction**.

## Backend interface (conceptual)

Define an internal interface/protocol for execution backends:

* `execute(run_context) -> BackendExecutionResult`

### MVP backend implementation

Implement one backend:

* `SubprocessBackend`

Responsibilities:

* build subprocess command from `backend.command + backend.args`
* substitute placeholders
* pass env vars
* capture stdout/stderr
* apply timeout
* collect backend output files (if produced)
* return normalized execution info

### Why this matters

This keeps the CLI flexible:

* today: shell out to Python runner
* later: direct Foundry SDK
* later: direct REST API
* later: Docker/container backend

---

## Normalized `results.json` schema (MVP)

The backend may produce arbitrary outputs. The CLI must write a **normalized result artifact** for reporting and CI.

Example structure:

```json
{
  "version": 1,
  "status": "completed",
  "bundle": {
    "name": "rag_baseline",
    "path": ".agentops/bundles/rag_baseline.yaml"
  },
  "dataset": {
    "name": "smoke",
    "path": ".agentops/datasets/smoke.yaml"
  },
  "execution": {
    "backend": "subprocess",
    "command": "python -m my_eval_runner --bundle ...",
    "started_at": "2026-02-23T09:15:00Z",
    "finished_at": "2026-02-23T09:16:12Z",
    "duration_seconds": 72.3,
    "exit_code": 0
  },
  "metrics": [
    { "name": "groundedness", "value": 0.84 },
    { "name": "relevance", "value": 0.81 },
    { "name": "coherence", "value": 0.78 },
    { "name": "fluency", "value": 0.80 }
  ],
  "thresholds": [
    {
      "metric": "groundedness",
      "operator": ">=",
      "target": 0.8,
      "actual": 0.84,
      "passed": true
    },
    {
      "metric": "relevance",
      "operator": ">=",
      "target": 0.8,
      "actual": 0.81,
      "passed": true
    }
  ],
  "summary": {
    "metrics_count": 4,
    "thresholds_count": 4,
    "thresholds_passed": 4,
    "thresholds_failed": 0,
    "overall_passed": true
  },
  "artifacts": {
    "backend_stdout": "backend.stdout.log",
    "backend_stderr": "backend.stderr.log"
  }
}
```

### Notes

* Keep this schema stable early
* Add fields later instead of changing/removing existing fields
* This file is the source of truth for `agentops report`

---

## Markdown report format (MVP)

`report.md` should be concise and PR-friendly.

Include:

* Title (`AgentOps Evaluation Report`)
* Run timestamp
* Bundle and dataset names
* Overall status (PASS / FAIL)
* Execution duration
* Metrics section
* Threshold checks section
* Artifact links (relative file names)
* Optional backend command summary

Do not over-design the report in MVP. Readability matters more than styling.

---

## Exit code contract (must be stable)

This is critical for CI usage.

* `0` → execution succeeded and thresholds passed
* `2` → execution succeeded but one or more thresholds failed
* `1` → runtime/configuration error (invalid YAML, missing file, subprocess failure, timeout, unexpected exception)

This contract must be documented and tested.

---

## Logging and error handling

## Logging requirements (MVP)

* Use Python `logging`
* Default level: INFO
* `--verbose` enables DEBUG
* Console logs should be human-readable
* Backend stdout/stderr should be saved to files in the output directory

## Error handling requirements

* Catch expected errors and show clear messages:

  * invalid YAML
  * validation errors
  * missing files
  * subprocess command not found
  * timeout
  * invalid backend output
* Avoid stack traces by default
* Optionally show stack trace only in `--verbose` mode

---

## Implementation architecture (Python package layout)

Recommended package structure:

```text
src/
  agentops/
    __init__.py
    __main__.py

    cli/
      app.py                 # Typer app entrypoint
      commands_init.py
      commands_eval.py
      commands_report.py

    core/
      models.py              # Pydantic models
      config_loader.py       # YAML load + validation
      paths.py               # workspace path helpers
      thresholds.py          # threshold evaluation logic
      results.py             # normalized results creation
      reporter.py            # Markdown report generator

    backends/
      base.py                # backend protocol/interface
      subprocess_backend.py  # subprocess implementation

    services/
      initializer.py         # agentops init logic
      runner.py              # orchestration for eval run
      reporting.py           # report orchestration

    utils/
      io.py                  # file IO helpers
      yaml.py                # YAML read/write wrappers
      time.py                # timestamp helpers
      errors.py              # custom exceptions
      logging.py             # logging setup

tests/
  unit/
  integration/
```

### Design rules

* Keep CLI command functions thin
* Put business logic in `services/` and `core/`
* Keep models centralized
* No circular imports
* No side effects during module import

---

## Typer app design

Recommended command structure:

* `agentops init`
* `agentops eval run`
* `agentops report`

Use Typer sub-apps for `eval`, and optionally `report` can stay top-level in MVP.

### Example structure

* Main app

  * `init`
  * `report`
  * `eval` (sub-app)

    * `run`

This leaves room for future commands:

* `agentops eval validate`
* `agentops bundle list`
* `agentops bundle show`

---

## `pyproject.toml` requirements

Use modern packaging with setuptools or hatchling (either is fine). Keep it simple.

Must include:

* project metadata
* dependencies
* console script entry point
* Python version requirement (`>=3.11`)
* optional dev dependencies group

### Suggested runtime dependencies

* `typer`
* `pydantic` (v2)
* `ruamel.yaml` (or `PyYAML`)
* `rich` (optional, nice console output; not required for MVP)

### Suggested dev dependencies

* `pytest`
* `pytest-cov`
* `mypy` (optional but recommended)
* `ruff`
* `types-PyYAML` (if using PyYAML)

---

## Testing strategy (MVP)

## Unit tests (required)

Test core logic without subprocesses:

* YAML loading and validation
* threshold evaluation logic
* results normalization
* report generation
* path resolution and output folder creation

## Integration tests (required)

Use a fake backend command (or a small Python stub script) to test:

* `agentops eval run` end-to-end
* output files are created
* exit code `0` on pass
* exit code `2` on threshold failure
* exit code `1` on subprocess failure

## CLI tests (nice to have)

Use Typer test runner / `CliRunner` style tests for:

* `--help`
* invalid args
* missing config error messaging

---

## Development workflow (for VS Code + Copilot)

## Recommended first implementation order

### Phase 1 — Skeleton

* Create package structure
* Add Typer app with placeholder commands
* Add `--version` support
* Add `pyproject.toml`
* Add basic logging

### Phase 2 — Models and config loading

* Implement Pydantic models
* YAML loader
* Validation errors
* Test model parsing

### Phase 3 — `agentops init`

* Create `.agentops/` folder
* Write starter YAML files
* Add `--force`
* Test idempotency behavior

### Phase 4 — `agentops eval run`

* Parse `run.yaml`
* Load bundle and dataset files
* Implement subprocess backend
* Write normalized `results.json`
* Evaluate thresholds
* Generate `report.md`
* Return correct exit codes

### Phase 5 — `agentops report`

* Read `results.json`
* Generate markdown
* Test output

### Phase 6 — Polish

* Improve help text
* Improve errors
* Add examples to README
* Add CI workflow for lint + test

---

## Acceptance criteria for MVP

The CLI is MVP-ready when all of the following are true:

1. `pipx install <package>` installs successfully and exposes the CLI
2. `agentops --help` and `agentops --version` work
3. `agentops init` creates a usable `.agentops` workspace
4. `agentops eval run --config .agentops/run.yaml`:

   * validates YAML
   * executes backend
   * writes `results.json`
   * writes `report.md`
   * returns a deterministic exit code
5. `agentops report --in .../results.json` generates Markdown successfully
6. Core unit tests and one end-to-end integration test pass
7. README includes:

   * install
   * upgrade
   * uninstall
   * quickstart commands

---

## Future roadmap (not MVP, but design for it)

These are not required now, but the architecture should not block them:

* Bundle registry commands (`bundle list`, `bundle show`)
* Multiple backends:

  * direct Foundry SDK backend
  * REST backend
  * Docker backend
* Result comparison:

  * `agentops compare run1 run2`
* Trend reporting across runs
* JSON output mode for command summaries (`--json`)
* Optional `azd` wrapper extension that calls this CLI
* Azure project context integration (`azure.yaml`, azd env) in a separate adapter

---

## Suggested starter files generated by `agentops init`

Keep the templates minimal but valid.

### `.agentops/config.yaml`

* workspace defaults

### `.agentops/bundles/rag_baseline.yaml`

* baseline evaluators + thresholds

### `.agentops/datasets/smoke.yaml`

* sample dataset reference

### `.agentops/run.yaml`

A ready-to-edit run file that references the bundle and dataset and uses subprocess backend.

The backend command in the template can point to a placeholder runner command that the user edits later.

Example placeholder:

* `python -m my_eval_runner ...`

This is okay for MVP, because the focus is the orchestration structure.

---

## Documentation requirements (MVP)

README must include:

1. **Quick Install (pipx first)**

   * how to install pipx
   * install command
   * verify command

2. **Quick Start**

   * `agentops init`
   * edit `.agentops/run.yaml`
   * `agentops eval run ...`
   * `agentops report ...`

3. **Exit codes**

   * `0`, `1`, `2`

4. **Config reference**

   * links to example YAML files

5. **Troubleshooting**

   * command not found
   * invalid YAML
   * backend subprocess not found
   * timeout

---

## Quality standards for code generation (important for Copilot)

Copilot should generate code that follows these rules:

* Python 3.11+
* Type hints on all public functions
* Use `pathlib.Path`, not raw string paths
* Keep command handlers thin
* Business logic in service/core modules
* Pydantic v2 models for config and results
* No hidden global state
* No side effects on import
* Exceptions should be explicit and user-friendly
* Prefer small focused functions over long command methods
* Use standard library when possible

---

# `.github/copilot-instructions.md` (short version)

Paste this into `.github/copilot-instructions.md` to guide Copilot while coding.

```md
This repository implements a standalone Python CLI called `agentops` for standardized evaluation workflows.

Primary goals:
- Python 3.11+
- Typer-based CLI
- Pydantic v2 models for YAML config validation
- YAML-first workflow (`.agentops/` workspace)
- Commands:
  - `agentops init`
  - `agentops eval run --config <run.yaml> [--output <dir>]`
  - `agentops report --in <results.json> [--out <report.md>]`
- Outputs:
  - normalized `results.json`
  - `report.md`
- Exit codes:
  - `0` success and thresholds passed
  - `2` thresholds failed
  - `1` runtime/config error

Design rules:
- Keep CLI command functions thin
- Put business logic in `services/` and `core/`
- Use `pathlib.Path`
- No side effects on import
- Clear, user-friendly error messages
- Avoid hardcoding Foundry SDK logic in CLI core
- Use a backend abstraction; MVP backend is subprocess-based

Testing expectations:
- Unit tests for config parsing, thresholds, results normalization, report generation
- Integration test for `eval run` end-to-end with a fake subprocess backend

Do not over-engineer MVP. Prioritize a clean command flow, stable config schema, and deterministic outputs.
```
