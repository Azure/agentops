# Quickstart: Validating Observe Console Refinements

**Feature**: `014-observe-console-refinements` | **Date**: 2026-08-31

This is a validation guide, not an implementation guide. It describes how to
prove the feature works end to end once it is built. Entity fields and interface
shapes are not repeated here — see [`data-model.md`](./data-model.md) and
[`contracts/observe-console.md`](./contracts/observe-console.md).

---

## Prerequisites

- Python 3.11 or later.
- The package installed in editable mode from the repository root.
- For the automated checks: no Azure credential and no network access. Every
  test in the Observe suites runs against mocked telemetry.
- For the manual checks only: a workspace with a Foundry project endpoint and an
  Application Insights connection string, and a signed-in Azure CLI session. Runs
  must exist in the observable scope, otherwise there is nothing to price and the
  cost scenarios cannot be exercised.

---

## Automated validation

Run the Observe suites first. They are fast and require no credentials.

```powershell
$env:PYTHONPATH = "src"
python -m pytest tests/unit/test_observe_ui.py tests/unit/test_observe_ui_visual.py tests/unit/test_observe_queries.py tests/unit/test_observe_service.py -q
```

Then the full suite, which is the gate that matters:

```powershell
python -m pytest tests/ -x -q
```

### Regenerating visual snapshots

Any change to the rendered document breaks the golden snapshots. That is the
point — the break is the signal. Regenerate only after reading the diff and
confirming every change is intended.

```powershell
$env:PYTHONPATH = "src"
$env:AGENTOPS_UPDATE_SNAPSHOTS = "1"
python -m pytest tests/unit/test_observe_ui_visual.py -q
Remove-Item Env:\AGENTOPS_UPDATE_SNAPSHOTS
```

Inspect the regenerated files under `tests/unit/__snapshots__/` before staging
them, and confirm line endings did not change. A snapshot diff that is entirely
line-ending churn hides the real change and must not be committed.

---

## Manual validation

Start the console from a configured workspace:

```powershell
agentops cockpit
```

If the port is already held by an earlier console, stop that process before
starting a new one. Then open the Observe page and work through the scenarios
below.

### Scenario 1 — Selecting scope without typing an identifier

*Proves user story 1; exercises FR-001 through FR-008 and SC-001.*

1. Open Observe with no scope applied.
2. Open the Foundry resource control. It presents the resources observed in the
   current window as selectable, recognisable values.
3. Select one. Open the project control: it offers only projects under that
   resource.
4. Select a project, then an agent, using selection only.
5. Confirm you reached a single agent's data without typing an identifier, and
   without opening any other tool to look one up.
6. Open a control in a scope with many distinct values. Confirm it stays
   responsive, states that it is showing a subset, and that typing a fragment
   finds a value that was not in the initial list.
7. Change a left-hand selection. Confirm the controls to its right reset rather
   than retaining a now-impossible value.

### Scenario 2 — Choosing a window

*Proves user story 2; exercises FR-009 through FR-014, SC-003 and SC-004.*

1. Load Observe fresh. Confirm the window is seven days and that this is shown
   as a selected preset, not merely implied by the boundaries.
2. Confirm all eight named durations are present and that each is one
   interaction away.
3. Select a shorter preset. Confirm every tab honours it and that switching tabs
   does not reset it.
4. Leave a preset selected and wait for an automatic refresh, or trigger a
   manual one. Confirm the window moved with the present moment rather than
   staying anchored where it was when selected. **This is the scenario most
   likely to regress**: a preset frozen at selection looks correct on first load
   and only reveals itself as stale after a refresh cycle.
5. Choose Custom, enter an explicit interval, and confirm exactly that interval
   is applied.
6. Enter a custom interval whose end is not after its start. Confirm it is
   refused with an explanation and that no query is issued.

### Scenario 3 — Reading times consistently

*Proves user story 2's presentation half; exercises FR-015 through FR-019 and SC-005.*

1. Compare the window boundaries, a row timestamp, and the refresh indicator on
   the same page. All three read in the same basis.
2. Confirm the basis is stated once, next to the time controls, and is not
   repeated on every value.
