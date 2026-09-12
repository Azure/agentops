# Implementation Plan: Regression Commit Attribution

**Branch**: `012-regression-commit-attribution` | **Date**: 2026-09-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/012-regression-commit-attribution/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

When an evaluation run's metrics regress relative to the previous comparable
run, nobody is told why automatically today — someone has to manually diff
prompts, model choice, and config between two commits. This feature (1)
records commit metadata (SHA, subject, author, timestamp) on every evaluation
run's stored result, prioritizing the Foundry hosted-agent / cloud-CI scenario
where CI already reliably exposes the commit SHA, with best-effort local-git
capture as a secondary path; and (2) when two comparable runs both have
commit metadata and a metric regressed between them, deterministically diffs
their recorded prompt/model/config fields and produces a plain-language,
one-to-two-sentence explanation plus a brief suggested fix. The explanation
is surfaced in the existing `report.md` (already attached to the PR pipeline
as a CI artifact and PR comment — no new delivery mechanism) and in a new
Cockpit version-history view listing every evaluated run and what changed
relative to the previous one, independent of regression.

Technical approach: a single new commit-metadata resolver
(`pipeline/commit_info.py`) attached at the one existing choke point every
execution mode already shares (`orchestrator._persist()`), plus a single
deterministic diff/explanation helper (`pipeline/regression_insight.py`)
consumed by both existing regression signals (`pipeline/comparison.py`'s
`--baseline` comparison and Doctor's rolling-baseline
`agent/checks/regression.py`) and by Cockpit's existing run-history scan
(`agent/cockpit.py`). All additions are additive to `results.json` and
introduce no new CLI flags, Azure calls, or exit-code semantics.

## Technical Context

