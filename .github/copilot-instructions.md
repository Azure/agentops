# Copilot Instructions for AgentOps

## Project Overview

AgentOps is a **standalone Python CLI** that helps developers run **standardized evaluation workflows** using reusable **evaluation bundles**.

The CLI:
- Is installed via `pipx`
- Uses YAML configuration
- Produces normalized outputs:
  - `results.json` (machine-readable)
  - `report.md` (human-readable, PR-friendly)
- Returns **CI-friendly exit codes** to gate pipelines on quality thresholds

This repository currently targets **MVP scope only**.

The authoritative design and MVP contract is defined in:
👉 `docs/SPEC.md`

All code, commands, schemas, and behavior **must conform to that document**.

---

## Technology Choices (MVP)

- **Language**: Python 3.11+
- **CLI framework**: Typer
- **Config & schema validation**: Pydantic v2
- **Configuration format**: YAML
- **Execution model**: backend abstraction
  - MVP backend is **subprocess-based**
- **Installation**: `pipx install <package>`

Do **not** introduce additional frameworks or SDK integrations unless explicitly defined in `docs/SPEC.md`.

---

## CLI Command Surface (MVP – fixed contract)

The CLI command name is `agentops`.

Only the following commands are in scope for MVP:

- `agentops init`
- `agentops eval run --config <run.yaml> [--output <dir>]`
- `agentops report --in <results.json> [--out <report.md>]`

Do not add new commands or flags unless the spec is updated.

---

## Exit Code Contract (critical)

Exit codes are part of the public API and **must be respected everywhere**:

- `0` → execution succeeded **and** all thresholds passed
- `2` → execution succeeded **but** one or more thresholds failed
- `1` → runtime or configuration error

Do not overload or reinterpret these codes.

---

## Architecture Rules

- Use **Python src layout**
- Keep CLI command handlers **thin**
- Place business logic in:
  - `core/`
  - `services/`
  - `backends/`
- Use `pathlib.Path` everywhere (no raw string paths)
- No side effects at import time
- No hidden global state
- Prefer small, focused functions
- Explicit, user-friendly error messages

---

## Configuration Model (MVP)

Configuration is **YAML-first** and layered:

- `.agentops/config.yaml` → workspace defaults
- bundle YAML → evaluators + thresholds
- dataset YAML → dataset reference and metadata
- run YAML → concrete run specification
- CLI flags override YAML

Schemas are validated using **Pydantic v2 models**.

Both config files and results files must include a `version` field.

---

## Outputs (MVP)

Every evaluation run must produce:

- `results.json`
  - normalized, versioned schema
  - stable and machine-readable
- `report.md`
  - human-readable summary
  - suitable for PR reviews

`agentops report` must be able to regenerate `report.md` from `results.json`.

---

## Execution Backend (MVP)

- Use a backend abstraction
- MVP backend is **subprocess-based**
- The CLI orchestrates execution; it does **not** embed SDK logic
- Backend commands are defined in `run.yaml`
- Support placeholder substitution in backend args (as defined in `docs/SPEC.md`)

---

## Testing Expectations

- Unit tests for:
  - config parsing and validation
  - threshold evaluation
  - results normalization
  - report generation
- Integration test for:
  - `agentops eval run` end-to-end using a fake subprocess backend
- Tests must assert correct **exit codes**

---

## Out of Scope (MVP)

Do not implement the following unless the spec changes:

- Direct integration with Foundry SDK
- Azure Monitor / KQL integration
- Remote bundle registries
- Dataset ingestion pipelines
- Interactive prompts
- azd integration
- Web UI or dashboards

---

## Copilot Guidance

When generating or modifying code:

- Follow `docs/SPEC.md` as a **hard contract**
- Do not invent new concepts or commands
- Prefer clarity and determinism over cleverness
- Optimize for maintainability and CI usage