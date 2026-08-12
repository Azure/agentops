# Feature Specification: Trace-to-Regression Promotion

**Feature Branch**: `placerda-spec-kit-feature`

**Created**: 2026-08-12

**Status**: Implemented Baseline

**Input**: This specification was reverse-engineered from the current implementation, tests, and public documentation of AgentOps Toolkit. It documents the existing `agentops eval promote-traces` behavior as-built, not a new proposal. Sources reviewed include `src/agentops/services/trace_promotion.py`, the `promote-traces` command in `src/agentops/cli/app.py`, `src/agentops/services/evidence_pack.py`, `src/agentops/agent/cockpit.py`, and `tests/unit/test_trace_promotion.py`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preview candidate dataset rows from a production trace export before writing anything (Priority: P1)

An operator has a JSON or JSONL export of production traces and wants to preview what a promoted regression-dataset candidate would look like -- how many rows, what fields, what labels -- before any file is written or modified.

**Why this priority**: A safe, review-first preview is the foundation of the whole feature; every other capability (applying, labeling, deduplication, lineage) only matters once a trustworthy preview exists.

**Independent Test**: Can be fully tested by running `agentops eval promote-traces --source <export>` without `--apply` and confirming a preview of candidate rows is printed and that no dataset or manifest file is created or modified as a result.

**Acceptance Scenarios**:

1. **Given** a JSON or JSONL trace export file, **When** `agentops eval promote-traces --source <export>` is run without `--apply`, **Then** a preview of the candidate dataset rows is displayed and no output file is written.
2. **Given** the same export, **When** the preview is inspected, **Then** it shows a truncated sample of up to 3 candidate rows' input text alongside a summary of the total candidate count, skipped-record count, and any warnings, so the operator can judge quality before applying without needing every row rendered.
3. **Given** a `--max-rows` value smaller than the number of traces in the export, **When** the preview is generated, **Then** it contains at most that many rows.

---

### User Story 2 - Apply a reviewed promotion to create a candidate dataset with lineage (Priority: P1)

An operator who has reviewed a preview and is satisfied wants to run the same command with `--apply` to actually write the candidate dataset file and an accompanying manifest recording aggregate lineage for the run.

**Why this priority**: Writing the reviewed output with traceable lineage is what turns a preview into a usable dataset candidate for future eval runs, Doctor, Cockpit, and release evidence.

**Independent Test**: Can be fully tested by running `agentops eval promote-traces --source <export> --apply --out <path>` and confirming the candidate dataset file and a `trace-regression-manifest.json` are written, with the manifest recording aggregate lineage information (for example, distinct source trace identifiers) for the run as a whole.

**Acceptance Scenarios**:

1. **Given** a reviewed trace export, **When** `agentops eval promote-traces --source <export> --apply --out <path>` is run, **Then** the candidate dataset file is written to `<path>` and a `trace-regression-manifest.json` is written alongside it.
2. **Given** the written manifest, **When** it is inspected, **Then** it records aggregate lineage available across the promoted rows - such as distinct source trace identifiers, replay URLs, evaluation URLs, source systems, agents, agent versions, and sampling policies when those values exist - plus a count of multi-turn rows, rather than a per-row source mapping.
3. **Given** the same export is promoted twice with `--apply` targeting the same `--out` path, **When** the second run completes, **Then** duplicate rows within that second run's own candidate set are not written twice, but the written output reflects only that run's candidates - the second run's write overwrites the first run's output file rather than merging or deduplicating against it.

---

### User Story 3 - Choose a labeling mode appropriate to review maturity (Priority: P2)

An operator wants to choose between `self-similarity` labeling (storing the production response as the `expected` value, to catch future behavior drift against a known production answer) and `pending` labeling (leaving `expected` blank, marking rows as awaiting human review), depending on how much manual review has already happened.

**Why this priority**: Label-mode choice affects how the resulting dataset can be used (drift detection versus a to-be-reviewed backlog), but it is a refinement on top of the core preview/apply flow.

**Independent Test**: Can be fully tested by running promotion once with `--label-mode self-similarity` and once with `--label-mode pending` against the same export, and confirming the `expected` field is populated from the production response in the first case and left blank with a pending marker in the second.

**Acceptance Scenarios**:

