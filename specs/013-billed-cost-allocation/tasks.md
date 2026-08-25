---

description: "Implementation tasks for billed cost allocation"
---

# Tasks: Billed Cost Allocation

**Input**: Design documents from `/specs/013-billed-cost-allocation/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`,
`contracts/`, and `quickstart.md`

**Tests**: Required by the feature plan and constitution. Add each focused test
before the implementation it specifies and confirm the new assertion fails for
the intended reason.

**Organization**: Tasks are grouped by user story so each story can be
implemented and validated as an incremental operator outcome.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it changes a different file and does not
  depend on another incomplete task in the same phase
- **[Story]**: User story from `spec.md`
- Every task includes an exact repository-relative file path

## Phase 1: Setup (Shared Test Infrastructure)

**Purpose**: Establish reusable cost-model and telemetry fixtures without adding
runtime dependencies.

- [X] T001 [P] Extend the shared Observe telemetry factories with bounded agent, model, tool, run, granular-token, duration, and direct-credit rows in tests/fixtures/observe.py
- [X] T002 [P] Add reusable valid, invalid, overlapping-period, mixed-currency, fallback, and secret-shaped cost-model payload factories in tests/fixtures/cost.py

**Checkpoint**: Cost scenarios can be assembled consistently across unit and
integration tests.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Implement the strict pure contracts and additive Observe types used
by every cost story.

**CRITICAL**: No user story implementation begins until this phase is complete.

- [X] T003 [P] Add failing contract tests for versioning, 32 KiB and cardinality bounds, decimal precision, intervals, overlap detection, unique IDs, selector normalization, compatibility rules, credit-event operations, and secret-shaped field rejection in tests/unit/test_cost_models.py
- [X] T004 [P] Add failing additive-contract tests for the cost view, identifier-only cost filters including `cost_agent_key`, granular run usage, null-versus-zero behavior, and allocation-key cost coverage context in tests/unit/test_observe_models.py
- [X] T005 Implement strict Pydantic v2 cost configuration, load-result, usage, allocation-row, component-summary, currency-subtotal, and view-data contracts in src/agentops/core/cost.py
- [X] T006 Implement bounded environment parsing, canonical serialization, deterministic fingerprinting, cross-period overlap checks, component compatibility validation, and non-sensitive error codes in src/agentops/core/cost.py
- [X] T007 Extend Observe view, cost-period/component/breakdown/agent filter, run, and allocation-key coverage contracts additively without changing existing non-cost defaults or scope validation in src/agentops/core/observe.py
- [X] T008 Run and fix the foundational contract tests while preserving the schemas in specs/013-billed-cost-allocation/contracts/cost-model.schema.json and specs/013-billed-cost-allocation/contracts/observe-cost-api.openapi.yaml

**Checkpoint**: Cost configuration and response contracts are pure, strict,
bounded, and usable without Azure dependencies.

---

## Phase 3: User Story 1 - Explain Billed Spend by Agent (Priority: P1) MVP

**Goal**: Allocate each declared billed component across observed agents using
its compatible usage key and display exact, currency-safe agent allocations.

**Independent Test**: Configure one period with at least two components and two
agents, query the agent cost breakdown, and verify each row shows its usage share
while every component reconciles exactly to its declared total.

### Tests for User Story 1

- [X] T009 [P] [US1] Add failing agent-allocation tests for weighted and total tokens, tool invocations, duration, direct credits, explicit credit-event fallback, deterministic largest remainder, zero totals, and separate currencies in tests/unit/test_cost_allocation.py
- [X] T010 [P] [US1] Add failing run-query projection tests for granular token classes, operation names, direct credits, credit-event counts, period boundaries, bounded rows, and protected-content exclusion in tests/unit/test_observe_queries.py
- [X] T011 [P] [US1] Add failing service tests for one-time models/tools/runs collection, usage matching, agent grouping, cache fingerprinting, component reconciliation, and row-bound summaries in tests/unit/test_observe_service.py
- [X] T012 [P] [US1] Add failing facade tests for `view: cost`, authoritative configured-period semantics, ignored shared Observe filters, configured component validation, post-allocation `cost_agent_key`, default agent breakdown, serialized `CostViewData`, and unchanged non-cost requests in tests/unit/test_observe_facade.py
- [X] T013 [P] [US1] Add failing server-rendered and JavaScript-rendered agent Cost view tests for navigation, selectors, currency grouping, usage shares, and exact component summaries in tests/unit/test_observe_ui.py
- [X] T014 [P] [US1] Add a failing `AGENTOPS_COST_MODEL` startup journey that opens a valid agent allocation with fake telemetry in tests/integration/test_observe_end_to_end.py

