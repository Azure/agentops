# Tasks: Current azd AI Evaluation Surface Support

**Input**: Design documents from `/specs/011-azd-ai-eval-surface/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/azd-eval-surface.md](contracts/azd-eval-surface.md), [quickstart.md](quickstart.md)

**Tests**: Test tasks are **required**, not optional. Constitution Principle V
mandates focused automated coverage for every behavior change, and Principle III
requires the azd boundary to be mocked so the suite runs without azd, Azure
credentials, or network access. Test tasks are written before their
implementation task in each story.

**Organization**: Tasks are grouped by user story so each story can be
implemented, tested, and merged independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- Exact file paths are included in every task

## Path Conventions

Single Python project at repository root: `src/agentops/`, `tests/unit/`,
`tests/integration/`, `docs/`. Paths below follow the structure recorded in
plan.md.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish a clean baseline and the shared test double that every
later phase depends on.

- [X] T001 Run `python -m pytest tests/ -x -q` and record the green baseline, so any later failure is attributable to this feature rather than pre-existing state
- [X] T002 [P] Add a reusable azd subprocess test double in `tests/fixtures/azd_stub.py` that lets a test script an ordered sequence of `(command, returncode, stdout, stderr)` responses, assert the exact argv of each invocation, and assert that no unexpected command was run

**Checkpoint**: Baseline green and the azd boundary is fakeable.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The recipe layer. Every user story depends on being able to
discover, classify, and parse a current-surface recipe.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

**Note on parallelism**: T003–T006 all edit `src/agentops/core/azd_eval.py`, so
they are strictly sequential despite being separable concerns.

- [X] T003 Add the `EvalSurface` enum (`legacy`, `current`) and a content-based `classify_recipe_document()` in `src/agentops/core/azd_eval.py`, applying the rules from data-model.md: root `evals` as a sequence means current; a mapping-valued `agent` or a `dataset_reference` key with no `evals` sequence means legacy; anything else raises `AzdEvalRecipeError` naming the path and stating that the document matches neither supported schema
- [X] T004 Add the current-surface recipe models to `src/agentops/core/azd_eval.py` — `CurrentEvalRecipe`, `CurrentDatasetDecl`, `CurrentEvaluatorDecl`, `CurrentEval`, `CurrentSourceDecl`, `CurrentEvaluatorRef`, `CurrentTarget` — as Pydantic v2 models with `extra="allow"` so unknown preview fields are preserved, coercing every `version` field to string exactly as the existing legacy models do, and reading the dataset path from `file` (never `source`)
- [X] T005 Add `current_recipe_metric_names()` to `src/agentops/core/azd_eval.py` returning every metric name a current recipe can produce: each evaluator reference's `name` when set otherwise its `evaluator`, plus each rubric dimension's metric name resolved from local `CurrentEvaluatorDecl` entries via inline `definition.dimensions` or the referenced `source` file, reusing the existing dimension metric-name rule
- [X] T006 Add `RecipeResolution` and extend discovery in `src/agentops/core/azd_eval.py` to search `evals/azure.eval.yaml` alongside the existing legacy locations, implementing the precedence from contracts: explicit `eval_recipe` wins outright; a cross-surface tie selects the current surface and records the skipped paths; two candidates within one surface raise the existing ambiguity error listing candidates; no candidate raises a configuration error naming both supported locations
- [X] T007 Add unit tests to `tests/unit/test_azd_eval.py` covering the seven workspace states in quickstart T1.2, plus: a current recipe using `datasets[].file` parses, an unknown top-level key is preserved rather than rejected, and declared metric names include builtin references, evaluator labels, and rubric dimension ids
- [X] T008 Add a shared extension availability probe to `src/agentops/pipeline/azd_runner.py` that parses `azd extension list --installed -o json` and matches on extension `id`, falling back to the existing text scan only when the structured form is unavailable, and accepts the extension id as a parameter so both surfaces reuse it
- [X] T009 Add unit tests to `tests/unit/test_azd_runner.py` for the probe: a structured listing containing the id means available; a listing omitting it means unavailable; and a human-readable listing that contains the id with a not-installed status means **unavailable** — the false-positive guard from quickstart T1.4. Existing legacy tests in this file must pass unmodified

**Checkpoint**: A recipe can be found, classified, parsed, and reduced to its
declared metric names, and extension presence can be determined reliably.

---

## Phase 3: User Story 1 - Evaluate through the current azd surface (Priority: P1) 🎯 MVP

**Goal**: `agentops eval run` against `evals/azure.eval.yaml` delegates the
evaluation to the current azd surface, executes it exactly once, and produces
normalized `results.json` and `report.md`.

**Independent Test**: With the azd boundary faked, run `agentops eval run` in a
workspace holding a current-surface recipe and confirm the exact command
sequence, a single evaluation execution, and a normalized result set with
computed aggregate metrics and one row per sample.

### Tests for User Story 1

> Write these first and confirm they fail before implementing.

- [X] T010 [US1] Write failing tests in `tests/unit/test_azd_eval_runner.py` for the command sequence and flag correctness per contracts: probe, `create`, `run start --no-wait`, poll, final `run show`, `run output list --all --output-file`; assert `--path` receives the recipe's parent directory, `run start` receives no positional argument, `run output list` receives the run id positionally, the current surface uses `--output-file` and never `--out-file`, and no failure-gating flag is ever passed
- [X] T011 [US1] Write failing tests in `tests/unit/test_azd_eval_runner.py` for polling: non-terminal statuses followed by `completed` resolve; each of `failed`, `error`, `canceled`, and `cancelled` is terminal and raises a runtime error; an unrecognized status keeps polling; and a timeout raises a runtime error whose message contains the evaluation id, run id, and last observed status
- [X] T012 [US1] Write failing tests in `tests/unit/test_azd_eval_runner.py` for output parsing and aggregation: a metric mean excludes samples with no score rather than treating them as zero; a metric with no scores anywhere is absent from the aggregate map; a score arriving as the string `"4"` decodes to `4.0`; a non-numeric score is recorded as absent; `passed: null` is neither a pass nor a judged failure; a sample with an empty result list is a failure; a run error object with null members is not a failure while a non-empty `error.message` is; and `report_url` wins over `portal_url`
- [X] T013 [US1] Write failing tests in `tests/unit/test_azd_eval_runner.py` asserting that raw artifacts — `azd_evaluation.json`, `azd_eval_output_items.json`, `azd_stdout.log`, `azd_stderr.log` — are written to the run output directory for a successful run and for a failed run

### Implementation for User Story 1

- [X] T014 [US1] Create `src/agentops/pipeline/azd_eval_runner.py` with the `CurrentEvalRun` dataclass, `RunCounts`, `SampleScore`, and the `CURRENT_EXTENSION_NAME` / minimum azd version constants, plus every azd command builder for this surface, so all volatile surface details live in one module
- [X] T015 [US1] Implement the delegated sequence in `src/agentops/pipeline/azd_eval_runner.py`: probe availability via the shared helper from T008 and raise an actionable error naming `azure.ai.evaluations`, its azd version floor, and the install command when absent; run `create` for idempotent reconciliation; run `run start --no-wait` and capture the evaluation and run identifiers; then poll `run show` at the existing heartbeat interval under the AgentOps timeout until a terminal status
- [X] T016 [US1] Implement result retrieval in `src/agentops/pipeline/azd_eval_runner.py`: a final `run show` for the run object and `run output list <run-id> --all --output-file <tmp>` for the per-sample items, reading the items from the written temporary file rather than stdout, and cleaning the temporary file up afterwards
- [X] T017 [US1] Implement per-sample parsing and aggregation in `src/agentops/pipeline/azd_eval_runner.py` per data-model.md: build `SampleScore` entries keyed by `metric` falling back to `name`, decode scores tolerantly, preserve three-valued verdicts, and compute each aggregate metric as the mean of its non-absent scores
- [X] T018 [US1] Implement `normalize_to_results()` in `src/agentops/pipeline/azd_eval_runner.py` producing a `RunResult` with one `RowResult` per sample (best-effort input/expected/response extraction that never affects the gate), `aggregate_metrics` from T017, counts from `RunCounts` cross-checked against retrieved rows, `result_granularity` of `row`, and the extended `config.azd_evaluation` provenance block including `surface`, `extension`, `skipped_recipes`, `result_counts`, `missing_metrics`, `retrieval_warnings`, and `error_message`
- [X] T019 [US1] Implement raw artifact writing in `src/agentops/pipeline/azd_eval_runner.py` for both success and failure paths, matching the existing legacy artifact naming and writing into the run's output directory
- [X] T020 [US1] Update the azd branch in `src/agentops/pipeline/orchestrator.py` to resolve the recipe through `RecipeResolution`, dispatch to `azd_eval_runner` for the current surface and the untouched `azd_runner` for the legacy surface, and emit progress naming the resolved recipe, the selected surface, and any skipped recipe
- [X] T021 [US1] Add an end-to-end test in `tests/integration/test_cli_flat_schema.py` that runs the CLI against a faked azd with a current-surface recipe and asserts exit code `0`, a written `results.json` with populated rows and computed aggregate metrics, a written `report.md`, and exactly one evaluation execution

**Checkpoint**: A current-surface evaluation runs end to end and produces
normalized artifacts. This is the MVP.

---

## Phase 4: User Story 2 - Gate the release on thresholds and rubric dimensions (Priority: P2)

**Goal**: Thresholds bind to emitted metrics and rubric dimensions, unbound and
missing metrics fail closed, baseline comparison works, and exit codes follow the
project contract.

**Independent Test**: Run the same faked evaluation with a satisfied threshold,
an unsatisfied threshold, a threshold naming an undeclared metric, and a
threshold naming a declared-but-unemitted metric, asserting the exit code for
each.

### Tests for User Story 2

- [X] T022 [US2] Write failing tests in `tests/unit/test_azd_eval_runner.py` for pre-flight binding: a threshold matching no declared metric and a threshold matching more than one declared metric each raise a configuration error, and in both cases the faked azd records **zero invocations** — the observable proof that a typo never bills a cloud run
- [X] T023 [US2] Write failing tests in `tests/unit/test_azd_eval_runner.py` for post-run behavior: a threshold bound to a declared metric that the run did not emit is recorded as a failed `ThresholdEvaluation` and appears in `config.azd_evaluation.missing_metrics`, the run does not report a pass, and a run with zero samples or no decodable metrics never reports a pass
- [X] T024 [US2] Write failing tests in `tests/unit/test_azd_eval_runner.py` asserting that thresholds naming rubric dimension ids bind to the corresponding per-dimension scores and are gated identically to builtin evaluator metrics

### Implementation for User Story 2

- [X] T025 [US2] Implement pre-flight threshold binding in `src/agentops/pipeline/azd_eval_runner.py`, binding configured threshold keys against the declared metric names from T005 using the existing `bind_threshold_metrics` helper, raising a configuration error before any azd command runs when a key is unmatched or ambiguous, and listing the candidates for the ambiguous case
- [X] T026 [US2] Implement post-run gate handling in `src/agentops/pipeline/azd_eval_runner.py`: a declared metric absent from `aggregate_metrics` produces a failed `ThresholdEvaluation` rather than an exception, `overall_passed` requires both a `completed` status and a full threshold pass rate, and a zero-sample or zero-metric run cannot pass
- [X] T027 [US2] Add integration coverage in `tests/integration/test_cli_flat_schema.py` for the full exit-code matrix from quickstart T1.5 — `0` for a satisfied gate, `2` for an unsatisfied threshold, `2` for a declared-but-unemitted metric, `1` for each pre-flight binding failure with zero azd invocations, `1` for each non-`completed` terminal status, `1` for a poll timeout, and `1` when azd or the extension is missing
- [X] T028 [US2] Add a test in `tests/integration/test_cli_flat_schema.py` confirming `--baseline` comparison against a current-surface run behaves identically to any other execution mode

**Checkpoint**: The release gate is trustworthy and fails closed in every
identified failure mode.

---

## Phase 5: User Story 3 - Keep existing legacy azd recipes working unchanged (Priority: P3)

**Goal**: Existing adopters upgrade with no configuration changes and no
behavioral difference, and nobody is opted in to the new surface by accident.

**Independent Test**: Run the pre-existing legacy test suite unmodified against
the updated build, and run the CLI in a legacy workspace confirming identical
commands, normalization, and exit code.

- [X] T029 [US3] Verify by inspection and by test that `src/agentops/pipeline/azd_runner.py` retains its original discovery, command sequence, post-run binding semantics, and `result_granularity` of `aggregate`, with the only changes being the extracted shared availability probe from T008 and any helper reuse that leaves observable behavior identical
- [X] T030 [US3] Confirm every pre-existing test in `tests/unit/test_azd_runner.py`, `tests/unit/test_azd_eval.py`, and `tests/unit/test_azd_eval_init.py` passes **without modification**; a test that required editing is a finding to resolve in the implementation, not by changing the test
- [X] T031 [US3] Add a regression guard test in `tests/integration/test_cli_flat_schema.py` proving the current surface is strictly opt-in: in a workspace with a legacy `eval.yaml` and no `evals/` directory, `agentops eval run` and `agentops eval analyze` produce output identical to the previous release, with no mention of the current surface and no new warning or error
- [X] T032 [US3] Add a test in `tests/integration/test_cli_flat_schema.py` confirming that a legacy workspace dispatches to `azd ai agent eval` commands and never to `azd ai eval` commands

**Checkpoint**: Existing adopters are provably unaffected.

---

## Phase 6: User Story 4 - Diagnose azd-backed setup and failures (Priority: P4)

**Goal**: `agentops eval analyze` reports the resolved recipe and its surface,
initialization only targets an installable surface, and raw azd output is
available for troubleshooting.

**Independent Test**: Run `agentops eval analyze` in workspaces with a
current-surface recipe, a legacy recipe, both, and none, and confirm the resolved
recipe and surface or the specific gap in each case.

- [X] T033 [US4] Write failing tests in `tests/unit/test_eval_analysis.py` for the four workspace states, asserting that the report names the resolved recipe path and surface, or reports the gap and the required action when no recipe resolves, and reports the selection and the skipped path when both surfaces are present
- [X] T034 [US4] Implement an azd recipe readiness signal in `src/agentops/services/eval_analysis.py` that reuses `RecipeResolution` from T006, replacing the current weak `azure.yaml`-presence signal with the resolved recipe path and surface, and reporting resolution errors as actionable gaps rather than raising
- [X] T035 [US4] Write failing tests in `tests/unit/test_azd_eval_init.py` asserting that initialization probes for the current-surface extension and selects the legacy surface when it is absent, selects the current surface when it is present, and never generates `evals/azure.eval.yaml` in an environment where the required extension cannot be installed
- [X] T036 [US4] Implement installable-surface selection in `src/agentops/services/azd_eval_init.py` per plan design decision 13 and spec FR-016, keeping the existing legacy generation path as the default while the current-surface extension is unavailable so a freshly initialized workspace is always immediately runnable
- [X] T037 [US4] Add a test in `tests/unit/test_azd_eval_runner.py` asserting that a run failing at the poll, retrieval, or normalization stage still writes every raw artifact, so a failed run is always diagnosable without re-running the evaluation

**Checkpoint**: Setup and failure diagnosis are self-service.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: User-visible documentation and final validation.

- [X] T038 [P] Document both surfaces in `docs/evaluation.md`: extension ids, azd version floors, discovery locations, the cross-surface precedence rule, the threshold binding table, and a plain statement that `azure.ai.evaluations` is preview and not yet in the default azd extension registry
- [X] T039 [P] Update the azd execution path description in `docs/how-it-works.md` to describe two surfaces, the delegated command sequence, and the fact that AgentOps computes aggregate metrics from per-sample scores for the current surface
- [X] T040 [P] Add a current-surface walkthrough to `docs/tutorial-hosted-agent.md` using an `evals/azure.eval.yaml` recipe, including the local-source install path for the preview extension
- [X] T041 [P] Add a `CHANGELOG.md` entry under the unreleased section recording current-surface support, the preserved legacy path, and the preview status of the required extension
- [X] T042 [P] Update comment guidance in `src/agentops/templates/agentops.yaml` so `execution: azd` mentions both recipe locations and the precedence rule
- [X] T043 Run `python -m pytest tests/ -x -q` and confirm the full suite passes with no pre-existing test modified
- [X] T044 Walk the Tier 1 scenarios in [quickstart.md](quickstart.md) (T1.1 through T1.8) and confirm each expectation, in particular the opt-in regression guard and the wording of the unavailable-extension message

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup — **blocks every user story**
- **US1 (Phase 3)**: Depends on Foundational
- **US2 (Phase 4)**: Depends on US1, because gating operates on the normalized result US1 produces
- **US3 (Phase 5)**: Depends on Foundational only; can run in parallel with US1 and US2
- **US4 (Phase 6)**: Depends on Foundational for T033–T036; T037 depends on US1
- **Polish (Phase 7)**: Depends on all desired stories

### User Story Dependencies

- **US1 (P1)**: Independent once Foundational completes. This is the MVP.
- **US2 (P2)**: The one genuine cross-story dependency — it gates the result US1 normalizes. Sequential after US1.
- **US3 (P3)**: Fully independent. It is verification and guard work over code the other stories must not disturb, so it can start as soon as Foundational lands and acts as a continuous safety net.
- **US4 (P4)**: Largely independent. Analyze and init work touch different service modules from the adapter.

### Within Each User Story

- Tests are written before their implementation task and must fail first
- Core models before services before adapters before orchestration
- Every task in a story completes before that story's checkpoint is claimed

### Parallel Opportunities

Genuine parallelism is limited by file ownership rather than by logic:

- T003–T006 all edit `core/azd_eval.py` and are strictly sequential
- T010–T013 and T022–T024 all edit `tests/unit/test_azd_eval_runner.py` and are strictly sequential
- T014–T019 all edit `pipeline/azd_eval_runner.py` and are strictly sequential
- Across stories: US3 (T029–T032) and US4 (T033–T036) touch different modules from US1/US2 and can proceed concurrently
- All of Phase 7 except T043 and T044 is parallelizable

## Parallel Example: after Foundational completes

```bash
# Three developers, no file conflicts:
Developer A: US1 (Phase 3) - src/agentops/pipeline/azd_eval_runner.py
Developer B: US3 (Phase 5) - regression guards in tests/
Developer C: US4 T033-T036 - src/agentops/services/{eval_analysis,azd_eval_init}.py
```

```bash
# Phase 7 documentation, all parallel:
Task: "Document both surfaces in docs/evaluation.md"
Task: "Update azd execution path in docs/how-it-works.md"
Task: "Add current-surface walkthrough to docs/tutorial-hosted-agent.md"
Task: "Add CHANGELOG.md entry"
Task: "Update templates/agentops.yaml comments"
```

---

## Implementation Strategy

### MVP scope

**Phases 1, 2, and 3 (T001–T021).** That delivers a Foundry hosted agent
evaluated end to end through the current azd surface, producing normalized
`results.json` and `report.md`. It satisfies the primary acceptance criterion of
issue #484 and unblocks the VBD Evaluate lab.

Thresholds still function in the MVP through the existing evaluation path; US2
adds the fail-closed guarantees and the pre-flight check that make the gate
trustworthy for CI.

### Recommended delivery order

1. **Phases 1–2** — foundation; nothing user-visible yet
2. **Phase 3 (US1)** — MVP; stop and validate against quickstart Tier 1
3. **Phase 5 (US3)** — land the regression guards early so every later change is protected; cheap and independent
4. **Phase 4 (US2)** — make the gate trustworthy; required before recommending this path in CI
5. **Phase 6 (US4)** — diagnosis and safe initialization
6. **Phase 7** — documentation and final validation

Landing US3 before US2 is deliberate: the guards are inexpensive, and having them
in place makes every subsequent change provably non-breaking for existing
adopters.

### Do not ship without

- **T031** — the opt-in regression guard. Without it there is no proof that
  existing users and fresh clones are unaffected.
- **T036** — installable-surface selection at init. Without it a freshly
  initialized workspace could fail on a dependency the user cannot install.
- **T027** — the exit-code matrix. Exit codes are a public contract.

---

## Notes

- `[P]` marks tasks in different files with no incomplete dependencies
- Every azd command construction and every external JSON field name belongs in
  `src/agentops/pipeline/azd_eval_runner.py`, so an upstream surface change stays
  a localized edit
- No test may reach the network, require azd, or require Azure credentials
- A pre-existing test that needs editing is a finding, not a fix
- Commit after each task or logical group; stop at any checkpoint to validate
