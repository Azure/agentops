---

description: "Implementation tasks for privacy-safe user and department usage and cost attribution"
---

# Tasks: User and Department Usage and Cost Attribution

**Input**: Design documents from `specs/001-user-department-attribution/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Required by the feature plan and AgentOps constitution. Write each phase's tests first and confirm that they fail for the intended missing behavior before implementing that phase.

**Organization**: Tasks are grouped by user story so department attribution, protected user investigation, and coverage diagnostics remain independently reviewable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it changes different files and does not depend on another incomplete task in the same phase
- **[Story]**: Maps implementation work to User Story 1, 2, or 3
- Every task names the exact repository path it changes or validates

## Phase 1: Setup (Shared Preparation)

**Purpose**: Close the formal privacy gate and establish reusable synthetic fixtures before implementation.

- [X] T001 Resolve every blocking finding in `specs/001-user-department-attribution/checklists/privacy.md`, recording clarifications in `specs/001-user-department-attribution/spec.md`, `plan.md`, `data-model.md`, `contracts/attribution-config.schema.json`, and `contracts/observe-attribution-api.openapi.yaml` before coding
- [X] T002 Add synthetic authenticated identities, conflicting aliases, department mappings, group-overage principals, singleton departments, and high-cardinality attribution rows to `tests/fixtures/observe.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Create the strict shared contracts, deterministic derivation primitives, and opt-in startup state required by every user story.

**CRITICAL**: No user-story implementation begins until this phase passes its focused tests.

### Foundational tests

- [X] T003 [P] Add failing tests for configuration size/cardinality/global uniqueness, load states, canonical fingerprints, pseudonym stability/separation, mapping resolution primitives, and scope/config/principal-bound token validation in `tests/unit/test_attribution_models.py`
- [X] T004 [P] Add failing tests for nullable attribution filters, request/response unions, `ambiguous` coverage, `user_attribution` details, strict extra-field rejection, and 500-row bounds in `tests/unit/test_observe_models.py`
- [X] T005 [P] Add failing tests for additive `department`, `user`, `other_users`, and `unattributed` cost consumer kinds without changing existing allocation contracts in `tests/unit/test_cost_models.py`
- [X] T006 [P] Add failing tests for absent, disabled, valid-empty, valid-mapped, and invalid attribution startup states plus disabled-mode parity in `tests/unit/test_cockpit_modes.py`

### Foundational implementation

- [X] T007 Implement strict `AttributionConfiguration`, `DepartmentDefinition`, bounded parsing, global uniqueness checks, load states, and redacted error representations in `src/agentops/core/attribution.py`
- [X] T008 Implement canonical config/scope fingerprints, full SHA-256 pseudonymous keys, department and principal-bound user token codecs, mapping-resolution contracts, and attribution row/summary models in `src/agentops/core/attribution.py`
- [X] T009 Extend `ObserveFilterState`, `CoverageState`, `CoverageResult`, bounds, and additive attribution request/response envelopes in `src/agentops/core/observe.py`
- [X] T010 [P] Extend cost consumer contracts for department, user, Other-users, and unattributed outcomes without changing existing totals or allocation semantics in `src/agentops/core/cost.py`
- [X] T011 Load `AGENTOPS_ATTRIBUTION_CONFIG` once at Cockpit startup, isolate invalid attribution configuration from existing views, and expose only non-sensitive enablement metadata in `src/agentops/agent/cockpit.py`

**Checkpoint**: Pure contracts load without Azure dependencies; attribution remains absent when unconfigured; all foundational tests pass.

---

## Phase 3: User Story 1 - Attribute Shared Consumption by Department (Priority: P1) - MVP

**Goal**: Provide opt-in department usage and selected-component cost grouping/filtering with exact reconciliation, safe aggregate access, explicit mapping precedence, and least-privilege deployment preview.

**Independent Test**: With synthetic eligible telemetry and department mappings, department usage and configured cost groupings filter by department, reconcile exactly with unfiltered totals, preserve unattributed evidence, use aggregate access only for non-singleton results, and leave existing behavior unchanged when attribution is disabled.

### Tests for User Story 1

