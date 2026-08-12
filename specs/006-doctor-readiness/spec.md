# Feature Specification: Doctor Readiness

**Feature Branch**: `placerda-spec-kit-feature`

**Created**: 2026-08-12

**Status**: Implemented Baseline

**Input**: This specification was reverse-engineered from the current implementation, tests, and public documentation of AgentOps Toolkit. It documents the existing `agentops doctor` readiness analysis behavior as-built, not a new proposal. Sources reviewed include `src/agentops/agent/analyzer.py`, `findings.py`, `report.py`, `history.py`, `checks/`, `sources/`, `docs/doctor-checks.md`, `docs/doctor-explained.md`, and associated tests.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Get a severity-ranked list of readiness findings from multiple local signals (Priority: P1)

An operator runs `agentops doctor` and receives a categorized, severity-ranked list of findings drawn from multiple signal sources (eval result history, Azure resource configuration, Azure Monitor telemetry, Foundry configuration), without Doctor altering project source, project configuration, generated workflows, or cloud resources.

**Why this priority**: The severity-ranked findings list is the core output of Doctor; every other capability (history, recommendations, evidence integration) exists to make this list more useful over time.

**Independent Test**: Can be fully tested by running `agentops doctor` against a workspace with a known mix of quality, performance, reliability, operational-excellence, security, and responsible-AI conditions, and confirming each finding is reported with the correct category and severity (`info`, `warning`, or `critical`), with no source or configuration file modified.

**Acceptance Scenarios**:

1. **Given** a workspace with a recent eval run that regressed against its prior baseline, **When** `agentops doctor` runs, **Then** a finding in the quality or reliability category is reported reflecting the regression.
2. **Given** a workspace whose generated CI/CD workflows are missing a Doctor readiness gate, **When** `agentops doctor` runs, **Then** an operational-excellence finding is reported describing the gap.
3. **Given** a workspace with no readiness issues that the currently implemented checks can detect, **When** `agentops doctor` runs, **Then** the run completes successfully and reports whatever findings the implemented checks actually produce for that state, without fabricating findings that do not correspond to an implemented check.
4. **Given** any of the above runs, **When** completed, **Then** no project source file, project configuration file, generated workflow, or Azure resource is modified; only documented local report, history, and explicitly requested evidence artifacts may be written.

---

### User Story 2 - Gate a pipeline on a configurable severity floor (Priority: P1)

A CI pipeline author wants `agentops doctor` to exit with code `2` when findings at or above a configured severity floor are present, so that a pipeline step can block promotion on readiness regressions while distinguishing a finding-gate failure from a runtime/configuration error.

**Why this priority**: Turning findings into an automatable gate is what allows Doctor to function inside CI/CD, matching the same automation value proposition as the eval exit-code contract.

**Independent Test**: Can be fully tested by running `agentops doctor --severity-fail critical` against a workspace with only warning-level findings (expecting `0`) and then against a workspace with a critical finding (expecting `2`), and by varying `--severity-fail` to confirm the floor is respected.

**Acceptance Scenarios**:

1. **Given** `--severity-fail critical` and a workspace whose worst finding is `warning`, **When** `agentops doctor` runs, **Then** the command succeeds (exit code `0`).
2. **Given** the same flag and a workspace with at least one `critical` finding, **When** `agentops doctor` runs, **Then** the command exits with code `2`.
3. **Given** `--severity-fail warning` and a workspace whose worst finding is `warning`, **When** `agentops doctor` runs, **Then** the command exits with code `2`, since `warning` now meets the configured floor.

---

### User Story 3 - Preserve Doctor analyses for later operator and Cockpit review (Priority: P2)

An operator wants each Doctor run's findings recorded to local history so that recurring, new, or resolved findings can be reviewed later by an operator or through Cockpit.

**Why this priority**: Historical tracking increases the value of each individual run and supports Cockpit review, but it is secondary to the core single-run analysis and gating capabilities.

**Independent Test**: Can be fully tested by running `agentops doctor` multiple times against an evolving writable workspace, confirming each completed analysis appends an entry to local history, and confirming the records are returned by Cockpit's Doctor history endpoint without being rewritten.

