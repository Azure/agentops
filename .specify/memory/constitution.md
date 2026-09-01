<!--
Sync Impact Report
- Version change: 1.1.0 -> 2.0.0
- Modified principles:
  - IV. Keep Release Evidence Trustworthy -> IV. Keep Release Evidence Trustworthy
- Modified sections:
  - Product and Technical Constraints
  - Development Workflow and Quality Gates
- Added sections: none
- Removed constraints: cloud deployment of the Cockpit
- Follow-up TODOs: none
-->
# AgentOps Constitution

## Core Principles

### I. Preserve Public Contracts
The documented CLI surface, flat `agentops.yaml` version 1 schema, normalized
`results.json` and release-evidence schemas, and exit-code meanings are stable
public contracts. Changes MUST be backward compatible unless an explicit product
decision approves a breaking change and provides migration guidance. Exit code
`0` MUST mean successful execution with all configured gates passing, `2` MUST
mean successful execution with one or more gates failing, and `1` MUST mean a
runtime or configuration error. New commands and flags require explicit product
discussion; command help MUST remain concise, with detailed guidance placed in
the corresponding `explain` content.

### II. Enforce Architectural Boundaries
`src/agentops/cli/app.py` MUST parse input, render concise output, and delegate
behavior to `pipeline/` or `services/`. `src/agentops/core/` MUST remain pure:
no Azure SDK imports, network calls, or filesystem writes. Evaluation execution
belongs in `pipeline/`; workspace and orchestration services belong in
`services/`; Doctor and Cockpit behavior belongs in `agent/`. Paths MUST use
`pathlib.Path`. New hidden global state and import-time side effects are
prohibited, except for the established CLI dotenv-loading behavior.

### III. Isolate Azure Runtime Integration
Azure SDK imports MUST occur lazily inside runtime functions. Core models and
tests MUST remain usable without Azure credentials or installed Azure runtime
dependencies. `DefaultAzureCredential` usage MUST preserve the Windows-compatible
process timeout required by this project. Foundry SDK clients MUST rely on their
supported API-version selection unless a documented compatibility requirement
demands otherwise. Errors from Azure integrations MUST be surfaced with explicit,
actionable messages rather than broad catches or success-shaped fallbacks.

### IV. Keep Release Evidence Trustworthy
Foundry owns hosted agent runtime, cloud evaluations, traces, monitoring,
red-teaming, datasets, and operations. AgentOps owns repository-controlled
readiness configuration, gates, normalized artifacts, diagnostics, and release
evidence. Doctor and the running Cockpit experience MUST remain read-only and
MUST NOT mutate or delete monitored cloud resources.

`doctor --evidence-pack` MUST project existing signals without introducing
another exit-code contract. Trace promotion MUST remain review-first;
self-similarity labels MUST NOT be represented as human-verified correctness.

### V. Verify Every Behavior Change
Every behavior or contract change MUST have focused automated coverage at the
appropriate level. Unit tests belong in `tests/unit/`; end-to-end workflow
coverage belongs in `tests/integration/`. Azure SDK interactions MUST be mocked
or avoided so tests run without cloud credentials. Tests affecting CLI gates
MUST assert exit-code behavior. Implementations MUST use the smallest existing
test selection that proves the change and MUST pass the relevant test suite
before review.

## Product and Technical Constraints

- The supported runtime is Python 3.11 or newer.
- Configuration and output contracts MUST use Pydantic v2 models in the
  appropriate pure `core/` module.
- The flat root-level `agentops.yaml` MUST remain the single configuration
  contract; schema evolution MUST be additive unless a breaking change is
  explicitly approved.
- `results.json`, `report.md`, and release-evidence artifacts MUST remain
  reproducible and suitable for CI and human review.
- Foundry remains the system of record for hosted runtime and cloud operations;
  AgentOps MUST not duplicate those responsibilities.
- The Cockpit MUST remain a local, read-only projection over workspace evidence
  and links to authorized Foundry and Azure Monitor data.
- Changes MUST reuse existing helpers and established repository patterns before
  introducing new abstractions.

## Development Workflow and Quality Gates

Specifications and implementation plans MUST identify affected public contracts,
architectural layers, evidence boundaries, and required tests before
implementation. Plans MUST record any justified exception to a core principle;
an unexplained exception blocks implementation.

Pull requests MUST be focused, include documentation for user-visible behavior,
and include a changelog entry when the change is user-visible. Review MUST verify
constitutional compliance, relevant automated tests, schema compatibility, and
the exit-code contract. Changes to coding-agent skills MUST be made in their
canonical template source and synchronized to generated copies using the
repository's existing workflow.

## Governance

This constitution is the authoritative source for non-negotiable AgentOps
engineering and product boundaries. `AGENTS.md`, GitHub Copilot instructions,
`CONTRIBUTING.md`, and `docs/how-it-works.md` provide operational detail; when
they conflict with this constitution, the constitution governs and the
conflicting document MUST be corrected.

Amendments require a documented rationale, maintainer review, an impact analysis
for existing specifications and code, and migration guidance when applicable.
Constitution versions follow semantic versioning: MAJOR for incompatible
principle removals or redefinitions, MINOR for new principles or materially
expanded requirements, and PATCH for non-semantic clarification. Every feature
plan and pull-request review MUST verify compliance. Any necessary violation
MUST be explicit, narrowly scoped, justified in the plan, and approved by a
maintainer before implementation.

**Version**: 2.0.0 | **Ratified**: 2026-08-12 | **Last Amended**: 2026-09-01