- [X] T012 [P] [US1] Add failing KQL-builder tests for existing filters, the two eligible identity aliases, conflict classification, KQL-side pseudonym derivation, one bounded mapping `datatable`, and raw-identity-free aggregate projection in `tests/unit/test_observe_queries.py`
- [X] T013 [P] [US1] Add failing adapter tests for normalized department rows, nullable usage fields, source attribution, and rejection of raw identity in aggregate results in `tests/unit/test_observe_adapters.py`
- [X] T014 [P] [US1] Add failing service tests for explicit-user precedence, unmapped/ambiguous buckets, department filters, exact usage reconciliation, bounded query count, and partial-source preservation in `tests/unit/test_observe_service.py`
- [X] T015 [P] [US1] Add failing principal tests for exact signed-in-user matching, cross-user group prohibition, multiple mapped groups, missing groups, and group-claim overage without directory lookup in `tests/unit/test_observe_principal.py`
- [X] T016 [P] [US1] Add failing cost tests for one-period/one-component department grouping, post-allocation department filtering, unchanged denominators/amounts, and declared-total reconciliation in `tests/unit/test_cost_allocation.py`
- [X] T017 [P] [US1] Add failing route tests for disabled/invalid/valid attribution states, strict request validation, safe aggregate responses, and unchanged non-attribution routes in `tests/unit/test_cockpit.py`
- [X] T018 [P] [US1] Add failing UI tests for opt-in Department controls, department tokens in URLs, unmapped totals, cost-unavailable explanations, and server/JavaScript parity in `tests/unit/test_observe_ui.py`
- [X] T019 [P] [US1] Add failing preview/template tests for redacted `AGENTOPS_ATTRIBUTION_CONFIG`, the delegated-data warning, declined confirmation, unchanged resources/roles, and conditional App Service propagation in `tests/unit/test_cockpit_deployment_preview.py` and `tests/unit/test_cockpit_hosted_templates.py`
- [X] T020 [P] [US1] Add a failing department-attribution integration scenario covering disabled parity, valid mappings, filters, exact usage/cost totals, and partial source failure in `tests/integration/test_observe_end_to_end.py`

### Implementation for User Story 1

- [X] T021 [US1] Implement pure explicit/group mapping precedence, ambiguity handling, safe department cardinality classification, and exact usage reconciliation in `src/agentops/agent/observe/attribution.py`
- [X] T022 [US1] Add dedicated aggregate department usage KQL builders with eligible-identity normalization, conflict counters, pseudonym derivation, mapping joins, and existing-filter composition in `src/agentops/agent/observe/queries.py`
- [X] T023 [US1] Normalize aggregate attribution rows and prevent raw identity from crossing the aggregate adapter boundary in `src/agentops/agent/observe/adapters.py`
- [X] T024 [P] [US1] Preserve validated group claims and group-overage context while keeping access-token values redacted from diagnostic output in `src/agentops/agent/observe/principal.py`
- [X] T025 [US1] Compose per-source department usage, unmapped totals, safe cardinality, partial failures, and basic attribution coverage in `src/agentops/agent/observe/service.py`
- [X] T026 [P] [US1] Extend declared-total allocation for department and unattributed consumers while applying department filters only after full-period allocation in `src/agentops/agent/observe/cost_allocation.py`
- [X] T027 [US1] Add department attribution dispatch, scope/config/token validation, uncached singleton classification, and safe aggregate cache keys to `src/agentops/agent/observe/facade.py`
- [X] T028 [US1] Expose authenticated `POST /api/observe/attribution` with absent/disabled/invalid error mapping and aggregate response semantics in `src/agentops/agent/cockpit.py`
- [X] T029 [P] [US1] Add Department navigation, usage/cost selectors, opaque department URL state, summaries, and identical server/JavaScript rendering in `src/agentops/agent/observe/ui.py`
- [X] T030 [P] [US1] Add attribution-setting validation, redacted preview metadata, delegated-boundary warning, normal confirmation, and azd environment propagation in `src/agentops/services/cockpit_deployment.py`
- [X] T031 [US1] Add the optional attribution parameter and conditional Web App setting without changing resources or role assignments in `src/agentops/templates/cockpit-hosted/infra/main.bicep` and `src/agentops/templates/cockpit-hosted/infra/main.parameters.json`
- [X] T032 [US1] Run the User Story 1 focused commands documented in `specs/001-user-department-attribution/quickstart.md` and correct department-attribution regressions in the files changed by T012-T031

