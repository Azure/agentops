# Feature Specification: Observe tools and runs views

**Feature Branch**: `placerda-cockpit-observe-tools-runs`

**Created**: 2026-08-23

**Status**: Draft

**Input**: GitHub issue [Azure/agentops#441](https://github.com/Azure/agentops/issues/441) — "feat(cockpit): add tools and runs views to the Observe API"

## Clarifications

### Session 2026-08-24

- Q: When the Runs view reports how many turns a run took, what should count as one turn? → A: One exchange with the agent — a single request plus the response it produced. A run correlated from a single trace has exactly one turn; a session-level run has one turn per request in that session.
- Q: What should make a run count as failed in the Runs view? → A: A run is failed if any activity inside it — a turn or a tool invocation — is reported as failed by the telemetry, consistent with how failures are already counted in the existing Observe views.
- Q: How should the Tools and Runs views handle a scope whose activity exceeds the maximum number of rows a single result can hold? → A: Keep the top rows of the view's default ordering — highest-invocation tools, most-recent runs — discard the rest, and report that the list was truncated along with how many rows were shown out of how many exist.

## User Scenarios & Testing *(mandatory)*

Observe today answers "which agent is busy" and "which model is expensive". It
cannot answer "which tool is slow or failing" or "what did one complete run
cost me in turns and time". Tool activity is already present in the telemetry
operators are paying to collect, but nothing surfaces it. There is no notion of
a complete run at all, so an operator cannot tell a clean single-turn success
from a five-turn run that failed after several successful tool calls.

Both gaps are also the blocking prerequisite for any future cost allocation
work: tool invocation counts are the allocation key for tool-side meters, and
run duration is the allocation key for compute.

### User Story 1 - Find the tool that is slow, failing, or over-used (Priority: P1)

An operator investigating a slow or unreliable agent opens Observe, applies the
existing scope, project, agent, and time-range filters, and opens the Tools
view. They see one row per tool the agent actually invoked, with how often it
was invoked, how often it failed, its p95 latency, and when it was last active.
They can narrow to a single tool to confirm the pattern, and they can share the
resulting page with a teammate.

**Why this priority**: This is the largest currently-unanswerable operational
question, the telemetry already exists and is being paid for, and it is the
allocation key for tool-side cost work later. It delivers value on its own with
no dependency on the runs view.

**Independent Test**: Request the Tools view for a scope containing an agent
that emits tool activity and confirm one normalized row per tool with
invocations, failures, p95 latency, and last observed activity. Confirm the
same view for an agent with no tool activity returns an explicit coverage
explanation rather than rows.

**Acceptance Scenarios**:

1. **Given** a readable scope containing an agent that invoked two distinct
   tools in the selected time range, **When** the operator opens the Tools
   view, **Then** they see exactly two rows, each identifying the reporting
   project, the agent, the tool name, and the originating telemetry source,
   with invocation count, failure count, p95 latency, and last observed
   activity.
2. **Given** the Tools view is showing results, **When** the operator applies a
   tool-name filter, **Then** only activity for that tool is returned and every
   other applied filter stays in effect.
3. **Given** the Tools view is showing results, **When** the operator inspects
   any row, **Then** no token counts are shown for that row, because token
   usage is not reported on tool activity.
4. **Given** an agent that produced invocations but no tool activity in the
   selected time range, **When** the operator opens the Tools view, **Then**
   they are told tool activity was not reported or has no data for the period,
   and they are **not** shown a row containing zeros.

---

### User Story 2 - Understand one complete run end to end (Priority: P2)

An operator reviewing agent behavior opens the Runs view and sees one row per
complete run rather than per individual invocation. Each row tells them which
agent it belongs to, how many turns it took, how many tool invocations it made,
how many input and output tokens were observed, whether it failed, how long it
took, and when it was last active. They can select a single run to focus on it.

**Why this priority**: Answers "how many turns did this take" and "which runs
failed after partial tool success", which today require manual trace reading.
It also produces the duration figure that later compute cost allocation
depends on. It is valuable alone, but the tool view answers the more frequent
day-to-day question first.

**Independent Test**: Request the Runs view for a scope containing correlated
multi-turn activity and confirm one row per run with turn count, tool
invocation count, observed token totals, failure state, duration, last
observed activity, and which correlation formed the run. Confirm activity that
cannot be correlated into a run is reported as a coverage gap rather than as
fabricated single-turn runs.

**Acceptance Scenarios**:

1. **Given** a readable scope containing a run with three turns and two tool
   invocations, **When** the operator opens the Runs view, **Then** they see a
   single row reporting three turns and two tool invocations, its agent, its
   failure state, its duration, and its last observed activity.
2. **Given** the Runs view is showing results, **When** the operator applies a
   run filter for one specific run, **Then** only that run is returned and every
   other applied filter stays in effect.
3. **Given** a run in which some turns succeeded and a later turn failed,
   **When** the operator reviews the row, **Then** the run is reported as
   failed, and its successful tool invocation count is also visible, so a
   partial-success failure is distinguishable from a clean failure.
4. **Given** a run in which no activity reported token usage, **When** the
   operator reviews the row, **Then** the token totals are reported as not
   available rather than as zero.
5. **Given** a scope containing both activity that reports a session or
   conversation identity and activity that reports only a single correlated
   trace, **When** the operator reviews the Runs view, **Then** each row states
   which correlation formed it, so a session-level run is not silently compared
   against a single-request run as if their turn counts and durations meant the
   same thing.

---

### User Story 3 - Learn when tool and run data is missing, not just absent (Priority: P3)

An operator who sees an empty Tools or Runs view needs to know whether the
agent genuinely did nothing, the telemetry was never configured to report it,
the data is outside the selected period, or the operator cannot read the
source. Telemetry coverage tells them which, and what to do next.

**Why this priority**: Without it, the first two views silently look like "no
tools were used" whenever attribution is missing, which erodes trust in every
other Observe number. It is smaller than the two views but protects them.

**Independent Test**: Request telemetry coverage for a scope where tool
attribution is absent and for a scope where run correlation is absent, and
confirm each is reported as a distinct, explained coverage result with a
recommended next action.

**Acceptance Scenarios**:

1. **Given** a readable scope where agent activity is present but no tool
   attribution is reported, **When** the operator opens telemetry coverage,
   **Then** tool attribution is reported as not reported, with a concise reason
   and a recommended next action.
2. **Given** a readable scope where activity cannot be correlated into complete
   runs, **When** the operator opens telemetry coverage, **Then** run
   correlation is reported as unavailable or partial, with a concise reason and
   a recommended next action.
3. **Given** a scope the operator cannot read, **When** the operator opens
   telemetry coverage, **Then** tool attribution and run correlation are
   reported as inaccessible rather than as no data.

---

### User Story 4 - Tell which runtime an agent is actually running on (Priority: P4)

An operator looking at an agent row needs to know whether it is a Foundry
hosted agent, a Foundry prompt agent, an external agent that registered its
identity, an external agent that did not, or a Copilot Studio agent — because
the answer changes what data they can expect and who owns the fix. When the
runtime cannot be determined from telemetry, the operator is told it is
unknown rather than shown a guess.

**Why this priority**: It improves interpretation of every other Observe view
and is required to explain why some agents have less data than others, but no
operational question is blocked on it today.

**Independent Test**: Request the Agents view across a scope containing more
than one runtime and confirm each agent reports its determinable runtime, and
that agents whose runtime cannot be determined report unknown.

**Acceptance Scenarios**:

1. **Given** an agent whose runtime is determinable from its telemetry, **When**
   the operator views its row, **Then** the reported runtime distinguishes
   Foundry hosted, Foundry prompt, external registered, external unregistered,
   and Copilot Studio.
2. **Given** an agent whose runtime cannot be determined from its telemetry,
   **When** the operator views its row, **Then** the runtime is reported as
   unknown and is **not** inferred from unrelated telemetry.
3. **Given** an agent previously reported only as a Foundry agent, **When**
   telemetry does not determine whether it is hosted or prompt, **Then** it is
   reported as unknown rather than retaining the previous coarser value.

---

### Edge Cases

- An agent has invocations but no tool activity in the period: coverage
  explains it; the Tools view does not emit a zero row.
- Tool activity is present but carries no usable tool name: the activity is
  reported as unattributed tool coverage, not folded into a synthesized or
  guessed tool name.
- Activity in the period cannot be correlated into a complete run (for example,
  correlation identifiers are missing or the parent activity is unreadable):
  run correlation coverage reports the gap; unattributable activity is not
  emitted as fabricated single-turn runs.
- A run has not finished by the end of the selected time range: the run is
  reported with the activity observed inside the window and is marked as still
  in progress, rather than silently truncating turn count, duration, or failure
  state into apparently complete values.
- A run started before the selected time range began: only the activity inside
  the window is reported, and the view states that start, duration, turn count,
  and failure state are scoped to the selected range. The run is not flagged as
  truncated at its leading edge, which is an accepted limitation of this feature.
- A run touches more than one project or agent: the row preserves its
  originating project and agent attribution rather than collapsing them.
- Some activity in the period reports a session or conversation identity and
  some does not: session-level rows and single-trace rows coexist, and each row
  states which correlation formed it rather than presenting mixed granularity as
  uniform.
- An agent that previously reported only a coarse Foundry runtime cannot be
  resolved to hosted or prompt: it is reported as unknown rather than retaining
  the previous coarser value.
- No activity in a run reports token usage: token totals are absent, not zero.
- A resource-identifying filter value falls outside the configured authorization
  boundary: the request is rejected the same way existing out-of-scope dimension
  values are rejected today.
- A tool-name or run filter value is malformed — empty after trimming, longer
  than the accepted bound, or carrying query syntax: it is rejected as a
  well-formedness failure, and any accepted value is escaped so it cannot alter
  the shape of the query it is placed into.
- A tool-name or run filter value is well-formed but matches nothing: an
  empty, explained result is returned rather than an error.
- Tool-name cardinality is very high: the result set stays bounded to the
  highest-invocation tools, and the operator is told the list was truncated and
  how many tools were shown out of how many exist.
- Copilot Studio reports environment-level tool activity in a different shape
  than Foundry-native telemetry: it is normalized into the same rows when
  readable, and reported as a coverage gap when it is not.
- Tool and run rows are aggregates only: no prompt, response, tool argument, or
  tool result content is exposed by these views, including when the operator
  would otherwise be authorized to read protected content elsewhere.

## Requirements *(mandatory)*

### Functional Requirements

#### Tools view

- **FR-001**: Observe MUST provide a Tools view that reports observed tool
  activity for the applied scope, filters, and time range.
- **FR-002**: The Tools view MUST report one normalized row per combination of
  reporting project, agent, tool name, and originating telemetry source.
- **FR-003**: Each tool row MUST report invocation count, failure count, p95
  latency, and last observed activity.
- **FR-004**: Tool rows MUST NOT report token counts, because token usage is
  not required to be reported on tool activity and MUST NOT be synthesized.
- **FR-005**: Tool activity that carries no usable tool name MUST be reported
  through telemetry coverage and MUST NOT be assigned an inferred, defaulted,
  or placeholder tool name.

#### Runs view

- **FR-006**: Observe MUST provide a Runs view that reports one row per
  correlated run for the applied scope, filters, and time range.
- **FR-007**: Each run row MUST report its run identity, its agent, turn count,
  tool invocation count, total observed input tokens, total observed output
  tokens, failure state, duration, and last observed activity.
- **FR-007A**: One turn MUST mean one exchange with the agent — a single request
  sent to the agent plus the response it produced. A run formed from a single
  correlated trace therefore reports exactly one turn, and a session-level run
  reports one turn per request in that session. Turn count MUST NOT be derived
  from the number of model calls made while producing a single response.
- **FR-008**: A run's failure state and its tool invocation count MUST both be
  reported, so a run that failed after partially successful tool activity is
  distinguishable from a run that failed outright.
- **FR-008A**: A run MUST be reported as failed when any activity inside it — a
  turn or a tool invocation — is reported as failed by the telemetry, consistent
  with how failures are counted in the existing Observe views. A run MUST NOT be
  reported as successful because a later turn recovered from an earlier failure.
- **FR-009**: A run MUST be identified by the most specific correlation
  available for that activity: a reported session or conversation identity when
  one is present, otherwise a single correlated trace — one request and
  everything it caused.
- **FR-009A**: Each run row MUST report which correlation was used to form it,
  so an operator can tell a session-level run from a single-request run and
  does not compare turn count or duration across incompatible rows.
- **FR-010**: Activity that cannot be correlated into a run MUST be reported
  through telemetry coverage and MUST NOT be emitted as a fabricated run.
- **FR-011**: When no activity in a run reports token usage, the run's token
  totals MUST be reported as unavailable and MUST NOT be reported as zero.
- **FR-012**: A run that has not finished by the end of the selected time range
  MUST be identifiable as still in progress, so its turn count, duration, and
  failure state are not misread as final.
- **FR-012A**: A run's reported start, duration, turn count, and failure state
  are scoped to the selected time range: they describe activity observed inside
  the window, not the run's absolute lifetime. Activity that occurred before the
  window began is outside the queried period and is not reported. Both views
  MUST state this window-scoped meaning wherever these values are presented, so
  a run that began before the window is not read as having a shorter duration or
  fewer turns than it actually had. Detecting and flagging that a run began
  before the window is explicitly out of scope for this feature; see the
  corresponding assumption.

#### Filters

- **FR-013**: Observe filters MUST support an optional tool-name dimension and
  an optional run dimension, each defaulting to all values.
- **FR-014**: Filter dimensions that identify a readable resource MUST continue
  to be validated against the configured authorization boundary, and requests
  carrying an out-of-boundary resource value MUST be rejected rather than
  silently narrowed or ignored.
- **FR-014A**: The tool-name and run dimensions do not identify a resource and
  have no authorization boundary of their own. They only narrow a result set
  that the resource-level boundary has already constrained, so they MUST be
  validated for well-formedness — trimmed, rejected when empty after trimming,
  bounded in length, and escaped so a filter value cannot alter the shape of the
  query it is placed into — and a well-formed value that matches nothing MUST
  return an empty, explained result rather than an authorization error.
- **FR-015**: Applied filters, including the new dimensions, MUST be
  represented in the shared page location so a Tools or Runs view can be
  bookmarked and shared, and MUST remain applied when the operator switches
  between Observe views.

#### Runtime attribution

- **FR-016**: An observed agent's reported runtime MUST distinguish Foundry
  hosted, Foundry prompt, external registered, external unregistered, and
  Copilot Studio when the runtime is determinable from telemetry.
- **FR-017**: When the runtime is not determinable from telemetry, the reported
  runtime MUST be unknown, and MUST NOT be inferred from unrelated telemetry
  fields.
- **FR-018**: The refined runtime values MUST replace today's coarse Foundry and
  external runtime values rather than being reported alongside them, so a
  reported runtime has exactly one granularity. This is a declared breaking
  change to a published contract and MUST be released as such, with the
  replacement documented for existing consumers.
- **FR-018A**: Because today's coarse Foundry value does not map onto exactly
  one refined value, an agent previously reported as Foundry MUST be reported as
  Foundry hosted or Foundry prompt only when telemetry determines which, and as
  unknown otherwise.

#### Coverage

- **FR-019**: Telemetry coverage MUST report tool attribution and run
  correlation as distinct coverage dimensions.
- **FR-020**: Each new coverage dimension MUST distinguish source inaccessible,
  telemetry not configured, no data in the selected period, expected
  attribution not reported, and partial availability.
- **FR-021**: Each unavailable or incomplete tool-attribution or
  run-correlation result MUST include a concise reason and a recommended next
  action.

#### Scope, sources, and integrity

- **FR-022**: Both views MUST include compatible external-agent telemetry when
  it reaches a readable telemetry source in the selected scope, consistent with
  the existing Observe source-inclusion rule (spec 011, FR-033).
- **FR-023**: Both views MUST preserve each result's originating telemetry
  source, project, and agent rather than collapsing results from different
  sources into indistinguishable rows.
- **FR-023A**: The existing agents view MUST preserve each result's originating
  telemetry source for the same reason and by the same means as FR-023. Agent
  rows are also produced per source and concatenated, so without source
  attribution two sources reporting the same agent yield two rows an operator
  cannot tell apart. This is an additive, non-breaking change to an existing
  view, adopted in this feature so that every row-bearing Observe view is
  consistently attributed within a single release rather than leaving the
  agents view inconsistent with the two new ones.
- **FR-024**: Both views MUST be read-only projections of telemetry the
  operator can already read, and MUST NOT create, modify, or delete any cloud
  resource, and MUST NOT change or generate customer telemetry configuration.
- **FR-025**: Missing tool or run data MUST NOT be represented as zero, as
  inferred success, or as a value derived from unrelated fields.
- **FR-026**: Neither view MUST expose prompt, response, system-instruction,
  tool-argument, or tool-result content; both views report aggregates only.
- **FR-027**: Neither view MUST report or imply monetary value, cost, or price.
- **FR-028**: Both views MUST return bounded result sets and MUST make
  truncation visible when the available activity exceeds the bound.
- **FR-028A**: Tool rows MUST default to highest-invocation-count-first ordering
  and run rows MUST default to most-recent-activity-first ordering. When the
  bound is exceeded, the result MUST retain the top rows of that default
  ordering and discard the remainder, and MUST report both how many rows were
  returned and how many the scope contains, so the operator can tell what was
  omitted.
- **FR-029**: Normal operator use of both views MUST NOT require query syntax,
  telemetry-workspace wiring, or raw resource identifiers, consistent with the
  existing Observe usability rule (spec 011, FR-034).

### Key Entities

- **Observed tool activity**: A normalized aggregate of one tool as invoked by
  one agent within one reporting project, from one telemetry source. Carries
  invocation count, failure count, p95 latency, and last observed activity.
  Deliberately carries no token usage and no monetary value.
- **Observed run**: A normalized aggregate of one correlated run. Carries run
  identity, which correlation formed it (a reported session or conversation
  identity, or a single correlated trace), agent identity, turn count — where a
  turn is one request to the agent plus its response — tool invocation count,
  observed input and output token totals, failure state, duration, last observed
  activity, and whether the run extends outside the selected time range.
- **Observe filter dimensions**: The set of optional narrowing dimensions an
  operator can apply, extended with tool name and run identity. Every dimension
  is validated against the configured authorization boundary.
- **Runtime attribution**: The reported runtime an observed agent is running
  on — Foundry hosted, Foundry prompt, external registered, external
  unregistered, Copilot Studio, or unknown. Never inferred.
- **Coverage result**: A per-dimension statement of whether expected data is
  available, why it is not, and what to do next; extended with tool attribution
  and run correlation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator can identify the slowest and the most-failing tool for
  a selected agent within 60 seconds of opening Observe, without writing a
  query or knowing a resource identifier.
- **SC-002**: An operator can determine how many turns a run took and whether it
  failed from a single row, without opening a second view or reading a raw
  trace.
- **SC-003**: 100% of scopes where tool activity or run correlation is
  unavailable produce an explicit coverage explanation with a recommended next
  action, and 0% produce an unexplained empty table.
- **SC-004**: 0% of tool rows report token counts, and 0% of rows in either view
  report a monetary value.
- **SC-005**: 0% of reported values in either view are zero-filled, inferred, or
  derived from unrelated telemetry when the underlying data is absent.
- **SC-006**: 100% of agents whose runtime is determinable from telemetry report
  a specific runtime, and 100% of agents whose runtime is not determinable
  report unknown.
- **SC-007**: 100% of requests for a view or a resource-identifying filter value
  outside the configured authorization boundary are rejected, and 100% of
  malformed tool-name or run filter values are rejected as well-formedness
  failures.
- **SC-008**: 100% of applied filter states in both views are reproduced exactly
  when the shared page location is reopened by another operator with equivalent
  access.
- **SC-009**: Both views return results, or an explained partial result, within
  the same bounded time budget the existing Observe views already honor, with
  no view able to block the others.
- **SC-010**: Tool invocation counts and run durations are available as
  allocation keys for a later cost-allocation effort without that effort needing
  to re-derive them from raw telemetry.
- **SC-011**: 100% of run rows state which correlation formed them, so no two
  rows of different granularity can be compared without the operator knowing.
- **SC-012**: 0% of agent rows report a runtime at the previous coarser
  granularity once this change is released.

## Assumptions

- The existing Observe behavior established in spec 011 applies unchanged to
  both new views: scope validation, filter defaults and Apply behavior, the
  24-hour default time range, progressive and on-demand loading, refresh and
  reuse windows, bounded query execution, partial-result reporting, and query
  diagnostics. This feature adds views and dimensions rather than changing that
  behavior.
- Presenting both views in the Cockpit is in scope, not only making the
  underlying data queryable, because the accepted criteria require filters to
  round-trip through the shared page location and require operators to see an
  explanation instead of an empty table.
- All five in-scope runtimes are read-only telemetry sources for this feature:
  Foundry hosted, Foundry prompt, external registered, external unregistered,
  and Copilot Studio environment-level telemetry. No billing, metering, or
  provisioning dependency is introduced.
- Copilot Studio **agent-level event telemetry** is out of scope; only
  environment-level telemetry that aligns to the shared generative-AI telemetry
  conventions is normalized into these views.
- Run completeness is reported for the trailing boundary only. A run still
  active at the end of the selected range is marked as in progress, but a run
  that began before the range started is not flagged as truncated. Detecting the
  leading edge requires scanning telemetry outside the selected range to find
  each run's true first activity, which would materially increase the data read
  by every runs query for a case that is uncommon at the default lookback and
  typical agent run duration. The accepted consequence is that such a run reports
  a start, duration, turn count, and failure state scoped to the selected window
  rather than to the run's absolute lifetime; FR-012A requires both views to
  state this meaning explicitly so the values are not misread as absolute.
- Attributing tokens to an individual tool invocation is out of scope. Token
  totals appear only at run level, as observed usage, and only when reported.
- A run is formed from the most specific correlation the telemetry offers: a
  reported session or conversation identity when present, otherwise a single
  correlated trace. This keeps every in-scope runtime representable, including
  runtimes that report no session identity, at the cost of rows with mixed
  granularity — which is why each row states the correlation that formed it
  (FR-009, FR-009A).
- Replacing the previous coarse runtime values is an accepted, declared breaking
  change to a published contract rather than an additive one, so a reported
  runtime always has a single granularity (FR-018). It requires an explicit
  release note and an explicit exception to the project's
  preserve-public-contracts principle, and it is expected to move some agents
  from a previously specific-looking value to unknown until their runtime is
  determinable (FR-018A).
- The row bound applied to both views is the same bounded-detail rule already
  used elsewhere in Observe (spec 011, FR-044); this feature adopts that bound
  rather than defining a new one, and specifies only the ordering and truncation
  reporting on top of it (FR-028A).
- Whether Foundry prompt agents emit generative-AI activity that identifies
  their runtime is unconfirmed. This does not block the feature: when the
  runtime cannot be determined, it is reported as unknown, per FR-017.
- Whether Copilot Studio tool activity carries a tool name that normalizes
  cleanly is unconfirmed. This does not block the feature: unnormalizable tool
  activity is reported through coverage, per FR-005.
- The feature depends on telemetry the operator already has access to. It does
  not add a new telemetry source, and it does not require customers to change
  their telemetry configuration to see honest coverage results.
