# Implementation Plan: Observe tools and runs views

**Branch**: `placerda-cockpit-observe-tools-runs` | **Date**: 2026-08-24 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/012-observe-tools-runs-views/spec.md`

## Summary

Add two new read-only views to the Cockpit Observe surface — **Tools** (which tools agents actually call, how often, how often they fail, how slow they are) and **Runs** (end-to-end agent executions with turn counts, tool counts, duration, and outcome) — plus two new filter dimensions (`tool_name`, `run_key`), a refined agent runtime attribution set that replaces today's coarse three-value set, and additional coverage dimensions so an absent tool, run, or runtime signal is explained rather than silently rendered as zero.

The technical approach extends the existing Observe layering rather than introducing anything new: pure Pydantic v2 contracts grow in `core/observe.py`; two new bounded KQL builders join `agent/observe/queries.py`; row normalization and per-view dispatch grow in `agent/observe/service.py`; the new views register in the `_VIEW_QUERY_BUILDERS` table in `agent/observe/adapters.py`; and `agent/observe/ui.py` gains two tables plus the matching entries in its view/label/wire-name constants and its mirrored inline JS client. No new service, route family, deployment artifact, or write path is introduced — Cockpit stays a read-only projection.

Three questions materially shape the design and are resolved in [research.md](./research.md): which telemetry signals can distinguish five runtime kinds when today's code only inspects presence of `gen_ai.agent.id` versus `gen_ai.agent.name`; how to report "showing N of M" when the existing `| top N` bound discards the total; and how a run correlation key is chosen when session identity is present for some traces and absent for others.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: Pydantic v2 (strict `ConfigDict(extra="forbid")` contract models in `core/`); FastAPI + Starlette (Cockpit routes); Azure Monitor Logs reached through a duck-typed `query_batch` adapter (`agent/observe/queries.py` deliberately never imports `azure.monitor.query`, so tests substitute fakes); `azure-identity` (lazy import, `DefaultAzureCredential(process_timeout=30)`); stdlib `html` for server-side escaping. No new third-party dependency is required.

**Storage**: None. Every value is a read-only projection over Azure Monitor / Application Insights tables (`AppDependencies`, `AppRequests`, `AppGenAIContent`) belonging to the caller's authorized scope. Results are reused from the existing in-process cache (`CACHE_TTL_SECONDS = 120.0`) and are never persisted to disk.

**Testing**: pytest. Unit coverage extends `tests/unit/test_observe_models.py`, `test_observe_queries.py`, `test_observe_service.py`, `test_observe_facade.py`, and `test_observe_ui.py`; end-to-end coverage extends `tests/integration/test_observe_end_to_end.py`. All Azure clients are faked via the duck-typed `query_batch` seam, so the suite runs without credentials. Standard command: `python -m pytest tests/ -x -q`.

**Target Platform**: Local Cockpit (`agentops cockpit`) on Windows, macOS, and Linux, and hosted Cockpit on Azure App Service (Linux container). Both run the same code path; no platform-specific branches are introduced.

**Project Type**: Single Python package (`src/agentops/`) exposing a server-rendered web UI with an inline JavaScript client. New views touch both the Python HTML render path and the mirrored JS render path.

**Performance Goals**: Inherit the bounds established by spec 011 and enforced in `agent/observe/queries.py` — at most 10 telemetry sources per batch, 30-second per-source timeout, 10-second overall request deadline, `MAX_ROWS_PER_QUERY = 500` rows per query, and a 24-hour default lookback. Both new views must satisfy SC-001 and SC-002 (first useful answer without hand-written queries, within the same responsiveness envelope as the existing agents and models views) and must never issue an unbounded query.

**Constraints**:
- Cockpit remains strictly read-only; neither new view may create, mutate, or delete any resource (Principle IV).
- `core/observe.py` must stay import-safe and pure — no Azure SDK import, no network call, no filesystem write (Principle II).
- Absent measurements must remain distinguishable from genuine zero. Token fields are deliberately **absent** from the tools view and must not be synthesized (FR-005, FR-025).
- New filter values must never leak protected generative-AI content into the URL; only keys added to the `OBSERVE_FILTER_QUERY_KEYS` allow-list may round-trip through the query string.
- Refining `source_kind` from three values to five plus `unknown` is a breaking change to a published contract and is recorded under [Complexity Tracking](#complexity-tracking).
- Every row must remain attributable to exactly one authorized source; per-source failures must degrade to coverage rows, never to a blank page.

**Scale/Scope**: 2 new views, 2 new filter dimensions, 1 refined enum (3 values → 5 values + `unknown`), at least 2 new coverage dimensions, 2 new contract entities (`ObservedTool`, `ObservedRun`), 2 new KQL builders, and matching UI in both the Python render path and the mirrored JS client. Roughly six existing modules are touched; no module is replaced.

## Constitution Check

*GATE: Checked before Phase 0 research. Re-checked after Phase 1 design.*

Evaluated against `.specify/memory/constitution.md` v1.1.0.

| Principle | Verdict | Evidence |
| --- | --- | --- |
| **I. Preserve Public Contracts** | ⚠️ **EXCEPTION — justified** | The `view` enum, `ObserveFilterState` fields, and `CoverageResult.dimension` grow additively and stay backward compatible. However, FR-018/FR-018A **replace** the published `source_kind` values, which is a breaking contract change. Documented, justified, and mapped in [Complexity Tracking](#complexity-tracking). No CLI command, `agentops.yaml` field, `results.json` field, evidence schema field, or exit code changes. |
| **II. Enforce Architectural Boundaries** | ✅ PASS | Contracts land in pure `core/observe.py`; KQL builders in `agent/observe/queries.py`; normalization and dispatch in `agent/observe/service.py`; builder registration in `agent/observe/adapters.py`; rendering in `agent/observe/ui.py`. Cockpit code stays under `agent/`. `cli/app.py` is untouched. No import-time side effects; `pathlib.Path` where paths appear. |
| **III. Isolate Azure Runtime Integration** | ✅ PASS | `queries.py` stays free of `azure.monitor.query` and is exercised through the duck-typed `query_batch` seam. Any new credential use reuses the existing lazy `DefaultAzureCredential(process_timeout=30)` construction in `agent/observe/auth.py`. Contracts and their tests import with no Azure package installed. Per-source failures surface as explicit coverage states — never as success-shaped empty results (FR-023, FR-026). |
| **IV. Keep Release Evidence Trustworthy** | ✅ PASS | Both views are read-only projections. Nothing in this feature writes evidence, changes Doctor severity rollup, or alters the `doctor --evidence-pack` contract. Absent-versus-zero fidelity is preserved by design (FR-005, FR-011, FR-025). |
| **V. Verify Every Behavior Change** | ✅ PASS | Every new contract field, KQL builder, normalization path, coverage dimension, and rendered table gets unit coverage; the two views get end-to-end coverage against faked Azure clients. No behavior ships untested; no network calls in tests. |

**Gate result**: PASS with one recorded, justified exception under Principle I. Proceed to Phase 0.

**Post-design re-check (after Phase 1)**: Re-evaluated against the generated `data-model.md` and `contracts/`. The design keeps `core/` pure, adds no Azure import to the contract layer, introduces no write path, and confines the breaking change to the single `source_kind` field already recorded below. **Verdict unchanged: PASS with the same single documented exception.** No new violations were introduced by the design.

## Project Structure

### Documentation (this feature)

```text
specs/012-observe-tools-runs-views/
├── spec.md              # Feature specification (complete)
├── plan.md              # This file
├── research.md          # Phase 0 output — resolved unknowns and decisions
├── data-model.md        # Phase 1 output — entities, fields, validation rules
├── quickstart.md        # Phase 1 output — runnable validation scenarios
├── contracts/           # Phase 1 output — Observe API contract additions
│   └── observe-api.additions.openapi.yaml
├── checklists/
│   └── requirements.md  # Specification quality checklist (16/16 passing)
└── tasks.md             # Created later by /speckit-tasks — not part of this plan
```

### Source Code (repository root)

```text
src/agentops/
├── core/
│   └── observe.py                  # Pure Pydantic v2 contracts (no Azure, no I/O)
│                                   #  · extend ObserveQueryRequest.view enum
│                                   #  · add tool_name / run_key to ObserveFilterState
│                                   #  · refine ObservedAgent.source_kind (breaking)
│                                   #  · add ObservedTool, ObservedRun
│                                   #  · extend CoverageResult.dimension
│
└── agent/
    ├── cockpit.py                  # FastAPI routes — unchanged; /api/observe/query
    │                               #  already forwards any contract-valid view
    └── observe/
        ├── queries.py              # add build_tools_query, build_runs_query;
        │                           #  reuse _dimension_filters, _time_window_clause,
        │                           #  MAX_ROWS_PER_QUERY and the existing bounds
        ├── service.py              # extend View literal; add normalize_tool_row,
        │                           #  normalize_run_row; refine agent_source_kind;
        │                           #  extend _normalize_view dispatch + coverage
        ├── adapters.py             # register both builders in _VIEW_QUERY_BUILDERS
        ├── facade.py               # no routing change (views flow from View);
        │                           #  serialization only
        └── ui.py                   # OBSERVE_VIEWS / _LABELS / _WIRE_NAMES entries,
                                    #  OBSERVE_FILTER_QUERY_KEYS additions,
                                    #  render_tools_table + render_runs_table,
                                    #  new <section> blocks, refreshed source-kind
                                    #  badge tones, and the mirrored JS renderers

