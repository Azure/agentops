// AgentOps hosted Cockpit — Azure infrastructure template.
//
// Scope: this template is deployed at resource-group scope (the default
// Bicep target scope) and creates/updates exactly the resources allowed by
// spec FR-054:
//   - one Linux App Service plan
//   - one Linux Web App
//   - one dedicated user-assigned managed identity (UAMI), stable across
//     re-runs so role assignments and federated-identity-credential
//     bindings remain valid
//   - the Web App's authsettingsV2 child resource (Azure AD, single tenant,
//     Easy Auth, only `/healthz` excluded from authentication)
//   - non-secret application settings (FR-060)
//   - deterministic Reader / Log Analytics Reader role assignments
//     (FR-056, FR-064) — see the note below on scope
//   - resource outputs consumed by the CLI/service layer and azd
//
// It deliberately never creates or mutates Azure AI Foundry accounts,
// Foundry projects, Application Insights, Log Analytics workspaces/tables,
// diagnostic settings, alerts, gateway policies, or Microsoft Entra ID
// objects (apps, service principals, groups, consent grants). Where those
// resources need to be *referenced* (for example, to scope a read-only role
// assignment to a Foundry project the Cockpit will observe) they are
// declared with the `existing` keyword, which never creates or modifies
// them.
//
// Federated identity credential (FIC) creation is a Microsoft Graph
// operation and is intentionally out of scope for this ARM/Bicep template;
// it is applied by the CLI/service layer using the UAMI's outputs below.
//
// Role-assignment scope note (FR-064): ARM/Bicep only allows an extension
// resource (such as a role assignment) to be deployed at the same scope as
// the template itself, or scoped to an `existing`/newly created resource
// that is itself resolvable at that same scope, without a nested
// deployment/module. Since this template intentionally ships as a single
// file with no helper modules, the Reader/Log Analytics Reader assignments
// below cover the modes that are expressible from a resource-group-scoped
// template: the deployment's own resource group (FR-064 resource-group
// mode), and Foundry accounts/projects/Log Analytics workspaces that live
// in that same resource group (FR-064 project mode and Foundry-resource
// mode). Subscription-wide Reader (FR-064 subscription mode) and targets in
// a different resource group are out of reach for a single flat template
// and are intentionally left to the out-of-template
// preview/apply/orchestration layer, consistent with how FIC creation is
// handled.
//
// This template targets public Azure only and has no dependency on any
// specific cloud environment beyond the `environment()` intrinsic function
// used for the AAD issuer URL.

@description('Name of the Linux Web App to create. Also used to derive the App Service plan and managed identity names.')
param webAppName string

@description('Azure region for all resources. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Tags applied to every resource created by this template.')
param tags object = {}

@description('App Service plan SKU name (Linux). Defaults to a low-cost tier suitable for the Cockpit workload.')
param appServicePlanSkuName string = 'B1'

@description('Microsoft Entra tenant ID used for single-tenant Easy Auth (authsettingsV2) and recorded as a non-secret app setting.')
param tenantId string

@description('Application (client) ID of the Microsoft Entra app registration used for Easy Auth validation.')
param applicationClientId string

@description('Optional Microsoft Entra group object ID. When set, only members of this group are authorized by Easy Auth in addition to the application itself.')
param allowedGroupObjectId string = ''

@description('Versioned identifier describing what this Cockpit deployment observes (FR-064 scope descriptor), recorded as a non-secret app setting.')
param agentopsObserveScope string = ''

@description('Optional operator-declared billed-cost allocation model, recorded as a non-secret app setting. This value contains no credentials and grants no billing access.')
param agentopsCostModel string = ''

@description('agentops-accelerator package version installed on the Web App, recorded as a non-secret app setting for diagnostics.')
param agentopsVersion string = ''

@description('When true, grants the managed identity Reader on this deployment\'s own resource group (FR-064 resource-group mode).')
param grantReaderOnResourceGroup bool = true

@description('Names of existing Microsoft.CognitiveServices/accounts (Azure AI Foundry accounts) in this resource group to grant the managed identity Reader on (FR-064 Foundry-resource mode). These resources are only referenced, never created or modified.')
param foundryAccountNames array = []

@description('References ({accountName, projectName}) to existing Foundry projects in this resource group to grant the managed identity Reader on (FR-064 project mode). These resources are only referenced, never created or modified.')
param foundryProjectRefs array = []

@description('Names of existing Log Analytics workspaces in this resource group to grant the managed identity Log Analytics Reader on. These resources are only referenced, never created or modified.')
param logAnalyticsWorkspaceNames array = []

var readerRoleDefinitionId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'acdd72a7-3385-48ef-bd42-f606fba81ae7')
var logAnalyticsReaderRoleDefinitionId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '73c42c96-874c-492b-b04d-ab87d138a893')

resource appServicePlan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: '${webAppName}-plan'
  location: location
  tags: tags
  kind: 'linux'
  sku: {
    name: appServicePlanSkuName
  }
  properties: {
    reserved: true
  }
}

