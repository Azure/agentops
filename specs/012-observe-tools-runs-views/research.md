# Phase 0 Research: Observe tools and runs views

**Feature**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md) | **Date**: 2026-08-24

This document resolves every `NEEDS CLARIFICATION` raised in the plan's Technical Context, plus the two items the `/speckit-clarify` completion report deferred to planning. Each entry records the decision, why it was chosen, and what was rejected.

All findings are grounded in the code that exists today: `src/agentops/core/observe.py` (contracts), `src/agentops/agent/observe/queries.py` (KQL builders and bounds), `src/agentops/agent/observe/service.py` (normalization, dispatch, cache), `src/agentops/agent/observe/discovery.py` (control-plane inventory), and `src/agentops/agent/observe/ui.py` (rendering and URL state).

---

## R1. Which telemetry signals distinguish the five runtime kinds

**Context**: FR-016 through FR-018A require agents to be attributed to one of `foundry_hosted`, `foundry_prompt`, `external_registered`, `external_unregistered`, `copilot_studio`, or `unknown`. Today `agent_source_kind()` in `service.py` inspects only whether `gen_ai.agent.id` is present (→ `foundry`) or only `gen_ai.agent.name` (→ `external`). That single signal cannot produce five values.

**Decision**: Classify from **two inputs joined at normalization time**, not from telemetry alone.

1. **Telemetry attributes** already available on each row:
   - `gen_ai.agent.id` — present when the agent has a Foundry-managed identity.
   - `gen_ai.agent.name` — present for agents that report a name but no managed identity.
   - `gen_ai.project.id` / `gen_ai.foundry.resource.id` — locate the agent inside a Foundry project (already extracted by `_dimension_filters` and `_agent_extend_clauses`).
   - `gen_ai.provider.name` (emitted by `utils/telemetry.py`) and the legacy `gen_ai.system` (already read in `cockpit.py`) — identify the runtime provider, and are the only signal that can positively identify Copilot Studio.
2. **Control-plane inventory** from `agent/observe/discovery.py`, which already enumerates the resources in scope. Whether an agent is *registered* is a control-plane fact and is not discoverable from telemetry at all.

Resolution order, first match wins:

| Condition | Result |
| --- | --- |
| Provider identifies Copilot Studio | `copilot_studio` |
| `gen_ai.agent.id` present **and** inventory reports the agent as a hosted/container agent | `foundry_hosted` |
| `gen_ai.agent.id` present **and** inventory reports the agent as a prompt agent | `foundry_prompt` |
| `gen_ai.agent.id` present, inventory has no matching entry | `unknown` |
| Only `gen_ai.agent.name`, and the name matches a discovered project agent | `external_registered` |
| Only `gen_ai.agent.name`, no inventory match | `external_unregistered` |
| Neither identifier present, or the signals conflict | `unknown` |

**Rationale**: FR-017 explicitly permits `unknown` and forbids guessing, so a classifier that returns `unknown` when the joined evidence is insufficient is correct rather than incomplete. Splitting hosted from prompt agents is genuinely a control-plane distinction — two agents can emit byte-identical telemetry and differ only in how they are defined in the project — so any telemetry-only heuristic would be inventing an answer. Reusing the existing discovery inventory adds no new Azure call, because Observe already discovers sources for every request in order to build the source batch.

