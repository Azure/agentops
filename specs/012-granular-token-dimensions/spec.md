# Feature Specification: Granular Token Dimensions in the Models View

**Feature Branch**: `012-granular-token-dimensions`

**Created**: 2026-08-23

**Status**: Draft

**Input**: User description: "https://github.com/Azure/agentops/issues/442"

## Clarifications

### Session 2026-08-23

- Q: Which normalized token classes are in scope for this feature? → A: Cache-read,
  cache-write, and reasoning, alongside the existing input and output totals.
  Cache-write is emitted far less widely than cache-read, so a partial coverage state
  is expected to be the common case rather than the exception.
- Q: How should long-context consumption be represented? → A: Out of scope as both a
  token class and a request-level classification. Providers meter long context as a
  rate tier that re-prices tokens already counted in the input total, not as an
  additional quantity of tokens, so a token class would double count. No widely
  adopted telemetry attribute reports it, and classifying requests by a
  context-length threshold would require this feature to own a rate-tier boundary,
  which is explicitly out of scope. Should a source ever report it directly, the
  unnormalized passthrough surfaces it without a vocabulary change.
- Q: How should vendor-specific classes outside the vocabulary be handled? → A:
  Retained unnormalized alongside the normalized set, keyed by the source attribute
  name as observed, so nothing reported is discarded and a newly emitted class is
  visible before it is mapped.

### Session 2026-08-24

- Q: Which observed telemetry attributes are eligible for the unnormalized
  passthrough? → A: Only attributes belonging to the same attribute group that
  already carries the input and output token counts, and only when the reported
  value is a non-negative number. This keeps a genuinely new token class from any
  vendor captured automatically while preventing unrelated record data from
  reaching an operator-facing payload.
- Q: Is there a bound on how many unnormalized attributes a single record may
  retain? → A: A fixed cap of five per record. When more are observed, the record
  retains the first five by source attribute name in a deterministic order and
  signals that retention was truncated, so a truncated attribute is never mistaken
  for an unreported one.
- Q: Where does an operator see that token-usage coverage is partial? → A: On the
  affected row in the models view, in addition to the existing coverage entry, so
  the explanation appears where the missing value is observed rather than only in a
  separate panel.
- Q: How are the two model families that verify the mapping selected? → A: By rule
  rather than by name: two families from distinct vendors that report at least one
  shared normalized class under different source attribute names. A rule survives
  vendor changes and guarantees the verification exercises real naming divergence.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Compute a Provisioned Throughput Burn-Down (Priority: P1)

An operator running a model deployment on a purchased throughput commitment opens
the Cockpit models view to understand how fast that commitment is being consumed.
Today the view shows two token totals, but the commitment is consumed by several
token classes that are billed at materially different rates and are invisible in
those two numbers. The operator needs each reported token class broken out
separately for the deployments they own, so they can reason about burn-down rate
instead of guessing.

**Why this priority**: This is the blocking operator need in the issue. Without a
per-class breakdown, a throughput customer cannot answer the single question the
models view exists to answer, and no amount of additional aggregation or filtering
helps. It is also the smallest slice that delivers standalone value: even with no
coverage signalling and no mapping documentation, a correct per-class breakdown for
deployments that report those classes is immediately usable.

**Independent Test**: With a telemetry source containing inference activity from a
model family that reports cached-input and reasoning token classes, open the models
view and confirm each reported class is displayed as its own value on the row for
that deployment, distinct from the existing input and output totals, and that the
displayed values match the source-reported sums for the selected window.

**Acceptance Scenarios**:

1. **Given** a deployment whose telemetry reports a cached-input token class,
   **When** the operator opens the models view for a window containing that
   activity, **Then** the row for that deployment shows the cached-input total as a
   separate value alongside the existing input and output totals.
2. **Given** a deployment whose telemetry reports a reasoning token class,
   **When** the operator opens the models view, **Then** the row shows the
   reasoning total as its own value and does not fold it into the output total.
3. **Given** a deployment whose telemetry reports only input and output totals,
   **When** the operator opens the models view, **Then** the row renders exactly as
   it does today for those two totals, each unreported class is shown as
   missing rather than as zero, and the row indicates that its coverage is partial.
4. **Given** a deployment that reports a total alongside only some of its component
   classes, **When** the operator opens the models view, **Then** the unreported
   classes remain missing and are never back-calculated from the total.