// Stable, dedicated identity name so re-running this template never creates
// a second identity and never invalidates previously issued role
// assignments or federated identity credentials (FR-055).
resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${webAppName}-uami'
  location: location
  tags: tags
}

resource webApp 'Microsoft.Web/sites@2023-01-01' = {
  name: webAppName
  location: location
  tags: tags
  kind: 'app,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uami.id}': {}
    }
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appCommandLine: 'gunicorn --bind=0.0.0.0 --timeout 600 -k uvicorn.workers.UvicornWorker main:app'
      // FR-060: only non-secret settings — no secrets, certificates, tokens,
      // connection strings, or credentials are ever placed here.
      appSettings: concat([
        {
          name: 'AGENTOPS_COCKPIT_MODE'
          value: 'hosted'
        }
        {
          name: 'AGENTOPS_OBSERVE_SCOPE'
          value: agentopsObserveScope
        }
        {
          name: 'AGENTOPS_TENANT_ID'
          value: tenantId
        }
        {
          name: 'AGENTOPS_APPLICATION_CLIENT_ID'
          value: applicationClientId
        }
        {
          name: 'AGENTOPS_UAMI_CLIENT_ID'
          value: uami.properties.clientId
        }
        {
          name: 'AGENTOPS_ALLOWED_GROUP_OBJECT_ID'
          value: allowedGroupObjectId
        }
        {
          name: 'AGENTOPS_VERSION'
          value: agentopsVersion
        }
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
        {
          name: 'WEBSITES_PORT'
          value: '8000'
        }
      ], empty(agentopsCostModel) ? [] : [
        {
          name: 'AGENTOPS_COST_MODEL'
          value: agentopsCostModel
        }
      ])
    }
  }
}

// Single-tenant Easy Auth (Azure AD) with only `/healthz` excluded from
// authentication (FR-057). All other routes require a validated token at
// the platform boundary; the Cockpit application itself never re-implements
// this gate.
resource authSettings 'Microsoft.Web/sites/config@2023-01-01' = {
  parent: webApp
  name: 'authsettingsV2'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: 'Return401'
      excludedPaths: [
        '/healthz'
      ]
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: applicationClientId
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenantId}/v2.0'
        }
        validation: {
          allowedAudiences: [
            'api://${applicationClientId}'
          ]
          defaultAuthorizationPolicy: union(
            {
              allowedApplications: [
                applicationClientId
              ]
            },
            empty(allowedGroupObjectId) ? {} : {
              allowedPrincipals: {
                groups: [
                  allowedGroupObjectId
                ]
              }
            }
          )
        }
      }
    }
    login: {
      tokenStore: {
        enabled: true
      }
    }
  }
}

// FR-064 resource-group mode: Reader on this deployment's own resource
// group. Deterministic name via guid() so re-running this template never
// creates a duplicate assignment.
resource rgReaderAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (grantReaderOnResourceGroup) {
  name: guid(resourceGroup().id, uami.id, 'Reader')
  scope: resourceGroup()
  properties: {
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: readerRoleDefinitionId
  }
}

// FR-064 Foundry-resource mode: Reader scoped to specific, existing Foundry
// accounts in this resource group. Referenced only — never created here.
resource foundryAccounts 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = [for name in foundryAccountNames: {
  name: name
}]

resource foundryAccountReaderAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (name, i) in foundryAccountNames: {
  name: guid(foundryAccounts[i].id, uami.id, 'Reader')
  scope: foundryAccounts[i]
  properties: {
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: readerRoleDefinitionId
  }
}]

// FR-064 project mode: Reader scoped to specific, existing Foundry projects
// in this resource group. Referenced only — never created here.
resource foundryProjects 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = [for ref in foundryProjectRefs: {
  name: '${ref.accountName}/${ref.projectName}'
}]

resource foundryProjectReaderAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (ref, i) in foundryProjectRefs: {
  name: guid(foundryProjects[i].id, uami.id, 'Reader')
  scope: foundryProjects[i]
  properties: {
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: readerRoleDefinitionId
  }
}]

// Log Analytics Reader scoped to specific, existing workspaces in this
// resource group (telemetry read access). Referenced only — never created,
// mutated, or configured (no diagnostic settings, no table plan changes)
// here.
resource logAnalyticsWorkspaces 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = [for name in logAnalyticsWorkspaceNames: {
  name: name
}]

resource logAnalyticsReaderAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (name, i) in logAnalyticsWorkspaceNames: {
  name: guid(logAnalyticsWorkspaces[i].id, uami.id, 'LogAnalyticsReader')
  scope: logAnalyticsWorkspaces[i]
  properties: {
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: logAnalyticsReaderRoleDefinitionId
  }
}]

output webAppResourceId string = webApp.id
output webAppHostName string = webApp.properties.defaultHostName
output uamiResourceId string = uami.id
output uamiClientId string = uami.properties.clientId
output uamiPrincipalId string = uami.properties.principalId
