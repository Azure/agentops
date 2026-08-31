# Feature Specification: Observe Console Refinements

**Feature Branch**: `014-observe-console-refinements`

**Created**: 2026-08-31

**Status**: Draft

**Input**: Annotated review document `ajustes_observe.pdf` (3 pages, 7 annotated
screenshots) covering the Observe **Overview** tab, the Observe **main menu**,
and the Observe **Runs** tab.

## User Scenarios & Testing *(mandatory)*

The Observe console already answers "what did my agents do?" but an operator
still has to work around the console to get there. Scope has to be typed from
memory as free text, the time window has to be assembled by hand from two
calendar fields, the Overview headline numbers do not say what they count,
timestamps mix two timezones on the same screen, and the Runs table spends its
width on unreadable identifiers while the question operators actually ask —
"what did this run cost me?" — is not answered at all.

This feature makes the console usable at a glance: pick scope from what is
actually there, pick time from named presets, read every number and timestamp
without guessing what it refers to, and see an estimated cost next to every run
and totalled per agent and per model.

## Clarifications

### Session 2026-08-31

- Q: When a filter has far more values than fit on screen, should the console load every option up front or fetch on demand as the operator types? → A: Load the most active options for the window (target 50) and fetch the remainder on demand once the operator types, so open time stays constant regardless of environment size.
- Q: Who keeps the price reference current, and what should the console do once it is too old to trust? → A: The price reference ships with the accelerator and is refreshed each release; ninety days past its effective date the console marks the estimate stale but keeps showing it.
- Q: After an operator builds a scope, what should survive a page reload or a return the next day? → A: The scope lives in the console address — reload restores it, the address is shareable, and browser history undoes a filter change; nothing is retained outside that address.
- Q: Should estimated cost appear only per run, or also rolled up per agent and per model? → A: Per run in the Runs view plus a rolled-up total per agent and per model in the views that already group by those entities, each total declaring how many runs carry no estimate.

### User Story 1 - Scope the Console by Picking, Not Typing (Priority: P1)

An operator opens Observe and narrows the console to the agents they care
about. Each scope filter offers the values that actually exist in the current
scope and time window as a checkable list, and the operator can select several
at once. The filters follow the natural hierarchy from left to right — choosing
a Foundry resource narrows the projects on offer, choosing projects narrows the
agents, and so on — so the operator is never offered a combination that returns
nothing. Whatever they select stays selected as they move between tabs, and the
console address carries that scope so the page can be reloaded or the address
handed to a colleague and land on the same view.

**Why this priority**: Every other view is read through these filters. Today an
operator must already know an identifier verbatim before the console can be
narrowed at all, which makes the whole console unusable for anyone who did not
deploy the agent. This unblocks all the other stories.

**Independent Test**: With telemetry for at least two Foundry resources, two
projects, and three agents in range, open Observe, select two agents from the
Agent filter without typing, apply, switch to another tab, and confirm the
selection is still applied and the result set matches only those agents.

**Acceptance Scenarios**:

1. **Given** telemetry for three agents in the current time window, **When** the
   operator opens the Agent filter, **Then** exactly those three agents are
   offered as selectable options with human-readable names.
2. **Given** the operator selects two agents, **When** they apply the filters,
   **Then** every view reflects both agents and no other agent.
3. **Given** the operator selects one Foundry resource, **When** they open the
   Project filter, **Then** only projects belonging to that resource are
   offered.
4. **Given** the operator has selected an agent and then narrows the Foundry
   resource so that agent is no longer in scope, **When** the filters are
   applied, **Then** the now-invalid selection is dropped and the operator is
   told which selections were removed.
5. **Given** the operator has scoped the console on the Overview tab, **When**
   they switch to the Runs tab, **Then** the same scope is applied without
   re-entry.
6. **Given** no values are selected in a filter, **When** the filters are
   applied, **Then** that dimension is unrestricted.
