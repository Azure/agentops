---

description: "Implementation tasks for Azure Storage evaluation datasets"
---

# Tasks: Azure Storage Evaluation Datasets

**Input**: Design documents from `/specs/010-azure-storage-datasets/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/dataset-source.md`, `quickstart.md`

**Tests**: Focused automated tests are required by the feature specification and AgentOps constitution. Write each story's tests first and confirm they fail for the expected missing behavior before implementing it.

**Organization**: Tasks are grouped by user story so remote execution, identity-only authentication, and readiness diagnostics can be implemented and validated as separate increments.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it changes different files and has no dependency on another incomplete task in the same phase
- **[Story]**: Maps the task to User Story 1, 2, or 3
- Every task includes the exact file path or paths it changes

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add the required Azure Storage runtime dependencies without changing the public CLI.

- [X] T001 Add `azure-storage-blob>=12.20,<13` and `azure-storage-file-datalake>=12.20,<13` to the normal project dependencies in `pyproject.toml`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish the pure dataset-reference model, backward-compatible configuration typing, and reusable Azure credential behavior needed by every story.

**Critical**: No user story implementation starts until this phase is complete.

- [X] T002 [P] Add failing coverage for local-path preservation, canonical Blob/DFS parsing, Windows-safe URL handling, and rejected schemes/hosts/roots/wildcards in `tests/unit/test_dataset_source.py` and `tests/unit/test_agentops_config.py`
- [X] T003 Implement the pure `DatasetReference` model and local/Blob/ADLS classification and validation functions with no Azure SDK or filesystem I/O in `src/agentops/core/dataset_source.py`
- [X] T004 Update the additive `dataset` scalar typing and pre-validation flow so local values retain `Path` behavior while remote URLs remain intact in `src/agentops/core/agentops_config.py`
- [X] T005 [P] Add failing coverage for Azure CLI preference, `DefaultAzureCredential` fallback, process-wide caching, reset behavior, and `process_timeout=30` in `tests/unit/test_shared_credentials.py`
- [X] T006 Extract the generic credential factory and concise credential-error helpers to `src/agentops/utils/azure_credentials.py`, then preserve existing Doctor imports through a compatibility re-export in `src/agentops/agent/sources/_credentials.py`

**Checkpoint**: Dataset references and Azure credentials are reusable without network access, import-time side effects, or local-dataset regressions.

---

## Phase 3: User Story 1 - Run an Evaluation from Azure Storage (Priority: P1) - MVP

**Goal**: Load one JSONL object from Blob Storage or ADLS Gen2 into a bounded immutable snapshot and use it across AgentOps-owned local, cloud, official-evaluation, reporting, and prompt-deploy paths while preserving local dataset behavior.

**Independent Test**: Configure equivalent local, Blob, and ADLS datasets; verify that each produces the same rows, validation outcomes, evaluator selection, and evaluation results, while remote results retain the original validated URI and never expose the temporary file path.

### Tests for User Story 1

- [X] T007 [P] [US1] Add failing Blob and ADLS resolver tests for metadata inspection, chunked download, 100 MiB rejection before and during download, ETag consistency, one-snapshot reuse, and temporary-file cleanup in `tests/unit/test_dataset_source.py`
- [X] T008 [P] [US1] Add failing local and remote orchestration tests for row parity, one resolution per run, immutable snapshot use, progress labels, telemetry, cleanup, and `RunResult.dataset_path` provenance in `tests/unit/test_pipeline_orchestrator.py`
- [X] T009 [P] [US1] Add failing cloud-runner tests proving temporary snapshot upload works while dataset naming and lineage retain the validated remote URI rather than `local_path` in `tests/unit/test_cloud_runner.py`
- [X] T010 [P] [US1] Add failing official-evaluation tests for remote snapshot resolution, row conversion, source-based naming, and cleanup in `tests/unit/test_official_eval.py`
- [X] T011 [P] [US1] Add failing prompt-deploy tests proving local paths still resolve from the config directory and remote dataset URIs are preserved without `Path` coercion in `tests/unit/test_prompt_deploy.py`
- [X] T012 [P] [US1] Add failing reporter tests proving validated remote URI provenance is rendered unchanged and temporary materialization paths are absent in `tests/unit/test_pipeline_reporter.py`
- [X] T013 [P] [US1] Add failing CLI compatibility coverage for local, Blob, and DFS `dataset` scalars without new commands or flags in `tests/integration/test_cli_flat_schema.py`

### Implementation for User Story 1

- [X] T014 [US1] Implement lazy BlobClient/DataLakeFileClient construction, metadata checks, bounded chunk streaming, ETag capture, private temporary snapshot lifecycle, and local-source passthrough in `src/agentops/services/dataset_source.py`
- [X] T015 [P] [US1] Resolve one snapshot for local and cloud runs while separating reader `local_path` from source URI/display provenance and guaranteeing cleanup in `src/agentops/pipeline/orchestrator.py`
- [X] T016 [P] [US1] Accept source provenance separately from the upload path and preserve remote dataset lineage and naming in `src/agentops/pipeline/cloud_runner.py`
- [X] T017 [P] [US1] Resolve and consume remote snapshots without changing official evaluator selection or row validation in `src/agentops/pipeline/official_eval.py`
- [X] T018 [P] [US1] Preserve validated remote URIs in generated candidate configuration and human-readable reports in `src/agentops/pipeline/prompt_deploy.py` and `src/agentops/pipeline/reporter.py`

