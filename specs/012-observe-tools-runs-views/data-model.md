# Phase 1 Data Model: Observe tools and runs views

**Feature**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md) | **Research**: [research.md](./research.md) | **Date**: 2026-08-24

All contract entities live in `src/agentops/core/observe.py` and extend `ContractModel`, which sets `ConfigDict(extra="forbid")`. The module stays pure — no Azure SDK import, no network call, no filesystem write — so every model below is importable and testable with no Azure package installed (Constitution Principle II and III).

Two conventions from the existing module are preserved throughout:

- **Absent is not zero.** Any measurement that may be missing is typed `X | None` with a `None` default. A field that can never be measured for an entity is **omitted from that entity entirely** rather than declared as permanently `None`.
- **Counters are bounded by their parent.** Failure counts carry an `after` model validator asserting they cannot exceed the count they are a subset of, mirroring `ObservedAgent._failures_not_greater_than_invocations`.

---

## 1. New shared type: `RuntimeKind`

```python
RuntimeKind = Literal[
    "foundry_hosted",
    "foundry_prompt",
    "external_registered",
    "external_unregistered",
    "copilot_studio",
    "unknown",
]
```

A single named alias is introduced because three entities now share this set (`ObservedAgent`, `ObservedTool`, `ObservedRun`). Defining it once prevents the three from drifting apart.

