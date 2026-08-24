# Implementation Plan: Granular Token Classes in the Models View

**Branch**: `012-granular-token-dimensions` | **Date**: 2026-08-24 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/012-granular-token-dimensions/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

The Cockpit models view currently collapses all observed token activity into a single
aggregate pair (`In: … Out: …`). Operators cannot see *which kind* of token work a model
is doing, so a workload dominated by cache reads is indistinguishable from one dominated
by fresh input, and reasoning-heavy models look identical to ordinary ones.

This feature widens the models view to report three normalized token classes —
**cache-read**, **cache-write**, and **reasoning** — alongside the existing input/output
totals, plus a bounded passthrough of up to five unnormalized but eligible token
attributes so vendor-specific classes are preserved instead of silently dropped. Because
most workloads emit only a subset of these classes, the view also gains a tri-state
`token_usage` coverage signal (`not_reported` / `partial` / `available`) surfaced both
per model row and in the coverage panel, so a blank cell is unambiguously "not emitted"
rather than "zero".

Technical approach, in the order data flows:

1. **Query** (`agent/observe/queries.py`) — project the eligible token attributes into the
   models query via models-query-only extend clauses appended after the existing
   `deployment` extend — never inside the shared `_agent_extend_clauses()` — and carry them
   through the existing `summarize … by project_resource_id, model, deployment`
   aggregation. No new round-trip to Azure Monitor is introduced.
2. **Contract** (`core/observe.py`) — widen `ModelUsage` with optional, non-negative
   per-class fields, an explicitly typed bounded passthrough map, and a truncation flag.
   All additions are optional, so the change is additive.
3. **Normalization + coverage** (`agent/observe/service.py`) — map source attribute names
   to normalized classes, enforce the eligibility rule and the five-attribute retention
   bound deterministically, add a **sibling** tri-state `TokenClassInventory` helper while
   leaving the existing two-state `token_reporting_state` untouched (research.md D7), and
   add a `token_usage` coverage entry to the models branch (which has none today).
4. **Rendering** (`agent/observe/ui.py`) — surface per-class values and the per-row partial
   indicator in **both** the server-side `render_usage_table` and its client-side
   `renderUsage` JavaScript mirror, reusing `_render_maybe_missing` so absence keeps
   rendering as "Not reported" and preserving the `(observed usage, not billing data)`
   label verbatim.

No value is ever synthesized by subtracting one class from another (FR-006), and no
monetary, cost, rate, spend, or billing framing is introduced anywhere in the feature
(FR-017). Copilot Studio agents remain out of scope (FR-019, tracked separately).

## Technical Context

**Language/Version**: Python 3.11+ (`src/` layout, `pathlib.Path` throughout)

**Primary Dependencies**: Pydantic v2 (`core/observe.py` contracts, `extra="forbid"`);
FastAPI + server-rendered HTML with a vanilla-JS client mirror (Cockpit); Azure Monitor
Logs KQL executed through the existing lazily-imported `azure-monitor-query` client; Typer
(CLI entry point). **No new dependency is introduced by this feature.**

**Storage**: N/A — the Cockpit is a read-only projection over Azure Monitor Log Analytics.
Nothing is persisted; every value is recomputed per request from observed telemetry.

**Testing**: pytest — focused unit tests in `tests/unit/` (`test_observe_queries.py`,
`test_observe_service.py`, `test_observe_ui.py`) plus Cockpit integration coverage in
`tests/integration/`. Azure SDK calls are mocked; the suite runs without Azure credentials.

**Target Platform**: Local Cockpit (`agentops cockpit`, localhost FastAPI) on Windows,
macOS, and Linux; Python 3.11+.

**Project Type**: Single project — CLI plus a locally hosted web UI, `src/agentops/…`.

**Performance Goals**: Zero additional Azure Monitor round-trips. The models view keeps
exactly one query per telemetry source; the new token classes ride along as additional
projected columns on the existing `summarize`, and the existing `| top 500 by requests desc`
row cap (`MAX_ROWS_PER_QUERY`) is unchanged. Per-record passthrough is bounded at five
attributes, so payload growth per row is constant and small.

**Constraints**:
- `core/` stays pure — no Azure SDK import, no network call, no filesystem write, no
  import-time side effect.
- All contract models inherit `extra="forbid"`, so the passthrough **must** be an explicit
  typed field; loose extra keys are not an option.
- Public-contract changes must be additive only; existing consumers of `ModelUsage` and
  `CoverageResult` must keep working unmodified.