tests/
├── unit/
│   ├── test_observe_models.py      # new contracts + refined source_kind
│   ├── test_observe_queries.py     # both builders stay bounded and scope-filtered
│   ├── test_observe_service.py     # row normalization, coverage, absent-vs-zero
│   ├── test_observe_facade.py      # view dispatch and serialization
│   └── test_observe_ui.py          # view constants, tables, URL round-trip
└── integration/
    └── test_observe_end_to_end.py  # both views end to end against faked clients

docs/
└── observe.md                      # document the two new views and the refined
                                    #  runtime attribution values
```

**Structure Decision**: This feature extends the existing single-package layout rather than adding any new top-level module. Observe already separates its concerns exactly along the boundaries Principle II requires — pure contracts in `core/observe.py`, query construction in `agent/observe/queries.py`, normalization and per-view dispatch in `agent/observe/service.py`, a builder registry in `agent/observe/adapters.py`, and rendering in `agent/observe/ui.py` — so both new views slot into the same seams the `agents` and `models` views already use. The one structural note worth calling out is that `ui.py` renders each view twice, once as server-side Python HTML and once in its mirrored inline JavaScript client; every rendering change in this feature must be made in both places or the initial paint and subsequent refreshes will disagree.

## Complexity Tracking

*This section is filled because the Constitution Check above records an exception.*

| Violation | Why Needed | Simpler Alternative Rejected Because |
| --- | --- | --- |
| **Principle I — breaking change to the published `ObservedAgent.source_kind` values.** FR-018/FR-018A replace the current `foundry \| external \| unknown` set with `foundry_hosted \| foundry_prompt \| external_registered \| external_unregistered \| copilot_studio \| unknown`. | The coarse set cannot answer the operational question the feature exists to answer. `foundry` conflates hosted agents with prompt agents, which have different runtimes, different failure modes, and different cost characteristics; `external` conflates agents that are registered in the project with agents that only appear in telemetry. Without the refinement, the tools and runs views would attribute activity to a runtime label too vague to act on, and the future cost-allocation work this feature is a prerequisite for would inherit an unusable key. | **Add the five new values alongside the three old ones.** Rejected: the old and new values overlap in meaning, so every consumer would have to know that `foundry` and `foundry_hosted` may describe the same agent, and a row could be classified either way depending on which code path produced it. That is a worse contract than a clean replacement. **Introduce a parallel `source_kind_detail` field and leave `source_kind` untouched.** Rejected: it doubles the field surface, leaves the misleading coarse value as the one most consumers read, and makes the two fields drift apart the moment classification improves. The mapping below is deliberately **not one-to-one**, so no automatic upgrade shim can be correct: `foundry` splits into `foundry_hosted` or `foundry_prompt`, `external` splits into `external_registered` or `external_unregistered`, `copilot_studio` has no predecessor at all, and any agent whose runtime cannot be determined from telemetry resolves to `unknown` (FR-017). A migration note and the mapping table are the honest answer; a silent shim is not. |

**Old → new mapping recorded for consumers:**

| Old value | New value(s) | Notes |
| --- | --- | --- |
| `foundry` | `foundry_hosted` **or** `foundry_prompt` | Split by runtime; not automatically derivable from the old value alone. |
| `external` | `external_registered` **or** `external_unregistered` | Split by whether the agent is registered in the project. |
| — | `copilot_studio` | New classification with no predecessor value. |
| `unknown` | `unknown` | Unchanged. Also the required fallback whenever telemetry is insufficient to classify (FR-017). |

The deprecation and migration window for this change is resolved in [research.md](./research.md); the accepted outcome is recorded there so `/speckit-tasks` can schedule the documentation and release-note work alongside the code change.

**Maintainer approval** (required by [constitution](../../.specify/memory/constitution.md) — Principle I exceptions must be approved by a maintainer *before* implementation):

> ✅ **APPROVED — 2026-08-24.** Phase 6 (T043–T049) is authorized.
>
> - Approving maintainer: `@placerda`
> - Date: `2026-08-24`
> - Approval reference (PR review or issue comment link): approved during spec review for [issue #441](https://github.com/Azure/agentops/issues/441) on branch `placerda-cockpit-observe-tools-runs`. This record is itself the approval artifact and is merged together with the spec.

Recording the approval here is the gate. With all three values filled in, the breaking `source_kind` replacement is documented, justified, and authorized, and every phase (1–7) may proceed.
