# Feature Specification: User and Department Usage and Cost Attribution

**Feature Branch**: `placerda-issue-444-spec`

**Created**: 2026-08-25

**Status**: Draft

**Input**: User description: "https://github.com/Azure/agentops/issues/444"

## Clarifications

### Session 2026-08-25

- Q: Qual identificador deve ficar persistido no mapa que liga usuários a departamentos? → A: Código pseudônimo por implantação; a identidade real aparece somente sob demanda na visão individual protegida.
- Q: Ao habilitar a atribuição individual, como o operador deve confirmar que aceita ampliar o uso do acesso delegado para dados de uso e custo por pessoa? → A: O preview mostra um aviso específico e a confirmação normal da implantação aceita a mudança.
- Q: Quando uma fonte de telemetria oferece mais de um identificador de usuário, qual deles deve valer para atribuição por pessoa e departamento? → A: Somente uma identidade autenticada e estável, incluindo aliases documentados por runtime; sem fallback anônimo.
- Q: Quando uma consulta tiver mais de 500 usuários distintos, como a visão individual deve representar os usuários excedentes? → A: Mostrar até 499 usuários e uma linha agregada "Outros usuários", com truncamento explícito e reconciliação exata.
- Q: Por quanto tempo a chave pseudônima de cada usuário deve permanecer igual? → A: Durante toda a vida da implantação, inclusive após reinícios e novas versões; muda apenas por rotação explícita.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Attribute Shared Consumption by Department (Priority: P1)

An operator responsible for a shared agent can see which departments drive its
usage and, when cost allocation is available, which departments consume the
allocated cost. The operator can narrow the view to one department without
exporting and manually joining telemetry.

**Why this priority**: Department attribution answers the primary chargeback and
capacity-planning question while preserving aggregate access for normal
operational use.

**Independent Test**: Enable attribution with telemetry that contains user
identifiers and a department mapping, then verify that usage and available cost
are grouped and filterable by department and reconcile to the unfiltered totals.

**Acceptance Scenarios**:

1. **Given** attribution is enabled and all eligible records map to departments,
   **When** an operator opens an applicable usage or cost view, **Then** the view
   shows a department grouping whose totals equal the corresponding unfiltered
   totals for the same scope and period.
2. **Given** a department grouping is displayed, **When** the operator applies a
   department filter, **Then** all applicable results are narrowed to that
   department and the filter is preserved when the resulting page URL is
   reopened.
3. **Given** some identified users have no unambiguous department mapping,
   **When** the operator views department attribution, **Then** their consumption
   appears as unmapped and coverage reports partial attribution rather than
   dropping or assigning the consumption.
4. **Given** allocated cost is not configured or unavailable, **When** the
   operator views attribution, **Then** usage attribution remains available and
   cost coverage explains why cost cannot be grouped.
5. **Given** attribution is enabled in a deployment configuration, **When** the
   operator previews the deployment, **Then** the preview explains that delegated
   access will cover individual usage and cost, and the change proceeds only
   after the normal deployment confirmation.

---

### User Story 2 - Investigate Individual Consumption Safely (Priority: P2)

An authorized operator can narrow usage and available cost to an individual
person when operational investigation requires it. Because this reveals an
individual's activity, the view uses the signed-in operator's delegated access
and is never treated as a shareable aggregate.

**Why this priority**: Individual drilldown enables investigation and validation,
but it must not weaken the existing identity boundary used for protected data.

**Independent Test**: Request an individual-level view as an authorized user,
then verify delegated authorization, scope enforcement, non-cacheable handling,
and the absence of raw identity values from the page URL and persistent state.

**Acceptance Scenarios**:

1. **Given** an authorized operator has delegated access within the configured
   observation scope, **When** the operator selects an individual user, **Then**
   the result contains only that person's in-scope usage and available allocated
   cost and, when telemetry supplies it, shows the person's real identity
   alongside the pseudonymous key for that protected response only.
2. **Given** a request would reveal or narrow to one individual, **When** the
   delegated credential is absent or insufficient, **Then** the result is
   reported as protected or unavailable and is not retried through aggregate
   access.