7. **Given** a filter has more options than can be shown at once, **When** the
   operator opens it, **Then** they can search within the option list and are
   told how many of the total options are being shown.
8. **Given** the operator has applied a scope, **When** they reload the page or
   open the same console address elsewhere, **Then** the same filter selections
   and time window are restored.
9. **Given** the operator opens the console at an address that carries no scope,
   **When** the console loads, **Then** it starts from the default window with
   no filters applied rather than restoring a previous scope.

---

### User Story 2 - Choose the Time Window From Named Presets (Priority: P1)

An operator picks the reporting window from a row of named choices — 30 minutes,
1 hour, 6 hours, 12 hours, 1 day, 3 days, 7 days, 30 days — instead of filling
in two calendar fields. The last 7 days is selected by default. A final
**Custom** choice reveals the explicit start and end pickers for the cases the
presets do not cover. The chosen window applies to every tab.

**Why this priority**: The time window is the second half of "what am I
looking at". Two free-form datetime fields are the slowest and most
error-prone control on the page, and they are on the critical path for every
single question the console answers.

**Independent Test**: Open Observe with no saved state, confirm the last 7 days
is preselected and the result set matches it, click the "1 day" preset and
confirm the result set narrows, then click "Custom" and confirm explicit start
and end pickers appear and drive the window.

**Acceptance Scenarios**:

1. **Given** a first visit with no saved state, **When** Observe loads, **Then**
   the 7-day preset is selected and the data shown covers the last 7 days.
2. **Given** the operator selects the "6 hours" preset, **When** the filters are
   applied, **Then** every tab reports over the last 6 hours.
3. **Given** a relative preset is selected, **When** the operator refreshes,
   **Then** the window is recomputed relative to the refresh moment rather than
   frozen at the time of selection.
4. **Given** the operator selects "Custom", **When** the control expands,
   **Then** explicit start and end pickers appear and the console uses exactly
   that fixed interval.
5. **Given** a custom interval whose end precedes its start, **When** the
   operator applies it, **Then** the console explains the problem and does not
   run the query.
6. **Given** a preset is selected on one tab, **When** the operator moves to
   another tab, **Then** the same preset remains selected.

---

### User Story 3 - Read the Runs Table Without Decoding It (Priority: P2)

An operator scanning the Runs table can read every column at a glance. Long
opaque identifiers — the run key and the telemetry source — are shortened to
what a human needs and can be copied in full with one action. The source shows
the logs workspace name in plain text instead of a full resource path rendered
as a badge. Column headers say what they mean without the repeated "in range"
suffix that every column already implies. Where a header needs explaining, the
explanation is presented by the console itself rather than the browser's
built-in hover text.

**Why this priority**: The Runs table is the operator's primary diagnostic
surface, and today most of its width is spent on strings nobody reads. This is
high-value and low-risk, but it depends on nothing, so it follows the two
scoping stories.

**Independent Test**: With at least five runs in range, open the Runs tab and
confirm the run key and source are shortened, each offers a copy action that
places the full value on the clipboard, no header carries the "in range"
suffix, and hovering a header shows the console's own explanation panel.

**Acceptance Scenarios**:

1. **Given** a run key longer than the visible column width, **When** the row is
   rendered, **Then** a shortened form is shown together with a copy control.
2. **Given** the operator activates the run key copy control, **When** the copy
   succeeds, **Then** the full untruncated run key is on the clipboard and the
   console confirms the copy.
3. **Given** a run whose source is a full logs workspace resource path, **When**
   the row is rendered, **Then** only the workspace name is shown, as plain
   text with no badge or pill styling, with a copy control for the full path.
4. **Given** the Runs table is rendered, **When** the operator reads the column
   headers, **Then** they read "Started", "Duration", and "Turns" with no "in
   range" suffix.
5. **Given** a column header carries an explanation, **When** the operator
   hovers or focuses it, **Then** the console renders a formatted explanation
   panel rather than the browser's plain hover text.
