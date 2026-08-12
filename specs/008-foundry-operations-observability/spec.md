# Feature Specification: Foundry Operations Observability

**Feature Branch**: `placerda-spec-kit-feature`

**Created**: 2026-08-12

**Status**: Implemented Baseline

**Input**: This specification was reverse-engineered from the current implementation, tests, and public documentation of AgentOps Toolkit. It documents the existing Foundry operations observability workbook and Doctor posture rule behavior as-built, not a new proposal. Sources reviewed include `src/agentops/services/dashboard.py`, `src/agentops/templates/workbooks/`, `src/agentops/agent/checks/posture_rules/aoai_diagnostic_categories.py`, `src/agentops/agent/cockpit.py`, `docs/foundry-ops-workbook*.md`, and associated tests.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Deploy a ready-made Azure OpenAI operations workbook (Priority: P1)

An operator wants a single command to deploy a pre-built Azure Monitor Workbook (`foundry-ops`) covering Azure OpenAI capacity, traffic, latency, and error signals into their own subscription, without hand-authoring KQL queries or workbook JSON.

**Why this priority**: Deploying the ready-made workbook is the primary deliverable; every other capability (opening, exporting, gating) depends on this workbook existing.

**Independent Test**: Can be fully tested by running `agentops telemetry dashboard deploy` against a target subscription/resource group/workspace and confirming a workbook resource is created whose content matches the packaged `foundry-ops` template, with the expected parameters (subscription, workspace, Azure OpenAI resource, deployment, model, time range, streaming) applied.

**Acceptance Scenarios**:

1. **Given** a target subscription, resource group, Log Analytics workspace, and Azure OpenAI resource are supplied, **When** `agentops telemetry dashboard deploy` is run, **Then** an Azure Monitor Workbook resource is created or updated using the packaged `foundry-ops` template content.
2. **Given** the deployed workbook, **When** it is opened, **Then** it exposes parameters for subscription, workspace, Azure OpenAI resource, deployment, model, time range, and a streaming toggle.
3. **Given** the deployed workbook, **When** its sections are reviewed, **Then** it presents Capacity, Traffic and tokens, Latency, and Errors and throttling sections backed by the packaged KQL query assets (`capacity_ptu_spillover`, `traffic_tokens`, `latency_percentiles`, `errors_throttling`).
4. **Given** a target environment where RBAC preflight conclusively finds a missing required role, **When** `agentops telemetry dashboard deploy` is run, **Then** the command reports the specific missing permission and stops before deploying the workbook.
5. **Given** a target environment where the Azure OpenAI resource is missing one or more required diagnostic log categories, **When** `agentops telemetry dashboard deploy` is run, **Then** the command prints the exact command needed to enable the missing category as a non-fatal advisory and still deploys the workbook.

---

### User Story 2 - Verify diagnostic settings are configured for observability (Priority: P2)

An operator wants Doctor to flag when an Azure OpenAI resource's diagnostic settings are missing the log categories the `foundry-ops` workbook depends on, so the gap is caught before the workbook is deployed or relied upon.

**Why this priority**: The workbook is only useful if the underlying diagnostic data exists; this check closes the loop between deployment and data availability, but is secondary to the deployment capability itself.

**Independent Test**: Can be fully tested by running `agentops doctor` against a workspace whose discovered Azure OpenAI resource has diagnostic settings missing one or more required log categories, and confirming a finding for rule `waf.observability.aoai_diagnostic_categories` is reported naming the missing categories.

**Acceptance Scenarios**:

1. **Given** an Azure OpenAI resource with diagnostic settings missing a required log category, **When** `agentops doctor` runs, **Then** a finding for `waf.observability.aoai_diagnostic_categories` is reported identifying the missing category or categories.
2. **Given** an Azure OpenAI resource with all required diagnostic log categories enabled, **When** `agentops doctor` runs, **Then** no finding for that rule is reported.
3. **Given** a missing-category finding, **When** its detail is inspected, **Then** it includes a runnable command suggestion for enabling the missing diagnostic category.

---

### User Story 3 - Open or export the workbook definition without leaving the CLI (Priority: P3)

An operator wants to open the deployed workbook directly in the Azure portal from the command line, or export the workbook's JSON definition locally (for example, to version it or adapt it), without needing to locate it manually in the portal.

**Why this priority**: Convenience access to an already-deployed or packaged workbook is useful but secondary to deployment and diagnostic verification.

**Independent Test**: Can be fully tested by running `agentops telemetry dashboard open --print-url` and confirming a portal URL for the deployed workbook resource is produced, and by running `agentops telemetry dashboard export --out <path>` and confirming the packaged workbook JSON is written to that path.

