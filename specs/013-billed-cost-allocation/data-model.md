# Data Model: Billed Cost Allocation

## Conventions

- Public models are strict Pydantic v2 contracts with unknown fields rejected.
- JSON money, weight, credit, and usage-share values are canonical decimal
  strings. Python uses `Decimal`; binary floating-point is never used for
  reconciliation.
- Timestamps are UTC date-times. Cost periods use inclusive start and exclusive
  end boundaries.
- Missing values are `null`; reported zero is `"0"` or the corresponding
  numeric zero and remains distinct.
- ARM resource IDs are canonicalized with the existing Observe helper. Other
  identifiers are trimmed and matched exactly.
- Configuration is non-secret and bounded before parsing.

## 1. CostModel

The complete optional configuration supplied through `AGENTOPS_COST_MODEL`.

| Field | Type | Rules |
| --- | --- | --- |
| `version` | integer | Required; exactly `1`. |
| `periods` | list of `CostPeriod` | 1-24 unique period IDs. |

### Model-level validation

1. Encoded JSON is at most 32 KiB.
2. Period IDs are unique.
3. For a given `(component.id, billing_boundary.kind,
   billing_boundary.value)`, periods must not overlap.
4. Unknown or secret-shaped fields are rejected by the strict schema.
5. The canonical model fingerprint is derived from validated, sorted JSON and
   is used only in cache keys and diagnostics; the raw model is not copied
   there.

## 2. CostPeriod

One billed interval and its independently reconcilable cost components.

| Field | Type | Rules |
| --- | --- | --- |
| `id` | string | Stable 1-64 character identifier; letters, numbers, `.`, `_`, `-`. |
| `starts_at` | datetime | Inclusive UTC boundary. |
| `ends_at` | datetime | Exclusive UTC boundary; later than `starts_at`. |
| `components` | list of `CostComponent` | 1-50; component IDs unique within the period. |

The selected telemetry window is exactly `[starts_at, ends_at)`. A run crossing
either boundary contributes only observations inside the period.

## 3. CostComponent

One declared billed pool that reconciles independently.

| Field | Type | Rules |
| --- | --- | --- |
| `id` | string | Stable 1-64 character identifier; unique in the period. |
| `type` | `CostComponentType` | Closed enum in the compatibility matrix below. |
| `billing_boundary` | `BillingBoundary` | Required; identifies the pool owner/scope. |
| `billed_source` | string | Required 1-256 character non-secret description. |
| `billed_total` | decimal string | Non-negative; must fit `currency_minor_units` exactly. |
| `currency` | string | Three uppercase ASCII letters. |
| `currency_minor_units` | integer | 0-6; required to avoid an embedded currency table. |
| `allocation_model` | `metered` or `commitment` | Must be compatible with component type. |
| `allocation_key` | `AllocationKey` | Preferred observed unit. |
| `fallback_key` | `AllocationKey` or null | Only the compatibility matrix permits it. |
| `token_weights` | `TokenWeights` or null | Required only for `weighted_tokens`. |
| `usage_match` | `UsageMatch` | Required and must contain at least one narrowing selector. |

### CostComponentType

`provisioned_throughput`, `standard_model`, `search`, `grounding`,
`content_safety`, `storage`, `hosted_compute`, `customer_compute`,
`credit_payg`, `credit_prepaid`.

### AllocationKey

`weighted_tokens`, `total_tokens`, `tool_invocations`,
`active_session_seconds`, `credits`, `credit_events`.

### Compatibility matrix

| Component type | Allowed model | Preferred key | Allowed fallback |
| --- | --- | --- | --- |
| `provisioned_throughput` | `commitment` | `weighted_tokens`, `total_tokens` | `total_tokens` when preferred is weighted |
| `standard_model` | `metered` | `weighted_tokens`, `total_tokens` | `total_tokens` when preferred is weighted |
| `search`, `grounding`, `content_safety`, `storage` | `metered` | `tool_invocations` | none |
| `hosted_compute` | `metered` | `active_session_seconds` | none |
| `customer_compute` | either | `active_session_seconds` | none |
| `credit_payg` | `metered` | `credits` | `credit_events` |
| `credit_prepaid` | `commitment` | `credits` | `credit_events` |

Any combination outside this matrix invalidates the entire cost model.
`fallback_key` must differ from `allocation_key`. When either the preferred or
fallback key is `credit_events`, `usage_match.credit_event_operations` must
contain at least one operation name.

## 4. BillingBoundary

The operator-declared boundary to which the billed pool belongs.

| Field | Type | Rules |
| --- | --- | --- |
| `kind` | enum | `resource`, `subscription`, `account`, `pool`, or `custom`. |
| `value` | string | Required 1-512 character stable identifier. |
| `label` | string or null | Optional 1-128 character display label. |

The boundary is provenance, not authorization. ObserveScope remains the
authorization boundary for telemetry reads.

## 5. UsageMatch

An explicit, narrowing-only match from a cost component to normalized observed
usage.

