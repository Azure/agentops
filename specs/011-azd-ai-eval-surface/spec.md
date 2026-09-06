# Feature Specification: Current azd AI Evaluation Surface Support

**Feature Branch**: `011-azd-ai-eval-surface`

**Created**: 2026-09-06

**Status**: Draft

**Input**: GitHub issue [Azure/agentops#484](https://github.com/Azure/agentops/issues/484) - "Support the current azd ai eval command surface". AgentOps currently delegates `execution: azd` to the legacy `azd ai agent eval` command surface and discovers `eval.yaml` recipes. Current Microsoft Foundry guidance uses the `azure.ai.evaluations` extension with `azd ai eval`, `evals/azure.eval.yaml`, `azd ai eval create`, `azd ai eval run start`, and `azd ai eval run output list`. This gap blocks the AgentOps VBD Evaluate lab (related: [#483](https://github.com/Azure/agentops/issues/483)) from using `agentops eval run` as the evidence and release-readiness wrapper around the current Foundry cloud evaluation flow.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Evaluate a Foundry hosted agent through the current azd evaluation surface (Priority: P1)

A release engineer following current Microsoft Foundry guidance has an `evals/azure.eval.yaml` recipe in their workspace. They set `execution: azd` in `agentops.yaml` and run `agentops eval run`. AgentOps discovers the current-surface recipe, delegates the cloud evaluation to the current azd evaluation commands, and produces the same normalized `results.json` and `report.md` that every other AgentOps execution mode produces.

**Why this priority**: This is the entire point of the feature. Without it, teams on current Foundry guidance cannot use AgentOps as their release-readiness wrapper at all, and the VBD Evaluate lab has no supported path. Every other story in this specification is a refinement of this one.

**Independent Test**: Can be fully tested by placing a current-surface recipe at `evals/azure.eval.yaml`, setting `execution: azd`, running `agentops eval run`, and confirming that the evaluation is submitted and retrieved through the current azd evaluation surface exactly once and that a normalized `results.json` and `report.md` are written to the run's artifact directory.

**Acceptance Scenarios**:

1. **Given** a workspace containing exactly one current-surface recipe at `evals/azure.eval.yaml` and `execution: azd` in `agentops.yaml`, **When** `agentops eval run` is executed, **Then** the recipe is auto-discovered without requiring an explicit `eval_recipe` path.
2. **Given** the same setup, **When** the run executes, **Then** the cloud evaluation is created, started, and its per-sample output retrieved through the current azd evaluation surface, and the evaluation itself is executed exactly once (no duplicate submission and no re-execution to retrieve results).
3. **Given** a completed current-surface run, **When** artifacts are written, **Then** run-level metrics, rubric dimension scores, per-sample outcomes, and any per-sample failures are normalized into the existing `results.json` schema and rendered into `report.md`, with the same field names and shapes produced by local, cloud, and legacy azd execution.
4. **Given** `execution: azd` with an explicit `eval_recipe` pointing at a current-surface recipe outside the default discovery locations, **When** `agentops eval run` is executed, **Then** that recipe is used instead of auto-discovery.
5. **Given** a workspace where the azd tooling or the evaluation extension required by the discovered recipe is unavailable, **When** `agentops eval run` is executed, **Then** the run fails with a configuration error that names the missing dependency and the action required, and AgentOps does not silently fall back to another execution engine.

---

### User Story 2 - Gate the release on thresholds and rubric dimensions from a current-surface run (Priority: P2)

A release engineer has thresholds in `agentops.yaml` that reference both built-in evaluator metrics and named rubric dimensions declared in their current-surface recipe. They run `agentops eval run` in CI and expect the gate to behave exactly as it does for every other execution mode: thresholds bind to the emitted metrics, missing metrics fail the run rather than silently passing, baseline comparison still works, and the process exit code communicates pass, gate failure, or error.

**Why this priority**: Delivering normalized artifacts without a trustworthy gate would produce false-green releases, which directly violates the project's release-evidence guarantees. This story is what turns a working integration into release-readiness evidence, but it depends on Story 1 producing a run at all.

**Independent Test**: Can be fully tested by running a current-surface evaluation with a threshold that binds to an emitted metric (expect pass), a threshold whose metric is absent from the run (expect gate failure, not a pass), and a `--baseline` comparison against a previous run, then asserting the process exit code for each case.

**Acceptance Scenarios**:

1. **Given** thresholds referencing metrics that the current-surface run emits, **When** all thresholds are satisfied, **Then** the run reports a pass and exits with code `0`.
2. **Given** thresholds referencing metrics that the current-surface run emits, **When** at least one threshold is not satisfied, **Then** the run reports a gate failure and exits with code `2`.
3. **Given** a threshold whose metric is not present in the completed run's emitted metrics, **When** the run finishes, **Then** the threshold is treated as failed and the run does not report a pass, and the report names the unbound threshold.
4. **Given** a threshold key that could bind to more than one emitted metric name, **When** the run finishes, **Then** the ambiguity is surfaced as an explicit failure rather than resolved by guessing.
5. **Given** thresholds that reference rubric dimensions declared in the current-surface recipe, **When** the run emits per-dimension scores, **Then** those dimension scores bind to the corresponding thresholds and are gated identically to built-in evaluator metrics.
6. **Given** a completed current-surface run and a `--baseline` pointing at a previous run's results, **When** `agentops eval run` is executed, **Then** the baseline comparison operates on the normalized result shape identically to any other execution mode.
7. **Given** the azd evaluation surface reports an execution error for the run as a whole, **When** the run terminates, **Then** AgentOps reports a runtime error with exit code `1`, distinct from a threshold gate failure.

---

### User Story 3 - Keep existing legacy azd recipes working unchanged (Priority: P3)

A team that already integrated AgentOps with the legacy `azd ai agent eval` surface and a root-level `eval.yaml` recipe upgrades AgentOps. Their existing configuration, commands, CI workflows, and results continue to work with no configuration changes.

**Why this priority**: Backward compatibility is a hard project constraint on public contracts, and breaking existing adopters would be a regression regardless of how well the new surface works. It is prioritized after the gate because it protects existing value rather than delivering new value.

**Independent Test**: Can be fully tested by running the existing legacy-recipe test suite and a legacy end-to-end scenario against the updated build, with no changes to `agentops.yaml`, and confirming identical discovery, execution, normalization, and exit-code behavior.

**Acceptance Scenarios**:

1. **Given** an unchanged workspace containing only a legacy recipe at the workspace root or under `src/<agent>/` and `execution: azd`, **When** `agentops eval run` is executed, **Then** the legacy recipe is discovered and executed through the legacy azd evaluation surface exactly as before.
2. **Given** an unchanged `agentops.yaml` from a previous AgentOps version, **When** it is loaded, **Then** it remains valid and requires no new or renamed configuration fields to keep working.
3. **Given** a legacy-surface run, **When** artifacts are written, **Then** the `results.json` and `report.md` contract is unchanged from the previous behavior.

---

### User Story 4 - Diagnose azd-backed evaluation setup and failures (Priority: P4)

A release engineer preparing an azd-backed evaluation runs `agentops eval analyze` to understand which recipe will be used and which surface it belongs to before spending time on a cloud run. When a run does fail, they need the raw azd output retained alongside the normalized artifacts so they can reproduce and troubleshoot the failure directly against azd.

**Why this priority**: This story reduces time-to-diagnosis and onboarding friction but is not required for a correct evaluation or a correct gate. It is valuable polish layered on the three stories above.

**Independent Test**: Can be fully tested by running `agentops eval analyze` in workspaces containing a current-surface recipe, a legacy recipe, and no recipe, confirming each case reports the resolved recipe and surface or an actionable gap, then forcing a run failure and confirming the raw azd output is retained in the run's artifact directory.

**Acceptance Scenarios**:

1. **Given** a workspace with a discoverable recipe and `execution: azd`, **When** `agentops eval analyze` is executed, **Then** the report identifies the resolved recipe path and which azd evaluation surface it will use.
2. **Given** a workspace with `execution: azd` and no discoverable recipe, **When** `agentops eval analyze` is executed, **Then** the report identifies the missing recipe as a gap and states the action required to resolve it.
3. **Given** any azd-backed run, whether it succeeds or fails, **When** the run terminates, **Then** the raw azd command output is retained in the run's artifact directory for diagnostics and audit.
4. **Given** a user setting up an azd-backed evaluation for the first time, **When** they consult the published documentation, **Then** the documentation states the minimum azd version and the required evaluation extension for each supported surface.

---

### Edge Cases

- What happens when the workspace contains both a legacy recipe and a current-surface recipe and no explicit `eval_recipe` is set? The current-surface recipe MUST win, and the run MUST report which recipe was selected and that another discoverable recipe was skipped, so the choice is never silent.
- What happens when the workspace contains more than one recipe of the *same* surface and no explicit `eval_recipe` is set? The run MUST be rejected as ambiguous, since there is no basis to prefer one over the other.
- What happens when a discovered recipe file exists but its schema matches neither the legacy nor the current shape? The run MUST fail with a configuration error naming the recipe path and the reason, rather than attempting a best-effort run against an unknown schema.
- What happens when the evaluation is created and started successfully but the run never reaches a terminal state within the allowed time? The run MUST terminate with a runtime error that preserves the created evaluation and run identifiers so the operator can inspect the run in Foundry, rather than reporting a pass or a gate failure.
- What happens when the run completes but returns zero samples? The run MUST NOT report a pass; a zero-sample completion is treated as a failure to produce evidence.
- What happens when the run completes but individual samples failed? Failed samples MUST be represented in the normalized results and MUST NOT be silently dropped from sample counts or averaged away as if they had not been attempted.
- What happens when the run completes but emits no readable metrics at all? The run MUST fail with a runtime error rather than producing an empty-but-passing result.
- What happens when a rubric dimension is declared in the recipe but the completed run emits no score for it? Any threshold bound to that dimension MUST fail closed; an undeclared-but-emitted metric MUST NOT be treated as an error.
- How does the system behave when the required extension for the current surface is installed but the discovered recipe is a legacy recipe (or vice versa)? The run MUST fail with an error naming the specific missing extension for the surface the recipe requires.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST discover current-surface evaluation recipes at the location prescribed by current Microsoft Foundry guidance (`evals/azure.eval.yaml`) in addition to the existing legacy recipe locations, without requiring new configuration.
- **FR-002**: The system MUST determine which azd evaluation surface a discovered recipe belongs to by inspecting the recipe itself, and MUST NOT require the user to declare the surface in `agentops.yaml`.
- **FR-003**: The system MUST parse both the legacy recipe schema and the current recipe schema, extracting at minimum the evaluation identity, the agent under evaluation, the dataset reference, the declared evaluators, and any declared rubric dimensions.
- **FR-004**: The system MUST support an explicit `eval_recipe` path that overrides auto-discovery for both recipe schemas.
- **FR-004a**: When auto-discovery finds recipes from both surfaces and no explicit `eval_recipe` is set, the system MUST select the current-surface recipe and MUST report both the selected recipe and the skipped recipe to the user.
- **FR-004b**: When auto-discovery finds more than one recipe belonging to the same surface and no explicit `eval_recipe` is set, the system MUST reject the run with an error that lists the candidates and directs the user to set `eval_recipe`.
- **FR-005**: For a current-surface recipe, the system MUST create the evaluation, start the run, and retrieve the run's per-sample output through the current azd evaluation commands, executing the evaluation exactly once per `agentops eval run` invocation.
- **FR-006**: The system MUST normalize run-level metrics, rubric dimension scores, per-sample outcomes, per-sample failures, and execution errors from the current surface into the existing `results.json` schema, producing the same row and metric shapes as every other execution mode.
- **FR-007**: The system MUST bind configured thresholds to the metrics and rubric dimensions emitted by a current-surface run using the same binding rules already applied to legacy azd runs, resolving exact metric names before any alias.
- **FR-008**: The system MUST treat a threshold that cannot be bound to exactly one emitted metric as a failed gate, and MUST NOT report the run as passing when any threshold is unbound or ambiguous.
- **FR-009**: The system MUST preserve the existing exit-code contract for azd-backed runs: `0` when execution succeeded and all gates passed, `2` when execution succeeded and at least one gate failed, and `1` for runtime or configuration errors.
- **FR-010**: The system MUST support `--baseline` comparison for current-surface runs using the same normalized result shape and comparison behavior applied to all other execution modes.
- **FR-011**: The system MUST render `report.md` for current-surface runs from the normalized results, including bound thresholds, unbound thresholds, rubric dimension outcomes, and failed samples.
- **FR-012**: The system MUST retain the raw azd command output for every azd-backed run in that run's artifact directory, for both successful and failed runs.
- **FR-013**: The system MUST continue to discover, execute, and normalize legacy recipes through the legacy azd evaluation surface with unchanged behavior, and MUST NOT require any configuration change from existing adopters.
- **FR-014**: The system MUST fail with an actionable configuration error, naming the missing dependency and the required action, when the azd tooling or the evaluation extension required by the discovered recipe is unavailable, and MUST NOT switch execution engines implicitly.
- **FR-015**: `agentops eval analyze` MUST report, for an azd-backed workspace, the resolved recipe path and the azd evaluation surface it belongs to, or an actionable gap when no recipe can be resolved.
- **FR-016**: Workspace initialization MUST continue to generate a recipe for a surface that is actually installable in the user's environment. It MUST default to the legacy surface while the current-surface extension is unavailable, MUST prefer the current surface once that extension is detected as installed, and MUST NOT produce a workspace whose first evaluation run fails for lack of an uninstallable dependency.
- **FR-017**: Published documentation MUST state the minimum azd version and the required evaluation extension for each supported azd evaluation surface, and the release changelog MUST record the added support.
- **FR-018**: The system MUST NOT report a passing run when the completed evaluation produced zero samples or no readable metrics.

### Key Entities

- **Evaluation Recipe**: A workspace file describing an azd-backed evaluation. Two schemas are supported: the legacy schema discovered at the workspace root or under `src/<agent>/`, and the current schema discovered under the `evals/` directory. Carries the evaluation identity, agent, dataset reference, evaluators, and rubric dimensions.
- **Evaluation Surface**: The azd command family and extension a recipe requires. Each discovered recipe resolves to exactly one surface, which determines how the evaluation is created, started, and read back.
- **Evaluation Run Reference**: The identifiers returned when an evaluation is created and started, used to retrieve output and to point an operator at the run in Foundry when something goes wrong.
- **Rubric Dimension**: A named scoring dimension declared in a recipe and emitted per sample and in aggregate by the evaluation run. Bindable to a threshold exactly like a built-in evaluator metric.
- **Failed Sample**: A dataset sample that the evaluation attempted but could not score or complete. Represented in the normalized results rather than dropped.
- **Raw azd Result**: The unmodified azd command output retained alongside the normalized artifacts for troubleshooting and audit.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A Foundry hosted agent with a current-surface recipe can be evaluated end to end through `agentops eval run` and produces a normalized result set and a human-readable report, with no manual post-processing.
- **SC-002**: A single `agentops eval run` invocation against a current-surface recipe causes the cloud evaluation to execute exactly once; retrieving results never re-executes the evaluation.
- **SC-003**: The row and metric structure of a current-surface run is identical in shape and field names to a legacy azd run, a local run, and a cloud run, so downstream reporting, comparison, evidence, and readiness tooling require no branching on which surface produced the run.
- **SC-004**: 100% of configured thresholds that cannot be bound to exactly one emitted metric result in a non-passing run; no configuration of thresholds and emitted metrics produces a false pass.
- **SC-005**: Every existing legacy-recipe scenario and its automated coverage continues to pass unchanged against the updated build, with no edits to existing configuration files.
- **SC-010**: A user who initializes a new azd-backed workspace and immediately runs an evaluation never fails because of an uninstallable dependency; initialization only targets a surface whose extension is installable in that environment.
- **SC-006**: For every azd-backed run that fails, an operator can locate the raw azd output and the run identifiers in the run's artifact directory without re-running the evaluation.
- **SC-007**: `agentops eval analyze` correctly identifies the resolved recipe and surface, or the specific gap, in each of these workspace states: current-surface recipe only, legacy recipe only, both surfaces present, and no recipe.
- **SC-009**: Recipe resolution is deterministic: the same workspace always resolves to the same recipe, and whenever a discoverable recipe is skipped the user is told which one was chosen and which was skipped.
- **SC-008**: A new user following the published documentation can determine the required azd version and extension for their chosen surface without reading source code or issue history.

## Assumptions

- The Azure Developer CLI and the evaluation extension required by the user's chosen surface are installed and authenticated by the operator. Installing, upgrading, or authenticating azd and its extensions remains out of scope for AgentOps, which only detects their absence and reports it.
- `execution: azd` remains the single configuration value that selects azd-backed execution for both surfaces; no new execution mode value and no new required configuration field are introduced, in line with the existing configuration contract.
- The current surface's supported agent targets remain the same Foundry prompt and Foundry hosted agent targets already accepted for `execution: azd`; targets rejected today remain rejected.
- Foundry remains the system of record for the cloud evaluation run itself. AgentOps reads and normalizes the run's output and does not reimplement evaluation, scoring, or run management.
- The existing `results.json` and `report.md` contracts are sufficient to represent current-surface output; any schema evolution required to represent rubric dimensions or failed samples is additive and backward compatible.
- Threshold binding for the current surface reuses the existing narrow alias rules rather than introducing broader fuzzy matching, so that gate behavior stays predictable and cannot create false-green results.
- The dataset referenced by a current-surface recipe is resolved and supplied by the recipe and the azd evaluation surface; AgentOps does not need to re-upload or re-shape it.
- Both azd evaluation surfaces are expected to coexist for some period, so surface support is additive rather than a migration that removes the legacy path.
- The current surface is strictly opt-in. It activates only when a current-surface recipe is present, so a workspace that does not have one is unaffected by this feature in every respect, including error messages.
- When both surfaces are present in one workspace, the current surface is assumed to represent the team's intended direction, so it is preferred over the legacy recipe. The selection is always reported, so a team that wants the legacy recipe can pin it with an explicit `eval_recipe`.

## Out of Scope

- Installing, upgrading, configuring, or authenticating the Azure Developer CLI or any of its extensions.
- Authoring or generating the content of a current-surface recipe beyond the initialization guidance required by FR-016; recipe authoring belongs to the azd tooling and Foundry guidance.
- Removing, deprecating, or migrating away from the legacy azd evaluation surface.
- Changing the local or cloud execution modes, the threshold expression language, the exit-code contract, or the `results.json` and `report.md` schemas beyond additive changes needed to represent current-surface output.
- Doctor readiness checks, release evidence composition, Cockpit presentation, and CI/CD workflow generation, which consume these results but are specified separately.
- Any change to how Foundry executes, scores, or stores the evaluation run.
