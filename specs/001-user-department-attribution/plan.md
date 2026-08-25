# Implementation Plan: User and Department Usage and Cost Attribution

**Branch**: `placerda-issue-444-spec` | **Date**: 2026-08-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-user-department-attribution/spec.md`

## Summary

Add an opt-in attribution projection to Cockpit Observe so operators can group
supported usage and allocated cost by department and investigate individual
consumption without persisting raw identity. The design extends the existing
credential split: department aggregates use the deployment identity only after
an uncached singleton-safety check, while every user list, user filter, or
singleton department uses a fresh delegated OBO credential and bypasses shared
caches.

A strict `AGENTOPS_ATTRIBUTION_CONFIG` JSON contract supplies a deployment
namespace, rotation generation, and mappings from deployment-scoped
pseudonymous user keys or group object IDs to departments. Aggregate KQL
normalizes only `UserAuthenticatedId` and its documented OpenTelemetry source
`enduser.id`, rejects conflicting aliases, derives pseudonyms with Azure
Monitor's stable `hash_sha256()` function, and projects away raw identity before
returning aggregate data. Opaque, scope/config-bound URL tokens fail closed;
user tokens are additionally bound to the signed-in principal. Cost attribution
reuses the existing declared-total allocation model and preserves its
denominators, amounts, currencies, and exact reconciliation.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: Pydantic v2 for strict configuration and response
contracts; FastAPI and Starlette for Cockpit routes; Azure Monitor Logs through
the existing lazy, duck-typed adapters; stdlib `hashlib` and canonical JSON for
filter-token validation; Kusto `hash_sha256`, `substring`, `strcat`, `datatable`,
and bounded aggregation; existing `Decimal` cost allocation. No new third-party
dependency.

**Storage**: No runtime store. The optional versioned mapping is supplied through
`AGENTOPS_ATTRIBUTION_CONFIG`, propagated as a privacy-sensitive non-secret App
Service setting and local azd environment value, and loaded read-only at
startup. User-level results and raw identities are never cached or persisted.
Only safe department aggregates may use the existing in-process Observe cache,
keyed by a canonical configuration fingerprint.

**Testing**: pytest. Pure configuration, pseudonym, mapping, token, coverage, and
reconciliation tests run without Azure packages or credentials. Query,
facade/service, principal, API, UI, deployment preview/template, evidence
exclusion, and end-to-end behavior use existing fakes and mocked Azure
boundaries.

**Target Platform**: Local Cockpit on Windows, macOS, and Linux; hosted Cockpit
on Azure App Service Linux with Python 3.11. Protected individual views require
the hosted Easy Auth/OBO context; local mode reports them as protected or
unavailable rather than falling back.

**Project Type**: Single Python package providing a CLI, deployment service, and
FastAPI Cockpit with server-rendered HTML plus a mirrored vanilla-JavaScript
refresh path.

**Performance Goals**: Preserve the existing maximum of 10 telemetry sources per
batch, per-source and request deadlines, and 500-row response bound. Department
views must meet the five-second target for at least 95% of supported,
standard-scope requests. A user result returns at most the top 499 identified
users plus one `Other users` row; unattributed totals remain in the summary
outside that row array. Query count is independent of mapping-entry count:
mapping is joined through one bounded KQL `datatable`, not queried one user at a
time.

**Constraints**:

- Attribution is absent or disabled by default and must not change existing
  Observe queries, payloads, caching, authorization, or cost behavior.
- `core/` remains pure; Azure SDK imports stay lazy inside runtime functions.
- Only `AppRequests.UserAuthenticatedId` / `AppDependencies.UserAuthenticatedId`
  and the documented OpenTelemetry `enduser.id` source are eligible in v1.
  `UserId`, `enduser.pseudo.id`, sessions, devices, browsers, network addresses,
  prompts, and behavior are never fallbacks.
- Aggregate KQL derives pseudonyms before projection; aggregate responses,
  diagnostics, caches, logs, deployment journals, Doctor evidence, and browser
  storage never contain raw identity.
- Pseudonyms remain linkable personal data. The attribution config is
  non-secret but privacy-sensitive and its value is redacted from preview
  rendering, logs, and deployment journals.
- Pseudonyms use full SHA-256 output over a canonical, deployment-specific
  namespace/generation/tenant/identity input. The namespace is random,
  operator-supplied, non-secret, and stable until explicit rotation.
- Filter tokens include current generation, semantic config fingerprint, and
  Observe-scope fingerprint. User tokens also include a principal-binding
  digest. Invalid, stale, out-of-scope, or cross-principal tokens fail before
  data is returned.
- Explicit pseudonymous-user mappings are valid across users. Group claims may
  classify only the signed-in principal after exact claim-to-telemetry identity
  matching; no Microsoft Graph or directory lookup is permitted.
- A department response is classified before caching. If any returned
  department resolves to one active person, the complete response is rerun
  through delegated access and marked `private, no-store`.
- User-level cost attribution requires one configured cost period and component
  so ranking and reconciliation remain within one declared pool and currency.
  User/department filters are applied after full-period allocation and never
  change denominators or row amounts.
- Deployment adds one optional Bicep/App Service setting and a warning only. It
  creates no resource, role, permission, secret, connection string, or runtime
  mutation path; `Reader` and `Log Analytics Reader` assignments stay unchanged.
- Configuration JSON is capped at 64 KiB, with at most 500 pseudonymous user
  keys and 100 group identifiers across at most 100 departments.

**Scale/Scope**: One versioned configuration contract; one additive protected
Observe endpoint; two attribution grains (`department`, `user`); two measures
(`usage`, `cost`); one coverage dimension; two eligible identity representations;
one pseudonym generation per deployment; up to 500 user rows; one hosted
application setting; no database, directory client, CLI command, or new Azure
resource/role.

## Constitution Check

*GATE: Checked before Phase 0 research. Re-checked after Phase 1 design.*

Evaluated against `.specify/memory/constitution.md` v1.1.0.

| Principle | Verdict | Evidence |
| --- | --- | --- |
| I. Preserve Public Contracts | PASS | `AGENTOPS_ATTRIBUTION_CONFIG`, the attribution endpoint, filter fields, response models, coverage state/dimension, and cost consumer kinds are additive. Existing CLI commands/flags, `agentops.yaml`, results/evidence schemas, and exit codes are unchanged. |
| II. Enforce Architectural Boundaries | PASS | Strict models and deterministic derivation helpers live in pure `core/`; KQL and attribution composition stay under `agent/observe/`; deployment propagation stays in `services/cockpit_deployment.py`; `cli/app.py` is unchanged. |
| III. Isolate Azure Runtime Integration | PASS | Existing lazy Azure Monitor adapters and aggregate/OBO credential factories are reused. KQL builders and pure models remain testable without Azure packages or credentials. Failures produce explicit coverage/errors with no aggregate fallback. |
| IV. Keep Release Evidence Trustworthy | PASS | Runtime remains read-only/stateless. Provisioning adds only one non-secret setting to existing Cockpit infrastructure, previews the privacy/delegation change, and keeps the exact existing resources and read-only role assignments. Individual evidence is excluded from shared caches and release evidence. |
| V. Verify Every Behavior Change | PASS | Focused tests cover disabled parity, strict configuration, identity eligibility/conflict, pseudonym stability/rotation, token fail-closed behavior, singleton escalation, delegated/no-cache enforcement, coverage, 499+Other reconciliation, cost invariants, UI parity, preview/template allowlists, and end-to-end behavior. |

**Gate result**: PASS. No constitutional exception is required.

Affected public contracts are the optional attribution configuration, additive
Observe filters, new attribution API, additive coverage vocabulary, and
additive cost consumer kinds. Provisioning is limited to propagating the
setting and warning; runtime is a read-only projection over already authorized
Azure Monitor data. Raw identity is outside shared evidence and persistence
boundaries.

### Post-design re-check

The generated [research.md](./research.md), [data-model.md](./data-model.md),
[attribution configuration schema](./contracts/attribution-config.schema.json),
and [Observe API delta](./contracts/observe-attribution-api.openapi.yaml)
preserve all five principles:

- contracts are strict, additive, versioned, and pure;
- identity derivation, mapping, and reconciliation are isolated from Azure
  adapters and UI code;
- only existing Azure Monitor reads and credential paths are used;
- deployment preview exposes the widened delegated boundary while resources,
  roles, Graph permissions, and runtime mutation capabilities remain unchanged;
- privacy, coverage, singleton handling, rotation, exact totals, bounds, and
  Python/JavaScript rendering parity have explicit test seams.

**Post-design verdict**: PASS. No violation or complexity exception was
introduced.

## Project Structure

### Documentation (this feature)

```text
specs/001-user-department-attribution/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── attribution-config.schema.json
│   └── observe-attribution-api.openapi.yaml
├── checklists/
│   └── requirements.md
└── tasks.md                         # Created later by /speckit-tasks
```

### Source Code (repository root)

```text
src/agentops/
├── core/
│   ├── attribution.py              # Pure config, pseudonym, filter-token,
│   │                               # mapping, row, and summary contracts
│   ├── observe.py                  # Add filter fields, coverage vocabulary,
│   │                               # and attribution request/response envelopes
│   └── cost.py                     # Add user/department consumer kinds only
├── agent/
│   ├── cockpit.py                  # Load optional config and expose the
│   │                               # attribution endpoint with cache headers
│   └── observe/
│       ├── attribution.py          # Mapping resolution, safe/delegated
│       │                           # classification, bounds, reconciliation
│       ├── queries.py              # Eligible identity normalization,
│       │                           # KQL pseudonyms, mapping join, 499+Other
│       ├── adapters.py             # Normalize attribution query rows
│       ├── service.py              # Aggregate department and cost composition
│       ├── facade.py               # Credential selection, singleton escalation,
│       │                           # token validation, no-cache protected path
│       ├── principal.py            # Preserve validated group-overage context
│       └── ui.py                   # User/department controls, protected labels,
│                                   # URL tokens, Python/JavaScript parity
├── services/
│   ├── cockpit_deployment.py       # Setting allowlist, validation, redacted
│   │                               # preview, consent warning, azd propagation
│   └── evidence_pack.py            # Explicit exclusion regression guard
└── templates/
    └── cockpit-hosted/infra/
        ├── main.bicep              # Optional attribution app setting only
        └── main.parameters.json    # azd substitution parameter

