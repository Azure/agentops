# Quickstart: validating the Observe tools and runs views

**Feature**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md) | **Data model**: [data-model.md](./data-model.md) | **Contract**: [contracts/observe-api.additions.openapi.yaml](./contracts/observe-api.additions.openapi.yaml)

This is a validation guide, not an implementation guide. It lists the scenarios that prove the feature works end to end and the exact commands to run them. Field definitions live in the data model; wire shapes live in the contract. Neither is repeated here.

---

## Prerequisites

| Requirement | Check |
| --- | --- |
| Python 3.11+ | `python --version` |
| AgentOps installed from this worktree | `python -m pip install -e .` |
| Azure CLI signed in (scenarios 3–7 only) | `az account show` |
| A Foundry project with Application Insights and recent agent traces | Required for scenarios 3–7 |

Scenarios 1 and 2 are offline and require no Azure access. Run them first — they catch most regressions without any cloud dependency.

---

## Scenario 1 — Contract and unit suites (offline)

Proves the new models, query builders, service dispatch, and UI constants agree with each other.

```powershell
python -m pytest tests/unit/test_observe_models.py tests/unit/test_observe_queries.py tests/unit/test_observe_adapters.py tests/unit/test_observe_service.py tests/unit/test_observe_facade.py tests/unit/test_observe_ui.py tests/unit/test_observe_scope_contract.py -q
```

**Expected**: all pass.

Two existing tests in `tests/unit/test_observe_ui.py` assert full coverage of the view enums and **will fail until they are updated** for `tools` and `runs`:

- `test_observe_views_and_labels_cover_all_required_surfaces`
- `test_observe_view_wire_names_map_internal_ids_to_openapi_view_enum`

That failure is the intended signal that the internal view list, the label map, and the wire-name map were not all updated together. A green run here means the four separate view lists (request literal, service literal, UI internal list, UI wire map) are in agreement.

---

## Scenario 2 — Full suite (offline)

```powershell
python -m pytest tests/ -x -q
```

**Expected**: all pass, with no new failures outside the Observe modules.

