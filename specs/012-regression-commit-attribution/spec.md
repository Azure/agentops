# Feature Specification: Regression Commit Attribution

**Feature Branch**: `012-regression-commit-attribution`

**Created**: 2026-09-11

**Status**: Draft

**Input**: User description: "Today, when an eval run's metrics regress relative to a prior comparable run, nobody is told why automatically — someone has to manually go dig up what changed between the two runs (system prompt, model, config). Record commit metadata on evaluation runs, starting with and prioritizing the hosted-agent scenario (Foundry hosted agents run via cloud/azd execution in CI), where CI already provides reliable source-control information. Once two comparable runs both have commit metadata and a metric has regressed between them, automatically correlate the two commits and produce a plain-language explanation of the likely cause (prompt changed, model changed, config changed) plus a suggested corrective adjustment, using deterministic diffing rather than an LLM call. Surface this in the existing evaluation report (already attached to the PR pipeline as a CI artifact and PR comment) with no new delivery mechanism. Additionally, give Cockpit a history view listing each evaluated version and what changed relative to the previous comparable version, independent of whether a regression occurred."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See why a hosted-agent evaluation regressed, without manual digging (Priority: P1)

A release engineer opens a pull request whose CI pipeline ran `agentops eval run` against a Foundry hosted agent and sees that a metric (for example accuracy) regressed compared to the previous comparable run. Instead of having to check out both commits and manually diff the prompt, model, and configuration, the evaluation report already tells them, in plain language, what changed between the two runs and what likely caused the drop.

**Why this priority**: This is the core value of the feature and the scenario the product owner explicitly asked to prioritize (hosted agents evaluated in CI/cloud, where commit information is already reliably available). Every other capability builds on this correlation working for at least one execution path.

**Independent Test**: Can be fully tested by running two evaluations of the same Foundry hosted agent/dataset/evaluator combination from two different commits in a CI-like environment, where the second run's metric regresses relative to the first, and confirming the resulting evaluation report contains a plain-language explanation naming the regressed metric's before/after values and the concrete changed inputs (e.g. prompt content, model identifier).

**Acceptance Scenarios**:

1. **Given** two CI-executed evaluation runs of the same Foundry hosted agent, dataset, and evaluator set, each with recorded commit metadata, **When** the second run's metric regresses relative to the first, **Then** the second run's evaluation report includes a plain-language statement of the metric change (e.g. "accuracy dropped from 0.91 to 0.79") and the changed inputs found between the two commits (e.g. "the system prompt changed" and/or "the model changed from gpt-4o to gpt-4o-mini").
2. **Given** a regression explanation naming a changed input, **When** the operator reads the report, **Then** it also includes a brief suggested corrective adjustment tied to that changed input (for example, reviewing or reverting the identified change).
3. **Given** the report is uploaded as a CI artifact and posted as a PR comment as it already is today, **When** a regression explanation is produced, **Then** it appears in that same artifact and PR comment with no additional workflow step required.
4. **Given** two comparable runs where only configuration (e.g. dataset path, evaluator set, or thresholds) changed and not the prompt or model, **When** a regression occurs, **Then** the explanation names the configuration field(s) that changed instead of guessing at a prompt or model change that didn't happen.

---

### User Story 2 - Browse a history of evaluated versions and what changed between them (Priority: P2)

An operator opens Cockpit and wants to see, for a given agent and dataset, the sequence of evaluation runs over time along with what changed from one to the next (prompt, model, or configuration), so they can understand the evaluation trend as a causal trail rather than a bare metrics chart.

**Why this priority**: This turns the same underlying commit-and-config comparison into an always-available browsing view, not just a reactive regression alert, but it depends on commit metadata and change-detection already existing from User Story 1.

**Independent Test**: Can be fully tested by producing three or more evaluation runs for the same agent/dataset/evaluator combination from different commits (with at least one metric change that is not a regression) and confirming Cockpit's history view lists each run in order with its recorded commit (when known) and a summary of what changed relative to the immediately preceding run, regardless of whether that run's metrics improved, regressed, or stayed flat.

**Acceptance Scenarios**:

1. **Given** a workspace with several prior evaluation runs for the same methodology, **When** the operator opens Cockpit's version history view, **Then** each run is listed with its commit metadata (when known) and the changes detected relative to the previous run.
2. **Given** a run in the history for which no metric regressed, **When** it is displayed in the history view, **Then** it still shows what changed relative to the previous run (the view is not gated on regression).
3. **Given** a run with no recorded commit metadata (for example, an older run captured before this feature existed), **When** it appears in the history view, **Then** it is shown without a commit reference and without a fabricated change description, rather than being omitted or causing an error.

---

### User Story 3 - Best-effort commit attribution for local evaluation runs (Priority: P3)

A developer running `agentops eval run` locally against a git-managed workspace gets the same commit metadata capture and regression attribution as the CI/hosted-agent path, on a best-effort basis, so the benefit isn't limited to CI.

**Why this priority**: Valuable for local iteration, but explicitly lower priority than the hosted-agent/CI scenario per product direction, and depends on the same underlying mechanism already built for User Story 1.

**Independent Test**: Can be fully tested by running two local evaluations from two different commits inside a local git repository and confirming commit metadata is recorded on both runs and that a regression between them produces the same kind of explanation as the CI path; and separately, by running an evaluation outside of any git repository and confirming the run still completes normally with no commit metadata and no error.

**Acceptance Scenarios**:

1. **Given** a local workspace that is a git repository, **When** `agentops eval run` executes locally, **Then** the resulting run's stored result includes commit metadata for the current commit.
2. **Given** a local workspace that is not a git repository (or where git is unavailable), **When** `agentops eval run` executes locally, **Then** the run completes exactly as it does today, without commit metadata and without any new error or warning that blocks the run.

---

### Edge Cases

- Commit metadata is missing for one or both runs being compared (local run outside a git repo, shallow CI checkout, or a run captured before this feature existed): the system reports the regression's metric numbers as it does today, without a fabricated cause.
- The two commits are not in a resolvable ancestor/descendant relationship (e.g. runs from unrelated branches, rebased or force-pushed history) or one commit is no longer present in the local git history (e.g. pruned): the system falls back to comparing whatever fields are already recorded in each run's stored result rather than requiring `git diff` access to both commits.
- Multiple inputs changed at once (prompt and model and configuration): the explanation lists all detected changes rather than only the first one found.
- No prior comparable run exists yet for a given methodology (first run of its kind): no regression explanation and no history entry's "changed" comparison is produced for that first run.
- A metric improves rather than regresses between two comparable runs: no cause/attribution explanation is generated for the report (attribution is regression-triggered), but the run still appears in Cockpit's version history with its changes relative to the previous run.
- The regression is detected via the rolling-baseline check rather than an explicit `--baseline` comparison: the same attribution logic applies to either detection path.
- Two runs are "comparable" by methodology fingerprint but were produced through different execution modes (e.g. one local, one CI): attribution still proceeds as long as both have commit metadata.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST record commit metadata (commit SHA, short SHA, subject line, author, and commit timestamp) on an evaluation run's stored result whenever it can be determined from the environment the run executes in.
- **FR-002**: For evaluation runs against a Foundry hosted agent executed via cloud or azd execution in a CI environment, the system MUST reliably capture commit metadata from CI-provided source-control information, at the same reliability level already achieved for existing CI-based commit capture.
- **FR-003**: For local execution, the system MUST make a best-effort attempt to capture commit metadata from the local git repository when the working directory is one, and MUST allow the run to complete normally, without error, when it is not.
- **FR-004**: Commit metadata MUST be persisted as an additive part of the run's stored result such that existing consumers of that result format that are unaware of the new field are unaffected.
- **FR-005**: The system MUST only compare two runs for regression-cause attribution when they share the same evaluation methodology (same agent target, dataset, and evaluator set) already used for existing baseline and regression comparisons.
- **FR-006**: When a regression is detected between two comparable runs that both have recorded commit metadata, the system MUST determine, at minimum, whether the evaluated agent's system prompt content, its model/deployment identifier, and other tracked run configuration (dataset, evaluator set, thresholds) changed between the two commits.
- **FR-007**: When a regression cause is determined, the system MUST produce a plain-language, one-to-two-sentence explanation naming the regressed metric's before/after values and the concrete inputs found to have changed.
- **FR-008**: Where a likely cause is identified, the system SHOULD include a brief suggested corrective adjustment tied directly to that cause.
- **FR-009**: When commit metadata is missing for either compared run, or the changed inputs cannot be determined, the system MUST still report the regression's metric numbers as it does today, without producing a fabricated cause.
- **FR-010**: A produced regression explanation MUST appear in the same evaluation report already generated for the run, and MUST reach every destination that report is already delivered to (CI artifact upload and PR comment) without requiring a new delivery mechanism.
- **FR-011**: Adding commit metadata and regression explanations MUST NOT change the existing exit-code or threshold-gating outcome of a run; this feature is informational only.
- **FR-012**: Cockpit MUST provide a history view that, for a given evaluation methodology, lists its evaluated runs in order, each showing its commit reference (when known) and the changes detected relative to the immediately preceding comparable run, independent of whether that run regressed.
- **FR-013**: Determining what changed between two runs' commits MUST be based on deterministic comparison of recorded fields and content (prompt text, model identifier, tracked configuration values), not a generative or LLM-based summarization call.

