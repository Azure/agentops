# Phase 0 Research: Granular Token Classes in the Models View

**Feature**: `012-granular-token-dimensions` | **Date**: 2026-08-24 | **Plan**: [plan.md](./plan.md)

This document resolves every open unknown required before design. Each decision records what
was chosen, why, and what was rejected. Attribute names are cited to primary sources (registry
YAML or SDK source), never inferred.

> **Vocabulary note**: this feature uses the word **class** (cache-read, cache-write,
> reasoning), never "dimension", in all prose. No monetary, cost, rate, spend, or billing
> language appears anywhere.

---

## D1 — Which attributes are eligible for admission

**Decision**: An attribute is eligible only when it lives in the **`gen_ai.usage.*` group of the
`Properties` bag** — the same attribute group that already carries the totals the models view
renders today — **and** its value parses to a non-negative number.

**Rationale**: This binds clarification Q1 (answer B) to a literal, checkable namespace. The
existing query already reads `Properties["gen_ai.usage.input_tokens"]` and
`Properties["gen_ai.usage.output_tokens"]` (`queries.py:135-136`), so "the group already
carrying input/output token counts" resolves unambiguously to `gen_ai.usage.*`. The rule is
mechanical: prefix test plus numeric-and-non-negative test. No name-pattern heuristics, no
guessing at semantics, no risk of admitting a latency or temperature attribute because its name
happens to contain a token-ish word.

**Alternatives considered**:

- *Broader name-pattern matching across all of `Properties`* (clarification Q1 option C) —
  rejected during `/speckit-clarify`. It would admit unrelated attributes and make eligibility
  depend on English word shapes rather than on a declared namespace.
- *Admitting the OpenInference `llm.token_count.*` namespace* — rejected. It is a genuinely
  different attribute group and is not the group carrying this view's existing totals, so
  admitting it would contradict Q1=B. Recorded in D3 as a known future extension point, not as
  a gap in this feature.

---

## D2 — Source attribute names accepted for each normalized class

**Decision**: Each normalized class accepts a **fixed, explicitly ordered tuple of accepted source
attribute names, restricted to `gen_ai.usage.*`**. When a record carries more than one accepted name
for the same class, the **first name present in declared tuple order supplies the value** and the
remaining accepted names for that class are consumed and discarded — accepted names for one class are
never summed together. Every name consumed by a class is also removed from passthrough consideration,
so it can never be counted twice.

| Normalized class | Accepted source attribute name | Origin | Stability / reality check |
|---|---|---|---|
| **cache-read** | `gen_ai.usage.cache_read.input_tokens` | OTel `semantic-conventions-genai` registry — canonical | `development`; also the value exported by the OTel **Python** incubating package, so this is what OpenLLMetry emits today |
| **cache-read** | `gen_ai.usage.cache_read_input_tokens` | Traceloop legacy `LLM_USAGE_CACHE_READ_INPUT_TOKENS` | legacy alias (underscore instead of dot) |
| **cache-write** | `gen_ai.usage.cache_write.input_tokens` | OTel `semantic-conventions-genai` registry — canonical | `development`; **defined but not yet emitted** by any widely deployed instrumentation |
| **cache-write** | `gen_ai.usage.cache_creation.input_tokens` | OTel Python incubating package (not yet renamed) | **the form actually seen in production today** — emitted by OpenLLMetry's Anthropic, LangChain, LlamaIndex and Bedrock instrumentors |
| **cache-write** | `gen_ai.usage.cache_creation_input_tokens` | Traceloop legacy `LLM_USAGE_CACHE_CREATION_INPUT_TOKENS` | legacy alias (all-underscore) |
| **reasoning** | `gen_ai.usage.reasoning.output_tokens` | OTel `semantic-conventions-genai` registry — canonical | `development`; recently added, **not yet emitted** by major instrumentation |
| **reasoning** | `gen_ai.usage.reasoning_tokens` | Traceloop `GEN_AI_USAGE_REASONING_TOKENS` | **the form actually seen today** — emitted by OpenLLMetry's OpenAI and OpenAI-Agents instrumentors |

