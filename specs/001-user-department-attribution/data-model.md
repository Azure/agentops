# Data Model: User and Department Usage and Cost Attribution

## Conventions

- Public contracts are strict Pydantic v2 models with unknown fields rejected.
- Configuration JSON is versioned, non-secret, privacy-sensitive, and limited
  to 64 KiB before parsing.
- Timestamps are UTC. Existing Observe time semantics remain unchanged; cost
  periods remain inclusive-start and exclusive-end.
- Missing identity, reported zero, inaccessible data, and no activity are
  distinct states.
- Raw identity is transient protected data. It may exist only in Azure Monitor
  source data, local query evaluation, and the current delegated response.
- Pseudonymous keys, group IDs, and filter tokens are linkable personal data.
  They are never represented as anonymous identifiers.
- Canonical fingerprints use validated, semantically sorted JSON so property or
  list order alone does not invalidate a token.

## 1. AttributionConfiguration

The complete optional configuration supplied by
`AGENTOPS_ATTRIBUTION_CONFIG`.

| Field | Type | Rules |
| --- | --- | --- |
| `version` | integer | Required; exactly `1`. |
| `enabled` | boolean | Required; defaults to no implicit enablement. |
| `deployment_namespace` | UUID or null | Required and non-null when enabled; generated randomly once per deployment and retained until explicit rotation. |
| `generation` | integer or null | Required and at least `1` when enabled; incremented on explicit rotation. |
| `departments` | list of `DepartmentDefinition` | At most 100; IDs unique. Empty is valid for bootstrap. |

### Model-level validation

1. Encoded JSON is at most 64 KiB.
2. Enabled configuration requires a namespace and generation.
3. Department IDs are unique.
4. At most 500 user keys and 100 group IDs occur across the model.
5. A user key occurs in at most one department.
6. A group ID occurs in at most one department definition. A principal may
   still match different configured group IDs and therefore be ambiguous.
7. Every user key has the current generation prefix.
8. Unknown and secret-shaped fields are rejected.
9. Canonical fingerprinting sorts departments, user keys, and group IDs and
   excludes no semantic field.

## 2. DepartmentDefinition

One operator-declared department and its permitted mapping inputs.

| Field | Type | Rules |
| --- | --- | --- |
| `id` | string | Required, stable, 1-64 URL-safe characters; non-identifying slug. |
| `label` | string | Required, trimmed, 1-128 characters; non-secret display label. |
| `user_keys` | list of `PseudonymousUserKey` | Unique, current-generation keys; may be empty. |
| `group_ids` | list of UUID | Unique Microsoft Entra group object IDs; may be empty. |

At least one of `user_keys` or `group_ids` must be non-empty for a department
entry. The complete `departments` list may be empty while the operator uses the
protected Users view to bootstrap mappings.

Group IDs are never emitted in API responses or URLs. They are matched only to
the validated signed-in principal's group claims.

## 3. AttributionConfigurationLoadResult

Internal startup state. It is not persisted independently.

| Field | Type | Meaning |
| --- | --- | --- |
| `state` | `absent`, `disabled`, `valid`, `invalid` | Parsing/enablement outcome. |
| `config` | `AttributionConfiguration` or null | Present only for valid/disabled parsed configuration. |
| `fingerprint` | string or null | Semantic SHA-256 fingerprint when parsing succeeds. |
| `error_code` | string or null | Stable non-sensitive code when invalid. |
| `message` | string or null | Actionable correction text without config values. |

### State transitions

```text
environment missing ---------------------------> absent
environment present + enabled false -----------> disabled
environment present + enabled true + valid ----> valid
environment present + malformed/invalid --------> invalid

absent/disabled/invalid -- restart with valid enabled config --> valid
valid -- restart with setting removed ------------------------> absent
valid -- restart with enabled false --------------------------> disabled
```

Only `valid` enables attribution. `absent` and `disabled` omit all attribution
UI, filters, and coverage. `invalid` rejects attribution requests but does not
block other Cockpit views.

## 4. EligibleIdentityEvidence

An internal per-record identity classification produced in KQL.

| Field | Type | Rules |
| --- | --- | --- |
| `authenticated_id` | string or null | Trimmed `UserAuthenticatedId`. |
| `otel_enduser_id` | string or null | Trimmed `Properties["enduser.id"]`. |
| `state` | `identified`, `not_reported`, `ambiguous` | Derived deterministically. |
| `raw_identity` | string or null | Present only when exactly one effective value exists; projected away from aggregate results. |
| `user_key` | `PseudonymousUserKey` or null | Derived only for `identified`. |

Classification:

```text
both empty                         -> not_reported
one non-empty                     -> identified
both non-empty and exactly equal  -> identified
both non-empty and unequal        -> ambiguous
```

No other telemetry field participates.

## 5. PseudonymousUserKey

A deployment-scoped identifier used in configuration and protected responses.

```text
usr1.g<generation>.<64 lowercase SHA-256 hex characters>
```

Canonical digest input:

```text
agentops-attribution-v1|<deployment_namespace>|<generation>|<validated_tenant_id>|<raw_identity>
```

### Invariants