**Acceptance Scenarios**:

1. **Given** a previously deployed workbook, **When** `agentops telemetry dashboard open --print-url` is run, **Then** a valid Azure portal URL referencing that workbook resource is printed.
2. **Given** no prior deployment, **When** `agentops telemetry dashboard export --out <path>` is run, **Then** the packaged `foundry-ops` workbook JSON is written to the given path unchanged.
3. **Given** the Cockpit dashboard is rendered, **When** a workbook has been deployed for the workspace, **Then** Cockpit surfaces a tile or link to that workbook (see Read-Only Cockpit specification).

---

### Edge Cases

- What happens when `agentops telemetry dashboard deploy` is run without the Azure CLI (`az`) available? The command MUST fail with a clear error identifying the missing prerequisite rather than a raw stack trace.
- What happens when the target resource group already contains a workbook with the same name? The deploy command MUST update the existing resource using an idempotent ARM template rather than creating a duplicate.
- What happens when the caller lacks RBAC permission to read the Azure OpenAI resource's diagnostic settings and this can be conclusively determined? The `check_rbac` preflight MUST report the specific missing role/permission and MUST block deployment.
- What happens when RBAC status cannot be conclusively determined (for example, the identity-listing call itself fails)? The preflight MUST fail open with a warning rather than blocking deployment, since ARM's own authorization check still applies at deploy time.
- What happens when `--dry-run` is passed to `deploy`? The command MUST resolve the target's local/azd metadata and emit the same ARM template that would be deployed, without making any Azure API call that creates or modifies a resource.
- What happens when the Doctor posture rule cannot discover any Azure OpenAI resource in scope? The rule MUST report an appropriately scoped outcome (no applicable resource) rather than a false-positive missing-category finding.
- What happens when one or more required diagnostic log categories are missing at deploy time? `dashboard deploy` MUST treat this as a non-fatal advisory: it prints the exact command to enable the missing category and still completes the deployment, rather than refusing to proceed.
- What happens when `dashboard export` is run with no `--out` value? The command MUST fall back to a documented default output path.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST package a `foundry-ops` Azure Monitor Workbook template covering Capacity, Traffic and tokens, Latency, and Errors and throttling sections for Azure OpenAI resources.
- **FR-002**: The packaged workbook MUST expose parameters for subscription, Log Analytics workspace, Azure OpenAI resource, deployment, model, time range, and a streaming toggle.
- **FR-003**: The system MUST provide `agentops telemetry dashboard deploy` to create or update the workbook resource in a target subscription/resource group using an idempotent ARM template built from the packaged workbook content.
- **FR-004**: The system MUST perform an RBAC preflight check before deployment and MUST block deployment with a specific missing-permission error only when the check conclusively finds a missing required role; when the check cannot conclusively determine RBAC status, it MUST fail open with a warning rather than blocking.
- **FR-005**: The system MUST perform a diagnostic-settings preflight check before deployment and MUST treat a missing required diagnostic log category as a non-fatal advisory - printing the exact command to enable it while still attempting the deployment.
- **FR-006**: The system MUST provide a `--dry-run` mode for `dashboard deploy` that resolves target metadata and emits the intended ARM template without creating or modifying any Azure resource.
- **FR-007**: The system MUST provide `agentops telemetry dashboard open` to produce a portal URL for the deployed workbook, including a `--print-url` mode.
- **FR-008**: The system MUST provide `agentops telemetry dashboard export` to write the packaged workbook JSON to a local path unchanged, with a documented default path when `--out` is omitted.
- **FR-009**: The system MUST implement a Doctor posture rule (`waf.observability.aoai_diagnostic_categories`) that reports a finding identifying any Azure OpenAI diagnostic log category required by the workbook queries that is not enabled.
- **FR-010**: The Doctor posture rule MUST include an actionable fix-command suggestion in its finding detail for enabling a missing diagnostic category.
- **FR-011**: The Cockpit dashboard view MUST surface a tile or link to a deployed `foundry-ops` workbook when one exists for the workspace, consistent with the Read-Only Cockpit specification.

### Key Entities

