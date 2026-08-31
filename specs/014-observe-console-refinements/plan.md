# Implementation Plan: Observe Console Refinements

**Branch**: `014-observe-console-refinements` | **Date**: 2026-08-31 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/014-observe-console-refinements/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

This feature refines the Observe console so an operator can narrow scope without
knowing identifiers by heart, read a time window and a refresh moment without
mental arithmetic, understand the Overview without visiting every tab, read the
Runs table without decoding column labels, and see an approximate money figure
next to observed token usage.

The technical approach keeps every change inside the existing Observe module and
its pure contracts. Scope filtering gains a new facet capability: a dedicated
KQL builder that enumerates distinct values per dimension, served through a
cached, non-blocking path so it never sits on the render critical path. Time
handling moves the refresh indicator and window boundaries onto one consistent
basis with an explicit set of relative-window presets, each re-resolved to
absolute bounds every time a query is built so a preset stays live across
refreshes. Overview gains per-entity summaries assembled from the data
already retrieved for the existing views, with no additional telemetry
round-trip. The Runs table gains clearer labels, a copy affordance, an inline
expansion in place of a details column, and suppression of single-valued
dimensions. Estimated cost is introduced as a new, separate figure derived from
a versioned list-price reference shipped with the accelerator, parsed by a pure
loader in `core/` and read from packaged data in `agent/`, deliberately never
combined with the declared-billed-total allocation that Spec 013 already
delivers.

The single largest technical risk is not any individual requirement but the
existing duplication between server-rendered Python and the embedded client-side
JavaScript in `ui.py`. Nine constructs are currently maintained twice, and the
Runs sort map is keyed by the very column labels this feature renames. The plan
treats de-duplication of the affected constructs as a prerequisite of the
rename, not as an optional cleanup.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: Typer (CLI surface), Pydantic v2 (pure contracts in
`core/`), FastAPI + Uvicorn (Cockpit host, existing `cockpit` extra),
ruamel.yaml (already a core dependency; candidate carrier for the price
reference), `azure-monitor-query` and `azure-identity` (lazy-imported inside the
Observe adapters only). No new runtime dependency is introduced by this feature.

**Storage**: No database. Two file-backed artifacts are relevant. Telemetry is
read from Azure Monitor / Application Insights via KQL and is never written.
The list-price reference is a new read-only data file shipped inside the
installed package and surfaced through `importlib.resources`, following the
established `src/agentops/agent/knowledge/` precedent (packaged data, cached
read, graceful degradation to an empty set when absent).

**Testing**: pytest. Unit tests in `tests/unit/`, end-to-end flows in
`tests/integration/`, deterministic HTML/CSS golden files in
`tests/unit/__snapshots__/`. Azure SDK access is mocked through the existing
fake query client and fixture builders in `tests/fixtures/observe.py`; no test
requires a credential.

**Target Platform**: Local operator workstation running the Cockpit web console.
Windows and Linux are both first-class; the console renders in any modern
browser and ships as a fully self-contained HTML document with no CDN reference
and no outbound fetch beyond the Cockpit host itself.

**Project Type**: Single project — a Python CLI plus a locally hosted read-only
web console. There is no separate frontend build; the console's markup, styles
and script are emitted from Python.

**Performance Goals**: Scope filter options present within one second for a
scope containing on the order of one thousand distinct agents (SC-013). The
enriched Overview renders within three seconds for a window containing on the
order of one thousand runs, and must not add a telemetry round-trip relative to
today's Overview (SC-011, FR-024). Estimated cost adds no additional telemetry
retrieval, being computed from token counts already returned.

**Constraints**: The console is strictly read-only — it must never create,
mutate, or delete a cloud resource, and must not require a new credential
(FR-045, Constitution Principle IV). Raw generative-AI content must never enter
the console address, the cache, or any persisted artifact; the existing address
allowlist and the cache's sensitive-value guard both continue to hold (FR-008).
`core/` must stay pure — no Azure SDK import, no network access, no filesystem
write — so the price reference is parsed from an in-memory string in `core/`
while the packaged file is read in `agent/`. The rendered document must remain
self-contained and byte-deterministic so the existing visual regression
snapshots stay meaningful.

