# Contract: `token_usage` Coverage Entry on the Models View

**Feature**: `012-granular-token-dimensions` | **Model**: `CoverageResult` in
`src/agentops/core/observe.py` | **Producer**: models branch of the view builder in
`src/agentops/agent/observe/service.py`

**No schema change.** `CoverageResult.dimension` already permits `"token_usage"` and
`CoverageState` already contains `"partial"`. This contract describes a **new call site** on the
models view, which today emits only a `model_attribution` entry
([research.md D8](../research.md)).

## Shape

```jsonc
{
  "source_id": "…",
  "dimension": "token_usage",
  "state": "partial",
  "reason": "…",
  "next_action": "…",
  "refreshed_at": "2026-08-24T11:02:17Z"
}
```

## State selection

Arms are evaluated in order; the first match wins.

| Order | Condition | `state` | `reason` describes | `next_action` directs to |
|---|---|---|---|---|
| 1 | query `status != "success"` | `partial` / `error` | Query degradation — **existing text, unchanged** | Access or retry |
| 2 | `row_count == 0` | `no_data` | No matching telemetry in the window | Widen the window — existing text |
| 3 | inventory `not_reported` | `not_reported` | Rows exist but carry no granular token class | Instrumentation |
| 4 | inventory `partial` | `partial` | Some granular classes reported, some absent | Instrumentation |
| 5 | otherwise | `available` | All granular classes reported | — |

Arm 1 precedes the inventory arms because a degraded query yields an incomplete row set, from
which no trustworthy statement about workload instrumentation can be made
([research.md D7](../research.md)).

## Rows eligible for the inventory

Arms 3, 4 and 5 are computed over **token-reporting rows only**. A row that carries no token counter
at all — neither `input_tokens` / `output_tokens` nor any granular class — is excluded from the fold,
because it says nothing about whether the workload emits granular classes. This is what prevents an
out-of-scope source whose rows carry no token attributes (Copilot Studio) from dragging an in-scope,
token-reporting source down to `not_reported` (FR-019). When no row qualifies, the inventory is
`not_reported` and arm 3 applies.

## Disambiguating the overloaded `partial`

`state == "partial"` can arrive from arm 1 or arm 4. Consumers **must not** branch on `state`
alone to decide what the operator should do; the distinction is carried by `reason` and
`next_action`, which are what the coverage panel renders.

| Origin | Distinguishing property |
|---|---|
| Arm 1 | `next_action` refers to source access, permissions, or retrying the query |
| Arm 4 | `next_action` refers to the attributes the workload emits |

Adding a new `CoverageState` member was rejected: widening a public `Literal` is a breaking
contract change under Constitution I, for a distinction the text fields already carry.

## Text constraints

| Constraint | Rule |
|---|---|
| FR-017 | No monetary, cost, price, rate, spend, charge, or billing wording |
| FR-012 | `not_reported` and `partial` are described as instrumentation states, not failures |
| FR-006 | No text implies a class can be inferred from the totals |
| — | `next_action` names the attribute group concretely, consistent with the existing `"Confirm the workload emits the expected gen_ai.* attributes."` phrasing used elsewhere |

Per [research.md D4](../research.md), Azure's own instrumentation emits only
`gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens`, so `not_reported` is the
**expected** outcome for a Foundry-native workload. Text that frames it as an error would flag
virtually every Foundry deployment as broken.

## Relationship to the per-row indicator

This entry and the per-row `token_classes_partial` flag are **both** required by clarification
Q3 (answer B): the coverage panel states the source-level situation, and the row-level indicator
shows which specific rows are affected. Neither substitutes for the other.

## Backward compatibility

The agents view continues to emit its own `token_usage` entry through the unchanged
`token_reporting_state` two-state helper. Adding an entry on the models view does not alter the
agents view's entry, its states, or its text.