3. **Given** an individual filter is active, **When** the page URL is copied or
   reopened, **Then** the filter round-trips through an opaque value and the raw
   telemetry identity is not present in the URL.
4. **Given** a department contains exactly one attributed person, **When** that
   department is selected, **Then** the resulting view follows the same protected
   handling as an explicit individual view.
5. **Given** more than 500 distinct users are in scope, **When** the operator
   opens the individual attribution view, **Then** it shows up to 499 users plus
   one "Other users" aggregate, marks the result as truncated, and preserves the
   complete usage and cost totals.

---

### User Story 3 - Understand Attribution Coverage (Priority: P3)

An operator can tell whether each telemetry source supplies enough identity data
for user and department attribution. Missing, partial, conflicting, or
unavailable identity is shown explicitly with an actionable explanation.

**Why this priority**: Coverage prevents an empty grouping or zero value from
being mistaken for no consumption and helps operators improve instrumentation.

**Independent Test**: Query sources with complete, partial, absent, and
conflicting user identity and verify that each source receives the correct
coverage state without inferred identities or suppressed consumption.

**Acceptance Scenarios**:

1. **Given** a telemetry source emits no supported user identity, **When**
   attribution is requested, **Then** user-attribution coverage states that the
   identity was not reported and provides a next action.
2. **Given** only some eligible records contain an unambiguous user identity,
   **When** attribution is requested, **Then** coverage is partial and both
   attributed and unattributed consumption remain visible.
3. **Given** identity values conflict or cannot be associated safely, **When**
   attribution is requested, **Then** the affected records remain unattributed
   and coverage explains the ambiguity.
4. **Given** attribution is disabled, **When** existing observation views are
   used, **Then** user-attribution coverage and identity dimensions are absent
   and all existing behavior remains unchanged.

### Edge Cases

- A record reports usage or allocated cost but no user identifier.
- A source reports only an anonymous, session-scoped, device-scoped, or
  browser-scoped identifier; the record remains unattributed because that value
  cannot reliably represent one person.
- A source reports different identifiers for what may be the same person; the
  records are not merged unless the source or operator mapping explicitly
  establishes the relationship.
- The same identifier appears in different tenants or outside the configured
  observation scope; attribution remains isolated to the validated tenant and
  scope.
- A person matches multiple department mappings; the result is treated as
  ambiguous and remains unmapped instead of choosing a department silently.
- Group claims are unavailable because of claim overage; no directory lookup is
  attempted and coverage explains that claim-based grouping is unavailable.
- A configured department has no matching telemetry during the selected period;
  it is not presented as having consumed zero unless zero was explicitly
  reported.
- An opaque user filter is stale, malformed, outside scope, or opened by a
  different principal; validation fails closed and never broadens the query.
- An operator declines or does not complete the deployment confirmation after
  the attribution warning; the deployed attribution state remains unchanged.
- A mapping changes while an old filtered URL exists; the filter is revalidated
  against the current configuration before any data is shown.
- A pseudonymous-key rotation occurs; mappings and individual URLs created with
  the previous key fail closed until they are regenerated, while ordinary
  restarts and version deployments preserve existing keys.
- A department resolves to one person; its filtered result is protected as an
  individual-level view.
- A source reports user identity for usage but lacks the data needed for cost
  allocation; usage and cost coverage are reported independently.
- More than 500 distinct users are in scope; the bounded individual view keeps
  the 499 users with the highest value for the view's primary consumption
  measure, combines every remaining user into "Other users", and reports that
  truncation occurred. Ties use the pseudonymous key for stable ordering.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: User and department attribution MUST be disabled by default.
- **FR-002**: When attribution is absent or disabled, existing usage, cost,
  coverage, filtering, authorization, and deployment behavior MUST remain
  unchanged.
- **FR-003**: Enabling attribution MUST require explicit operator configuration.
  The deployment preview MUST state that delegated access will be widened to
  individual usage and cost, and the change MUST require the existing deployment
  confirmation. Removing or disabling the configuration MUST reverse the change.
