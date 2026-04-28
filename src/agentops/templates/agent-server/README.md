# AgentOps Watchdog — deploy scaffold

This folder contains the minimum bits to host `agentops agent serve`
on **Azure Container Apps** as a GitHub Copilot Extension.

## Files

- `Dockerfile` — installs `agentops-toolkit[agent]` and runs
  `agentops agent serve --host 0.0.0.0 --port 8080`.
- `main.bicep` — single-resource ACA app with HTTPS ingress, a
  user-assigned managed identity, and a `/healthz` liveness probe.

## Quickstart

```bash
# 1. Build & push the image (server-side build avoids local Docker).
az acr build \
  --registry <your-acr> \
  --image agentops-watchdog:1.0.0 \
  --file Dockerfile .

# 2. Provision the Container App.
az deployment group create \
  --resource-group <rg> \
  --template-file main.bicep \
  --parameters \
      environmentName=<aca-environment> \
      image=<your-acr>.azurecr.io/agentops-watchdog:1.0.0 \
      userAssignedIdentityId=<umi-resource-id> \
      appInsightsResourceId=<app-insights-resource-id> \
      foundryProjectEndpoint=<https://...>
```

The user-assigned identity needs read access on the Application
Insights resource (`Monitoring Reader`) and on the Foundry project
(`Azure AI Developer`).

## Wire to Copilot Chat

Once the app is running, register a GitHub App that points its Copilot
Extension webhook at:

```
https://<app-fqdn>/agents/messages
```

Local development bypasses the GitHub signature check via
`agentops agent serve --no-verify`. **Never** deploy with
`--no-verify` to a public endpoint.

## What the agent does

The container runs the watchdog analyzer on every chat turn,
combining:

1. AgentOps eval history (mounted at `/app/.agentops` or pulled at
   runtime).
2. Application Insights traces (Foundry telemetry).
3. Foundry control plane (`azure-ai-projects`).

It returns a Markdown reply with severity-ranked findings.