3. Confirm the refresh indicator sits to the right of the filter controls and is
   visually quieter than the data it describes, using an abbreviated date form.
4. Load the page and read the indicator before the first refresh completes.
   Confirm it says so explicitly rather than showing an empty or placeholder
   time.

### Scenario 4 — Reading the Overview

*Proves user story 3; exercises FR-020 through FR-025, SC-006 and SC-011.*

1. Open Overview. Confirm figures are grouped by the entity family they describe
   and that the runs family appears first.
2. Read each headline figure's label. Confirm it names what it counts — a run
   count is not ambiguous with a turn count.
3. Find a dimension with no telemetry. Confirm it reads as not reported and is
   visibly distinct from a reported zero.
4. Time the load against a scope with roughly a thousand runs. Confirm it stays
   within a few seconds and that the added summaries did not introduce another
   retrieval round-trip.

### Scenario 5 — Working the Runs table

*Proves user story 4; exercises FR-026 through FR-033, SC-007, SC-008 and SC-012.*

1. Confirm identification and triage columns are readable without horizontal
   scrolling at a normal window width.
2. Copy a run key using its copy affordance. Confirm one interaction puts the
   full value on the clipboard.
3. Disable clipboard access in the browser, reload, and repeat. Confirm the full
   value is presented for manual selection rather than the affordance failing
   silently.
4. Expand a row's detail. Confirm it opens inline beneath the row, does not
   navigate away, and does not consume a table column of its own.
5. Sort by every sortable column, including any that was renamed. **Confirm
   sorting still works on the renamed columns** — this is the specific regression
   the column declaration exists to prevent.
6. Apply a scope that leaves one value in a column. Confirm the column is
   replaced by a single statement above the table, and that it returns as a
   column once a second value appears.
7. Complete steps 2, 4 and 5 using the keyboard alone.

### Scenario 6 — Reading estimated cost

*Proves user stories 5 and 6; exercises FR-034 through FR-044 and SC-009, SC-010, SC-015, SC-016.*

1. Open Runs with a scope containing priced models. Confirm each run shows an
   estimated cost with its currency and the price basis date.
2. Confirm every figure carries a completeness state and states it is an estimate
   at published list prices.
3. Find a run whose model is not in the price reference. Confirm it reads as not
   priced with a reason, and that it is not silently shown as zero.
4. Find a run spanning more than one model. Confirm its estimate prices each
   model's tokens at that model's own rates rather than applying one model's
   rates to the run's combined totals, and that it is not degraded to partial
   merely for having used several models.
5. Compare an agent's rolled-up estimate to its runs. Confirm the total equals
   the sum of the priced runs and that the count of unpriced runs is stated.
6. Confirm the estimate is presented separately from the declared billed-cost
   allocation and that the two are never added together.
7. Temporarily rename or remove the packaged price reference and reload. Confirm
   the console still renders, cost figures degrade to not priced with a reason,
   and nothing raises.
8. Confirm nothing in the console offers to edit, upload, or overwrite the price
   reference.

### Scenario 7 — Sharing a view

*Proves the address contract; exercises SC-014.*

1. Apply scope and a preset window. Copy the address.
2. Reload. Confirm the same scope is applied.
3. Open the same address on another machine. Confirm the same scope is applied
   and that the preset resolves relative to that machine's present moment.
4. Repeat with a custom interval. Confirm the exact interval is reproduced.
5. Read the address. Confirm no prompt text, model output, or other generative
   content appears in it, and that no facet search text was carried.
6. Inspect browser storage. Confirm nothing was written to local storage, session
   storage, or cookies.

### Scenario 8 — Packaged distribution

*Proves the packaging contract for the price reference.*

Build and install into a clean environment, then confirm the price reference is
present and cost estimates render. A price reference that works from a source
checkout but is missing from a built wheel is the most likely packaging defect
for this feature, and it fails silently — every cost figure simply degrades to
not priced.

---

## Documentation obligations

Two user-visible changes carry documentation obligations under the project
constitution and must be complete before the change is proposed:

- Renamed Runs table columns, and the time presentation basis, both documented in
  the Observe documentation.
- Both recorded in the changelog.