1. Same deployment namespace, generation, tenant, and raw identity always
   produce the same key in Python and KQL.
2. A different namespace, generation, tenant, or raw identity changes the key.
3. The complete digest is retained.
4. A key with a non-current generation is invalid.
5. No reverse lookup or persisted raw-to-pseudonym table exists.

## 6. AttributionResolution

The pure result of resolving one identified user to a department.

| Field | Type | Rules |
| --- | --- | --- |
| `user_key` | `PseudonymousUserKey` | Required. |
| `department_id` | string or null | Present only when unambiguous. |
| `department_label` | string or null | Present only with `department_id`. |
| `source` | `explicit_user`, `principal_group`, `unmapped`, `ambiguous` | Required. |
| `matched_group_ids` | integer | Count only; group values are not returned. |
| `reason` | string | Non-identifying explanation. |

Resolution precedence:

```text
explicit user-key match
  -> mapped, ignore group candidates

no explicit match + identity exactly matches signed-in principal
  -> zero matching group departments: unmapped
  -> one matching group department: mapped
  -> more than one matching department: ambiguous

no explicit match + different/unknown principal
  -> unmapped (group claims are not applicable)
```

## 7. AttributionFilterToken

An opaque URL-safe selector. It is parsed and validated, never persisted by the
runtime.

### User token

Contains:

- contract/type version;
- current generation;
- shortened semantic config fingerprint;
- shortened Observe-scope fingerprint;
- pseudonymous user key;
- SHA-256 binding digest over tenant, current principal ID, scope fingerprint,
  config fingerprint, and user key.

The binding digest is compared in constant time. A token generated for one
principal cannot resolve for another principal.

### Department token

Contains:

- contract/type version;
- current generation;
- shortened semantic config fingerprint;
- shortened Observe-scope fingerprint;
- SHA-256 digest of the configured department ID.

The server resolves the digest against current configured departments. The
token contains no department label or group ID.

### Validation order

1. Parse bounded ASCII format.
2. Match token type to requested filter field.
3. Match current generation.
4. Match semantic config fingerprint.
5. Match Observe-scope fingerprint.
6. For user tokens, validate current-principal binding.
7. Resolve current user key or department.
8. Reject zero or multiple matches.

Any failure stops before a data-bearing response and never removes the filter or
runs a broader query.

## 8. AttributionQueryRequest

The body of `POST /api/observe/attribution`.

| Field | Type | Rules |
| --- | --- | --- |
| `metric` | `usage` or `cost` | Required. |
| `group_by` | `department` or `user` | Required. |
| `filters` | `ObserveFilterState` | Required; gains nullable user and department token fields. |
| `refresh` | boolean | Optional; existing refresh semantics for safe aggregate reads only. |

When `metric=cost`, `filters.cost_period_id` and
`filters.cost_component_id` are required and must identify one configured
component. Existing shared cost-period semantics remain authoritative.

When `group_by=user` or `user_filter_token` is set, delegated access is
unconditional. A `department_filter_token` is resolved before querying; a
singleton result escalates the complete response to delegated access.

## 9. AttributionUsage

One usage measure bundle used by rows and summaries.

| Field | Type | Rules |
| --- | --- | --- |
| `invocations` | integer | Non-negative; primary usage ranking measure. |
| `input_tokens` | integer or null | Null when never reported. |
| `output_tokens` | integer or null | Null when never reported. |
| `tool_invocations` | integer or null | Null when not applicable/reported. |
| `active_session_seconds` | decimal string or null | Period-scoped; null when unavailable. |

Summation preserves null-versus-zero: a field is null when no contributing
record reports it, otherwise it is the sum including reported zeros.

## 10. AttributionCost

One user's or department's amount from one already configured cost component.

| Field | Type | Rules |
| --- | --- | --- |
| `period_id` | string | Existing configured period. |
| `component_id` | string | Required selected component. |
| `amount` | decimal string | Exact configured minor-unit precision. |
| `currency` | string | Existing component currency. |
| `currency_minor_units` | integer | Existing component precision. |
| `usage_numerator` | decimal string | Existing applied allocation numerator. |
| `usage_denominator` | decimal string | Complete full-period denominator. |
| `allocation_key` | existing enum | Existing applied key. |
| `confidence` | existing cost confidence | Includes attribution completeness. |

Filtering and `Other users` folding happen after allocation. The denominator,
minor-unit rounding, and amount of every retained user never change.

## 11. AttributionRow

One displayed group.

| Field | Type | Rules |
| --- | --- | --- |
| `kind` | `department`, `user`, `other_users` | Required discriminator. |
| `department_id` | string or null | Department rows only. |
| `department_label` | string or null | Department rows only. |
| `user_key` | `PseudonymousUserKey` or null | User rows only; never `other_users`. |
| `filter_token` | string or null | User/department rows only; opaque selector for the current response context. |
| `raw_identity` | string | Required for user rows and present only in the current delegated response. |
| `member_count` | integer or null | Department/Other rows; exact active identified-user count. |
| `usage` | `AttributionUsage` | Required for `metric=usage`; supporting evidence for cost. |
| `cost` | `AttributionCost` or null | Required for `metric=cost`. |
| `mapping_state` | `mapped`, `unmapped`, `ambiguous`, `not_applicable` | Required. |

