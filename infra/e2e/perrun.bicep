// AgentOps E2E — per-run ephemeral resources.
//
// Deploys a small Container Apps app running a public echo image so
// the http_backend scenario has a real URL to POST to during this run.
// Named with a unique suffix so multiple workflow runs do not collide
// and teardown is straightforward.
//
// The echo image (mendhak/http-https-echo) returns the incoming request
// as JSON, including a `body` field that mirrors the POST body. AgentOps
// extracts the model response from `body.message` (configured in the
// generated agentops.yaml).

targetScope = 'resourceGroup'

@description('Azure region for the ACA app.')
param location string = resourceGroup().location

@description('Resource id of the long-lived Container Apps managed environment from bootstrap.bicep.')
param acaEnvironmentId string

@description('Unique suffix for this workflow run (e.g. github.run_id).')
param suffix string

@description('Echo image. Pin to a digest in production; tag is fine for e2e.')
param echoImage string = 'mendhak/http-https-echo:31'

@description('Container target port. mendhak/http-https-echo listens on 8080 by default.')
param targetPort int = 8080

var appName = 'aca-echo-${suffix}'

resource echoApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
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
    }
    template: {
      containers: [
        {
          name: 'echo'
          image: echoImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            {
              name: 'HTTP_PORT'
              value: string(targetPort)
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
      }
    }
  }
}

@description('Public ingress URL for the echo app — used by the http-aca scenario.')
output echoUrl string = 'https://${echoApp.properties.configuration.ingress.fqdn}'

@description('App name (for teardown).')
output appName string = appName
