# Phase 1 Data Model: Observe Console Refinements

**Feature**: `014-observe-console-refinements` | **Date**: 2026-08-31

This document defines the data contracts this feature introduces or changes. All
contracts described as *pure* live in `src/agentops/core/` and must remain free
of Azure SDK imports, network access, and filesystem writes, per Constitution
Principle II. Field names below are descriptive rather than prescriptive where
the existing module already establishes a naming convention; the intent is the
contract, not the spelling.

Existing contracts that this feature reads but does not change — `ObserveScope`,
`ObservedAgent`, `ModelUsage`, `ObservedTool`, `CoverageResult`,
`QueryDiagnostics` — are described only where a relationship matters.

---

## 1. Window selection

**Purpose**: Represent the reporting window as the operator expressed it, so a
relative choice stays relative across refreshes and a fixed choice stays fixed.
Corresponds to the *Window selection* key entity and to FR-009 through FR-014.

**Placement**: Pure, `core/observe.py`.

**Shape**: A discriminated choice between a named relative duration and a custom
fixed interval.

| Field | Type | Notes |
|---|---|---|
| kind | enum: `preset` \| `custom` | Discriminator |
| preset | enum of eight durations | Present only when kind is `preset`. Values: 30 minutes, 1 hour, 6 hours, 12 hours, 1 day, 3 days, 7 days, 30 days |
| start | timestamp, UTC | Present only when kind is `custom` |
| end | timestamp, UTC | Present only when kind is `custom` |
| timezone_label | string | The basis in which the window is presented to the operator, carried for display only |

**Validation rules**:
- Exactly one of `preset` or the `start`/`end` pair is populated, determined by
  `kind`. A payload carrying both, or neither, is invalid.
- For `custom`, `end` must be strictly after `start`. A window failing this is
  rejected with an explanation and must never reach a query builder (FR-014).
- The default when no window has been expressed is the 7-day preset (FR-010).

**Resolution behaviour**: A `preset` window resolves to absolute boundaries at
the moment a query is built, not at the moment it is selected, and re-resolves on
every manual and automatic refresh (FR-012). A `custom` window resolves to
itself. Resolution always produces the absolute UTC pair the existing query
builders already accept, so no query builder learns about presets.

**Relationships**: Replaces the loose `start`/`end` pair currently held on the
filter state as the operator-facing representation. The resolved absolute pair
continues to be what the filter state carries into query construction, which
keeps the existing query layer unchanged.

---

## 2. Scope filter dimension and option

**Purpose**: Let an operator select scope by recognising a value rather than
recalling an identifier. Corresponds to the *Scope filter* key entity and to
FR-001 through FR-008.

**Placement**: Pure, `core/observe.py`.

### Scope filter dimension

| Field | Type | Notes |
|---|---|---|
| dimension | enum | One of: foundry resource, project, agent, model, tool, run key |
| cascade_position | integer | Position in the strict left-to-right hierarchy, in the order listed above |
| selected_values | list of strings | Currently applied selections; empty means unconstrained |

### Scope filter option

| Field | Type | Notes |
|---|---|---|
| value | string | The identifier applied when selected |
| label | string | The human-recognisable form shown to the operator |
| dimension | enum | The dimension this option belongs to |

### Scope filter option set

| Field | Type | Notes |
|---|---|---|
| dimension | enum | The dimension enumerated |
| options | list of scope filter options | Bounded to approximately 50 entries, ordered by observed activity |
| truncated | boolean | True when more distinct values exist than were returned |
| total_observed | integer or null | Distinct count when cheaply known; null when not determined |
| coverage_state | reuses existing coverage state | Why the set may be incomplete |

**Validation rules**:
- `options` never exceeds the configured bound; exceeding it sets `truncated`.
- A dimension's option set is derived within the currently selected window and
  the currently selected values of every dimension to its left, and is never
  narrowed by selections to its right.
- An option's `label` is an identifier or name drawn from telemetry metadata. It
  must never carry generative-AI content.

**Relationships**: The cascade means an option set for a dimension is a function
of `ObserveScope`, the resolved window, and the selected values of all
left-positioned dimensions. Changing a selection invalidates the option sets of
every dimension to its right.

---

## 3. Entity summary

**Purpose**: Give each Overview figure an owning entity family so no count or
rate is unqualified. Corresponds to the *Entity summary* key entity and to
FR-020 through FR-025.

**Placement**: Pure, `core/observe.py`.

| Field | Type | Notes |
|---|---|---|
| entity_family | enum | Runs, agents, models, tools |
| label | string | The family's display name |
| figures | list of summary figures | The headline values for this family |
| coverage_state | reuses existing coverage state | Completeness of the family's data |