- **FR-004**: User attribution MUST use only a non-empty, authenticated, stable
  user identifier explicitly present in source telemetry. Documented
  runtime-specific aliases MAY be normalized to this meaning, but anonymous,
  session-scoped, device-scoped, and browser-scoped identifiers MUST NOT be used
  as fallbacks. Identity MUST NOT be inferred from prompts, behavior, or other
  indirect signals.
- **FR-005**: The system MUST NOT query a directory or another enrichment source
  to obtain a person's identity, name, group membership, or department.
- **FR-006**: Cross-user department grouping MUST use an operator-supplied,
  versioned mapping from deployment-scoped pseudonymous user keys or known group
  identifiers to department labels; raw telemetry user identifiers MUST NOT
  appear in this persisted mapping. A pseudonymous key MUST remain stable across
  ordinary restarts and version deployments for the life of one deployment,
  MUST NOT be reusable to correlate a user across deployments, and MUST change
  only through explicit operator rotation.
- **FR-007**: Validated group claims MAY determine the department of the matching
  signed-in principal, but MUST NOT be used to infer the department of a
  different telemetry user.
- **FR-008**: An explicit pseudonymous-user-to-department mapping MUST take
  precedence over a claim-based department mapping for the same user.
- **FR-009**: A user with no mapping, multiple equally applicable mappings, or
  unavailable required claims MUST remain unmapped and MUST contribute to
  partial attribution coverage.
- **FR-010**: Applicable usage views MUST support grouping and filtering by user
  and department when attribution is enabled.
- **FR-011**: Applicable cost views MUST support the same grouping and filtering
  when cost allocation is available, without changing the declared cost totals
  or allocation method.
- **FR-012**: User and department groupings MUST reconcile exactly to the
  corresponding unfiltered usage and allocated-cost totals for the same scope
  and period; unattributed consumption MUST remain part of the reconciliation.
- **FR-013**: Observation filters MUST add nullable user and department
  dimensions while preserving all existing filter behavior.
- **FR-014**: User and department filters MUST be validated against the
  authenticated tenant, configured observation scope, current attribution
  configuration, and applicable access boundary before data is returned.
- **FR-015**: An invalid, stale, ambiguous, or unauthorized identity filter MUST
  fail closed with an actionable message and MUST NOT fall back to a broader
  result.
- **FR-016**: Applied user and department filters MUST round-trip through the
  page URL without placing a raw user identifier, user name, group identifier,
  or other directly identifying value in the URL. URLs containing a key from an
  earlier explicit rotation MUST fail closed rather than resolving through raw
  identity or a broader filter.
- **FR-017**: Any view that reveals, lists, compares, or narrows to an individual
  person MUST be classified as an individual-level view.
- **FR-018**: Individual-level views MUST use the authenticated operator's
  delegated credential and MUST NOT fall back to the deployment identity.
- **FR-019**: Individual-level responses MUST be non-cacheable in shared caches.
  A raw identity MAY appear only in the current delegated response when supplied
  by telemetry; it MUST NOT be persisted in attribution configuration, browser
  storage, cookies, runtime storage, or application logs.
- **FR-020**: Department-only aggregates MAY continue to use aggregate access
  only when they do not reveal or narrow to one person.
- **FR-021**: Telemetry coverage MUST add a user-attribution dimension that can
  distinguish available, partial, not reported, inaccessible, ambiguous, and
  error outcomes using the existing coverage vocabulary where applicable.
- **FR-022**: Missing identity MUST produce an explicit coverage result with a
  reason and next action; it MUST NOT produce an empty or zeroed user grouping.
- **FR-023**: Partial failures from one source MUST NOT hide attributed or
  unattributed evidence returned by other sources.
- **FR-024**: Enabling attribution MUST introduce no new cloud role assignment,
  directory permission, or deployment-identity privilege.
- **FR-025**: The feature MUST NOT change who is authorized to use the Cockpit;
  it adds attribution dimensions within the existing permission model.
- **FR-026**: The runtime MUST remain stateless and MUST NOT create a persistent
  store or mutate user identities, mappings, filters, or individual activity.
  The versioned operator-supplied department mapping remains read-only deployment
  configuration, not runtime-managed storage.
