# One-shot bootstrap script for the E2E pipeline against a NEW Azure tenant.
#
# Run this from the repo root after `az login --tenant <new-tenant>`. It will:
#
#   1. Deploy the shared E2E infra (AI Services + Foundry project + gpt-4o-mini
#      deployment + Container Apps env + ACR + UAMI) via `infra/e2e/bootstrap.bicep`.
#   2. Create an Entra app registration in the current tenant, grant it Contributor
#      + User Access Administrator on the resource group, and add a federated
#      credential bound to the current branch of this repo.
#   3. Update every GitHub Actions Variable that the E2E workflow consumes
#      (subscription, tenant, client id, endpoints, ACA env id, ACR, model).
#   4. Print the manual remaining steps (creating the `e2e-prompt:1` Foundry
#      agent) and the `gh workflow run` command to trigger the pipeline.
#
# Idempotent: re-running is safe. Each step checks for the existing resource
# before creating.
#
# Required tools on PATH:  az, gh, jq is NOT required.
# Required CLI auth:       az login (correct tenant) AND gh auth status.

#Requires -Version 7.0
[CmdletBinding()]
param(
    [string]$SubscriptionId = '9788a92c-2f71-4629-8173-7ad449cb50e1',
    [string]$TenantId       = '16b3c013-d300-468d-ac64-7eda0820b6d3',
    [string]$ResourceGroup  = 'rg-agentops-e2e',
    [string]$Repo           = 'Azure/agentops',
    [string]$AppName        = 'agentops-e2e',
    [string]$Branch         = (& git rev-parse --abbrev-ref HEAD).Trim(),
    [string]$BicepParams    = 'infra/e2e/bootstrap.parameters.example.json',
    [string]$DeploymentName = 'agentops-e2e-bootstrap'
)

$ErrorActionPreference = 'Stop'
function Header($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }

# ---------------------------------------------------------------------------
# 0. Pre-flight
# ---------------------------------------------------------------------------
Header "Pre-flight"

$current = (az account show --query "{sub:id, tenant:tenantId}" -o json | ConvertFrom-Json)
if ($current.sub -ne $SubscriptionId -or $current.tenant -ne $TenantId) {
    Write-Host "Switching az context to $SubscriptionId in tenant $TenantId..."
    az account set --subscription $SubscriptionId | Out-Null
    $current = (az account show --query "{sub:id, tenant:tenantId}" -o json | ConvertFrom-Json)
    if ($current.tenant -ne $TenantId) {
        throw "az is logged into tenant $($current.tenant) but expected $TenantId. Run: az login --tenant $TenantId"
    }
}
Write-Host "OK: subscription=$($current.sub) tenant=$($current.tenant) branch=$Branch"

if (-not (az group exists --name $ResourceGroup)) {
    Write-Host "Resource group $ResourceGroup does not exist; creating in eastus2..."
    az group create --name $ResourceGroup --location eastus2 | Out-Null
}

# ---------------------------------------------------------------------------
# 1. Deploy bootstrap.bicep
# ---------------------------------------------------------------------------
Header "Deploying bootstrap.bicep (this can take 5-10 minutes)"

az deployment group create `
    --resource-group $ResourceGroup `
    --name $DeploymentName `
    --template-file infra/e2e/bootstrap.bicep `
    --parameters "@$BicepParams" `
    --output none

$outputs = az deployment group show `
    -g $ResourceGroup -n $DeploymentName `
    --query properties.outputs -o json | ConvertFrom-Json

$foundryEndpoint = $outputs.foundryProjectEndpoint.value
$openAiEndpoint  = $outputs.azureOpenAiEndpoint.value
$modelDeployment = $outputs.modelDeployment.value
$acaEnvId        = $outputs.acaEnvironmentId.value
$acrLoginServer  = $outputs.acrLoginServer.value

Write-Host "  foundry        = $foundryEndpoint"
Write-Host "  openai         = $openAiEndpoint"
Write-Host "  modelDeployment= $modelDeployment"
Write-Host "  acaEnv         = $acaEnvId"
Write-Host "  acrLoginServer = $acrLoginServer"

# ---------------------------------------------------------------------------
# 2. Entra app + federated credential
# ---------------------------------------------------------------------------
Header "Entra app + federated credential ($AppName)"

$appId = (az ad app list --display-name $AppName --query "[0].appId" -o tsv)
if (-not $appId) {
    Write-Host "Creating app registration $AppName..."
    $appId = (az ad app create --display-name $AppName --query appId -o tsv)
}
$spId = (az ad sp list --filter "appId eq '$appId'" --query "[0].id" -o tsv)
if (-not $spId) {
    $spId = (az ad sp create --id $appId --query id -o tsv)
}
Write-Host "  appId=$appId  spId=$spId"

foreach ($role in @('Contributor', 'User Access Administrator')) {
    $existing = az role assignment list `
        --assignee $appId `
        --role "$role" `
        --scope "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup" `
        --query "[0].id" -o tsv 2>$null
    if (-not $existing) {
        Write-Host "  granting $role on RG..."
        az role assignment create `
            --assignee-object-id $spId `
            --assignee-principal-type ServicePrincipal `
            --role "$role" `
            --scope "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup" | Out-Null
    } else {
        Write-Host "  already has $role"
    }
}

$ficName = "agentops-e2e-$($Branch -replace '[^a-zA-Z0-9-]', '-')"
$ficSubject = "repo:${Repo}:ref:refs/heads/$Branch"
$existingFic = az ad app federated-credential list --id $appId `
    --query "[?subject=='$ficSubject'].name" -o tsv
