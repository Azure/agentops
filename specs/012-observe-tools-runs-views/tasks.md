# Tasks: Observe tools and runs views

**Input**: Design documents from `/specs/012-observe-tools-runs-views/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/observe-api.additions.openapi.yaml](./contracts/observe-api.additions.openapi.yaml), [quickstart.md](./quickstart.md)

**Tests**: **Required, not optional.** Constitution Principle V ("Verify Every Behavior Change") mandates focused automated coverage for every behavior or contract change, with Azure SDK interactions mocked. Test tasks are therefore first-class in every phase.

**Organization**: Tasks are grouped by user story so each story can be implemented, tested, and shipped independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US4)
- Every task carries an exact file path

## Path Conventions

Single project, `src/` layout per [plan.md](./plan.md):

- Contracts: `src/agentops/core/observe.py` (pure — no Azure SDK, no I/O)
- Observe internals: `src/agentops/agent/observe/{service,queries,adapters,facade,ui}.py`
- Tests: `tests/unit/`, `tests/integration/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the baseline and confirm the blast radius of the breaking change before touching contracts

- [X] T001 Run `python -m pytest tests/ -q` and record the current pass/fail baseline in the PR description, explicitly noting `test_observe_views_and_labels_cover_all_required_surfaces` and `test_observe_view_wire_names_map_internal_ids_to_openapi_view_enum` in `tests/unit/test_observe_ui.py` as the two known tripwires that must go red then green
- [X] T002 [P] Grep the repository for every consumer of the current coarse runtime values (`"foundry"`, `"external"` as `source_kind`) across `src/agentops/` and `tests/`, and list each hit in the PR description so no consumer is missed when the values are replaced in Phase 6

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared contracts, runtime classification, and the bounded-query shape that all four stories build on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

**Note on scope**: This phase adds `RuntimeKind` as a shared type and applies it to *new* entities only. Retargeting the published `ObservedAgent.source_kind` field — the actual breaking change — is deliberately deferred to Phase 6 (US4) so the breakage lands in exactly one reviewable increment.

- [X] T003 Add the `RuntimeKind` type alias and the `ResultBounds` model to `src/agentops/core/observe.py` per [data-model.md §1 and §4](./data-model.md), including the `rows_total_in_scope >= rows_shown` validator and the `truncated` implies `rows_shown == MAX_ROWS_PER_QUERY` rule
- [X] T004 Add the optional `tool_name` and `run_key` fields to `ObserveFilterState` in `src/agentops/core/observe.py` with trim, reject-empty-after-trim, and max-length validators per [data-model.md §5](./data-model.md); leave `validate_scope()` unchanged
- [X] T005 Extend the `CoverageResult.dimension` literal in `src/agentops/core/observe.py` with `tool_attribution` and `run_correlation` per [data-model.md §7](./data-model.md)
- [X] T006 [P] Add unit tests covering T003–T005 in `tests/unit/test_observe_models.py`: `ResultBounds` validators, filter trim/empty/length rejection, `extra="forbid"` still enforced, and the two new coverage dimensions accepted
- [X] T007 Add the `bounds: ResultBounds | None` field to both `ObserveResult` and `_CachedView` in `src/agentops/agent/observe/service.py` per [data-model.md §8](./data-model.md), and confirm the cached-versus-recomputed docstring reasoning still holds
- [X] T008 Implement `classify_runtime()` in `src/agentops/agent/observe/service.py` per [research R1](./research.md), joining telemetry attributes with the control-plane inventory from `src/agentops/agent/observe/discovery.py` and returning `unknown` whenever evidence is insufficient — never a guess
- [X] T009 [P] Add a `_bounded_aggregate()` KQL helper to `src/agentops/agent/observe/queries.py` implementing the `let agg = ...; let total_in_scope = toscalar(agg | count); agg | sort | take N | extend total_in_scope` shape from [research R2](./research.md)
- [X] T010 Rewrite `build_agents_query` and `build_models_query` in `src/agentops/agent/observe/queries.py` to use `_bounded_aggregate()` instead of `| top N`, preserving one query per source so the 10-source batch bound is untouched (depends on T009)
- [X] T011 [P] Populate `bounds` from the `total_in_scope` column in `src/agentops/agent/observe/adapters.py`, yielding `rows_total_in_scope=None` — never a value equal to `rows_shown` — when a source omits the column
- [X] T012 Add `tool_name` and `run_key` to `OBSERVE_FILTER_QUERY_KEYS` in `src/agentops/agent/observe/ui.py` **and** to the `FILTER_KEYS` array inside the embedded `_OBSERVE_SCRIPT` JavaScript, so URL round-tripping works in both the server and client paths
- [X] T013 [P] Add unit tests for `bounds` propagation and `classify_runtime()` in `tests/unit/test_observe_service.py`, including the "cannot classify returns unknown" case
- [X] T014 [P] Add unit tests in `tests/unit/test_observe_queries.py` asserting the bounded-aggregate query shape emits `total_in_scope` and still respects `MAX_ROWS_PER_QUERY`