### Implementation for User Story 1

- [X] T015 [P] [US1] Extend the bounded runs projection with period-scoped granular tokens, operation names, direct credits, and credit-event counts in src/agentops/agent/observe/queries.py
- [X] T016 [P] [US1] Implement Decimal usage matching, weighted numerators, explicit fallback selection, minor-unit largest-remainder allocation, stable tie-breaking, and currency-safe subtotals in src/agentops/agent/observe/cost_allocation.py
- [X] T017 [US1] Normalize the additive run fields into protected-content-free cost usage observations while preserving null versus reported zero in src/agentops/agent/observe/service.py
- [X] T018 [US1] Compose each required bounded models, tools, and runs view at most once and dispatch the agent breakdown through the allocation engine in src/agentops/agent/observe/service.py
- [X] T019 [US1] Validate cost period, component, breakdown, and agent selectors; ignore shared Observe filters for cost calculation; include the model fingerprint in cache identity; and serialize the response through the existing query facade in src/agentops/agent/observe/facade.py
- [X] T020 [US1] Load optional `AGENTOPS_COST_MODEL` once into absent/valid/invalid startup state, inject valid configuration into the Observe facade, hide Cost when absent, and isolate invalid configuration from non-cost routes in src/agentops/agent/cockpit.py
- [X] T021 [US1] Add the Cost navigation item, period/component/breakdown/agent controls, agent allocation table, currency subtotals, and matching JavaScript refresh rendering that omits shared Observe filters in src/agentops/agent/observe/ui.py

**Checkpoint**: The agent breakdown independently answers which agents consumed
each declared billed pool and reconciles every component exactly.

---

## Phase 4: User Story 2 - Find the Tools and Runs Driving Cost (Priority: P2)

**Goal**: Provide tool and run alternatives to the agent breakdown without
double-counting the shared billed pools.

**Independent Test**: With two tools and two correlated runs, verify tool spend
uses invocation share, each run component uses its declared key, missing
tool/run identities remain unattributed, and switching breakdowns never adds the
same pool twice.

### Tests for User Story 2

- [X] T022 [P] [US2] Add failing tool/run allocation tests for invocation, token, duration, credit, and unattributed consumer grouping in tests/unit/test_cost_allocation.py
- [X] T023 [P] [US2] Add failing service tests for alternate tool and run compositions, run-boundary clipping, and no duplicate underlying view query in tests/unit/test_observe_service.py
- [X] T024 [P] [US2] Add failing facade tests for `tools` and `runs` cost breakdown selectors, `cost_agent_key` post-allocation filtering, preserved omitted amounts, and invalid breakdown/component combinations in tests/unit/test_observe_facade.py
- [X] T025 [P] [US2] Add failing UI tests for tool/run tables, two-interaction drill-down, preserved filters, and the non-additive breakdown warning in tests/unit/test_observe_ui.py
- [X] T026 [P] [US2] Add failing end-to-end tool and run allocation journeys with correlated and uncorrelated activity in tests/integration/test_observe_end_to_end.py

### Implementation for User Story 2

- [X] T027 [US2] Extend the allocation engine with deterministic tool, run, and reserved unattributed consumer keys while preserving per-breakdown reconciliation in src/agentops/agent/observe/cost_allocation.py
- [X] T028 [US2] Compose tool-side and run-attributable observations from the existing normalized views and clip usage to the selected period in src/agentops/agent/observe/service.py
- [X] T029 [US2] Support tool/run selector validation, post-allocation agent filtering with omitted-amount reconciliation, and alternate-breakdown dispatch without summing across breakdowns in src/agentops/agent/observe/facade.py
- [X] T030 [US2] Render tool and run allocation tables, `cost_agent_key` drill-down links, unattributed buckets, and the fixed alternative-breakdown warning in both render paths in src/agentops/agent/observe/ui.py

**Checkpoint**: Tool and run breakdowns work independently as diagnostic views
of the same billed pools.

---

## Phase 5: User Story 3 - Trust How Every Amount Was Produced (Priority: P3)

**Goal**: Make every displayed allocation auditable through provenance,
allocation method, observed share, confidence, and freshness.

**Independent Test**: Inspect a mixed metered/commitment period and verify every
amount carries complete provenance, preferred/applied key, usage
numerator/denominator, deterministic confidence, calculation time, and latest
observation time.