- **FR-027**: A runtime is eligible for attribution only when its telemetry
  explicitly carries a usable user identifier; runtime type alone MUST NOT imply
  coverage.
- **FR-028**: The feature MUST NOT classify prompt intent, workload type, or user
  behavior.
- **FR-029**: An individual attribution result MUST contain at most 500 rows. If
  more than 500 distinct users are in scope, it MUST contain the 499 users with
  the highest value for the active view's primary consumption measure plus one
  "Other users" aggregate that includes every omitted user, MUST use the
  pseudonymous key to resolve ranking ties deterministically, MUST mark the
  result as truncated, and MUST preserve exact total reconciliation.

### Privacy and Authorization Boundary Requirements

#### Data classification, lifecycle, and permitted surfaces

- **FR-030**: Attribution data classes MUST follow this complete surface and
  lifecycle policy:

  | Data class | Permitted surfaces | Prohibited surfaces and retention |
  | --- | --- | --- |
  | Raw telemetry identity | Azure Monitor source data; transient query evaluation; the current delegated user-list, comparison, selected-user, or singleton response; transient DOM for that response | Attribution configuration; aggregate responses; URLs/history; cookies; local/session storage; shared or application caches; logs; diagnostics; errors; deployment preview/journal; Doctor/release evidence; retention after response/navigation |
  | Pseudonymous user key | Aggregate KQL evaluation; operator mapping configuration; current delegated response; opaque user-token validation | Standalone URL parameter; shared cache key/value; logs; diagnostics; errors; deployment preview/journal; Doctor/release evidence; claims of anonymity |
  | Group ID or group claim | Operator mapping configuration; validated current-principal claims; transient exact-principal resolution | API/UI response; URL/history; browser storage; cache; logs; diagnostics; errors; preview/journal; Doctor/release evidence; cross-user classification |
  | Complete mapping configuration | App Service/azd deployment input; protected deployment setting; validated startup memory; bounded KQL mapping input | API response; rendered preview value; deployment journal; cache key/value; logs; diagnostics; errors; Doctor/release evidence |
  | Opaque filter token | Current request/response links and the browser address/history or a copied URL | Cookies; local/session storage; shared cache keys; logs; diagnostics; errors; deployment journal; Doctor/release evidence; interpretation by clients |
  | Department ID/label | Mapping configuration; current aggregate or delegated API/UI response; protected query composition | Raw URL value; cache key; logs; diagnostics; errors; deployment journal; Doctor/release evidence |
  | Attribution and cardinality counts | Current response and UI; safe aggregate cache only after cardinality classification | Raw-identity association; logs; deployment journal; Doctor/release evidence; aggregate access when the selected result identifies one active person |

  Removing or disabling configuration removes attribution UI/filter/coverage
  surfaces after restart. Explicit rotation replaces the namespace and
  generation, invalidates prior pseudonyms/tokens/mappings, and requires a new
  protected bootstrap; no prior-generation lookup or grace path is retained.

- **FR-031**: Version 1 identity eligibility MUST recognize exactly the trimmed
  non-empty `UserAuthenticatedId` and documented OpenTelemetry `enduser.id`
  representations. Trimming surrounding whitespace is the only normalization;
  case folding and other canonicalization are prohibited. One non-empty value or
  two exactly equal values is identified; two unequal non-empty values is
  ambiguous; two empty values is not reported. `UserId`,
  `enduser.pseudo.id`, session/conversation IDs, device/browser IDs, IP/network
  addresses, prompts, behavior, runtime type, and operator guesses MUST never be
  fallbacks.

#### Access-boundary classification

- **FR-032**: The complete attribution response MUST use the delegated boundary
  when it lists users, compares users, applies a user filter, serves mapping
  bootstrap, or when any selected department, unmapped/ambiguous partition, or
  identity-bearing coverage/diagnostic partition contains exactly one active
  identified person. A bounded multi-user bootstrap response MAY contain raw
  identities only for the listed users and only in that current delegated
  response.
