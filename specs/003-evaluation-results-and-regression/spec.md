# Feature Specification: Evaluation Results and Regression

**Feature Branch**: `placerda-spec-kit-feature`

**Created**: 2026-08-12

**Status**: Implemented Baseline

**Input**: This specification was reverse-engineered from the current implementation, tests, and public documentation of AgentOps Toolkit. It documents the existing results, reporting, and baseline-comparison behavior as-built, not a new proposal. Sources reviewed include `src/agentops/core/results.py`, `src/agentops/pipeline/thresholds.py`, `comparison.py`, `reporter.py`, `orchestrator.py`, `src/agentops/cli/app.py` (`eval run`, `report generate`), and associated tests.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Get a normalized, machine-readable result and a human-readable report from every run (Priority: P1)

A release engineer runs an evaluation and needs both a machine-readable artifact for automation (CI gating, dashboards) and a human-readable summary suitable for a pull request review, produced consistently regardless of which agent kind or execution mode was used.

**Why this priority**: Every other capability in this feature (thresholds, exit codes, comparison, reporting) depends on there being a single, stable, normalized result shape to operate on.

**Independent Test**: Can be fully tested by running any supported evaluation configuration and confirming a `results.json` and `report.md` are written to the run's output directory, with `results.json` containing run-level summary metrics, per-row metrics, and threshold outcomes, and `report.md` rendering the same information in Markdown.

**Acceptance Scenarios**:

1. **Given** a completed evaluation run, **When** the run finishes, **Then** a `results.json` file is written containing run metrics, row-level metrics, and per-threshold pass/fail outcomes.
2. **Given** the same completed run, **When** the run finishes, **Then** a `report.md` file is written that summarizes the same run in human-readable Markdown, including a per-threshold summary table.
3. **Given** an existing `results.json` from a prior run, **When** `agentops report generate --in <path>` is executed, **Then** a `report.md` is regenerated from that file without re-running the evaluation.
4. **Given** a cloud-executed run, **When** the run finishes, **Then** an additional `cloud_evaluation.json` is written containing the cloud evaluation/run identifiers and a deep-link to the corresponding Foundry Evaluations page, alongside the same `results.json`/`report.md` shape used by local runs.

---

### User Story 2 - Get a stable, automatable pass/fail signal from every run (Priority: P1)

A CI pipeline author wants a run's outcome to be expressed as one of exactly three exit codes so that a pipeline step can gate merges or deployments without parsing free-form text.

**Why this priority**: A stable exit-code contract is what makes AgentOps usable as an automated release gate; without it, results are informative but not actionable in CI.

**Independent Test**: Can be fully tested by running configurations engineered to succeed, to fail a threshold, and to hit a runtime/configuration error, and confirming the process exit code is `0`, `2`, and `1` respectively.

**Acceptance Scenarios**:

1. **Given** a run in which every threshold passes, **When** `agentops eval run` completes, **Then** the process exits with code `0`.
2. **Given** a run in which the agent was invoked successfully but at least one threshold fails, **When** `agentops eval run` completes, **Then** the process exits with code `2`.
3. **Given** a run that cannot proceed due to a configuration or runtime error (for example, an invalid `agentops.yaml` or an unreachable required resource before any row is evaluated), **When** `agentops eval run` is executed, **Then** the process exits with code `1` and the error is described in the output.

---

### User Story 3 - Compare a run against a previous baseline (Priority: P2)

A release engineer wants to see whether a new run's metrics have improved, regressed, or stayed the same relative to a previously saved baseline result, as part of the same `eval run` invocation.

**Why this priority**: Regression comparison against a baseline is valuable for catching drift over time, but it builds on top of (and is optional relative to) the core result/threshold/exit-code contract, so it ranks below the P1 stories.

**Independent Test**: Can be fully tested by running `agentops eval run --baseline <path-to-prior-results.json>` and confirming the resulting `results.json`/`report.md` include a comparison section showing, per metric, the current value, the baseline value, and the direction of change (improved, regressed, or unchanged).

**Acceptance Scenarios**:

1. **Given** a prior run's `results.json` supplied via `--baseline`, **When** the new run completes, **Then** the result includes a comparison entry for every aggregate metric present in either run, showing the available current and baseline values without inventing a missing-side value, plus the computed change direction.
2. **Given** a baseline file that does not exist or cannot be parsed as a valid prior result, **When** `agentops eval run --baseline <path>` is executed, **Then** the run fails with a clear error rather than silently skipping the comparison.
3. **Given** a `--baseline` comparison is requested, **When** the run completes, **Then** the presence of a baseline comparison does not change the threshold pass/fail outcome or exit code of the current run; comparison is informational.

---

### Edge Cases

- What happens when a run's dataset is empty (zero rows)? The run MUST be rejected with a clear "dataset is empty" error before any row is invoked and before `results.json` is written for that attempt; a zero-row run is not a supported outcome.
- What happens when a threshold expression references a metric that no evaluator produced for a given row? That row's threshold evaluation for the missing metric MUST be represented explicitly (for example, as not evaluated) rather than silently counted as a pass.
- How does `agentops report generate` behave when pointed at a `results.json` that does not match the expected schema version? Report generation MUST fail with a clear error rather than producing a malformed or misleading `report.md`.
- What happens when `--baseline` is combined with a current run whose metric set differs from the baseline's (for example, a threshold was added or removed between runs)? Metrics present in only one of the two runs MUST be reported without fabricating a value for the side missing that metric.
- What happens when a run's agent invocation succeeds for every row but every row fails every threshold? The exit code MUST still be `2` (a threshold failure), not `1`, since the failure is a quality gate outcome rather than a runtime error.
- How is `agentops eval compare` handled as a standalone subcommand? It does not exist; only the `--baseline` flag on `agentops eval run` provides comparison, and the CLI help surface must not expose a `compare` subcommand.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST produce a normalized `results.json` for every completed run, containing a run-level summary, per-row metrics, and per-threshold pass/fail outcomes, regardless of execution mode or agent kind.
- **FR-002**: The system MUST produce a human-readable `report.md` for every completed run, rendered from the same `results.json` content, including a per-threshold summary.
- **FR-003**: The system MUST support regenerating `report.md` from an existing `results.json` via `agentops report generate --in <path>` without re-running the evaluation.
- **FR-004**: For cloud-executed runs, the system MUST additionally produce a `cloud_evaluation.json` containing cloud evaluation/run identifiers and a deep-link to the corresponding Foundry Evaluations page.
- **FR-005**: The system MUST translate a completed run's outcome into exactly one of three process exit codes: `0` when all thresholds pass, `2` when the run completed but at least one threshold failed, and `1` when a runtime or configuration error prevented the run from completing.
- **FR-006**: The system MUST keep the exit-code contract independent of execution mode; a threshold failure under local, cloud, or azd execution MUST all yield exit code `2`.
- **FR-007**: The system MUST support an optional `--baseline <path>` flag on `agentops eval run` that loads a prior run's `results.json` and produces a comparison across the union of current and baseline aggregate metrics (available current value, available baseline value, and direction of change) alongside the current run's own results.
- **FR-008**: The system MUST fail clearly when a `--baseline` path does not exist or does not parse as a valid prior result, rather than silently proceeding without a comparison.
- **FR-009**: The system MUST NOT let the presence or outcome of a baseline comparison change the current run's threshold pass/fail outcome or resulting exit code; comparison is informational only.
- **FR-010**: The system MUST NOT expose a standalone `agentops eval compare` subcommand; comparison functionality is only reachable through the `--baseline` flag on `agentops eval run`.
- **FR-011**: The system MUST persist run results under the workspace's results history directory and maintain a "latest" pointer to the most recent run for tools that need the current result without knowing its timestamp.
- **FR-012**: The system MUST reject a run whose dataset contains zero rows before any row is invoked, with a clear error, rather than producing a `results.json` that reports zero evaluated rows.

### Key Entities