**Rationale**: The canonical OTel names and the names actually emitted in the wild have
diverged, because the `gen_ai.*` attributes were **moved out of
`open-telemetry/semantic-conventions` into the new `open-telemetry/semantic-conventions-genai`
repository** and renamed there, while the downstream Python `opentelemetry-semantic-conventions`
incubating package has not yet followed. Accepting only the canonical names would produce an
empty view for every workload instrumented today; accepting only the emitted names would rot as
the ecosystem catches up. Accepting both, per class, is the only option that reads correctly
now **and** after the rename propagates.

First-present-wins rather than summing matters precisely **because** a single record can carry both a
canonical and a legacy alias during a migration window. In that window an instrumentation library
emits the *same* count under both spellings — for example `gen_ai.usage.cache_creation.input_tokens`
and `gen_ai.usage.cache_creation_input_tokens` both set to `100`. Summing would report `200`, which is
exactly the double count FR-009 and SC-008 forbid. Determinism is not at risk: the accepted names are
declared as an explicit ordered tuple (canonical spelling first, legacy spellings after), so
resolution never depends on dict iteration order. Removing consumed attributes from the passthrough
candidate pool is what additionally prevents the same number reappearing as an unnormalized entry in
the same row.

**Alternatives considered**:

- *Accept only the canonical `semantic-conventions-genai` names* — rejected: yields no data for
  real workloads today.
- *Accept only the currently-emitted names* — rejected: guarantees silent breakage when
  instrumentation adopts the canonical spelling.
- *Summing every accepted alias instead of first-present-wins* — rejected: during a migration window
  the same count is commonly emitted under two spellings, so summing double counts and breaks FR-009
  and SC-008. The ordered-tuple declaration removes the iteration-order concern that would otherwise
  be the only argument for summing.

**Primary sources**:

- <https://github.com/open-telemetry/semantic-conventions-genai/blob/56d6b11a02129319bf371083fa134b7ce989c976/model/gen-ai/registry.yaml>
- <https://github.com/open-telemetry/opentelemetry-js/blob/98c5bd77/semantic-conventions/CHANGELOG.md>
- <https://github.com/open-telemetry/opentelemetry-python-contrib/blob/466ae4c0/util/opentelemetry-util-genai/src/opentelemetry/util/genai/_agent_invocation.py>
- <https://github.com/traceloop/openllmetry/blob/62e24c2f/packages/opentelemetry-semantic-conventions-ai/opentelemetry/semconv_ai/__init__.py>
- <https://github.com/traceloop/openllmetry/blob/62e24c2f/packages/opentelemetry-semantic-conventions-ai/opentelemetry/semconv_ai/_testing.py>

---

## D3 — Evidence that two distinct vendor families share a class under different names

**Decision**: FR-020 / SC-006 are satisfied by **cache-write across the OpenAI and Anthropic
families**, and secondarily by **reasoning across the same two families**. Neither family is
hardcoded anywhere; they are selected **by the rule** stated in clarification Q4 (answer B) —
a class qualifies when two or more distinct source attribute names map to it.

| Class | OpenAI family | Anthropic family |
|---|---|---|
| **cache-write** | `usage.prompt_tokens_details.cache_write_tokens` | `usage.cache_creation_input_tokens` |
| **cache-read** | `usage.prompt_tokens_details.cached_tokens` | `usage.cache_read_input_tokens` |
| **reasoning** | `usage.completion_tokens_details.reasoning_tokens` | `usage.output_tokens_details.thinking_tokens` |

The same divergence reappears at the span-attribute layer *inside* the eligible namespace, which
is what the mapping table in D2 actually consumes:

| Class | OTel canonical spelling | Spelling emitted in the wild |
|---|---|---|
| cache-write | `gen_ai.usage.cache_write.input_tokens` | `gen_ai.usage.cache_creation.input_tokens` |
| reasoning | `gen_ai.usage.reasoning.output_tokens` | `gen_ai.usage.reasoning_tokens` |