5. **Given** any row in the models view, **When** any token value is rendered,
   **Then** it carries the existing observed-usage labeling and no value anywhere in
   the row is presented as a cost, price, rate, or billing record.

---

### User Story 2 - Know Which Token Classes Are Missing (Priority: P2)

An operator sees an incomplete token breakdown and needs to know whether the
missing classes mean "this model does not use them", "this workload is not
instrumented for them", or "the Cockpit failed to read them". Today a missing class
is indistinguishable from a class that was never emitted, so the operator cannot
tell whether to fix instrumentation or accept the gap. The operator needs the
existing token-usage coverage signal to say that coverage is partial and to name
what is missing and what to do about it.

**Why this priority**: This turns an ambiguous blank into an actionable diagnosis,
but the breakdown in User Story 1 is still useful without it. It is independently
testable because coverage classification is observable on its own, separately from
how the models view renders values.

**Independent Test**: With a telemetry source whose rows report only input and
output totals, request the models view and confirm the token-usage coverage entry
reports a partial state, gives a reason that identifies the reported subset, and
gives a next action naming the specific classes that were not reported, and that
each affected row in the models view itself indicates the partial coverage.

**Acceptance Scenarios**:

1. **Given** a source whose rows report every normalized token class, **When**
   coverage is classified for that source, **Then** the token-usage dimension
   reports the fully available state.
2. **Given** a source whose rows report some but not all normalized token classes,
   **When** coverage is classified, **Then** the token-usage dimension reports the
   partial state with a reason and a next action that name the classes that were
   not reported.
3. **Given** a source whose rows report no token class at all, **When** coverage is
   classified, **Then** the token-usage dimension keeps its existing not-reported
   state rather than reporting partial.
4. **Given** a source that returned no rows for the selected window, **When**
   coverage is classified, **Then** the token-usage dimension keeps its existing
   no-data state rather than reporting partial.
5. **Given** a source whose telemetry query failed, **When** coverage is
   classified, **Then** the token-usage dimension keeps its existing error state
   and the failure reason remains non-leaky and actionable.
6. **Given** a source reporting only a subset of the normalized classes, **When**
   the operator views the models table, **Then** each affected row indicates that
   its token-usage coverage is partial without requiring the operator to open the
   coverage panel first.

---

### User Story 3 - Trust the Normalization Across Model Families (Priority: P3)

A platform engineer supporting several model families, including non-Microsoft
families available through the platform, needs confidence that the same displayed
class means the same thing regardless of which family or instrumentation library
produced the telemetry. Different families and libraries name these attributes
differently, and today any such mapping would be implicit in query text. The
engineer needs the mapping from source attribute to displayed class to be
explicitly defined and verifiable, so that a new family can be onboarded without
guesswork and a mis-mapping is caught before it reaches an operator.

**Why this priority**: This protects the correctness of the first two stories over
time, but the first two stories deliver value the moment a single family is mapped
correctly. It is independently testable because the mapping can be exercised
directly against representative attribute sets without rendering a view.

**Independent Test**: Take representative source attribute sets from at least two
distinct model families that name the same token class differently, run each
through the mapping, and confirm both produce the same normalized class with the
correct value, and that an unrecognized attribute name does not silently populate a
normalized class but is still retained unnormalized under its source attribute name.

**Acceptance Scenarios**:

1. **Given** two distinct model families that report the same token class under
   different source attribute names, **When** each is normalized, **Then** both
   populate the same normalized class with the source-reported value.
2. **Given** a source that emits both a legacy and a current attribute name for the
   same token class on the same record, **When** it is normalized, **Then** the
   class is counted once and is not double counted.
3. **Given** an eligible source attribute that does not correspond to any normalized
   class, **When** it is normalized, **Then** no normalized class is populated from
   it, the record does not fail, and the value is retained unnormalized under its
   source attribute name.
4. **Given** the defined mapping, **When** it is reviewed, **Then** every normalized
   class lists the source attribute names it accepts, and that list is the single
   place the mapping is defined rather than being restated in query text.
5. **Given** a record carrying an unrecognized token attribute alongside a complete
   set of normalized classes, **When** coverage is classified, **Then** the
   token-usage dimension still reports the fully available state and the unnormalized
   value is not counted as a missing class.
