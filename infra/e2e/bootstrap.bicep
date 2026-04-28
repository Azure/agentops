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
//     -g rg-paulolacerda-0426261026 \
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
@maxValue(50)
param modelCapacity int = 10

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

resource aiServices 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
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

resource gptDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
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

// ---------- Outputs (capture into GitHub Actions Variables) ----------

@description('Foundry project endpoint URL — set as AZURE_E2E_FOUNDRY_PROJECT_ENDPOINT.')
output foundryProjectEndpoint string = 'https://${aiServices.properties.endpoint}/api/projects/${projectName}'

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