**Scale/Scope**: Roughly one thousand distinct agents and one thousand runs per
window as the design target, against an existing hard display bound of five
thousand rows per query. The query layer already summarises before it bounds and
returns the true in-scope total alongside the capped rows, so scope size drives
neither query cost nor transferred volume; what the bound does constrain is how
much the console can display and therefore what it may claim. This feature's
roll-ups are consequently computed from server-side aggregation rather than from
displayed rows, and its column-suppression rule stands down whenever the result
is truncated. The feature touches one module of about sixteen
thousand lines across twelve files, principally `ui.py` (~6,600 lines),
`service.py` (~4,100 lines) and `queries.py` (~1,350 lines), plus new pure
contracts in `core/observe.py` and a new pure price-reference parser in `core/`.
Fifty-eight functional requirements across six user stories.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against Constitution v1.1.0.

**I. Preserve Public Contracts** — PASS with obligations. No CLI command, flag,
or exit code changes. The console's HTTP surface gains one new read-only
endpoint for scope facet options; existing view endpoints keep their wire names,
including the established `usage` → `models` mapping. The address allowlist is
extended, not replaced, and every added key is a scope identifier, never
content. Renaming Runs table column labels is a user-visible contract change to
the rendered document and therefore requires the documentation and changelog
updates recorded below.

**II. Enforce Architectural Boundaries** — PASS with obligations. New Pydantic
contracts (facet options, estimated-cost figures, price reference entries) go in
`core/observe.py` and a new pure price module in `core/`, both free of Azure
imports and filesystem access. Packaged-file reading, caching, and query
execution stay in `agent/observe/`. `cli/app.py` is untouched. The constitution
requires reusing existing helpers before introducing new abstractions, which
directly shapes three decisions: the price reference reuses the
`agent/knowledge/` packaged-data pattern rather than inventing a loader; the
facet cache reuses `ObserveCache` rather than adding a second cache; and the
pure parse-from-string boundary mirrors `core/cost.py::load_cost_model`.

**III. Isolate Azure Runtime Integration** — PASS. The new facet query is built
by the existing pure query builders and executed through the existing adapter,
which already lazy-imports the Azure SDK and already applies
`DefaultAzureCredential(process_timeout=30)`. No new credential, no new client,
no new import-time Azure dependency.

**IV. Keep Release Evidence Trustworthy** — PASS. Cockpit remains read-only. No
Doctor check, evaluation run, exit code, or release evidence artifact is
modified. Estimated cost is explicitly labelled as an estimate at list price and
is never merged into the declared-billed-total figure, so neither number can be
mistaken for the other (FR-042, FR-043).

**V. Verify Every Behavior Change** — PASS with obligations. Every requirement
maps to a test: new query builders and pure contracts get unit tests; the facet
path and Overview enrichment get integration coverage; renamed labels, the copy
affordance, inline expansion, single-valued-dimension suppression, and cost
presentation all regenerate the visual regression snapshots. Because the sort
map is keyed by column label, a targeted regression test asserting that every
sortable column still sorts after renaming is mandatory rather than optional.

**Documentation obligation**: `docs/observe.md` and `CHANGELOG.md` must both be
updated, since the console's visible behaviour changes.

**No violations requiring justification. Complexity Tracking is intentionally
empty.**

### Post-Design Re-Check

*Re-evaluated after Phase 1 design artifacts were produced.*

**Result: PASS. No new violations, no new complexity to justify.**

The design confirmed the five principles rather than straining them:

- **I. Preserve Public Contracts** — the design adds one read-only local route
  and additive fields on two existing responses. No CLI command, flag, or exit
  code changes; no existing field is removed, renamed, or repurposed. The
  address allowlist is extended rather than replaced, so a field added elsewhere
  is still excluded by default.
- **II. Enforce Architectural Boundaries** — every new contract is a pure
  Pydantic model in `core/`, and the price reference parser accepts an in-memory
  string rather than a path, mirroring the existing declared-cost-model loader.
  The file read and its caching stay in `agent/`.
- **III. Isolate Azure Runtime Integration** — the new route reuses the existing
  query client and cache. No new SDK surface, no new credential, no new outbound
  dependency, and every scenario in the quickstart's automated section runs
  without credentials.
- **IV. Keep Release Evidence Trustworthy** — the console remains strictly
  read-only, including the price reference, which is packaged data the console
  never writes. Estimated cost is contractually separate from the declared
  billed-total allocation and the two are never summed.
- **V. Verify Every Behavior Change** — the design names the two regressions that
  would otherwise be silent, and both are covered: sorting after a column rename,
  and a price reference missing from a built distribution.