Each summary figure carries:

| Field | Type | Notes |
|---|---|---|
| label | string | Names the entity it counts, e.g. distinguishing runs from turns |
| value | number or null | Null denotes not reported, which is distinct from a reported zero |
| unit | string or null | Where the figure has one |
| tone | reuses existing tone vocabulary | Presentation emphasis only |

**Validation rules**:
- A figure's `label` must name its entity; a bare count with no entity noun is
  invalid by FR-021 and SC-006.
- A `value` of zero means an observed zero. A `value` of null means unreported.
  The two must render differently, as the existing cell helpers already do.
- The runs family is ordered first among summaries.

**Relationships**: Assembled entirely from aggregates produced by the existing
Overview query. No entity summary may require an additional telemetry retrieval
(FR-024).

---

## 4. Observed run — extension

**Purpose**: Carry enough information to price a run. Corresponds to the *Run
row* key entity and to FR-034, FR-035 and FR-035a.

**Placement**: Pure, `core/observe.py`. Extends the existing `ObservedRun`.

**New fields**:

| Field | Type | Notes |
|---|---|---|
| model_usage | list of per-model usage entries | One entry per distinct model observed within the run, each carrying that model's own five token counts |
| model_usage_truncated | boolean | True when the run used more models than the bound retained |

**Per-model usage entry**:

| Field | Type | Notes |
|---|---|---|
| model | string | The model that produced the tokens in this entry |
| input_tokens | optional integer | Null when the model reported none, never zero-filled |
| output_tokens | optional integer | Null when the model reported none |
| cache_read_tokens | optional integer | Null when the model reported none |
| cache_write_tokens | optional integer | Null when the model reported none |
| reasoning_tokens | optional integer | Null when the model reported none |

**Existing fields relied upon**: run key, run key kind, agent key, started at,
last activity at, duration, turns, failed turns, tool invocations, and the five
run-level token counts. The run-level counts already exist and are already
summed by the runs query; they remain the totals shown in the token columns and
are **not** re-derived. `model_usage` is additive and exists so the run can be
priced per FR-035a.

**Validation rules**:
- `model_usage` may be empty, which means no model could be resolved for the run.
- Each entry's token counts MUST sum, across entries, to the corresponding
  run-level count whenever `model_usage_truncated` is false. When it is true the
  entries account for less than the run total, and the estimate derived from them
  is partial per FR-039.
- A run whose `model_usage` is empty is not priceable and MUST be reported as not
  priced per FR-038, never as zero.

**Relationships**: Populated by extending the existing runs query to group by
model in an inner aggregation and re-group by run in an outer one, following the
established two-stage `summarize` pattern in `build_models_query`. The `model`
column is already in scope in the runs pipeline via the shared agent extend
clauses, so this adds no query, no round trip, and no telemetry source. The
outer aggregation preserves the existing one-row-per-run shape, so the display
bound is unchanged.

---

## 5. Price entry and price reference

**Purpose**: Supply published unit prices so token usage can be expressed in
money. Corresponds to the *Price entry* key entity and to FR-036 through FR-041.

**Placement**: Pure parsing and validation in `core/observe_pricing.py`. The
packaged file itself lives under `src/agentops/agent/observe/pricing/` and is
read there.

### Price entry

| Field | Type | Notes |
|---|---|---|
| model | string | The model identifier the price applies to |
| token_class | enum | Input, output, and the recognised additional classes |
| unit_price | decimal | Price per token, held as decimal not float |
| currency | string | ISO currency code |

### Price reference

| Field | Type | Notes |
|---|---|---|
| version | string | The reference version, aligned with the accelerator release |
| effective_date | date | The date the prices were published or last verified |
| source | string | Attribution for the published prices |
| entries | list of price entries | The priced models and token classes |

**Validation rules**:
- `unit_price` is a decimal. Monetary arithmetic must not use binary floating
  point.
- An entry with a non-positive `unit_price` is invalid.
- Two entries sharing the same `model` and `token_class` is invalid.
- A reference whose `effective_date` is more than ninety days before the present
  is *stale*: it remains usable and its prices are still applied, but every
  figure derived from it carries a stale marker stating the reference's age
  (SC-015).
- A missing or unreadable reference is not an error. It degrades every cost
  figure to *not priced* with a stated reason, mirroring the graceful
  degradation the packaged-knowledge reader already performs (FR-041).

**Relationships**: The parser accepts an in-memory string and returns a load
result carrying either the reference or the reasons it could not be used, exactly
as the existing declared-cost-model loader does. The `agent/` layer performs the
file read and the caching.

---

## 6. Cost estimate