6. **Given** the correlation dimension carries only one distinct value across
   every run in scope, **When** the table is rendered, **Then** the console does
   not spend a column on it and instead states the single value once above the
   table.
7. **Given** a copy action is used, **When** it completes, **Then** the copied
   value never appears in the page address.

---

### User Story 4 - See What a Run Cost (Priority: P2)

An operator reviewing runs sees an estimated cost beside each one, derived from
the tokens that run consumed and a published unit-price reference that ships
with the accelerator and states its effective date. The explanation
attached to the column states the calculation, that prices differ by model and
by token type, the effective date of the price reference, and that the
figure is an estimate from public list prices — not an invoice and not a billed
amount. Where a run has cost components that cannot be derived from telemetry,
the estimate says so instead of pretending to be complete. Once the reference is
more than ninety days old, the estimate is marked stale but is still shown. The
same estimate is rolled up per agent and per model in the views that already
group by those entities, so the operator can answer "what did this agent cost
this week?" without adding rows by hand — and each total declares how many of
its runs carry no estimate, so a partial total is never mistaken for a complete
one.

**Why this priority**: Token economy is the question operators ask most often
about runs, and it is the one number the console cannot currently produce. It
is second only to being able to read the table at all.

**Independent Test**: With runs whose token usage is recorded for a priced
model, open the Runs tab and confirm each such run shows a currency-qualified
estimate, that opening the column explanation states the formula and the price
reference date, that a run using an unpriced model shows an explicit
"not priced" state rather than zero, and that the view grouped by agent shows a
rolled-up estimate naming how many of its runs went unpriced.

**Acceptance Scenarios**:

1. **Given** a run with recorded input and output token counts for a priced
   model, **When** the Runs table is rendered, **Then** an estimated cost is
   shown with its currency.
2. **Given** the operator opens the estimated cost explanation, **When** it is
   displayed, **Then** it states the calculation basis, that prices vary by
   model and by token type, the price reference version and effective date,
   and that the value is an estimate rather than a billed amount.
3. **Given** a run using a model absent from the price reference, **When** the
   row is rendered, **Then** the console shows an explicit unpriced state and
   never shows zero or an invented figure.
4. **Given** a run that also incurs cost components not derivable from
   telemetry, **When** the estimate is shown, **Then** it is marked as partial
   and the excluded components are named.
5. **Given** the price reference is missing or unreadable, **When** the Runs tab
   is rendered, **Then** the estimated cost column reports itself unavailable
   and every other column still renders.
6. **Given** declared billed totals are also configured, **When** both are
   available, **Then** the estimate is presented as a distinct figure from the
   allocated billed amount and neither replaces the other.
7. **Given** any estimated figure is displayed, **When** it is shown, **Then**
   its currency and the date of the price basis are shown with it.
8. **Given** the price reference is more than ninety days past its effective
   date, **When** an estimate derived from it is shown, **Then** it is marked
   stale and states the reference's age, and the figure is still displayed.
9. **Given** a scope containing several agents with priced runs, **When** the
   view that groups by agent is rendered, **Then** each agent shows a rolled-up
   estimated cost for the selected window with its currency.
10. **Given** an agent whose runs are only partly priced, **When** its rolled-up
    estimate is shown, **Then** it states how many of its runs carry no estimate
    and those runs are not counted as zero.

---

### User Story 5 - Understand What the Overview Counts (Priority: P3)

An operator landing on Overview can tell, without opening documentation, what
each headline number measures. Counts and rates state the entity they belong to
rather than standing alone as "Invocations" or "Success rate". The Overview is
organised so runs — the operator's primary lens — are presented first, followed
by agents, models, and tools, and the runs summary includes token consumption.
The navigation reflects the same priority, placing Runs directly after
Overview.

**Why this priority**: This is a comprehension problem, not a blocking one. The
numbers are already correct; they are simply unlabelled. It ships after the
controls and the table are usable.

