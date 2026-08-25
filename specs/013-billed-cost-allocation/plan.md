# Implementation Plan: Billed Cost Allocation

**Branch**: `placerda-cockpit-cost-allocation` | **Date**: 2026-08-24 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/013-billed-cost-allocation/spec.md`

## Summary

Add a read-only Cost view to Cockpit Observe that distributes operator-declared
billed totals across agents, tools, and runs using observed tokens, tool
invocations, active-session duration, credits, or explicitly configured
credit-consuming event counts. Every amount retains its period, currency,
billing boundary, source description, allocation method, usage share,
confidence, and coverage state. Metered and commitment pools remain distinct,
alternate breakdowns are never additive, and each component reconciles exactly
to the declared total.

The technical approach reuses the existing Observe stack. A strict, versioned
`AGENTOPS_COST_MODEL` JSON contract is parsed at Cockpit startup beside
`AGENTOPS_OBSERVE_SCOPE` and is optionally propagated as a non-secret App
Service setting by the existing deployment preview. Pure Pydantic contracts
live in `core/`; allocation math lives in a focused `agent/observe/` module;
`ObserveService` composes bounded model, tool, and run observations; the
existing `/api/observe/query` route gains a `cost` view; and `ui.py` renders
period, component, breakdown, and post-allocation agent drill-down selectors in
both its Python and JavaScript paths. The configured period and full authorized
usage set remain the allocation denominator; shared Observe filters never
redistribute a billed pool. No billing API, persistent store, new Azure role,
CLI command, or cloud mutation path is introduced.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: Pydantic v2 for strict configuration and response
contracts; FastAPI and Starlette for Cockpit routes; Azure Monitor Logs through
the existing duck-typed Observe adapters; stdlib `decimal.Decimal` for exact
money and weighted usage arithmetic; server-rendered HTML with a mirrored
vanilla-JavaScript client. No new third-party dependency.

**Storage**: None. The cost model is an optional environment/App Service
setting, observed usage is read from authorized telemetry, and results use only
the existing bounded in-process Observe cache. Neither configuration nor
allocations are persisted.

**Testing**: pytest. Pure contract and allocation tests run without Azure
packages or credentials. Observe query, service, facade, UI, runtime
configuration, deployment preview, Bicep template, and end-to-end tests use
existing fakes and mocked Azure boundaries.

**Target Platform**: Local Cockpit on Windows, macOS, and Linux; hosted Cockpit
on Azure App Service Linux with Python 3.11.

**Project Type**: Single Python package providing a CLI and FastAPI Cockpit with
a server-rendered UI and inline JavaScript refresh path.

**Performance Goals**: Keep the existing Observe limits: at most 10 telemetry
sources per batch, bounded per-source and overall deadlines, a 24-hour default
Observe lookback where applicable, and at most 500 returned rows for a
row-bearing view. A cost request performs no query per configured component:
it composes at most the bounded models, tools, and runs observations required
for the selected breakdown, concurrently, then allocates in memory. Component
summaries retain omitted-row amounts so truncation never breaks reconciliation.

**Constraints**:

- Cockpit runtime remains read-only and never queries Azure Cost Management or
  another billing system.
- `core/` remains pure: no Azure SDK import, network call, filesystem write, or
  import-time side effect.
- No new role assignment, resource, credential, connection string, or secret is
  introduced.
- The optional cost model is capped at 32 KiB, 24 periods, 50 components per
  period, and 100 values per selector list.
- Monetary inputs and outputs use decimal strings; binary floating-point is not
  used for reconciliation.
- Missing usage, attribution, and billed totals remain distinct from genuine
  zero.
- Cost-specific URL filters contain identifiers only; no protected content,
  billed amount, or configuration payload enters the URL.
- Cost calculations ignore shared Observe time/source/project/model/tool/run
  filters. Optional `cost_agent_key` filters already-allocated rows only and
  preserves hidden amounts in component reconciliation.
- Existing non-cost Observe views behave identically when cost configuration is
  absent, invalid, or removed.
- App Service configuration changes restart the hosted app; cost-model updates
  are therefore deliberate deployment/configuration events, not runtime edits.

**Scale/Scope**: One optional configuration contract; one new Observe view with
three breakdowns; up to 24 periods and 50 components per period; six allocation
keys; four confidence states; one additional coverage dimension; additive run
usage fields for granular tokens and credit signals; one pure allocation
engine; one hosted app setting; no new route family or data store.

## Constitution Check

*GATE: Checked before Phase 0 research. Re-checked after Phase 1 design.*

Evaluated against `.specify/memory/constitution.md` v1.1.0.

| Principle | Verdict | Evidence |
| --- | --- | --- |
| I. Preserve Public Contracts | PASS | The Observe `view` enum and filters grow additively; `ObservedRun` gains optional fields; `CoverageResult` gains optional cost context; existing payloads remain valid. No CLI command, flag, `agentops.yaml` field, results/evidence schema, or exit code changes. |
| II. Enforce Architectural Boundaries | PASS | Strict models live in pure `core/cost.py` and additive Observe contracts in `core/observe.py`; allocation and orchestration stay under `agent/observe/`; deployment setting propagation stays in `services/cockpit_deployment.py`; `cli/app.py` is unchanged. |
| III. Isolate Azure Runtime Integration | PASS | No new Azure client or dependency is introduced. Existing lazy credentials and duck-typed telemetry adapters are reused; cost math is independently testable without Azure packages or credentials. |
| IV. Keep Release Evidence Trustworthy | PASS | Cost is explicitly an operational allocation over declared totals and observed telemetry, never an invoice. Runtime remains read-only, no billing system is queried, and deployment adds only an optional non-secret setting with no resource or role change. |
| V. Verify Every Behavior Change | PASS | Tests cover strict configuration, compatibility rules, overlap validation, exact reconciliation, confidence, coverage, URL filters, API serialization, both renderers, deployment allowlists/templates, absent-config non-regression, and end-to-end behavior using fakes. |

**Gate result**: PASS. No constitutional exception is required.

The affected public contracts are the optional cost model, the additive
`cost` Observe view, additive cost filter fields including post-allocation
`cost_agent_key`, additive `ObservedRun` usage fields, and additive cost context
including the observed allocation key on coverage. Architectural layers and
tests are enumerated below. The evidence boundary remains a read-only
operational projection and does not alter Doctor or release-evidence behavior.

### Post-design re-check

The generated [research.md](./research.md), [data-model.md](./data-model.md),
[cost-model schema](./contracts/cost-model.schema.json), and
[Observe API delta](./contracts/observe-cost-api.openapi.yaml) preserve all
five principles:

- configuration and response models are strict, additive, and pure;
- allocation behavior is isolated from contracts and Azure adapters;
- telemetry remains the only cloud read path;
- deployment adds no resource or role and previews the optional non-secret
  setting;
- money reconciliation, missing-versus-zero behavior, coverage, security, and
  Python/JavaScript rendering parity all have focused test seams.

**Post-design verdict**: PASS. No new violation or complexity exception was
introduced.

## Project Structure

### Documentation (this feature)

```text
specs/013-billed-cost-allocation/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── cost-model.schema.json
│   └── observe-cost-api.openapi.yaml
├── checklists/
│   └── requirements.md
└── tasks.md                         # Created later by /speckit-tasks
```

### Source Code (repository root)

```text
src/agentops/
├── core/
│   ├── cost.py                     # Pure CostModel and allocation response contracts
│   └── observe.py                  # Add cost view/filters/coverage context and
│                                  # optional granular-token/credit fields on runs
├── agent/
│   ├── cockpit.py                  # Load optional AGENTOPS_COST_MODEL and expose
│   │                              # configuration state without blocking other views
│   └── observe/
│       ├── cost_allocation.py      # Decimal allocation, compatibility, confidence,
│       │                          # deterministic largest-remainder reconciliation
│       ├── queries.py              # Extend bounded run projection for granular
│       │                          # token and directly reported credit signals
│       ├── service.py              # Compose models/tools/runs into usage observations
│       │                          # and dispatch the cost allocation engine
│       ├── facade.py               # Validate configured period/component selectors;
│       │                          # serialize CostViewData through existing route
│       └── ui.py                   # Cost view, filters, summaries, provenance,
│                                  # confidence, coverage, Python/JS parity
├── services/
│   └── cockpit_deployment.py       # Optional non-secret setting in preview/deploy
└── templates/
    └── cockpit-hosted/infra/
        ├── main.bicep              # Optional AGENTOPS_COST_MODEL app setting
        └── main.parameters.json    # azd substitution parameter