**Checkpoint**: Department attribution is a complete MVP: independently testable, exactly reconciled, disabled by default, and least privilege.

---

## Phase 4: User Story 2 - Investigate Individual Consumption Safely (Priority: P2)

**Goal**: Add delegated-only user listing and drilldown, singleton escalation, privacy-safe URL tokens, mapping bootstrap, and deterministic 499-plus-Other bounds.

**Independent Test**: A synthetic authorized principal can inspect and filter individual usage/cost only through fresh OBO access; copied/stale/foreign tokens fail closed; singleton departments rerun delegated; protected results are private/no-store and never shared-cached; more than 500 users return top 499 plus one exact Other-users row.

### Tests for User Story 2

- [X] T033 [P] [US2] Add failing token tests for semantic config reordering, mapping changes, generation rotation, scope changes, malformed values, cross-principal use, constant-time binding checks, and zero/multiple resolution in `tests/unit/test_attribution_models.py`
- [X] T034 [P] [US2] Add failing facade tests for fresh OBO-only user dispatch, singleton rerun, aggregate-result discard, missing assertion failure, no deployment-identity retry, cache bypass, and private/no-store classification in `tests/unit/test_observe_facade.py`
- [X] T035 [P] [US2] Add failing protected-query tests for delegated raw identity, pseudonym parity with aggregate KQL, deterministic user ranking, 499-plus-Other folding, and separate unattributed totals in `tests/unit/test_observe_queries.py`
- [X] T036 [P] [US2] Add failing service tests for user grouping/filtering, selected-user isolation, null-versus-zero preservation, omitted-user counts, and exact user/Other/unattributed reconciliation in `tests/unit/test_observe_service.py`
- [X] T037 [P] [US2] Add failing cost tests for user consumers, one selected pool/currency, final minor-unit ranking, post-allocation user filters, Other-users summation, and unchanged declared totals in `tests/unit/test_cost_allocation.py`
- [X] T038 [P] [US2] Add failing API tests for delegated user responses, `Cache-Control: private, no-store`, 422-style closed token failures, and protected/unavailable OBO failures in `tests/unit/test_cockpit.py`
- [X] T039 [P] [US2] Add failing UI tests for the delegated Users bootstrap view, raw-identity/pseudonym pairing, protected labels, opaque user URLs, browser-storage exclusion, truncation, and server/JavaScript parity in `tests/unit/test_observe_ui.py`
- [X] T040 [P] [US2] Add failing integration scenarios for user drilldown, copied tokens, singleton departments, missing OBO assertions, restart stability, explicit rotation, and more than 500 users in `tests/integration/test_observe_end_to_end.py` and `tests/integration/test_cockpit_hosted.py`

### Implementation for User Story 2

- [X] T041 [US2] Complete user-token issuance/validation and protected-row serialization so principal, tenant, scope, config, and generation mismatches fail before query execution in `src/agentops/core/attribution.py`
- [X] T042 [P] [US2] Add delegated user-list and selected-user KQL builders that return raw identity only on the protected path and produce deterministic bounded rows in `src/agentops/agent/observe/queries.py`
- [X] T043 [US2] Implement user grouping, deterministic top-499 ranking, one Other-users aggregate, omitted counts, and exact unattributed reconciliation in `src/agentops/agent/observe/attribution.py` and `src/agentops/agent/observe/service.py`
- [X] T044 [P] [US2] Extend allocation output for user and Other-users consumers without recalculating component denominators or rounded amounts in `src/agentops/agent/observe/cost_allocation.py`
- [X] T045 [US2] Implement fresh delegated credential construction, user-filter dispatch, singleton aggregate discard/rerun, protected failure handling, and unconditional shared-cache bypass in `src/agentops/agent/observe/facade.py`
- [X] T046 [US2] Apply private/no-store headers to every delegated attribution response and preserve safe status/error bodies without identity-bearing details in `src/agentops/agent/cockpit.py`
- [X] T047 [P] [US2] Implement Users bootstrap/drilldown controls, opaque token round-trip, raw identity display only in delegated views, and 499-plus-Other messaging in `src/agentops/agent/observe/ui.py`
- [X] T048 [US2] Run the User Story 2 focused commands documented in `specs/001-user-department-attribution/quickstart.md` and correct protected-view regressions in the files changed by T033-T047