- **FR-033**: Safe aggregate classification MUST occur after tenant and Observe
  scope validation and after applying the requested time range, metric,
  component, and all existing and attribution filters. A department or
  unattributed partition with zero activity is omitted rather than represented
  as zero; every returned identity-bearing partition MUST contain at least two
  active identified people. If any partition contains one, the aggregate result
  MUST be discarded before caching and the complete request MUST rerun through
  delegated access.
- **FR-034**: Group claims MAY classify only telemetry whose effective identity
  exactly equals the current validated principal identifier. Persisted group IDs
  are privacy-sensitive configuration inputs, not response data. Duplicate group
  IDs across departments MUST make configuration invalid; multiple distinct
  matching groups mapped to different departments MUST produce ambiguity;
  missing claims or claim overage MUST produce partial/unmapped coverage. None of
  these cases permits Microsoft Graph or directory lookup.
- **FR-035**: Mapping bootstrap MUST be a two-deployment flow: first enable a
  valid namespace/generation with an empty department list and confirm the
  widened delegated boundary; then use the delegated, non-cacheable Users view
  to associate current raw identities with pseudonymous keys, update the
  operator-owned mapping, and preview and confirm the changed configuration
  again.

#### Closed failures, caches, and transient handling

- **FR-036**: Invalid, stale, ambiguous, foreign, or unauthorized selectors and
  protected-resource failures MUST use stable non-identifying error codes and
  corrective actions. Error text MUST NOT reveal whether a person, user key,
  mapping, group, token subject, department membership, or protected resource
  exists, and MUST NOT echo any privacy-sensitive input.
- **FR-037**: Timeouts, partial source failures, shared-cache failures, missing or
  expired delegated assertions, and delegated-token exchange/query failures MUST
  fail closed within the requested access boundary. They MUST NOT retry through
  a more privileged or broader credential, drop a selector, reuse stale data, or
  return a success-shaped empty/zero result. Successful independent sources MAY
  remain visible with explicit partial coverage.
- **FR-038**: Every user, comparison, bootstrap, user-filtered, and singleton
  response MUST bypass `ObserveCache` and application/intermediary caches and
  return exactly `Cache-Control: private, no-store`. Raw identity and user rows
  MAY exist only in request-local server memory, the current response body, and
  the current page DOM. Navigation, refresh, or replacement of that response
  MUST NOT copy them into application state. The only permitted browser-retained
  selector is an opaque token in the address/history or a copied URL; it remains
  linkable personal data and is validated anew on every request.
- **FR-039**: Token validation MUST distinguish syntax/type failure, generation
  rotation, semantic mapping change, harmless configuration reordering, Observe
  scope change, current-principal change, and zero/multiple current resolutions.
  All failures stop before querying. Semantically identical reordering preserves
  validity; every other mismatch fails closed without a previous-generation
  grace path.
- **FR-040**: Privacy-preserving diagnostics MAY record only stable error codes,
  aggregate source/state counts that pass the access-boundary rule, request
  timing, and non-sensitive configuration metadata such as enabled state,
  generation, fingerprint, and entry counts. Diagnostics MUST NOT record raw
  identities, pseudonymous keys, mapping contents, group IDs/claims, department
  labels, user rows, opaque filter tokens, or identity-bearing exact counts.

#### Dependencies, ownership, and privilege baseline

- **FR-041**: Attribution depends on the existing validated Easy Auth principal,
  tenant allowlist, Observe scope, OBO exchange, delegated Azure Monitor
  `Data.Read` scope, and the operator's direct log-data RBAC. Missing Easy Auth
  or tenant authorization is unauthorized/forbidden; invalid scope fails before
  querying; missing OBO or log RBAC is protected/inaccessible with no deployment
  identity fallback; a runtime that emits only anonymous identity is
  `not_reported`.
- **FR-042**: Only an operator already authorized to deploy/configure the hosted
  Cockpit or manage its azd environment may read or change
  `AGENTOPS_ATTRIBUTION_CONFIG`. This feature MUST NOT introduce a new per-user
  configuration permission model, runtime mapping mutation API, or configuration
  disclosure endpoint.