**Derivation**: joined from telemetry attributes and the control-plane inventory per [research R1](./research.md#r1-which-telemetry-signals-distinguish-the-five-runtime-kinds). `unknown` is a first-class, expected outcome (FR-017), not an error state.

**Breaking change**: this replaces `ObservedAgent.source_kind`'s current `Literal["foundry", "external", "unknown"]`. The mapping is not one-to-one; see [plan.md Complexity Tracking](./plan.md#complexity-tracking) and [research R6](./research.md#r6-deprecation-and-migration-window-for-the-refined-runtime-values-deferred-from-speckit-clarify).

---

## 2. New entity: `ObservedTool`

One row per (source, project, agent, tool) grouping within the query window. Satisfies FR-001 through FR-005.

| Field | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `source_id` | `str` | `min_length=1` | Originating telemetry source (FR-002, FR-023). Supplied by the normalizer from `TelemetrySource.source_id`, not read from the query result. |
| `tool_name` | `str` | `min_length=1` | The tool identifier from telemetry. Never a message body. |
| `agent_key` | `str` | `min_length=1` | Same coalesced key the agents view uses, so rows join cleanly. |
| `agent_id` | `str \| None` | default `None` | Present only for agents with a managed identity. |
| `agent_name` | `str \| None` | default `None` | Present when the runtime reports a name. |
| `project_resource_id` | `str \| None` | default `None` | Attribution target (FR-022). |
| `foundry_resource_id` | `str \| None` | default `None` | Attribution target (FR-022). |
| `source_kind` | `RuntimeKind` | required | Runtime attribution (FR-016). |
| `last_seen` | `datetime` | required | Most recent invocation in window (FR-003). |
| `invocations` | `int` | `ge=0` | Tool-meter allocation key for future cost work. |
| `failures` | `int` | `ge=0` | Subset of `invocations`. |
| `p95_latency_ms` | `float \| None` | `ge=0`, default `None` | `None` when latency is not measurable, never `0.0` as a stand-in. |

**Validation rules**

- `failures <= invocations` — model validator, mirroring `ObservedAgent`.

**Source attribution**

`source_id` is **required and non-nullable** on both `ObservedTool` and `ObservedRun`, unlike the nullable `project_resource_id` / `foundry_resource_id` attribution fields. It is always knowable: each telemetry source is queried with its own request, so the normalizer already receives the `TelemetrySource` (the existing `normalize_agent_row(row, source=source)` signature) and reads `source.source_id` directly. Nothing is added to the KQL and no grouping key changes — rows are already produced per source, since a `summarize` cannot span two separate queries.

The field exists because rows from different sources are **concatenated, never merged** in `agent/observe/service.py`. Without it, two sources reporting the same agent and tool yield two rows that are indistinguishable to a consumer, which is exactly what FR-023 prohibits. The pre-existing `ObservedAgent` carries the same defect today, so it gains the same field in this feature (§9, FR-023A) rather than being left inconsistent with the two new views. That change is additive, not breaking.

**Deliberate omissions**

`input_tokens` and `output_tokens` are **absent from this model**, unlike `ObservedAgent` and `ModelUsage`. Attributing token consumption to an individual tool invocation is out of scope for this feature (issue #441), and declaring the fields as always-`None` would invite the absent-versus-zero confusion FR-005 and FR-025 exist to prevent. Enforced structurally by `extra="forbid"`: a serializer that tries to add a token field fails loudly. See [research R4](./research.md#r4-how-tool-invocations-are-identified-in-telemetry).

---

## 3. New entity: `ObservedRun`

One row per correlated agent execution within the query window. Satisfies FR-006 through FR-012A.

| Field | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `source_id` | `str` | `min_length=1` | Originating telemetry source (FR-023). Same provenance as `ObservedTool.source_id`. |
| `run_key` | `str` | `min_length=1` | The correlation identifier (FR-009). |
| `run_key_kind` | `Literal["conversation", "trace"]` | required | Which correlation formed this row (FR-009A). |
| `agent_key` | `str` | `min_length=1` | Joins to the agents and tools views. |
| `agent_id` | `str \| None` | default `None` | |
| `agent_name` | `str \| None` | default `None` | |
| `project_resource_id` | `str \| None` | default `None` | |
| `foundry_resource_id` | `str \| None` | default `None` | |
| `source_kind` | `RuntimeKind` | required | |
| `started_at` | `datetime` | required | First activity observed **inside the query window**, not the run's absolute start (FR-012A). |
| `last_activity_at` | `datetime` | required | Most recent activity in the run. |
| `duration_ms` | `float \| None` | `ge=0`, default `None` | Spans `started_at` to `last_activity_at`, so it is window-scoped like `started_at` (FR-012A). Compute allocation key for future cost work. |
| `status` | `Literal["succeeded", "failed", "in_progress"]` | required | Outcome plus trailing-edge completeness in one field (FR-008, FR-012). |
| `turns` | `int` | `ge=1` | One request plus its response (FR-007A). |
| `failed_turns` | `int` | `ge=0` | Subset of `turns`. |
| `tool_invocations` | `int` | `ge=0` | Tool calls made during the run (FR-010). |
| `tool_failures` | `int` | `ge=0` | Subset of `tool_invocations`. |
| `input_tokens` | `int \| None` | `ge=0`, default `None` | Total observed input tokens across the run (FR-007). |
| `output_tokens` | `int \| None` | `ge=0`, default `None` | Total observed output tokens across the run (FR-007). |

**Token reporting**

Unlike `ObservedTool` (§2), runs **do** carry token totals, because a run aggregates model activity that already reports usage — the same signal `ObservedAgent` and `ModelUsage` read today. The fields are nullable and default to `None`: when no activity inside the run reported usage, the totals are `None`, never `0` (FR-011, FR-025). Consumers MUST render `None` as "not available" and MUST NOT coerce it to zero. The existing `token_reporting_state()` helper in `agent/observe/service.py` is the source of truth for whether a source reports usage at all.

**Validation rules**

- `last_activity_at >= started_at`.
- `failed_turns <= turns`.
- `tool_failures <= tool_invocations`.
- If `status == "succeeded"` then `failed_turns == 0` **and** `tool_failures == 0` — encodes FR-008A ("any failed turn or tool invocation fails the run; later recovery does not clear it") in the type system, so no normalization path can emit a run that claims success while carrying failures.

**State semantics**

`status` carries both outcome and trailing-edge completeness rather than pairing a boolean `complete` flag with a separate outcome, because the three values are mutually exclusive and a `complete=False, status="succeeded"` combination would be meaningless. `in_progress` is derived per [research R7](./research.md#r7-run-completeness-versus-the-inherited-refresh-and-cache-behaviour-deferred-from-speckit-clarify): a run whose `last_activity_at` falls within the 120-second settling margin of the query window end is in progress; otherwise it has settled and reports `succeeded` or `failed`.

**Window-scoped values (leading edge deliberately unflagged)**

`status` covers the **trailing** boundary only. A run that began before the query window is **not** flagged. There is no `truncated_start` field and no fourth `status` value, because detecting the leading edge would require reading telemetry outside the selected range to recover each run's true first activity, materially increasing the data every runs query scans for a case that is uncommon at the default 24-hour lookback and typical run duration.

The accepted consequence, per FR-012A and the matching spec assumption: for such a run, `started_at`, `duration_ms`, `turns`, `failed_turns`, and `status` describe only the activity inside the window. A failure that occurred before the window is not visible, so the row may report `succeeded`. Consumers MUST present these as window-scoped values rather than as the run's absolute lifetime. This is a stated limitation, not an absent-versus-zero violation: no value is fabricated, and nothing missing is rendered as zero.

**Granularity note**

Rows in one result set may legitimately mix `run_key_kind` values. A `conversation` run may span many turns while a `trace` run is exactly one turn (FR-007A). `run_key_kind` is what keeps that comparison honest, and the UI must surface it on every row rather than treating all rows as equivalent.

---

## 4. New entity: `ResultBounds`

Reports how much of the in-scope data a bounded view actually returned. Satisfies FR-028A.

| Field | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `rows_shown` | `int` | `ge=0` | Rows present in `data`. |
| `rows_total_in_scope` | `int \| None` | `ge=0`, default `None` | `None` means the total could not be determined, not that it equals `rows_shown`. |
| `truncated` | `bool` | default `False` | True when the row bound discarded rows. |

**Validation rules**

- If `rows_total_in_scope` is not `None`, then `rows_total_in_scope >= rows_shown`.
- If `truncated` is `True`, `rows_shown` equals the configured `MAX_ROWS_PER_QUERY` bound.

**Population**: `rows_total_in_scope` is read from the `total_in_scope` column each row carries, produced by the `let` / `toscalar` query shape in [research R2](./research.md#r2-reporting-showing-n-of-m-when-the-row-bound-truncates-results). A source that does not return the column yields `None` rather than a fabricated total.

---

## 5. Extended: `ObserveFilterState`

Two narrowing-only filters are added to the existing model (FR-013, FR-014).

| New field | Type | Constraints |
| --- | --- | --- |
| `tool_name` | `str \| None` | default `None`; trimmed; rejected if empty after trim; maximum length bound |
| `run_key` | `str \| None` | default `None`; trimmed; rejected if empty after trim; maximum length bound |

**Unchanged**: `validate_scope()` continues to check only `foundry_resource_id` and `project_resource_id`. Neither new filter is an ARM resource ID, and neither can widen scope — both are applied inside a query already bounded to the caller's authorized sources, so they can only reduce a result set the caller is already entitled to see. This is what FR-014A requires: resource-identifying dimensions are authorization-checked (FR-014), while these two are validated for well-formedness only. Rationale and the rejected alternatives are in [research R5](./research.md#r5-validating-the-new-tool_name-and-run_key-filters).

**Injection safety**: both values pass through the existing `_kql_escape` helper at query-construction time, exactly as `agent_id` and `model` do today. No new escaping path is introduced.

**Backward compatibility**: purely additive with `None` defaults, so every existing request payload remains valid.

---

## 6. Extended: view enums

Two enums must grow together, and a test already asserts they agree.

| Location | Change |
| --- | --- |
| `core/observe.py` → `ObserveQueryRequest.view` | `Literal["overview", "agents", "models", "coverage"]` → adds `"tools"`, `"runs"` |
| `agent/observe/service.py` → `View` | `Literal["overview", "agents", "models"]` → adds `"tools"`, `"runs"` |

`agent/observe/facade.py` needs **no routing change**: `_NATIVE_QUERY_VIEWS` is derived via `get_args(View)`, so extending the service literal extends the facade's accepted set automatically. `agent/observe/adapters.py` **does** need a change — both new views must be registered in the `_VIEW_QUERY_BUILDERS` mapping or dispatch fails at request time.

`agent/observe/ui.py` carries a third, *internal* view list (`OBSERVE_VIEWS`) plus `OBSERVE_VIEW_WIRE_NAMES`, which maps internal names to wire names — note the existing internal `"usage"` maps to wire `"models"`. Both new views use identical internal and wire names, so their mapping entries are identity, but the entries must still exist because `test_observe_ui.py::test_observe_view_wire_names_map_internal_ids_to_openapi_view_enum` asserts full coverage of the wire enum.

---

## 7. Extended: `CoverageResult.dimension`

Two dimensions are added so an absent tool or run signal is explained rather than silently rendered as an empty table (FR-019 through FR-021).

| New dimension | Reported when |
| --- | --- |
| `tool_attribution` | The source is reachable and has traces, but no row carries a tool name — tool activity cannot be attributed. |
| `run_correlation` | The source is reachable and has traces, but runs cannot be formed or can only be formed at trace granularity because conversation identity is absent. |

`CoverageState` is unchanged — the existing eight states already express every outcome these two dimensions need. `agent/observe/ui.py`'s `COVERAGE_DIMENSION_LABELS` must gain a human-readable label for each new dimension, or the coverage table renders a raw identifier.

**Interaction with runtime attribution**: an agent classified `unknown` (FR-017) is reported through the existing `agent_attribution` dimension rather than a new one, since the cause is the same missing-identity signal that dimension already describes.

---

## 8. Extended: `ObserveResult` and `_CachedView`

Both frozen dataclasses in `agent/observe/service.py` gain one field:

| Field | Type | Notes |
| --- | --- | --- |
| `bounds` | `ResultBounds \| None` | `None` for views that are not row-bounded. |

`bounds` belongs in `_CachedView` as well as `ObserveResult` because it describes the *underlying data*, not the current request — the same reasoning the existing docstring gives for caching `diagnostics`, `coverage`, and `partial_failures` while recomputing `cache_status` and `refreshed_at`. A cached result must report the same shown-versus-total figures it reported when first produced.

---

## 9. Extended: `ObservedAgent`

The pre-existing `ObservedAgent` in `agent/observe/service.py` gains one field. This is **additive and non-breaking** — it is separate from, and must not be confused with, the breaking `source_kind` retarget described in §1.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `source_id` | `str` (`min_length=1`) | required | Telemetry source that produced this row (FR-023A). |

**Why this belongs in this feature**

Agent rows have the same defect the new entities were given `source_id` to avoid: `service.py` runs one query per source and appends the results, so two sources reporting the same agent produce two rows an operator cannot tell apart. Shipping `source_id` on `ObservedTool` and `ObservedRun` while leaving `ObservedAgent` without it would deliver a single release in which one of the three row-bearing views is inconsistent with the other two.

**Why it is cheap**

`normalize_agent_row(row, *, source: TelemetrySource)` already receives the source object and already reads `source.foundry_resource_id` from it. Populating `source_id=source.source_id` requires no KQL change, no new grouping key, and no additional query.

**Why "required" is still additive**

Adding a required property to a *response* model does not break consumers — they receive strictly more information. The constraint applies to producers, and this codebase has exactly one producer. Code that constructs `ObservedAgent` directly (tests, fixtures) must be updated in the same change, which is why the work carries its own tasks rather than riding along inside another phase.

**UI consequence**

The agents table currently renders a `source_kind` badge but never says *which* source a row came from. Surfacing `source_id` therefore requires a column in both the Python renderer and its embedded JavaScript twin in `agent/observe/ui.py` — the same two-places rule that applies to every other UI change in this feature.

---

## 10. Entity relationships

```text
ObserveScope ──authorizes──► telemetry sources
                                   │
                                   ▼
                          ObserveFilterState
                    (+ tool_name, run_key: narrowing only)
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
       ObservedAgent         ObservedTool          ObservedRun
        (agent_key) ◄──joins──(agent_key)──joins──► (agent_key)
              │                    │                    │
              ├──── source_kind: RuntimeKind (shared) ───┤
              └──── source_id: str (shared, required) ───┘

Every view result also carries:
  coverage: list[CoverageResult]   — why a signal is absent
  diagnostics: QueryDiagnostics    — how the query executed
  bounds: ResultBounds | None      — how much of the scope is shown
```

`agent_key` is the common join column across all three row entities, which is what lets a user move from an agent in the agents view to its tools and its runs without re-deriving identity. `source_id` is carried by all three so that a row can always be traced back to the telemetry source that produced it.

---

## 11. Requirements traceability

| Requirement | Satisfied by |
| --- | --- |
| FR-001 – FR-004 | `ObservedTool` fields and validators |
| FR-002, FR-023 | `source_id` on `ObservedTool` (§2) and `ObservedRun` (§3), required and non-nullable |
| FR-023A | `source_id` on the pre-existing `ObservedAgent` (§9), additive and non-breaking |
| FR-005, FR-025 | `ObservedTool` token-field omission (§2); `ObservedRun` nullable token totals (§3) |
| FR-006, FR-010 | `ObservedRun` counters |
| FR-007, FR-011 | `ObservedRun.input_tokens` / `output_tokens`, nullable, never zero-filled (§3) |
| FR-007A | `ObservedRun.turns` semantics (§3) |
| FR-008, FR-008A | `ObservedRun.status` + succeeded-implies-no-failures validator |
| FR-009, FR-009A | `run_key` + `run_key_kind` |
| FR-012 | `status == "in_progress"` with the 120s settling margin (trailing boundary only) |
| FR-012A | Window-scoped `started_at` / `duration_ms` and the leading-edge limitation note (§3) |
| FR-013, FR-014, FR-014A | `ObserveFilterState.tool_name` / `run_key` (§5); `validate_scope()` unchanged |
| FR-016 – FR-018A | `RuntimeKind` (§1) |
| FR-019 – FR-021 | `tool_attribution`, `run_correlation` dimensions (§7) |
| FR-022 | Attribution fields on both new entities |
| FR-028A | `ResultBounds` (§4) |
