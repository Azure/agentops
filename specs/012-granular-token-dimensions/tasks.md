---

description: "Task list for granular token classes in the Cockpit models view"
---

# Tasks: Granular Token Classes in the Models View

**Input**: Design documents from `/specs/012-granular-token-dimensions/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: Test tasks ARE included. Constitution v1.1.0 Principle V ("Verify Every Behavior
Change") makes them mandatory for this feature, and `plan.md` § "Required tests" names the
four seams that must be covered.

**Organization**: Tasks are grouped by user story so each story can be implemented, tested,
and shipped independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: `[US1]`, `[US2]`, `[US3]` — maps to the user stories in `spec.md`
- Exact file paths are included in every task

## Path Conventions

Single project, `src/` + `tests/` at repository root. This feature is a vertical slice
through the existing Cockpit observe stack and adds **no new module, package, or directory**.

## Non-negotiable constraints (apply to every task)

These come from the issue and the spec. Violating any one of them fails the feature even if
the tests pass.

| Constraint | Source |
|---|---|
| No token value, label, tooltip, or field name may read as cost, price, rate, spend, charge, or billing | FR-017 |
| Never derive a token class by subtraction or inference from another value | FR-006 |
| `(observed usage, not billing data)` stays byte-identical and stays where it is | FR-016, `ui.py:400-414` |
| `None` means *not reported*; `0` means *reported as zero*. Never collapse the two | FR-005, FR-007 |
| The agents view and the combined usage view keep their current token rendering | Out of Scope |
| Copilot Studio stays out — it is tracked separately in #443 | FR-019 |
| Use the word **class** in all prose and identifiers, never "dimension" (that word is reserved for `CoverageResult.dimension`) | spec vocabulary |

---

## Phase 1: Setup

**Purpose**: Establish a known-green baseline before touching anything.

- [X] T001 Record the pre-change baseline by running `python -m pytest tests/unit/test_observe_queries.py tests/unit/test_observe_service.py tests/unit/test_observe_ui.py -q` and confirming it passes; capture the output in the working branch notes so later regressions are attributable
- [X] T002 [P] Confirm this feature adds no dependency by checking that `pyproject.toml` needs no edit — the change is KQL text, Pydantic field declarations, and rendering only (see `plan.md` § Technical Context)

**Checkpoint**: Baseline green, dependency surface unchanged.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The alias vocabulary and the data contract. Every user story reads from both.

**⚠️ CRITICAL**: No user story work can begin until T003 and T004 are complete.

- [X] T003 Add the `TOKEN_CLASS_ALIASES` mapping as a module-level constant in `src/agentops/agent/observe/queries.py`, mapping each of the three normalized classes (`cache_read`, `cache_write`, `reasoning`) to an ordered tuple of accepted `gen_ai.usage.*` attribute names exactly as tabulated in `research.md` D2, plus a derived `TOKEN_CLASS_ALIAS_NAMES` frozenset of every accepted name for later exclusion. Place it in `queries.py` rather than `service.py`: `service.py` already imports `queries.py` at line 24, so the reverse import would be circular, and `queries.py` is where the names are consumed to build the query. The constant must be a plain data structure that can be inspected and asserted on without parsing query text (FR-002, FR-008, FR-009)
- [X] T004 Extend `ModelUsage` in `src/agentops/core/observe.py` with the six fields defined in `data-model.md` §1 — `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens` (each `int | None = None`, `ge=0`), `additional_token_classes` (`dict[str, int]`, default `{}`, values `ge=0`), `additional_token_classes_truncated` (`bool = False`), `token_classes_partial` (`bool = False`). All six carry defaults so existing construction sites keep working under `extra="forbid"`. Do not modify `CoverageState` or `CoverageResult` (FR-001, FR-004, FR-005, FR-007, FR-021, FR-022, Constitution I)
- [X] T005 [P] Add a test in `tests/unit/test_observe_queries.py` asserting `TOKEN_CLASS_ALIASES` has exactly the three normalized classes, that every accepted name starts with `gen_ai.usage.`, and that no name appears under two classes — this is the FR-008 "single place" guarantee made executable
- [X] T006 [P] Add a test in `tests/unit/test_observe_service.py` asserting `ModelUsage` can still be constructed with only the pre-existing fields and that the six new fields take their documented defaults (backward compatibility, Constitution I)

**Checkpoint**: Vocabulary and contract in place. User stories can now proceed in parallel.

---

## Phase 3: User Story 1 - Compute a Provisioned Throughput Burn-Down (Priority: P1) 🎯 MVP

**Goal**: Each reported normalized token class appears as its own labeled value on the
models-view row for that deployment, distinct from the existing input and output totals.

**Independent Test**: With a telemetry source containing inference activity from a model
family that reports cached-input and reasoning classes, open the models view and confirm each
reported class renders as its own value on the deployment's row, matching the source-reported
sums for the window, with unreported classes shown as missing rather than zero.

### Tests for User Story 1 ⚠️

> Write these first and confirm they FAIL before implementing T012–T015.

- [X] T007 [P] [US1] Add failing tests in `tests/unit/test_observe_queries.py` asserting `build_models_query` projects `cache_read_tokens`, `cache_write_tokens`, and `reasoning_tokens` by `coalesce`-ing each class's accepted names from `TOKEN_CLASS_ALIASES`, and that the `summarize` clause sums all three by the unchanged grouping keys `project_resource_id, model, deployment` — per `contracts/models-query-columns.md`
- [X] T008 [P] [US1] Add failing tests in `tests/unit/test_observe_service.py` for `normalize_model_row` covering: the three fields populate from their columns; an absent column yields `None`; a reported `0` yields `0` and not `None`; `token_classes_partial` is `True` when at least one class is non-`None` and at least one is `None`; and `token_classes_partial` is `False` when all three are `None` (nothing reported is not partially reported) — per `data-model.md` §1 and `contracts/model-usage-row.md`
- [X] T009 [P] [US1] Add failing tests in `tests/unit/test_observe_ui.py` for the Python `render_usage_table` covering: each reported class renders as its own labeled value; an unreported class renders through the existing `_render_maybe_missing` "Not reported" treatment and never as `0`; the per-row partial indicator appears when `token_classes_partial` is `True`; and `(observed usage, not billing data)` is still emitted verbatim

### Implementation for User Story 1

- [X] T010 [US1] In `src/agentops/agent/observe/queries.py`, extend `build_models_query` only — append the three `| extend <class>_tokens = toint(coalesce(...))` clauses immediately after the existing models-only `deployment` extend at line 187, then add the three `sum()` terms to the existing `summarize`. Build the `coalesce` argument list from `TOKEN_CLASS_ALIASES` so the names are never restated in the query string. Do **not** modify `_agent_extend_clauses()` (lines 123–137) — it is shared with `build_agents_query` and `build_usage_query`, and both must keep byte-identical text (research.md D5, FR-008)
- [X] T011 [US1] In `src/agentops/agent/observe/service.py`, extend `normalize_model_row` (lines 204–218) to read the three new columns into their `ModelUsage` fields preserving `None` versus `0`, and to compute `token_classes_partial` once from the three values so neither renderer re-derives the rule. Leave `token_reporting_state` (lines 151–155) completely untouched (research.md D7, FR-005, FR-007, FR-022)
- [X] T012 [US1] In `src/agentops/agent/observe/ui.py`, extend the Python `render_usage_table` token cell (around line 820) to render the three classes as distinct labeled values reusing `_render_maybe_missing`, and to show the per-row partial indicator driven by `token_classes_partial`. Do not move or reword `_render_token_totals` (lines 400–414) (FR-014, FR-016, FR-022)
- [X] T013 [US1] In `src/agentops/agent/observe/ui.py`, mirror the exact same rendering in the `renderUsage` JS function (token cell around line 2009) so the served page and the Python-rendered page agree cell for cell — same labels, same missing text, same partial indicator (FR-014, FR-022)
- [X] T014 [US1] Run the User Story 1 wording gate: `Select-String -Path src\agentops\agent\observe\*.py -Pattern '(?i)\b(cost|price|pricing|rate|spend|charge|billing|bill)\b'` and confirm the only surviving match is the preserved `(observed usage, not billing data)` disclaimer (FR-017)
- [X] T015 [US1] Run `python -m pytest tests/unit/test_observe_queries.py tests/unit/test_observe_service.py tests/unit/test_observe_ui.py -q` and confirm T007–T009 now pass with no pre-existing test broken

**Checkpoint**: A deployment reporting cached-input and reasoning classes shows them broken
out on its own row. This is the MVP and is shippable on its own.

---

## Phase 4: User Story 2 - Know Which Token Classes Are Missing (Priority: P2)

**Goal**: The existing `token_usage` coverage entry reports a partial state for the models
view, naming what was reported and what was not, so a blank cell becomes an actionable
diagnosis.

**Independent Test**: With a telemetry source whose rows report only input and output totals,
request the models view and confirm the `token_usage` coverage entry reports `partial`, its
reason identifies the reported subset, and its next action names the classes that were not
reported.

### Tests for User Story 2 ⚠️

- [X] T016 [US2] Add failing tests in `tests/unit/test_observe_service.py` covering the full five-arm precedence from `contracts/token-usage-coverage.md`: a failed query still wins and yields the existing failure state; zero rows still yields `no_data`; no class reported at all still yields `not_reported`; some-but-not-all yields `partial`; all reported yields `available`. Assert the two `partial` variants are distinguishable by their `reason` and `next_action`, and assert the models branch now emits a `token_usage` entry while the agents branch entry (lines 639–657) is unchanged. Also assert that rows carrying **no** token attribute at all (the shape an out-of-scope Copilot Studio agent produces) are skipped by the fold, so a batch mixing one class-reporting row with one token-less row still yields `partial` and is never dragged down to `not_reported` (FR-011, FR-012, FR-013, FR-019)

### Implementation for User Story 2

- [X] T017 [US2] In `src/agentops/agent/observe/service.py`, add the `TokenClassInventory` helper described in `data-model.md` §2 — a sibling of `token_reporting_state` that folds a batch of normalized rows into the tri-state `"reported" | "partial" | "not_reported"`. Keep it in `service.py`, not in `core/`, because it derives state from runtime rows (Constitution II). A row that reports no token attribute at all carries no evidence about granular instrumentation and MUST be skipped by the fold entirely, so out-of-scope sources cannot degrade the signal for token-reporting sources (FR-019)
- [X] T018 [US2] In `src/agentops/agent/observe/service.py`, widen `classify_query_coverage` (lines 313–358) so its `reported` parameter also accepts the tri-state, and insert the new `partial` arm between the existing `not_reported` and `available` arms. Keep the default `reported: bool = True` so every current call site — including the agents view — behaves exactly as before. Do not add a member to `CoverageState`; `"partial"` already exists at `core/observe.py:15` (research.md D7, Constitution I)
- [X] T019 [US2] In `src/agentops/agent/observe/service.py`, add a `token_usage` `CoverageResult` entry to the `view == "models"` branch (lines 660–695), which today emits only `model_attribution`, using the agents branch as the reference pattern and feeding it the `TokenClassInventory` tri-state. No schema change is involved (research.md D8, `contracts/token-usage-coverage.md`)
- [X] T020 [US2] Add an automated assertion in `tests/unit/test_observe_service.py` that the partial `reason` and `next_action` satisfy the text constraints in `contracts/token-usage-coverage.md`: they name the specific classes, match none of the forbidden cost/price/rate/spend/charge/billing tokens, and contain no resource identifier or raw query text (FR-012, FR-017)
- [X] T021 [US2] Run `python -m pytest tests/unit/test_observe_service.py -q` and confirm T016 passes and `test_token_reporting_state_distinguishes_absence_from_zero` (line 172) is still green untouched

**Checkpoint**: Coverage panel and per-row indicator now agree, and a missing class is
explained rather than merely blank.

---

## Phase 5: User Story 3 - Trust the Normalization Across Model Families (Priority: P3)

**Goal**: The same displayed class means the same thing regardless of which vendor family or
instrumentation library produced the telemetry, and an unrecognized token attribute is
retained under its source name instead of being silently dropped or misfiled.

**Independent Test**: Run representative attribute sets from two distinct vendor families
that name the same class differently through the mapping and confirm both produce the same
normalized class with the correct value; confirm an unrecognized eligible attribute populates
no normalized class but is retained under its source attribute name.

### Tests for User Story 3 ⚠️

- [X] T022 [P] [US3] Add failing tests in `tests/unit/test_observe_service.py` covering: two distinct vendor families reporting the same class under different accepted names both normalize to that one class (FR-020, selected by the rule "≥2 distinct source names map to one class", never hardcoded by vendor); a record carrying both a canonical and a legacy alias for one class, each set to the same count, renders that count exactly once — the first accepted name in declared order wins and the aliases are never summed (FR-009, SC-008); an eligible unrecognized attribute is retained verbatim in `additional_token_classes` and populates no normalized class (FR-010); an attribute outside `gen_ai.usage.*` or with a negative or non-numeric value is rejected entirely (FR-004); more than five eligible unrecognized attributes retain exactly five ordered ascending by source attribute name with `additional_token_classes_truncated` set (FR-021)
- [X] T023 [P] [US3] Add failing tests in `tests/unit/test_observe_queries.py` asserting `build_models_query` projects the dynamic passthrough bag and that the passthrough join does not change the grouping keys, the `top {MAX_ROWS_PER_QUERY}` bound, or the number of round trips (`contracts/models-query-columns.md` invariants)

### Implementation for User Story 3

- [X] T024 [US3] In `src/agentops/agent/observe/queries.py`, extend `build_models_query` with the passthrough projection from `research.md` D5: expand `bag_keys(Properties)`, keep only keys under `gen_ai.usage.*` that are not in `TOKEN_CLASS_ALIAS_NAMES` and whose value is a non-negative number, sum per key per group, pack into `extra_token_classes`, and left-outer-join back on `project_resource_id, model, deployment` so it stays a single round trip. Do not apply the five-attribute cap here — the cap belongs in Python so truncation stays detectable (research.md D6, FR-004)
- [X] T025 [US3] In `src/agentops/agent/observe/service.py`, extend `normalize_model_row` to populate `additional_token_classes` from the `extra_token_classes` bag keyed by the source attribute name exactly as observed, excluding any name already consumed by a normalized class, sorting ascending by source attribute name, keeping the first five, and setting `additional_token_classes_truncated` when more were eligible. Unnormalized values must never be summed into a normalized class and must never influence `token_classes_partial` or the coverage state (FR-004, FR-010, FR-021)
- [X] T026 [US3] In `src/agentops/agent/observe/service.py`, confirm that when a record carries more than one accepted name for the same class, the first name present in the declared `TOKEN_CLASS_ALIASES` tuple order supplies the value and every remaining accepted name for that class is discarded rather than summed, and that every consumed source name is removed from the passthrough candidate set (research.md D2, FR-009, SC-008)
- [X] T027 [US3] In `src/agentops/agent/observe/ui.py`, render the retained unnormalized classes under their source attribute names and show a truncation indicator when `additional_token_classes_truncated` is `true`, in **both** the Python `render_usage_table` and the `renderUsage` JS mirror, so a truncated attribute is visibly distinguishable from one that was never reported (FR-021, `contracts/model-usage-row.md`)
- [X] T028 [US3] Run `python -m pytest tests/unit/test_observe_service.py tests/unit/test_observe_queries.py tests/unit/test_observe_ui.py -q` and confirm T022–T023 pass

**Checkpoint**: All three stories are independently functional. Onboarding a new model family
is a change to one table, not to query text.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Prove nothing outside the models view moved, and close out the quickstart.

- [X] T029 [P] Add non-regression tests in `tests/unit/test_observe_queries.py` asserting the text produced by `build_agents_query` and `build_usage_query` is unchanged by this feature — these share `_agent_extend_clauses()` with the models query and must not pick up the new clauses. Also assert `build_models_query` applies no predicate on agent runtime type, so Foundry hosted, Foundry prompt, registered-external and unregistered-external runtimes — and non-Microsoft model families — are all aggregated identically (FR-018)
- [X] T030 [P] Add a non-regression test in `tests/unit/test_observe_ui.py` asserting the agents view and the combined usage view token rendering is byte-identical to the pre-change output, and that `(observed usage, not billing data)` is still emitted verbatim (Out of Scope, FR-016). Also add a **models-view** non-regression assertion that a row reporting only input/output tokens renders exactly as it does today apart from the FR-022 per-row partial indicator (FR-015, SC-004)
- [X] T031 Run the repository-wide wording gate `Select-String -Path src\agentops\agent\observe\*.py,src\agentops\core\observe.py -Pattern '(?i)\b(cost|price|pricing|rate|spend|charge|billing|bill)\b'` and confirm the feature adds no match; the broad scan still reports pre-existing failure-rate labels plus the preserved `(observed usage, not billing data)` disclaimer (FR-017)
- [X] T032 Grep the changed surface for the forbidden derivation patterns — confirm no expression subtracts one token count from another to produce a class, and no expression compares a token total against a context-length threshold to derive a long-context class, in either `queries.py` KQL text or `service.py` (FR-006, FR-003)
- [X] T033 Run the full suite `python -m pytest tests/ -x -q` and confirm it is green
- [X] T034 Walk the offline scenarios in [quickstart.md](./quickstart.md) (rows 1–17) and confirm each maps to a passing test or an observed behavior
- [X] T035 Assess the live Cockpit checks in [quickstart.md](./quickstart.md) (rows 18–22). The generated Models-view KQL was executed against `managed-appi-agentops-dev-ws`, linked to `appi-agentops-dev`; the first pass caught and corrected an invalid `startswith` expression. A temporary Foundry prompt agent (`agentops-token-telemetry-dummy` v1) then invoked `gpt-5-nano` with Code Interpreter, and two controlled OpenTelemetry spans exercised canonical aliases, fallback aliases, first-present-wins, and seven passthrough attributes. The live query returned input `1200`, output `600`, cache-read `122`, cache-write `244`, and reasoning `366`; normalization reported all three granular classes, retained the first five sorted passthrough classes, and marked truncation. Live validation also showed that Log Analytics returns the dynamic passthrough bag as JSON text, so `normalize_model_row()` now decodes that production shape before applying the cap.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup — **blocks all user stories**
- **User Story 1 (Phase 3)**: depends on Phase 2 only
- **User Story 2 (Phase 4)**: depends on Phase 2. Reads `token_classes_partial` semantics established in US1 for consistency, but the coverage entry itself is independently testable
- **User Story 3 (Phase 5)**: depends on Phase 2. Extends the same query builder and the same normalizer as US1, so if US1 and US3 run concurrently they will conflict in `queries.py` and `service.py` — sequence them or coordinate the merge
- **Polish (Phase 6)**: depends on every story that is being shipped

### File-level conflicts (why some tasks are not [P])

| File | Tasks touching it |
|---|---|
| `src/agentops/agent/observe/queries.py` | T003, T010, T024 |
| `src/agentops/core/observe.py` | T004 |
| `src/agentops/agent/observe/service.py` | T011, T017, T018, T019, T025, T026 |
| `src/agentops/agent/observe/ui.py` | T012, T013, T027 |
| `tests/unit/test_observe_service.py` | T006, T008, T016, T022 |
| `tests/unit/test_observe_queries.py` | T005, T007, T023, T029 |
| `tests/unit/test_observe_ui.py` | T009, T030 |

Tasks in the same row must not run concurrently.

### Parallel Opportunities

- T005 and T006 run in parallel (different test files)
- T007, T008, T009 run in parallel (three different test files)
- T022 and T023 run in parallel (different test files)
- T029 and T030 run in parallel (different test files)
- With multiple developers, US2 (Phase 4) can run alongside US1 (Phase 3) because its
  implementation tasks are confined to `service.py` coverage functions that US1 does not
  touch — coordinate on `normalize_model_row` only

---

## Parallel Example: User Story 1

```bash
# Write all three failing test files together:
Task: "T007 query projection tests in tests/unit/test_observe_queries.py"
Task: "T008 normalization tests in tests/unit/test_observe_service.py"
Task: "T009 rendering tests in tests/unit/test_observe_ui.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup → green baseline
2. Phase 2 Foundational → alias table + contract fields (blocks everything)
3. Phase 3 User Story 1 → per-class breakdown on the row
4. **STOP and VALIDATE**: quickstart rows 1–8 against a source that reports at least one
   granular class
