# Data Model: Deploy Hosted Cockpit

The hosted runtime is stateless. These models define configuration, deployment
preview, normalized telemetry, and API response contracts; they do not imply a
database.

## ObserveScope

Versioned non-secret discovery boundary serialized in
`AGENTOPS_OBSERVE_SCOPE`.

| Field | Type | Rules |
|---|---|---|
| `version` | integer | Required; exactly `1`. |
| `mode` | enum | `projects`, `foundry`, `resource_group`, or `subscription`. |
| `root_resource_id` | string or null | Required for parent modes; canonical lowercase-insensitive ARM ID. |
| `project_resource_ids` | array of strings | Required and non-empty only for `projects`; unique canonical project ARM IDs. |
| `default_project_resource_id` | string or null | Optional; when set, must be in `project_resource_ids` or under `root_resource_id`. |

Validation:

- `projects` forbids `root_resource_id`; parent modes require it.
- Project IDs must have resource type
  `Microsoft.CognitiveServices/accounts/projects`.
- Parent IDs must match the selected mode.
- No independent subscription/resource-group/project fields are allowed.
- The contract stores no discovered agent, model, connection, or workspace
  inventory.

## DeploymentSelection

Operator inputs before preview.

| Field | Type | Rules |
|---|---|---|
| `workspace` | path | Existing AgentOps workspace for initial detection. |
| `subscription_id` | UUID | Must match active Azure context or explicit selection. |
| `resource_group` | string | Existing target group for Cockpit hosting. |
| `location` | string | Supported App Service region. |
| `app_name` | string | Azure Web App naming rules; globally unique result. |
| `tenant_id` | UUID | Workforce tenant; must match app registration. |
| `client_id` | UUID | Existing single-tenant app registration. |
| `allowed_group_id` | UUID or null | Optional dedicated access group. |
| `scope` | ObserveScope | Defaults to the current workspace project. |

## DeploymentPreview

Complete mutation review shown before confirmation.

| Field | Type | Rules |
|---|---|---|
| `selection` | DeploymentSelection | Normalized operator choices. |
| `resources` | array of PlannedResource | App Service plan, Web App, and UAMI only. |
| `role_assignments` | array of RoleAssignmentPlan | Exact principal, role, and scope. |
| `federated_credential` | FederatedCredentialPlan | Existing/reuse/create state and Graph target. |
| `application_settings` | object | Names and non-secret values; no credentials. |
| `warnings` | array of string | Includes subscription scope, preview dependencies, and group overage. |
| `infrastructure_preview` | object | Parsed azd/Bicep what-if result. |

## PlannedResource

| Field | Type | Rules |
|---|---|---|
| `resource_id` | string | Deterministic canonical ARM ID. |
| `resource_type` | enum | App Service plan, Web App, or UAMI. |
| `change_type` | enum | `create`, `modify`, `no_change`, or `unknown`. |
| `location` | string | Must match the deployment selection where applicable. |

## RoleAssignmentPlan

| Field | Type | Rules |
|---|---|---|
| `assignment_id` | UUID | Deterministic from principal, role definition, and scope. |
| `principal_id` | UUID | UAMI principal ID. |
| `role` | enum | `Reader` or `Log Analytics Reader` only. |
| `role_definition_id` | string | Canonical built-in role definition ID. |
| `scope_resource_id` | string | Selected boundary or derived linked telemetry resource/workspace. |
| `reason` | string | Discovery or telemetry-read purpose. |

`Privileged Monitoring Data Reader` is invalid for a UAMI assignment plan.

## FederatedCredentialPlan

| Field | Type | Rules |
|---|---|---|
| `application_object_id` | UUID | Object ID of the existing registration. |
| `name` | string | Deterministic Cockpit/UAMI credential name. |
| `issuer` | URL | Tenant v2 issuer. |
| `subject` | UUID | UAMI principal/object ID. |
| `audiences` | array | Exactly `api://AzureADTokenExchange` in public Azure. |
| `action` | enum | `create`, `reuse`, or `conflict`. |

A conflict blocks deployment; an existing exact match is reused.

## HostedCockpitDeployment

Result of a completed deployment.

| Field | Type | Rules |
|---|---|---|
| `web_app_resource_id` | string | Canonical Web App ID. |
| `managed_identity_resource_id` | string | Stable UAMI ID. |
| `scope` | ObserveScope | Effective non-secret boundary. |
| `app_url` | URL | HTTPS Cockpit URL. |
| `portal_url` | URL | Azure management URL. |
| `health` | enum | `healthy`, `auth_pending`, `rbac_pending`, or `failed`. |
| `deployed_version` | string | AgentOps package version. |

State transitions:

```text
unresolved -> validated -> previewed -> confirmed -> provisioned
           -> federated -> deployed -> verified
```

Any stage may transition to `failed`. Rerun resumes from the first unmatched
stage and reuses exact resources, role assignments, and federation.

## DeploymentJournal

Versioned, non-secret local recovery state stored at
`.agentops/deploy/cockpit/deployment-state.json`.

| Field | Type | Rules |
|---|---|---|
| `version` | integer | Required; exactly `1`. |
| `attempt_id` | UUID | Unique deployment-attempt identifier. |
| `selection_fingerprint` | string | Stable hash of normalized deployment inputs. |
| `last_completed_stage` | enum or null | One deployment state-machine stage. |
| `mutations` | array of MutationRecord | Ordered planned and observed mutations. |
| `resource_ids` | array of string | Canonical resulting Cockpit resource IDs only. |
| `updated_at` | datetime | UTC. |
| `failure` | DeploymentFailure or null | Safe diagnostic and recovery state. |