**Rationale**: The requirement asks for proof that normalization is *necessary*, not for a
vendor allow-list. Anthropic calls reasoning output `thinking_tokens` while OpenAI calls it
`reasoning_tokens`; both describe the same class. That is the concrete justification for having
a normalization layer at all, and it is discoverable purely from the alias table — no vendor
name is ever branched on in code.

**Alternatives considered**:

- *Hardcode a vendor list (`if vendor == "anthropic"`)* — rejected by clarification Q4=B. It
  would break the moment a fourth ecosystem appears and would embed vendor identity in logic
  that only needs attribute names.

**Primary sources**:

- <https://github.com/openai/openai-python/blob/e43b4224/src/openai/types/completion_usage.py>
- <https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/src/anthropic/types/usage.py>
- <https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/src/anthropic/types/output_tokens_details.py>

---

## D4 — Reality check: what Foundry-native telemetry actually emits

**Decision**: Treat **`not_reported` and `partial` as the expected steady state** of the
`token_usage` coverage entry on the models view, not as defects, and make the UI copy say so.

**Rationale**: Azure's own instrumentation emits **only** `gen_ai.usage.input_tokens` and
`gen_ai.usage.output_tokens`. A search across `sdk/ai/` in `Azure/azure-sdk-for-python` for
`cache_read`, `cache_creation`, `cache_write`, and `reasoning_tokens` returns **zero results**;
the agents instrumentor imports only `GEN_AI_USAGE_INPUT_TOKENS` and
`GEN_AI_USAGE_OUTPUT_TOKENS`. Consequently a pure Foundry-hosted workload will show **all three
classes as "Not reported"**, and a mixed workload with an OpenLLMetry-instrumented Anthropic or
OpenAI caller will show a **subset**.

This is decisive for how the feature must be framed: an empty granular column is overwhelmingly
likely to mean "your instrumentation does not emit this", not "your model did zero of this
work". FR-007 (absence rendered distinctly from zero) and the `next_action` copy on the coverage
entry are therefore load-bearing, not cosmetic.

**Alternatives considered**:

- *Treat missing classes as an error or warning state* — rejected: it would flag virtually every
  Foundry deployment as broken.
- *Fall back to deriving a class from the totals* — rejected outright by FR-006; no class value
  is ever produced by subtraction.

**Primary sources**:

- <https://github.com/Azure/azure-sdk-for-python/blob/26ac9611/sdk/ai/azure-ai-agents/azure/ai/agents/telemetry/_ai_agents_instrumentor.py>
- <https://github.com/Azure/azure-sdk-for-python/blob/26ac9611/sdk/ai/azure-ai-inference/azure/ai/inference/tracing.py>

---

## D5 — How the eligible attributes are projected in KQL

**Decision**: Extend `build_models_query` only, in two parts:

1. **Normalized classes** — three `| extend` clauses placed **after** the existing models-only
   `deployment` extend, each `coalesce`-ing that class's accepted aliases **in the declared
   `TOKEN_CLASS_ALIASES` tuple order**, so the first name present on a record supplies the value and
   aliases are never added together (D2). Then three corresponding `sum(...)` terms in the existing
   `summarize`, aggregating the already-resolved per-span value across the group.
2. **Unnormalized passthrough** — discovered dynamically in the same query by expanding
   `bag_keys(Properties)`, keeping keys that start with `gen_ai.usage.` and are not already
   mapped, summing each key per aggregation group, packing the result into a bag, and joining it
   back onto the main aggregation on `project_resource_id, model, deployment`.

The query remains **one round trip per telemetry source**; the existing
`| top MAX_ROWS_PER_QUERY by requests desc` cap is untouched.

