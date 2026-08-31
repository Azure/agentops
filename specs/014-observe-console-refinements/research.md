# Phase 0 Research: Observe Console Refinements

**Feature**: `014-observe-console-refinements` | **Date**: 2026-08-31

This document resolves every open technical question raised by the Technical
Context before design begins. Each entry states what was decided, why, and what
was rejected. Findings are grounded in a direct reading of the existing Observe
module rather than assumption; file and line references are given where a
decision hinges on current behaviour.

---

## 1. Where the list-price reference lives and what shape it takes

**Decision**: Ship the reference as a single YAML file inside a new packaged
data directory, `src/agentops/agent/observe/pricing/`, read through
`importlib.resources` with a one-entry cache, and parse it with a new pure
module `src/agentops/core/observe_pricing.py` that accepts an in-memory string
rather than a path. Add the directory to `[tool.setuptools.package-data]` in
`pyproject.toml`. Every entry carries a model identifier, a currency, per-token
unit prices per token class, a reference version, an effective date, and a
source attribution.

**Rationale**: Two existing patterns in this repository already answer this
question, and the constitution requires reusing established patterns before
introducing new abstractions. `src/agentops/agent/knowledge/__init__.py` is the
packaged-data precedent: it resolves its file with
`resources.files(__name__).joinpath(...)`, wraps the read in a single-entry
cache, and degrades to an empty collection on `FileNotFoundError` or `OSError`
with a warning rather than raising. That degradation behaviour is exactly what
FR-041 asks for when a reference is missing. `src/agentops/core/cost.py`
supplies the purity precedent: `load_cost_model(raw: str | None)` takes a string,
not a path, so that parsing and validation stay in `core/` while the actual file
read happens in `agent/`. Mirroring both keeps `core/` free of filesystem access
as Principle II demands, and keeps the price parser unit-testable without
touching disk.

YAML rather than CSV or JSON because the reference is human-maintained and
human-reviewed at each release, it is naturally nested (a model has several
token classes, each with a price), and `ruamel.yaml` is already a core
dependency so no new package enters the dependency set. CSV would flatten the
per-token-class structure into repetition; JSON would be materially worse to
review in a pull request.

The explicit version and effective date are not decoration. Q2 of the
clarification session fixed the maintenance model: the reference is versioned
with the accelerator, refreshed each release, and after ninety days is marked
stale but still displayed. That rule cannot be implemented without an effective
date in the data itself, and the displayed provenance required by FR-036 cannot
be honest without a recorded source.

**Alternatives considered**: Fetching live prices from a public pricing API was
rejected outright — it would put a network call on a console that is guaranteed
read-only and offline-capable, would introduce a credential or egress
requirement, and would make the console's output non-deterministic, breaking the
snapshot tests. Letting the operator supply their own price file was rejected
because Q2 explicitly chose a shipped reference requiring no operator action,
and because the console is out of scope for editing or uploading a reference
(spec, Out of Scope). A workspace override file, as `agent/knowledge/` supports,
was considered and deferred: it is a small, additive extension that can be added
later without changing the contract, and adding it now would create an untested
configuration surface for a capability nobody has asked for.

---

## 2. How scope filter options are retrieved

**Decision**: Add a dedicated facet query builder to `queries.py` that emits a
`distinct`-then-`take` KQL fragment per requested dimension, bounded to
approximately fifty values ordered by observed activity. Execute it through a
separate service call served by its own Cockpit route, not inline with the view
query. Cache the result in the existing `ObserveCache` under a key derived from
identity, scope, dimension, and window, using the longer inventory-style time to
live rather than the short view time to live. When the operator types beyond the
returned set, issue a filtered facet query for the typed prefix rather than
enumerating everything.

**Rationale**: No distinct or facet capability exists today. The current filter
bar is six free-text inputs (`ui.py`, filter form) and there is no builder in
`queries.py` that enumerates unique values, so this is genuinely new query
surface rather than a reuse. Q1 of the clarification session chose top-N plus
search-on-demand precisely to avoid unbounded enumeration, and SC-013 sets a
one-second budget for a scope of roughly a thousand agents, so an unbounded
`distinct` is not acceptable: without an explicit bound a KQL `distinct` returns
everything the workspace holds.