6. **Given** a record carrying an attribute outside the token-count attribute group,
   or one whose value is not a non-negative number, **When** it is normalized,
   **Then** it is not retained in the unnormalized passthrough and does not reach the
   models view.
7. **Given** a record carrying more than five eligible unrecognized attributes,
   **When** it is normalized, **Then** exactly five are retained deterministically
   and the record indicates that retention was truncated.

---

### Edge Cases

- A source reports a grand total but omits one or more component classes. The
  omitted classes MUST remain missing; deriving them by subtracting the reported
  components from the total is prohibited.
- A source reports a component class whose value exceeds the total it belongs to.
  The values MUST be surfaced as reported without correction, clamping, or
  suppression, because they are observed signals rather than reconciled accounting.
- A source reports a token class with an explicit value of zero. Zero MUST render
  as a genuine zero and MUST NOT be treated as missing.
- A source emits both a deprecated and a current attribute name for the same class
  on the same record. The class MUST be counted once.
- Aggregation spans records where some report a class and others do not. The
  aggregate MUST reflect only the records that reported it, and the coverage signal
  MUST reflect that the class was only partially reported.
- A source emits a token attribute the mapping has never seen. It MUST be retained
  unnormalized under its source attribute name rather than dropped, and MUST NOT be
  guessed into a normalized class.
- A source emits an attribute that carries a non-numeric value, a negative value, or
  that sits outside the attribute group carrying the existing token counts. It MUST
  NOT be retained in the unnormalized passthrough.
- A source emits more than five eligible unnormalized token attributes on one
  record. Five MUST be retained deterministically and the record MUST indicate that
  retention was truncated, so the omission is not read as an unreported attribute.
- A deployment runs prompts large enough to cross a provider's long-context rate
  tier. The observed input total MUST be reported as-is and MUST NOT be split,
  re-tiered, or reclassified, because the tier re-prices tokens that are already
  counted rather than adding new ones.
- A Copilot Studio agent appears within the selected scope. It meters in credits
  rather than tokens, so it MUST NOT be forced into a token class and MUST NOT
  cause the token-usage coverage signal to degrade for token-reporting sources.
- A row's token classes are entirely unreported. The row MUST still render its
  request, failure, latency, and last-observed values without degradation.
- The models view is filtered to a window in which a deployment had activity but no
  inference spans carrying token attributes. Token classes MUST be missing rather
  than zero.

## Requirements *(mandatory)*

### Inherited Constraints

These requirements are already in force from the Read-Only Cockpit and Deploy
Hosted Cockpit specifications and are restated here because this feature is bound
by them. They are not restated as new numbered requirements.

- Model usage MUST continue to aggregate available activity by project, agent,
  deployment, and model, including requests, failures, p95 latency, and last
  observed activity.
- Token counts MUST be labeled as observed usage and MUST NOT be represented as
  billing records or estimated cost.
- The Cockpit runtime MUST remain a read-only projection and MUST NOT mutate any
  monitored cloud resource.

### Functional Requirements

- **FR-001**: Model usage records MUST carry a normalized set of token classes in
  addition to the existing input and output totals.
- **FR-002**: The normalized token class vocabulary MUST consist of exactly three
  classes: tokens served from a cache instead of being reprocessed, tokens written
  into a cache for later reuse, and tokens consumed by internal reasoning that are
  not part of the returned response.
- **FR-003**: Long-context consumption MUST NOT be represented as a normalized token
  class and MUST NOT be derived by comparing an observed token total against a
  context-length threshold, because it is a rate tier applied to tokens already
  counted in the input total rather than an additional quantity of tokens. If a
  source ever reports it directly, it MUST surface through the unnormalized
  passthrough required by FR-004 without a change to the normalized vocabulary.
- **FR-004**: A source token attribute that falls outside the normalized vocabulary
  MUST be retained unnormalized alongside the normalized set, keyed by the source
  attribute name exactly as observed, so that no reported token value is discarded.
  An attribute is eligible for retention only when it belongs to the same attribute
  group that already carries the input and output token counts and its reported
  value is a non-negative number; any other observed attribute MUST NOT be retained.
  Unnormalized values MUST NOT be summed into any normalized class and MUST NOT
  affect the token-usage coverage state.
- **FR-005**: Every normalized token class MUST be optional and MUST be absent when
  the source telemetry does not report it.
- **FR-006**: A normalized token class MUST NOT be derived, inferred, or
  back-calculated from any other value, including by subtracting reported
  components from a reported total.