**Acceptance Scenarios**:

1. **Given** a workspace with no prior Doctor history, **When** `agentops doctor` runs for the first time, **Then** a new local history entry is created recording that run's findings.
2. **Given** a workspace with existing Doctor history, **When** `agentops doctor` runs again, **Then** a new history entry is appended without discarding prior entries.
3. **Given** accumulated Doctor history, **When** Cockpit's Doctor history endpoint is requested, **Then** it returns the persisted analyses for read-only review; a later Doctor analysis does not consume those prior Doctor records as input signals.

---

### Edge Cases

- What happens when one of Doctor's underlying data sources (for example, Azure Monitor) is unreachable? The affected check(s) MUST report a degraded/unknown outcome rather than causing the entire `agentops doctor` run to fail.
- What happens when the workspace has no `.agentops/agent/history.jsonl` yet? Doctor MUST create it on the first run rather than requiring it to pre-exist.
- What happens when `--severity-fail` is given a value that is not one of the supported severities? The command MUST reject it with a clear validation error rather than silently defaulting to a different floor.
- What happens when `--lookback-days` excludes all eval-history or telemetry records used by a time-bounded source? Doctor MUST still run and report current findings from other sources; persisted Doctor analysis records are not themselves filtered or consumed as input to the new analysis.
- What happens when the local Doctor history file cannot be written? The completed analysis and report remain valid, the append failure is logged for debugging, and the command does not claim that a new history record was persisted.
- How does Doctor behave when it is invoked from within a generated CI/CD workflow versus interactively? The severity-fail exit-code contract MUST behave identically in both contexts, since this is what allows the same command to be reused as a CI gate.
- What happens when two findings from different checks describe the same underlying condition? Each check's finding MUST still be reported independently; Doctor does not silently deduplicate findings across unrelated checks.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST analyze multiple local signal sources -- including eval result history, generated CI/CD workflow presence, Azure resource configuration, Azure Monitor telemetry, and Foundry configuration -- and produce a combined list of findings from a single `agentops doctor` invocation.
- **FR-002**: The system MUST assign every finding a category from a fixed set (quality, performance, reliability, operational excellence, security, responsible AI) and a severity from a fixed set (info, warning, critical).
- **FR-003**: The system MUST NOT modify project source, project configuration, generated workflows, or Azure resources as a result of running `agentops doctor`; it MAY write only its documented local report, best-effort history record, and explicitly requested evidence artifacts.
- **FR-004**: The system MUST support a `--severity-fail <severity>` option that causes the command to exit with code `2` when at least one finding at or above the configured severity is present, and with code `0` when the analysis succeeds below that floor; runtime or configuration errors MUST use code `1`.
- **FR-005**: The system MUST reject an unsupported `--severity-fail` value with a validation error rather than silently substituting a default.
- **FR-006**: After a completed analysis, the system MUST attempt to append its findings to local history (`.agentops/agent/history.jsonl` or equivalent), MUST create the history store automatically on first successful write, and MUST leave prior records unchanged; a history-write failure MUST NOT invalidate the completed analysis.
- **FR-007**: The system MUST support a positive `--lookback-days` option that scopes time-bounded eval-history and telemetry source queries, without treating prior persisted Doctor analyses as inputs to the current run.
- **FR-008**: The system MUST degrade gracefully when an individual data source is unreachable, reporting that specific check as degraded/unknown rather than aborting the entire `agentops doctor` run.
- **FR-009**: The system MUST produce a human-readable report (in addition to the exit code and history entry) summarizing the run's findings, organized by category and severity.
- **FR-010**: The system MUST support an `agentops doctor explain` command that provides long-form documentation of Doctor's checks and behavior, reachable without running an analysis.
- **FR-011**: The system MUST expose its composed analysis result in a form that the Release Evidence feature can consume (see the Release Evidence specification) without Doctor itself performing evidence composition.

### Key Entities