**Checkpoint**: Individual investigation and mapping bootstrap are usable without weakening the aggregate identity, authorization, persistence, or cache boundaries.

---

## Phase 5: User Story 3 - Understand Attribution Coverage (Priority: P3)

**Goal**: Explain per-source user-attribution quality for usage and cost without hiding successful evidence or turning missing/inaccessible identity into zero consumption.

**Independent Test**: Synthetic complete, partial, absent, conflicting, inaccessible, protected, and failing sources each receive the correct per-source coverage state, counts, reason, and next action while successful attributed and unattributed evidence from other sources remains visible.

### Tests for User Story 3

- [X] T049 [P] [US3] Add failing adapter tests for eligible, identified, mapped, unattributed, ambiguous, and returned counters with null counts for inaccessible sources in `tests/unit/test_observe_adapters.py`
- [X] T050 [P] [US3] Add failing service tests for independent usage/cost coverage, all coverage states, per-component context, partial-source preservation, and no success-shaped zero grouping in `tests/unit/test_observe_service.py`
- [X] T051 [P] [US3] Add failing UI tests for coverage reasons, next actions, protected states, missing-versus-zero language, per-source/component labels, and server/JavaScript parity in `tests/unit/test_observe_ui.py`
- [X] T052 [P] [US3] Add failing end-to-end scenarios for complete, partial, not-reported, ambiguous, inaccessible, protected, and error coverage across multiple sources in `tests/integration/test_observe_end_to_end.py`

### Implementation for User Story 3

- [X] T053 [US3] Normalize attribution counters and source failures into strict `user_attribution` coverage records without identity-bearing values in `src/agentops/agent/observe/adapters.py`
- [X] T054 [US3] Merge per-source usage/cost coverage independently, retain successful attributed/unattributed evidence during partial failures, and distinguish absent data from reported zero in `src/agentops/agent/observe/service.py`
- [X] T055 [P] [US3] Render actionable attribution coverage and explained empty/protected states identically in server HTML and JavaScript refresh paths in `src/agentops/agent/observe/ui.py`
- [X] T056 [US3] Run the User Story 3 focused commands documented in `specs/001-user-department-attribution/quickstart.md` and correct coverage regressions in the files changed by T049-T055

**Checkpoint**: Every supported source communicates attribution quality explicitly and independently for usage and cost.

---

## Phase 6: Polish and Cross-Cutting Concerns

**Purpose**: Enforce privacy exclusions, document the operator lifecycle, and complete repository-wide validation.

- [X] T057 [P] Add failing regression tests that exclude mapping values, raw identities, user rows, group IDs, and filter tokens from Doctor/release evidence in `tests/unit/test_evidence_pack_agent_identity.py`
- [X] T058 Enforce attribution privacy exclusions while preserving aggregate readiness evidence in `src/agentops/services/evidence_pack.py`
- [X] T059 [P] Document enablement, mapping bootstrap, eligible identity, delegated access, coverage, privacy limitations, explicit rotation, and disablement in `docs/observe.md`
- [X] T060 [P] Add the user-visible attribution, privacy boundary, and least-privilege deployment changes under the next release section in `CHANGELOG.md`
- [X] T061 Run every scenario in `specs/001-user-department-attribution/quickstart.md`, then run `python -m pytest tests/ -x -q` and resolve only feature-related failures

---

## Dependencies and Execution Order

### Phase dependencies