- **FR-007**: A reported value of zero MUST be preserved as zero and MUST be
  distinguishable from an unreported class.
- **FR-008**: The mapping from source telemetry attribute name to normalized token
  class MUST be defined explicitly in a single place, MUST be inspectable
  independently of the telemetry query text, and MUST be exercisable in isolation.
- **FR-009**: The mapping MUST accept multiple source attribute names for the same
  normalized class, declared in an explicit, fixed order. When more than one accepted name
  is present on the same record, the first name present in that declared order MUST supply
  the class value and every remaining accepted name for that class MUST be discarded. The
  values MUST NOT be added together, because during an instrumentation migration a single
  record commonly carries the same count under two spellings, and adding them would report
  double the observed value.
- **FR-010**: A source attribute that the mapping does not recognize MUST NOT
  populate any normalized class and MUST NOT cause the record to be rejected; when it
  satisfies the eligibility rule and retention bound in FR-004 and FR-021 it MUST
  instead be retained unnormalized rather than dropped.
- **FR-011**: The existing token-usage coverage dimension MUST report a partial
  state when telemetry rows report at least one but not all normalized token
  classes.
- **FR-012**: A partial token-usage coverage result MUST provide a reason
  identifying which classes were reported and a next action naming the classes that
  were not.
- **FR-013**: The token-usage coverage dimension MUST retain its existing states
  when no rows are returned, when no token class is reported at all, when the source
  is inaccessible or unconfigured, and when the query fails.
- **FR-014**: The models view MUST render each reported normalized token class as a
  distinct labeled value on the corresponding row.
- **FR-015**: The models view MUST render a row whose telemetry reports only input
  and output totals without visual or behavioral regression relative to current
  behavior, apart from the partial-coverage indication required by FR-022.
- **FR-016**: An unreported token class MUST render using the existing
  missing-value treatment and MUST NOT render as zero, as an empty value, or as a
  broken cell.
- **FR-017**: Token classes, aggregates, and display values introduced by this
  feature MUST NOT be labeled or described as a cost, price, rate, spend, charge, or
  billing record anywhere in the response payload or the rendered view.
- **FR-018**: The normalized token classes MUST apply to every agent runtime that
  reports token attributes on inference spans, covering Foundry hosted, Foundry
  prompt, external registered, and external unregistered agents, and MUST cover
  non-Microsoft model families available through the platform.
- **FR-019**: Copilot Studio consumption MUST remain outside this feature, MUST NOT
  be represented through token classes, and MUST NOT degrade the token-usage
  coverage signal for token-reporting sources in the same scope.
- **FR-020**: The mapping MUST be verified against representative source attribute
  sets from two model families selected by rule rather than by name: the families
  MUST come from distinct vendors and MUST report at least one shared normalized
  class under different source attribute names.
- **FR-021**: A model usage record MUST retain at most five unnormalized token
  attributes. When more eligible attributes are observed, the record MUST retain
  five selected deterministically by source attribute name and MUST indicate that
  retention was truncated, so a truncated attribute is distinguishable from one that
  was never reported. Truncation MUST NOT affect any normalized class or the
  token-usage coverage state.
- **FR-022**: The models view MUST indicate on each affected row that the
  token-usage coverage for that row's source is partial, in addition to the existing
  coverage entry, so the operator sees why a value is missing on the row where it is
  missing.

### Out of Scope

- Any monetary value, rate table, price lookup, or per-token pricing.
- Classifying, tiering, or thresholding requests by context length, including any
  long-context rate-tier boundary.
- Copilot Studio credit accounting, which is addressed separately.
- Synthesizing any token class the source does not report.
- Changing the agents view or the combined usage view token rendering. Those views
  MUST continue to work unchanged.
- Historical backfill or re-normalization of previously observed telemetry.

### Key Entities

- **Model usage record**: One aggregated row of observed inference activity for a
  project, agent, deployment, and model over the selected window. Already carries
  requests, failures, p95 latency, input tokens, output tokens, and last observed
  activity. Gains a set of optional normalized token classes.
- **Normalized token class**: A named, provider-agnostic category of token
  consumption with an optional non-negative observed total. Present only when the
  source reported it.
- **Token attribute mapping**: The explicit, single-source association between one
  normalized token class and the set of source telemetry attribute names that are
  accepted as reporting it. Includes deprecated aliases so a class is counted once.