5. Shippable — an operator can reason about burn-down without the coverage work

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. Add US1 → validate → ship (MVP)
3. Add US2 → validate → ship (missing classes become explained instead of blank)
4. Add US3 → validate → ship (new vendor families onboard without query edits)

Each increment leaves the agents view and the combined usage view untouched.

---

## Notes

- `[P]` means different files and no dependency on an incomplete task
- Commit after each task or logical group; every checkpoint is a valid stopping point
- Confirm each test fails before implementing the task it covers
- The design is validated offline against synthetic rows; no Azure credentials are required
  for any task except T035
- The `bag_keys(Properties)` passthrough in T024 was executed against a live Azure Monitor
  workspace in T035, including seven additional classes and the five-class display cap.

---

## Phase 7: Convergence

**Purpose**: Close the remaining specification, plan, and constitutional gaps found after implementation.

- [X] T036 CRITICAL Document the user-visible granular token behavior in `docs/how-it-works.md`, including the three normalized classes, missing-versus-zero semantics, row and coverage-panel partial reporting, the five-class unnormalized passthrough cap, and truncation signaling, per Constitution §Development Workflow (missing)
- [X] T037 CRITICAL Add an Unreleased entry to `CHANGELOG.md` describing the user-visible Models-view granular token classes, partial coverage, and bounded passthrough behavior, per Constitution §Development Workflow (missing)
- [X] T038 Preserve intermittent per-class reporting across aggregated telemetry records in `src/agentops/agent/observe/queries.py`, `src/agentops/agent/observe/service.py`, and the `ModelUsage` row indication, so class totals include only reporting records while both row-level and source-level coverage remain partial when any qualifying record omits a class; add focused regressions in `tests/unit/test_observe_queries.py`, `tests/unit/test_observe_service.py`, and `tests/unit/test_observe_ui.py`, per FR-011, FR-012, FR-022 and the mixed-record Edge Case (partial)
- [X] T039 Add an end-to-end granular-token Models-view scenario to `tests/integration/test_observe_end_to_end.py` that exercises normalized values, missing-versus-zero rendering, partial coverage, passthrough retention, and truncation through the Cockpit response/rendering flow, per plan: Testing (missing)