**One correction was made during Phase 1.** The initial research decided to
resolve a relative window preset to absolute boundaries at selection time. That
contradicts FR-012, which requires a preset to be re-evaluated against the
current moment on every query including both refresh paths. Freezing at selection
would leave auto-refresh re-querying an ageing, stationary window. The window
contract is now a discriminated preset-or-custom choice that resolves at
query-build time, and `research.md` records the corrected reasoning.

## Project Structure

### Documentation (this feature)

```text
specs/014-observe-console-refinements/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
│   └── observe-console.md
├── checklists/
│   └── requirements.md  # Spec quality checklist (already complete)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/agentops/
├── core/
│   ├── observe.py             # Pure contracts: ObserveScope, ObserveFilterState,
│   │                          #   ObservedAgent, ModelUsage, ObservedTool,
│   │                          #   ObservedRun, CoverageResult, QueryDiagnostics.
│   │                          #   Extended here with facet-option, estimated-cost
│   │                          #   and run-model contracts.
│   ├── observe_pricing.py     # NEW. Pure parse/validate of the list-price
│   │                          #   reference from an in-memory string, mirroring
│   │                          #   core/cost.py::load_cost_model. No I/O.
│   └── cost.py                # Existing declared-billed-total contract (Spec 013).
│                              #   Read for boundary alignment; not modified.
│
├── agent/
│   ├── observe/
│   │   ├── queries.py         # KQL builders. Gains a facet/distinct builder and
│   │   │                      #   run-to-model projection in build_runs_query.
│   │   ├── service.py         # Orchestration, normalization, caching. Gains facet
│   │   │                      #   retrieval, Overview enrichment assembly, and
│   │   │                      #   estimated-cost derivation.
│   │   ├── ui.py              # Server-rendered HTML + embedded JS. Filter controls,
│   │   │                      #   time presentation, Overview cards, Runs table,
│   │   │                      #   copy affordance, inline expansion, cost display.
│   │   ├── cache.py           # ObserveCache reused for facet options.
│   │   ├── adapters.py        # Azure query client + batching. Unchanged contract.
│   │   ├── cost_allocation.py # Spec 013 rateio of a declared total. Not modified.
│   │   └── pricing/           # NEW packaged data directory for the list-price
│   │       └── <reference>    #   reference, read via importlib.resources following
│   │                          #   the agent/knowledge/ precedent.
│   ├── knowledge/__init__.py  # Reference pattern for packaged data + graceful
│   │                          #   degradation. Read, not modified.
│   ├── ui_theme.py            # Shared theme tokens and toggle. Reused as-is.
│   └── cockpit.py             # FastAPI host. Gains the facet options route.
│
└── templates/                 # Unchanged.

tests/
├── unit/
│   ├── test_observe_queries.py       # Facet builder, run-model projection.
│   ├── test_observe_service.py       # Facet retrieval, caching, Overview assembly,
│   │                                 #   estimated-cost derivation and partial states.
│   ├── test_observe_ui.py            # Labels, sort integrity, address allowlist,
│   │                                 #   privacy invariants, accessibility.
│   ├── test_observe_ui_visual.py     # Golden HTML/CSS snapshots.
│   ├── test_observe_pricing.py       # NEW. Pure price parsing, versioning, staleness.
│   └── __snapshots__/
│       ├── observe_overview.html     # Regenerated by this feature.
│       └── observe_styles.css        # Regenerated by this feature.
├── integration/                      # End-to-end console flows.
└── fixtures/observe.py               # Deterministic row builders; extended for
                                      #   facet rows, run-model rows, price entries.

docs/observe.md                       # Must be updated (user-visible change).
CHANGELOG.md                          # Must be updated (user-visible change).
pyproject.toml                        # package-data entry for the new pricing package.
```

**Structure Decision**: Single project. The repository is one installable Python
package under `src/agentops/` with tests under `tests/`, and this feature stays
entirely within that layout. There is no separate frontend or service tier to
introduce: the Observe console is emitted from `src/agentops/agent/observe/ui.py`
as a self-contained document, so what would be "frontend work" in a web
application is Python string rendering plus one embedded script here. The one
structural addition is a packaged data directory for the list-price reference,
placed under `src/agentops/agent/observe/pricing/` so that the file lives beside
the code that reads it, exactly as `agent/knowledge/` places its checklist beside
its reader. That addition requires a corresponding `package-data` entry in
`pyproject.toml`, without which the reference would be missing from an installed
wheel and every cost figure would silently degrade to unavailable.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No constitutional violations. This section is intentionally empty.