**Checkpoint**: User Story 1 is deployable as the MVP; Blob and ADLS datasets run end-to-end with local parity and stable provenance.

---

## Phase 4: User Story 2 - Authenticate Securely in Local and Automated Runs (Priority: P2)

**Goal**: Reuse the Azure identity already available to AgentOps for all remote dataset reads and reject every dataset-specific credential or token-bearing reference before network access.

**Independent Test**: Run with an active `az login` identity and with mocked managed/workload/service-principal identity fallback, then verify successful reads require only data-plane permission; verify query strings, fragments, user information, SAS, keys, and connection strings have no supported configuration path and are rejected before client creation.

### Tests for User Story 2

- [X] T019 [US2] Add failing tests proving query strings, fragments, user information, and embedded authentication material are rejected before any Blob or Data Lake client is created in `tests/unit/test_dataset_source.py`
- [X] T020 [P] [US2] Extend credential tests for active `az login`, workload/managed/service-principal fallback, single-line authentication failures, and absence of credential-chain dumps in `tests/unit/test_shared_credentials.py`
- [X] T021 [US2] Add failing resolver tests that distinguish authentication from authorization and require `az login`, `Storage Blob Data Reader`, and ADLS ACL remediation without leaking authorization details in `tests/unit/test_dataset_source.py`

### Implementation for User Story 2

- [X] T022 [US2] Enforce identity-only URL validation, including pre-network rejection of query strings, fragments, user information, and unsupported endpoint forms, in `src/agentops/core/dataset_source.py`
- [X] T023 [US2] Wire remote reads exclusively to `get_shared_credential()`, classify authentication versus authorization failures, and emit concise identity/RBAC/ACL remediation in `src/agentops/services/dataset_source.py`

**Checkpoint**: User Story 2 works with the same identity as the AgentOps process and introduces no storage token, SAS, account-key, connection-string, command, flag, or environment-variable contract.

---

## Phase 5: User Story 3 - Diagnose Remote Dataset Readiness (Priority: P3)

**Goal**: Make `agentops eval analyze` and runtime resolution consistently distinguish malformed, missing, authentication, authorization, connectivity, service, consistency, size, and content failures without producing success-shaped artifacts.

**Independent Test**: Exercise every documented failure category with mocked Azure responses; verify analysis returns the matching diagnosis, evaluation exits with code `1`, no completed result is written, and messages identify the validated source with actionable remediation.

### Tests for User Story 3

- [X] T024 [P] [US3] Add failing resolver tests for `malformed`, `not_found`, `authentication_failed`, `authorization_failed`, `connectivity_failed`, `service_unavailable`, `source_changed`, `oversized`, and `invalid_content` mappings in `tests/unit/test_dataset_source.py`
- [X] T025 [P] [US3] Add failing readiness-analysis tests for remote structural validation, bounded access/content inspection, discovered columns, `access_checked`, and stable diagnosis messages in `tests/unit/test_eval_analysis.py`
- [X] T026 [P] [US3] Add failing CLI integration tests proving remote resolution failures return exit code `1`, never `2`, and do not create success-shaped `results.json` or `report.md` artifacts in `tests/integration/test_cli_flat_schema.py`

### Implementation for User Story 3

- [X] T027 [US3] Implement bounded Azure retry configuration and explicit Azure Core/service error mapping with source-safe messages in `src/agentops/services/dataset_source.py`
- [X] T028 [US3] Replace local-only dataset probing with shared remote resolution and `DatasetSourceDiagnosis` generation in `src/agentops/services/eval_analysis.py`
- [X] T029 [US3] Propagate classified resolution/content failures through local and cloud orchestration with exit code `1`, no partial success result, and guaranteed temporary cleanup in `src/agentops/pipeline/orchestrator.py`

**Checkpoint**: All three stories are independently verifiable and the complete remote-dataset failure contract is observable in analysis and execution.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Complete user guidance, examples, release notes, and repository-wide validation.