- **Finding**: A single reported condition with a `Category`, a `Severity`, and a human-readable description, produced by exactly one check.
- **Category**: One of `quality`, `performance`, `reliability`, `operational_excellence`, `security`, or `responsible_ai`.
- **Severity**: One of `info`, `warning`, or `critical`, ordered for comparison against `--severity-fail`.
- **AnalysisResult**: The aggregate output of a single `agentops doctor` run, containing the full list of findings across all checks and sources.
- **History Entry**: A persisted record of a single past `agentops doctor` run's findings, appended to local history and available to Cockpit and operator review without becoming an input to later Doctor analyses.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every finding reported by any check always carries exactly one category from the fixed set and exactly one severity from the fixed set - no finding is ever reported without both.
- **SC-002**: For every severity floor value, `--severity-fail` produces exit code `0` when the worst reported finding is below the floor and exit code `2` when it is at or above the floor; runtime or configuration errors use exit code `1`.
- **SC-003**: Running `agentops doctor` never modifies project source, project configuration, generated workflows, or Azure resources; its writes are limited to documented report/history artifacts and explicitly requested evidence artifacts.
- **SC-004**: In a writable workspace, a first completed analysis creates local history when none exists and each subsequent completed analysis appends one record without discarding prior entries.
- **SC-005**: An unreachable or unavailable individual data source always degrades only that check's outcome (reported as unknown/degraded), never causing the overall `agentops doctor` invocation to abort.
- **SC-006**: `agentops doctor explain` is reachable and returns Doctor documentation without performing any analysis or touching any file.

## Assumptions

- Doctor's checks operate over signals already available locally or via read-only Azure/Foundry queries that the operator's existing credentials can already access; Doctor does not provision new credentials or resources.
- Doctor history is a local read-only review surface for operators and Cockpit; the current implementation does not feed prior Doctor analysis records into subsequent Doctor analyses or release-evidence composition.
- The fixed category and severity sets are considered stable for this baseline; adding new categories or severities is a larger change outside this specification's scope.
- Doctor is expected to be run both interactively by an operator and non-interactively inside a generated CI/CD workflow, and both invocation styles are covered by the same command and flag surface.

## Out of Scope

- Remediating, auto-fixing, or opening pull requests for any finding Doctor reports.
- Real-time or continuous monitoring; Doctor performs a single point-in-time analysis per invocation.
- Composing the cross-feature release-evidence artifact itself, which is specified separately (see Release Evidence) even though it consumes Doctor's `AnalysisResult`.
- Defining new finding categories or severities beyond the fixed sets already implemented.
- Claiming that an empty or minimal workspace always yields zero findings; the actual outcome depends on which checks are implemented and what conditions they detect, and is not asserted as a blanket guarantee in this baseline.

## Implementation Evidence

- `src/agentops/agent/analyzer.py` - orchestration of multiple checks into a combined `AnalysisResult`.
- `src/agentops/agent/findings.py` - `Finding`, `Category` (`quality`, `performance`, `reliability`, `operational_excellence`, `security`, `responsible_ai`), and `Severity` (`info`, `warning`, `critical`) definitions, including severity ordering used for `--severity-fail` comparisons.
- `src/agentops/agent/report.py` - human-readable Doctor report rendering grouped by category and severity.
- `src/agentops/agent/history.py` - `.agentops/agent/history.jsonl` read/write logic, including first-run creation and lookback-window filtering.
- `src/agentops/agent/checks/` - individual check implementations (for example, Foundry configuration, regression, operational-excellence, and posture-rule checks) each producing `Finding` entries.
- `src/agentops/agent/sources/` - data source adapters (results history, Azure resources, Azure Monitor, Foundry control plane) each degrading independently when unavailable.
- `src/agentops/cli/app.py` - `doctor` and `doctor explain` command wiring, including `--severity-fail` and `--lookback-days`.
- `tests/unit/test_agent_analyzer.py`, `test_agent_history.py`, `test_doctor_catalog.py`, `test_doctor_cli_explain.py`, `test_agent_checks_foundry_config.py`, `test_agent_checks_observability.py` - unit coverage of analysis composition, history persistence, and individual checks.
- `docs/doctor-checks.md` and `docs/doctor-explained.md` - narrative documentation of Doctor's checks, categories, severities, and operations model consistent with this baseline.
