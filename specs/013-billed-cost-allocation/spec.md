# Feature Specification: Allocate Billed Cost to Agents, Tools, and Runs

**Feature Branch**: `placerda-cockpit-cost-allocation`

**Created**: 2026-08-24

**Status**: Draft

**Input**: GitHub issue [Azure/agentops#443](https://github.com/Azure/agentops/issues/443) — "feat(cockpit): allocate billed cost to agents, tools and runs"

## User Scenarios & Testing *(mandatory)*

The Cockpit can show observed usage by agent, model, tool, and run, but it
cannot answer how the money already billed for a period should be attributed
to those consumers. Runtime estimates are incomplete, tool-side resources are
billed separately, and commitments such as provisioned throughput or prepaid
credit pools are paid before individual requests consume them.

This feature lets an operator provide non-secret billed totals for a defined
period and allocate each total by the observed usage key that best represents
consumption. The resulting figures are allocations for operational
accountability, not invoice records or forecasts.

### User Story 1 - Explain Billed Spend by Agent (Priority: P1)

An operator responsible for a portfolio of agents selects a configured cost
period and opens the Cost view. They can see how each declared billed total was
distributed across the agents that consumed it, together with the observed
usage share that drove the distribution. Provisioned throughput, standard
model usage, tool-side services, compute, and credit-based consumption can all
be represented without pretending that they share one meter.

**Why this priority**: The primary customer question is "which agents consumed
the money we already spent?" Agent allocation provides a useful first release
even before an operator drills into tools or individual runs.

**Independent Test**: Configure one cost period with billed totals for at least
two cost components and telemetry for two agents. Open the agent cost breakdown
and confirm each component is allocated according to its declared key, each
agent row shows the observed usage that produced its share, and the component
allocations reconcile exactly to the declared total.

**Acceptance Scenarios**:

1. **Given** a declared provisioned-throughput commitment and two agents with
   observed token usage in the same period, **When** the operator opens the
   agent cost breakdown, **Then** the commitment is distributed by each
   agent's token share and the allocations sum exactly to the declared
   commitment.
2. **Given** declared tool-side spend and two agents with observed tool
   invocations, **When** the operator opens the agent cost breakdown, **Then**
   the spend is distributed by invocation share and each allocation identifies
   the tool-side component it came from.
3. **Given** a declared compute total and observed active-session duration,
   **When** the operator opens the agent cost breakdown, **Then** the compute
   total is distributed by duration share and the duration is shown beside the
   allocation as observed usage.
4. **Given** a credit-based runtime and a declared prepaid credit pool,
   **When** credit-consuming events are reported, **Then** the pool is
   distributed by observed credit consumption without converting credits into
   tokens.
5. **Given** two configured cost components use different currencies, **When**
   the operator views their allocations, **Then** the components remain
   separate and no cross-currency total is presented.

---

### User Story 2 - Find the Tools and Runs Driving Cost (Priority: P2)

An operator investigating one expensive agent drills into tool and run
allocations. The tool breakdown shows which tools consumed tool-side billed
totals. The run breakdown combines the model, tool, compute, and credit
allocations attributable to each run, so the operator can compare expensive
runs with their observed tokens, tool invocations, duration, and credit events.

**Why this priority**: Agent totals identify where to investigate, while tool
and run allocations explain why. These views depend on the tool invocation and
run duration foundations from issue #441 but deliver a separately testable
diagnostic workflow.

**Independent Test**: Use telemetry containing two tools and two correlated
runs for one agent. Confirm tool-side spend is allocated by invocation count,
run-attributable components are allocated by their respective run usage keys,
and the same billed component is presented as alternative breakdowns rather
than being added twice.

**Acceptance Scenarios**:

1. **Given** a tool-side billed total and two tools with different invocation
   counts, **When** the operator opens the tool breakdown, **Then** each tool
   receives its proportional share and the two shares sum exactly to the
   declared total.
2. **Given** a run with observed model tokens, tool invocations, and active
   duration, **When** the operator opens the run breakdown, **Then** the run
   shows each attributable cost component separately and shows the matching
   observed usage beside it.
3. **Given** a billed component appears in the agent, tool, and run breakdowns,
   **When** the operator moves between them, **Then** the interface states that
   these are alternative allocations of the same billed pool and never sums
   them across breakdowns.
4. **Given** activity cannot be correlated to a run but can be attributed to an
   agent, **When** the operator opens the run breakdown, **Then** the amount is
   represented as unattributed-to-run rather than assigned to a fabricated run
   or dropped.
5. **Given** a tool invocation has no usable tool identity, **When** tool-side
   spend is allocated, **Then** its share remains in an unattributed-tool
   bucket and the coverage explanation identifies the missing attribution.

---

### User Story 3 - Trust How Every Amount Was Produced (Priority: P3)

An operator reviewing an allocated amount can tell the billed source, cost
period, currency, allocation model, allocation key, observed numerator and
denominator, and confidence without consulting configuration or raw telemetry.
Metered spend and commitment allocation are visibly different, and neither is
presented as an invoice or a billing-accurate value.

**Why this priority**: Cost data without provenance is likely to be mistaken
for an invoice or an exact per-request charge. Provenance and confidence make
the allocation auditable and preserve the Cockpit's evidence boundaries.

**Independent Test**: Inspect every cost figure returned for a mixed period
containing metered and commitment components. Confirm each amount carries all
required provenance, that the two methods remain distinguishable in both the
data and presentation, and that incomplete coverage lowers confidence instead
of being hidden.

**Acceptance Scenarios**:

1. **Given** any allocated amount, **When** an operator inspects it, **Then**
   they can identify its declared billed source, period, currency, method,
   allocation key, observed share, and confidence.
2. **Given** one metered component and one commitment component, **When** both
   appear in a breakdown, **Then** they have distinct method labels and cannot
   be mistaken for the same type of figure.
3. **Given** a dimension-weighted model allocation has all required token
   dimensions, **When** it is calculated, **Then** it uses the declared
   dimension weights and reports high confidence.
4. **Given** dimension-weighted allocation is requested but only aggregate
   token totals are available, **When** the configured fallback permits uniform
   token allocation, **Then** the fallback is used, identified explicitly, and
   reported with lower confidence.
5. **Given** observed usage coverage is partial, **When** an allocation is
   shown, **Then** the coverage gap and reduced confidence appear with the
   amount rather than only in a separate coverage view.

---

### User Story 4 - Configure Allocation Without Expanding Privilege (Priority: P4)

An operator supplies a versioned, non-secret cost model that declares periods,
billed totals, currencies, billing boundaries, methods, keys, and permitted
fallbacks. The running Cockpit validates this configuration but does not store
it, query a billing system, or receive credentials. Removing the configuration
removes the Cost view and returns Observe to its current behavior.

**Why this priority**: Operator-supplied totals are necessary because billed
money is not present in telemetry. Reusing the existing stateless
configuration convention delivers the outcome without adding a persistent
database or widening the read-only permission surface.

**Independent Test**: Start the Cockpit with a valid cost model, an invalid cost
model, and no cost model. Confirm valid configuration enables allocation,
invalid configuration produces no cost figures and an actionable validation
error, and absent configuration leaves every existing Observe view unchanged.

**Acceptance Scenarios**:

1. **Given** a valid versioned cost model, **When** it is loaded, **Then** the
   configured periods and components become available for allocation without
   the runtime persisting the model.
2. **Given** an invalid cost model, **When** it is loaded, **Then** cost
   allocation fails closed, no cost figure is returned, and the operator
   receives an actionable message identifying the invalid field.
3. **Given** no cost model is configured, **When** the Cockpit runs, **Then**
   all existing Observe behavior remains available and unchanged, and no Cost
   view is implied to contain zero spend.
4. **Given** the deployment uses its existing read-only roles, **When** cost
   allocation is enabled, **Then** no additional cloud permission is required
   and no billing service is queried.
5. **Given** a cost model containing a credential-bearing field, **When**
   validation runs, **Then** the unsupported field is rejected as outside the
   non-secret contract.

---

### User Story 5 - Understand Missing or Unallocatable Cost (Priority: P5)

An operator sees every declared component even when it cannot be allocated.
The Cost view and telemetry coverage explain whether the billed total is
missing, the allocation key was not reported, the source was inaccessible, no
usage occurred in the period, or attribution was partial. Missing data is never
silently converted to zero.

**Why this priority**: Explicit coverage protects trust in the primary
allocation workflow, but allocation for fully covered components remains
valuable without every gap state.

**Independent Test**: Exercise one component for each missing-data state and
confirm the component remains visible with a reason, next action, and no
fabricated zero or allocation.

**Acceptance Scenarios**:

1. **Given** a component has no declared billed total, **When** the operator
   opens cost coverage, **Then** the component is reported as not configured
   with a next action and is never shown as zero.
2. **Given** a declared total but no observed allocation key in the period,
   **When** allocation runs, **Then** the full total remains unallocated with an
   explanation rather than being divided evenly or dropped.
3. **Given** some observed usage lacks agent, tool, or run attribution, **When**
   allocation runs, **Then** its proportional share is preserved in an explicit
   unattributed bucket at that breakdown level.
4. **Given** only part of the configured period is readable, **When** an
   allocation is shown, **Then** it is marked low confidence and the unread
   portion is identified through coverage.

---

### Edge Cases

- A declared billed total is explicitly zero: zero is preserved as a genuine
  configured value and remains distinguishable from a missing total.
- A declared billed total or usage weight is negative, non-numeric, or
  non-finite: the cost model is invalid and allocation fails closed.
- Two configured cost periods overlap for the same component and billing
  boundary: the model is invalid because a billed amount could be counted
  twice.
- A configured period has a billed total but its allocation-key denominator is
  zero or unavailable: the total remains fully unallocated and no equal-share
  fallback is invented.
- One consumer reports usage and another consumer's telemetry is inaccessible:
  the observed consumer may receive an allocation only with low confidence,
  and coverage states that the denominator is incomplete.
- Usage is observed without an agent, tool, or run identity: its share is
  retained in an explicit unattributed bucket for the affected breakdown.
- A run crosses the configured cost-period boundary: only usage observed inside
  the period participates, and the run allocation states that it is
  period-scoped rather than lifetime cost.
- Existing Observe time, source, project, model, tool, and run filters are
  present in the page URL when the operator enters Cost: the Cost view ignores
  them and calculates from the complete configured period and authorized scope.
  An agent drill-down uses the dedicated `cost_agent_key` selector only after
  allocation, so it cannot change any component denominator or amount.
- A component uses granular token weights but only some token classes are
  reported: the configured fallback is applied only when explicitly permitted;
  otherwise the amount remains unallocated.
- A component's declared currency differs from other components: amounts remain
  separately grouped and are never converted or summed.
- Multiple billing boundaries exist in one Observe scope: each component
  preserves its boundary and reconciles independently; no total spans
  boundaries unless they share a currency and the operator explicitly views a
  grouped subtotal.
- The same billed pool is visible by agent, tool, and run: these are alternate
  breakdowns and are not additive across views.
- The selected period contains no telemetry activity: every declared total
  remains visible as unallocated with a no-data coverage state.
- A prepaid credit pool reports credit-consuming events but not their credit
  quantities: event-count allocation is used only when that key is explicitly
  declared; credits are never inferred from tokens or message count otherwise.
- Telemetry arrives after a period was first viewed: recalculation uses the
  latest readable observations and identifies its refresh time; prior
  allocations are not treated as immutable accounting records.

## Requirements *(mandatory)*

### Inherited Constraints

These constraints are already in force from the Read-Only Cockpit, hosted
Cockpit, tools and runs, and granular token specifications. They are restated
here because every cost result depends on them.

- The running Cockpit remains a read-only projection and does not create,
  modify, or delete monitored cloud resources.
- Observed token counts, tool invocations, credit events, and durations remain
  labeled as observed usage wherever they appear.
- Missing telemetry dimensions remain missing and are not synthesized.
- Tool and run results preserve their originating source, project, and agent.
- Existing Observe behavior remains available when optional cost configuration
  is absent.

### Functional Requirements

#### Cost model contract

- **FR-001**: The system MUST accept an optional, versioned cost model that
  declares one or more cost periods and the billed components available for
  allocation in each period.
- **FR-002**: Each cost period MUST declare an inclusive start, exclusive end,
  and one or more cost components whose usage is evaluated over exactly the
  same interval. That interval MUST be authoritative for cost calculation and
  MUST NOT be narrowed by the shared Observe start or end filters.
- **FR-003**: Each cost component MUST declare a stable component identity,
  component type, billing boundary, billed source description, non-negative
  billed total, currency, allocation model, allocation key, and any explicitly
  permitted fallback.
- **FR-004**: The contract MUST distinguish metered components from commitment
  components. A metered component represents spend accrued during the period;
  a commitment component represents money paid or committed independently of
  individual requests.
- **FR-005**: The cost model MUST be non-secret, MUST NOT accept credentials,
  access tokens, connection secrets, or credential references, and MUST be
  usable without a persistent application database.
- **FR-006**: Invalid cost configuration MUST fail closed for cost allocation:
  no cost result may be produced, and validation MUST identify the invalid
  field and corrective action without echoing sensitive input.
- **FR-007**: Cost periods for the same component identity and billing boundary
  MUST NOT overlap.
- **FR-008**: An explicitly declared zero billed total MUST be preserved as
  zero and MUST be distinguishable from a component whose total is absent.
- **FR-009**: Removing or omitting the cost model MUST return the Cockpit to its
  existing behavior with no cost results, no cost-specific error, and no change
  to any non-cost Observe view.
- **FR-010**: The running Cockpit MUST NOT persist cost-model content or
  calculated allocations beyond the existing bounded result reuse behavior.

#### Allocation models and keys

- **FR-011**: Every declared billed total MUST be allocated independently
  within its cost period, component identity, billing boundary, and currency.
- **FR-012**: Provisioned-throughput commitments MUST allocate by observed token
  share per consumer.
- **FR-013**: Standard model-deployment spend MUST allocate by observed token
  share and MUST use declared token-dimension weights when the required
  dimensions are reported.
- **FR-014**: When weighted token allocation cannot be completed, a uniform
  total-token fallback MUST be used only when the component explicitly permits
  it; otherwise the amount MUST remain unallocated.
- **FR-015**: Search, grounding, content-safety, and storage spend MUST allocate
  by observed tool invocation count for the corresponding component.
- **FR-016**: Hosted-agent container compute and operator-declared
  customer-owned compute MUST allocate by observed active-session duration in
  seconds.
- **FR-017**: Pay-as-you-go credit-based spend and prepaid credit pools MUST
  allocate by observed credit quantity when reported, or by credit-consuming
  event count only when event-count allocation is explicitly declared.
- **FR-018**: Credits MUST NOT be converted to tokens, and token, invocation,
  duration, and credit keys MUST NOT be substituted for one another unless an
  explicit fallback in FR-014 or FR-017 permits it.
- **FR-019**: A component with no positive observed denominator for its declared
  key MUST remain fully unallocated and MUST NOT be distributed equally,
  assigned to a default consumer, or omitted.
- **FR-020**: Within one component breakdown, the sum of all attributed,
  explicitly unattributed, and unallocated amounts MUST equal the declared
  billed total exactly, subject only to a visible rounding remainder assigned
  deterministically.
- **FR-021**: The rounding policy MUST preserve the declared total, MUST be
  deterministic for the same inputs, and MUST expose the applied currency
  precision.
- **FR-022**: Allocations MUST be recalculated from the currently readable
  observed usage for the configured period and MUST report the usage refresh
  time; they MUST NOT be represented as immutable accounting records.

#### Agent, tool, and run breakdowns

- **FR-023**: The Cost view MUST provide agent, tool, and run breakdowns for a
  configured cost period. Cost calculation MUST use only cost-period,
  cost-component, cost-breakdown, and optional `cost_agent_key` selectors;
  other Observe filters MUST NOT alter the allocation denominator.
- **FR-024**: The agent breakdown MUST attribute every allocatable component to
  observed agents using the component's declared key and MUST preserve an
  unattributed-agent bucket when usage has no usable agent identity.
- **FR-025**: The tool breakdown MUST attribute tool-side components to observed
  tools by invocation share and MUST preserve an unattributed-tool bucket when
  usage has no usable tool identity.
- **FR-026**: The run breakdown MUST attribute model, tool, compute, and credit
  components to correlated runs using each component's declared key and MUST
  preserve an unattributed-run bucket when activity cannot be correlated.
- **FR-027**: Every breakdown row MUST show the observed usage numerator and
  denominator that produced its allocation, using the unit declared for the
  component.
- **FR-028**: Agent, tool, and run breakdowns of the same billed component MUST
  be presented as alternative views of one pool and MUST NOT be summed across
  breakdown levels. `cost_agent_key` MAY hide rows belonging to other agents
  after allocation, but MUST NOT recalculate amounts; hidden allocated amounts
  MUST remain represented in component reconciliation.
- **FR-029**: A run that crosses a cost-period boundary MUST include only the
  observed usage inside the period and MUST identify its allocation as
  period-scoped.
- **FR-030**: Cost allocation MUST support Foundry hosted, Foundry prompt,
  external registered, external unregistered, and Copilot Studio runtimes to
  the extent that each runtime reports the required allocation key.
- **FR-031**: Customer-owned compute outside the readable scope MUST be
  allocatable only from an operator-declared billed total and readable active
  duration; when either is absent, compute cost MUST remain unallocated.

#### Provenance, confidence, and presentation

- **FR-032**: No allocated figure may be returned or displayed without its cost
  period, currency, declared billed source, allocation model, allocation key,
  observed numerator, observed denominator, and confidence.
- **FR-033**: Metered and commitment allocations MUST use distinct method
  labels in both the cost result and the rendered view.
- **FR-034**: Confidence MUST use four states: high, medium, low, and
  unavailable.
- **FR-035**: Confidence MUST be high only when the billed total is declared,
  the preferred allocation key is fully reported for the period, and every
  observed unit at that breakdown level is attributable.
- **FR-036**: Confidence MUST be medium when coverage is complete but an
  explicitly permitted fallback key is used.
- **FR-037**: Confidence MUST be low when an allocation is calculated from
  partial coverage, incomplete attribution, or an incomplete readable period.
- **FR-038**: Confidence MUST be unavailable when no allocation is produced.
- **FR-039**: Every fallback, partial denominator, and unattributed share MUST be
  visible beside the affected amount and MUST NOT be discoverable only through
  a separate coverage view.
- **FR-040**: Currency amounts MUST preserve their declared currency and MUST
  NOT be converted. Totals across components MUST be shown only within one
  currency.
- **FR-041**: Allocated amounts MUST be labeled as operational cost allocations
  and MUST NOT be represented as invoices, billing-accurate charges, forecasts,
  budgets, reconciled accounting entries, or per-request prices.
- **FR-042**: Observed allocation keys MUST retain the existing observed-usage
  labeling wherever they appear beside allocated cost.
- **FR-043**: Every cost result MUST identify its last calculation time and the
  latest observed activity included in the allocation.

#### Coverage, security, and integrity

- **FR-044**: Telemetry coverage MUST include a distinct cost-attribution
  dimension for each configured cost component and for each observable
  allocation capability present in the selected period but unmatched by any
  configured component. Observable capabilities are limited to reported total
  or granular tokens, tool invocations, active-session duration, direct credits,
  and explicitly selected credit-event operations; the system MUST NOT infer a
  billing component type or allocation model from telemetry.
- **FR-045**: Cost-attribution coverage MUST distinguish at least: billed total
  not configured, source inaccessible, telemetry not configured, no data in the
  period, allocation key not reported, partial allocation-key coverage,
  incomplete attribution, and fully available.
- **FR-046**: Every unavailable or incomplete cost-attribution result MUST
  include a concise reason and a recommended next action.
- **FR-047**: An observable allocation capability with no matching configured
  component and declared total MUST be reported as not configured, MUST identify
  its observed allocation key, and MUST NOT be represented as zero.
- **FR-048**: Observed usage without a usable agent, tool, or run identity MUST
  remain visible through an explicit unattributed bucket at the affected
  breakdown level and MUST NOT be dropped or assigned to a fabricated identity.
- **FR-049**: The Cost view MUST preserve each component's originating billing
  boundary, observed telemetry source, project, agent, tool, and run identities
  when available.
- **FR-050**: Cost allocation MUST NOT require any new cloud role, MUST NOT
  query a billing or cost-management service, and MUST NOT expand the running
  Cockpit's cloud-mutation capability.
- **FR-051**: Normal operator use of the Cost view MUST NOT require query syntax,
  telemetry workspace wiring, or raw resource identifiers.
- **FR-052**: Cost results MUST remain bounded and progressively loaded under
  the existing Observe query limits, and partial source failures MUST not
  prevent fully readable components from being shown.
- **FR-053**: Cost results MUST NOT expose prompt, response, system instruction,
  tool argument, tool result, credential, or other protected content.

### Out of Scope

- Billing-accurate cost, invoice generation, and invoice reconciliation.
- Forecasting, budgets, anomaly alerts, or future-spend estimation.
- Reading billed totals from Cost Management or any other billing service.
- Adding a cost-reader, billing-reader, or other new cloud role.
- Persisting cost configuration or allocation history in an application
  database.
- Currency conversion or exchange-rate management.
- Changing how cloud meters, agents, tools, or telemetry emit usage.
- Deriving absent token classes, credits, tool names, run identities, or
  durations.
- Treating alternate agent, tool, and run breakdowns as additive cost pools.
- Mutating monitored cloud resources from the running Cockpit.

### Dependencies

- The tool and run allocation keys require the Tools and Runs views specified
  by [Azure/agentops#441](https://github.com/Azure/agentops/issues/441).
- Dimension-weighted model allocation benefits from the granular token
  dimensions specified by
  [Azure/agentops#442](https://github.com/Azure/agentops/issues/442). This
  feature remains usable without them through an explicitly configured uniform
  total-token fallback.
- The existing Observe scope, read-only deployment boundary, runtime
  attribution, coverage states, bounded queries, and observed-usage labeling
  remain authoritative.

### Key Entities

- **Cost model**: The optional, versioned, non-secret declaration of cost
  periods, billed components, allocation methods, keys, weights, and permitted
  fallbacks. It is configuration supplied by an operator and is not stored by
  the running application.
- **Cost period**: An inclusive-start, exclusive-end interval over which one or
  more billed totals and their observed allocation keys are evaluated.
- **Cost component**: One independently reconcilable billed pool within a
  billing boundary and currency. It identifies the component type, source of
  truth, billed total, allocation model, allocation key, and fallback policy.
- **Allocation model**: The declared interpretation of a component as metered
  spend accrued during a period or as a commitment paid independently of
  requests.
- **Allocation key**: The observed unit used to distribute a component: token,
  dimension-weighted token, tool invocation, active-session second, credit, or
  explicitly declared credit-consuming event.
- **Cost allocation**: One consumer's proportional share of a cost component.
  It includes amount, currency, period, method, source, observed numerator and
  denominator, confidence, calculation time, and coverage context.
- **Cost consumer**: An observed agent, tool, or run, or an explicit
  unattributed bucket at one of those breakdown levels.
- **Cost attribution coverage result**: A per-component statement of whether
  billed input and observed allocation data are sufficient, why they are not,
  and what the operator should do next.
- **Confidence**: High, medium, low, or unavailable qualification based on
  allocation-key completeness, fallback use, attribution completeness, and
  readable-period coverage.
- **Billing boundary**: The operator-declared resource, subscription, account,
  pool, or other scope to which one billed total belongs. It prevents unrelated
  pools from being reconciled together.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator can identify the three agents receiving the largest
  allocations for a configured period within 60 seconds of opening the Cost
  view, without writing a query or knowing a raw resource identifier.
- **SC-002**: For 100% of allocatable components, the sum of attributed,
  unattributed, and deterministic rounding allocations equals the declared
  billed total exactly at the component's currency precision.
- **SC-003**: For 100% of displayed cost figures, an operator can identify the
  period, currency, billed source, allocation model, allocation key, observed
  share, and confidence from the Cost view alone.
- **SC-004**: For 100% of components with missing or incomplete inputs, the Cost
  view shows an explicit coverage reason and next action; 0% are silently
  omitted or represented as zero.
- **SC-005**: An operator can move from an agent allocation to its tool and run
  breakdowns within two interactions using `cost_agent_key` and can see the
  observed usage driving each amount without opening raw telemetry or changing
  the original component denominator.
- **SC-006**: Metered and commitment allocations are distinguishable in 100% of
  cost results and rendered amounts, with no unlabeled allocation method.
- **SC-007**: 100% of allocations using a fallback or partial denominator are
  identified beside the amount with medium or low confidence as applicable.
- **SC-008**: Removing the cost model yields zero changes to the existing
  Observe views, filters, coverage results, permissions, and startup workflow.
- **SC-009**: Enabling cost allocation adds zero cloud roles and causes zero
  reads from billing or cost-management services.
- **SC-010**: A review of all cost presentation text finds zero claims that an
  allocation is an invoice, billing-accurate charge, forecast, budget, or
  reconciled accounting entry.
- **SC-011**: All five supported runtimes can appear in cost coverage, and each
  runtime with a declared total and readable allocation key can produce an
  allocation without runtime-specific operator queries.
- **SC-012**: Agent, tool, and run cost breakdowns return a result or an
  explained partial result within the same bounded response target as the
  existing Observe views.

## Assumptions

- The first release uses only operator-declared billed totals. Direct billing
  and Cost Management integration may be evaluated separately but is not part
  of this specification.
- The cost model applies per configured cost period and may contain components
  from several billing boundaries. Each component reconciles independently.
- Operator-declared customer-owned compute cost is acceptable when compute runs
  outside the Cockpit's readable cloud scope; without both a declared total and
  readable duration, it is omitted from allocation and reported through
  coverage.
- Credit quantity is the preferred key for credit-metered runtimes. Event-count
  allocation is an explicit lower-confidence option for scenarios that report
  credit-consuming events but not quantities.
- Dimension-weighted model allocation uses only token dimensions reported by
  telemetry and weights declared in the cost model. It never supplies a rate
  table or derives missing dimensions.
- If granular token dimensions are unavailable, the operator may explicitly
  permit uniform allocation by total observed tokens. The fallback is not
  automatic.
- Agent, tool, and run breakdowns reuse the same billed pool at different
  grains. They are diagnostic alternatives, not amounts that can be added
  together.
- All calculations are scoped to observed usage inside the configured period.
  Late-arriving telemetry may change a later calculation, which is why results
  carry refresh and calculation times and are not accounting records.
- The existing Observe authorization boundary, progressive loading, bounded
  query execution, partial-result behavior, result reuse window, and source
  diagnostics continue to apply.
- Configuration deployment and secret handling follow the existing non-secret,
  stateless Observe configuration convention.
- The running Cockpit remains read-only. Cost allocation is a projection over
  operator-declared totals and already-readable telemetry, not a cloud
  provisioning or mutation workflow.