**Independent Test**: Open Overview with telemetry present and confirm each
headline states its entity, that a runs summary including token consumption is
presented first, and that the navigation reads Overview, Runs, Agents, Models
and usage, Tools.

**Acceptance Scenarios**:

1. **Given** the Overview is rendered, **When** the operator reads any headline
   figure, **Then** the entity it counts is stated in the label.
2. **Given** telemetry is present, **When** the Overview renders, **Then** a
   runs summary that includes token consumption appears before the agent,
   model, and tool summaries.
3. **Given** the operator reads the navigation, **When** the tabs render,
   **Then** their order is Overview, Runs, Agents, Models and usage, Tools.
4. **Given** a dimension has no data in the current scope and window, **When**
   the Overview renders, **Then** that summary reports the absence plainly
   rather than showing a bare zero.
5. **Given** the Overview is expanded with per-entity summaries, **When** it is
   rendered, **Then** it requires no telemetry beyond what the Overview already
   retrieves for the selected scope and window.

---

### User Story 6 - Read Every Time in One Timezone (Priority: P3)

An operator comparing the "refreshed" indicator with the selected window sees
both expressed in the same timezone, stated once on the page so there is no
ambiguity about what "10:41" means. The refresh indicator is moved out of the
primary reading path to the right of the filter controls and rendered
compactly, because it is supporting information rather than a headline.

**Why this priority**: Mixed timezones on one screen cause real
misinterpretation, but the underlying data is correct and the workaround —
mental arithmetic — exists. It is a polish item that ships last.

**Independent Test**: In a non-UTC timezone, open Observe, note the start and
end of the selected window and the refreshed indicator, and confirm both are
expressed in the same timezone, that the timezone is stated on the page, and
that the refreshed indicator sits to the right of the controls in a compact
format.

**Acceptance Scenarios**:

1. **Given** the viewer is in a non-UTC timezone, **When** the page renders,
   **Then** the window boundaries and the refreshed indicator are expressed in
   the same timezone.
2. **Given** the page renders, **When** the operator looks for the timezone,
   **Then** it is stated once, adjacent to the time controls.
3. **Given** the refreshed indicator renders, **When** it is displayed, **Then**
   it appears to the right of the filter controls, visually de-emphasised
   relative to the data.
4. **Given** the refreshed indicator renders, **When** it shows a date, **Then**
   the date is abbreviated rather than fully spelled out.
5. **Given** the console has never completed a refresh, **When** the page
   renders, **Then** the indicator reports that state instead of showing an
   empty or placeholder time.

---

### Edge Cases

- A scope filter has no observable values in the current window: the filter
  reports that it is empty rather than offering an empty dropdown.
- A scope filter has thousands of observable values: options are searchable and
  bounded, and the operator is told how many of the total are being shown.
- The operator selects values in a lower filter and then changes a higher one so
  those values leave scope: invalidated selections are dropped and named.
- A saved or shared console link references a filter value that no longer
  exists: the console loads, drops the missing value, and says so.
- A relative preset window crosses a daylight-saving transition: the window
  length remains the nominal duration and no rows are duplicated or dropped.
- A "Custom" interval extends beyond the available telemetry retention: the
  console reports the truncated coverage rather than silently returning less.
- A run key or source path is short enough to fit: it is shown in full and the
  copy control is still offered.
- The clipboard is unavailable or blocked by the browser: the console reports
  the failure and offers the full value for manual selection.
- A run records tokens for more than one model: the estimate accounts for each
  model at its own price and the explanation names them.
- An entity's runs are priced in more than one currency: the rolled-up estimate
  reports per-currency subtotals rather than a single combined figure.
- A run records token totals but no input/output split: the estimate reports the
  reduced precision instead of assuming a split.
- The price reference is more than ninety days past its effective date: the
  estimate is marked stale and states the reference's age, but is still shown.