**Rationale**: `build_models_query` already establishes the models-only extension pattern — it
calls the shared `_agent_extend_clauses()` and then appends its own `deployment` extend
(`queries.py:186-187`). Adding the class extends at that same point keeps `build_agents_query`
and `build_usage_query` byte-for-byte unchanged, which is exactly what the spec's Out of Scope
section requires. Dynamic discovery via `bag_keys` is unavoidable for the passthrough: the whole
purpose of FR-004 is to preserve vendor classes that are *not* on any known list, so a static
projection cannot implement it. Summing per key before packing keeps the passthrough consistent
with how the normalized classes aggregate.

**Alternatives considered**:

- *Add the class extends to the shared `_agent_extend_clauses()`* — rejected. Existing query
  tests assert substrings only, so it would not fail the suite, but it would silently alter the
  agents and combined-usage query text that the spec freezes. Rejected on blast radius, not on
  test pressure.
- *A second query dedicated to the passthrough* — rejected: doubles Azure Monitor round trips
  for the view, against the stated performance goal.
- *`mv-apply` producing a per-row bag before the summarize* — viable, but merging bags across
  rows in the outer `summarize` is last-wins rather than additive, which would under-report.
  Rejected for correctness.

---

## D6 — Where the five-attribute cap and truncation signal are applied

**Decision**: The **eligibility filter runs in KQL**; the **cap and the truncation signal are
applied in Python**, inside `normalize_model_row`. Candidates are sorted **ascending by source
attribute name**, the first five are retained, and a boolean truncation flag is set when more
than five eligible candidates were present.

**Rationale**: Clarification Q2 (answer B) fixes the cap at five per record with deterministic
selection by source attribute name and an explicit truncation signal. Sorting by name is total
and stable, so the same telemetry always yields the same five attributes and the same flag —
which is precisely what makes SC-009 testable. Applying the cap in Python means the whole rule
is exercised by plain unit tests over row mappings, with no Azure dependency, satisfying
Constitution III and V. The natural cardinality of `gen_ai.usage.*` keys per record is small, so
returning the unbounded set from KQL before capping carries no meaningful payload risk.

**Alternatives considered**:

- *Cap inside KQL* — rejected: once the query truncates, the information needed to set the
  truncation flag is gone, and reconstructing it (fetch six, flag if six) is a hack that also
  moves a testable rule into a string that unit tests cannot execute.
- *Order by value descending ("keep the biggest")* — rejected: value-ordered selection makes the
  retained set change from window to window even when instrumentation is stable, so the view
  would appear to flicker.

---

## D7 — Tri-state token-class coverage, and the `partial` state collision

**Decision**:

1. Add a sibling helper that classifies the **token-class inventory** of a set of model rows as
   `not_reported` (no class present on any row), `partial` (at least one class present, at least
   one absent), or `reported` (all three present).
2. Widen `classify_query_coverage`'s existing `reported: bool = True` parameter to accept that
   tri-state value in addition to a bool, and add one new arm on the success path that maps
   `partial` to `CoverageState.partial` with **token-class-specific** `reason` and `next_action`
   text.
3. **Leave `token_reporting_state` untouched** so the agents view keeps its current two-state
   behavior and its existing test.

Evaluation precedence inside `classify_query_coverage` becomes:

| Order | Condition | Resulting state |
|---|---|---|
| 1 | `status != "success"` | existing failure arm — `partial` (source-level) or `error`, unchanged |
| 2 | `row_count == 0` | `no_data` |
| 3 | inventory is `not_reported` | `not_reported` |
| 4 | inventory is `partial` | `partial` — **new arm**, token-class reason/next_action |
| 5 | otherwise | `available` |

**Rationale**: `CoverageState` already contains `"partial"`, so no public `Literal` is widened —
the change stays additive and Constitution I stays green. The state is now genuinely overloaded
(a degraded query versus a partially-instrumented workload), and that is an accepted, documented
trade-off: the two cases are disambiguated by their `reason` and `next_action` text, which is
what the coverage panel actually renders. Query outcome is checked **first** because a degraded
or failed query means the class inventory itself is untrustworthy — reporting "partially
instrumented" from an incomplete result set would be a false statement about the workload.

