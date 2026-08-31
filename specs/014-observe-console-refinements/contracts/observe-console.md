# Contract: Observe Console Interfaces

**Feature**: `014-observe-console-refinements` | **Date**: 2026-08-31

The Observe console exposes four interface surfaces: the console address, the
local HTTP routes the document calls, the rendered document itself, and the
packaged price reference file. This document states the contract for each,
distinguishing what already exists and must be preserved from what this feature
adds or changes.

The console is hosted locally by the Cockpit and is read-only. No route in this
contract creates, mutates, or deletes anything, and none requires a credential
beyond the one the Cockpit already resolves.

---

## Surface 1 — Console address

The console address is a shareable, reproducible expression of applied scope. It
is governed by an allowlist: only enumerated keys are read from it and written to
it, so a field added elsewhere is excluded by default rather than carried by
default.

### Existing keys — preserved

The current allowlist carries the selected view, the theme, and the eight scope
and window filter keys: Foundry resource, project, agent, model, tool, run key,
cost period, and the window start and end boundaries.

### Changes

| Key | Change | Notes |
|---|---|---|
| window preset | Added | The named relative duration, when a preset is selected |
| window start, window end | Semantics narrowed | Carried only when the window is a custom fixed interval |

### Invariants

- **No generative content**: no field carrying model input, model output, prompt
  text, or any other generative-AI content may appear in the address. This holds
  for every key, existing and added, and is asserted by test.
- **Preset and custom are exclusive**: an address carries either a window preset
  or a start and end pair, never both. An address carrying both is resolved by
  ignoring the boundaries and honouring the preset, and this resolution is
  stated rather than silent.
- **Facet search text is not carried**: text typed to search for a scope option
  is transient input, not applied scope, and never reaches the address.
- **Reproducible**: opening an address on another machine reproduces the same
  applied scope. A preset address reproduces the operator's relative intent; a
  custom address reproduces the exact interval.
- **No client-side persistence**: the address is the only place applied scope
  lives. The console stores nothing in local storage, session storage, or
  cookies, and this remains asserted by test.

---

## Surface 2 — Local HTTP routes

### Existing routes — preserved unchanged

| Method | Path | Purpose |
|---|---|---|
| GET | `/observe` | Renders the console document |
| GET | `/api/observe/discovery` | Resolves the observable scope |
| POST | `/api/observe/query` | Retrieves data for a view |
| POST | `/api/observe/attribution` | Retrieves department attribution |
| POST | `/api/observe/agent-detail` | Retrieves one agent's detail |
| POST | `/api/observe/drilldown` | Retrieves drill-down rows |
| POST | `/api/observe/trace-content` | Retrieves trace content on explicit request |

Their request and response shapes are unchanged by this feature, with two
additive exceptions noted below.