- **FR-043**: The zero-new-privilege baseline is the existing Cockpit App Service,
  user-assigned managed identity, Easy Auth application, delegated Azure Monitor
  `Data.Read` scope, and exactly the existing `Reader` and `Log Analytics Reader`
  role assignments. Enabling attribution MUST add no Microsoft Graph/directory
  permission, new delegated/application permission, Key Vault access,
  write-capable role, or broader deployment-identity capability.
- **FR-044**: Pseudonymous keys and opaque tokens MUST be described and handled as
  deterministic, linkable personal data, not anonymous data. The random
  deployment namespace limits cross-deployment correlation but is non-secret;
  anyone with the configuration and a candidate low-entropy identity may test a
  guess. Requirements and UI guidance MUST NOT claim irreversible anonymity.

#### Recovery and measurable failure outcomes

- **FR-045**: Correcting invalid configuration requires a corrected deployment
  setting and restart. Disabling/removing the setting restores disabled-state
  parity and makes attribution routes unavailable. Rotation requires a fresh
  random namespace, incremented generation, renewed preview/confirmation, and
  rebuilt mappings. Old filtered URLs fail closed and MUST NOT be translated,
  broadened, or resolved against retained prior data.
- **FR-046**: Acceptance coverage MUST include missing delegated access,
  insufficient direct log RBAC, stale/malformed/cross-principal tokens, group
  overage, conflicting aliases, invalid configuration, cache failure, timeout,
  and partial source failure. Each scenario MUST assert its documented status or
  coverage state, no broader query/credential fallback, no privacy-sensitive
  error data, and preservation of successful independent evidence where
  permitted.

### Affected Product Contracts

- **Observation filters**: Add nullable user and department dimensions with the
  same scope-validation guarantees as existing narrowing filters.
- **Telemetry coverage**: Add user attribution as an independently reported
  dimension.
- **Attribution configuration**: Add a versioned, non-secret, privacy-sensitive,
  opt-in department mapping that contains pseudonymous user keys or group
  identifiers but no raw user identity; absence of this configuration preserves
  current behavior.
- **Deployment preview**: Disclose the widened delegated-data boundary whenever
  attribution is enabled and require the normal deployment confirmation without
  adding a new role or permission consent.
- **Usage and cost views**: Add attribution groupings without changing existing
  usage measurements, cost methods, source-of-truth labels, or totals.
- **Access boundary**: Extend delegated protected handling from protected trace
  content to every individual-level attribution view; aggregate access remains
  unchanged.

### Key Entities

- **User Attribution**: An ephemeral association between an eligible telemetry
  record and an explicit telemetry-provided, authenticated, stable user
  identifier. Documented runtime aliases may express the same identity meaning;
  anonymous or session-bound identifiers do not. The identifier is protected
  data and is never enriched or persisted by the runtime.
- **Department Mapping**: A versioned operator declaration that associates
  deployment-scoped pseudonymous user keys or validated group identifiers with
  non-secret department labels. It contains no raw user identity and has one
  unambiguous department outcome per user. Pseudonymous keys remain stable for
  one deployment lifetime and are replaced only by explicit rotation.
- **Attribution Filter**: A nullable user or department selector that narrows an
  observation only after tenant, scope, configuration, and authorization
  validation. A user selector is represented in navigation by an opaque value.
- **Attribution Coverage**: A per-source statement of whether user attribution is
  available, partial, absent, inaccessible, ambiguous, or failed, including a
  reason and next action.
- **Attributed Aggregate**: Usage or allocated cost grouped by department or
  protected user identity while retaining unattributed consumption so grouped
  totals reconcile to the source total.

### Scope Boundaries

**In scope**:

- User and department dimensions for usage and available allocated cost.
- Explicit coverage for complete, partial, absent, inaccessible, and ambiguous
  user identity.
- Protected delegated access for individual-level views.
- Operator-supplied mappings and existing validated claims without directory
  enrichment.

**Out of scope**:

- Prompt, intent, workload, or behavior classification.
- Directory reads or identity enrichment.
- Persisting raw identity or attribution history, or creating a runtime-managed
  mapping store; the operator-supplied deployment configuration remains in
  scope.