- [X] T030 [P] Add commented local, Blob, and DFS dataset examples while keeping the local seed as the default in `src/agentops/templates/agentops.yaml`
- [X] T031 [P] Document supported URL forms, identity-only authentication, `Storage Blob Data Reader`, ADLS ACLs, private networking, the 100 MiB limit, provenance, and the unchanged azd boundary in `docs/evaluation.md` and `docs/how-it-works.md`
- [X] T032 [P] Add the user-visible Azure Storage dataset capability and identity-only security model to `CHANGELOG.md`
- [X] T033 Run the focused test command from `specs/010-azure-storage-datasets/quickstart.md` and resolve all failures in the files changed by this feature
- [X] T034 Run `python -m pytest tests/ -x -q` and resolve regressions without changing the 0/2/1 exit-code contract

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 - Setup**: Starts immediately.
- **Phase 2 - Foundational**: Depends on T001 and blocks every user story.
- **Phase 3 - User Story 1**: Depends on Phase 2 and is the MVP.
- **Phase 4 - User Story 2**: Depends on Phase 2; it can be developed alongside User Story 1 after the shared parser, config, and credential foundations exist, but final resolver integration must incorporate T014.
- **Phase 5 - User Story 3**: Depends on Phase 2; readiness analysis can start independently, while runtime error integration depends on the resolver from T014 and identity mapping from T023.
- **Phase 6 - Polish**: Depends on every user story selected for the release.

### User Story Completion Order

```text
Setup -> Foundational -> US1 (MVP)
                      |-> US2
                      `-> US3

US1 resolver + US2 identity mapping -> US3 runtime diagnostics
US1 + US2 + US3 -> Polish
```

### Within Each User Story

- Write the story's tests first and confirm they fail for the missing feature.
- Implement pure models and validation before network/service behavior.
- Implement the resolver before pipeline integrations.
- Preserve separate reader paths and source provenance at every integration boundary.
- Complete the independent test before moving the story checkpoint.

### Parallel Opportunities

- T002 and T005 can run in parallel; T003 and T006 can then proceed independently.
- T007-T013 can be authored in parallel because they target separate test surfaces.
- After T014, T015-T018 can be implemented in parallel in separate pipeline files.
- T019-T021 can be authored in parallel; T022 can proceed independently of shared-credential test work.
- T024-T026 can be authored in parallel before the diagnostic implementation.
- T030-T032 can be completed in parallel after the feature behavior stabilizes.

---

## Parallel Example: User Story 1

```text
Task: "Add orchestration snapshot/provenance tests in tests/unit/test_pipeline_orchestrator.py"
Task: "Add remote lineage tests in tests/unit/test_cloud_runner.py"
Task: "Add official-evaluation snapshot tests in tests/unit/test_official_eval.py"
Task: "Add prompt-deploy URI-preservation tests in tests/unit/test_prompt_deploy.py"
Task: "Add remote reporter provenance tests in tests/unit/test_pipeline_reporter.py"
```

After T014 completes:

```text
Task: "Integrate resolved snapshots in src/agentops/pipeline/orchestrator.py"
Task: "Preserve remote lineage in src/agentops/pipeline/cloud_runner.py"
Task: "Integrate snapshots in src/agentops/pipeline/official_eval.py"
Task: "Preserve remote configuration/reporting in src/agentops/pipeline/prompt_deploy.py and src/agentops/pipeline/reporter.py"
```

## Parallel Example: User Story 2

```text
Task: "Test pre-network rejection in tests/unit/test_dataset_source.py"
Task: "Test Azure identity selection in tests/unit/test_shared_credentials.py"
Task: "Test authentication/authorization remediation in tests/unit/test_dataset_source.py"
```

## Parallel Example: User Story 3

```text
Task: "Test resolver error taxonomy in tests/unit/test_dataset_source.py"
Task: "Test readiness diagnoses in tests/unit/test_eval_analysis.py"
Task: "Test CLI exit code and artifact behavior in tests/integration/test_cli_flat_schema.py"
```

---

## Implementation Strategy

### MVP First: User Story 1

1. Complete Setup and Foundational tasks.
2. Complete User Story 1 tests and implementation.
3. Validate local, Blob, and ADLS row parity and provenance independently.
4. Stop at the User Story 1 checkpoint for an MVP review.

### Incremental Delivery

1. Deliver US1 for direct remote evaluation with local compatibility.
2. Deliver US2 to lock the feature to the existing Azure identity and actionable RBAC/ACL behavior.
3. Deliver US3 for full readiness and runtime diagnosis.
4. Complete documentation, changelog, focused tests, and the full suite.

### Parallel Team Strategy

1. Complete Setup and Foundational work together.
2. Assign one developer to the US1 resolver/pipeline path, one to US2 identity/security coverage, and one to US3 readiness analysis.
3. Merge the shared resolver before finalizing US2 service integration and US3 runtime diagnostics.
4. Validate each story at its checkpoint before the release-wide test pass.

---

## Notes

- `[P]` tasks intentionally target separate files or test surfaces.
- Azure SDK imports remain lazy and all Azure interactions are mocked in tests.
- `src/agentops/core/` remains pure and uses no Azure SDK, network, or filesystem writes.
- `execution: azd` remains recipe-owned and is not rewritten by this feature.
- No task adds a dataset-specific token, SAS, account key, connection string, CLI command, or flag.
- Commit after each task or coherent task group and stop at any checkpoint for review.

## Phase 7: Convergence

- [X] T035 Add parameterized local, Blob, and ADLS orchestration parity coverage proving equivalent rows, evaluator selection, validation outcomes, and evaluation results while allowing only source provenance to differ in `tests/unit/test_pipeline_orchestrator.py` per SC-002, T008, and Constitution V (partial)