The choice to run facets outside the view batch deserves the most scrutiny,
because at first glance the opposite looks cheaper. The batch executor accepts
up to ten queries and issues one round-trip
(`MAX_SOURCES_PER_BATCH = 10`, `execute_source_batch`), so a facet query appears
to be free if a slot is spare. The trouble is that a slot is consumed per
telemetry source, not per query kind: the batch already carries one view query
per source, so with six or more sources adding facets pushes the batch over the
limit and produces a second round-trip on the render critical path, against a
ten-second request deadline that the view render must already fit inside. Facets
also change far more slowly than metrics, which is why the codebase already
distinguishes a two-minute view cache from a fifteen-minute inventory cache. Put
together, facets belong on their own cached path where a slow or failed facet
retrieval degrades the filter control to free text — which is the current
behaviour, and therefore a safe floor — instead of degrading the whole view.

Ordering by observed activity rather than alphabetically matters for the same
reason the cap exists: with a thousand agents, the fifty an operator is most
likely to want are the fifty that have been active, not the fifty whose
identifiers sort first.

**Alternatives considered**: Deriving options client-side from rows already
rendered was rejected because the rendered set is capped and filtered, so the
options would silently exclude exactly the entities the operator is trying to
find. Adding the facet query to the existing view batch was rejected for the
round-trip and deadline reasons above. Pre-computing a full inventory on console
start was rejected because the option set must shrink and grow with the selected
window (spec, Assumptions), which a start-time snapshot cannot honour.

---

## 3. Extending the address allowlist without leaking content

**Decision**: Extend the existing allowlist tuple in `ui.py` with any newly
carried scope key, and add a test asserting that the allowlist contains no
generative-content field. Do not replace the allowlist mechanism. Carry only
scope identifiers, the selected window preset name, and — for a custom interval
only — its absolute boundaries. Carry no free-text search term used against the
facet endpoint.

**Rationale**: The allowlist already exists and already enumerates exactly eight
filter keys plus the view and theme, and the existing test suite already asserts
that no personally identifying or generative content reaches the query string.
FR-008 preserves that guarantee, so the correct move is to extend a working
mechanism rather than generalise it. The one non-obvious call is the facet
search term: it is operator-typed free text, and although in practice it will be
a fragment of an agent name, treating it as address-worthy would create a
precedent for putting typed text in a shareable URL. It is transient input, not
applied scope, so it stays out — which also matches Q3's framing that the address
holds the applied scope and nothing else.

**Alternatives considered**: Replacing the allowlist with a denylist of known
content fields was rejected as strictly weaker: a new content field added later
would default to being carried rather than default to being excluded.

---

## 4. Estimating cost per execution when runs carry no model

**Decision**: Attribute tokens to the model that produced them by grouping the
runs query by `model` in an inner aggregation and re-grouping to one row per run
in an outer one, carrying the resulting per-model token breakdown on the
`ObservedRun` contract. Price each model's tokens at that model's own rates and
sum. When no model can be resolved for a run, present no figure and state why.

**Rationale**: This is the sharpest constraint uncovered during research, and it
had to be resolved before design because it determines whether Q4's answer is
buildable at all. The first reading of the code suggested it was not: `ObservedRun`
has five token fields but no model field, and `build_runs_query` groups only by
project, agent key, run key and run key kind, so its output carries no model.

Reading further overturned that. The runs pipeline calls the shared agent extend
clauses, and those clauses already derive `model` per span from the response
model falling back to the request model. `model` is therefore in scope in the
runs pipeline before its `summarize`; the query simply chooses not to group by
it. Nothing about the telemetry prevents the attribution — only the current
shape of one aggregation does.

The two-stage aggregation that fixes it is not a new pattern either. The models
query already runs an inner `summarize` and an outer one that packs the inner
results into a bag, so the shape is established in the same module and can be
followed rather than invented.