- **Phase 1 (Setup)**: Starts immediately; T001 is a formal gate and must finish before fixtures or code are treated as authoritative.
- **Phase 2 (Foundational)**: Depends on Phase 1 and blocks all user-story implementation.
- **Phase 3 (US1)**: Depends on Phase 2 and delivers the recommended MVP plus the shared attribution endpoint.
- **Phase 4 (US2)**: Pure token, query, service, and UI work can be prepared after Phase 2; route integration and completion depend on the US1 endpoint from T028.
- **Phase 5 (US3)**: Coverage adapters and UI work can be prepared after Phase 2; endpoint integration and completion depend on US1 response composition from T025 and T028.
- **Phase 6 (Polish)**: Depends on every story selected for the release.

### User-story dependency graph

```text
Phase 1 Setup
    |
Phase 2 Foundation
    |
    +--> US1 Department attribution (MVP / shared endpoint)
            |
            +--> US2 Protected user investigation
            |
            +--> US3 Attribution coverage
                     |
              Phase 6 Polish
```

- **US1 (P1)**: First deliverable; independently demonstrates privacy-safe department usage/cost allocation and deployment enablement.
- **US2 (P2)**: Reuses the additive attribution endpoint but remains independently testable with delegated-user and singleton fixtures.
- **US3 (P3)**: Reuses the normalized attribution envelope but remains independently testable with per-source coverage fixtures.
- US2 and US3 may proceed in parallel after the US1 endpoint contract and foundation are stable.

### Within each phase

- Write the listed tests and confirm their intended failures before implementation tasks.
- Implement pure contracts before Azure/KQL adapters.
- Implement query/adapter primitives before service composition.
- Implement service composition before facade/route integration.
- Update both server-rendered and JavaScript UI paths in the same story.
- Run the story checkpoint before starting the next priority.

## Parallel Opportunities

- Foundation tests T003-T006 can run concurrently; T010 can proceed independently once its cost-contract test exists.
- US1 tests T012-T020 target separate seams and can run concurrently. Principal work T024, cost work T026, UI work T029, and deployment work T030 can proceed in parallel after foundational contracts stabilize.
- US2 tests T033-T040 can run concurrently. Protected KQL T042, cost work T044, and UI work T047 can proceed in parallel before facade/route integration.
- US3 tests T049-T052 can run concurrently. UI rendering T055 can proceed in parallel with adapter/service work.
- Evidence tests, documentation, and changelog tasks T057, T059, and T060 can run in parallel after story contracts stabilize.

## Parallel Example: User Story 1

```text
Task T012: Define aggregate attribution KQL expectations in tests/unit/test_observe_queries.py
Task T015: Define principal/group-claim expectations in tests/unit/test_observe_principal.py
Task T016: Define department cost invariants in tests/unit/test_cost_allocation.py
Task T019: Define deployment preview/template invariants in deployment unit tests
```

## Parallel Example: User Story 2

```text
Task T034: Define OBO, singleton, and cache-boundary behavior in tests/unit/test_observe_facade.py
Task T035: Define protected user-query and 499-plus-Other behavior in tests/unit/test_observe_queries.py
Task T037: Define post-allocation user cost behavior in tests/unit/test_cost_allocation.py
Task T039: Define protected user UI and URL behavior in tests/unit/test_observe_ui.py
```

## Parallel Example: User Story 3

```text
Task T049: Define coverage normalization in tests/unit/test_observe_adapters.py
Task T050: Define coverage composition in tests/unit/test_observe_service.py
Task T051: Define coverage presentation in tests/unit/test_observe_ui.py
Task T052: Define multi-source coverage outcomes in tests/integration/test_observe_end_to_end.py
```

## Implementation Strategy

### MVP first: User Story 1

1. Complete the privacy gate and synthetic fixtures in Phase 1.
2. Complete and validate all shared contracts in Phase 2.
3. Complete department attribution and deployment enablement in Phase 3.
4. Stop and run the US1 independent test before adding protected user views.

### Incremental delivery

1. **Foundation**: strict opt-in configuration, pseudonyms, tokens, and additive contracts.
2. **US1 MVP**: department usage/cost grouping and filtering with safe aggregate access.
3. **US2**: delegated user investigation, bootstrap, singleton escalation, and high-cardinality bounds.
4. **US3**: complete per-source coverage and troubleshooting.
5. **Polish**: evidence exclusions, operator documentation, changelog, and full-suite validation.