### Key Entities

- **Commit Metadata**: The commit SHA, short SHA, subject line, author, and commit timestamp attributed to a single evaluation run, along with how it was obtained (CI-provided vs. local best-effort); absent when it could not be determined.
- **Regression Insight**: A pairing of two comparable evaluation runs (the prior run and the regressed run), the metric(s) that regressed with their before/after values, the list of concrete changed inputs detected between the two (each with a short description such as "system prompt changed" or "model changed from X to Y"), and an optional suggested corrective adjustment.
- **Version History Entry**: A single evaluated run as shown in Cockpit's history view for a given methodology: its metrics, its commit reference (when known), and the changes detected relative to the previous entry, independent of regression status.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of evaluation runs against a Foundry hosted agent executed via cloud/azd execution in a CI environment that provides source-control information have commit metadata recorded in their stored result.
- **SC-002**: When a metric regresses between two comparable runs that both have commit metadata and either the prompt or the model changed, the evaluation report names that change without the operator performing any manual commit comparison.
- **SC-003**: When exactly one of prompt, model, or tracked configuration changed between two compared commits, an operator reviewing the report alone can correctly identify which one changed at least 90% of the time.
- **SC-004**: An operator can view, for the last several evaluated versions of a given agent/dataset in Cockpit, what changed at each step without consulting any other tool or manually comparing commits.
- **SC-005**: Runs lacking commit metadata, or for which a change cause cannot be determined, continue to produce a regression report identical in structure to today's, with no new errors introduced by this feature.

## Assumptions

- "Hosted agents" refers to Foundry hosted agent targets evaluated via cloud or azd execution, consistent with how the system already classifies agent targets.
- The CI environment used for the PR pipeline provides the commit SHA of the code under evaluation through standard CI environment variables, the same class of information already relied on for existing CI-based commit capture elsewhere in the system.
- "Comparable runs" reuses the existing methodology-fingerprint concept (same agent target, dataset, and evaluator set) already used for baseline and rolling-baseline regression comparisons; this feature does not introduce a new comparability rule.
- A full commit-to-commit diff assumes both compared commits are reachable in the local git history available at comparison time; when they are not (e.g. a shallow clone), the system falls back to comparing the fields already recorded in each run's stored result instead of requiring direct git access to both commits.
- "System prompt" content for diffing is whatever prompt content the run's configuration already records for the evaluated agent version; this feature does not introduce new prompt-capture beyond what configuration/version resolution already provides.
- Suggested corrective adjustments are short and rule-based, directly tied to the specific change detected (e.g. a changed prompt suggests reviewing that prompt change); this is not a general-purpose troubleshooting assistant and does not call out to a language model.
- Regression detection itself (thresholds, what counts as a regression) is unchanged by this feature; it reuses whichever existing detection path (explicit baseline comparison or rolling-baseline check) flagged the regression.