- **Unnormalized token passthrough**: The set of observed token attributes on a model
  usage record that the mapping does not recognize, retained under their source
  attribute names with their reported values. Eligible only when the attribute
  belongs to the same attribute group as the existing token counts and reports a
  non-negative number, and bounded to five per record with a truncation indication.
  Exists so no observed token value is discarded and so a newly emitted class is
  visible before it is mapped. Not part of the normalized vocabulary and not counted
  toward coverage.
- **Token usage coverage result**: The existing per-source coverage entry for the
  token-usage dimension, which gains the ability to express a partial state with a
  reason and a next action describing which classes are missing.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator viewing a deployment whose telemetry reports granular
  token classes can identify the consumption of each reported class from the models
  view alone, without exporting data or running a separate query.
- **SC-002**: For a deployment reporting granular classes, every displayed class
  total matches the source-reported sum for the selected window exactly, with no
  derived, estimated, or reconciled values.
- **SC-003**: 100% of unreported token classes render as missing rather than as
  zero, across every model family in the selected scope.
- **SC-004**: A deployment that reports only input and output totals renders in the
  models view with the same information and layout it has today, with zero
  regressions in existing behavior, plus the partial-coverage indication for the
  classes it does not report.
- **SC-005**: When only a subset of token classes is reported, an operator can
  determine which specific classes are missing and what to do about it from the
  coverage signal alone, without inspecting raw telemetry.
- **SC-006**: The source-attribute-to-class mapping is verified for two model
  families from distinct vendors that report at least one shared class under
  different attribute names, and both resolve to the same class.
- **SC-007**: A review of the models view and its response payload finds zero
  occurrences of cost, price, rate, spend, charge, or billing wording applied to any
  token value.
- **SC-008**: A record carrying both a deprecated and a current attribute name for
  the same class contributes that class exactly once, never twice.
- **SC-009**: An eligible token attribute the mapping does not recognize remains
  visible to the operator under its source attribute name, with zero eligible token
  values discarded during normalization up to the five-attribute retention bound.
- **SC-010**: A record observing more than five eligible unnormalized attributes
  retains exactly five and is identifiable as truncated, so no omitted attribute is
  reported as unobserved.
- **SC-011**: For a row whose source reports only a subset of the normalized
  classes, an operator can tell from that row alone that its coverage is partial,
  without first opening the coverage panel.

## Assumptions

- The models view already aggregates by project, agent, deployment, and model, and
  this feature extends that existing row shape rather than introducing a new view or
  a new aggregation grain.
- Granular token classes arrive as additional attributes on the same inference
  spans that already carry the existing input and output token attributes, so no
  new telemetry source, ingestion path, or emitter change is required on the
  AgentOps side.
- Telemetry sources vary in which classes they emit, and that variation is expected
  and permanent rather than a defect to be corrected by the Cockpit.
- The existing missing-value and observed-usage presentation treatments are reused
  for the new classes rather than replaced, so the disclaimer and the missing-value
  rendering stay consistent across every token value in the view.
- The existing coverage classification already distinguishes no-data, not-reported,
  and error states, and this feature adds only the partial case to the token-usage
  dimension rather than redefining the others.
- Query result size limits, time-window bounds, and read-only access constraints
  already in force for the models view continue to apply unchanged.
- Non-Microsoft model families reachable through the platform emit token attributes
  on the same spans through the same instrumentation path, differing only in
  attribute naming.
- Long-context billing across model families is a rate tier applied to tokens already
  counted in the input total rather than an additional quantity of tokens, and no
  widely adopted telemetry attribute reports it, so representing it as a token class
  would double count and classifying it by threshold would require owning a rate-tier
  boundary this feature is not permitted to define.
- Cache-write reporting is materially less common than cache-read reporting across
  instrumentation libraries, so the partial coverage state introduced by this feature
  is expected to be the steady-state outcome for many sources rather than a defect.
- The attribute group that already carries the existing input and output token counts
  is the only group in which additional token-count attributes are emitted, so
  scoping passthrough eligibility to that group captures new classes without
  admitting unrelated record data.
- Sources emit few unrecognized token attributes per record in practice, so a
  five-attribute retention bound is expected to be reached rarely and exists to keep
  row and payload size predictable rather than to filter meaningful signal.