**Checkpoint**: Shared contracts, runtime classification, and truncation reporting are in place — user stories can now proceed in parallel

---

## Phase 3: User Story 1 - Find the tool that is slow, failing, or over-used (Priority: P1) 🎯 MVP

**Goal**: A Tools view reporting one normalized row per (project, agent, tool, source) with invocations, failures, p95 latency, and last activity — filterable and shareable.

**Independent Test**: Request the Tools view for a scope containing an agent that emits tool activity and confirm one normalized row per tool with invocations, failures, p95 latency, and last observed activity. Confirm the same view for an agent with no tool activity returns an explicit coverage explanation rather than rows.

### Tests for User Story 1 ⚠️

> Write these first and confirm they fail before implementing

- [X] T015 [P] [US1] Add `ObservedTool` contract tests in `tests/unit/test_observe_models.py`: required fields including a required non-empty `source_id`, `failures <= invocations`, `p95_latency_ms=None` distinct from `0.0`, and `extra="forbid"` rejecting any `input_tokens` / `output_tokens` key
- [X] T016 [P] [US1] Add `build_tools_query` tests in `tests/unit/test_observe_queries.py`: filters on presence of `gen_ai.tool.name`, reads `union AppDependencies, AppRequests`, never touches `AppGenAIContent`, escapes `tool_name` via `_kql_escape`, and orders by invocation count descending
- [X] T017 [P] [US1] Add tools-view normalization and dispatch tests in `tests/unit/test_observe_service.py` covering row normalization and the `_normalize_view` branch
- [X] T018 [P] [US1] Add an end-to-end tools-view scenario to `tests/integration/test_observe_end_to_end.py` with a mocked telemetry client, asserting the response envelope shape against the contract

### Implementation for User Story 1

- [X] T019 [US1] Add the `ObservedTool` model to `src/agentops/core/observe.py` per [data-model.md §2](./data-model.md), including the required `source_id` and deliberately omitting token fields
- [X] T020 [US1] Add `"tools"` to the `ObserveQueryRequest.view` literal in `src/agentops/core/observe.py`
- [X] T021 [US1] Implement `build_tools_query()` in `src/agentops/agent/observe/queries.py` per [research R4](./research.md), using `_bounded_aggregate()` and applying the `tool_name` filter
- [X] T022 [US1] Register `"tools"` in the `_VIEW_QUERY_BUILDERS` dict in `src/agentops/agent/observe/adapters.py`
- [X] T023 [US1] Add `"tools"` to the `View` literal, implement `normalize_tool_row()`, and add the `tools` branch to `_normalize_view()` in `src/agentops/agent/observe/service.py`
- [X] T024 [US1] Emit a `tool_attribution` coverage result from `src/agentops/agent/observe/service.py` when tool activity carries no usable tool name (FR-005), instead of emitting a placeholder-named row
- [X] T025 [US1] Render the Tools view in `src/agentops/agent/observe/ui.py`: add to `OBSERVE_VIEWS`, `OBSERVE_VIEW_LABELS`, and `OBSERVE_VIEW_WIRE_NAMES`; add the table renderer and its `_OBSERVE_SCRIPT` JavaScript twin; surface the originating source on every row so rows from different sources are distinguishable (FR-023); render absent latency as an explicit "not measured" indicator, never `0`
- [X] T026 [US1] Render the truncation notice ("showing N of M", or "total unknown" when `rows_total_in_scope` is `None`) in `src/agentops/agent/observe/ui.py` for the Tools view, in both the Python and JavaScript render paths

**Checkpoint**: The Tools view is fully functional, filterable, shareable, and independently testable — this is the MVP

---

## Phase 4: User Story 2 - Understand one complete run end to end (Priority: P2)

**Goal**: A Runs view reporting one row per correlated run with turn count, tool invocations, token totals, failure state, duration, completeness, and which correlation formed it.

**Independent Test**: Request the Runs view for a scope containing correlated multi-turn activity and confirm one row per run with turn count, tool invocation count, observed token totals, failure state, duration, last observed activity, and which correlation formed the run. Confirm activity that cannot be correlated into a run is reported as a coverage gap rather than as fabricated single-turn runs.

