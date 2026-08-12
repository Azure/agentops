# Feature Specification: Read-Only Cockpit

**Feature Branch**: `placerda-spec-kit-feature`

**Created**: 2026-08-12

**Status**: Implemented Baseline

**Input**: This specification was reverse-engineered from the current implementation, tests, and public documentation of AgentOps Toolkit. It documents the existing `agentops cockpit` local dashboard behavior as-built, not a new proposal. Sources reviewed include `src/agentops/agent/cockpit.py`, `production_telemetry.py`, the `cockpit` command in `src/agentops/cli/app.py`, and `tests/unit/test_cockpit.py` and related CLI cockpit tests.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See workspace readiness at a glance from local history (Priority: P1)

An operator runs `agentops cockpit` and opens a local web page that summarizes the workspace's eval and Doctor readiness status, built entirely from local result and history files already produced by prior `agentops eval run` and `agentops doctor` invocations.

**Why this priority**: Presenting a consolidated readiness view from history the operator already has is the entire value of Cockpit; every other capability (links, telemetry, next actions) is layered on top of this core view.

**Independent Test**: Can be fully tested by starting Cockpit against a workspace with known eval and Doctor history, confirming `GET /` returns the loading shell, then requesting `/?_partial=1` and confirming the hydrated page reflects the known readiness state, run count, and latest results without triggering a new eval or Doctor run.

**Acceptance Scenarios**:

1. **Given** a workspace with existing eval run history, **When** Cockpit's loading shell requests `/?_partial=1`, **Then** the hydrated response reflects the latest known eval readiness state derived from that history.
2. **Given** the same workspace, **When** `/api/eval-runs` is requested, **Then** it returns eval run summaries derived from results already present under `.agentops/results/`.
3. **Given** a workspace with existing Doctor history, **When** `/api/history` is requested, **Then** it returns the persisted Doctor analysis records, and the hydrated readiness page reflects the latest Doctor state.
4. **Given** a workspace with no eval or Doctor history at all, **When** Cockpit is started and `/?_partial=1` is requested, **Then** the page renders a defined empty/fallback state rather than raising an error.

---

### User Story 2 - Get contextual next actions and external links without leaving the local view (Priority: P2)

An operator viewing Cockpit wants to see recommended next actions (for example, "run an eval", "check Doctor findings") along with direct links out to the relevant Foundry, Azure Monitor, or workbook resource, so they can act without Cockpit itself performing any remote action.

**Why this priority**: Contextual guidance and external links make the readiness summary actionable, but they build on top of the core summary in User Story 1 rather than being independently central.