if (-not $existingFic) {
    $ficJson = @{
        name      = $ficName
        issuer    = 'https://token.actions.githubusercontent.com'
        subject   = $ficSubject
        audiences = @('api://AzureADTokenExchange')
    } | ConvertTo-Json -Compress
    $tmp = New-TemporaryFile
    Set-Content -Path $tmp -Value $ficJson -Encoding UTF8
    az ad app federated-credential create --id $appId --parameters "@$tmp" | Out-Null
    Remove-Item $tmp
    Write-Host "  federated cred created: $ficName -> $ficSubject"
} else {
    Write-Host "  federated cred already exists for $ficSubject"
}

# Also add a federated cred bound to the `e2e` GitHub Environment so the
# environment-protected jobs can mint OIDC tokens.
$envSubject = "repo:${Repo}:environment:e2e"
$existingEnvFic = az ad app federated-credential list --id $appId `
    --query "[?subject=='$envSubject'].name" -o tsv
if (-not $existingEnvFic) {
    $ficJson2 = @{
        name      = 'agentops-e2e-environment'
        issuer    = 'https://token.actions.githubusercontent.com'
        subject   = $envSubject
        audiences = @('api://AzureADTokenExchange')
    } | ConvertTo-Json -Compress
    $tmp2 = New-TemporaryFile
    Set-Content -Path $tmp2 -Value $ficJson2 -Encoding UTF8
    az ad app federated-credential create --id $appId --parameters "@$tmp2" | Out-Null
    Remove-Item $tmp2
    Write-Host "  federated cred created: e2e environment -> $envSubject"
} else {
    Write-Host "  federated cred already exists for $envSubject"
}

# ---------------------------------------------------------------------------
# 3. Update GitHub Actions Variables
# ---------------------------------------------------------------------------
Header "Updating GitHub Actions Variables on $Repo"

$vars = [ordered]@{
    AZURE_SUBSCRIPTION_ID              = $SubscriptionId
    AZURE_TENANT_ID                    = $TenantId
    AZURE_CLIENT_ID                    = $appId
    AZURE_E2E_RESOURCE_GROUP           = $ResourceGroup
    AZURE_E2E_FOUNDRY_PROJECT_ENDPOINT = $foundryEndpoint
    AZURE_E2E_OPENAI_ENDPOINT          = $openAiEndpoint
    AZURE_E2E_MODEL_DEPLOYMENT         = $modelDeployment
    AZURE_E2E_ACA_ENV_ID               = $acaEnvId
    AZURE_E2E_ACR_LOGIN_SERVER         = $acrLoginServer
}
foreach ($kv in $vars.GetEnumerator()) {
    gh variable set $kv.Key --repo $Repo --body "$($kv.Value)" | Out-Null
    Write-Host "  set $($kv.Key)"
}

# ---------------------------------------------------------------------------
# 4. Manual steps + workflow trigger
# ---------------------------------------------------------------------------
Header "Manual step still required"
Write-Host "Foundry agents cannot yet be created via Bicep. Open the AI Foundry portal:"
Write-Host "  https://ai.azure.com/"
Write-Host ""
Write-Host "1. Open the project at: $foundryEndpoint"
Write-Host "2. Create a prompt-based agent named 'e2e-prompt' using model '$modelDeployment'."
Write-Host "3. Publish it (note the version, usually 1)."
Write-Host "4. Set the AGENTOPS_E2E_FOUNDRY_PROMPT_AGENT GitHub variable:"
Write-Host ""
Write-Host "   gh variable set AGENTOPS_E2E_FOUNDRY_PROMPT_AGENT --repo $Repo --body 'e2e-prompt:1'"
Write-Host ""
Write-Host "Then trigger the workflow:"
Write-Host ""
Write-Host "   gh workflow run e2e.yml --repo $Repo --ref $Branch -f scenarios=foundry-prompt"
Write-Host ""
Write-Host "Or run all scenarios:"
Write-Host ""
Write-Host "   gh workflow run e2e.yml --repo $Repo --ref $Branch -f scenarios=all"
Write-Host ""
Write-Host "Done."
