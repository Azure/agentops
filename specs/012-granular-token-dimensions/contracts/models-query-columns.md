# Contract: Models View Query Columns

**Feature**: `012-granular-token-dimensions` | **Producer**: `build_models_query` in
`src/agentops/agent/observe/queries.py` | **Consumer**: `normalize_model_row` in
`src/agentops/agent/observe/service.py`

This contract defines the column set that the models-view telemetry query must return. It is an
**internal** contract between the query builder and the normalization layer, versioned together
with them.

## Existing columns (unchanged)

| Column | Type | Source |
|---|---|---|
| `project_resource_id` | string | `Properties["gen_ai.project.id"]` |
| `model` | string | `coalesce(Properties["gen_ai.request.model"], Properties["gen_ai.response.model"])` |
| `deployment` | string | `Properties["gen_ai.request.deployment"]` |
| `requests` | long | `count()` |
| `failures` | long | `countif(Success == false)` |
| `p95_latency_ms` | real | `percentile(DurationMs, 95)` |
| `input_tokens` | long | `sum(toint(Properties["gen_ai.usage.input_tokens"]))` |
| `output_tokens` | long | `sum(toint(Properties["gen_ai.usage.output_tokens"]))` |
| `last_seen` | datetime | `max(TimeGenerated)` |

## New columns

| Column | Type | Nullable | Definition |
|---|---|---|---|
| `cache_read_tokens` | long | yes | Sum over the cache-read alias set |
| `cache_write_tokens` | long | yes | Sum over the cache-write alias set |
| `reasoning_tokens` | long | yes | Sum over the reasoning alias set |
| `extra_token_classes` | dynamic (bag) | yes | `{attribute_name: summed_count}` for eligible unmapped attributes |

### Alias sets

Each normalized class coalesces the accepted source attribute names defined in
[research.md D2](../research.md). All names are within the `gen_ai.usage.*` group; nothing
outside that group is admitted ([research.md D1](../research.md)).

| Column | Accepted source attributes |
|---|---|
| `cache_read_tokens` | `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_read_input_tokens` |
| `cache_write_tokens` | `gen_ai.usage.cache_write.input_tokens`, `gen_ai.usage.cache_creation.input_tokens`, `gen_ai.usage.cache_creation_input_tokens` |
| `reasoning_tokens` | `gen_ai.usage.reasoning.output_tokens`, `gen_ai.usage.reasoning_tokens` |

### `extra_token_classes` eligibility

An attribute is included when **all** of the following hold:

1. Its name starts with `gen_ai.usage.`
2. Its name is not `gen_ai.usage.input_tokens` or `gen_ai.usage.output_tokens`
3. Its name is not a member of any alias set above
4. Its value parses to a number and that number is `>= 0`

Values are summed per attribute name within the aggregation group before being packed into the
bag. The query does **not** apply the five-attribute cap; that is applied during normalization
([research.md D6](../research.md)).

## Invariants

| Invariant | Rationale |
|---|---|
| Grouping keys remain `project_resource_id, model, deployment` | Row identity is unchanged (FR-015) |
| The row cap `\| top MAX_ROWS_PER_QUERY by requests desc` is unchanged | Bounded-query guarantee preserved |
| The view is served by **one** query per telemetry source | Performance goal; no additional round trips |
| `build_agents_query` and `build_usage_query` emit byte-identical text to today | Agents view and combined usage view are out of scope |
| No column is computed by subtracting one token count from another | FR-006 |

## Absence semantics

A `null` in any of the four new columns means **the attribute was not present in telemetry**.
It does not mean zero. Normalization maps `null` to `None` and a numeric zero to `0`, and the
renderers must keep those distinguishable (FR-007).