- Two runs are priced in different currencies: no combined total across
  currencies is presented.

## Requirements *(mandatory)*

### Scope Filtering

- **FR-001**: Each scope filter — Foundry resource, project, agent, model, tool,
  and run key — MUST offer the values observable within the current scope and
  time window as a selectable list, rather than requiring a typed identifier.
- **FR-002**: Each scope filter MUST accept zero, one, or many selected values,
  where zero selections means the dimension is unrestricted.
- **FR-003**: Filter options MUST cascade in hierarchy order — Foundry resource,
  then project, then agent, then model, then tool, then run key — so that each
  filter offers only values reachable given the selections made before it.
- **FR-004**: When a selection becomes unreachable because a higher-level filter
  changed, the system MUST drop that selection and name what was dropped.
- **FR-005**: Applied filter selections and the selected time window MUST persist
  across every Observe tab without re-entry, and MUST be carried in the console
  address so that reloading the page, sharing the address, or using browser
  history restores the same scope.
- **FR-005a**: The console MUST NOT retain a previously applied scope beyond the
  console address; opening the console without a scope in its address MUST start
  from the default window with no filters applied.
- **FR-006**: Filter changes MUST remain in a draft state until explicitly
  applied, so a multi-part change results in a single query.
- **FR-007**: A filter MUST present an initial option set drawn from the most
  active values in the current scope and window, and MUST state how many of the
  total options that set represents.
- **FR-007a**: A filter MUST let the operator search the full option set for the
  current scope and window, including values outside the initial set, and MUST
  resolve that search without loading every option first.
- **FR-007b**: A filter's initial option set MUST open in the same amount of time
  regardless of how many distinct values exist in the environment.
- **FR-008**: Filter option values and selections MUST NOT introduce any
  generative-AI content field into the console address or shareable link.

### Time Window Selection

- **FR-009**: The console MUST offer named relative window presets covering
  30 minutes, 1 hour, 6 hours, 12 hours, 1 day, 3 days, 7 days, and 30 days.
- **FR-010**: The 7-day preset MUST be the default selection when no window has
  been chosen.
- **FR-011**: The console MUST offer a Custom option that reveals explicit start
  and end selection and applies exactly that fixed interval.
- **FR-012**: A relative preset MUST be re-evaluated against the current moment
  on every query, including manual refresh and automatic refresh.
- **FR-013**: The selected window MUST apply to every Observe tab and MUST
  persist across tab changes.
- **FR-014**: A custom interval whose end is not after its start MUST be
  rejected with an explanation and MUST NOT be queried.

### Time Presentation

- **FR-015**: All times displayed on an Observe page — window boundaries, row
  timestamps, and the refresh indicator — MUST be expressed in one and the same
  timezone.
- **FR-016**: The timezone in use MUST be stated once on the page, adjacent to
  the time controls.
- **FR-017**: The refresh indicator MUST be positioned to the right of the
  filter controls and MUST be visually subordinate to the data it accompanies.
- **FR-018**: The refresh indicator MUST use an abbreviated date form rather
  than a fully spelled-out date.
- **FR-019**: Before any successful refresh, the indicator MUST report that
  state explicitly rather than rendering an empty or placeholder time.

### Overview Comprehension

- **FR-020**: Every Overview headline figure MUST state the entity it measures
  in its label.
- **FR-021**: The Overview MUST present a summary per entity family — runs,
  agents, models, and tools — with the runs summary presented first.
- **FR-022**: The runs summary MUST include token consumption for the selected
  scope and window.
- **FR-023**: An entity family with no data in the current scope and window MUST
  report the absence explicitly rather than presenting a bare zero.
- **FR-024**: The expanded Overview MUST NOT require telemetry beyond what the
  Overview already retrieves for the selected scope and window.
- **FR-025**: Navigation MUST present the views in the order Overview, Runs,
  Agents, Models and usage, Tools.

