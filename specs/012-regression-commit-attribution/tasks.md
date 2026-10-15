---

description: "Task list template for feature implementation"
---

# Tasks: Regression Commit Attribution

**Input**: Design documents from `/specs/012-regression-commit-attribution/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md (all present)

**Tests**: Included. The constitution (Principle V: "Verify Every Behavior Change") requires focused automated coverage for every behavior/contract change, and `plan.md`/`quickstart.md` already commit to specific test files, so test tasks are part of each phase rather than optional.

**Organization**: Tasks are grouped by user story (spec.md priorities P1/P2/P3) to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Every task includes exact file paths

## Path Conventions

Single project — `src/agentops/`, `tests/unit/`, `tests/integration/` at repository root (per `plan.md`'s Project Structure).

---

## Phase 1: Setup

No new project scaffolding, dependencies, or tooling is required — this
feature extends existing modules (`core/results.py`, `pipeline/`, `agent/`)
in an existing project with no new external dependency (per `plan.md`'s
Technical Context). Phase 1 is intentionally empty; proceed to Phase 2.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The commit-metadata data model, the resolver that captures it,
and the pure field-diff primitive all three user stories build on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T001 Add `CommitInfo`, `ChangedInput`, and `RegressionInsight` Pydantic models to `src/agentops/core/results.py`, plus `commit: Optional[CommitInfo] = None` on `RunResult` and `insight: Optional[RegressionInsight] = None` on `ComparisonInfo`, per `data-model.md`. Keep `model_config = ConfigDict(extra="forbid")` intact on `RunResult`.
- [X] T002 [P] Create `src/agentops/pipeline/commit_info.py` with `resolve_commit_info() -> Optional[CommitInfo]`: try `GITHUB_SHA` → `BUILD_SOURCEVERSION` → `Build.SourceVersion` env vars (mirroring `_git_sha()` in `src/agentops/pipeline/prompt_deploy.py:649`, set `source="ci"`), else run `git rev-parse HEAD` (`source="local"`); then enrich via `git show -s --format=%H%x1f%h%x1f%s%x1f%an%x1f%aI <sha>` (or equivalent) for `sha`/`short_sha`/`subject`/`author`/`authored_at`. Return `None` on any `subprocess` failure, missing `git` binary, or non-git workspace — never raise.
- [X] T003 [P] Create `src/agentops/pipeline/regression_insight.py` with `build_changed_inputs(from_run: RunResult, to_run: RunResult) -> List[ChangedInput]`: a pure, deterministic comparison of `target.name`/`target.version`/`target.deployment` (→ `"system_prompt"`/`"model"` changes) and `config["dataset"]`/`evaluators`/`thresholds` fields between the two runs (per `research.md` #5). No git or network access in this function.
- [X] T004 Wire `resolve_commit_info()` into `orchestrator._persist()` in `src/agentops/pipeline/orchestrator.py:1143` to set `result.commit` before `results.json`/`report.md` are written, so every execution mode (local, cloud, azd) gets commit metadata from one place (per `research.md` #2). Depends on: T001, T002.
- [X] T005 [P] `tests/unit/test_commit_info.py`: env-var precedence (`GITHUB_SHA` > `BUILD_SOURCEVERSION` > `Build.SourceVersion`), local `git rev-parse HEAD` fallback when no env var is set, `git show` field parsing, and graceful `None` on a non-git workspace / missing `git` binary (mock `subprocess.run`/`shutil.which`). Depends on: T002.
- [X] T006 [P] `tests/unit/test_regression_insight.py`: unit tests for `build_changed_inputs()` covering a prompt/version change, a model/deployment change, a dataset/evaluators/thresholds change, multiple simultaneous changes, and the no-change case. Depends on: T003.

**Checkpoint**: Every evaluation run now records `commit` metadata; the pure diff primitive exists and is tested. User story work can begin.

---

## Phase 3: User Story 1 - See why a hosted-agent evaluation regressed, without manual digging (Priority: P1) 🎯 MVP

**Goal**: When two comparable runs (same methodology) both carry commit
metadata and a metric regressed between them, `report.md` (and Doctor's
finding) explains what changed and suggests a fix — with zero change to
exit-code/threshold behavior.

**Independent Test**: `quickstart.md` Scenario 2 (regression explanation)
and Scenario 3 (graceful fallback with no commit metadata) — run two
evaluations with a committed baseline, confirm the "Regression Insight"
section appears with correct content, and confirm it is silently absent
(no fabricated cause, no error) when commit metadata is missing.

### Tests for User Story 1

- [X] T007 [P] [US1] `tests/unit/test_regression_insight.py`: add tests for the insight-assembly function from T008 — explanation sentence content (metric before/after, changed inputs named), `suggested_action` text, `used_git_diff` true/false branches, and confirm no `RegressionInsight` is produced when either `from_commit` or `to_commit` is `None` (FR-009). Depends on: T003, T006.
- [X] T008 [P] [US1] `tests/unit/test_pipeline_reporter.py`: extend with cases asserting the new "Regression Insight" section renders exactly when `result.comparison.insight` is present and is absent (report unchanged from today) when it is `None`.
- [X] T009 [P] [US1] `tests/unit/test_agent_checks_regression.py`: extend `run_regression_check` tests to assert `Finding.evidence["insight"]` and an updated `recommendation` string are present when the latest and immediately-preceding comparable run both have commit metadata, and that today's generic recommendation text is unchanged otherwise.

### Implementation for User Story 1

- [X] T010 [US1] Add `build_regression_insight(from_run: RunResult, to_run: RunResult, *, metric: str) -> RegressionInsight` to `src/agentops/pipeline/regression_insight.py`: call `build_changed_inputs()`, determine `used_git_diff` (attempt local git ancestry resolution between `from_run.commit.sha` and `to_run.commit.sha`, e.g. via `git cat-file -e`/`git merge-base`, falling back to `False` without raising when either commit isn't locally resolvable per `research.md` #4), render the one-to-two-sentence `explanation`, and derive a short rule-based `suggested_action` per changed input. Return early with no call needed when `from_run.commit`/`to_run.commit` is `None` (caller enforces this per T011/T013). Depends on: T001, T003.
- [X] T011 [US1] Wire `src/agentops/pipeline/comparison.py`'s `build_comparison()` to call `build_regression_insight()` for the first metric with `direction == "regressed"` and attach the result to `ComparisonInfo.insight`, only when both `current.commit` and `baseline.commit` are non-`None`. Depends on: T010.
- [X] T012 [US1] Render a new "## Regression Insight" section in `src/agentops/pipeline/reporter.py`'s `render()`, immediately after `_render_comparison()`, only when `result.comparison.insight` is not `None`, per `contracts/report-and-cockpit.md`. Depends on: T011.
- [X] T013 [US1] Update `src/agentops/agent/checks/regression.py`'s `run_regression_check()` to call `build_regression_insight()` against the single most recent run in `baseline_runs` (not the rolling mean) when both `latest.commit`... — note `ResultsHistory`/`RunSummary` do not carry `commit` or full `RunResult` today, so first re-load the full `RunResult` from `latest.raw_path` / the chosen baseline run's `raw_path` when `source == "local"` (skip insight generation, keep today's `recommendation`, when `source != "local"` or either file can't be reloaded). Attach the result to `Finding.evidence["insight"]` and fold its `explanation`/`suggested_action` into `Finding.recommendation`. Depends on: T010.
- [X] T014 [US1] `tests/integration/test_regression_commit_attribution.py`: end-to-end test that runs two evaluations (mocking `GITHUB_SHA` to two different values between runs, and a config/model change between them) via the orchestrator with `--baseline`-equivalent options, and asserts the rendered `report.md` contains the expected "Regression Insight" text and that the run's exit code is unaffected. Depends on: T004, T011, T012.

**Checkpoint**: User Story 1 is fully functional and independently testable — this is the MVP.

---

## Phase 4: User Story 2 - Browse a history of evaluated versions and what changed between them (Priority: P2)

**Goal**: Cockpit's dashboard shows every evaluated run for a methodology, in
order, with its commit (when known) and what changed vs. the previous run —
independent of whether that run regressed.

**Independent Test**: `quickstart.md` Scenario 4 — produce 3+ local runs for
one agent/dataset/evaluator combination, open Cockpit, and confirm the
history view lists each with commit + changes, including a run with no
resolvable commit shown without fabricated data.

### Tests for User Story 2

- [X] T015 [P] [US2] `tests/unit/test_cockpit.py`: add tests for `_project_run()` returning `commit`, `methodology_fingerprint`, `changed_inputs`, and `regressed` fields, including the case where `commit` is absent (shown as `null`, not omitted) and the case of the first run for a fingerprint (`changed_inputs == []`).

### Implementation for User Story 2

- [X] T016 [US2] Extend `_project_run()` in `src/agentops/agent/cockpit.py:856` to include `data.get("commit")` and a `methodology_fingerprint`. **Course-corrected during implementation**: reusing `results_history._methodology_fingerprint()` as originally planned was wrong - it hashes the *entire* target including version/deployment, so any prompt or model change (exactly what this feature needs to detect) also changes the fingerprint, making consecutive versions look "incomparable" and silently emptying the history view. Implemented a dedicated, coarser `_version_lineage_key()` in `cockpit.py` instead: same agent identity (`target.name`/`url`/`raw`) + dataset + evaluators, deliberately excluding version/deployment. Depends on: T001 (RunResult.commit persisted).
- [X] T017 [US2] Extend `_load_eval_runs()`/`_project_run()` in `src/agentops/agent/cockpit.py` so each projected run also carries `changed_inputs` (via `regression_insight.build_changed_inputs()` against the previous entry sharing the same lineage key, empty list for the first) and a boolean `regressed` (any shared metric lower than that previous entry's). Depends on: T003, T016.
- [X] T018 [US2] Add a new `_build_eval_history_section()` in `src/agentops/agent/cockpit.py` producing the `eval_history` payload shape (`has_runs`, `entries` newest-first), and include it in `build_cockpit_payload()`'s output (consumed by the `/?_partial=1` route). Note: `_build_eval_section()`/`.` at `cockpit.py:180` turned out to be dead code (never called from `build_cockpit_payload`) - not reused, left as-is since removing unrelated dead code is out of scope for this feature. Depends on: T017.
- [X] T019 [US2] Render the version-history view as a new collapsible section in Cockpit's dashboard HTML (`render_cockpit_html`/`_COCKPIT_TEMPLATE`), listing `eval_history.entries` with commit short-SHA/subject, metrics, and changed-inputs per entry, plus a "regressed" badge. Verified via FastAPI `TestClient` hitting `/?_partial=1` (server-rendered HTML, no client-side JS to browser-test) - not manually reviewed in a live browser. Depends on: T018.

**Checkpoint**: User Stories 1 AND 2 both work independently.

---

## Phase 5: User Story 3 - Best-effort commit attribution for local evaluation runs (Priority: P3)

**Goal**: Local (non-CI) evaluation runs get the same commit capture and
regression attribution on a best-effort basis, and runs outside any git
repository still complete normally with no error.

**Independent Test**: `quickstart.md` Scenario 1 — run locally inside a git
repo and confirm `commit.source == "local"`; run from a non-git copy and
confirm the run completes with `commit: null` and no error.

### Tests for User Story 3

- [X] T020 [P] [US3] `tests/unit/test_commit_info.py`: add an explicit local-repo test asserting `source == "local"` and `sha` matches `git rev-parse HEAD` when no CI env var is set. Already fully covered by `test_local_fallback_resolves_head_commit` (added under T005) - no duplicate added.
- [X] T021 [P] [US3] Added as `tests/integration/test_pipeline_smoke.py::test_local_run_captures_commit_metadata_inside_git_repo` and `test_local_run_has_no_commit_metadata_outside_git_repo` (moved from `test_pipeline_orchestrator.py` since exercising a full local run needs a real invokable target - reused the file's existing HTTP-echo-server fixture rather than duplicating it as a unit-test double). Asserts real `git`-backed commit capture inside a repo and `result.commit is None` with unchanged exit code and no error outside one.

### Implementation for User Story 3

- [X] T022 [US3] Verified via T021's real end-to-end tests (not just the isolated resolver unit tests): `orchestrator._persist()`/`_finalize_commit_and_comparison()` already handle a `None` from `resolve_commit_info()` transparently - no code change was needed, both tests passed against the existing T004 implementation. Depends on: T004.
- [X] T023 [US3] `tests/integration/test_regression_commit_attribution.py::test_regression_commit_attribution_purely_local` (added under T014): clears all CI env vars and reproduces the same regression-insight outcome as the CI-style test, confirming the report/insight pipeline itself has no CI-only code path. Depends on: T014, T010, T011, T012.

**Checkpoint**: All three user stories are independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T024 [P] Update `CHANGELOG.md` under `[Unreleased]` describing the new `commit`/`comparison.insight` `results.json` fields and the Cockpit version-history view, per the constitution's changelog requirement for user-visible changes.
- [X] T025 [P] Update `docs/how-it-works.md` to mention commit attribution and version history (new "Regression commit attribution" subsection under "Outputs and history").
- [X] T026 Ran Scenario 1 for real via the packaged `agentops` CLI (venv install, real git repo, real HTTP echo agent) - confirmed `results.json`'s `commit` field is populated correctly end to end. Scenarios 2-4 are covered by the automated integration/unit tests added under US1/US2/US3 (`test_regression_commit_attribution.py`, `test_pipeline_reporter.py`, `test_cockpit.py`), which assert the exact outcomes those scenarios describe; not re-run as separate manual CLI sessions given equivalent automated coverage already exists.
- [X] T027 Given the size of the full suite, ran targeted passes instead of one full `pytest tests/` invocation (per direction during the session): every touched/added unit and integration test file (all pass, see below), plus `ruff check` and `mypy` on every new/edited source file. Fixed two genuine ruff findings (a mutable class-level dict, an unused unpacked variable) and one real mypy `union-attr` error in `cockpit.py`; declined to "fix" ~400 additional ruff findings surfaced by a newer local ruff than the version pinned in `.pre-commit-config.yaml` (mostly `Optional`/`Dict`/`List` vs `X | None`/`dict`/`list` style, matching this codebase's existing convention throughout `core/results.py` and elsewhere) since they're pre-existing/version-drift noise unrelated to this change, not a regression it introduced. A full `pytest tests/` and pinned-version `pre-commit run --all-files` pass is still recommended in CI before merge.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: Empty — no dependencies.
- **Foundational (Phase 2)**: No dependencies beyond Setup. **Blocks all user stories.**
- **User Story 1 (Phase 3)**: Depends on Foundational completion. No dependency on US2/US3.
- **User Story 2 (Phase 4)**: Depends on Foundational completion (specifically T001/T003 for the `commit` field and diff helper). Independent of US1's report/Doctor wiring (T010-T013) — only reuses the shared T003 diff primitive.
- **User Story 3 (Phase 5)**: Depends on Foundational completion (T002, T004) and, for its integration test (T023), on US1's insight-assembly/reporting tasks (T010-T012) already existing.
- **Polish (Phase 6)**: Depends on all desired user stories being complete.

### Parallel Opportunities

- T002 and T003 (Foundational) touch different new files and can run in parallel once T001 lands.
- T005 and T006 (Foundational tests) can run in parallel with each other.
- Within US1: T007, T008, T009 (tests, different files) can be drafted in parallel; implementation tasks T010→T011→T012→T013 are sequential (each depends on the previous).
- US2 (Phase 4) can proceed in parallel with US1 (Phase 3) once Foundational is done, since T016-T019 only touch `cockpit.py` and reuse T001/T003, not US1's `comparison.py`/`reporter.py`/`regression.py` changes.
- T024 and T025 (Polish) can run in parallel.

---

## Parallel Example: Foundational Phase

```bash
# After T001 (models) lands, run these together:
Task: "Create src/agentops/pipeline/commit_info.py with resolve_commit_info()"
Task: "Create src/agentops/pipeline/regression_insight.py with build_changed_inputs()"