Each `MutationRecord` identifies the target, action, pre-existing status,
attempt status, and resulting identifier. `DeploymentFailure` identifies
completed, incomplete, and uncertain mutations, rollback of local temporary
state, preserved cloud resources, Cockpit usability, and safe retry guidance.
The journal contains no tokens, credentials, connection strings, or raw
telemetry. It is reconciled with live ARM and Microsoft Graph state before a
failed deployment resumes.

## ResourceInventory

Fifteen-minute runtime-discovery cache value.

| Field | Type | Rules |
|---|---|---|
| `scope` | ObserveScope | Cache boundary. |
| `foundry_resources` | array | Readable Foundry ARM resources. |
| `projects` | array | Readable project IDs, names, endpoints, and parents. |
| `telemetry_sources` | array of TelemetrySource | Live resolved sources. |
| `discovered_at` | datetime | UTC. |
| `expires_at` | datetime | `discovered_at + 15 minutes`. |
| `partial_failures` | array | Source-specific discovery errors. |

## TelemetrySource

| Field | Type | Rules |
|---|---|---|
| `source_id` | string | Stable hash of telemetry resource/workspace ID. |
| `resource_id` | string | Application Insights or workspace ARM ID. |
| `workspace_id` | string or null | Log Analytics customer/workspace ID when resolved. |
| `foundry_resource_id` | string or null | Origin attribution. |
| `project_resource_ids` | array | All projects linked to this source. |
| `state` | enum | `available`, `inaccessible`, `not_configured`, `not_found`, or `error`. |
| `reason` | string or null | Safe actionable diagnostic. |
| `last_query_duration_ms` | integer or null | Non-negative. |

Shared workspaces appear once with multiple project IDs.

## ObserveFilterState

| Field | Type | Rules |
|---|---|---|
| `foundry_resource_id` | string or null | Must remain inside `ObserveScope`. |
| `project_resource_id` | string or null | Must be in discovered inventory. |
| `agent_id` | string or null | Opaque reported identifier. |
| `model` | string or null | Opaque reported model/deployment. |
| `start` | datetime | UTC and earlier than `end`. |
| `end` | datetime | UTC; defaults to now. |

The default range is the previous 24 hours. These fields may be encoded in the
page URL; raw content may not.

## QueryDiagnostics

| Field | Type | Rules |
|---|---|---|
| `started_at` | datetime | UTC. |
| `completed_at` | datetime | UTC. |
| `duration_ms` | integer | Non-negative. |
| `source_count` | integer | 0 through 10. |
| `successful_sources` | integer | Not greater than source count. |
| `partial_sources` | integer | Not greater than source count. |
| `failed_sources` | integer | Not greater than source count. |
| `cache_status` | enum | `hit`, `miss`, or `bypass`. |

## ObservedAgent

| Field | Type | Rules |
|---|---|---|
| `key` | string | Stable source/project/agent normalization key. |
| `agent_id` | string or null | `null` means not reported. |
| `agent_name` | string or null | `null` means not reported. |
| `project_resource_id` | string or null | Origin, never inferred from unrelated telemetry. |
| `foundry_resource_id` | string or null | Origin attribution. |
| `source_kind` | enum | `foundry`, `external`, or `unknown`. |
| `model` | string or null | Reported value only. |
| `last_seen` | datetime | Latest observed invocation, not lifecycle state. |
| `invocations` | integer | Non-negative. |
| `failures` | integer | Non-negative and not greater than invocations. |
| `p95_latency_ms` | number or null | Non-negative. |
| `input_tokens` | integer or null | Observed usage only. |
| `output_tokens` | integer or null | Observed usage only. |

## ModelUsage

Aggregated by reported project, agent, deployment, and model. It contains
requests, failures, p95 latency, input/output tokens, and last observed activity.
Missing dimensions remain null and are not synthesized.

## CoverageResult

| Field | Type | Rules |
|---|---|---|
| `source_id` | string | Related telemetry source. |
| `dimension` | enum | Resource access, telemetry connection, recent traces, agent attribution, model attribution, token usage, trace correlation, or protected content. |
| `state` | enum | `available`, `inaccessible`, `not_configured`, `no_data`, `not_reported`, `partial`, or `protected_or_unavailable`. |
| `reason` | string | Concise and non-sensitive. |
| `next_action` | string | Actionable operator guidance. |
| `refreshed_at` | datetime | UTC. |

No state may convert missing data to numeric zero.

## GenerativeAIContent

Non-cacheable delegated response projected from `AppGenAIContent`.

| Field | Type | Rules |
|---|---|---|
| `trace_id` | string | Required correlation key. |
| `span_id` | string or null | Optional narrower correlation. |
| `source_resource_id` | string | Must be in the current inventory and scope. |
| `protection_state` | enum | `available`, `protected_or_unavailable`, or `not_configured`. |
| `input_messages` | value or null | Returned only when authorized. |
| `output_messages` | value or null | Returned only when authorized. |
| `system_instructions` | value or null | Returned only when authorized. |
| `tool_content` | value or null | Arguments/results/definitions when authorized. |
| `evaluation_explanation` | value or null | Returned only when authorized. |

Raw fields are omitted, not blank-filled, when authorization is unavailable.