- **RunResult**: The root normalized result object persisted as `results.json`, containing run summary, target information, row results, threshold evaluations, and optional cloud/comparison sections.
- **RowResult / RowMetric**: Per-dataset-row outcome and per-metric score captured for that row.
- **ThresholdEvaluation**: The pass/fail outcome, expression, and observed aggregate value for a single configured threshold.
- **RunSummary**: Aggregate run-level statistics (for example, overall pass/fail, item counts, per-metric averages) used to derive the exit code.
- **ComparisonInfo / ComparisonRow / ComparisonMetric**: The baseline-comparison structure capturing, per metric, the current value, baseline value, and computed direction of change.
- **TargetInfo**: Metadata identifying which agent target and execution mode produced a given `RunResult`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every completed run, regardless of agent kind or execution mode, produces a `results.json` and `report.md` pair with the same top-level structure (run summary, row results, threshold outcomes).
- **SC-002**: Each of the three exit-code outcomes is distinguishable by its exit code alone: `0` when every threshold passes, `2` when the run completed but at least one threshold failed, `1` when a runtime or configuration error prevented completion - the three codes never overlap for the same outcome.
- **SC-003**: `agentops report generate --in <path>` reproduces `report.md` from a saved `results.json` alone, with no network access and no re-invocation of the evaluated agent.
- **SC-004**: When `--baseline` is supplied, the resulting comparison section includes exactly one entry for every aggregate metric present in either the current or baseline run; a metric present on only one side retains a missing value on the other side rather than receiving a fabricated score.
- **SC-005**: `agentops eval --help` never lists a `compare` subcommand; comparison is reachable only through `--baseline` on `agentops eval run`.
- **SC-006**: A dataset with zero rows never reaches a completed run; every zero-row attempt is rejected before any row is invoked.

## Assumptions

- Downstream consumers (CI systems, Doctor, Cockpit, release evidence) read `results.json` as the canonical machine-readable artifact and treat `report.md` as a rendering of it, not an independent source of truth.
- A `--baseline` file is itself a `results.json` produced by a prior AgentOps run; comparing against externally produced or hand-authored result files is not part of this baseline.
- The exit-code contract (`0`/`1`/`2`) is a stable public contract that downstream automation depends on and is not expected to change as part of this feature area.
- Cloud evaluation identifiers and Foundry deep-links in `cloud_evaluation.json` depend on the cloud evaluation service being reachable at run time; this specification assumes the run has already reached completion when that artifact is produced.

## Out of Scope

- A standalone `agentops eval compare` command; it is not implemented, and this specification explicitly documents its absence rather than describing it as a capability.
- The mechanics of how a run is executed (agent invocation, per-row scoring) which are covered by the Evaluation Execution specification.
- Doctor readiness analysis and release evidence composition, which consume `results.json` history but are specified separately.
- Publishing results to Foundry Evaluations panels, which is covered by the Evaluation Execution specification's publishing behavior.
- Defining or changing individual evaluator scoring logic; this specification concerns the result container and reporting layer, not evaluator internals.

## Implementation Evidence

- `src/agentops/core/results.py` - `RunResult`, `RowResult`, `RowMetric`, `ThresholdEvaluation`, `RunSummary`, `TargetInfo`, `ComparisonInfo`, `ComparisonRow`, and `ComparisonMetric` schema definitions.
- `src/agentops/pipeline/thresholds.py` - `evaluate()` threshold evaluation logic that produces `ThresholdEvaluation` entries and drives `RunSummary.overall_passed`.
- `src/agentops/pipeline/orchestrator.py` - `exit_code_from(result)` translating `result.summary.overall_passed` into the `0`/`2` contract, with runtime errors raised as exceptions surfaced as exit code `1` by the CLI layer.
- `src/agentops/pipeline/comparison.py` - `load_baseline()` and `build_comparison()` implementing the `--baseline` comparison logic.
- `src/agentops/pipeline/reporter.py` - `render(result)` rendering `RunResult` into `report.md`, including threshold, comparison, cloud evaluation, and azd aggregate sections.
- `src/agentops/cli/app.py` - `eval run` command wiring `--baseline`, exit-code translation, and the `report generate` command regenerating `report.md` from a saved `results.json`.
- `tests/unit/test_cli_commands.py::test_eval_help_does_not_expose_compare_subcommand` - direct evidence that `agentops eval compare` is not implemented.
- `tests/integration/test_pipeline_smoke.py` (`test_http_pipeline_end_to_end`, `test_http_pipeline_with_baseline`) - end-to-end coverage of result production and baseline comparison.
- `tests/unit/test_pipeline_reporter.py` - unit coverage of `report.md` rendering from `results.json`.
- `docs/how-it-works.md` - narrative documentation of the results/report/exit-code contract consistent with this baseline.