### Tests for User Story 3

- [X] T031 [P] [US3] Add failing allocation tests for provenance propagation, preferred-versus-applied keys, fallback flags, rounding adjustments, and high/medium/low/unavailable confidence precedence in tests/unit/test_cost_allocation.py
- [X] T032 [P] [US3] Add failing service tests for source/project/runtime provenance, partial-period confidence reduction, and per-component cost-attribution coverage in tests/unit/test_observe_service.py
- [X] T033 [P] [US3] Add failing API serialization tests proving every displayed figure contains the OpenAPI-required provenance and freshness fields in tests/unit/test_observe_facade.py
- [X] T034 [P] [US3] Add failing Python/JavaScript rendering-parity tests for metered/commitment labels, fallback/partial explanations, observed-usage labels, freshness, and the operational-allocation disclaimer in tests/unit/test_observe_ui.py

### Implementation for User Story 3

- [X] T035 [US3] Populate complete row and component provenance, rounding evidence, calculation freshness, and deterministic confidence classification in src/agentops/agent/observe/cost_allocation.py
- [X] T036 [US3] Merge query coverage, readable-period completeness, attribution completeness, and latest observation timestamps into cost results in src/agentops/agent/observe/service.py
- [X] T037 [US3] Preserve the typed provenance and coverage envelope through existing response serialization and diagnostics in src/agentops/agent/observe/facade.py
- [X] T038 [US3] Render provenance details, method/confidence badges, inline fallback and partial-coverage reasons, observed-usage labels, freshness, and the fixed disclaimer in src/agentops/agent/observe/ui.py

**Checkpoint**: Every amount is independently auditable and cannot be mistaken
for an invoice or billing-accurate charge.

---

## Phase 6: User Story 4 - Configure Allocation Without Expanding Privilege (Priority: P4)

**Goal**: Load and optionally deploy the non-secret cost model while keeping
other Cockpit views available and adding no role, resource, billing read, or
runtime mutation capability.

**Independent Test**: Start with valid, invalid, and absent
`AGENTOPS_COST_MODEL` values and verify valid enables Cost, invalid blocks only
Cost with an actionable error, absent restores existing Observe behavior, and
deployment preview adds only the optional non-secret setting.

### Tests for User Story 4

- [X] T039 [P] [US4] Add failing local/hosted parity tests for malformed, oversized, overlapping, incompatible, removed, and secret-shaped cost models plus actionable non-sensitive errors in tests/unit/test_cockpit_modes.py
- [X] T040 [P] [US4] Add failing deployment preview tests for optional cost-model propagation, exact redaction behavior, unchanged Reader roles, and rejection of secret-shaped settings in tests/unit/test_cockpit_deployment_preview.py
- [X] T041 [P] [US4] Add failing hosted-template tests for the optional Bicep parameter and Web App setting with no new resource or role assignment in tests/unit/test_cockpit_hosted_templates.py
- [X] T042 [P] [US4] Add failing route-level tests for absent and invalid direct cost requests plus unchanged overview/agents/models/tools/runs/coverage requests in tests/unit/test_cockpit.py

### Implementation for User Story 4

- [X] T043 [US4] Complete local/hosted cost-model parity, restart/removal behavior, 32 KiB enforcement, and actionable non-sensitive validation errors on the startup path established by T020 in src/agentops/agent/cockpit.py
- [X] T044 [P] [US4] Add `AGENTOPS_COST_MODEL` to the established non-secret setting allowlist, preview, azd environment values, and deployment application settings without changing role plans in src/agentops/services/cockpit_deployment.py
- [X] T045 [P] [US4] Add the optional cost-model parameter and conditional Web App application setting without adding resources or role assignments in src/agentops/templates/cockpit-hosted/infra/main.bicep
- [X] T046 [US4] Add the azd substitution for the optional cost-model parameter in src/agentops/templates/cockpit-hosted/infra/main.parameters.json
- [X] T047 [US4] Extend the end-to-end fixture with absent and invalid configuration non-regression assertions in tests/integration/test_observe_end_to_end.py

**Checkpoint**: Cost configuration is stateless and fail-closed while deployment
and runtime privileges remain unchanged.

---

## Phase 7: User Story 5 - Understand Missing or Unallocatable Cost (Priority: P5)

**Goal**: Keep every configured or observed component visible with truthful
unattributed, unallocated, partial, or not-configured explanations.