| Field | Type | Rules |
| --- | --- | --- |
| `source_resource_ids` | list of strings | 0-100 canonical ARM IDs. |
| `project_resource_ids` | list of strings | 0-100 canonical ARM IDs. |
| `agent_keys` | list of strings | 0-100 exact normalized keys. |
| `deployments` | list of strings | 0-100 exact deployment names. |
| `models` | list of strings | 0-100 exact model names. |
| `tool_names` | list of strings | 0-100 exact tool names. |
| `runtime_kinds` | list of runtime enum values | 0-6. |
| `credit_event_operations` | list of strings | 0-32 exact normalized operation names; valid only with `credit_events`. |

At least one of the first seven fields must be non-empty. Lists are
deduplicated deterministically. Selector values never widen ObserveScope and
are not raw KQL.

## 6. TokenWeights

The declared relative consumption factors for `weighted_tokens`.

| Field | Type | Rules |
| --- | --- | --- |
| `input_tokens` | positive decimal string or null | Optional. |
| `output_tokens` | positive decimal string or null | Optional. |
| `cache_read_tokens` | positive decimal string or null | Optional. |
| `cache_write_tokens` | positive decimal string or null | Optional. |
| `reasoning_tokens` | positive decimal string or null | Optional. |

At least one weight is required. A weighted numerator is the sum of each
reported token class multiplied by its declared weight. If any class with a
declared weight is unreported, the preferred key is incomplete; the explicit
`total_tokens` fallback is used or the component remains unallocated.

## 7. CostModelLoadResult

Internal startup state. It is not persisted.

| Field | Type | Meaning |
| --- | --- | --- |
| `state` | `absent`, `valid`, `invalid` | Parsing outcome. |
| `model` | `CostModel` or null | Present only when valid. |
| `fingerprint` | string or null | Present only when valid. |
| `error_code` | string or null | Stable non-sensitive code when invalid. |
| `message` | string or null | Actionable, non-sensitive correction text. |

### State transitions

```text
environment missing ───────────────► absent
environment present + valid ───────► valid
environment present + invalid ─────► invalid

absent/invalid -- configuration restart with valid value --> valid
valid -- configuration restart with value removed --------> absent
```

Only `valid` enables the Cost view. `absent` leaves existing Observe unchanged.
`invalid` rejects direct cost requests but does not block other views.

## 8. CostUsageObservation

Internal normalized usage consumed by allocation. It is composed from bounded
models, tools, and runs results and never contains protected content.

| Field | Type | Rules |
| --- | --- | --- |
| `source_resource_id` | string | Originating telemetry resource. |
| `project_resource_id` | string or null | Preserved when reported. |
| `agent_key` | string or null | Null becomes unattributed at agent grain. |
| `tool_name` | string or null | Null becomes unattributed at tool grain. |
| `run_key` | string or null | Null becomes unattributed at run grain. |
| `runtime_kind` | runtime enum | `unknown` allowed. |
| `deployment` | string or null | For usage matching. |
| `model` | string or null | For usage matching. |
| `operation_name` | string or null | For explicit credit-event matching. |
| `input_tokens` | non-negative integer or null | Observed usage. |
| `output_tokens` | non-negative integer or null | Observed usage. |
| `cache_read_tokens` | non-negative integer or null | Observed usage. |
| `cache_write_tokens` | non-negative integer or null | Observed usage. |
| `reasoning_tokens` | non-negative integer or null | Observed usage. |
| `tool_invocations` | non-negative integer or null | Observed usage. |
| `active_session_seconds` | non-negative decimal or null | Period-scoped duration. |
| `credits` | non-negative decimal or null | Directly reported only. |
| `credit_events` | non-negative integer or null | Count of selected operations. |
| `latest_observed_at` | datetime or null | Provenance and freshness. |
| `coverage_complete` | boolean | Whether the key's readable period is complete. |

An observation may contribute to more than one alternate breakdown, but a
single component is reconciled independently inside each breakdown and
breakdowns are never added together.

## 9. CostAllocationRow

One component's amount assigned to one consumer bucket.

| Field | Type | Rules |
| --- | --- | --- |
| `period_id`, `starts_at`, `ends_at` | identifiers/time | Required provenance. |
| `component_id`, `component_type` | identifiers/enum | Required provenance. |
| `billing_boundary`, `billed_source` | objects/string | Required provenance. |
| `allocation_model` | enum | `metered` or `commitment`. |
| `preferred_key`, `applied_key` | allocation key | Differ only for an explicit fallback. |
| `fallback_used` | boolean | True iff keys differ. |
| `breakdown` | enum | `agents`, `tools`, or `runs`. |
| `consumer_kind` | enum | `agent`, `tool`, `run`, `unattributed`. |
| `consumer_key` | string | Stable key; reserved deterministic key for unattributed bucket. |
| `source_resource_id` | string or null | Origin preserved when one source owns the row. |
| `project_resource_id`, `agent_key`, `tool_name`, `run_key` | strings or null | Available identities. |
| `amount` | decimal string | Exactly `currency_minor_units` precision. |
| `currency`, `currency_minor_units` | string/integer | Required. |
| `usage_numerator`, `usage_denominator` | decimal strings | Applied observed share. |
| `usage_unit` | enum | Matches applied key. |
| `rounding_adjustment_minor_units` | integer | Usually 0; records largest-remainder adjustment. |
| `confidence` | enum | `high`, `medium`, `low`, `unavailable`. |
| `coverage_state`, `coverage_reason` | enum/string | Inline explanation. |
| `calculated_at`, `latest_observed_at` | datetimes | Required calculation time; latest observation nullable. |

