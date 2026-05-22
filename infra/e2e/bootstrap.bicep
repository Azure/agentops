// AgentOps E2E — long-lived shared infrastructure.
//
// Deploy once into an existing resource group. Creates the heavy/slow
// resources that the e2e workflow reuses across runs: AI Services account,
// Foundry project, gpt-4o-mini model deployment, Container Apps managed
// environment (with Log Analytics), and a Container Registry.
//
// Per-run ephemeral resources (ACA echo app, Foundry agents) are deployed
// separately by perrun.bicep + scripts/e2e_create_agents.py.
//
// Usage:
//   az deployment group create \
//     -g <YOUR_RESOURCE_GROUP> \
//     -f infra/e2e/bootstrap.bicep \
//     -p prefix=agentops-e2e
//
// The deployment is idempotent — re-running it is a fast no-op when no
// resource shape changed.

targetScope = 'resourceGroup'

@description('Azure region. Must support AI Services + gpt-4o-mini + Container Apps.')
param location string = resourceGroup().location

@description('Short prefix used for naming resources. Lowercase, max 12 chars.')
@maxLength(12)
param prefix string = 'agentopse2e'

@description('Capacity (TPM, in thousands of tokens per minute) for the gpt-4o-mini deployment.')
@minValue(1)
@maxValue(500)
param modelCapacity int = 100

@description('Model deployment name surfaced to AgentOps as model:<name>.')
param modelDeploymentName string = 'gpt-4o-mini'

@description('Underlying model name to deploy.')
param modelName string = 'gpt-4o-mini'

@description('Underlying model version. Pin to a specific version for reproducibility.')
param modelVersion string = '2024-07-18'

var suffix = uniqueString(resourceGroup().id, prefix)
var aiServicesName = '${prefix}-ai-${suffix}'
var projectName = '${prefix}-proj'
var logAnalyticsName = '${prefix}-law-${suffix}'
var acaEnvName = '${prefix}-acaenv-${suffix}'
// ACR names must be alphanumeric, lowercase, 5-50 chars.
var acrName = toLower(replace('${prefix}acr${suffix}', '-', ''))

// ---------- AI Services + Foundry project ----------

resource aiServices 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: aiServicesName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: aiServicesName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
    allowProjectManagement: true
  }
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: aiServices
  name: projectName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: projectName
    description: 'AgentOps e2e shared Foundry project.'
  }
}

resource gptDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: aiServices
  name: modelDeploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

// ---------- Log Analytics + Container Apps env ----------

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource acaEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: acaEnvName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    zoneRedundant: false
  }
}

// ---------- Container Registry ----------

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

// ---------- Long-lived UAMI for per-run ACA hello-agent apps ----------
//
// The hello-agent ACA app (deployed by perrun.bicep on every workflow run)
// pulls its image from the ACR and calls Azure OpenAI. We give it a *single*,
// long-lived User-Assigned Managed Identity here — instead of creating a new
// UAMI per run — because Entra ID role assignments take several minutes to
// propagate to issued tokens, and a freshly-created UAMI will see 401s from
// Azure OpenAI for the entire duration of a typical e2e run. Reusing the same
// UAMI across runs sidesteps that propagation delay entirely.

var acaUamiName = '${prefix}-aca-uami-${suffix}'

// Built-in role definition ids (subscription-scoped).
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var openAiUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd' // Cognitive Services OpenAI User

resource acaUami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: acaUamiName
  location: location
}

resource acaUamiAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, acaUami.id, 'AcrPull')
  scope: acr
  properties: {
    principalId: acaUami.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
  }
}

resource acaUamiOpenAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServices.id, acaUami.id, 'CognitiveServicesOpenAIUser')
  scope: aiServices
  properties: {
    principalId: acaUami.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRoleId)
  }
}

// ---------- Outputs (capture into GitHub Actions Variables) ----------

@description('Foundry project endpoint URL — set as AZURE_E2E_FOUNDRY_PROJECT_ENDPOINT.')
output foundryProjectEndpoint string = 'https://${aiServices.name}.services.ai.azure.com/api/projects/${projectName}'

@description('Azure OpenAI endpoint of the AI Services account.')
output azureOpenAiEndpoint string = aiServices.properties.endpoint

@description('Model deployment name — set as AZURE_E2E_MODEL_DEPLOYMENT.')
output modelDeployment string = modelDeploymentName

@description('Container Apps managed environment resource id — set as AZURE_E2E_ACA_ENV_ID.')
output acaEnvironmentId string = acaEnv.id

@description('ACR login server — set as AZURE_E2E_ACR_LOGIN_SERVER.')
output acrLoginServer string = acr.properties.loginServer

@description('AI Services account name (for diagnostics).')
output aiServicesName string = aiServices.name

@description('Resource id of the long-lived UAMI used by per-run ACA hello-agent apps. Has AcrPull on the ACR and Cognitive Services OpenAI User on AI Services.')
output acaUamiResourceId string = acaUami.id

@description('Client id (appId) of the long-lived UAMI — set as AZURE_CLIENT_ID inside the ACA container so DefaultAzureCredential picks the right identity.')
output acaUamiClientId string = acaUami.properties.clientId

@description('Name of the long-lived UAMI (for diagnostics).')
output acaUamiName string = acaUami.name