An `other_users` row has no filter token, raw identity, user key, or department
label. It represents only omitted identified users.

## 12. AttributionSummary

Totals retained outside the bounded row array. Both variants contain
`distinct_users` (null only when the source cannot report it) and
`omitted_users`.

### UsageAttributionSummary

| Field | Type | Rules |
| --- | --- | --- |
| `metric` | literal `usage` | Discriminator. |
| `total` | `AttributionUsage` | Unfiltered usage in the authorized scope. |
| `attributed` | `AttributionUsage` | Usage represented by identified rows plus `Other users`. |
| `unattributed` | `AttributionUsage` | Missing/ambiguous identity or unresolved department usage. |

### CostAttributionSummary

| Field | Type | Rules |
| --- | --- | --- |
| `metric` | literal `cost` | Discriminator. |
| `period_id`, `component_id` | string | One configured cost pool. |
| `declared_total` | decimal string | Existing configured component total. |
| `attributed_amount` | decimal string | Sum represented by identified rows plus `Other users`. |
| `unattributed_amount` | decimal string | Allocated amount without safe attribution. |
| `unallocated_amount` | decimal string | Existing zero-denominator/unallocatable remainder. |
| `currency`, `currency_minor_units` | string, integer | Existing component currency and precision. |
| `allocation_key`, `confidence` | existing enums | Existing applied method and confidence. |
| `total_usage`, `attributed_usage`, `unattributed_usage` | `AttributionUsage` | Supporting allocation evidence. |

Invariants:

```text
usage.total = usage.attributed + usage.unattributed

cost.declared_total
  = cost.attributed_amount
  + cost.unattributed_amount
  + cost.unallocated_amount

attributed user amount
  = sum(individual user rows)
  + Other users amount
```

## 13. AttributionViewData

The `data` object returned by the attribution endpoint.

| Field | Type | Rules |
| --- | --- | --- |
| `metric` | `usage` or `cost` | Echoes validated request. |
| `group_by` | `department` or `user` | Echoes validated request. |
| `access_boundary` | `aggregate` or `delegated` | Actual credential path used. |
| `rows` | list of `AttributionRow` | At most 500. |
| `summary` | `AttributionSummary` | Always present, including no-data cases. |
| `primary_measure` | `invocations` or `allocated_amount` | Determines ranking. |
| `calculated_at` | datetime | Required. |
| `latest_observed_at` | datetime or null | Latest included source activity. |

For `group_by=user`, if more than 500 identified users are in scope, rows are
the top 499 plus exactly one `other_users` row. Unattributed usage or cost
remains outside the row array in the summary. The enclosing existing
`ResultBounds` records
`rows_shown=500`, the exact distinct-user `rows_total_in_scope`, and
`truncated=true`.

## 14. AttributionCoverageDetails

Optional fields added to a `CoverageResult` whose dimension is
`user_attribution`.

| Field | Type | Rules |
| --- | --- | --- |
| `attribution_level` | `user` or `department` | Requested grain. |
| `metric` | `usage` or `cost` | Requested measure; coverage is independent across measures. |
| `component_id` | string or null | Selected component for cost coverage only. |
| `eligible_records` | integer or null | Records in the applicable source/time/scope. |
| `identified_records` | integer or null | Records with one supported identity. |
| `mapped_records` | integer or null | Identified records with a department. |
| `unattributed_records` | integer or null | Missing identity or mapping. |
| `ambiguous_records` | integer or null | Conflicting alias/mapping evidence. |
| `returned_records` | integer or null | Rows represented before UI grouping. |

`CoverageState` gains `ambiguous`; `CoverageResult.dimension` gains
`user_attribution`. Counts are omitted when the source is inaccessible rather
than represented as zero.

## 15. Query and access lifecycle

```text
load/validate enabled configuration
  -> validate existing Observe scope and opaque filters
  -> choose metric and authoritative time/component
  -> normalize eligible authenticated identity in KQL
  -> derive deployment-scoped user key in KQL
  -> join explicit mapping / classify coverage
  -> for department: run uncached cardinality classification
       -> all groups safe: aggregate credential, safe result may be cached
       -> any singleton: discard aggregate result and rerun delegated
  -> for user: delegated credential directly, no shared cache
  -> for cost: allocate complete component before display filtering/folding
  -> rank and fold top 499 + Other users where required
  -> reconcile rows plus unattributed summary
  -> issue current scope/config/principal-bound filter tokens
  -> serialize coverage and diagnostics without identity-bearing values
  -> apply private, no-store to delegated response
```

## 16. Rotation lifecycle

```text
enabled config generation N
  -> stable namespace/config across restart or version deployment
  -> identical pseudonymous keys and valid URL tokens

operator chooses fresh namespace and generation N+1
  -> deployment preview warns that mappings and old URLs become stale
  -> normal confirmation applies the new config
  -> all generation N keys/tokens fail closed
  -> operator bootstraps fresh mappings through delegated Users view
```

No grace period, previous-generation lookup, automatic rotation, or runtime
write exists.