That matters because the alternative was to price a multi-model run by applying
one model's rates to the run's combined token totals, which produces a
confident-looking figure that is wrong by an unbounded margin whenever the
models differ in price. Degrading such runs to a partial estimate would have been
honest but needlessly lossy, given the attribution is available. Inferring the
model from the run's agent would be worse than useless: silently wrong for any
run that used a model other than its agent's principal one, with no way for the
operator to tell.

Cost is bounded. The inner aggregation carries more rows than today, but the
bounded-aggregate helper aggregates before it takes, so the display bound and
the returned row shape are both unchanged. A run that used one model produces
one entry and prices exactly; a run that used several produces several entries
and still prices exactly.

**Alternatives considered**: Attributing run tokens to the agent's principal
model was rejected as silently incorrect. Pricing a multi-model run at a single
model's rates, and labelling the result partial, was rejected once the per-model
attribution proved available — it would have shipped a knowingly wrong number
under a hedge. Re-querying per run to obtain a per-turn model breakdown was
rejected because it turns one bounded query into a query per run, which cannot
meet SC-011's three-second budget for a thousand runs. Dropping per-run cost and
offering only per-agent and per-model totals was rejected because it contradicts
Q4, though it remains the natural fallback if the second aggregation proves
unexpectedly costly at scale.

---

## 5. Which basis time is presented on

**Decision**: Present timestamps in the viewer's local timezone with the offset
shown, and continue to transmit and store every boundary in UTC. Apply the same
basis to the refresh indicator, run timestamps, and window boundaries, and
render the basis explicitly rather than leaving it implied.

**Rationale**: This decision corrects a real inconsistency in the current
console, which is very likely what prompted the review feedback. The window
inputs are already local: the script converts a UTC instant to browser-local for
display in the datetime inputs and back to UTC on submit. But the refresh
indicator and compact timestamps are rendered in UTC on both sides — the Python
formatter emits a UTC-suffixed string and the JavaScript compact formatter reads
UTC components. The operator therefore sets a window in their own working day and
then reads a refresh moment in another basis, with nothing on screen saying so.
That is precisely the arithmetic the spec asks to remove.

The spec's Assumptions already resolved which way to move: the refresh indicator
moves onto the window's basis rather than the window moving onto UTC, because
the operator reasons about their own working day. Showing the offset is what
keeps the change safe — a bare local time is ambiguous when an operator shares a
screenshot, whereas a local time with its offset is unambiguous and still
readable. Keeping the wire in UTC preserves the existing address contract and
avoids making a shared link mean different windows for different recipients.

**Alternatives considered**: Moving everything to UTC would have been a smaller
code change and is defensible for a distributed team, but it was rejected by the
spec's own reasoning and would make the console harder to use for the single
operator it is designed for. Offering a timezone selector was rejected as
unrequested configuration surface for a read-only console; the browser already
knows the answer.

---

## 6. Renaming Runs columns without breaking sorting

**Decision**: Before renaming any label, change the sort mapping to be keyed by
a stable column identifier rather than by the displayed label, and derive both
the Python-rendered header cells and the JavaScript column definitions from one
declaration. Only then apply the new labels. Add a regression test that asserts
every sortable column still resolves to its sort key after the rename.

**Rationale**: This is the highest-risk mechanical change in the feature. The
Runs sort map is keyed by display label — the mapping literally uses
`"Started in range"` as the key for `started_at` — and the same labels are
declared a second time in the embedded JavaScript column definitions, and a third
time as the `data-label` attribute emitted by the header cell helper. Renaming a
label in one place therefore breaks sorting silently: the header still renders,
the click still registers, and nothing sorts. FR-030 forbids exactly that kind of
behavioural regression while FR-029 requires the rename, so the two requirements
are only jointly satisfiable if the coupling is removed first.

Research found nine constructs duplicated between Python and JavaScript,
including view identifiers, filter keys, the sort map, timestamp formatting, the
default range, and the auto-refresh interval. De-duplicating all nine is out of
scope, but the ones this feature touches must be unified or the feature will
reintroduce drift it cannot test for. Deriving both sides from a single
declaration is the minimal version of that: one table of columns, each with an
identifier, a label, help text, and a sort key, rendered by Python and serialised
into the script.

