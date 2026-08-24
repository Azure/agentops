# Contract: Models View Row Payload

**Feature**: `012-granular-token-dimensions` | **Model**: `ModelUsage` in
`src/agentops/core/observe.py` | **Consumers**: `render_usage_table` (server-side Python) and
`renderUsage` (client-side JavaScript), both in `src/agentops/agent/observe/ui.py`

`ModelUsage` inherits `ContractModel`, which sets `extra="forbid"`. Unknown keys are rejected,
so the unnormalized passthrough is an explicitly typed field rather than loose extras.

## Shape

```jsonc
{
  // existing fields, unchanged
  "project_resource_id": "…",
  "agent_id": null,
  "deployment": "gpt-4o-prod",
  "model": "gpt-4o",
  "requests": 1284,
  "failures": 3,
  "p95_latency_ms": 812.4,
  "input_tokens": 940213,
  "output_tokens": 118442,
  "last_seen": "2026-08-24T11:02:17Z",

  // new fields, all optional with defaults
  "cache_read_tokens": 402118,
  "cache_write_tokens": null,
  "reasoning_tokens": 22940,
  "additional_token_classes": {},
  "additional_token_classes_truncated": false,
  "token_classes_partial": true
}
```

## Field contract

| Field | Type | Default | Constraint |
|---|---|---|---|
| `cache_read_tokens` | `int \| null` | `null` | `>= 0` |
| `cache_write_tokens` | `int \| null` | `null` | `>= 0` |
| `reasoning_tokens` | `int \| null` | `null` | `>= 0` |
| `additional_token_classes` | `object<string, int>` | `{}` | values `>= 0`; at most 5 entries |
| `additional_token_classes_truncated` | `bool` | `false` | — |
| `token_classes_partial` | `bool` | `false` | — |

### Three-valued token counts

| Value | Meaning | Rendering |
|---|---|---|
| `null` | Not reported by telemetry | `Not reported` via `_render_maybe_missing` |
| `0` | Reported, and the count was zero | `0` |
| `n > 0` | Reported count | formatted number |

Collapsing `null` into `0` is a contract violation (FR-007). Per
[research.md D4](../research.md), `null` is the expected value for Foundry-native workloads.

### `additional_token_classes` keys

Keys are source attribute names **carried verbatim**, never renamed, prettified, or
lower-cased. Values are never merged into a normalized class field and never influence coverage
state (FR-005, FR-009).

### `token_classes_partial`

`true` exactly when at least one of `cache_read_tokens`, `cache_write_tokens`,
`reasoning_tokens` is non-`null` **and** at least one is `null`. Computed once during
normalization so both renderers read the same value.

## Rendering contract

| Requirement | Rule |
|---|---|
| FR-014 | Granular class values appear in the models table |
| FR-022 | A per-row partial indicator is shown when `token_classes_partial` is `true` |
| FR-021 | A truncation indicator is shown when `additional_token_classes_truncated` is `true` |
| FR-016 | The literal string `(observed usage, not billing data)` is preserved verbatim |
| FR-017 | No monetary, cost, price, rate, spend, charge, or billing wording anywhere |
| FR-015 | `input_tokens` and `output_tokens` render exactly as they do today |
| — | The Python and JavaScript renderers produce equivalent output for the same payload |

## Example payloads

**All three classes reported**

```jsonc
{
  "cache_read_tokens": 402118,
  "cache_write_tokens": 18004,
  "reasoning_tokens": 22940,
  "additional_token_classes": {},
  "additional_token_classes_truncated": false,
  "token_classes_partial": false
}
```

**Foundry-native workload — nothing granular reported** (the expected steady state)

```jsonc
{
  "cache_read_tokens": null,
  "cache_write_tokens": null,
  "reasoning_tokens": null,
  "additional_token_classes": {},
  "additional_token_classes_truncated": false,
  "token_classes_partial": false
}
```

`token_classes_partial` is `false` here: nothing is reported, so nothing is *partially*
reported. The coverage entry carries `not_reported` for this case.

**Reported zero, distinct from not reported**

```jsonc
{
  "cache_read_tokens": 0,
  "cache_write_tokens": null,
  "reasoning_tokens": null,
  "token_classes_partial": true
}
```

**Passthrough retained and truncated**

```jsonc
{
  "cache_read_tokens": 402118,
  "cache_write_tokens": null,
  "reasoning_tokens": null,
  "additional_token_classes": {
    "gen_ai.usage.audio_input_tokens": 4021,
    "gen_ai.usage.audio_output_tokens": 990,
    "gen_ai.usage.ephemeral_1h_input_tokens": 12,
    "gen_ai.usage.ephemeral_5m_input_tokens": 340,
    "gen_ai.usage.image_input_tokens": 7788
  },
  "additional_token_classes_truncated": true,
  "token_classes_partial": true
}
```

Seven eligible unmapped attributes were present; the five retained are the first five by
ascending attribute name, and the flag records that others were dropped
([research.md D6](../research.md)).

## Backward compatibility

Every new field is optional with a default, so a `ModelUsage` constructed without them
validates and serializes exactly as before. No existing field changed type, name, or
constraint. The change is additive under Constitution I.