tests/
├── unit/
│   ├── test_attribution_models.py
│   ├── test_observe_models.py
│   ├── test_observe_queries.py
│   ├── test_observe_adapters.py
│   ├── test_observe_service.py
│   ├── test_observe_facade.py
│   ├── test_observe_principal.py
│   ├── test_observe_ui.py
│   ├── test_cost_allocation.py
│   ├── test_cockpit_modes.py
│   ├── test_cockpit_deployment_preview.py
│   └── test_cockpit_hosted_templates.py
└── integration/
    ├── test_observe_end_to_end.py
    └── test_cockpit_hosted.py

docs/
└── observe.md                      # Enablement, bootstrap, privacy,
                                    # coverage, rotation, limitations

CHANGELOG.md                         # User-visible feature entry
```

**Structure Decision**: Keep the existing single-package Observe architecture.
A focused pure `core/attribution.py` owns the sizeable versioned and
privacy-sensitive contract without adding Azure dependencies to `core/`.
`agent/observe/attribution.py` owns classification and exact grouping logic;
`queries.py` alone owns KQL; `facade.py` remains the credential/cache boundary.
The new route avoids changing existing Observe response shapes. Deployment
changes mirror `AGENTOPS_COST_MODEL` and do not modify the resource or role
allowlists.

## Required Tests

| Seam | Primary files | Required behavior |
| --- | --- | --- |
| Configuration and derivation | `test_attribution_models.py` | Version/size/cardinality bounds, namespace/generation, canonical fingerprint, global mapping uniqueness, stable same-deployment keys, cross-deployment separation, rotation, no raw identity in serialization/log-safe representations |
| Observe contracts | `test_observe_models.py` | Nullable opaque filters, additive coverage state/dimension/details, strict request/response shapes, 500-row bounds |
| KQL | `test_observe_queries.py` | Only `UserAuthenticatedId` and `enduser.id`; explicit conflict detection; no `UserId`/anonymous/session fallback; aggregate projection removes raw identity; mapping `datatable`; deterministic tie order; 499+Other plus unattributed summary |
| Adapters/service | adapter/service tests | Per-source normalization, available/partial/not-reported/inaccessible/ambiguous/error coverage, safe department aggregation, no per-user query fan-out, exact usage totals |
| Principal/facade | principal/facade tests | Exact current-principal matching, group-overage coverage, explicit-user precedence, cross-user group prohibition, user/singleton OBO-only dispatch, missing assertion fail closed, no aggregate retry, shared-cache bypass |
| URL tokens | model/facade/UI tests | Scope/config/generation validation, principal binding for users, semantic config reorder stability, mapping-change invalidation, malformed/stale/cross-principal failure without broadening |
| Cost | `test_cost_allocation.py` | One selected component/currency, unchanged full-period denominators and amounts, user/department consumer grouping, post-allocation filters, exact declared-total reconciliation, Other users sum |
| API/UI | Cockpit/UI tests | New endpoint authorization, `private, no-store` on delegated responses, aggregate cache only after singleton safety, no raw identity in URL/storage/logs, protected labels, server/JavaScript parity |
| Deployment/evidence | preview/template/evidence tests | Optional setting validation/propagation, redacted mapping value, warning before normal confirmation, unchanged resources/roles/permissions, no mapping or individual rows in journals/evidence |
| End to end | integration tests | Disabled parity; complete/partial/absent/conflicting identity; department filter; individual drilldown; singleton escalation; >500 users; restart stability; explicit rotation; cost present/absent |

## Complexity Tracking

No constitutional violations. This section is intentionally empty.