### New route

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/observe/scope-options` | Enumerates selectable values for one scope dimension |

**Why POST**: it follows the established pattern of every other data route on
this surface, which carries scope and window in a request body rather than in a
query string. It is read-only despite the method.

**Request**: identifies the dimension to enumerate, the current scope, the
resolved window, the already-selected values of dimensions to the left in the
cascade, and an optional search fragment.

**Response**: a scope filter option set as defined in the data model — the
bounded, activity-ordered options, whether the set was truncated, the distinct
total when cheaply known, and a coverage state.

**Behavioural contract**:
- The response is bounded to approximately fifty options. It never enumerates an
  unbounded set.
- Options reflect the supplied window and the supplied left-hand selections.
  Selections to the right of the requested dimension are ignored.
- A search fragment filters the enumeration server-side rather than being applied
  to an already-truncated set, so any value in scope is reachable.
- Failure or timeout is not fatal to the console. The affected control degrades
  to free-text entry, which is the console's current behaviour and therefore a
  safe floor.
- The route is served off the render critical path and is cached at
  inventory-length duration, not view-length duration.
- Option labels carry identifiers and names only, never generative content.

### Additive changes to existing route payloads

| Route | Addition | Compatibility |
|---|---|---|
| `/api/observe/query` (runs view) | Each run row gains a per-model token breakdown and a truncation flag; run, agent, and model rows may carry a cost estimate | Additive fields only; existing fields, including the run-level token totals, and their meanings are unchanged |
| `/api/observe/query` (overview view) | The response carries per-entity summaries | Additive; existing overview figures are unchanged |

Both additions are new fields on existing responses. No field is removed,
renamed, or given a new meaning.

### Route invariants

- Every route remains read-only.
- No route acquires a new credential requirement or a new outbound dependency.
- The Overview response must be satisfiable without an additional telemetry
  round-trip relative to today.
- Cost estimate and declared-billed-total allocation appear as separate fields
  and are never merged into a single monetary value.
- A rolled-up cost estimate is derived from an aggregation over the entity's
  scope, not from the run rows in the response. Reaching the response's row bound
  reduces the rows returned; it never reduces a roll-up's coverage.

---

## Surface 3 — Rendered document

The document is server-rendered, fully self-contained, and byte-deterministic
for a given input, which is what makes its visual regression snapshots
meaningful. It references no CDN, loads no external asset, and performs no
outbound request beyond the local Cockpit routes above.

### Runs table columns

The table is declared once, with each column carrying a stable identifier, a
displayed label, an optional sort key, optional help text, and a priority.

**Contract**:
- The stable identifier is what sorting, the header's data attribute, and the
  script's column lookup all key on. It is never displayed and never renamed.
- A column's displayed label may be renamed freely without affecting its sorting.
- Every column that is sortable today remains sortable after any rename, and this
  is asserted directly rather than inferred from markup equality.

### Time presentation

- Every time on a page — window boundaries, row timestamps, and the refresh
  indicator — is expressed in one and the same basis.
- That basis is stated once on the page, adjacent to the time controls.
- The wire and the address continue to carry UTC.
- Before any successful refresh, the indicator states that condition explicitly
  rather than rendering an empty or placeholder time.

### Progressive enhancement

- The copy affordance uses the browser's clipboard capability where available and
  otherwise presents the full value for manual selection. The same markup serves
  both cases.
- Row detail uses a native disclosure element rendered inline beneath its row. It
  renders and remains meaningful without script.
- Every control introduced by this feature is fully operable by keyboard alone.

### Data presentation invariants

- A reported zero and an unreported value render differently. This distinction
  already exists and must survive every change made here.
- A dimension whose displayed rows all share one value is stated once above the
  table and its column is omitted; the column returns automatically when a second
  value appears. A dimension that is entirely unreported is not treated as
  single-valued. When the displayed rows are a truncated subset of the scope, no
  column is omitted, because uniformity across a subset proves nothing about the
  rows not returned.
- No estimated monetary figure renders without its completeness state and its
  estimate-at-list-price disclaimer.
- A figure derived from a price reference more than ninety days past its
  effective date renders with a stale marker stating the reference's age.

---

## Surface 4 — Packaged price reference

**Location**: a data file inside the installed package, beside the code that
reads it, following the precedent already set by the packaged knowledge
checklist.

**Contract**:
- The file is read-only at runtime. The console never writes, uploads, or edits
  it.
- It carries a version, an effective date, a source attribution, and the priced
  entries.
- It must be declared as package data so it is present in an installed
  distribution. Absent that declaration the file is missing from a wheel and
  every cost figure silently degrades — this is the single most likely packaging
  defect for this feature and is covered by test.
- A missing or unreadable file is not an error. It degrades cost figures to *not
  priced* with a stated reason.
- Prices are published list prices in a single currency per entry. They are not
  negotiated rates, not an invoice, and not a forecast, and the presentation says
  so.

---

## Compatibility summary

| Contract | Status |
|---|---|
| CLI commands, flags, exit codes | Unchanged |
| Observe view wire names, including the usage-to-models mapping | Unchanged |
| Existing HTTP routes and their existing fields | Unchanged |
| Address allowlist mechanism | Extended, not replaced |
| Declared-billed-total cost model and allocation | Unchanged and kept separate |
| Doctor, evaluation execution, release evidence | Untouched |
| Runs table column labels | **Changed — user-visible; requires documentation and changelog updates** |
| Time presentation basis | **Changed — user-visible; requires documentation and changelog updates** |