- A class value is never derived by subtraction (FR-006) and never derived by comparing a
  token total against a context-length threshold (FR-003); absence is never rendered as zero
  (FR-016), and a reported zero stays distinguishable from absence (FR-007).
- No monetary/cost/price/rate/spend/charge/billing vocabulary anywhere in code, UI copy,
  tests, or docs (FR-017).
- The `(observed usage, not billing data)` label is preserved verbatim (FR-016).
- The agents view and the combined usage view keep their current token rendering unchanged
  (Out of Scope).

**Scale/Scope**: Up to 500 model rows per telemetry source per request; 3 normalized classes
+ at most 5 passthrough attributes per row. Implementation touches 4 source files
(`core/observe.py`, `agent/observe/{queries,service,ui}.py`) and 3–4 test files.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against `.specify/memory/constitution.md` v1.1.0.

| # | Principle | Verdict | Justification |
|---|-----------|---------|---------------|
| I | Preserve Public Contracts | **PASS** | Every new `ModelUsage` field is optional with a default, so existing payloads and consumers stay valid — the change is purely additive. No enum widening is needed: `CoverageState` already contains `"partial"` and `CoverageResult.dimension` already contains `"token_usage"`. No CLI command, flag, or exit-code semantics (`0`/`2`/`1`) is touched. **Watch item:** `extra="forbid"` means the unnormalized passthrough must be an explicitly declared typed field. |
| II | Enforce Architectural Boundaries | **PASS** | Contract changes are declarative Pydantic additions in `core/observe.py` — no SDK, network, or filesystem access, no import-time side effect. Query text stays in `queries.py`, normalization and coverage classification in `service.py`, rendering in `ui.py`. Per constitution §Additional Constraints, the design reuses existing helpers (`_render_maybe_missing`, `_render_token_totals`, `classify_query_coverage`, `token_reporting_state`) instead of introducing new abstractions. |
| III | Isolate Azure Runtime Integration | **PASS** | No new Azure dependency and no new client. The feature widens the projection of a KQL query already executed by the existing lazily-imported Azure Monitor client. Unit tests bind against plain normalized row mappings and mocked `SourceResult` objects, so the suite continues to run with no Azure credentials present. |
| IV | Keep Release Evidence Trustworthy | **PASS** | The Cockpit runtime stays strictly read-only — this feature adds no write, deploy, or mutation path. Reported values are verbatim observed telemetry; FR-006 forbids synthesizing a class by subtraction, so no computed number can be mistaken for an observed one, and FR-007 keeps "not emitted" visually distinct from a genuine zero. |
| V | Verify Every Behavior Change | **PASS** | Focused tests are planned per layer: query projection and agents/usage-query non-regression (`test_observe_queries.py`); attribute→class mapping, eligibility, deterministic cap, and tri-state coverage (`test_observe_service.py`); per-class cells, per-row partial indicator, preserved disclaimer, and Python↔JS parity (`test_observe_ui.py`). All Azure interaction is mocked. |

**Gate result: PASS — no violations, `## Complexity Tracking` intentionally left empty.**

Contract, layer, and evidence surfaces identified per constitution §Development Workflow:

- **Affected public contracts**: `ModelUsage` (additive fields), the models-view JSON row
  payload, and the `token_usage` `CoverageResult` entry newly emitted for the models view.
- **Affected architectural layers**: `core/` (pure contracts), `agent/observe/` (query
  construction, normalization, coverage classification, rendering).
- **Evidence boundary**: read-only observation only; nothing enters release evidence or
  Doctor exit-code decisions.

## Project Structure

### Documentation (this feature)

```text
specs/012-granular-token-dimensions/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
├── checklists/
│   └── requirements.md  # Authored during /speckit-specify
├── spec.md              # Feature specification (/speckit-specify + /speckit-clarify)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/agentops/
├── core/
│   └── observe.py                 # Pure contracts: CoverageState, ContractModel,
│                                  #   ModelUsage (+ new optional class fields,
│                                  #   bounded passthrough, truncation flag),
│                                  #   CoverageResult. No SDK / network / FS.
└── agent/
    └── observe/
        ├── queries.py             # KQL builders: _agent_extend_clauses (shared by
        │                          #   agents/models/usage), build_models_query
        │                          #   (+ models-only token-class projection)
        ├── service.py             # token_reporting_state (-> tri-state),
        │                          #   normalize_model_row (+ class mapping,
        │                          #   eligibility, deterministic 5-attribute cap),
        │                          #   classify_query_coverage (partial arm),
        │                          #   models branch of the view builder
        │                          #   (+ new token_usage coverage entry)
        └── ui.py                  # _render_token_totals (disclaimer preserved),
                                   #   render_usage_table (Python renderer) and the
                                   #   renderUsage JS mirror — both must change

tests/
├── unit/
│   ├── test_observe_queries.py    # models query projects classes; agents/usage unchanged
│   ├── test_observe_service.py    # mapping, eligibility, cap determinism, tri-state
│   └── test_observe_ui.py         # per-class cells, per-row partial marker, JS parity
└── integration/
    └── (existing Cockpit observe flows)  # end-to-end models view rendering
```

