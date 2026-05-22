// AgentOps E2E — per-run ephemeral resources.
//
// Deploys an Azure Container Apps app running the AgentOps E2E hello-agent
// (Microsoft Agent Framework + Azure OpenAI) so the http-aca scenario can
// exercise AgentOps' http-json invocation path against a real LLM.
//
// Auth flow (no secrets):
//   The long-lived UAMI created by bootstrap.bicep already has AcrPull on
//   the ACR and Cognitive Services OpenAI User on the AI Services account,
//   so all this template does is attach that UAMI to the ACA app.
//
// Per-run resources are named with a unique suffix derived from
// github.run_id so multiple workflow runs do not collide and teardown is a
// straight `az containerapp delete`.

targetScope = 'resourceGroup'

@description('Azure region for the ACA app.')
param location string = resourceGroup().location

@description('Resource id of the long-lived Container Apps managed environment from bootstrap.bicep.')
param acaEnvironmentId string

@description('Unique suffix for this workflow run (e.g. github.run_id).')
param suffix string

@description('Fully qualified container image (e.g. <acr>.azurecr.io/agentops-e2e/hello-agent:run123).')
param image string

@description('Login server of the long-lived ACR (e.g. <acr>.azurecr.io) — used by the registry config.')
param acrLoginServer string

@description('Resource id of the long-lived UAMI (created by bootstrap.bicep) that already has AcrPull + Cognitive Services OpenAI User. Reusing a long-lived UAMI avoids the multi-minute Entra ID propagation delay a fresh per-run UAMI would suffer.')
param uamiResourceId string

@description('Client id of the long-lived UAMI — set as AZURE_CLIENT_ID in the container so DefaultAzureCredential picks it.')
param uamiClientId string

@description('Azure OpenAI endpoint URL (https://<account>.cognitiveservices.azure.com/ or .openai.azure.com/).')
param azureOpenAiEndpoint string

@description('Azure OpenAI deployment name (e.g. gpt-4o-mini).')
param azureOpenAiDeployment string

@description('Container target port. The hello-agent listens on 8080 by default.')
param targetPort int = 8080

var appName = 'aca-agent-${suffix}'

resource agentApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uamiResourceId}': {}
    }
  }
  properties: {
    managedEnvironmentId: acaEnvironmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: targetPort
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      registries: [
        {
          server: acrLoginServer
          identity: uamiResourceId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'agent'
          image: image
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: azureOpenAiEndpoint
            }
            {
              name: 'AZURE_OPENAI_DEPLOYMENT'
              value: azureOpenAiDeployment
            }
            {
              // DefaultAzureCredential needs the UAMI client id to disambiguate
              // when more than one identity could be picked up.
              name: 'AZURE_CLIENT_ID'
              value: uamiClientId
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

@description('Public ingress URL for the hello-agent — used by the http-aca scenario.')
output agentUrl string = 'https://${agentApp.properties.configuration.ingress.fqdn}'

@description('App name (for teardown).')
output appName string = appName
