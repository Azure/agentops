---
description: "Task list for Observe console refinements"
---

# Tasks: Observe Console Refinements

**Input**: Design documents from `specs/014-observe-console-refinements/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/observe-console.md, quickstart.md

**Tests**: Test tasks are included. This repository carries an established suite
and the project constitution requires coverage for new contracts, services, and
UI behaviour, so tests are treated as part of each story rather than optional.

**Organization**: Tasks are grouped by user story so each story can be
implemented, tested, and demonstrated on its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US6)
- Exact file paths are given in every task

## Path Conventions

Single project. Package code lives under `src/agentops/`, tests under `tests/`.
All paths below are relative to the repository root.

## Standing Constraints (apply to every task)

- `src/agentops/core/` stays pure: no Azure SDK imports, no network, no I/O.
- Every route and every view introduced here stays read-only.
- No field carrying model input, model output, or prompt text may reach the
  console address, a log line, or a snapshot.
- The rendered document stays self-contained: no CDN, no external asset.
- Anything duplicated between Python and the embedded JavaScript in `ui.py` must
  be changed in both places in the same task, never in separate tasks.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish a known-good baseline and the test data every story needs

- [X] T001 Confirm the Observe baseline is green before any change by running `python -m pytest tests/unit/test_observe_ui.py tests/unit/test_observe_ui_visual.py tests/unit/test_observe_service.py tests/unit/test_observe_queries.py -q`, and record the current snapshot bytes so later diffs are attributable
- [X] T002 [P] Extend the deterministic row builders in `tests/fixtures/observe.py` with factories for scope-facet option rows, run-to-model rows carrying per-token-type counts, and price-reference entries, keeping every builder free of generative content

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Remove the label-keyed table coupling before any story renames a
column or adds one. Both US3 and US4 change the Runs table; doing this first is
what stops a rename from silently breaking sorting.

**⚠️ CRITICAL**: US3 and US4 must not begin until this phase is complete

- [X] T003 Add the `RunsTableColumn` declaration contract to `src/agentops/core/observe.py` per data-model §7, carrying a stable identifier, displayed label, optional sort key, optional help text, and priority, with validation that identifiers are unique
- [X] T004 Replace the label-keyed Python sort map in `src/agentops/agent/observe/ui.py` (the runs-table sort key mapping near the `_render_runs_table` helpers) with a lookup driven by the T003 declaration, and emit `data-column-id` on each `<th>` alongside the existing `data-label`
- [X] T005 Replace the label-keyed JavaScript sort map in the embedded script in `src/agentops/agent/observe/ui.py` with the same identifier-keyed lookup, reading `data-column-id`, so Python and JS resolve a column identically
- [X] T006 [P] Add a unit test in `tests/unit/test_observe_ui.py` proving that changing a column's displayed label leaves its sort behaviour, its filter behaviour, and its help association unchanged (FR-030)
- [X] T007 [P] Add a unit test in `tests/unit/test_observe_ui.py` asserting the Python and embedded-JS column declarations agree on identifiers, so the two copies cannot drift

**Checkpoint**: The Runs table is keyed by identity, not by prose. Stories may begin.

---

## Phase 3: User Story 1 - Scope the Console by Picking, Not Typing (Priority: P1) 🎯 MVP

**Goal**: Every scope filter offers the values that actually exist in the current
scope and window as a checkable, cascading, multi-select list, so an operator who
did not deploy the agent can still narrow the console.

**Independent Test**: With telemetry for at least two Foundry resources, two
projects, and three agents in range, open Observe, select two agents from the
Agent filter without typing, apply, switch to another tab, and confirm the
selection is still applied and the result set matches only those agents.

### Contracts and query layer for US1

- [X] T008 [P] [US1] Add the scope filter dimension and bounded option set contracts to `src/agentops/core/observe.py` per data-model §2, carrying the activity-ordered options, the `truncated` flag, the distinct total when cheaply known, and a coverage state
- [X] T009 [US1] Add a facet enumeration KQL builder to `src/agentops/agent/observe/queries.py` that summarises distinct values for one dimension bounded to approximately fifty by activity, applies only the left-hand cascade selections, and accepts an optional search fragment applied server-side rather than to an already-bounded set (FR-007a)
- [X] T010 [P] [US1] Add unit tests in `tests/unit/test_observe_queries.py` covering the facet builder's bound, its activity ordering, its cascade filtering, that selections to the right of the requested dimension are ignored, and that a search fragment reaches the query rather than the returned page

### Service layer for US1

- [X] T011 [US1] Add facet retrieval to `src/agentops/agent/observe/service.py`, reusing `ObserveCache` at inventory-length TTL, merging results across sources, and reporting truncation when any source truncated
- [X] T012 [US1] Implement cascade invalidation in `src/agentops/agent/observe/service.py` so a selection made unreachable by a higher-level filter change is dropped and reported rather than silently retained (FR-004)
- [X] T013 [P] [US1] Add unit tests in `tests/unit/test_observe_service.py` for facet caching, multi-source merge, truncation propagation, cascade invalidation, and graceful degradation when a source times out

### Route and UI for US1

- [X] T014 [US1] Add the `POST /api/observe/scope-options` route to `src/agentops/agent/cockpit.py` per contracts Surface 2, served off the render critical path, read-only, acquiring no new credential or outbound dependency
- [X] T015 [US1] Replace the free-text scope inputs in `src/agentops/agent/observe/ui.py` with checkable multi-select controls per dimension that hold changes in a draft state until explicitly applied (FR-006), that state for each dimension how many of the total available options the offered set represents whenever that set is bounded (FR-007), and that fall back to free-text entry when the options route fails or times out (FR-007b)
- [X] T016 [US1] Wire the cascade in the embedded script in `src/agentops/agent/observe/ui.py` so selecting a value refreshes the option sets to its right and leaves those to its left untouched
- [X] T017 [US1] Ensure applied selections and window round-trip through the console address in `src/agentops/agent/observe/ui.py` using the existing allowlist keys, and that no scope survives beyond what the address carries (FR-005a)
- [X] T018 [P] [US1] Add unit tests in `tests/unit/test_observe_ui.py` covering draft-until-apply, multi-select round-trip through the address, that a bounded option set renders its count against the total and an unbounded one does not (FR-007), that selecting zero values in a dimension leaves that dimension unrestricted (FR-002), the free-text fallback path, keyboard operability of the new controls, and that no option label or selection introduces generative content into the address
- [X] T019 [P] [US1] Add an integration test in `tests/integration/` driving select → apply → switch tab → confirm the selection and the narrowed result set survive

- [X] T019a [US1] Regenerate the golden snapshots under `tests/unit/__snapshots__/` with `$env:PYTHONPATH="src"; $env:AGENTOPS_UPDATE_SNAPSHOTS="1"; python -m pytest tests/unit/test_observe_ui_visual.py -q; Remove-Item Env:\AGENTOPS_UPDATE_SNAPSHOTS`, normalise line endings to LF, and review the diff line by line. The scope controls are part of the rendered document, so this story changes it

**Checkpoint**: An operator can scope the console without knowing an identifier. This alone is shippable.

---

## Phase 4: User Story 2 - Choose the Time Window From Named Presets (Priority: P1)

**Goal**: The window is chosen from named relative presets with a Custom escape
hatch, defaults to 7 days, and a relative choice stays relative across refreshes.

**Independent Test**: Open Observe with no saved state, confirm the last 7 days
is preselected and the result set matches it, click the "1 day" preset and
confirm the result set narrows, then click "Custom" and confirm explicit start
and end pickers appear and drive the window.

- [X] T020 [P] [US2] Add the `WindowSelection` discriminated contract to `src/agentops/core/observe.py` per data-model §1, covering the eight presets, the custom interval, and validation that exactly one form is populated and that a custom `end` is strictly after its `start`
- [X] T021 [US2] Resolve a preset to absolute boundaries at query-build time in `src/agentops/agent/observe/service.py`, re-resolving on every manual and automatic refresh so a relative window never freezes (FR-012), while continuing to hand the existing query builders the same absolute pair they accept today
- [X] T022 [US2] Add the window preset key to the address allowlist in `src/agentops/agent/observe/ui.py` per contracts Surface 1, keeping the existing start and end keys for custom windows
- [X] T023 [US2] Replace the window controls in `src/agentops/agent/observe/ui.py` with the named presets plus a Custom option that reveals explicit start and end pickers only when chosen, defaulting to 7 days when the address carries no window (FR-010)
- [X] T024 [US2] Apply the selected window across every Observe tab in `src/agentops/agent/observe/ui.py` so switching views does not reset it (FR-013)
- [X] T025 [US2] Reject a custom interval whose end is not after its start in `src/agentops/agent/observe/ui.py` with a visible explanation, ensuring the invalid pair never reaches a query builder (FR-014)
- [X] T026 [P] [US2] Add unit tests in `tests/unit/test_observe_service.py` proving a preset re-resolves against the current moment on refresh and a custom window does not
- [X] T027 [P] [US2] Add unit tests in `tests/unit/test_observe_ui.py` covering the 7-day default, preset round-trip through the address, the Custom reveal, the invalid-interval rejection, and keyboard operability of the preset controls

- [X] T027a [US2] Regenerate the golden snapshots under `tests/unit/__snapshots__/` with the T019a command, normalise line endings to LF, and review the diff. The window controls are part of the rendered document

**Checkpoint**: US1 and US2 together give a fully navigable, shareable console scope.

---

## Phase 5: User Story 3 - Read the Runs Table Without Decoding It (Priority: P2)

**Goal**: Long identifiers are shortened with a copy affordance, headers say what
they mean, explanations are the console's own and keyboard-reachable, and a
dimension that carries one value stops occupying a column — but only when the
displayed rows are the whole scope.

**Independent Test**: With at least five runs in range, open the Runs tab and
confirm the run key and source are shortened, each offers a copy action that
places the full value on the clipboard, no header carries the "in range" suffix,
and hovering a header shows the console's own explanation panel.

**Depends on**: Phase 2 (column declaration)

- [X] T028 [US3] Shorten the run key to its first eight characters followed by an ellipsis in `src/agentops/agent/observe/ui.py`, keeping the full value available to the copy affordance (FR-026). FR-026 says "readable column width" without a figure; eight characters is the decision recorded here, chosen because it matches the abbreviated-identifier convention operators already read elsewhere and keeps collisions within a single window implausible for a hex-shaped key. Hold the width in a named constant so revising it is a one-line change, and do not shorten a key already at or below that width
- [X] T029 [US3] Display the telemetry source as the logs workspace name rather than a full resource identifier in `src/agentops/agent/observe/ui.py`, keeping the full identifier available to the copy affordance (FR-027)
- [X] T030 [US3] Add the copy affordance to `src/agentops/agent/observe/ui.py` using the browser clipboard capability where available and presenting the full value for manual selection otherwise, from one piece of markup serving both cases, and confirming success or failure to the operator (FR-028)
- [X] T031 [US3] Rename the Runs table column labels in the T003 declaration in `src/agentops/core/observe.py` to drop the redundant "in range" suffix, relying on Phase 2 so sort and filter keys are untouched (FR-029)
- [X] T032 [US3] Replace native `title=` header explanations in `src/agentops/agent/observe/ui.py` with a console-rendered panel that supports the theme, is reachable by pointer and by keyboard, and dismisses on Escape (FR-031, FR-032)
- [X] T033 [US3] Suppress a column whose displayed rows all carry one distinct value in `src/agentops/agent/observe/ui.py`, stating the value once above the table and restoring the column automatically when a second value appears (FR-033)
- [X] T034 [US3] Guard the T033 suppression on `ResultBounds.truncated` in `src/agentops/agent/observe/ui.py` so a truncated row set never collapses a dimension, because uniformity across a returned subset establishes nothing about the rows not returned (FR-033a)
- [X] T035 [US3] Do not treat an entirely unreported dimension as single-valued in `src/agentops/agent/observe/ui.py`, preserving the existing distinction between a reported zero and an unreported value
- [X] T036 [US3] Add inline row detail to `src/agentops/agent/observe/ui.py` as a native disclosure element rendered beneath its row, meaningful without script, replacing the pressure to add another column
- [X] T037 [P] [US3] Add unit tests in `tests/unit/test_observe_ui.py` for run key and source shortening, and that the copy affordance carries the full untruncated value
- [X] T038 [P] [US3] Add a unit test in `tests/unit/test_observe_ui.py` asserting no rendered header contains the "in range" suffix and that each renamed column still sorts
- [X] T039 [P] [US3] Add unit tests in `tests/unit/test_observe_ui.py` for single-value suppression, automatic column restoration, the truncation guard from T034, and the unreported-dimension case
- [X] T040 [P] [US3] Add a unit test in `tests/unit/test_observe_ui.py` proving the header explanation panel and the row disclosure are both keyboard operable (FR-047)
- [X] T041 [US3] Regenerate the Runs and Overview golden snapshots under `tests/unit/__snapshots__/` with `$env:PYTHONPATH="src"; $env:AGENTOPS_UPDATE_SNAPSHOTS="1"; python -m pytest tests/unit/test_observe_ui_visual.py -q; Remove-Item Env:\AGENTOPS_UPDATE_SNAPSHOTS`, normalise line endings to LF, and review the diff line by line

**Checkpoint**: The Runs table is legible and its columns are honest about scope.

---

## Phase 6: User Story 4 - See What a Run Cost (Priority: P2)

**Goal**: Each run carries an estimated cost derived from observed tokens and a
packaged list-price reference, labelled as an estimate, honest about what it
omits, and never merged with a declared billed total.

**Independent Test**: With runs whose token usage is recorded for a priced model,
open the Runs tab and confirm each such run shows a currency-qualified estimate,
that opening the column explanation states the formula and the price reference
date, that a run using an unpriced model shows an explicit "not priced" state
rather than zero, and that the view grouped by agent shows a rolled-up estimate
naming how many of its runs went unpriced.

**Depends on**: Phase 2 (column declaration) and Phase 5 (US3). US3 renames labels
in the same `RunsTableColumn` declaration this story extends with a new column;
running them concurrently means two edits to one declaration.

### Price reference for US4

- [X] T042 [P] [US4] Add the packaged list-price reference data file under `src/agentops/agent/observe/pricing/` per contracts Surface 4, human-readable, versioned, carrying an effective date, the stated source of its figures (FR-036), and unit prices held separately per model and per token type, naming input, output and cached tokens at minimum (FR-037)
- [X] T043 [US4] Add the `package-data` entry for `agentops.agent.observe.pricing` to `pyproject.toml`, without which the reference is absent from an installed wheel and every figure silently degrades
- [X] T044 [P] [US4] Add the pure parser `src/agentops/core/observe_pricing.py` that validates a price reference from an in-memory string, mirroring `core/cost.py::load_cost_model`, performing no I/O
- [X] T045 [US4] Load the packaged reference via `importlib.resources` in `src/agentops/agent/observe/service.py` following the `agent/knowledge/__init__.py` precedent, cached once, degrading gracefully to an unavailable state when the file is missing, unreadable, or invalid (FR-041)
- [X] T046 [P] [US4] Add `tests/unit/test_observe_pricing.py` covering parsing, per-model and per-token-type separation, version and effective-date handling, the ninety-day staleness boundary, and rejection of an invalid reference

### Contracts and data for US4

- [X] T047 [P] [US4] Add the `CostEstimate` contract to `src/agentops/core/observe.py` per data-model §6, including `completeness`, `excluded_components`, `unpriced_run_count`, `covered_run_count`, `scope_run_count`, the reference version and effective date, and the staleness flag
- [X] T048 [P] [US4] Extend `ObservedRun` in `src/agentops/core/observe.py` per data-model §4 with `model_usage` — one entry per distinct model carrying that model's own five token counts — and the `model_usage_truncated` flag. The five run-level token counts already exist on `ObservedRun` and are already summed by the runs query: leave them as the totals the token columns display and do not re-add or re-derive them
- [X] T049 [US4] Attribute tokens to their model in `build_runs_query` in `src/agentops/agent/observe/queries.py` by grouping the inner aggregation by `model` alongside the existing run keys and re-grouping to one row per run in an outer aggregation, following the two-stage `summarize` + `make_bag` pattern already used by `build_models_query`. `model` is already in scope via the shared agent extend clauses; preserve the existing one-row-per-run shape and the existing run-level token sums, and set the truncation flag when the per-model set is bounded (FR-035a)
- [X] T050 [US4] Extend `build_usage_query` in `src/agentops/agent/observe/queries.py` to project token counts per token type alongside its existing request count, keyed by agent and model, so a roll-up can be priced without reading run rows
- [X] T051 [P] [US4] Add unit tests in `tests/unit/test_observe_queries.py` for the per-model token attribution in the runs query, its preservation of one row per run and of the existing run-level token sums, its truncation flag, and the token projection on the usage query

### Estimation and roll-up for US4

- [X] T052 [US4] Derive a per-run estimate in `src/agentops/agent/observe/service.py` by pricing each `model_usage` entry at its own model's rates and summing across entries, never by applying one model's rates to the run's combined totals (FR-035a), reporting `not_priced` rather than zero when a model or token type has no price (FR-038) and recording excluded components when a run incurs cost that tokens cannot express or when `model_usage_truncated` is set (FR-039)
- [X] T053 [US4] Derive agent-level and model-level roll-ups in `src/agentops/agent/observe/service.py` from the server-side aggregation keyed by entity and model, never by summing displayed run rows, so reaching the row bound reduces what is shown without reducing what is covered (FR-034d)
- [X] T054 [US4] Report `covered_run_count` against `scope_run_count` in `src/agentops/agent/observe/service.py`, and downgrade `completeness` to at most `partial` when full coverage cannot be established, so a partial sum is never presented as a total
- [X] T055 [P] [US4] Add unit tests in `tests/unit/test_observe_service.py` covering the per-run estimate, a multi-model run priced at each model's own rates rather than one model's (FR-035a), the unpriced state, excluded components, roll-up coverage equal to scope under truncation, and the downgrade path when coverage is unprovable

### Presentation for US4

- [X] T056 [US4] Add the estimated cost column to the T003 declaration and render it in `src/agentops/agent/observe/ui.py` with its currency, its completeness state, and the estimate-at-list-price disclaimer, never rendering a figure without them (FR-034, FR-040)
- [X] T057 [US4] Render the roll-up in the agent-grouped and model-grouped views in `src/agentops/agent/observe/ui.py`, naming how many contributing runs went unpriced (FR-034a, FR-034b, FR-034c)
- [X] T058 [US4] Render a stale marker in `src/agentops/agent/observe/ui.py` stating the reference's age when the reference is more than ninety days past its effective date, and keep displaying the figure (FR-036a)
- [X] T059 [P] [US4] Add unit tests in `tests/unit/test_observe_ui.py` asserting estimated cost and any allocated billed total render as distinct figures that are never summed together (FR-042, FR-043), that no estimate renders without its disclaimer, that the stale marker appears at the boundary, and that producing an estimate requires no credential and no outbound call (FR-044)

- [X] T059a [US4] Regenerate the golden snapshots under `tests/unit/__snapshots__/` with the T019a command, normalise line endings to LF, and review the diff. This story adds a column to the Runs table

**Checkpoint**: Cost is visible, labelled, and structurally incapable of overstating its coverage.

---

## Phase 7: User Story 5 - Understand What the Overview Counts (Priority: P3)

**Goal**: Every headline names the entity it measures, each entity family gets a
summary, runs come first with token consumption, and an empty family says so.

**Independent Test**: Open Overview with telemetry present and confirm each
headline states its entity, that a runs summary including token consumption is
presented first, and that the navigation reads Overview, Runs, Agents, Models and
usage, Tools.

- [X] T060 [P] [US5] Add the `EntitySummary` contract to `src/agentops/core/observe.py` per data-model §3, binding every figure to an owning entity family
- [X] T061 [US5] Assemble the per-family summaries in `src/agentops/agent/observe/service.py` from the aggregation the Overview query already performs, adding no telemetry round-trip relative to today (FR-024)
- [X] T062 [US5] Order degradation in `src/agentops/agent/observe/service.py` so summaries are dropped before run data when a source fails or times out
- [X] T063 [US5] Render the entity-qualified headlines and per-family summary cards in `src/agentops/agent/observe/ui.py`, presenting the runs summary with token consumption first (FR-020, FR-021, FR-022)
- [X] T064 [US5] Render an explicit empty state for a family with no data in the current scope and window in `src/agentops/agent/observe/ui.py`, distinct from a reported zero (FR-023)
- [X] T065 [US5] Reorder the navigation to Overview, Runs, Agents, Models and usage, Tools in `src/agentops/agent/observe/ui.py`, updating the view identifiers in both the Python and the embedded-JS copies together (FR-025)
- [X] T066 [P] [US5] Add unit tests in `tests/unit/test_observe_service.py` and `tests/unit/test_observe_ui.py` for summary assembly without an extra round-trip, the degradation order, the empty state, entity-qualified headlines, and the navigation order

- [X] T066a [US5] Regenerate the golden snapshots under `tests/unit/__snapshots__/` with the T019a command, normalise line endings to LF, and review the diff. This story changes the Overview headlines and the navigation order

**Checkpoint**: The Overview states what it is counting.

---

## Phase 8: User Story 6 - Read Every Time in One Timezone (Priority: P3)

**Goal**: Every time on a page shares one basis, that basis is stated once, and
the refresh indicator is compact, correctly placed, and honest before the first
refresh.

**Independent Test**: In a non-UTC timezone, open Observe, note the start and end
of the selected window and the refreshed indicator, and confirm both are
expressed in the same timezone, that the timezone is stated on the page, and that
the refreshed indicator sits to the right of the controls in a compact format.

- [X] T067 [US6] Express window boundaries, row timestamps, and the refresh indicator in one and the same basis in `src/agentops/agent/observe/ui.py`, changing the Python formatter and the embedded-JS formatter together so they cannot disagree (FR-015)
- [X] T068 [US6] Keep UTC on the wire in `src/agentops/agent/observe/service.py` and `src/agentops/agent/observe/queries.py`, converting only at the presentation boundary, so query behaviour and snapshot determinism are unaffected
- [X] T069 [US6] State the timezone in use once on the page adjacent to the time controls in `src/agentops/agent/observe/ui.py` (FR-016)
- [X] T070 [US6] Move the refresh indicator to the right of the time controls in `src/agentops/agent/observe/ui.py` (FR-017)
- [X] T071 [US6] Render the refresh indicator with an abbreviated date form in `src/agentops/agent/observe/ui.py` (FR-018)
- [X] T072 [US6] Report that no refresh has occurred before the first successful refresh in `src/agentops/agent/observe/ui.py`, rather than implying one has (FR-019)
- [X] T073 [P] [US6] Add unit tests in `tests/unit/test_observe_ui.py` asserting one shared time basis across window, rows, and indicator, that the basis is stated exactly once, the indicator's placement and abbreviated form, the pre-refresh state, and that the Python and JS formatters agree for a fixed input

- [X] T073a [US6] Regenerate the golden snapshots under `tests/unit/__snapshots__/` with the T019a command, normalise line endings to LF, and review the diff. This story changes how every time on the page is formatted, so the diff is expected to be wide; confirm it is confined to time expressions

**Checkpoint**: All six stories complete.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T074 [P] Add a unit test in `tests/unit/test_observe_ui.py` asserting every value duplicated between Python and the embedded JavaScript in `src/agentops/agent/observe/ui.py` agrees — view identifiers, filter keys, the default window, the auto-refresh interval, tone labels, source kind labels, and the column declaration
- [X] T075 [P] Verify every new control renders legibly under both themes in `src/agentops/agent/observe/ui.py` using the existing `ui_theme.py` tokens, adding no new colour literal (FR-048)
- [X] T076 [P] Add a unit test in `tests/unit/test_observe_service.py` asserting no route introduced or changed by this feature performs a write, and a unit test in `tests/unit/test_observe_ui.py` asserting the console address carries no generative content (FR-045, FR-008)
- [X] T077 Update `docs/observe.md` to describe pickable scope filters, window presets, the timezone basis, the expanded Overview, the readable Runs table, and estimated cost including its list-price basis, its staleness rule, and its separation from any declared billed total
- [X] T078 Add a CHANGELOG.md entry under Unreleased describing the user-visible Observe console changes
- [X] T078a [P] Add a test in `tests/unit/test_observe_ui.py` asserting the structural success criteria against the T002 fixtures: that the Runs table renders within its declared column budget with no horizontal scroll at the stated viewport width (SC-008), and that a result at the five-thousand-row bound renders its rows and its truncation notice without unbounded per-row cost (SC-017)
- [X] T078b [P] Add a timed test in `tests/unit/test_observe_service.py` that exercises the T002 fixtures at scale and records the measured figures, asserting a thousand-run scope resolves within its stated budget (SC-011) and a thousand-agent Overview resolves within its stated budget (SC-013), with the thresholds read from named constants so a change of budget is a one-line change
- [X] T079 Run the full suite from the repository root with `python -m pytest tests/ -x -q` and resolve every failure
- [ ] T080 **[Operator task — not automatable]** Walk `specs/014-observe-console-refinements/quickstart.md` end to end against a live workspace carrying real telemetry, and confirm each documented scenario produces its stated outcome. This needs credentials and a populated Log Analytics workspace, so it cannot be completed from the test suite; leave it unchecked until a human has run it

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup — **blocks US3 and US4**
- **US1 (Phase 3)** and **US2 (Phase 4)**: Depend only on Setup. They may start
  before Phase 2 completes, though completing Phase 2 first keeps the branch
  coherent
- **US3 (Phase 5)**: Depends on Phase 2
- **US4 (Phase 6)**: Depends on Phase 2 and on Phase 5 (US3), which renames labels
  in the same column declaration this story extends
- **US5 (Phase 7)** and **US6 (Phase 8)**: Depend only on Setup
- **Polish (Phase 9)**: Depends on every story that is being shipped

### User Story Dependencies

- **US1 (P1)**: Independent. Delivers the MVP
- **US2 (P1)**: Independent of US1. Both touch the control strip in `ui.py`, so
  sequence them rather than editing that region concurrently
- **US3 (P2)**: Requires Phase 2 only. Independent of other stories
- **US4 (P2)**: Requires Phase 2 only. Adds a column to the same declaration US3
  renames, so run US3 first to avoid two edits to one declaration
- **US5 (P3)**: Independent
- **US6 (P3)**: Independent, but touches the same control strip as US2

### Serialisation Hazards

Three files concentrate the work and cannot be edited concurrently by two agents:

- `src/agentops/agent/observe/ui.py` — touched by every story
- `src/agentops/core/observe.py` — touched by Phase 2, US1, US2, US4, US5
- `src/agentops/agent/observe/service.py` — touched by US1, US2, US4, US5, US6

Treat tasks marked [P] as parallel only when they are in different files.

### Snapshot Ordering

`tests/unit/__snapshots__/` regenerates from the rendered document. Every story
here changes that document, so each one closes with its own regeneration task —
T019a, T027a, T041, T059a, T066a, T073a. Regenerate once at the end of a story,
never mid-story, and always review the diff rather than accepting it. Skipping a
story's regeneration leaves the visual suite failing for reasons the next story
did not cause.

---

## Parallel Example: User Story 1

```bash
# Independent contract and test work, different files:
Task: "T008 Add scope filter option contracts in src/agentops/core/observe.py"
Task: "T010 Add facet builder tests in tests/unit/test_observe_queries.py"