Watch specifically for collateral damage from the runtime-value change: any test asserting `source_kind == "foundry"` or `"external"` is asserting against values that no longer exist and must be updated to the refined set. See the mapping table in [plan.md](./plan.md#complexity-tracking).

---

## Scenario 3 — Launch Cockpit and reach the new views

```powershell
agentops cockpit
```

Then open the printed URL and navigate to `#tools` and `#runs`.

**Expected**:

- Both views appear in the Observe navigation with human-readable labels, not raw identifiers.
- Each renders a table or an explicit empty-state message. A blank panel is a failure.
- Switching views updates the URL fragment, and reloading the page lands on the same view.

---

## Scenario 4 — Tools view content (US1)

With the tools view open and a time range covering known agent activity:

**Expected**:

- One row per tool, showing invocation count, failure count, last-seen time, the owning agent, and the originating telemetry source.
- Two sources reporting the same agent and tool produce two rows that are visibly distinguishable by source — they are never merged into one (FR-023).
- Latency renders as an explicit "not measured" indicator when absent — **never** as `0`.
- No token columns appear. Their absence is intentional; see [data-model.md §2](./data-model.md#2-new-entity-observedtool).
- No tool arguments, results, or message bodies appear anywhere. Protected content is never read by this path.

**Failure signals**: a `0` where a value was simply not collected; two sources collapsed into one indistinguishable row; any column showing tool payload text.

---

## Scenario 5 — Runs view content and granularity (US2)

**Expected**:

- One row per correlated run with turn count, tool-invocation count, duration, status, and the originating telemetry source.
- Every row displays which correlation formed it (conversation vs. single trace). Mixed granularity in one table is correct behaviour, not a bug — but it must be visible.
- Token totals appear at run level and render as "not available" when no activity inside the run reported usage — **never** as `0`.
- A run containing any failed turn or failed tool invocation shows as failed, even if a later turn succeeded.
- Runs whose last activity is near the window end show as in progress rather than being reported as complete.
- Start, duration and turn count are labelled as scoped to the selected range. A run that began before the window is **not** flagged as truncated — that is the accepted limitation in FR-012A, not a defect.

To verify the in-progress boundary, query a window ending "now" while an agent is actively running, then re-query a few minutes later: the same run should move from in-progress to a settled status.

To verify the window-scoped labelling, query a short range that starts in the middle of a known long run: the reported duration should be shorter than the run's real duration, and the view must say the values are range-scoped rather than presenting them as absolute.

---

## Scenario 5B — Agents view source attribution (FR-023A)

Open `#agents` for a scope in which **two distinct telemetry sources** report activity for the same agent.

**Expected**:

- Each agent row identifies the telemetry source it came from, in addition to the existing runtime-kind badge.
- The same agent seen through two sources appears as two rows an operator can tell apart, not as two identical-looking rows.
- The source indicator is present in both the server-rendered table and after a client-side refresh; a column that appears on load and disappears on refresh means the JavaScript twin was not updated.

**Failure signals**: rows show only a runtime-kind badge with no source; two rows are visually indistinguishable; the column exists in one render path only.

---

## Scenario 6 — Filters and URL round-trip (US1/US2)

1. Apply a tool-name filter in the tools view.
2. Copy the URL, open it in a new tab.
3. Repeat with a run-key filter in the runs view.

**Expected**:

- The filter appears in the URL query string and is restored on load.
- Results narrow; they never widen.
- An empty or whitespace-only filter value is rejected rather than silently treated as "no filter".
- Combining a new filter with the existing agent, model, and time-range filters narrows further and does not error.

**Security check**: a filter value containing KQL metacharacters (for example `a' | take 1 //`) must produce either an empty result or a validation error — never a broadened result set, and never a server error revealing query text.

---

## Scenario 7 — Row bound, coverage, and scope (US3, US4)

**Row bound (FR-028A)** — query a window with more than 500 distinct tools or runs:

- The view reports how many rows are shown out of how many exist in scope.
- If the total genuinely cannot be determined, the view says so. It must **never** display a total equal to the shown count as a stand-in.

**Coverage (US3)** — point at a source with telemetry but without tool or conversation attributes:

- The coverage view names the specific missing dimension (`tool_attribution` or `run_correlation`) with a reason and a next action.
- The corresponding table shows an explained empty state, not a silent blank.

**Runtime attribution (US4)**:

- Agents display one of the refined runtime values from [RuntimeKind](./data-model.md#1-new-shared-type-runtimekind).
- Agents that cannot be classified display `unknown`. This is correct behaviour — an `unknown` badge is a successful outcome, not a defect.

**Scope enforcement** — confirm no resource outside the configured Observe scope ever appears, in any of the new views, under any filter combination. This is the non-negotiable check; a scope leak invalidates the feature regardless of every other result.

---

## Scenario 8 — Documentation consistency

```powershell
python -m pytest tests/integration/test_observe_end_to_end.py tests/integration/test_cockpit_hosted.py -q
```

**Expected**: pass. Then confirm by inspection that `docs/observe.md` lists six views rather than four, and that `CHANGELOG.md` carries a breaking-change entry for the replaced runtime values with the old-to-new mapping.

A green test suite with stale docs still fails this scenario: the runtime-value change ships in a single release with no dual-emission window, so the changelog entry and the mapping table are the only migration aid users get.

---

## Completion criteria

| Scenario | Proves |
| --- | --- |
| 1, 2 | Contracts, builders, dispatch, and UI constants agree; no collateral regressions |
| 3 | Both views are reachable and never render blank |
| 4 | US1 — tool inventory correct; absent is not zero; no content exposure |
| 5 | US2 — run correlation, granularity visibility, failure and completeness semantics |
| 5B | FR-023A — the pre-existing agents view attributes every row to a telemetry source |
| 6 | Filters narrow only, round-trip through the URL, and resist injection |
| 7 | US3/US4 — truncation honesty, explained gaps, refined runtime values, scope integrity |
| 8 | Docs and changelog carry the breaking change |