**Language/Version**: Python 3.11+ (per constitution's supported runtime)

**Primary Dependencies**: None new. Reuses stdlib `subprocess` (for `git
show`/`git rev-parse`, mirroring the existing pattern in
`pipeline/prompt_deploy.py`), and existing Pydantic v2 models in `core/`.

**Storage**: Flat JSON files (existing) — `.agentops/results/<timestamp>/results.json`
(+ `latest/` mirror) and the optional committed
`.agentops/baseline/results.json`. This feature adds fields to that existing
schema; it introduces no database and no new artifact file.

**Testing**: pytest, per constitution Principle V — new unit tests under
`tests/unit/` for the commit resolver, the diff/explanation helper, reporter
rendering, and Cockpit projection; one integration test under
`tests/integration/` for the end-to-end two-run regression scenario.

**Target Platform**: Same as today — local developer machines (macOS/Linux/
Windows) and CI runners (GitHub Actions, Azure DevOps) executing the
`agentops` CLI. No server component beyond the existing local Cockpit.

**Project Type**: Single project (existing `src/agentops/` CLI + services
layout); no frontend/backend split.

**Performance Goals**: Negligible added overhead per run — a small, fixed
number of local `git` subprocess calls (sub-100ms typically) and in-memory
field comparisons against at most one prior run; must not measurably slow
`agentops eval run` or CI job duration.

**Constraints**: No new Azure SDK calls (git operations are local
subprocess only, consistent with Principle III's lazy/isolated Azure
integration rule not even applying here); must not change the exit-code or
threshold-gating contract (FR-011); must degrade to today's behavior with no
error when git is unavailable, the workspace isn't a repo, history is
shallow/pruned, or either compared run lacks commit metadata (FR-003, FR-009).

**Scale/Scope**: Per-workspace local run history (tens to low hundreds of
`.agentops/results/*` entries) and a small, fixed number of git commands per
run; no pagination or indexing concerns at this scale.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Preserve Public Contracts** — PASS. All `results.json` changes are
  additive optional fields (`commit`, `comparison.insight`); no existing
  field's type or meaning changes; no new CLI flags; exit-code contract
  (`0`/`1`/`2`) is explicitly unchanged (FR-011). See
  `contracts/results-json.md`.
- **II. Enforce Architectural Boundaries** — PASS. New git-subprocess and
  diff logic lives in `pipeline/` (`commit_info.py`, `regression_insight.py`);
  Cockpit-specific rendering stays in `agent/cockpit.py`; `core/results.py`
  gains only pure, I/O-free data models (`CommitInfo`, `ChangedInput`,
  `RegressionInsight`). `cli/app.py` is untouched — no new parsing/output
  responsibilities added there.
- **III. Isolate Azure Runtime Integration** — PASS (not applicable to the
  new code paths). No Azure SDK calls are introduced; commit capture and
  diffing are local-git-only and function identically with or without Azure
  credentials.
- **IV. Keep Release Evidence Trustworthy** — PASS. Cockpit's new history
  view is read-only, reusing the existing local-file scan; Doctor's
  regression `Finding` gains a richer `recommendation` string but no new
  exit-code contract (`evidence` is already free-form). No monitored cloud
  resource is mutated or deleted.
- **V. Verify Every Behavior Change** — PASS (planned). Unit coverage for
  the resolver, the diff helper (including both no-metadata and
  no-git-history fallback branches), reporter rendering, and Cockpit
  projection; one integration test for the end-to-end regression scenario.
  See `quickstart.md`'s "Automated coverage" section; concrete test tasks are
  generated in `tasks.md` by `/speckit-tasks`.

No violations requiring an entry in Complexity Tracking.

**Post-design re-check (after Phase 1)**: Unchanged — the data model
(`data-model.md`) and contracts (`contracts/`) confirmed above stay purely
additive and introduce no new architectural layer, Azure dependency, or
public-contract break. Gate still PASSES.

## Project Structure

### Documentation (this feature)

```text
specs/012-regression-commit-attribution/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md         # Phase 1 output (/speckit-plan command)
├── quickstart.md         # Phase 1 output (/speckit-plan command)
├── contracts/            # Phase 1 output (/speckit-plan command)
│   ├── results-json.md
│   └── report-and-cockpit.md
├── checklists/
│   └── requirements.md
└── tasks.md              # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

Single project (existing layout) — no new top-level directories.

```text
src/agentops/
├── core/
│   └── results.py                # + CommitInfo, ChangedInput, RegressionInsight models;
│                                  #   + RunResult.commit, ComparisonInfo.insight fields
├── pipeline/
│   ├── commit_info.py             # NEW: resolve commit metadata (CI env vars -> local git fallback)
│   ├── regression_insight.py      # NEW: deterministic field-diff + explanation/suggestion builder
│   ├── orchestrator.py            # _persist(): attach commit metadata for every execution mode
│   ├── comparison.py              # build_comparison(): attach insight when regression + both commits known
│   ├── reporter.py                # render(): new "Regression Insight" section
│   └── prompt_deploy.py           # unchanged; _git_sha() pattern is generalized into commit_info.py
├── agent/
│   ├── cockpit.py                 # _project_run()/_load_eval_runs(): commit + changed_inputs projection;
│   │                               #   new eval_history section in the /?_partial=1 payload
│   └── checks/
│       └── regression.py          # run_regression_check(): attach insight to Finding.evidence
└── cli/app.py                     # unchanged (no new flags/commands)

tests/
├── unit/
│   ├── test_commit_info.py        # NEW
│   ├── test_regression_insight.py # NEW
│   ├── test_reporter.py           # extended
│   ├── test_cockpit.py            # extended
│   └── test_regression_check.py   # extended (if present) for Doctor's insight wiring
└── integration/
    └── test_regression_commit_attribution.py  # NEW: end-to-end two-run scenario
```

**Structure Decision**: Single project, no new architectural layer. All
changes fit within the existing `core/` (pure models) → `pipeline/`
(orchestration + git/diff logic) → `agent/` (Doctor + Cockpit surfaces)
boundaries already mandated by the constitution; two new `pipeline/` modules
are added rather than growing `orchestrator.py`/`comparison.py` with
inline git/diff logic, keeping each new concern independently testable.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations — table intentionally omitted.