**Independent Test**: Can be fully tested by inspecting the rendered page or its API response for a workspace in a known state and confirming the next-action guidance and external links match what that state implies (for example, a workspace with a failed threshold surfaces a "review report" action and a link to the relevant run's report).

**Acceptance Scenarios**:

1. **Given** a workspace whose latest eval run failed a threshold, **When** the partial Cockpit page is rendered, **Then** it surfaces a next-action pointing the operator toward the failing run's report.
2. **Given** a workspace with a configured Foundry project endpoint, **When** the partial Cockpit page is rendered, **Then** it includes an outbound link to the corresponding Foundry resource rather than embedding Foundry's own UI.
3. **Given** a workspace with an Azure Monitor Workbook deployed via `agentops telemetry dashboard deploy`, **When** the partial Cockpit page is rendered, **Then** it includes a tile or link to that workbook.

---

### User Story 3 - View a specific run's report and deferred production telemetry (Priority: P3)

An operator wants to open a specific historical run's rendered report from Cockpit, and separately view production telemetry (for example, recent Application Insights activity) that is fetched only when requested rather than blocking the initial page load.

**Why this priority**: Drilling into a specific run and loading production telemetry are secondary, on-demand views that depend on the core summary already being available.

**Independent Test**: Can be fully tested by requesting `/api/runs/{run_id}/report` for a known run id and confirming the corresponding report is returned, and separately requesting `/api/production` and `/api/production/html` and confirming production telemetry is fetched only in response to that request rather than during the initial root page load.

**Acceptance Scenarios**:

1. **Given** a known historical run id, **When** `/api/runs/{run_id}/report` is requested, **Then** the rendered report for that specific run is returned.
2. **Given** an unknown or missing run id, **When** the same endpoint is requested, **Then** a defined not-found response is returned rather than an unhandled error.
3. **Given** production telemetry configuration is present, **When** `/api/production` is requested, **Then** telemetry is fetched at that time (deferred), not eagerly during Cockpit startup, loading-shell rendering, or partial readiness-page rendering.

---

### Edge Cases

- What happens when Cockpit is started in a workspace with no `agentops.yaml` at all? Cockpit MUST still start and serve its defined fallback/empty state rather than failing to start.
- What happens when production telemetry configuration (for example, an Application Insights connection string) is absent? The `/api/production` and `/api/production/html` endpoints MUST report that telemetry is unavailable rather than raising an unhandled error.
- What happens when a requested run id does not exist in history? The report endpoint MUST return a defined not-found response.
- What happens when history files are malformed or partially written? Cockpit MUST render its defined fallback state for the affected section rather than crashing the whole page.
- Does any Cockpit endpoint accept a request that mutates workspace state, cloud resources, or history? No; every exposed endpoint is a read (`GET`) operation over already-existing local data or external metadata.
- What happens when `--no-preflight` is passed to `agentops cockpit`? Cockpit MUST start without performing its startup preflight checks, while still serving the same read-only endpoints once running.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide `agentops cockpit` to start a local web server whose `GET /` response is a loading shell and whose `GET /?_partial=1` response presents a consolidated readiness view built from existing local eval and Doctor history, without requiring or triggering a new eval or Doctor run.
- **FR-002**: Every route exposed by Cockpit MUST be a read-only (`GET`) operation; the system MUST NOT expose any endpoint that creates, modifies, or deletes workspace files, Azure resources, or Foundry configuration.
- **FR-003**: The system MUST expose Doctor analysis history through `/api/history` and eval run summaries through the separate `/api/eval-runs` endpoint, each reflecting the corresponding local records already written by Doctor or eval execution.
- **FR-004**: The system MUST expose a per-run report endpoint (`/api/runs/{run_id}/report`) that renders the stored report for a specific historical run and MUST return a defined not-found response for an unknown run id.
- **FR-005**: The system MUST surface contextual next-action guidance on its hydrated partial page derived from the current readiness state (for example, pointing to a failing run's report or to Doctor findings).
- **FR-006**: The system MUST surface outbound links to external resources (Foundry project, Azure Monitor Workbook, and related dashboards) when the corresponding configuration or deployed resource is present, without embedding or proxying those external systems' own UIs.
- **FR-007**: The system MUST defer fetching production telemetry (`/api/production`, `/api/production/html`) until those endpoints are explicitly requested, rather than fetching it during Cockpit startup, loading-shell rendering, or partial readiness-page rendering.
- **FR-008**: The system MUST render a defined empty/fallback state for any section (eval history, Doctor history, production telemetry) when the corresponding local data or configuration is absent or unreadable, rather than raising an unhandled error.
- **FR-009**: The system MUST support `--host`, `--port`, `--workspace`, and `--no-preflight` options to control how and where Cockpit is served.
- **FR-010**: The system MUST provide a health-check endpoint (`/healthz`) suitable for verifying the local server is responsive.

### Key Entities

- **Readiness Summary**: The consolidated view of the workspace's latest eval and Doctor status, computed from local history rather than a live query.
- **Doctor Analysis Record**: A persisted Doctor run exposed through `/api/history` and used in the readiness view.
- **Evaluation Run Summary**: A persisted eval result (per `.agentops/results/<timestamp>/`) surfaced separately through `/api/eval-runs` and the readiness view.
- **Next Action**: A contextual recommendation (for example, "review failing report", "run Doctor") derived from the current readiness state.
- **External Link**: An outbound reference to a Foundry project, Azure Monitor Workbook, or similar external resource, rendered as a link rather than embedded content.
- **Production Telemetry Snapshot**: The deferred, on-demand result of querying production telemetry (for example, Application Insights) for the `/api/production` family of endpoints.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of routes registered by Cockpit are `GET` routes - no mutating endpoint (create, update, delete) is exposed anywhere in the served API.
- **SC-002**: Starting Cockpit against a workspace with existing eval and Doctor history returns a loading shell from `GET /`, while `GET /?_partial=1`, `/api/history`, and `/api/eval-runs` reflect their corresponding local records without fabricating state.
- **SC-003**: Starting Cockpit against a workspace with no eval or Doctor history still returns the loading shell and a defined hydrated empty/fallback state, never an error.
- **SC-004**: Requesting either the loading shell or the partial readiness page never triggers a production-telemetry fetch as a side effect; telemetry is retrieved only when `/api/production` or `/api/production/html` is explicitly requested.
- **SC-005**: Requesting a report for an unknown run id always returns a defined not-found response, never an unhandled exception.
- **SC-006**: No request to any Cockpit endpoint ever creates, modifies, or deletes a workspace file, Azure resource, or Foundry configuration.

## Assumptions

- Cockpit is intended for local, single-operator use (typically `localhost`) rather than as a multi-tenant hosted service; authentication/authorization beyond host/port binding is out of scope for this baseline.
- Cockpit's readiness summary is only as current as the local history it reads; it does not poll Foundry or Azure Monitor continuously, consistent with its read-only, on-demand design.
- The external links Cockpit renders assume the operator already has appropriate access to the linked Foundry/Azure resources; Cockpit does not manage or verify that access itself.

## Out of Scope

- Any endpoint or capability that mutates workspace files, Azure resources, or Foundry configuration; Cockpit is read-only by design.
- Real-time or continuously polling alerting; production telemetry is fetched on demand, not streamed or pushed.
- Triggering a new `agentops eval run` or `agentops doctor` invocation from within Cockpit itself.
- Embedding or proxying the full Foundry, Azure Monitor, or Application Insights UI; Cockpit only links out to those systems.
- Multi-user authentication, authorization, or remote-hosting concerns beyond local `--host`/`--port` binding.

## Implementation Evidence

- `src/agentops/agent/cockpit.py` - FastAPI route registrations: `GET /`, `GET /favicon.ico`, `GET /api/history`, `GET /api/eval-runs`, `GET /api/runs/{run_id}/report`, `GET /api/telemetry`, `GET /api/production`, `GET /api/production/html`, `GET /healthz` -- all `GET`-only, confirming no mutating endpoint exists.
- `src/agentops/agent/production_telemetry.py` - deferred production telemetry snapshot logic backing `/api/production` and `/api/production/html`.
- `src/agentops/cli/app.py` - `cockpit` command wiring `--host`, `--port`, `--workspace`, and `--no-preflight`.
- `src/agentops/services/preflight.py` - startup preflight checks shared by Cockpit and Doctor, skippable via `--no-preflight`.
- `tests/unit/test_cockpit.py` - unit coverage of Cockpit routes, history rendering, and fallback states.
- Additional CLI-level cockpit tests under `tests/unit/` covering the `cockpit` command surface and option handling.