### Runs Table Readability

- **FR-026**: A run key that exceeds the readable column width MUST be displayed
  in shortened form together with a control that copies the full value.
- **FR-027**: The telemetry source MUST be displayed as the logs workspace name
  in plain text, without badge, pill, or monospace treatment, together with a
  control that copies the full source path.
- **FR-028**: A copy control MUST confirm success or failure to the operator and
  MUST NOT place the copied value into the page address.
- **FR-029**: Column headers MUST NOT carry the redundant "in range" suffix; the
  affected headers MUST read "Started", "Duration", and "Turns".
- **FR-030**: Renaming a column header MUST NOT change that column's sort or
  filter behaviour.
- **FR-031**: Header explanations MUST be rendered by the console with support
  for multi-line and structured content, and MUST NOT rely on the browser's
  built-in hover text.
- **FR-032**: Header explanations MUST be reachable by keyboard as well as by
  pointer.
- **FR-033**: When a dimension carries a single distinct value across every row
  in scope, the console MUST state that value once outside the table instead of
  dedicating a column to it.
- **FR-033a**: When the displayed rows are a truncated subset of the rows in
  scope, the console MUST NOT collapse a dimension on the evidence of the
  displayed rows alone, because uniformity across a subset does not establish
  uniformity across the scope.

### Estimated Cost

- **FR-034**: The Runs view MUST present an estimated cost for each run,
  qualified by its currency.
- **FR-034a**: Views that group telemetry by agent and by model MUST present a
  rolled-up estimated cost for each of those entities over the selected scope
  and window, qualified by its currency.
- **FR-034b**: A rolled-up estimated cost MUST state how many runs contributing
  to that entity carry no estimate, and MUST NOT count an unestimated run as
  zero.
- **FR-034c**: A rolled-up estimated cost MUST carry the same estimate
  disclaimer and price-reference provenance required of an individual estimate.
- **FR-034d**: A rolled-up estimated cost MUST cover every run the entity has in
  the selected scope and window, independent of how many run rows the console
  displays. Where that coverage cannot be established, the roll-up MUST state
  the share of the scope it covers rather than presenting a partial sum as a
  total.
- **FR-035**: The estimate MUST be computed from the observed token counts for
  that run multiplied by unit prices drawn from a price reference published with
  the accelerator.
- **FR-035a**: When a run uses more than one model, its token counts MUST be
  attributed to the model that produced them and priced at that model's own
  rates. The console MUST NOT price a multi-model run by applying a single
  model's rates to the run's combined token totals.
- **FR-036**: The price reference MUST be human-readable, MUST be published as
  part of the accelerator and refreshed on each release, and MUST record its
  version, its effective date, and the source of its figures.
- **FR-036a**: When the price reference is more than ninety days past its
  effective date, every estimate derived from it MUST be marked stale, MUST state
  how old the reference is, and MUST still be displayed.
- **FR-037**: The price reference MUST express prices separately per model and
  per token type, distinguishing at minimum input, output, and cached tokens.
- **FR-038**: When no price exists for a model or token type used by a run, the
  console MUST report the run as not priced and MUST NOT display zero or an
  interpolated figure.
- **FR-039**: When a run incurs cost components that cannot be derived from
  observed telemetry, the estimate MUST be marked partial and the excluded
  components MUST be named.
- **FR-040**: The estimated cost explanation MUST state the calculation basis,
  that prices vary by model and by token type, the price reference version and
  effective date, and that the figure is a list-price estimate rather than a
  billed amount.
- **FR-041**: When the price reference is missing, unreadable, or invalid, the
  estimated cost MUST report itself unavailable while every other column
  continues to render.
- **FR-042**: Estimated cost MUST be presented as distinct from any allocated
  billed amount, and MUST NOT replace, override, or be summed with allocated
  billed figures.
- **FR-043**: The console MUST NOT present a total that combines figures
  expressed in different currencies.