# After the service layer lands, these are independent:
Task: "T018 Add UI unit tests in tests/unit/test_observe_ui.py"
Task: "T019 Add the select-apply-switch integration test in tests/integration/"
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. Phase 1: Setup
2. Phase 3: US1
3. **STOP and VALIDATE**: an operator who does not know an agent identifier can
   still narrow the console, and the narrowed scope survives a tab switch and a
   page reload
4. Ship if ready — US1 alone removes the console's hardest blocker

### Incremental Delivery

1. Setup → Foundational → foundation ready
2. US1 → validate → ship (MVP)
3. US2 → validate → ship — scope and window are now both pickable
4. US3 → validate → ship — the Runs table becomes legible
5. US4 → validate → ship — cost appears, honestly bounded
6. US5, US6 → validate → ship — Overview and time presentation

Each increment stands alone and none regresses the one before it.

### Parallel Team Strategy

After Phase 2, three tracks can run concurrently if the file hazards above are
respected:

- Track A: US1 then US2 (control strip and scope layer)
- Track B: US3 then US4 (Runs table and cost)
- Track C: US5 then US6 (Overview and time presentation)

Track C touches the control strip in US6, so it must land after Track A's US2.

---

## Notes

- [P] means different files and no dependency on incomplete work
- Every user story is independently completable and independently demonstrable
- Commit after each task or each coherent group
- Stop at any checkpoint to validate a story on its own
- Do not commit `uv.lock`