### Tests for User Story 2 ⚠️

- [X] T027 [P] [US2] Add `ObservedRun` contract tests in `tests/unit/test_observe_models.py`: a required non-empty `source_id`, `turns >= 1`, `failed_turns <= turns`, `tool_failures <= tool_invocations`, `last_activity_at >= started_at`, the succeeded-implies-no-failures validator, and `input_tokens` / `output_tokens` accepting `None` distinct from `0`
- [X] T028 [P] [US2] Add `build_runs_query` tests in `tests/unit/test_observe_queries.py`: conversation-id precedence over `OperationId`, `run_key` escaping, and most-recent-activity-first ordering
- [X] T029 [P] [US2] Add run-status tests in `tests/unit/test_observe_service.py`: a run whose last activity falls inside the 120-second settling margin reports `in_progress`; a later successful turn does not clear an earlier failure
- [X] T030 [P] [US2] Add an end-to-end runs-view scenario to `tests/integration/test_observe_end_to_end.py`, including a result set that legitimately mixes `run_key_kind` values

### Implementation for User Story 2

- [X] T031 [US2] Add the `ObservedRun` model to `src/agentops/core/observe.py` per [data-model.md §3](./data-model.md), including the required `source_id`, nullable `input_tokens` / `output_tokens`, and all four validators
- [X] T032 [US2] Add `"runs"` to the `ObserveQueryRequest.view` literal in `src/agentops/core/observe.py`
- [X] T033 [US2] Implement `build_runs_query()` in `src/agentops/agent/observe/queries.py` per [research R3](./research.md), preferring `gen_ai.conversation.id` (falling back to the Foundry thread id) and otherwise correlating on `OperationId`, emitting `run_key_kind` accordingly
- [X] T034 [US2] Register `"runs"` in the `_VIEW_QUERY_BUILDERS` dict in `src/agentops/agent/observe/adapters.py`
- [X] T035 [US2] Add `"runs"` to the `View` literal, implement `normalize_run_row()` including the settling-margin `in_progress` derivation from [research R7](./research.md), and add the `runs` branch to `_normalize_view()` in `src/agentops/agent/observe/service.py`
- [X] T036 [US2] Emit a `run_correlation` coverage result from `src/agentops/agent/observe/service.py` when activity cannot be correlated (FR-010), rather than fabricating single-turn runs
- [X] T037 [US2] Render the Runs view in `src/agentops/agent/observe/ui.py` (constants, labels, wire name, table renderer, JavaScript twin, truncation notice), surfacing `run_key_kind` and the originating source on every row, labelling start / duration / turns as scoped to the selected range per FR-012A, and rendering absent token totals as "not available", never `0`

**Checkpoint**: Tools and Runs views both work independently

---

## Phase 5: User Story 3 - Learn when tool and run data is missing, not just absent (Priority: P3)

**Goal**: Coverage distinguishes inaccessible, not configured, no data in period, attribution not reported, and partial — each with a reason and a next action.

**Independent Test**: Request telemetry coverage for a scope where tool attribution is absent and for a scope where run correlation is absent, and confirm each is reported as a distinct, explained coverage result with a recommended next action.

### Tests for User Story 3 ⚠️

- [X] T038 [P] [US3] Add coverage-state matrix tests in `tests/unit/test_observe_service.py` asserting `tool_attribution` and `run_correlation` each resolve to the correct state across all five conditions from FR-020, every non-OK result carrying a non-empty reason and next action
- [X] T039 [P] [US3] Add tests in `tests/unit/test_observe_ui.py` asserting `COVERAGE_DIMENSION_LABELS` covers both new dimensions in the Python map and its JavaScript twin

### Implementation for User Story 3

- [X] T040 [US3] Implement the full `tool_attribution` and `run_correlation` coverage evaluation in `src/agentops/agent/observe/service.py`, distinguishing source inaccessible, telemetry not configured, no data in the selected period, expected attribution not reported, and partial
- [X] T041 [US3] Attach a concise reason and a recommended next action to every unavailable or incomplete result for both new dimensions in `src/agentops/agent/observe/service.py` (FR-021)
- [X] T042 [US3] Add labels for both new dimensions to `COVERAGE_DIMENSION_LABELS` in `src/agentops/agent/observe/ui.py` and its `_OBSERVE_SCRIPT` JavaScript twin, and ensure the Tools and Runs tables render an explained empty state rather than a blank panel

**Checkpoint**: Empty Tools and Runs views now explain themselves

