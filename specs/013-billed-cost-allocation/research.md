# Research: Billed Cost Allocation

## Sources reviewed

- Existing Observe contracts and implementation under `src/agentops/core/observe.py`
  and `src/agentops/agent/observe/`
- Existing hosted deployment setting and role allowlists in
  `src/agentops/services/cockpit_deployment.py` and
  `src/agentops/templates/cockpit-hosted/infra/`
- Specs 011 and 012 for hosted Observe, tools/runs, and granular token classes
- [Microsoft Learn: Configure an App Service app](https://learn.microsoft.com/azure/app-service/configure-common)
- [Microsoft Learn: Copilot Studio telemetry overview](https://learn.microsoft.com/microsoft-copilot-studio/telemetry-overview)
- [Microsoft Learn: Copilot Studio environment-level telemetry](https://learn.microsoft.com/microsoft-copilot-studio/advanced-environment-level-agent-telemetry)
- [Microsoft Learn: Trace LangChain and LangGraph apps](https://learn.microsoft.com/azure/foundry/how-to/develop/langchain-traces#understand-trace-structure)
- Azure AI application best-practice guidance retrieved during planning

## D1. Configuration transport

**Decision**: Use optional `AGENTOPS_COST_MODEL` JSON, parallel to
`AGENTOPS_OBSERVE_SCOPE`. Parse it once at Cockpit startup into a strict
versioned model. Support it in both local and hosted modes. Hosted deployment
propagates it only when present as an allowlisted, previewed, non-secret App
Service setting.

**Rationale**: The issue explicitly requires operator-declared totals and
prohibits application persistence. App Service passes settings as environment
variables at startup, which matches the existing Observe pattern. A 32 KiB
encoded-size cap plus model cardinality limits keeps startup and deployment
payloads predictable. Updating the setting deliberately restarts the hosted app
and provides a clean configuration boundary.

**Alternatives considered**:

- Add fields to `agentops.yaml`: rejected because the flat evaluation config is
  a stable public contract and Cockpit deployment configuration is separate.
- Add a database or upload endpoint: rejected by scope and read-only runtime
  constraints.
- Read a local file path: rejected because hosted App Service would need a new
  storage/deployment contract and path lifecycle.
- Use Azure App Configuration: rejected because it adds a resource, identity
  permission, runtime dependency, and mutable external configuration source.

## D2. Invalid and absent configuration behavior

**Decision**: Represent startup parsing as `absent`, `valid`, or `invalid`.
Absent configuration hides the Cost view and leaves all existing Observe
behavior unchanged. Invalid configuration disables only cost allocation and
returns an actionable configuration error for a direct cost request; it does
not prevent the rest of Cockpit from starting.

**Rationale**: "Fail closed" means no amount may be produced from invalid
inputs, not that unrelated read-only diagnostics should become unavailable.
This also makes removal exactly restore current behavior.

**Alternatives considered**:

- Fail the entire process on invalid JSON: rejected because it unnecessarily
  takes non-cost Observe views offline.
- Ignore invalid fields or load a partial model: rejected because it could
  allocate an incomplete or ambiguous billed pool.

## D3. Cost API and UI integration

**Decision**: Add `cost` to the existing `/api/observe/query` view enum. Add
identifier-only filter fields for `cost_period_id`, `cost_breakdown`
(`agents`, `tools`, `runs`), optional `cost_component_id`, and optional
`cost_agent_key` for agent-specific drill-down. The configured cost period is
the only calculation time window. Shared Observe start/end/source/project/model/
tool/run filters are ignored by cost calculation and are not sent by the Cost
UI. `cost_agent_key` is applied only after the full-period allocation; rows for
other agents become omitted allocated amounts and never change denominators or
row amounts. The response `data` is a `CostViewData` object with component
summaries and bounded allocation rows. The shared response still carries
coverage, diagnostics, partial failures, refresh time, cache status, and result
bounds.

**Rationale**: Cost uses the same scope, authorization, source discovery,
filters, cache semantics, progressive loading, and partial-failure behavior as
the other Observe views. A new route family would duplicate these guarantees.
Component summaries preserve reconciliation even when consumer rows are
bounded.

**Alternatives considered**:

- New `/api/cost/*` endpoints: rejected because they duplicate Observe
  authorization and diagnostics.
- Put billed totals in the query request: rejected because configuration would
  leak into request logs, browser state, and URLs, and callers could bypass the
  reviewed deployment configuration.
- Reuse arbitrary shared Observe filters as allocation inputs: rejected because
  narrowing consumers or time could redistribute the complete billed pool over
  a partial denominator and produce misleading amounts.
- Return one flat row array only: rejected because truncation would make the
  declared pool appear unreconciled.

## D4. Component-to-telemetry matching

**Decision**: Every component has a required `usage_match` object with at least
one narrowing dimension. Allowed dimensions are telemetry source resource IDs,
project resource IDs, agent keys, model deployments, model names, tool names,
and runtime kinds. ARM resource IDs are canonicalized; other dimensions match
normalized values exactly. Selectors only narrow already-authorized data and
are cardinality- and length-bounded.

**Rationale**: A declared billed total is meaningful only when the system knows
which observed usage belongs to it. Billing boundary alone cannot reliably map
an external compute pool, model deployment, or tool resource to telemetry.
Explicit matching is auditable and avoids guesses.

When telemetry exposes a supported allocation key but no configured component
matches it, coverage reports the observable capability and key as
`not_configured`. It does not infer whether that usage represents a standard
model, provisioned commitment, hosted compute, customer compute, pay-as-you-go
credit, or prepaid pool; those billing interpretations exist only in the cost
model.

**Alternatives considered**:

- Infer from component type: rejected because one Observe scope can contain
  several deployments or tool resources with separate billed pools.
- Use arbitrary query fragments: rejected because it exposes query syntax and
  creates an injection and support boundary.
- Allow an empty selector to mean all usage: rejected because a configuration
  typo could allocate a narrow billed pool across the entire scope.

## D5. Usage collection and query count

**Decision**: Compose cost usage from the existing bounded `models`, `tools`,
and `runs` normalized rows. Extend `ObservedRun` and its existing query with
optional granular token classes, directly reported credit quantity, and
credit-event count. Query each required underlying view at most once per cost
request and source, concurrently; never query per cost component.

**Rationale**:

- model rows already provide agent-level token usage and granular classes;
- tool rows already provide agent/tool invocation counts;
- run rows already provide run/agent tokens, tool calls, and duration;
- additive run fields close the weighted-token and credit gaps without a new
  raw-telemetry API.

This design reuses established normalization, source attribution, query bounds,
coverage, and caches.

**Alternatives considered**:

- One KQL request per component: rejected because configuration cardinality
  would directly multiply Azure Monitor load and latency.
- A large new union query containing every metric: rejected because it
  duplicates mature models/tools/runs correlation logic.
- Allocate weighted model spend to runs using only total tokens: rejected
  because it would ignore reported granular dimensions unless the operator
  explicitly chose that fallback.

## D6. Exact money and rounding

**Decision**: The contract carries money and weights as canonical decimal
strings. Each component declares `currency_minor_units` from 0 through 6.
The billed total must already fit that precision. Allocation converts the total
to integer minor units, floors each proportional share, then distributes the
remaining minor units by largest fractional remainder; ties break by stable
consumer key. Each adjusted row identifies its rounding adjustment.

**Rationale**: Integer minor-unit reconciliation prevents binary
floating-point drift and guarantees that attributed, unattributed, unallocated,
omitted, and rounding-adjusted amounts equal the declared total exactly.
Declaring precision avoids embedding a currency table or guessing for
zero-decimal and non-standard units.

**Alternatives considered**:

- Binary floating-point with final rounding: rejected because totals can drift.
- Always use two decimals: rejected because currencies and credit/accounting
  units vary.
- Put the residual in a hidden balancing row: rejected because every amount
  must be auditable.

## D7. Allocation-key compatibility and fallbacks

**Decision**: Validate component type, allocation model, and allocation key as a
closed compatibility matrix:

| Component type | Model | Preferred key | Allowed explicit fallback |
| --- | --- | --- | --- |
| `provisioned_throughput` | `commitment` | `weighted_tokens` or `total_tokens` | `total_tokens` |
| `standard_model` | `metered` | `weighted_tokens` or `total_tokens` | `total_tokens` |
| `search`, `grounding`, `content_safety`, `storage` | `metered` | `tool_invocations` | none |
| `hosted_compute` | `metered` | `active_session_seconds` | none |
| `customer_compute` | `metered` or `commitment` | `active_session_seconds` | none |
| `credit_payg` | `metered` | `credits` | `credit_events` |
| `credit_prepaid` | `commitment` | `credits` | `credit_events` |

Weighted token components declare positive weights for one or more of
`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, and
`reasoning_tokens`. Missing required dimensions trigger only the declared
`total_tokens` fallback; otherwise the component remains unallocated.

**Rationale**: A closed matrix prevents semantically invalid substitutions such
as allocating compute by token count or credits by tool calls.

**Alternatives considered**:

- Any key for any component: rejected because it creates plausible-looking but
  meaningless allocations.
- Automatic fallback to request count: rejected because the specification
  requires explicit fallback and preserved units.

## D8. Credit telemetry

**Decision**: Normalize credit quantity only when telemetry directly reports a
non-negative credit value. Current Copilot Studio environment-level telemetry
documents `InvokeAgent`, `ExecuteTool`, and `OutputMessages` spans and their
correlation, but does not document a general Copilot Credit quantity on those
spans. Therefore the first release contains no rate table and does not derive
credits from tokens, messages, tools, or runtime kind.

When a credit component explicitly declares `credit_events` fallback, the
operator must select the normalized operation names that count as
credit-consuming events. The resulting allocation distributes a declared
pool by event share, labels the fallback, and has medium confidence only when
event coverage and attribution are complete. Without direct credits or the
explicit event selector, the amount remains unallocated.

**Rationale**: Microsoft documentation confirms environment-level telemetry is
trace-oriented and OTel-aligned but does not establish that each agent turn or
tool span consumes a fixed number of credits. A built-in scenario rate table
would become billing logic, conflict with the lower-privilege declared-total
design, and quickly drift.

**Alternatives considered**:

- Embed Copilot Studio scenario rates: rejected because this feature is not a
  price calculator and rates can change.
- Treat every agent turn as one credit: rejected because turns and credits are
  not equivalent.
- Exclude credit runtimes entirely: rejected because explicit event-share
  allocation of a declared pool remains useful and honest.

## D9. Confidence and coverage

**Decision**: Compute confidence with fixed precedence:

1. `unavailable` when no allocation is produced;
2. `low` when the readable period, key coverage, or consumer attribution is
   partial;
3. `medium` when coverage is complete but an explicit fallback key is used;
4. `high` only when the preferred key and attribution are complete.

Reuse `CoverageResult` with additive `cost_attribution`, `component_id`, and
`breakdown` context. Map conditions onto existing coverage states where
possible: not configured, inaccessible, no data, not reported, partial,
available, and error.

**Rationale**: One deterministic classifier keeps API and UI labels identical.
Coverage describes why data is incomplete; confidence describes how that
incompleteness affects a displayed amount.

**Alternatives considered**:

- Numeric confidence percentages: rejected because no defensible statistical
  calibration exists.
- Infer confidence separately in the UI: rejected because Python and
  JavaScript paths would drift.

## D10. Unattributed, unallocated, and truncated amounts

**Decision**:

- usage with a valid key but missing consumer identity enters an explicit
  unattributed bucket at that breakdown;
- a component with no positive denominator remains fully unallocated;
- allocation rows are bounded to 500, ordered by allocated amount descending,
  then component and consumer key;
- component summaries retain `omitted_allocated_amount` and omitted row count
  when the response bound hides low-ranked consumer rows.

**Rationale**: These are different operational facts and must not be collapsed.
Summaries keep every component exactly reconciled even when row bounds apply.

**Alternatives considered**:

- Drop unattributed usage: rejected because it under-allocates the pool.
- Divide a zero-denominator pool evenly: rejected because it invents usage.
- Return unbounded rows to preserve reconciliation: rejected by Observe
  performance and payload constraints.

## D11. Cache and sensitive-data boundary

**Decision**: Add the cost-model fingerprint to cost cache keys, never the raw
JSON. Cache only validated normalized cost results under the existing two-minute
Observe TTL. Reject secret-shaped configuration keys through strict schemas and
the existing deployment setting validation. Cost queries project metadata and
numeric usage only; no prompt, response, tool arguments, tool results, or
protected content.

**Rationale**: A fingerprint prevents stale results after configuration changes
without copying the billed model into cache keys or diagnostics. Cost totals
are non-secret by contract but still need bounded exposure.

**Alternatives considered**:

- Disable caching: rejected because a Cost view composes several existing
  telemetry views and would repeatedly issue identical reads.
- Cache raw configuration with results: rejected because the cache needs only a
  stable identity, not a duplicate payload.

## D12. Deployment and privilege surface

**Decision**: Add optional `AGENTOPS_COST_MODEL` to
`ALLOWED_SETTINGS_KEYS`, deployment preview/application settings, the Bicep
parameter, and the Web App setting list. Preview shows the exact non-secret
configuration that will be deployed. No resource, role assignment, identity
permission, API permission, or runtime client changes.

**Rationale**: This is the lower-privilege option recommended by issue #443 and
fits the constitution's allowed deployment scope for non-secret application
settings.

**Alternatives considered**:

- Add Cost Management Reader: rejected for the first release because it widens
  privilege and is unnecessary for declared totals.
- Let the running app update its setting: rejected because Cockpit runtime must
  remain read-only.