tests/
├── unit/
│   ├── test_cost_models.py
│   ├── test_cost_allocation.py
│   ├── test_observe_models.py
│   ├── test_observe_queries.py
│   ├── test_observe_service.py
│   ├── test_observe_facade.py
│   ├── test_observe_ui.py
│   ├── test_cockpit_modes.py
│   ├── test_cockpit_deployment_preview.py
│   └── test_cockpit_hosted_templates.py
└── integration/
    └── test_observe_end_to_end.py

docs/
└── observe.md                      # Cost configuration, allocation semantics,
                                   # limitations, and interpretation

CHANGELOG.md                         # User-visible feature entry
```

**Structure Decision**: Keep the existing single-package Observe architecture.
Cost contracts are large enough to warrant a focused pure `core/cost.py`, while
the existing `core/observe.py` receives only the additive cross-view fields.
`cost_allocation.py` isolates deterministic business math from telemetry
queries and UI code. `ObserveService` reuses existing normalized model, tool,
and run rows rather than issuing one query per component. Hosted deployment
changes are limited to the established non-secret app-setting allowlist and
template; provisioning resources, authentication, and role assignments are
unchanged.

## Required Tests

| Seam | Primary files | Required behavior |
| --- | --- | --- |
| Cost model | `test_cost_models.py` | Version, size/cardinality bounds, decimal strings, time intervals, overlap detection, selector requirements, component/key/model compatibility, secret-shaped field rejection |
| Allocation engine | `test_cost_allocation.py` | Exact minor-unit reconciliation, largest remainder tie-breaking, weighted tokens, explicit fallbacks, zero denominator, unattributed/unallocated buckets, confidence precedence, mixed currencies |
| Observe contracts | `test_observe_models.py` | Additive view/filter/run/coverage fields, null versus zero, strict extra-field rejection |
| Telemetry projection | `test_observe_queries.py` | Bounded run query includes granular token and direct credit signals without protected content; existing queries remain bounded |
| Orchestration | `test_observe_service.py` | Models/tools/runs composition, usage matching, source failures, partial coverage, no query per component, truncation summaries |
| API facade | `test_observe_facade.py` | Period/component/agent drill-down validation, authoritative cost-period semantics, cost response serialization, absent/invalid model behavior, other-view non-regression |
| UI | `test_observe_ui.py` | Period/breakdown/component/agent URL round-trip, exclusion of shared filters from cost calculation, provenance, method/confidence labels, currency grouping, non-additive warning, missing versus zero, Python/JS parity |
| Runtime config | `test_cockpit_modes.py` | Optional local/hosted loading, 32 KiB cap, invalid model disables only Cost |
| Deployment | preview/template tests | Optional setting is allowlisted and previewed; no secret-shaped key, resource, role, or billing permission is added |
| End to end | `test_observe_end_to_end.py` | Valid, missing, invalid, partial, mixed-currency, tool, and run scenarios with fake telemetry |

## Complexity Tracking

No constitutional violations. This section is intentionally empty.
