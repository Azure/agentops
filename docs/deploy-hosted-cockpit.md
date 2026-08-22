# Deploy the Hosted Cockpit

The hosted Cockpit is an authenticated, read-only Azure App Service for teams
that need a shared Observe experience. Deployment starts from a configured
AgentOps workspace, defaults to that workspace's Foundry project, and requires a
complete preview before it changes Azure or Microsoft Entra.

The local command remains unchanged:

```bash
agentops cockpit
```

Use the hosted deployment command only when a shared surface is required:

```bash
agentops cockpit deploy --workspace .
```

## Prerequisites

The target must be Azure public cloud and use workspace-based Application
Insights. Authenticate Azure CLI and Azure Developer CLI to the same workforce
tenant and target subscription.

Supply an existing, single-tenant app registration. AgentOps does not create an
app registration or service principal. The registration needs:

- the hosted Web App redirect URI;
- delegated Azure Monitor Logs `Data.Read` access with tenant admin consent;
- permission to accept the hosted app's user-assigned managed identity through
  one named federated identity credential;
- optional group claims when access is restricted to one group.

The deploying operator needs:

| Surface | Required access |
|---|---|
| Azure Resource Manager | Contributor-equivalent rights for the Cockpit App Service plan, Web App, UAMI, authentication settings, and non-secret app settings |
| Azure RBAC | `roleAssignments/read` and `roleAssignments/write` on every planned assignment scope |
| Microsoft Graph | Delegated `Application.ReadWrite.All` and a supported owner or directory role for the supplied application |
| Group resolution | `GroupMember.Read.All` only when `--allowed-group` is used |

The runtime UAMI receives only `Reader` and `Log Analytics Reader` at scopes
shown in the preview. It never receives `Privileged Monitoring Data Reader`.

## Select the Observe scope

The default is the one Foundry project resolved from `agentops.yaml` and the
current workspace context. An ambiguous or missing project is an error; AgentOps
does not silently deploy every project in a resource group.

Expand scope explicitly:

```bash
# Explicit projects
agentops cockpit deploy \
  --scope projects \
  --project-id /subscriptions/.../projects/project-a \
  --project-id /subscriptions/.../projects/project-b

# One Foundry account, resource group, or subscription
agentops cockpit deploy --scope foundry --scope-resource-id /subscriptions/.../accounts/foundry
agentops cockpit deploy --scope resource-group --scope-resource-id /subscriptions/.../resourceGroups/rg
agentops cockpit deploy --scope subscription --scope-resource-id /subscriptions/...
```

Subscription scope produces a separate warning and confirmation. The effective
scope is stored as one versioned, non-secret `AGENTOPS_OBSERVE_SCOPE` JSON value
containing canonical ARM resource IDs. `AGENTOPS_COCKPIT_MODE=hosted` selects
the hosted runtime.

## Preview before deployment

Run a no-mutation preview:

```bash
agentops cockpit deploy --workspace . --preview
```

The preview lists the exact App Service plan, Web App, UAMI,
`authsettingsV2`, non-secret app settings, role assignments, and federated
credential action. It also includes the azd/Bicep infrastructure preview and
blocks unknown, destructive, or out-of-bound changes.

The mutation allowlist is deliberately narrow:

- one Linux App Service plan;
- one Linux Web App;
- one dedicated UAMI;
- Web App `authsettingsV2`;
- non-secret application settings;
- `Reader` and `Log Analytics Reader` assignments;
- one named federated credential on the supplied app registration.

Foundry projects, telemetry resources, agents, models, alerts, gateways, and
diagnostic settings are monitored resources and remain immutable.

`--yes` suppresses only the final confirmation and is valid only when every
required deployment and scope value is explicit. It does not bypass validation
or subscription-scope warnings.

## Identity and access at runtime

App Service authentication rejects unauthenticated users on every application
route except `/healthz`. Tokens must belong to the configured tenant and, when
configured, the allowed group. The application validates the same tenant and
group boundary server-side.

Aggregate discovery and telemetry queries use the dedicated UAMI. Raw
generative-AI content is different: an explicit trace-content request exchanges
the signed-in user's Easy Auth assertion through an on-behalf-of flow and reads
Azure Monitor with that user's delegated permission. Therefore aggregate access
does not grant protected-content access.

## Recovery and reruns

Deployment state is written to:

```text
.agentops/deploy/cockpit/deployment-state.json
```

The journal contains resource IDs and mutation status, never tokens, connection
strings, credentials, or telemetry content. Before a rerun, AgentOps reconciles
the journal with live ARM and Graph state and generates a new preview. Stable
inputs reuse the Web App, UAMI, role assignments, federated credential, and URL.

If provisioning, federation, deployment, RBAC propagation, or health
verification fails, AgentOps preserves created cloud resources and reports the
completed, incomplete, uncertain, and failed stages. It does not attempt an
automatic destructive rollback. Correct the prerequisite or permission, rerun
the preview, and resume. Resource deletion is an explicit operator action
outside this command.

## Health and troubleshooting

After deployment, AgentOps checks process liveness, authenticated application
readiness, configuration validity, and effective UAMI reads. `/healthz` is
anonymous but returns liveness only.

Common failures:

| Symptom | Action |
|---|---|
| Workspace project is ambiguous | Pass explicit `--project-id` values or correct the workspace project endpoint. |
| ARM role assignment is denied | Grant the deployer `roleAssignments/read` and `roleAssignments/write` on each previewed scope. |
| Federated credential conflicts | Remove or intentionally replace the conflicting credential after reviewing its issuer, subject, and audience. |
| Group cannot be resolved | Verify the group object ID and delegated `GroupMember.Read.All` consent. |
| Aggregate queries are denied | Restore the UAMI's previewed `Reader` and `Log Analytics Reader` assignments and allow RBAC propagation. |
| Protected content is unavailable | Verify the signed-in user's delegated Azure Monitor permission; do not grant the UAMI privileged content access. |
| Health verification fails | Inspect App Service logs and the deployment journal, then rerun after correction. |

Preview and successful deployment return exit code `0`; invalid configuration,
validation, deployment, federation, or health failures return `1`. Cockpit
deployment never returns the eval/Doctor threshold code `2`.

## Preview dependencies

`protectGenAISensitiveData`, protected tables, and `AppGenAIContent` are public
preview capabilities and must be revalidated before release. The planned
migration windows are September 30, 2026 for legacy generative-AI evaluation
events and September 30, 2027 for legacy message fields. If the required
protected-content capability is unsupported, Observe reports it as unavailable;
it does not recover values from legacy telemetry fields.
