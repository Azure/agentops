# Research: User and Department Usage and Cost Attribution

## Sources reviewed

- [Feature specification](./spec.md) and
  [AgentOps constitution](../../.specify/memory/constitution.md)
- Existing Observe contracts and implementation under
  `src/agentops/core/observe.py` and `src/agentops/agent/observe/`
- Existing cost contracts and allocation engine in `src/agentops/core/cost.py`
  and `src/agentops/agent/observe/cost_allocation.py`
- Existing hosted deployment allowlists and templates in
  `src/agentops/services/cockpit_deployment.py` and
  `src/agentops/templates/cockpit-hosted/infra/`
- [Microsoft Learn: AppRequests table](https://learn.microsoft.com/azure/azure-monitor/reference/tables/apprequests)
- [Microsoft Learn: Add and modify Azure Monitor OpenTelemetry](https://learn.microsoft.com/azure/azure-monitor/app/opentelemetry-add-modify)
- [Microsoft Learn: `hash_sha256()`](https://learn.microsoft.com/kusto/query/hash-sha256-function)
- [Microsoft Learn: Manage access to Log Analytics workspaces](https://learn.microsoft.com/azure/azure-monitor/logs/manage-access)
- [Microsoft Learn: Agent-level telemetry with Application Insights](https://learn.microsoft.com/microsoft-copilot-studio/advanced-bot-framework-composer-capture-telemetry)
- [OpenTelemetry: End user attributes](https://opentelemetry.io/docs/specs/semconv/registry/attributes/enduser/)
- [Microsoft Purview: Data Privacy](https://learn.microsoft.com/purview/purview-privacy)

## D1. Configuration transport and lifecycle

**Decision**: Add one optional `AGENTOPS_ATTRIBUTION_CONFIG` JSON setting,
parallel to `AGENTOPS_OBSERVE_SCOPE` and `AGENTOPS_COST_MODEL`. Parse it once at
Cockpit startup into a strict Pydantic v2 model. The contract contains:

- `version: 1`;
- `enabled`;
- a random operator-supplied `deployment_namespace` UUID;
- a positive `generation`;
- bounded department entries containing pseudonymous user keys and/or Microsoft
  Entra group object IDs.

Absence or `enabled: false` produces the same runtime behavior as today. A valid
enabled config activates attribution. Invalid configuration disables only
attribution and returns an actionable error for direct attribution requests;
other Observe views remain available. Hosted deployment validates and
propagates the value through the existing App Service/azd setting path.

**Rationale**: This is the established read-only deployment-configuration
pattern. Operator ownership makes namespace stability explicit: ordinary
restarts and version deployments reuse the same value, while rotation is a
deliberate config edit followed by the normal preview/confirmation flow. A
single versioned document also lets semantic fingerprints ignore harmless JSON
property and mapping order.

**Alternatives considered**:

- Add fields to `agentops.yaml`: rejected because evaluation configuration is a
  separate stable public contract.
- Create a database, upload API, or runtime-managed mapping: rejected by the
  stateless/read-only requirement.
- Generate a new namespace automatically on every deployment: rejected because
  deployments from another machine or CI could rotate keys accidentally.
- Add a new CLI command or flag for mapping/rotation: rejected because the
  product discussion did not approve new CLI surface and the existing
  environment/configuration path is sufficient.

## D2. Eligible telemetry identity

**Decision**: Version 1 recognizes exactly two representations of the same
authenticated identity signal:

1. the Azure Monitor `UserAuthenticatedId` column; and
2. `Properties["enduser.id"]`, the documented OpenTelemetry source that Azure
   Monitor maps to authenticated user identity.

Values are trimmed but otherwise compared exactly. When both are non-empty and
equal, they form one identity. When both are non-empty and differ, the record is
ambiguous and remains unattributed. The system never uses `UserId`,
`enduser.pseudo.id`, session/conversation IDs, browser/device IDs, IP addresses,
prompt content, or behavioral inference as a fallback.

**Rationale**: Microsoft documents `UserAuthenticatedId` as the persistent
string identifying an authenticated application user and `UserId` as
anonymous. Azure Monitor's OpenTelemetry guidance maps `enduser.id` to
`user_AuthenticatedId` and `enduser.pseudo.id` to `user_Id`. OpenTelemetry also
labels both direct and pseudonymous end-user attributes as sensitive. This
gives the implementation an explicit, reviewable allowlist instead of
runtime-name guesses.

Copilot Studio documentation shows `user_Id` in example telemetry and warns
that its usefulness depends on authenticated, consistent IDs. The clarified
requirement is stricter: because Azure Monitor classifies that column as
anonymous, it is not eligible. A Copilot Studio or other runtime is eligible
only when it supplies `UserAuthenticatedId`/`enduser.id`; otherwise coverage is
`not_reported`.

**Alternatives considered**:

- Accept `UserId` when it happens to be stable: rejected because stability
  cannot prove authenticated identity and would reintroduce the prohibited
  anonymous fallback.
- Configure arbitrary identity property names: rejected because an operator
  typo or weak signal could silently become a person identifier.
- Infer aliases from runtime kind: rejected because runtime type does not prove
  that a usable identity was emitted.
- Normalize email/UPN casing or merge different values heuristically: rejected
  because it could merge distinct principals without an authoritative source.

## D3. Deployment-scoped pseudonym derivation

**Decision**: Derive the pseudonymous key inside KQL before aggregate results
leave Azure Monitor:

```text
usr1.g{generation}.
sha256("agentops-attribution-v1|{deployment_namespace}|{generation}|{tenant_id}|{raw_identity}")
```

The digest uses the complete lowercase hexadecimal output of Kusto
`hash_sha256()`. Python uses the identical UTF-8 canonical input and
`hashlib.sha256()` for protected-response validation. The namespace is random
per deployment, non-secret, and never placed in a URL. The generation is
included in both the prefix and digest.

Aggregate queries project away every raw identity candidate before returning
rows. Protected delegated queries may return the one requested identity or the
bounded user list, alongside the same pseudonymous key, only in the current
non-cacheable response.

**Rationale**: Microsoft documents `hash_sha256()` as stable SHA-256 available
in Azure Monitor and returning a hex string. Computing before projection avoids
bringing raw cross-user identity into the aggregate application response or
cache. A random deployment namespace prevents the same source value from
producing the same key in another deployment. Full 256-bit output makes
collisions negligible without a stateful collision registry.

The namespace is deliberately not described as a secret. Pseudonyms remain
linkable personal data, and a reader of both deployment configuration and a
candidate low-entropy identity may test guesses. The feature reduces direct
exposure and cross-deployment correlation; it does not claim anonymization.

**Alternatives considered**:

- HMAC in Python: rejected for aggregate queries because Kusto has no matching
  HMAC function and returning raw identities to Python would cross the aggregate
  response/cache boundary.
- Plain unsalted SHA-256: rejected because it is correlatable across deployments.
- Deterministic salt from ARM resource ID: rejected because the same deployment
  identity could be reconstructed from public resource metadata and would not
  be operator-rotatable.
- Key Vault secret: rejected because it adds a resource, role assignment, secret
  lifecycle, and runtime dependency forbidden by the specification and
  constitution.
- Truncated digest: rejected because the complete digest is still comfortably
  inside current filter limits and avoids a needless collision tradeoff.

## D4. Mapping model and bootstrap

**Decision**: Configuration groups mappings by department. Each department has
a stable non-identifying ID, display label, a list of pseudonymous user keys,
and a list of group object IDs. A pseudonymous key may occur in only one
department across the config. Group IDs are normalized as UUIDs and may each
occur once; a principal matching different configured groups for different
departments is ambiguous.

Explicit user-key mapping always wins. Group mappings are considered only for
the signed-in principal, only in a delegated response, and only when the
telemetry identity exactly matches the validated principal `user_id` or
`user_name`. Group claims never classify another telemetry user. Claim overage
causes partial coverage and no directory lookup.

Bootstrap is a two-deployment workflow with no new command:

1. enable attribution with a namespace, generation, and an empty department
   list;
2. use the delegated Users view to see each raw identity beside its generated
   pseudonymous key;
3. add selected pseudonymous keys to departments;
4. preview and confirm the updated deployment configuration.

**Rationale**: This is the only workflow that both lets an operator associate a
real person with a key and keeps raw identity out of persisted configuration.
Grouping keys under departments reduces JSON size and makes conflicting
explicit mappings rejectable at startup.

**Alternatives considered**:

- Persist raw-identity-to-key pairs: rejected by FR-006 and FR-019.
- Hash raw identities supplied directly in config: rejected because the config
  would still persist those identities before hashing.
- Resolve departments through Microsoft Graph: rejected because it adds a
  directory read, permission, failure mode, and enrichment store.
- Apply caller group claims to all users: rejected because claims describe only
  the signed-in principal.

## D5. Aggregate and delegated credential boundary

**Decision**: Add one route-facing attribution operation with two grains:

- `group_by: department` starts with the existing aggregate deployment
  credential;
- `group_by: user`, any user filter, and any department result containing one
  active person use a fresh per-request delegated OBO credential.

The facade performs department cardinality classification before a result is
placed in the shared cache. If every returned department contains zero or at
least two active users, the aggregate result is safe and may use the existing
two-minute cache. If any contains exactly one active user, the aggregate result
is discarded and the complete request is rerun with the delegated credential.
Delegated results never enter `ObserveCache` and the HTTP response is
`Cache-Control: private, no-store`.

Missing/insufficient delegated access produces protected-or-unavailable
coverage and never retries through the deployment identity.

**Rationale**: `ObserveFacade.trace_content`,
`build_delegated_monitor_credential`, and `MissingUserAssertionError` already
enforce exactly this no-fallback boundary. Microsoft documents that log queries
require query/read permission and that `Log Analytics Reader` includes
read/query access; the existing application delegated `Data.Read` scope and
the operator's existing Azure permissions are therefore reused. No role or
consent is added.

**Alternatives considered**:

- Use deployment identity for pseudonymous user rows: rejected because listing
  or comparing a person is individual-level even without a raw name.
- Always use delegated access, including safe department aggregates: rejected
  because it would make the primary department view unavailable to operators
  who can use Cockpit through the existing aggregate boundary but lack direct
  log RBAC.
- Cache delegated pseudonymous rows: rejected because pseudonyms are linkable
  personal data and the response is individual-level.
- Return singleton department amounts from aggregate access: rejected because a
  known singleton exposes one person's consumption.

## D6. Opaque URL filter tokens

**Decision**: Add nullable `user_filter_token` and
`department_filter_token` fields to `ObserveFilterState`. The browser stores
them only in the page URL; no cookie, local storage, or session storage is used.

A user token contains a version/type prefix, current generation, semantic
configuration fingerprint, Observe-scope fingerprint, the already opaque
pseudonymous user key, and a binding digest over the signed-in principal,
scope, config, and user key. A department token contains the same generation,
config, and scope bindings plus a digest of the configured department ID; it
contains no label or group ID. Digests use canonical SHA-256 and are validated
with constant-time comparison.

The server checks syntax, generation, config fingerprint, scope fingerprint,
principal binding for users, and current mapping before issuing a query. Any
failure returns an actionable closed error. Reordering semantically identical
configuration does not change its canonical fingerprint; changing a mapping
does.

**Rationale**: A token can be validated statelessly and the embedded user key
can be compared to the pseudonym computed in KQL without reversing it to raw
identity. Generation invalidates prior rotation URLs, the semantic fingerprint
revalidates mapping changes, scope prevents cross-scope use, and principal
binding prevents a copied user URL from resolving for another principal.

**Alternatives considered**:

- Put raw identity, group ID, or department label in the URL: rejected by the
  privacy contract.
- Server-side token registry: rejected because it adds mutable persistent
  state.
- Encryption/signing with a new secret: rejected because a new secret store and
  role are unnecessary; recomputation against the current validated config is
  sufficient for fail-closed resolution.
- Use the pseudonymous key alone as the user filter: rejected because it would
  not be principal-, scope-, or config-bound.

## D7. Query shape and filter composition

**Decision**: Implement dedicated attribution query builders so disabled
existing views retain byte-for-byte query behavior. Each source query:

1. applies existing time, source, project, agent, model, tool, and run filters;
2. normalizes the two eligible identity representations and marks conflicts;
3. derives `user_key` only for one unambiguous authenticated identity;
4. joins explicit user mappings through one bounded KQL `datatable`;
5. applies an optional resolved department ID or pseudonymous user key filter;
6. summarizes only the requested attribution grain and measure;
7. projects coverage counters and no raw identity for aggregate responses.

For usage, user/department filters narrow the same authorized telemetry set and
compose with existing filters by logical AND. For cost, the configured period
and full authorized usage set remain the denominator: attribution filtering is
post-allocation and cannot redistribute a declared pool.

**Rationale**: A separate builder prevents opt-in logic from changing existing
queries and lets tests assert that aggregate rows never contain raw identity.
A KQL `datatable` keeps query count independent of mapping count and avoids
directory/API fan-out.

**Alternatives considered**:

- Modify every existing query unconditionally: rejected because disabled mode
  would no longer have identical behavior.
- Query once per user or mapping: rejected because latency and Azure Monitor
  load would grow linearly with configuration size.
- Return raw user rows and aggregate in Python: rejected because those rows
  could enter aggregate process results or shared caches.

## D8. Usage and cost attribution

**Decision**: The attribution endpoint supports `metric: usage|cost` and
`group_by: department|user`.

- Usage rows carry invocation and token totals. `invocations` is the primary
  ranking measure.
- Cost requests require an existing `cost_period_id` and exactly one
  `cost_component_id`. The selected component's allocated minor-unit amount is
  the primary ranking measure. This keeps every ranking inside one declared
  pool, currency, precision, allocation method, and denominator.
- Cost allocation extends the existing observation/grouping engine with
  `department`, `user`, `other_users`, and `unattributed` consumer outcomes.
  It performs full-period allocation first, then applies attribution filters.

**Rationale**: Existing cost components reconcile independently and currencies
cannot be ranked or added safely. Requiring one component for user/department
cost attribution avoids an invented exchange rate or cross-pool score and
preserves the original billed-cost contract.

**Alternatives considered**:

- Rank users across all components/currencies: rejected because amounts are not
  comparable without conversion.
- Use usage requests to rank a cost view: rejected because that would not be the
  active cost view's primary measure.
- Recalculate the cost denominator after a department/user filter: rejected
  because filtering would change already allocated amounts and misrepresent the
  declared total.

## D9. High-cardinality bounds and reconciliation

**Decision**: User results contain at most 500 rows. Without a user filter:

1. rank identified users by the active primary measure descending;
2. break ties by pseudonymous user key ascending;
3. keep 499 users;
4. combine every remaining identified user into exactly one `Other users` row;
5. keep unattributed usage/cost in an explicit summary outside the row array;
6. report total distinct users, omitted user count, and truncation.

The `Other users` row sums already measured usage or already allocated
minor-unit amounts; it never reruns allocation. Row totals plus the separate
unattributed summary reconcile exactly to the unfiltered source/component
total.

**Rationale**: This meets the existing 500-row contract without hiding
unattributed consumption or consuming a row needed by the explicit
499-plus-one requirement.

**Alternatives considered**:

- Return 500 users plus an extra aggregate: rejected because it violates the
  established bound.
- Count unattributed records as one of the 500 users: rejected because missing
  identity is not a user and would distort distinct-user counts.
- Drop omitted rows and report only a scalar: rejected because the requirement
  explicitly calls for a visible `Other users` aggregate.
- Rank before complete cost allocation: rejected because rounding could change
  final minor-unit order and totals.

## D10. Coverage classification

**Decision**: Add `ambiguous` to `CoverageState` and `user_attribution` to
`CoverageResult.dimension`. Attribution coverage carries optional counts for
eligible, identified, mapped, unattributed, ambiguous, and returned records,
plus `attribution_level`.

Per source and requested grain:

| Condition | State |
| --- | --- |
| Every eligible record has one supported identity and required mapping | `available` |
| Some eligible records are identified/mapped and others are not | `partial` |
| No eligible record reports `UserAuthenticatedId`/`enduser.id` | `not_reported` |
| Conflicting aliases or multiple applicable group departments prevent a safe result | `ambiguous` |
| Source or delegated table cannot be read | `inaccessible` or `protected_or_unavailable` |
| Query/normalization fails | `error` |

Coverage is independent per source and independent for usage versus cost.
Successful attributed and unattributed evidence from other sources remains
visible when one source fails.

**Rationale**: Empty data is otherwise indistinguishable from missing
instrumentation or denied access. Counts make partial and ambiguous outcomes
auditable without exposing identities.

**Alternatives considered**:

- Reuse `agent_attribution`: rejected because agent identity and end-user
  identity are operationally different signals and remediation steps.
- Convert missing identity to zero consumption: rejected because it creates a
  false success-shaped answer.
- Hide ambiguous rows: rejected because totals would not reconcile.

## D11. Privacy, logs, cache, and evidence

**Decision**:

- Treat pseudonymous keys, group IDs, and mapping configuration as
  privacy-sensitive linkable personal data, not anonymous data.
- Redact the attribution setting's value from human preview output and
  deployment journals; show enabled state, generation, fingerprint, and entry
  counts instead.
- Never include raw identity, mapping contents, user rows, or opaque user
  tokens in application logs, Doctor output, release evidence, shared cache
  keys, or error text.
- Safe department cache keys use only the config fingerprint, scope,
  normalized non-identity filters, and requested measure.
- Individual and singleton-department responses use `private, no-store` and do
  not touch `ObserveCache`.

**Rationale**: OpenTelemetry explicitly calls `enduser.pseudo.id` linkable PII,
and Microsoft privacy guidance treats identifiers such as email/UPN as
identifiable data. Pseudonymization reduces exposure but does not make
per-person evidence appropriate for shared caches or release artifacts.

**Alternatives considered**:

- Print the complete non-secret setting in preview: rejected because
  non-secret does not mean non-personal.
- Put the raw config in a cache key or diagnostic: rejected because a canonical
  fingerprint provides invalidation without duplication.
- Include pseudonymous user rows in evidence packs: rejected because release
  readiness does not need person-level telemetry.

## D12. Deployment, consent, and privilege surface

**Decision**: Add `AGENTOPS_ATTRIBUTION_CONFIG` to the deployment setting
allowlist, azd environment propagation, Bicep parameter file, and conditional
Web App setting. When enabled, every preview includes a non-blocking warning
that delegated access now covers individual usage and cost. The existing
interactive confirmation or guarded `--yes` is the only consent step.

The planned resources remain one App Service plan, one Web App, and one
user-assigned managed identity. Role assignments remain exactly `Reader` and
`Log Analytics Reader`; the existing Easy Auth application, OBO token exchange,
and delegated Azure Monitor `Data.Read` scope are reused. No Microsoft Graph
read, new API permission, Key Vault, or write-capable runtime role is added.

**Rationale**: This exactly fits the constitution's allowed provisioning
boundary and the issue's corrected premise: the credential pattern is already
shipped, while its protected-data use is deliberately widened and disclosed.

**Alternatives considered**:

- Add `Privileged Monitoring Data Reader` to the deployment identity: rejected
  because individual usage/cost must follow the operator's delegation and the
  deployment identity must not gain protected-data privilege.
- Add group/directory permissions: rejected because configured mappings and
  claims already in the validated principal are sufficient.
- Add a separate consent command/dialog: rejected because the clarification
  explicitly selects the normal deployment confirmation after a specific
  preview warning.

## D13. Rotation and failure semantics

**Decision**: Rotation means the operator supplies a fresh namespace, increments
generation, regenerates user mappings through the protected view, and confirms
the deployment preview. Runtime never rotates automatically. Tokens and keys
with a prior generation/config fingerprint fail before query execution.
Ordinary restart/redeployment with unchanged config preserves keys byte for
byte.

Errors are closed and local:

- absent/disabled config: attribution UI and coverage are absent;
- invalid config: attribution request fails explicitly, other Observe views
  continue;
- malformed/stale/foreign filter: 422-style validation response, no broader
  query;
- missing OBO assertion: protected/unavailable, no deployment-identity retry;
- partial source failure: partial coverage plus successful source evidence;
- no supported identity: `not_reported`, never an empty successful grouping.

**Rationale**: Generation plus semantic config and scope fingerprints provide
stateless invalidation. Isolating failures preserves existing Cockpit
availability without producing misleading attribution.

**Alternatives considered**:

- Automatic time-based rotation: rejected because it would silently invalidate
  mappings and URLs contrary to the explicit-rotation decision.
- Keep old generations valid during a grace period: rejected because the
  requirement says old keys and URLs fail closed.
- Fail Cockpit startup for invalid attribution config: rejected because an
  optional feature must not take unrelated read-only diagnostics offline.