**Alternatives considered**: Renaming labels and updating all three sites by hand
was rejected — it works once and rots immediately, and the snapshot tests would
not catch a broken sort because the markup would still be byte-identical in the
unsorted state. Keeping old labels as hidden sort aliases was rejected as a
mechanism that hides the coupling instead of removing it.

---

## 7. Copy affordance and inline expansion in a self-contained document

**Decision**: Implement the copy control using the browser's asynchronous
clipboard capability with a synchronous selection fallback, and implement row
detail as a native disclosure element rendered inline beneath the row rather
than as a separate column or a modal. Both are rendered server-side and enhanced
by the existing embedded script; neither introduces a dependency.

**Rationale**: Neither capability exists today — research confirmed there is no
copy-to-clipboard helper and no expand-row helper anywhere in `ui.py`; the only
detail affordances are separate agent and trace detail shells reached by
navigation. So both are new, and the governing constraint is that the document
must stay self-contained with no CDN and no build step, which rules out any
component library.

The clipboard capability is unavailable in insecure contexts and in some
embedded browsers, which is why the spec's Assumptions already commit to offering
the full value for manual selection when it is unavailable. That fallback is also
the right no-JavaScript behaviour, so the same markup serves both cases.

A native disclosure element for expansion is preferred over a custom widget
because it brings keyboard operation, focus handling, and screen-reader semantics
without hand-written ARIA, which matters given the console's existing
accessibility invariants and the fact that its correctness is asserted by tests.
It also renders identically without script, keeping the document meaningful when
JavaScript is disabled and keeping snapshots deterministic.

**Alternatives considered**: A modal dialog was rejected because it hides the
surrounding table, forces focus management, and makes comparing two rows
impossible. A dedicated details column was explicitly what the review feedback
asked to remove, on the grounds that it squeezes the table for a control that
carries no data.

---

## 8. Enriching Overview without an additional round-trip

**Decision**: Assemble the Overview's per-entity summaries from aggregates the
existing Overview query already computes, adding aggregation clauses to that one
query where a needed figure is genuinely absent, and never issuing a second
retrieval. If a summary cannot be satisfied within that single query inside the
existing deadline, drop summaries in the order given by the spec, keeping runs
last.

**Rationale**: FR-024 and SC-011 make this a hard design bound rather than a
preference: the enriched Overview must not cost extra telemetry retrieval and
must render within three seconds for a thousand runs, against a ten-second
request deadline shared with every other source. The batch executor gives useful
headroom here — it already issues one round-trip for up to ten sources — so
adding aggregation to an existing query is cheap while adding a query per entity
family is not, since it multiplies slot consumption by the number of families and
risks a second batch.

The spec's Assumptions already pre-commit the fallback: per-entity summaries with
runs first, and if the bound cannot be met, the runs summary is the one that must
survive. Recording that here means the fallback is a planned degradation rather
than an improvisation during implementation.

**Alternatives considered**: Lazily loading each summary after first paint was
rejected because it produces exactly the piecemeal, shifting Overview the feature
exists to fix, and because a summary that arrives late is one the operator has
already left the tab to find manually. Reusing the other tabs' view queries
directly was rejected because those queries are shaped and capped for tabular
display, not for aggregate summary, and would over-retrieve.

---

## 9. Suppressing single-valued dimensions

**Decision**: Evaluate distinct-value cardinality per dimension over the rows
actually being displayed, and when a dimension carries exactly one value across
those rows, state it once above the table and omit its column — but only when
the displayed rows are the complete set for the scope. When the result is
truncated, keep every column. Restore a column automatically when a second value
appears. Apply the rule generally to any dimension that qualifies, not only to
the correlation dimension that prompted it.

**Rationale**: FR-033 generalises the review's specific complaint — that the
correlation column always shows one value — into a rule, and the spec's
Assumptions confirm that generalisation was deliberate. Computing cardinality
over displayed rows is what makes the check free: the displayed set is already in
memory, and the behaviour self-corrects as filters change. Computing it over the
whole scope would require another query.