**Independent Test**: Exercise every missing-data state and verify the component
remains visible with a reason and next action, missing data never becomes zero,
and partial readable usage lowers confidence without hiding readable results.

### Tests for User Story 5

- [X] T048 [P] [US5] Add failing allocation tests for zero denominator, missing preferred and fallback keys, explicit unattributed buckets, fully unallocated totals, and 500-row omitted-amount reconciliation in tests/unit/test_cost_allocation.py
- [X] T049 [P] [US5] Add failing service tests for inaccessible sources, telemetry not configured, no period data, key not reported, partial keys, incomplete attribution, observable allocation capabilities unmatched by configuration, prohibition on inferred billing types, and partial source success in tests/unit/test_observe_service.py
- [X] T050 [P] [US5] Add failing facade tests for explained partial responses, bounded rows, omitted amounts, and missing totals represented as `not_configured` rather than zero in tests/unit/test_observe_facade.py
- [X] T051 [P] [US5] Add failing UI tests for unavailable components, missing-versus-zero rendering, unattributed/unallocated totals, coverage reasons, next actions, and omitted-row notices in tests/unit/test_observe_ui.py
- [X] T052 [P] [US5] Add failing end-to-end scenarios covering mixed currencies, all four confidence states, unattributed usage, fully unallocated components, and partial telemetry sources in tests/integration/test_observe_end_to_end.py

### Implementation for User Story 5

- [X] T053 [US5] Preserve configured components with no usable denominator, calculate unattributed and omitted allocations separately, and enforce component summary invariants in src/agentops/agent/observe/cost_allocation.py
- [X] T054 [US5] Classify every cost-attribution coverage state with a concise reason and next action while retaining successful components after partial source failures in src/agentops/agent/observe/service.py
- [X] T055 [US5] Return explained partial cost responses with bounded rows and complete component summaries through the existing envelope in src/agentops/agent/observe/facade.py
- [X] T056 [US5] Render unavailable, unattributed, unallocated, and truncated states without coercing missing values to zero in both Cost render paths in src/agentops/agent/observe/ui.py

**Checkpoint**: Missing and partial inputs remain explicit, actionable, and
mathematically reconciled.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Complete user-facing guidance, release notes, security review, and
full validation across all stories.

- [X] T057 [P] Document cost-model configuration, allocation compatibility, confidence, coverage, currency behavior, non-additive breakdowns, and read-only limitations in docs/observe.md
- [X] T058 [P] Add the user-visible billed-cost allocation entry under the current release in CHANGELOG.md
- [X] T059 Audit cost query projections, serialized payloads, cache keys, diagnostics, and rendered URLs for protected content and raw cost-model leakage across src/agentops/agent/observe/queries.py, src/agentops/agent/observe/service.py, src/agentops/agent/observe/facade.py, and src/agentops/agent/observe/ui.py
- [X] T060 Run every focused command and expected outcome in specs/013-billed-cost-allocation/quickstart.md, then run `python -m pytest tests/ -x -q` and resolve regressions without changing the exit-code or read-only contracts

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup and blocks all user stories.
- **US1 (Phase 3)**: Starts after Foundational and establishes the MVP cost
  pipeline.
- **US2 (Phase 4)**: Depends on the US1 allocation/service/facade pipeline, then
  adds alternate tool and run grains.
- **US3 (Phase 5)**: Depends on US1 response rows and can run in parallel with US2.
- **US4 (Phase 6)**: Runtime and deployment work can begin after Foundational;
  route integration completes against the US1 pipeline.
- **US5 (Phase 7)**: Depends on the US1 allocation/service pipeline and can run
  in parallel with US2 and US3.
- **Polish (Phase 8)**: Depends on all selected user stories.

### User Story Dependency Graph

```text
Setup -> Foundational -> US1 (MVP)
                         |-> US2
                         |-> US3
                         |-> US5
         Foundational -> US4 --integrates with-> US1
US1 + US2 + US3 + US4 + US5 -> Polish
```

### Within Each User Story

1. Add the listed tests and confirm their new assertions fail.
2. Implement pure calculation or contract behavior.
3. Integrate service composition.
4. Wire facade/routes.
5. Complete both server and JavaScript render paths.
6. Run the story's focused tests and validate its checkpoint independently.

### Parallel Opportunities

- T001 and T002 can run in parallel.
- T003 and T004 can run in parallel.
- Test tasks within each user-story phase can run in parallel because they touch
  distinct test files.