- **DashboardTarget**: The resolved subscription, resource group, workspace, and Azure OpenAI resource identifiers used for workbook deployment.
- **RBAC Preflight Result**: The conclusive allowed/blocked or inconclusive outcome of the workbook-writer role check; only a conclusive missing-role result blocks before deployment.
- **Diagnostic-Settings Advisory**: The non-blocking result of inspecting required Azure OpenAI diagnostic categories, including the exact remediation command when a category is absent.
- **Workbook Template**: The packaged `foundry-ops.workbook.json` content defining the workbook's parameters and sections.
- **KQL Query Asset**: One of the packaged query files (`capacity_ptu_spillover`, `traffic_tokens`, `latency_percentiles`, `errors_throttling`) backing a workbook section.
- **Posture Finding**: A Doctor finding produced by the `waf.observability.aoai_diagnostic_categories` rule identifying a missing diagnostic log category.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The ARM template produced by `dashboard deploy` (including its `--dry-run` mode) always embeds the exact packaged `foundry-ops` workbook content - the rendered workbook a user sees always matches the packaged template.
- **SC-002**: When the RBAC preflight conclusively finds a missing required role, deployment always stops before any workbook is created or updated; when RBAC status cannot be conclusively determined, deployment always proceeds with a warning rather than blocking.
- **SC-003**: The Doctor posture rule reports a finding naming the missing category whenever an Azure OpenAI resource's diagnostic settings omit a required category, and reports no finding when all required categories are present - the two outcomes never overlap for the same resource state.
- **SC-004**: `dashboard export` always writes the packaged workbook JSON unchanged to the requested path, or to the documented default path when `--out` is omitted.
- **SC-005**: `dashboard open --print-url` always produces a well-formed Azure portal URL referencing the deployed workbook resource.
- **SC-006**: When deployment is otherwise permitted, a missing required diagnostic category does not block the workbook deployment attempt and the exact remediation command is printed; diagnostic-category absence alone never produces a blocking preflight result.
- **SC-007**: `dashboard deploy --dry-run` never creates or modifies an Azure resource, regardless of RBAC or diagnostic-category state.

## Assumptions

- The operator has (or can obtain) the Azure RBAC permissions the preflight check identifies as required; this feature surfaces missing permissions but does not grant them.
- The packaged KQL queries assume Azure OpenAI diagnostic logs are (or will be) sent to the target Log Analytics workspace; the workbook itself does not configure diagnostic settings, only reports on whether they are correctly enabled (via the Doctor posture rule).
- This feature is scoped to Azure OpenAI operational telemetry; it does not cover telemetry for other Foundry model types beyond what the packaged queries already target.

## Out of Scope

- Creating Azure Monitor alert rules or action groups; the workbook and CLI surface visualization and readiness signals only, not alerting.
- A general-purpose `agentops telemetry monitor setup|show|configure` command family; only `dashboard deploy|open|export` are implemented.
- Automatically remediating a missing diagnostic category; the posture rule surfaces a fix-command suggestion but does not execute it.
- Live or streaming trace visualization; the workbook renders Azure Monitor Log Analytics query results, not a live trace feed.
- Deployment or observability support for non-Azure-OpenAI Foundry model types beyond what the packaged `foundry-ops` queries already cover.

## Implementation Evidence

- `src/agentops/services/dashboard.py` - `DashboardTarget`, `PreflightResult`, `load_workbook_template()`, `load_workbook_content()`, `build_arm_template()`, `deploy_workbook()`, `check_rbac()`, `missing_diagnostic_categories()`, `build_diagnostic_settings_command()`, `build_workbook_portal_url()`, `discover_target()`.
- `src/agentops/templates/workbooks/foundry-ops.workbook.json` - packaged workbook definition with Capacity, Traffic and tokens, Latency, and Errors and throttling sections and parameters for subscription/workspace/resource/deployment/model/time range/streaming.
- `src/agentops/templates/workbooks/queries/capacity_ptu_spillover.kql`, `traffic_tokens.kql`, `latency_percentiles.kql`, `errors_throttling.kql` - packaged KQL query assets backing the workbook sections.
- `src/agentops/agent/checks/posture_rules/aoai_diagnostic_categories.py` - `RULE_ID = "waf.observability.aoai_diagnostic_categories"`, `evaluate()` producing findings for missing diagnostic categories, and `_fix_command()` producing the actionable remediation suggestion.
- `src/agentops/agent/cockpit.py` - dashboard/workbook tile rendering in the Cockpit view.
- `src/agentops/cli/app.py` - `telemetry dashboard deploy|open|export` command wiring, including `--dry-run` and `--print-url`.
- `tests/unit/test_dashboard.py`, `test_cli_dashboard.py`, `test_agent_checks_observability.py`, `test_agent_posture_rules.py` - unit coverage of workbook deployment, preflight checks, and the diagnostic-categories posture rule.
- `docs/foundry-ops-workbook.md` and related `docs/foundry-ops-workbook*.md` - narrative documentation of the workbook's sections and deployment flow consistent with this baseline.
