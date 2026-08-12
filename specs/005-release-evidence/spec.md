# Feature Specification: Release Evidence

**Feature Branch**: `placerda-spec-kit-feature`

**Created**: 2026-08-12

**Status**: Implemented Baseline

**Input**: This specification was reverse-engineered from the current implementation, tests, and public documentation of AgentOps Toolkit. It documents the existing `agentops doctor --evidence-pack` behavior as-built, not a new proposal. Sources reviewed include `src/agentops/core/release_evidence.py`, `src/agentops/services/evidence_pack.py`, `src/agentops/cli/app.py`, and `tests/unit/test_release_evidence.py`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Get a single evidence artifact answering "can we ship it" (Priority: P1)

A release manager preparing to promote a build wants one composed artifact that pulls together evaluation results, Doctor readiness, workflow presence, and related signals into a single ready/ready-with-warnings/blocked verdict, without running any new evaluation or remediation.

**Why this priority**: This is the entire purpose of the feature -- a single, trustworthy release-readiness artifact assembled from signals that already exist in the workspace.

**Independent Test**: Can be fully tested by running `agentops doctor --evidence-pack` against a workspace with controlled eval, Doctor, workflow, Foundry, and governance signals, then confirming the resulting evidence artifact's overall status and per-check statuses match those inputs without executing a new evaluation or deployment.

**Acceptance Scenarios**:

1. **Given** every composed evidence check is ready and none produces a warning or blocker, **When** `agentops doctor --evidence-pack` is run, **Then** the composed evidence reports an overall status of `ready`.
2. **Given** no composed check is blocked but at least one check produces a non-blocking advisory (for example, a `warning`-severity Doctor finding or a missing baseline), **When** the evidence pack is composed, **Then** the overall status is `ready_with_warnings` rather than `ready` or `blocked`.
3. **Given** any composed check produces a blocking condition (for example, the latest eval run failed its thresholds), **When** the evidence pack is composed, **Then** the overall status is `blocked`.
4. **Given** the evidence pack composition completes, **When** the output is inspected, **Then** both a machine-readable `evidence.json` and a human-readable `evidence.md` are written, and every individual check inside them carries its own status (`ready`, `warning`, `blocked`, or `unknown`).

---

### User Story 2 - Trust that evidence never leaks secrets (Priority: P1)

A security-conscious release manager wants any credential-shaped values (for example, connection strings, keys, tokens) that might appear inside underlying signal data to be redacted before they are written into the evidence artifacts.

**Why this priority**: Evidence artifacts are meant to be shared broadly (attached to a release, reviewed by stakeholders); leaking a secret through this artifact would be a serious regression, so redaction is as critical as composing the evidence itself.

**Independent Test**: Can be fully tested by seeding underlying signal data (for example, a results file or config value) with a recognizable secret-shaped string and confirming the resulting `evidence.json`/`evidence.md` contain a redaction marker in place of that value rather than the raw value.

**Acceptance Scenarios**:

1. **Given** an underlying signal contains a connection-string-shaped or key-shaped value, **When** the evidence pack is composed, **Then** the value is replaced with a redaction marker in both `evidence.json` and `evidence.md`.
2. **Given** the redaction has been applied, **When** the resulting JSON is parsed back, **Then** it remains valid JSON conforming to the `ReleaseEvidence` schema, confirming redaction does not corrupt the artifact's structure.

---

### User Story 3 - Control where evidence is written (Priority: P3)

A release engineer integrating evidence composition into an automated deployment workflow wants to specify a custom output location for the evidence artifacts, or otherwise rely on a sensible default location within the workspace.

**Why this priority**: Custom output paths matter for pipeline integration but are a secondary convenience relative to the correctness of the composed evidence itself.

**Independent Test**: Can be fully tested by running `agentops doctor --evidence-pack --evidence-out <custom-path>` and confirming the artifacts are written to the custom path, then running without `--evidence-out` and confirming they are written to the documented default location.

**Acceptance Scenarios**:

1. **Given** `--evidence-out <path>` is supplied, **When** the evidence pack is composed, **Then** `evidence.json` and `evidence.md` are written under that path.
2. **Given** `--evidence-out` is omitted, **When** the evidence pack is composed, **Then** the artifacts are written under `.agentops/release/latest`.

---

### Edge Cases

- What happens when no prior eval run exists in the workspace? The "Latest eval gate" check MUST report a `blocked` or `unknown` status with an explanatory summary rather than silently omitting the check.
- What happens when no Doctor analysis is supplied to the evidence composer? The composer MUST still produce valid `evidence.json`/`evidence.md` artifacts with Doctor readiness represented as unavailable or warning, rather than claiming a successful Doctor result.
- How does composition handle a workspace with CI/CD workflows partially generated (for example, PR gate present, deploy workflows missing)? The "PR gate" and "Deploy workflows" checks MUST be reported independently, each reflecting only what is actually present.
- What happens when the trace-to-dataset promotion flywheel has never been used in the workspace? The "Trace-to-dataset flywheel" check MUST report its status based on the absence of promotion manifests rather than erroring.
- What happens when a `--baseline` regression comparison was configured for the latest eval run? The "Regression baseline" check MUST reflect that a baseline comparison was present for that run.
- What happens when redaction encounters a value that only partially resembles a secret pattern (for example, a UUID that is not actually a credential)? Redaction rules MUST be scoped narrowly enough that this does not corrupt non-secret evidence content beyond what the documented redaction patterns are designed to match.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide `agentops doctor --evidence-pack` to compose a single release-evidence artifact from the current Doctor analysis and existing workspace signals (eval results, generated CI/CD workflows, trace-to-dataset promotion state, and related checks) without invoking any new evaluation.
- **FR-002**: The system MUST compute an overall readiness status of exactly one of `ready`, `ready_with_warnings`, or `blocked`, derived from the presence of blocking versus warning-level findings across the composed checks.
- **FR-003**: The system MUST represent each individual composed check with its own status of `ready`, `warning`, `blocked`, or `unknown`, a human-readable summary, and supporting evidence detail.
- **FR-004**: The system MUST write a machine-readable `evidence.json` conforming to a versioned `ReleaseEvidence` schema and a human-readable `evidence.md` rendering of the same content.
- **FR-005**: The system MUST redact credential-shaped values (for example, connection strings, keys, tokens) from both `evidence.json` and `evidence.md` before they are written to disk.
- **FR-006**: The system MUST support a custom output location via `--evidence-out <path>` and MUST fall back to a documented default location within the workspace when the flag is omitted.
- **FR-007**: The system MUST NOT execute, remediate, or modify any evaluation, Doctor finding, or CI/CD workflow as a side effect of composing evidence; composition is read-only over already-existing signals.
- **FR-008**: The system MUST produce valid `evidence.json`/`evidence.md` artifacts even when one or more underlying signals are unavailable (for example, no eval history), representing the affected checks as `unknown` or `blocked` rather than aborting composition entirely.
- **FR-009**: The system MUST include links to relevant external resources (for example, the latest Foundry evaluation or Azure Monitor dashboard) in the composed evidence when the corresponding signal identifies such a resource.