- T015 and T016 can run in parallel after Foundational.
- US2, US3, and US5 can proceed in parallel after US1.
- US4 deployment tasks T044 and T045 can run in parallel with runtime work.
- Documentation T057 and changelog T058 can run in parallel.

---

## Parallel Example: User Story 1

```text
Task T009: Add agent allocation tests in tests/unit/test_cost_allocation.py
Task T010: Add run projection tests in tests/unit/test_observe_queries.py
Task T011: Add agent composition tests in tests/unit/test_observe_service.py
Task T012: Add cost facade tests in tests/unit/test_observe_facade.py
Task T013: Add Cost renderer tests in tests/unit/test_observe_ui.py
Task T014: Add the agent end-to-end journey in tests/integration/test_observe_end_to_end.py
```

## Parallel Example: User Story 2

```text
Task T022: Add tool/run allocation tests in tests/unit/test_cost_allocation.py
Task T023: Add alternate composition tests in tests/unit/test_observe_service.py
Task T024: Add breakdown-selector tests in tests/unit/test_observe_facade.py
Task T025: Add tool/run UI tests in tests/unit/test_observe_ui.py
Task T026: Add tool/run integration journeys in tests/integration/test_observe_end_to_end.py
```

## Parallel Example: User Story 3

```text
Task T031: Add provenance and confidence tests in tests/unit/test_cost_allocation.py
Task T032: Add coverage provenance tests in tests/unit/test_observe_service.py
Task T033: Add response contract tests in tests/unit/test_observe_facade.py
Task T034: Add renderer parity tests in tests/unit/test_observe_ui.py
```

## Parallel Example: User Story 4

```text
Task T039: Add runtime configuration tests in tests/unit/test_cockpit_modes.py
Task T040: Add deployment preview tests in tests/unit/test_cockpit_deployment_preview.py
Task T041: Add hosted template tests in tests/unit/test_cockpit_hosted_templates.py
Task T042: Add route isolation tests in tests/unit/test_cockpit.py
```

## Parallel Example: User Story 5

```text
Task T048: Add missing-data allocation tests in tests/unit/test_cost_allocation.py
Task T049: Add coverage classification tests in tests/unit/test_observe_service.py
Task T050: Add partial-response tests in tests/unit/test_observe_facade.py
Task T051: Add missing-state UI tests in tests/unit/test_observe_ui.py
Task T052: Add missing-data integration journeys in tests/integration/test_observe_end_to_end.py
```

---

## Implementation Strategy

### MVP First: User Story 1

1. Complete Setup and Foundational contracts.
2. Implement US1 through the existing Observe route and both render paths.
3. Run the focused US1 tests and its valid-configuration end-to-end fixture.
4. Stop and demonstrate exact agent allocation before adding drill-down or
   advanced coverage behavior.

### Incremental Delivery

1. **Foundation**: Strict cost configuration and additive Observe contracts.
2. **US1**: Agent breakdown answers the primary billed-spend question.
3. **US2**: Tool and run alternatives explain cost drivers.
4. **US3**: Full provenance and confidence make amounts auditable.
5. **US4**: Startup/deployment configuration completes the least-privilege
   operator workflow.
6. **US5**: Missing and partial states complete truthful coverage.
7. **Polish**: Documentation, security boundary review, quickstart, and full suite.

### Parallel Team Strategy

After Setup and Foundational:

- One developer completes the US1 pipeline.
- A deployment-focused developer can begin US4 tests and templates in parallel.
- After US1, separate developers can own US2, US3, and US5 because their tests
  and primary implementation concerns are distinct.
- Merge shared-file changes in priority order to avoid conflicts in
  `cost_allocation.py`, `service.py`, `facade.py`, and `ui.py`.

## Notes

- All allocation arithmetic uses `Decimal` and integer minor units.
- No task adds a billing API, database, Azure role, credential, CLI command, or
  cloud mutation path.
- Agent, tool, and run results are alternative reconciliations of the same
  component, never additive.
- Missing, unreported, unattributed, unallocated, and reported zero remain
  distinct throughout contracts, services, and rendering.

## Phase 9: Convergence

- [X] T061 Make Cost component choices period-aware, clear stale component and agent selectors before submitting a changed period, and add valid multi-period startup, UI, and integration selector round-trip coverage in src/agentops/agent/cockpit.py, src/agentops/agent/observe/ui.py, tests/unit/test_cockpit_modes.py, tests/unit/test_observe_ui.py, and tests/integration/test_observe_end_to_end.py per US4/AC1 and plan: UI selector round-trip (partial)