- **FR-044**: Producing an estimate MUST NOT require any credential, any call to
  a commerce or billing service, or any write to a cloud resource.

### Cross-Cutting

- **FR-045**: All behaviour introduced by this feature MUST remain read-only
  with respect to cloud resources.
- **FR-046**: Every affected view MUST continue to function when the operator's
  scope grants access to only part of the telemetry, reporting reduced coverage
  rather than failing.
- **FR-047**: Interactive controls introduced by this feature MUST be operable by
  keyboard and MUST expose their state to assistive technology.
- **FR-048**: Controls and labels introduced by this feature MUST render legibly
  in both the light and dark presentation of the console.

### Key Entities

- **Scope filter**: A named dimension of the telemetry — Foundry resource,
  project, agent, model, tool, or run key — with an observable option set, a
  position in the cascade hierarchy, and a set of selected values.
- **Window selection**: Either a named relative duration or an explicit fixed
  interval, together with the timezone in which it is presented.
- **Entity summary**: A labelled group of headline figures belonging to one
  entity family, stating the entity it measures and its coverage state.
- **Run row**: One observed run, carrying its identifying key, its telemetry
  source, its timing, its turn count, its token consumption, and its cost
  estimate.
- **Price entry**: A unit price for one model and one token type, with a
  currency and the effective date of the price reference that contains it.
- **Cost estimate**: A monetary figure for a run or for a group of runs, with its
  currency, its completeness state (complete, partial, or not priced), the
  components excluded from it, the count of covered runs it could not price when
  it covers a group, and the price reference version it was computed from.

## Success Criteria *(mandatory)*

- **SC-001**: An operator who has never seen the environment before can narrow
  the console to a single agent in three interactions or fewer, without knowing
  or typing any identifier.
- **SC-002**: Scope and window selections survive every tab change without
  re-entry, in 100% of navigations.
- **SC-003**: On first load with no saved state, every tab reports over the last
  seven days.
- **SC-004**: Changing the reporting window takes a single interaction for each
  of the eight named durations.
- **SC-005**: Every time value visible on a page is expressed in one stated
  timezone, with zero mixed-timezone screens.
- **SC-006**: Every headline figure on the Overview names the entity it counts,
  with zero unqualified counts or rates remaining.
- **SC-007**: An operator can place the complete run key or the complete
  telemetry source on the clipboard in one interaction.
- **SC-008**: The columns needed to identify and triage a run are readable
  without horizontal scrolling on a standard laptop display.
- **SC-009**: Every run whose token usage is covered by the price reference
  displays an estimate carrying its currency and the date of its price basis.
- **SC-010**: No estimated figure is ever displayed without its completeness
  state and its estimate disclaimer.
- **SC-011**: With scope option loading, per-entity summaries, and cost
  estimation all present, every Observe view reaches a complete rendered state
  within three seconds for a scope containing 1,000 runs.
- **SC-012**: Every control introduced by this feature is fully operable by
  keyboard alone.
- **SC-013**: A scope filter presents its options within one second in a scope
  containing 1,000 distinct agents, and any value in that scope is reachable
  through search.
- **SC-014**: An applied scope survives a page reload and reproduces on another
  machine from the console address alone, and no generative-AI content field
  appears in that address.
- **SC-015**: An estimate derived from a price reference more than ninety days
  past its effective date is displayed and carries a stale marker stating the
  reference's age.
- **SC-016**: Every rolled-up estimated cost equals the sum of the estimates of
  the priced runs it covers, reports the count of runs it could not price, and
  covers the entity's full scope rather than only the displayed run rows.
- **SC-017**: In a scope whose result set reaches the console's row bound, the
  Runs view still reaches a complete rendered state and stays interactive, and
  it states how many rows in scope are not displayed.

## Assumptions