The truncation guard is what keeps that shortcut honest. FR-033 is worded over
every row *in scope*, and when the row bound is reached the displayed rows are a
ranked subset, so uniformity across them establishes nothing about the scope.
Collapsing a column there would state a single value as fact for a population the
console never examined, which is precisely the class of quiet falsehood the
console exists to prevent. The guard costs nothing to implement because the
existing result bounds already carry a truncation flag alongside the rows, so the
rule reads that flag rather than asking a new question. FR-033a records this.

The subtlety worth recording is that a dimension with one value is not the same
as a dimension with no values. A dimension that is entirely unreported must not
be promoted to a confident-looking statement above the table; the existing
console already distinguishes a reported zero from an unreported value in cell
rendering, and that distinction must survive this promotion.

**Alternatives considered**: Hard-coding the rule to the correlation column was
rejected as solving one instance of a general problem. Making suppression an
operator preference was rejected as configuration surface for something the data
can decide correctly on its own.

---

## 10. Keeping estimated cost separate from billed allocation

**Decision**: Model estimated cost as an entirely separate figure with its own
contract, its own provenance, and its own presentation, computed from observed
tokens and the shipped price reference. Never sum it with, reconcile it against,
or substitute it for the declared-billed-total allocation. Refuse to produce a
combined total across differing currencies.

**Rationale**: Research confirmed the two are structurally different rather than
merely differently sourced. The existing allocation logic takes a total the
operator has declared and rates it across observations proportionally by an
allocation key; it has no notion of a unit price anywhere. Estimated cost
multiplies observed token counts by published unit prices and answers a different
question. FR-042 requires them to stay distinct and FR-043 forbids mixed-currency
totals, and the spec's Assumptions spell out the two questions being answered.
Because both figures may be on screen at once, the risk of an operator reading
one as the other is real, which is why provenance and the estimate label are part
of the figure rather than a footnote.

**Alternatives considered**: Falling back to estimated cost when no billed total
is declared was rejected because it would make a screen showing money mean
different things on different days without saying so. Reconciling the two into a
variance figure was rejected as a genuinely useful capability that nonetheless
requires both inputs to be trustworthy and comparable, which is a larger question
than this feature answers.

---

## 11. Representing relative window presets

**Decision**: Model the window as a discriminated choice: either one of eight
named relative durations, or a custom fixed interval. Carry the chosen preset by
name in the address when a preset is selected, and carry absolute boundaries in
the address only when Custom is selected. Resolve a relative preset to absolute
boundaries at the moment each query is built — including on manual and automatic
refresh — never at selection time. Reject a custom interval whose end is not
after its start before any query is issued.

**Rationale**: The first draft of this decision resolved presets to absolute
boundaries once, at selection, so that the address always carried a fixed
interval. Re-reading the requirements showed that to be wrong. A preset must be
re-evaluated against the current moment on every query, including both refresh
paths, which means a preset is a live relative window and not a shorthand for
boundaries captured once. Freezing it at selection would make the auto-refresh
cycle re-query an ageing, stationary window — the console would keep refreshing
and keep showing the same seven days, drifting further from the present with
every cycle. That is a genuine defect, not a stylistic difference, so the address
has to carry the preset itself.

That reading also settles what a shared link means, which is the tension with
carrying scope in the address. A shared preset link reproduces the operator's
intent — the last seven days — rather than their instant, and a shared Custom
link reproduces the exact interval. Both are defensible because the operator
chose which one they were expressing when they picked a preset or opened Custom.
The discriminated shape is what makes that distinction representable at all; a
single pair of boundaries cannot express "the last seven days, whenever you open
this".

Eight presets are required rather than one, spanning thirty minutes to thirty
days, so the seven-day case is the default rather than the only option. Seven
days is already the effective default on both sides — the query layer's default
lookback and the script's default range agree — so that part of the requirement
makes an existing behaviour visible rather than changing it. The query builders
accept only absolute boundaries, which is why resolution happens as the query is
built rather than being pushed down into them.