Widening the existing parameter rather than adding a new one is deliberate reuse per the
constitution's "reuse existing helpers before introducing new abstractions" constraint; every
current caller passes a bool and is unaffected. The one genuinely new helper is justified by the
spec's Out of Scope clause: changing `token_reporting_state` in place would alter the agents
view, which this feature must not touch.

**Alternatives considered**:

- *Add a new `CoverageState` member such as `partially_reported`* — rejected: widening a public
  `Literal` is a breaking contract change under Constitution I, for a distinction that
  `reason`/`next_action` already carries.
- *Make `token_reporting_state` tri-state in place* — rejected: it would change agents-view
  behavior and break `test_token_reporting_state_distinguishes_absence_from_zero`, for no gain.
- *Add a second boolean parameter alongside `reported`* — rejected: permits contradictory
  combinations that have no meaning.

---

## D8 — Adding a `token_usage` coverage entry to the models view

**Decision**: Append a `token_usage` `CoverageResult` to the models branch of the view builder,
produced by the same `classify_query_coverage` call path the agents branch already uses, with
the tri-state inventory from D7 supplied as `reported`.

**Rationale**: The models branch currently emits only a `model_attribution` coverage entry
(`service.py:660-695`); `token_usage` classification exists solely in the agents branch
(`service.py:639-657`). FR-011/FR-012 therefore require **adding** an entry, not extending one.
`CoverageResult.dimension` already permits `"token_usage"`, so this is a new call site rather
than a contract change. Reusing the existing classifier keeps the models-view coverage text
consistent with the agents view for the states they share.

**Alternatives considered**:

- *Emit a bespoke coverage record for the models view* — rejected: duplicates
  `safe_failure_reason` handling and would let the two views drift apart in wording.

---

## D9 — Per-row partial indication in both renderers

**Decision**: Render per-class values through the existing `_render_maybe_missing` helper, and
attach the FR-022 per-row partial indicator in **both** `render_usage_table` (server-side
Python) and the `renderUsage` JavaScript mirror. The `(observed usage, not billing data)` label
emitted by `_render_token_totals` is preserved verbatim and unmoved.

**Rationale**: Clarification Q3 (answer B) requires the partial signal per row **and** in the
coverage panel; the coverage half is D8, this is the row half. The models table is implemented
twice — once server-rendered and once client-rendered — so any change applied to only one
produces a view that changes appearance on refresh. `_render_maybe_missing` already renders
absence as "Not reported", which is exactly the FR-016 semantics needed for the new cells, so no
new formatting helper is warranted.

**Alternatives considered**:

- *Show the partial signal only in the coverage panel* — rejected by Q3=B: it forces the
  operator to correlate a panel entry against a row by hand.
- *Introduce a new per-class cell formatter* — rejected: `_render_maybe_missing` already
  provides the required behavior, and the constitution requires reusing it.

---

## Residual risks

| Risk | Impact | Mitigation |
|---|---|---|
| The OTel Python incubating package completes the `cache_creation` → `cache_write` rename, changing what is emitted in the wild | Medium | Both spellings are already accepted per class in one ordered tuple (D2), and the first one present wins, so the transition is a no-op for this feature |
| `bag_keys`-based passthrough discovery proves costly on very large workspaces | Low | The passthrough branch filters to the `gen_ai.usage.` prefix before aggregating, and the row cap is unchanged; measurable during implementation |
| The overloaded `partial` state confuses operators | Low | Disambiguated by distinct `reason` and `next_action` text (D7); assert both texts in tests |
| OpenInference-instrumented workloads report nothing granular | Accepted | `llm.token_count.*` is outside the eligible group by D1/Q1=B; recorded as a future extension, not a defect |

## Unresolved

None. No `NEEDS CLARIFICATION` marker remains in `plan.md`'s Technical Context.