### Parallel team strategy

1. One maintainer closes T001 while another prepares synthetic fixtures.
2. After Phase 2, one developer owns department query/service work, one owns deployment/UI work, and one prepares US2/US3 tests against the stable contracts.
3. US2 backend and UI work proceed in parallel; US3 coverage work begins once the shared response composition is stable.
4. Merge only after each story's independent checkpoint passes.

## Notes

- `[P]` tasks change distinct files and have no incomplete same-phase dependency.
- `[US1]`, `[US2]`, and `[US3]` map directly to the prioritized stories in `spec.md`.
- Use synthetic identities only in tests and examples.
- Keep Azure imports lazy and `src/agentops/core/` pure.
- Do not add CLI commands, flags, Azure resources, roles, directory permissions, secrets, runtime stores, or cloud mutation paths.
- Commit after each task or cohesive task group.

## Phase 7: Convergence

- [X] T062 CRITICAL complete the FR-046 closed-failure acceptance matrix for insufficient direct log RBAC, group-claim overage, shared-cache failure, timeout, and partial-source preservation, asserting stable outcomes, no broader credential/query fallback, and no privacy-sensitive disclosure in attribution integration tests per FR-046 and Constitution V (missing)
- [X] T063 Connect department and delegated-user cost attribution to the production Facade/service path, preserving post-allocation filtering, original denominators, currencies, declared-total reconciliation, and independent coverage with real-path tests per FR-011, FR-012, and US1/AC1 (missing)
- [X] T064 Make aggregate cardinality count the current validated principal's ID/name aliases as one person or force delegated handling so one operator can never satisfy the two-person threshold through multiple aliases, with regression tests per FR-033 and FR-034 (contradicts)
- [X] T065 Move user ranking and `Other users` folding after exact cross-source aggregation, preserve all 500 users when the population is exactly 500, and add adversarial 499/500/501 and overlapping multi-source tests per FR-029 and SC-008 (partial)
- [X] T066 Remove opaque department-token values and derivatives from shared cache keys by filtering a safe unfiltered aggregate after retrieval or bypassing shared caching for selected departments, with cache-boundary tests per FR-030 and FR-038 (contradicts)
- [X] T067 Stop `/api/auth/context` from returning raw principal identifiers, display names, group IDs, or claims and expose only non-sensitive authentication status required by the UI, updating dependent tests per FR-030 and FR-040 (contradicts)
- [X] T068 Propagate singleton escalation's delegated boundary through failures so every successful or failed singleton outcome bypasses shared caching and returns exactly `Cache-Control: private, no-store`, with missing-OBO and query-failure tests per FR-032 and FR-038 (partial)
- [X] T069 Define and return a stable non-identifying attribution error envelope with code and corrective action, preserve token validation classifications, and replace raw Azure/source failure text with fixed safe messages across API, coverage, and contracts per FR-036 and FR-039 (partial)
- [X] T070 Handle shared attribution cache get/set failures explicitly without stale reuse, selector dropping, broader retry, or success-shaped empty results, and add closed-failure tests per FR-037 and FR-046 (partial)
- [X] T071 Carry group-claim overage into attribution coverage and render/document a dedicated privacy-safe partial/unmapped reason and next action without directory lookup per FR-034 and US3 (partial)
- [X] T072 Replace pattern-based attribution identity scrubbing in release evidence with structural allowlisting that excludes arbitrary non-email raw identities and attribution free text while retaining permitted aggregate readiness metadata per FR-030 and FR-040 (partial)
- [X] T073 Add a repeatable representative-operator acceptance protocol that measures identifying the highest-consuming department and restoring its opaque filtered URL within two minutes per SC-005 (missing)
- [X] T074 Add a defined standard-scope performance acceptance test and instrumentation for the p95 department usage and cost display target of five seconds per SC-006 (missing)
- [X] T075 Align the attribution deployment preview and operator documentation so the documented non-sensitive generation and entry counts are emitted and tested or the unsupported claim is removed per T059 (partial)