---

## Phase 5B: Source attribution on the existing agents view (non-breaking)

**Purpose**: Carry the same `source_id` attribution the new views gain (FR-023) onto the pre-existing agents view (FR-023A), so the release does not ship one row-bearing view that is inconsistent with the other two.

**Note on task IDs**: T056–T059 are numbered after T055 because this phase was added after the initial task generation. The IDs are intentionally non-contiguous with the surrounding phases; execution order is the phase order shown here, not the numeric order.

**Not gated by the Phase 6 approval**: this change is additive, not breaking, and does not depend on `RuntimeKind`. It can be completed as part of Phases 1–5 independently of the Phase 6 maintainer approval recorded in [plan.md](./plan.md#complexity-tracking).

**Independent Test**: Query a scope in which two distinct telemetry sources report the same agent, and confirm the agents view returns two rows that identify their originating source rather than two rows an operator cannot tell apart.

### Tests for source attribution ⚠️

- [X] T056 [P] Add a model test in `tests/unit/test_observe_models.py` asserting `ObservedAgent` requires a non-empty `source_id` and rejects an empty or missing value
- [X] T057 [P] Add a test in `tests/unit/test_observe_service.py` asserting `normalize_agent_row` populates `source_id` from the `TelemetrySource` it is given, and that two sources reporting the same agent key produce two rows distinguishable by `source_id` (FR-023A)

### Implementation for source attribution

- [X] T058 Add the required `source_id: str` field to `ObservedAgent` in `src/agentops/core/observe.py` and populate it from `source.source_id` in `normalize_agent_row` in `src/agentops/agent/observe/service.py`, updating every existing construction site (tests and fixtures) in the same change
- [X] T059 Surface the originating source per row in the agents table in `src/agentops/agent/observe/ui.py` and its `_OBSERVE_SCRIPT` JavaScript twin, alongside — not replacing — the existing `source_kind` badge

**Checkpoint**: All three row-bearing views attribute every row to a telemetry source

---

## Phase 6: User Story 4 - Tell which runtime an agent is actually running on (Priority: P4) ⚠️ BREAKING

**Goal**: Agent rows report the refined runtime, replacing the coarse published values.

**Independent Test**: Request the Agents view across a scope containing more than one runtime and confirm each agent reports its determinable runtime, and that agents whose runtime cannot be determined report unknown.

**⚠️ This phase contains the declared breaking change** to `ObservedAgent.source_kind`, justified in [plan.md Complexity Tracking](./plan.md#complexity-tracking). The old-to-new mapping is not one-to-one, so no automatic shim is possible. Ship it in a single release with no dual-emission window per [research R6](./research.md).

**Maintainer approval**: recorded and ✅ approved on 2026-08-24 in [plan.md](./plan.md#complexity-tracking). This phase is authorized to proceed.

### Tests for User Story 4 ⚠️

- [X] T043 [P] [US4] Update `tests/unit/test_observe_models.py` so `ObservedAgent.source_kind` asserts the six refined values and rejects the retired `"foundry"` and `"external"` values
- [X] T044 [P] [US4] Add classification tests in `tests/unit/test_observe_service.py` covering each refined value plus the "previously Foundry, now indeterminate, therefore unknown" case from FR-018A
- [X] T045 [P] [US4] Update `tests/unit/test_observe_ui.py` so the badge tone map covers all six values, and update the two Phase 1 tripwire tests to expect six views

### Implementation for User Story 4

- [X] T046 [US4] Retarget `ObservedAgent.source_kind` to `RuntimeKind` in `src/agentops/core/observe.py`, replacing the three coarse values
- [X] T047 [US4] Replace `agent_source_kind()` with a call to `classify_runtime()` in `src/agentops/agent/observe/service.py`, removing the id-versus-name heuristic and its stale comment
- [X] T048 [US4] Update `_render_source_kind_badge` in `src/agentops/agent/observe/ui.py` and **both** of its `_OBSERVE_SCRIPT` JavaScript twins so the tone map covers all six values, with `unknown` rendered as a neutral informational badge rather than an error
- [X] T049 [US4] Update every consumer identified in T002 that asserts or branches on the retired values, across `src/agentops/` and `tests/`

**Checkpoint**: All four user stories are independently functional

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T050 [P] Update `docs/observe.md` to document six views instead of four, rewrite the runtime-attribution section with the old-to-new mapping table from [plan.md](./plan.md#complexity-tracking), document that the agents view now identifies each row's telemetry source (FR-023A), and document the FR-012A window-scoped limitation: a run that began before the selected range reports start, duration, turns and failure state for the in-window activity only, and is not flagged as truncated at its leading edge
- [X] T051 [P] Add a breaking-change entry to `CHANGELOG.md` for the replaced `source_kind` values, including the mapping table and a statement that no dual-emission window is provided
- [X] T052 Verify the implementation against [contracts/observe-api.additions.openapi.yaml](./contracts/observe-api.additions.openapi.yaml) field by field, confirming `additionalProperties: false` holds, that no token field leaks into `ObservedTool`, and that `ObservedAgent` carries the additive `source_id` described by `ObservedAgentDelta`
- [X] T053 Confirm read-only and content-safety invariants by inspection and test: no new write path, and no query in `src/agentops/agent/observe/queries.py` reads `AppGenAIContent` or the `gen_ai.tool.message` attribute (FR-024, FR-026)
- [X] T054 Run the full suite `python -m pytest tests/ -x -q` and confirm the Phase 1 tripwire tests now pass
- [X] T055 Execute every scenario in [quickstart.md](./quickstart.md), including the KQL-injection filter check in Scenario 6 and the scope-leak check in Scenario 7

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup — **BLOCKS all user stories**
- **US1 (Phase 3)**: Depends on Phase 2 only
- **US2 (Phase 4)**: Depends on Phase 2 only — independent of US1
- **US3 (Phase 5)**: Depends on Phase 2; the coverage *dimensions* exist from T005, so US3 can be developed in parallel with US1/US2, but its empty-state rendering (T042) is best validated after at least one of the two views exists
- **Source attribution (Phase 5B)**: Depends on Phase 2 only — independent of US1, US2, US3 and US4, and explicitly **not** gated by the Phase 6 maintainer approval
- **US4 (Phase 6)**: Depends on Phase 2 (`classify_runtime` from T008) only — fully independent of US1–US3
- **Polish (Phase 7)**: Depends on all desired stories being complete

### Within Each User Story

- Tests are written first and must fail before implementation
- Contract models (`core/observe.py`) → query builders → adapter registration → service normalization → UI rendering
- Both the Python renderer and its embedded JavaScript twin must change together; changing only one is the most likely defect in this codebase

### Parallel Opportunities

- T002 runs in parallel with T001
- T006, T009, T013, T014 are parallel within Phase 2 (distinct files)
- All test tasks within a story (T015–T018, T027–T030, T038–T039, T043–T045, T056–T057) are parallel — distinct files
- US1, US2, and US4 can be developed concurrently by three developers once Phase 2 lands
- T050 and T051 are parallel in Phase 7

---

## Parallel Example: User Story 1

```bash
# Write all four US1 test tasks together, confirm they fail:
Task: "ObservedTool contract tests in tests/unit/test_observe_models.py"
Task: "build_tools_query tests in tests/unit/test_observe_queries.py"
Task: "Tools normalization tests in tests/unit/test_observe_service.py"
Task: "Tools end-to-end scenario in tests/integration/test_observe_end_to_end.py"

# Then implement sequentially: T019 → T020 → T021 → T022 → T023 → T024 → T025 → T026
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational — **blocks everything**
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: run quickstart Scenarios 1–4 and 6
5. Ship — the Tools view answers the largest currently-unanswerable operational question on its own

### Incremental Delivery

1. Setup + Foundational → shared contracts and truncation honesty ready
2. US1 → Tools view → validate → ship (MVP)
3. US2 → Runs view → validate → ship
4. US3 → coverage explanations → validate → ship
5. US4 → refined runtime values → **ship as a breaking release** with the changelog entry from T051

Sequence US4 last: it is the lowest-priority story and the only breaking one, so every other increment can ship non-breaking first.

### Parallel Team Strategy

1. Team completes Setup + Foundational together
2. Then: Developer A → US1, Developer B → US2, Developer C → US4
3. US3 follows whichever of US1/US2 lands first
4. Polish is a single owner after the last desired story merges

---

## Notes

- `[P]` = different files, no dependencies on incomplete tasks
- Every UI change lands in **two** places: the Python renderer and the embedded `_OBSERVE_SCRIPT` JavaScript
- Absent is never zero: `p95_latency_ms`, `input_tokens`, `output_tokens`, and `rows_total_in_scope` all carry `None` meaningfully
- The facade needs no routing change — `_NATIVE_QUERY_VIEWS` derives from the service `View` literal and extends automatically
- Never add a view to the service `View` literal without registering its builder in the same change, or the dispatch will raise
- Commit after each task or logical group; stop at any checkpoint to validate a story independently
