// Minimal Bicep to deploy the AgentOps Watchdog as a Copilot Extension
// hosted on Azure Container Apps. Pair with a pre-built image (e.g. from
// `az acr build`).

@description('Resource location.')
param location string = resourceGroup().location

@description('Container Apps environment name.')
param environmentName string

@description('Container app name.')
param appName string = 'agentops-watchdog'

@description('Fully qualified image reference, e.g. myacr.azurecr.io/agentops-watchdog:1.0.0.')
param image string

@description('Application Insights resource id consumed by the watchdog.')
param appInsightsResourceId string = ''

@description('Foundry project endpoint.')
param foundryProjectEndpoint string = ''

@description('User-assigned managed identity resource id with reader access on App Insights and Foundry.')
param userAssignedIdentityId string

resource env 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
      }
    }
    template: {
      containers: [
        {
          name: 'watchdog'
          image: image
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            {
              name: 'AZURE_CLIENT_ID'
              value: userAssignedIdentityId
            }
            {
              name: 'AGENTOPS_APP_INSIGHTS_RESOURCE_ID'
              value: appInsightsResourceId
            }
            {
              name: 'AZURE_AI_FOUNDRY_PROJECT_ENDPOINT'
              value: foundryProjectEndpoint
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 8080
              }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

output appFqdn string = app.properties.configuration.ingress.fqdn