Rows are sorted by amount descending, then component ID and consumer key.

## 10. CostComponentSummary

Reconciles one component even when allocation rows are bounded.

| Field | Type | Rules |
| --- | --- | --- |
| component provenance fields | same as row | Required. |
| `declared_total` | decimal string | Configuration source of truth. |
| `attributed_amount` | decimal string | Amount assigned to identified consumers. |
| `unattributed_amount` | decimal string | Valid key, missing identity. |
| `unallocated_amount` | decimal string | No usable denominator/allocation. |
| `omitted_allocated_amount` | decimal string | Allocated identified rows hidden by the result bound or `cost_agent_key` display filter. |
| `rows_shown`, `rows_total` | non-negative integers | Truncation evidence. |
| `confidence` | enum | Worst confidence among component rows/coverage. |
| `coverage_state`, `coverage_reason`, `next_action` | coverage | Component-level diagnosis. |

Invariant at currency precision:

```text
declared_total
  = attributed_amount
  + unattributed_amount
  + unallocated_amount

attributed_amount
  = amount represented by shown identified rows
  + omitted_allocated_amount
```

Rounding adjustments are already included in row/summary amounts and therefore
are not an extra pool.

## 11. CostViewData

The `data` object for `view: cost`.

| Field | Type | Rules |
| --- | --- | --- |
| `period` | `CostPeriodRef` | ID and exact boundaries only; no raw configuration. |
| `breakdown` | enum | `agents`, `tools`, or `runs`. |
| `component_filter` | string or null | Applied component ID. |
| `components` | list of `CostComponentSummary` | All selected configured components, including unallocatable ones. |
| `rows` | list of `CostAllocationRow` | At most 500. |
| `currency_subtotals` | list of `CurrencySubtotal` | Grouped only by currency; no conversion. |
| `calculated_at` | datetime | Required. |
| `latest_observed_at` | datetime or null | Latest included activity. |
| `disclaimer` | string | Fixed operational-allocation, not-invoice language. |

`CurrencySubtotal` contains currency, minor-unit precision, and selected
component declared/attributed/unattributed/unallocated totals. Components with
the same currency but different declared minor-unit precision are not combined.

## 12. Observe contract additions

### ObserveFilterState

Add optional:

- `cost_period_id`
- `cost_breakdown` (defaults to `agents` for cost requests)
- `cost_component_id`
- `cost_agent_key`

The IDs are URL-safe and length-bounded. They are validated against the loaded
CostModel by the facade, except `cost_agent_key`, which is validated as a
normalized observed identity. Non-cost views ignore these fields while
preserving them in the shared page URL.

For `view: cost`, the configured period is the authoritative calculation
window. Shared Observe start/end, source, project, model, tool, and run filters
do not participate in calculation and the Cost UI does not submit them.
`cost_agent_key` is applied after the complete allocation to rows that preserve
an `agent_key`; filtered-out identified rows are included in
`omitted_allocated_amount`. It never changes a component denominator or any
allocated amount.

### ObservedRun

Add optional observed fields:

- `cache_read_tokens`
- `cache_write_tokens`
- `reasoning_tokens`
- `credits`
- `credit_events`

All remain `null` when unreported. Credits are never inferred. Existing run UI
does not need to display these fields; the Cost view consumes them.

### CoverageResult

Add:

- `cost_attribution` to `dimension`
- optional `component_id`
- optional `cost_breakdown`
- optional `allocation_key`

Existing states map as follows:

| Cost condition | Coverage state |
| --- | --- |
| Observable allocation capability has no matching configured component | `not_configured` |
| Required source unreadable | `inaccessible` |
| Telemetry path absent | `not_configured` |
| No period activity | `no_data` |
| Allocation key absent | `not_reported` |
| Key/period/identity incomplete | `partial` |
| Complete preferred key and attribution | `available` |
| Query or normalization failure | `error` |

## 13. Allocation lifecycle

```text
select configured period and breakdown
  -> validate period/component IDs
  -> determine required underlying views
  -> collect bounded models/tools/runs concurrently
  -> normalize usage observations
  -> apply component usage_match
  -> classify key coverage and choose preferred/fallback/unavailable
  -> group numerator by consumer or unattributed bucket
  -> allocate integer minor units by largest remainder
  -> reconcile each component
  -> apply optional cost_agent_key as a post-allocation row filter
  -> rank and bound rows
  -> record omitted allocated amounts in summaries
  -> serialize CostViewData + component coverage + diagnostics
```

Any failure before a component has valid declared and observed inputs produces
coverage and an unallocated summary, never a success-shaped zero allocation.