- A new per-user permission model.
- New cloud roles, directory permissions, or deployment identity privileges.
- Changes to cost calculation, billing accuracy, forecasting, or invoices.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With attribution disabled, 100% of existing supported usage, cost,
  coverage, filter, and authorization acceptance tests produce their prior
  observable behavior.
- **SC-002**: For a test scope with complete identity and department mappings,
  100% of eligible usage and allocated cost is represented in user, department,
  or explicitly unattributed groups, and grouped totals reconcile exactly to the
  corresponding unfiltered totals.
- **SC-003**: Across complete, partial, absent, conflicting, and inaccessible
  identity test sources, 100% receive an explicit and accurate attribution
  coverage outcome; none are represented as an empty or zeroed grouping.
- **SC-004**: In security acceptance tests, 100% of individual-level requests
  require delegated authorization, bypass application/intermediary/shared
  caching, return `Cache-Control: private, no-store`, and expose no raw identity,
  pseudonymous key, group ID, mapping value, department label, user row, or
  opaque token outside the surfaces explicitly permitted by FR-030 and FR-038.
- **SC-005**: At least 90% of representative operators can identify the
  highest-consuming department and restore the same filtered view from its URL
  in under two minutes without exporting data.
- **SC-006**: At least 95% of supported department-filtered usage and cost views
  for a standard configured observation scope are displayed within five seconds.
- **SC-007**: In 100% of enabled deployment previews, the operator sees the
  delegated-boundary warning before confirmation, while a comparison against the
  FR-043 baseline confirms exactly the existing `Reader` and
  `Log Analytics Reader` role assignments and Azure Monitor `Data.Read`
  delegation, with zero new cloud roles, Graph/directory permissions, secrets,
  directory reads, write capabilities, or deployment-identity privileges.
- **SC-008**: For test scopes containing more than 500 distinct users, 100% of
  individual attribution results contain no more than 500 rows, include exactly
  one "Other users" aggregate for omitted users, report truncation, and reconcile
  exactly to the corresponding unfiltered totals.
- **SC-009**: Across test vectors, identical namespace, generation, tenant, and
  raw identity inputs produce byte-identical Python/KQL pseudonymous keys after
  ordinary restart and version deployment; changing any one of namespace,
  generation, tenant, or raw identity changes the key. After explicit rotation,
  100% of prior keys and URLs fail closed, and separate deployment namespaces
  never produce the same key for the same candidate identity.
- **SC-010**: For every FR-046 failure scenario, 100% of results match the
  documented closed status/coverage outcome, execute no broader query or
  alternate credential fallback, disclose no privacy-sensitive value, and retain
  successful independent-source evidence only where FR-037 permits it.

## Assumptions

- Operator-supplied mapping from deployment-scoped pseudonymous user keys is
  authoritative for department grouping across different users. Existing
  validated group claims can classify only the signed-in principal whose
  identity matches the telemetry user.
- An authorized operator can still identify a person when source telemetry
  provides a real identity: the delegated individual view shows that identity
  alongside its pseudonymous key for the current response without persisting it.
- Explicitly enabling the versioned attribution configuration and confirming its
  disclosed deployment preview is the operator's consent to widen delegated
  handling to individual-level usage and cost views; no additional role or
  permission consent is required.
- Department labels are non-secret organizational labels. Raw user and group
  identifiers remain protected even when their department aggregate is visible.
- Cost attribution appears only where a valid cost model and allocatable cost
  already exist. This feature does not calculate new billed totals or alter cost
  allocation.
- Telemetry sources may emit different identity attributes. Only documented
  aliases that represent an authenticated, stable identity are eligible. Exact
  source identity or explicit pseudonymous operator mapping is required to
  associate records; the system does not guess that different values represent
  the same person.
- The existing authenticated tenant boundary, observation scope, delegated
  credential path, coverage vocabulary, and read-only runtime remain
  authoritative as described in FR-041. Operators who configure the hosted
  Cockpit or its azd environment are the owners of the privacy-sensitive mapping
  setting; no new configuration role is introduced.
- This feature is independent of issue #441 and issue #442. It composes with the
  cost allocation from issue #443 when cost data is present.