### Key Entities

- **ReleaseEvidence**: The root composed artifact, containing an overall `ReadinessStatus`, a list of `ReleaseEvidenceCheck` entries, and a list of `ReleaseEvidenceLink` entries.
- **ReleaseEvidenceCheck**: A single named check (for example, "Latest eval gate", "Doctor readiness", "PR gate", "Deploy workflows", "Regression baseline", "Trace-to-dataset flywheel", "Runtime monitoring", "AI Landing Zone readiness") with its own `CheckStatus`, summary, and evidence payload.
- **ReleaseEvidenceLink**: A named external link (for example, to a Foundry evaluation run or an Azure Monitor workbook) surfaced alongside the composed evidence.
- **ReadinessStatus**: The overall composed verdict, one of `ready`, `ready_with_warnings`, or `blocked`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Each of the three overall readiness statuses (`ready`, `ready_with_warnings`, `blocked`) is produced exactly when its documented underlying-signal condition holds - a `blocked`-triggering condition never composes as `ready` or `ready_with_warnings`, and vice versa.
- **SC-002**: A credential-shaped value present in any underlying signal never appears verbatim in the composed `evidence.json` or `evidence.md`; it is always replaced with a redaction marker.
- **SC-003**: Composing evidence in a workspace with no prior eval history always produces a valid, schema-conforming `evidence.json` with the affected check reported as `unknown` or `blocked`, never an unhandled error and never a missing artifact.
- **SC-004**: `--evidence-out <path>` always controls the artifacts' output location; omitting it always falls back to the same documented default location.
- **SC-005**: Composing evidence never changes the content of any existing eval result, Doctor history entry, or CI/CD workflow file already present in the workspace.
- **SC-006**: Every individual composed check reports exactly one of `ready`, `warning`, `blocked`, or `unknown` - no check is left without a status.

## Assumptions

- Release evidence composition depends on the current Doctor analysis plus workspace signals also used by Doctor and Cockpit (results history, generated workflows, trace promotion manifests); this feature does not introduce a new monitoring or evaluation source of its own.
- The redaction patterns implemented are pattern-based (matching common credential-shaped strings) rather than an exhaustive secret-scanning solution; teams with additional secret formats are expected to avoid placing them in evidence-adjacent files.
- Evidence composition itself does not execute a new cloud query or mutation. The surrounding Doctor run may still read configured cloud sources using the credentials normally required by Doctor.

## Out of Scope

- Running a new evaluation as part of evidence composition; only already-completed eval history is consumed.
- Remediating any blocking or warning finding surfaced in the evidence; this feature only reports status.
- Comprehensive secret scanning beyond the implemented pattern-based redaction rules.
- Doctor's own readiness analysis logic, which is specified separately (see Doctor Readiness); this feature only consumes Doctor's output.
- Publishing or distributing the evidence artifact externally (for example, attaching it to a release or uploading it to a portal); this feature only writes local files.

## Implementation Evidence

- `src/agentops/core/release_evidence.py` - `ReleaseEvidence`, `ReleaseEvidenceCheck`, `ReleaseEvidenceLink` schema models, `ReadinessStatus` (`ready`/`ready_with_warnings`/`blocked`), and `CheckStatus` (`ready`/`warning`/`blocked`/`unknown`) literal types.
- `src/agentops/services/evidence_pack.py` - `build_release_evidence()` composing the overall status from per-check results, `write_release_evidence()` writing `evidence.json`/`evidence.md`, `render_release_evidence_markdown()` for Markdown rendering, and `_redact_text()`/`_redact_obj()` redaction logic applied before writing.
- `src/agentops/services/evidence_pack.py` (`_add_eval_check`, `_add_doctor_check`, `_add_workflow_checks`, `_add_baseline_check`, `_add_trace_dataset_check`, `_add_monitoring_check`, `_add_ailz_check`, `_add_governance_check`, `_add_agent_identity_check`, `_add_foundry_check`) - the individual composed checks referenced in the Key Entities and User Story acceptance scenarios above.
- `src/agentops/cli/app.py` - `doctor` command wiring of `--evidence-pack` and `--evidence-out`.
- `tests/unit/test_release_evidence.py` - unit coverage of evidence composition, status derivation, and redaction behavior.
- `docs/doctor-explained.md` - narrative documentation of `--evidence-pack` as a readiness projection over existing signals, consistent with this baseline.