# After T002/T003 land, run these together:
Task: "tests/unit/test_commit_info.py"
Task: "tests/unit/test_regression_insight.py (diff-primitive cases)"
```

## Parallel Example: User Story 1 tests

```bash
Task: "tests/unit/test_regression_insight.py — insight-assembly cases"
Task: "tests/unit/test_pipeline_reporter.py — Regression Insight section cases"
Task: "tests/unit/test_agent_checks_regression.py — Doctor insight wiring cases"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 2: Foundational (commit capture on every run + diff primitive).
2. Complete Phase 3: User Story 1 (regression insight in `report.md` + Doctor finding).
3. **STOP and VALIDATE**: Run `quickstart.md` Scenarios 2 and 3 independently.
4. This alone delivers the product owner's prioritized hosted-agent/CI scenario.

### Incremental Delivery

1. Foundational → commit metadata flows into every `results.json`.
2. Add User Story 1 → validate → this is the MVP the product owner asked to prioritize.
3. Add User Story 2 → validate → Cockpit history view ships as a pure read surface on the same data.
4. Add User Story 3 → validate → local-only workflows get parity, formally tested.
5. Each story adds value without breaking the previous ones — none of US2/US3 touch US1's `comparison.py`/`reporter.py`/`regression.py` changes.

---

## Notes

- No new CLI flags, commands, external dependencies, or Azure SDK calls are introduced anywhere in this task list (per `plan.md`'s Constitution Check).
- All `core/results.py` model changes are consolidated into T001 since they are small, additive, and share one file — splitting them per story would only add cross-task file-conflict risk with no benefit.
- Commit after each task or logical group; verify new/extended tests fail before the corresponding implementation task and pass after.