**Alternatives considered**:
- *Infer hosted-versus-prompt from span shape* (for example, treating agents that also emit model spans as hosted). Rejected: the correlation is incidental, not causal — a prompt agent invoking a model produces the same spans — so it would produce confidently wrong labels, which FR-017 forbids.
- *Require the runtime to emit a new dedicated attribute.* Rejected: the feature is explicitly out of scope for changing customer telemetry configuration, and it would leave every existing deployment permanently `unknown`.
- *Keep the coarse three-value set and add a separate detail field.* Rejected in [plan.md's Complexity Tracking](./plan.md#complexity-tracking) — it doubles the field surface and leaves the misleading value as the one most consumers read.

---

## R2. Reporting "showing N of M" when the row bound truncates results

**Context**: FR-028A requires each view to report how many rows are shown versus how many exist in scope. Existing builders end with `| top 500 by invocations desc`, which discards the discarded-row count — the total is unrecoverable client-side.

**Decision**: Restructure both new builders to compute the aggregate once, capture its size, then bound it, using KQL `let` bindings and `toscalar`:

```kusto
let agg = <scoped, time-windowed, filtered rows>
    | summarize <aggregations> by <grouping keys>;
let total_in_scope = toscalar(agg | count);
agg
| sort by <ordering key> desc
| take 500
| extend total_in_scope = total_in_scope
```

Every returned row carries `total_in_scope`; the normalization layer reads it once and records it on the view result so the UI can render "showing 500 of 1,842". When `total_in_scope` is absent from a row — an older or partial source — the count is reported as unknown rather than as equal to the row count.

**Rationale**: The counting pass runs over the already-summarized set, which is bounded by the number of distinct tools or runs rather than by raw span volume, so the cost is small and predictable. It keeps the whole answer inside one query per source, preserving the existing 10-source batch bound, the 30-second per-source timeout, and the 10-second request deadline without change.

**Alternatives considered**:
- *Issue a second counting query per source.* Rejected: it doubles the batch size against a hard 10-source bound, and the two queries could observe different data if they land either side of an ingestion boundary, producing a total smaller than the row count.
- *Request `MAX_ROWS_PER_QUERY + 1` rows and report "more than 500".* Rejected: FR-028A asks for the total, and "more than 500" cannot be compared across refreshes or used to judge whether a filter is narrow enough.
- *Raise or remove the row bound.* Rejected: the bound is an inherited safety property from spec 011 (FR-044) and removing it reintroduces unbounded responses.

---

## R3. How a run correlation key is chosen

**Context**: FR-009/FR-009A define a run as the most specific correlation available, and require each row to state which correlation formed it. The telemetry offers more than one candidate.

**Decision**: Resolve the run key in strict precedence order and record the winner alongside it.

| Precedence | Source | Recorded kind |
| --- | --- | --- |
| 1 | Session or conversation identity carried on the span (`gen_ai.conversation.id`, falling back to the Foundry thread identifier when that is what the runtime emits) | `conversation` |
| 2 | The correlated trace identifier already present on every Application Insights row (`OperationId`) | `trace` |

Every `ObservedRun` therefore carries both `run_key` and `run_key_kind`. Rows in one result set may legitimately have different kinds — that is the mixed granularity the spec accepted in specify-phase Q2 — and the UI labels each row so a two-turn conversation is never silently compared against a single trace.

**Rationale**: Conversation identity is the only correlation that spans multiple turns, so preferring it satisfies "most specific correlation available". `OperationId` is guaranteed present on every row Observe already reads, which makes the fallback total — there is no case where a run cannot be formed. Recording the kind is what keeps the mixed result set honest rather than misleading, and it is exactly what FR-009A asks for.

**Alternatives considered**:
- *Always use `OperationId`.* Rejected: it fragments a genuine multi-turn conversation into several unrelated single-turn runs, defeating the view's purpose.
- *Only emit runs when conversation identity is present.* Rejected: it silently hides all activity from runtimes that do not emit conversation identity, which contradicts the spec's requirement that absent signals be explained rather than dropped.
- *Synthesize a conversation key by clustering traces by agent and time proximity.* Rejected: it invents correlation that the telemetry does not support, and two concurrent users of the same agent would be merged into one fictitious run.

---

## R4. How tool invocations are identified in telemetry

**Context**: FR-001 and FR-005 require aggregating tool activity by name with invocation counts, failures, latency, and last activity — and deliberately without token fields.

**Decision**: A row counts as a tool invocation when a tool name attribute (`gen_ai.tool.name`) is present on the span. `gen_ai.operation.name == "execute_tool"` is treated as corroborating evidence and is used to sharpen the query where available, but it is **not** required, because not every runtime emits it. Both new builders read from the same `union AppDependencies, AppRequests` table expression the existing builders use, apply the same scope, time-window, and dimension filters, and derive failure and latency from the same fields as `build_agents_query`.

Tool **content** — `gen_ai.tool.message`, already mapped to the protected `tool_content` kind in `queries.py` — is never read by these builders. Only the tool name and derived counters cross the boundary.

Token fields are omitted from the tools contract entirely rather than being modelled as always-null, so that no consumer can mistake an absent field for a measured zero. Attributing tokens to an individual tool invocation is listed as out of scope in issue #441 and is not attempted.

**Rationale**: Presence of the tool name is the minimal, provider-neutral signal, and it is the same shape of coalescing check the existing agent and model builders already use. Excluding the field rather than nulling it enforces the absent-versus-zero fidelity Principle IV depends on at the type level rather than by convention.

**Alternatives considered**:
- *Require `gen_ai.operation.name == "execute_tool"`.* Rejected: runtimes that emit a tool name without the operation name would show zero tool activity, which reads as "this agent uses no tools" — a confidently wrong answer.
- *Infer tool calls from dependency type or target.* Rejected: it conflates genuine tool invocations with ordinary outbound HTTP calls and would inflate every count.
- *Include null token fields for symmetry with the models view.* Rejected: it invites exactly the absent-versus-zero confusion FR-005 and FR-025 exist to prevent.

---

## R5. Validating the new `tool_name` and `run_key` filters

**Context**: FR-014 requires the new filters to respect the authorization boundary, but `ObserveFilterState.validate_scope()` works by canonicalizing ARM resource IDs — and neither new filter is a resource ID.

**Decision**: Treat both as **narrowing-only** filters. They are applied inside a query that is already bounded to the caller's authorized sources, so they can only ever reduce a result set the caller is already entitled to see; they can never widen scope or reach another tenant's data. Consequently `validate_scope()` is unchanged for them. What they do get:

- Whitespace trimming and rejection of empty-after-trim values, matching the existing field-validator style in `ObserveFilterState`.
- A maximum length bound so a pathological value cannot inflate the generated query.
- Escaping through the existing `_kql_escape` helper at query-construction time, exactly as `agent_id` and `model` are handled today.

Both keys are added to the `OBSERVE_FILTER_QUERY_KEYS` allow-list so filtered views produce shareable links, consistent with `agent_id` and `project_resource_id` already round-tripping. The allow-list's existing prohibition is unchanged and unambiguous: it excludes generative-AI **content** — messages, system instructions, tool content, evaluation explanations — and both new values are identifiers or metadata, not content.

**Rationale**: The scope guarantee is structural, coming from the source batch rather than from the filter, so revalidating a non-ARM value against an ARM boundary would be theatre. Reusing the existing trim/escape path keeps one injection-safety story instead of two.

**Alternatives considered**:
- *Validate `tool_name` against a list of known tools.* Rejected: it requires a control-plane tool registry that does not exist, and an unknown tool name should legitimately return an empty result rather than an error.
- *Keep both filters out of the URL.* Rejected: it breaks the shareable-link behaviour the existing views already provide, for values that carry no content.

---

## R6. Deprecation and migration window for the refined runtime values *(deferred from `/speckit-clarify`)*

**Decision**: Ship the replacement in a **single release with no dual-emission window**, accompanied by a breaking-change entry in `CHANGELOG.md` and an updated runtime-attribution section in `docs/observe.md` carrying the old → new mapping table from [plan.md](./plan.md#complexity-tracking).

**Rationale**: The value is computed per request and never persisted — it does not appear in `agentops.yaml`, `results.json`, evidence packs, Doctor history, or any on-disk artifact — so there is no stored data to migrate and no reader that can be left behind mid-upgrade. The only two consumers are Cockpit's own UI, which changes in the same commit, and the documented Observe API response, whose consumers need the mapping table rather than a grace period. A dual-emission window is additionally *incoherent* here: because the mapping is not one-to-one, a transitional response would have to state both `foundry` and `foundry_prompt` for the same agent, which is less truthful than the clean replacement.

**Alternatives considered**:
- *Emit both old and new fields for one release.* Rejected for the incoherence above, and because it would leave the contract with a permanently ambiguous field if the removal slipped.
- *Gate the refinement behind an opt-in flag.* Rejected: it creates two divergent truths about the same agent and doubles the test matrix for a read-only display value.

---

## R7. Run completeness versus the inherited refresh and cache behaviour *(deferred from `/speckit-clarify`)*

**Context**: FR-012 requires each run to indicate whether it is complete or still in flight. Observe already reuses results for 120 seconds (`CACHE_TTL_SECONDS`) and the UI auto-refreshes every 5 minutes (`AUTO_REFRESH_MS`). A run could otherwise be labelled "complete" purely because the reading was taken before its next turn arrived.

**Decision**: Derive completeness per run at query time using a **settling margin equal to the cache TTL (120 seconds)**. A run whose most recent activity falls within the settling margin of the query's window end is reported as in progress; a run whose last activity is older than the margin is reported as complete. The 2-minute cache reuse and the 5-minute auto-refresh are left exactly as they are.

**Rationale**: Tying the margin to the cache TTL makes the two behaviours consistent by construction — a cached result can never be young enough to contain a run that was marked complete inside the settling window, so the cache can never turn a truthful "in progress" into a false "complete". Each row already carries `refreshed_at`, so the age of the reading stays visible and an in-flight run simply re-resolves on the next refresh. Changing the refresh or cache constants was rejected as an unnecessary regression of spec 011's tuned behaviour for a display concern that a derived flag solves.

**Alternatives considered**:
- *Shorten the cache TTL or the auto-refresh interval for the runs view.* Rejected: it increases query load against the same bounded batch to fix a labelling problem, and leaves the race present, merely narrower.
- *Mark every run touching the window edge as in progress.* Rejected: with a 24-hour default window, nearly every recent run would be permanently "in progress", making the flag meaningless.
- *Omit the completeness flag.* Rejected: FR-012 requires it, and a duration reported without it would be silently wrong for any run still executing.

**Scope of the flag (trailing edge only)**: the settling margin resolves the *end* of the window. It says nothing about a run that started *before* the window began. Detecting that would require scanning outside the requested range, roughly doubling the data volume of every runs query for a cosmetic flag. That was rejected. Instead, `started_at`, `duration_ms`, `turns`, `failed_turns` and `status` are defined as **window-scoped** (FR-012A): they describe activity inside the selected range, the views must say so, and the leading edge is an accepted, documented limitation rather than a defect.

---

## Resolved status

| Unknown | Resolved by |
| --- | --- |
| Five-value runtime classification signals | R1 |
| Reporting shown-versus-total under the row bound | R2 |
| Run correlation key selection and labelling | R3 |
| Tool invocation identification and token omission | R4 |
| Authorization handling for non-ARM filters | R5 |
| Breaking-change deprecation window | R6 |
| Run completeness versus cache and auto-refresh | R7 |

No `NEEDS CLARIFICATION` markers remain. Phase 1 may proceed.