1. **Given** `--label-mode self-similarity`, **When** promotion runs, **Then** each candidate row's `expected` field is populated with the corresponding production response text, and the row's metadata still marks it as needing review.
2. **Given** `--label-mode pending`, **When** promotion runs, **Then** each candidate row's `expected` field is left blank and the output communicates that rows are pending human labeling before being used as a blocking gate.
3. **Given** either label mode, **When** a candidate row is produced, **Then** its metadata always marks `needs_review` as true - `self-similarity` labeling never marks a row as already reviewed.
4. **Given** an unsupported `--label-mode` value, **When** promotion is invoked, **Then** the command rejects it with a validation error rather than silently defaulting to a different mode.

---

### Edge Cases

- What happens when `--max-rows` is zero or negative? The command MUST reject it with a validation error rather than silently promoting zero or a default number of rows.
- What happens when a trace record's JSON cannot be parsed at all (invalid JSON on a line)? The command MUST abort the entire load with a clear error rather than silently skipping the malformed line.
- What happens when a trace record parses as valid JSON but lacks a usable input or response? That record MUST be skipped (not counted as a candidate row) rather than causing the entire promotion to fail.
- What happens when multiple source records produce the same candidate `(input, expected)` pair? The de-duplication key MUST prevent that pair from producing duplicate candidate rows within that one promotion run, even if the source records have different trace identifiers.
- What happens when the same export (or an overlapping export) is promoted with `--apply` more than once against the same `--out` path? De-duplication applies only within each individual run's own candidate set; the write MUST overwrite the previous output file rather than merging with or deduplicating against rows already written by an earlier run.
- What happens when `--apply` is used without `--out`? The command MUST fall back to a documented default output location rather than requiring the flag.
- What happens when self-similarity-labeled rows are later consumed by an eval run? The dataset and its documentation MUST make clear that a self-similarity label only supports drift detection against a prior production response, not a verified-correct answer, and every row (in either label mode) MUST still be marked as needing review.
- How does release evidence or Cockpit reflect a workspace that has never run promotion? The corresponding check MUST report the absence of a promotion manifest rather than erroring (see Release Evidence specification).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide `agentops eval promote-traces --source <path>` to read a JSON or JSONL production trace export and derive candidate regression-dataset rows from it.
- **FR-002**: The system MUST default to a preview-only mode that displays a summary and a truncated sample of at most three candidate inputs without writing any file, requiring an explicit `--apply` flag to write output.
- **FR-003**: The system MUST support `--max-rows <n>` to cap the number of candidate rows produced, and MUST reject a non-positive value with a validation error.
- **FR-004**: The system MUST support `--label-mode self-similarity|pending`, where `self-similarity` populates each row's `expected` field from the production response and `pending` leaves it blank pending human review, and MUST reject any other value.
- **FR-005**: The system MUST mark every candidate row's metadata as needing review (`needs_review: true`) regardless of which label mode produced it - `self-similarity` labeling never marks a row as already reviewed.
- **FR-006**: When `--apply` is used, the system MUST write the candidate dataset to the requested (or default) `--out` path and MUST also write an accompanying `trace-regression-manifest.json` recording aggregate lineage available for the run (distinct source trace identifiers, replay URLs, evaluation URLs, source systems, agents, agent versions, sampling policies, and a multi-turn-row count when those values exist), rather than a per-row source mapping.
- **FR-007**: The system MUST de-duplicate candidate rows within a single promotion invocation using the `(input, expected)` pair. This de-duplication does not extend across separate invocations: each `--apply` run's write to `--out` overwrites any prior output rather than merging with or deduplicating against rows from an earlier run.
- **FR-008**: The system MUST abort the entire promotion with a clear error when a trace-export line cannot be parsed as valid JSON, and MUST separately skip (without aborting) any well-formed record that lacks a usable input or response.
- **FR-009**: The system MUST clearly communicate, in both `pending`-mode output and accompanying documentation, that promoted rows always require human review before being trusted as a blocking regression gate, and that `self-similarity` labels support drift detection rather than verified correctness.
- **FR-010**: The manifest written on `--apply` MUST be consumable by the Release Evidence and Cockpit features to report the state of the trace-to-dataset promotion flywheel from its aggregate lineage fields (see the Release Evidence and Read-Only Cockpit specifications) without those features re-implementing promotion logic themselves.