- **Timezone**: Times are presented in the viewer's own local timezone. This is a
  change of behaviour, not an exposure of existing behaviour: the console
  currently renders timestamps in UTC, and only the custom-window inputs operate
  in local time. Moving every display onto the local basis is therefore the work,
  and it is chosen over standardising on UTC because the operator reasons about
  their own working day.
- **Default window**: The seven-day default is already the effective behaviour;
  this feature exposes it as an explicit, visible preset rather than changing it.
- **Filter option source**: Scope options are derived from telemetry already
  observable within the current scope and window, so no new data source or
  credential is introduced, and the option set naturally shrinks and grows with
  the window.
- **Cascade direction**: The hierarchy is strictly left to right — resource,
  project, agent, model, tool, run key. Selections never restrict a filter to
  their left.
- **Scope persistence**: The console address is the only place an applied scope
  is retained. This makes a scope shareable and reproducible, keeps browser
  history working as an undo for filter changes, and guarantees the console
  never opens into a stale scope the operator has forgotten applying. The
  existing prohibition on placing generative-AI content fields in the address
  continues to apply and constrains what may be carried there.
- **Overview granularity**: The review document left open whether the Overview
  should show one summary per entity family or stay lean. This specification
  chooses per-entity summaries with runs first, bounded by FR-024 and SC-011 so
  the expansion cannot cost extra telemetry retrieval or rendering time. If that
  bound cannot be met during planning, the runs summary is the one that must
  survive.
- **Correlation column**: The review document questioned whether the correlation
  dimension deserves a column when it only ever shows one value. This
  specification generalises that into FR-033 — any single-valued dimension is
  stated once above the table instead of consuming a column — so the column
  disappears exactly when it carries no information and returns automatically if
  a second value ever appears.
- **Price basis**: Estimates use public list prices published with the
  accelerator, in a single currency per price entry. They are explicitly not
  negotiated prices, not invoice amounts, and not a forecast.
- **Price maintenance**: The price reference is maintained as part of the
  accelerator and refreshed on each release; no operator action is required to
  obtain prices. Ninety days is the point past which a shipped reference stops
  being presented as current, chosen to tolerate a release or two of lag while
  still flagging a genuinely abandoned reference. A stale reference is still
  useful for triage, so the figure remains visible and is labelled rather than
  withheld.
- **Non-token cost components**: Only components derivable from observed
  telemetry are estimated. Components such as hosted-agent compute or
  tool-side services, which are not observable per run from telemetry, are named
  as exclusions on a partial estimate rather than guessed. Attributing money
  actually billed for those components remains the responsibility of the
  existing declared-billed-total allocation capability, which this feature does
  not modify.
- **Relationship to billed cost allocation**: Estimated cost answers "roughly
  what did this run consume, at list price?" without any operator configuration.
  Declared-billed-total allocation answers "how do I attribute money I have
  already been billed?" and requires configuration. Both may be visible at once
  and are never combined into a single figure.
- **Copy affordance**: The copy control uses the standard clipboard capability
  of the browser; when that capability is unavailable, the full value is offered
  for manual selection instead.
- **Read-only guarantee**: Nothing in this feature creates, mutates, or deletes a
  cloud resource, and nothing requires a new credential.

## Out of Scope

- Changing how telemetry is collected, retained, or exported.
- Adding new telemetry dimensions that are not already observed.
- Modifying the declared-billed-total cost allocation contract or its
  configuration surface.
- Forecasting future spend or recommending cost reductions.
- Editing, uploading, or overriding the price reference from within the running
  console.
- Any change to Doctor, evaluation execution, or release evidence.
- Distinguishing where an agent runs beyond the runtime kinds already
  classified. The console already carries a Runtime column fed by the observed
  provider and system attributes, and that column is the natural home for a
  finer distinction between hosting environments. Widening it to name a cloud or
  an on-premises deployment requires new classification rules and telemetry that
  does not exist in any currently observed workload, so it cannot be verified
  here and belongs to its own feature.