**Structure Decision**: Single-project `src/` layout, unchanged. The feature is a vertical
slice through the existing Cockpit observe stack and introduces no new package, module, or
directory. Pure data contracts live in `src/agentops/core/observe.py`; all runtime behavior
(query construction, normalization, coverage classification, and rendering) lives in
`src/agentops/agent/observe/`. Tests follow the established `tests/unit/` + `tests/integration/`
split, extending the three existing `test_observe_*` modules rather than adding new ones.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No constitutional violations. This section is intentionally empty.

## Post-Design Constitution Check

Re-evaluated after Phase 1 against the concrete design in
[research.md](./research.md), [data-model.md](./data-model.md), and [contracts/](./contracts/).

| Principle | Verdict | Evidence from the design |
|---|---|---|
| I. Preserve Public Contracts | PASS | `ModelUsage` gains six fields, all optional with defaults, so existing construction and serialization are unaffected ([contracts/model-usage-row.md](./contracts/model-usage-row.md)). `CoverageState` and `CoverageResult` are **not** modified — the new models-view entry reuses the existing `"token_usage"` dimension and the existing `"partial"` member ([contracts/token-usage-coverage.md](./contracts/token-usage-coverage.md)). Widening `CoverageState` was considered and rejected in [research.md D7](./research.md) precisely because it would be breaking. CLI exit codes are untouched. |
| II. Enforce Architectural Boundaries | PASS | `core/observe.py` receives only Pydantic field declarations — no Azure SDK import, no network call, no I/O. Query text lives in `queries.py`, normalization and coverage classification in `service.py`, rendering in `ui.py`. The new `TokenClassInventory` helper is deliberately placed in `service.py`, not in `core/`, because it derives state from runtime rows ([data-model.md §2](./data-model.md)). Per constitution lines 90–91, the design reuses `_render_maybe_missing`, `classify_query_coverage`, and the existing models-only extend site at `queries.py:187` rather than introducing new abstractions. |
| III. Isolate Azure Runtime Integration | PASS | No new Azure SDK dependency and no new import of any kind at module scope. The feature only changes the **text** of a KQL string that the existing Azure Monitor client already executes. Every offline scenario in [quickstart.md](./quickstart.md) runs against synthetic rows with no credentials. |
| IV. Keep Release Evidence Trustworthy | PASS | Cockpit remains strictly read-only: the design adds columns to a read query and fields to a read model, and creates, mutates, or deletes nothing. FR-006 is enforced structurally — no column, field, or renderer derives a token class by subtracting one count from another ([contracts/models-query-columns.md](./contracts/models-query-columns.md) invariants). The `(observed usage, not billing data)` label is preserved verbatim and the cost/billing wording gate is an explicit validation step. |
| V. Verify Every Behavior Change | PASS | [quickstart.md](./quickstart.md) enumerates 17 offline scenarios plus 5 live checks, each mapped to a success criterion. Behavior changes are covered at all three seams: query text (`test_observe_queries.py`), normalization and coverage (`test_observe_service.py`), and both renderers (`test_observe_ui.py`). Non-regression checks 12–17 pin the surfaces that must not move. |

**Result**: all five gates still PASS after design. Complexity Tracking remains empty.

### Required tests (Constitution lines 95–101)

| Seam | Module | Behavior to cover |
|---|---|---|
| Query construction | `tests/unit/test_observe_queries.py` | New class extends and passthrough projection present in `build_models_query`; `build_agents_query` and `build_usage_query` text unchanged |
| Normalization | `tests/unit/test_observe_service.py` | Alias precedence (first accepted name in declared order wins, never summed); `None` vs `0`; five-attribute cap and truncation flag; `token_classes_partial` computation; ineligible-value rejection; token-less rows skipped by the inventory fold |
| Coverage | `tests/unit/test_observe_service.py` | Five-arm precedence including query-failure-wins; the two `partial` variants carry distinct `next_action`; `token_reporting_state` and the agents-view entry unchanged |
| Rendering | `tests/unit/test_observe_ui.py` | Class values, partial indicator, truncation indicator in **both** renderers; disclaimer verbatim; no cost or billing wording |