### Key Entities

- **TracePromotionPreview**: The in-memory result of a promotion run, containing candidate rows, the resolved output path, and the resolved manifest path, used both for preview rendering and for `--apply` writing.
- **Candidate Row**: A single derived dataset row (input, expected/context/tool fields per the current label mode) produced from one source trace, always carrying `metadata.needs_review: true` regardless of label mode.
- **LabelMode**: One of `self-similarity` or `pending`, controlling how the `expected` field is populated.
- **trace-regression-manifest.json**: The lineage manifest written alongside the candidate dataset on `--apply`, recording aggregate lineage values available for the whole run (distinct trace identifiers, replay URLs, evaluation URLs, source systems, agents, agent versions, sampling policies, and a multi-turn-row count) - not a per-row source mapping - and overwritten (not merged) on each `--apply` invocation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Running promotion without `--apply` always displays a preview (a sample of up to 3 candidate rows' input text plus a summary) and never creates or modifies a dataset or manifest file.
- **SC-002**: Running promotion with `--apply` writes both the candidate dataset and `trace-regression-manifest.json`, and the manifest aggregates every trace identifier, agent, system, URL, version, and sampling policy that is available in that run's candidate metadata without inventing absent lineage values.
- **SC-003**: `self-similarity` mode always populates `expected` from the production response and `pending` mode always leaves it blank; in both modes, every candidate row is always marked as needing review.
- **SC-004**: Within a single promotion run, no two candidate rows share the same `(input, expected)` pair; across separate `--apply` invocations targeting the same output path, each run's write reflects only that run's own candidates, never a merge with a prior run's output.
- **SC-005**: A non-positive `--max-rows` value and an unsupported `--label-mode` value are always rejected with a validation error before any row is processed.
- **SC-006**: An invalid JSON line in the trace export always aborts the entire load with a clear error, while a well-formed record lacking usable input/response is always skipped without aborting the run - the two failure modes are never conflated.

## Assumptions

- The trace export consumed by `promote-traces` is assumed to already exist (produced by an external export mechanism); this feature does not perform the export itself.
- Operators are expected to review promoted rows -- especially `pending`-mode rows -- before relying on the resulting dataset as a release-blocking eval gate.
- The de-duplication key is the candidate's `(input, expected)` pair and is scoped to a single in-memory promotion run only; it is not persisted or checked against a previously written output file, so operators who run `--apply` repeatedly against overlapping exports are expected to treat each run's output as authoritative and complete for that run, not as an incremental merge.

## Out of Scope

- Exporting traces from a running agent or Application Insights instance; this feature only consumes an already-produced export file.
- Live or streaming trace ingestion; promotion operates on a static export file per invocation.
- Automatic labeling or correctness judgment beyond the `self-similarity` (prior production response) and `pending` (blank) modes; no model-graded labeling is performed.
- Bidirectional synchronization between the promoted dataset and the original production trace source.
- Triggering promotion automatically (for example, on a schedule or CI event); the command is invoked explicitly by an operator.

## Implementation Evidence

- `src/agentops/services/trace_promotion.py` - `TracePromotionPreview`, `promote_traces()` (with `max_rows`, `apply`, and `label_mode` parameters and validation), `LabelMode = Literal["self-similarity", "pending"]`, `_trace_to_row()` populating `expected` conditionally on label mode, `_write_trace_dataset()` and the `trace-regression-manifest.json` output path, `_lineage_from_rows()` for manifest lineage, and the `seen` de-duplication set keyed per trace.
- `src/agentops/cli/app.py` - `eval promote-traces` command wiring `--source`, `--out`, `--max-rows`, `--label-mode`, and `--apply`.
- `src/agentops/services/evidence_pack.py` (`_trace_dataset_status`, `_add_trace_dataset_check`) - consumption of the promotion manifest for the "Trace-to-dataset flywheel" release-evidence check.
- `src/agentops/agent/cockpit.py` - Cockpit surfacing of trace-to-dataset promotion state consistent with the Read-Only Cockpit specification.
- `tests/unit/test_trace_promotion.py` - unit coverage of preview rendering, `--apply` writing, label-mode behavior, de-duplication, and validation errors.