**Alternatives considered**: Resolving presets to absolute boundaries at
selection was rejected for the stale-refresh defect described above. Carrying
both the preset name and its resolved boundaries in the address was rejected as
redundant and ambiguous — the two can disagree after any elapsed time, and
nothing would say which one wins. Offering only the seven-day preset was
rejected because the requirements name eight durations.

---

## 12. Behaviour at the row bound

**Decision**: Derive every rolled-up estimated cost from a server-side
aggregation keyed by entity and model, never by summing the run rows the console
happens to be displaying. Treat the existing row bound as a display bound only,
and let it govern how many rows are drawn without governing what any total means.
Keep the runs table a plain full render of the bounded row set, and hold the
per-row additions this feature introduces to markup that costs no more than the
cells already present.

**Rationale**: The query layer already solves the hard half of this. Every
bounded aggregate summarises first, orders by recency, returns a capped set of
rows, and computes the in-scope total in the same query, so a large scope costs
one bounded round-trip rather than a proportional one, and the result carries
both what was shown and what exists. The console already surfaces that as a
"showing N of M rows in scope" notice with a truncation marker. None of that
needed changing.

What does need care is that this feature is the first to put *money* on top of
that bounded set. Summing run rows for an agent's roll-up would silently inherit
the display bound: in a scope where the rows are capped, the figure would cover
the retained rows only while presenting itself as the entity's total, and the
run-level unpriced count required by FR-034b would report zero because every row
it examined was priced. A cost that understates by an unstated factor is worse
than no cost, and it fails the console's standing obligation not to state a
number it cannot support. Aggregating server-side by entity and model avoids this
entirely, because the aggregation happens before any bound is applied — which is
also the only shape that can price correctly, since prices are per model and per
token type and an entity-level token total cannot be split across the models that
produced it. FR-034d and the revised SC-016 record the requirement.

The render cost is the remaining exposure and it is bounded but not free. At the
row bound the table draws thousands of rows across its full column set, and this
feature adds a copy affordance and an inline disclosure to each one. Native
disclosure elements keep collapsed content cheap and require no scripting, and
suppressing single-valued columns removes work rather than adding it, so the
additions are proportionate — but proportionate is not measured. SC-017 states
the obligation at the bound so it is verified rather than assumed.

**Alternatives considered**: Introducing pagination or windowed rendering was
rejected as a change to the console's result model that reaches well past this
feature's scope, and the row bound with its in-scope total is a coherent
existing answer rather than a defect to route around. Raising the row bound was
rejected because it moves the same problem further out while multiplying render
cost. Computing the roll-up from run rows and labelling it partial whenever the
result is truncated was rejected because a total that is usually right and
occasionally a tenth of the truth is not made safe by a label; the aggregation
that is always right costs the same query.

---

## Summary of resolved unknowns

| Question | Resolution |
|---|---|
| Price reference format and location | Packaged YAML under `agent/observe/pricing/`, pure parser in `core/`, `package-data` entry required |
| Facet option retrieval | New bounded facet builder, separate cached route, inventory-length cache, free-text fallback |
| Address allowlist | Extend existing tuple with scope keys only; facet search term stays out |
| Per-run cost without a run model | Attribute tokens per model inside `build_runs_query` with a two-stage `summarize`; price each model at its own rates; absent when no model resolves |
| Time basis | Viewer-local with offset shown, UTC on the wire, applied consistently |
| Column rename versus sorting | Re-key sort by stable identifier and unify the Python/JS column declaration before renaming |
| Copy and expansion | Clipboard capability with selection fallback; native disclosure inline, no dependency |
| Overview enrichment | Aggregate within the existing single query; degrade by dropping summaries, runs last |
| Single-valued dimensions | Cardinality over displayed rows, suppressed only when the result is untruncated; unreported is not single-valued |
| Estimated versus billed cost | Separate contract and presentation; never combined; no mixed-currency totals |
| Relative window preset | Discriminated preset-or-custom; preset name in the address, resolved to boundaries at query-build time on every refresh |
| Behaviour at the row bound | Roll-ups aggregate server-side by entity and model, never from displayed rows; row bound stays a display bound |

**No unresolved NEEDS CLARIFICATION items remain.**