**Purpose**: Express observed token usage in money, honestly labelled. Corresponds
to the *Cost estimate* key entity and to FR-034, FR-035, and FR-042 through
FR-044.

**Placement**: Pure, `core/observe.py`.

| Field | Type | Notes |
|---|---|---|
| amount | decimal or null | Null when nothing could be priced |
| currency | string or null | Null when `amount` is null |
| completeness | enum: `complete` \| `partial` \| `not_priced` | Always present |
| excluded_components | list of strings | Named components the estimate omits |
| unpriced_run_count | integer or null | For group estimates: covered runs that could not be priced |
| covered_run_count | integer or null | For group estimates: runs included in the amount |
| scope_run_count | integer or null | For group estimates: runs the entity has in scope, whether or not displayed |
| price_reference_version | string or null | The reference the amount was computed from |
| price_reference_effective_date | date or null | The basis date shown alongside the figure |
| is_stale | boolean | True when the reference is more than ninety days old |

**Validation rules**:
- `completeness` is `complete` only when every priceable component of every
  covered run was priced and `excluded_components` is empty.
- `completeness` is `partial` when an amount exists but something was omitted;
  `excluded_components` must then be non-empty, or `unpriced_run_count` must be
  greater than zero, or both.
- `completeness` is `not_priced` when `amount` is null. A reason must still be
  available for display.
- A group estimate's `amount` equals the sum of the estimates of the priced runs
  it covers, and reports `unpriced_run_count` (SC-016).
- A group estimate's `covered_run_count` equals its `scope_run_count`: the amount
  is derived from an aggregation over the entity's whole scope, not from the run
  rows the console displays, so a truncated row set does not reduce it (FR-034d).
- Where that equality cannot be established, `completeness` is at most `partial`
  and the shortfall is reported, so no partial sum is ever presented as a total.
- All amounts in a single estimate share one currency. Amounts in differing
  currencies are never summed; such a group is reported per currency or as
  `partial` with the mismatch named (FR-043).
- An estimate is never displayed without its completeness state and its
  estimate-at-list-price disclaimer (SC-010).

**Relationships**: Attaches to a run row, to an agent aggregate, and to a model
aggregate. It is computed from `ObservedRun` token counts, `ObservedRun.models`,
and the price reference. It is never combined with, reconciled against, or
substituted for the declared-billed-total allocation produced by the existing
cost allocation path (FR-042).

---

## 7. Runs table column declaration

**Purpose**: Remove the label-keyed coupling that makes renaming a column break
its sorting. Supports FR-029 and FR-030.

**Placement**: `agent/observe/ui.py`, as a single declaration rendered by Python
and serialised into the embedded script.

| Field | Type | Notes |
|---|---|---|
| id | string | Stable identifier. Never displayed, never renamed |
| label | string | The displayed header text. Freely renameable |
| sort_key | string or null | The underlying field to sort by; null when not sortable |
| help_text | string or null | Tooltip content |
| priority | enum | Whether the column is core identification and triage, or secondary |

**Validation rules**:
- `id` is unique across the table and is the only key used for sorting, for the
  header cell's data attribute, and for the script's column lookup.
- Renaming `label` must not change `id` or `sort_key`.
- Every column with a non-null `sort_key` must remain sortable after any rename;
  this is asserted directly by test.

**Relationships**: Replaces the current arrangement in which the sort mapping is
keyed by display label and the same labels are separately declared in the
embedded script and in the emitted data attribute. One declaration becomes the
source for all three.

---

## Entity relationship overview

```text
ObserveScope ──┐
               ├──> ScopeFilterOptionSet (per dimension, cascading left to right)
WindowSelection┘         │
      │                  └──> selected values feed the next dimension's options
      │ resolves to absolute bounds at query-build time
      v
  Query layer ──> ObservedRun (+ models)  ──┐
              ──> ObservedAgent             ├──> CostEstimate
              ──> ModelUsage                │        ^
              ──> ObservedTool              │        │
                     │                      │   PriceReference ──> PriceEntry
                     └──> EntitySummary ────┘
                              (Overview, no extra retrieval)

RunsTableColumn ──> renders Runs table headers, sorting, tooltips (one declaration)

CostEstimate  ✗  never combined with  ✗  declared-billed-total allocation
```

## Contracts explicitly unchanged

- `ObserveScope`, `ObservedAgent`, `ModelUsage`, `ObservedTool`,
  `CoverageResult`, `QueryDiagnostics` — read and reused as-is.
- The declared-billed-total cost model and its allocation output — untouched by
  this feature, and deliberately kept separate from the cost estimate.
- The Observe view wire names, including the established mapping of the usage
  view to its models wire name.
