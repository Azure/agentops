# Research: Deploy Hosted Cockpit

## Decision 1: Use App Service Easy Auth with the existing app registration

**Decision**: Deploy a Linux Azure App Service and configure Easy Auth v2
against an operator-supplied, single-tenant workforce app registration. Use the
documented managed-identity federated-credential setting
`OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID` instead of a client secret.

**Rationale**: App Service supports the callback
`https://<app>.azurewebsites.net/.auth/login/aad/callback`, authenticated
principal headers, token-store headers, tenant validation, and UAMI federation
for workforce configurations. This removes secret rotation while retaining
platform-enforced authentication before FastAPI.

**Alternatives considered**:

- Client secret or Key Vault reference: rejected because the agreed contract
  prohibits application secrets.
- Client certificate: rejected because it still creates a rotation lifecycle.
- Custom OIDC middleware: rejected because it duplicates Easy Auth.
- External ID/CIAM tenant: rejected because managed-identity federation for Easy
  Auth is documented only for workforce configurations.

**Sources**:

- [Configure Microsoft Entra authentication for App Service](https://learn.microsoft.com/azure/app-service/configure-authentication-provider-aad)
- [Access App Service authentication tokens](https://learn.microsoft.com/azure/app-service/configure-authentication-oauth-tokens)
- [App Service authsettingsV2 schema](https://learn.microsoft.com/azure/templates/microsoft.web/sites/config-authsettingsv2)

## Decision 2: Use one UAMI federation for Easy Auth and OBO

**Decision**: Attach one dedicated user-assigned managed identity to the Web App
and add one federated identity credential to the existing app registration.
Reuse that UAMI as the `client_assertion_func` credential for
`OnBehalfOfCredential`; use the Easy Auth user access token as the OBO user
assertion.

**Rationale**: Azure Identity explicitly supports OBO with a client assertion
instead of a secret or certificate. The UAMI obtains an
`api://AzureADTokenExchange/.default` assertion, while OBO returns a delegated
Azure Monitor Logs token whose effective data access remains the user's RBAC.

**Alternatives considered**:

- A second app registration: rejected because it is unnecessary and tenant
  identity creation is outside the feature.
- Application-only access to protected content: rejected because all users
  would inherit one privileged identity.
- UAMI for OBO user identity: rejected because managed identity does not
  represent the signed-in user.

**Sources**:

- [OnBehalfOfCredential for Python](https://learn.microsoft.com/python/api/azure-identity/azure.identity.onbehalfofcredential)
- [OAuth 2.0 on-behalf-of flow](https://learn.microsoft.com/entra/identity-platform/v2-oauth2-on-behalf-of-flow)
- [Trust a managed identity with workload identity federation](https://learn.microsoft.com/entra/workload-id/workload-identity-federation-config-app-trust-managed-identity)

## Decision 3: Configure federation through Azure CLI after azd provision

**Decision**: Let Bicep create the UAMI and App Service resources, then have the
explicit deployment service idempotently run
`az ad app federated-credential create` after `azd provision` and before
`azd deploy`. Include this Graph mutation in the preview and preflight.

**Rationale**: The app registration is a Microsoft Graph object rather than an
ARM resource. The Graph Bicep extension is preview/experimental, while the
Azure CLI/Graph API federation operation is a documented stable surface. The
CLI boundary also makes tenant permissions and errors visible to the operator.

**Alternatives considered**:

- Microsoft Graph Bicep extension: viable but rejected as the default because it
  requires experimental Bicep extensibility and preview package pinning.
- A deployment-time secret: rejected.
- Require manual federation: rejected because rerunnable deployment must verify
  and preserve the trust.

## Decision 4: Enforce tenant and optional group at Easy Auth

**Decision**: Configure a single tenant in authsettingsV2 and
`WEBSITE_AUTH_AAD_ALLOWED_TENANTS`. Configure the optional group using
`allowedGroups`, and validate the same claims again in hosted middleware.
Warn that JWT group claims have an overage limit and require a dedicated group
that can be represented directly in the token.

**Rationale**: Platform enforcement denies access before Cockpit data is
displayed. Application validation provides defense in depth and testable error
messages.

**Alternatives considered**:

- Application-only authorization: rejected because the server would receive
  unauthorized traffic.
- Graph group-overage lookup in the MVP: deferred because it adds broad Graph
  permissions and is unnecessary for a dedicated access group.

## Decision 5: Discover projects with Resource Graph and connection metadata

**Decision**: Use Azure Resource Graph to enumerate
`Microsoft.CognitiveServices/accounts` and
`Microsoft.CognitiveServices/accounts/projects` inside the canonical scope.
Resolve linked Application Insights resource IDs through each project's
connection metadata using the existing `AIProjectClient.connections.list()`
pattern, then resolve workspace-based Application Insights to Log Analytics.

**Rationale**: Resource Graph provides bounded cross-resource discovery without
N+1 account scans. The repository already resolves credential-free Application
Insights resource IDs from project connections, including
ProjectManagedIdentity connections, so the implementation extends proven prior
art rather than assuming an undocumented inline project property.

**Alternatives considered**:

- Scan every account through management SDK list calls: rejected due to N+1
  calls and weaker scope handling.
- Store a static project/workspace inventory: rejected by the spec and because
  it becomes stale.
- Read connection strings: rejected because hosted runtime must not store or
  request telemetry secrets.

**Sources**:

- [Azure Resource Graph overview](https://learn.microsoft.com/azure/governance/resource-graph/overview)
- [Foundry architecture and ARM resource types](https://learn.microsoft.com/azure/foundry/concepts/architecture)
- [Connect Application Insights tracing](https://learn.microsoft.com/azure/foundry/observability/how-to/trace-agent-setup)

## Decision 6: Query sources independently with async LogsQueryClient batches

**Decision**: Use `azure.monitor.query.aio.LogsQueryClient.query_batch()` with
one bounded query per source, a 30-second source timeout, a 10-second Overview
deadline, and per-source result classification. Limit active sources to 10 per
request.

**Rationale**: Batch responses preserve full, partial, throttled, and failed
states per source. This is required for honest partial rendering and is safer
than one cross-workspace query that loses source-level failure isolation.

**Alternatives considered**:

- `additional_workspaces` on one query: rejected because failures are coarser
  and source-specific filters cannot be isolated.
- Existing Application Insights REST helper: retained for legacy Doctor paths
  but not extended for Observe because it collapses failures into `None`.
- Unbounded concurrent calls: rejected due to Resource Graph and Logs Query
  throttling.

**Sources**:

- [Azure Monitor Query client library for Python](https://github.com/Azure/azure-sdk-for-python/tree/main/sdk/monitor/azure-monitor-query)
- [Azure Monitor Logs query timeouts](https://learn.microsoft.com/azure/azure-monitor/logs/api/timeouts)
- [Resource Graph throttling guidance](https://learn.microsoft.com/azure/governance/resource-graph/concepts/guidance-for-throttled-requests)

## Decision 7: Keep protected content exclusively on the delegated path

**Decision**: Query `AppGenAIContent` only from an explicit trace-detail
endpoint using the user's OBO credential. Never grant Privileged Monitoring
Data Reader to the UAMI, never fall back to legacy content fields, and never
cache raw content.

**Rationale**: `AppGenAIContent` contains dedicated prompt, response,
instruction, tool, and evaluation fields correlated by `TraceId`, `SpanId`, and
`ParentSpanId`. Protected tables deny standard readers by returning zero rows,
so a UAMI without the privileged role cannot accidentally expose content.

**Alternatives considered**:

- Grant the UAMI privileged access and filter in code: rejected because a code
  defect could expose content to every hosted user.
- Recover content from legacy tables when protected results are empty: rejected
  because it bypasses the protection boundary.
- Shared or browser cache: rejected because authorization is per user.

**Sources**:

- [AppGenAIContent table reference](https://learn.microsoft.com/azure/azure-monitor/reference/tables/appgenaicontent)
- [Configure protected tables](https://learn.microsoft.com/azure/azure-monitor/logs/protected-tables-configure)

## Decision 8: Represent an empty protected query honestly

**Decision**: A zero-row delegated query returns
`protected_or_unavailable`, not `no_data`, unless independent metadata proves
the table is unprotected and readable. The UI explains that Azure intentionally
makes absent content and denied protected content indistinguishable.

**Rationale**: Microsoft documents that a query by a user lacking Privileged
Monitoring Data Reader succeeds but returns zero rows. Treating this as known
absence would create false confidence.

**Alternatives considered**:

- Proactively enumerate every user's role assignments: rejected for the MVP
  because inherited/PIM/conditional role evaluation is complex and a separate
  management-plane check could still race authorization propagation.
- Display `no data`: rejected as misleading.

## Decision 9: Use azd-compatible source deployment

**Decision**: Package a minimal hosted app entrypoint, generated
version-pinned requirements, `azure.yaml`, and Bicep under the installed
AgentOps templates. Materialize the bundle into
`.agentops/deploy/cockpit/`; use `azd provision --preview`, `azd provision`, and
`azd deploy`.

**Rationale**: This follows issue #433's responsibility split, keeps Bicep as
the versioned ARM source of truth, and deploys the same AgentOps package version
that initiated the command. The generated bundle remains local-only and
rerunnable.

**Alternatives considered**:

- Container image plus ACR: rejected for the MVP because it adds two cloud
  resources and a separate image-publishing contract.
- Direct ARM JSON: rejected because Bicep is the source of truth.
- Raw `az webapp up`: rejected because preview and deterministic RBAC are weaker.

## Resolved risks

- `protectGenAISensitiveData`, `AppGenAIContent`, and protected tables are public
  preview as of this plan. Implementation and release checks must verify the
  current API, role name, migration dates, and schema before shipping.
- The same-UAMI Easy Auth plus OBO chain is supported by its two documented
  components. A focused live integration test is mandatory before release; it
  is validation of the selected design, not an unresolved architecture choice.
- Azure portal blade URL formats are undocumented. Use documented Foundry links
  where available and mark any portal query deep link as best effort.

## Implementation revalidation (2026-08-21)

The implementation was checked again against current Microsoft documentation:

- [Application Insights telemetry data model](https://learn.microsoft.com/azure/azure-monitor/app/data-model-complete#generative-ai-telemetry)
  still documents `protectGenAISensitiveData`, routing to `AppGenAIContent` from
  September 30, 2026, the temporary `optOutProtectGenAISensitiveData` flag, and
  its September 30, 2027 retirement.
- [Restrict access to sensitive content in Microsoft Foundry traces](https://learn.microsoft.com/azure/foundry/observability/how-to/traces-sensitive-content#route-sensitive-content-to-the-dedicated-table)
  still requires the dedicated table to be configured as protected for the
  intended deny-by-default boundary.
- [Manage access to Log Analytics workspaces](https://learn.microsoft.com/azure/azure-monitor/logs/manage-access#protected-tables-preview)
  still defines `protectionLevel` values `General` and `Protected`, standard
  read-role denial for protected tables, and the `Privileged Monitoring Data
  Reader` role. The shared Cockpit UAMI deliberately does not receive that role.
- [Python `OnBehalfOfCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.onbehalfofcredential)
  still supports a `client_assertion_func` plus the required `user_assertion`,
  which allows the same federated UAMI to authenticate the middle tier without
  a client secret while preserving delegated user authorization.

Result: the planned public-cloud API names, feature flags, migration dates,
role boundary, and secretless OBO constructor remain current. Protected tables
and sensitive-content routing remain preview capabilities. Release approval
still requires the focused live same-UAMI Easy Auth plus OBO test described
above; an unsupported or changed preview surface must block protected-content
availability rather than weaken authorization.
