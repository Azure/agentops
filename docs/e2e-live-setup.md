# Live Azure E2E - one-time setup

This guide walks through the human-only steps needed to enable the **live**
jobs in [`.github/workflows/e2e.yml`](../.github/workflows/e2e.yml). Once
completed, anyone with `Run workflow` permission can dispatch the workflow
and pick which scenario(s) to execute against real Azure resources.

> **Auth model:** GitHub OIDC + Entra federated credential. **No client
> secrets** are stored in the repository.

---

## Prerequisites

- An Azure subscription you control (the test subscription is fine).
- A pre-created resource group in your subscription. Any RG works - the
  workflow reads its name from the `AZURE_E2E_RESOURCE_GROUP` Variable.
  Examples in this guide use `<YOUR_RESOURCE_GROUP>` as a placeholder.
- Sufficient role on that RG to assign roles (Owner or `User Access
  Administrator`).
- `az` CLI ≥ 2.60 and `bicep` ≥ 0.27 installed locally.
- Admin access to **GitHub repository settings** for `Azure/agentops`
  (to add Actions Variables and review the federated credential).

---

## Step 1 - One-time shared infra (`bootstrap.bicep`)

Deploys the long-lived resources that every workflow run reuses:
AI Services + Foundry project + `gpt-4o-mini` deployment + Container Apps
managed environment + Log Analytics + ACR.

```bash
az login
az account set --subscription <SUBSCRIPTION_ID>

az deployment group create \
  --resource-group <YOUR_RESOURCE_GROUP> \
  --name agentops-e2e-bootstrap \
  --template-file infra/e2e/bootstrap.bicep \
  --parameters @infra/e2e/bootstrap.parameters.example.json
```

Capture the outputs (printed at the end of the deployment):

```bash
az deployment group show \
  -g <YOUR_RESOURCE_GROUP> \
  -n agentops-e2e-bootstrap \
  --query properties.outputs
```

You will use these values in **Step 4**.

---

## Step 2 - Foundry agents (manual, in the portal)

Bicep does not yet declaratively manage Foundry agents, so create them
once via the [Azure AI Foundry portal](https://ai.azure.com):

1. Open the project created by `bootstrap.bicep`.
2. **Prompt agent** (covers the `foundry-prompt` scenario)
   - Create a new prompt-based agent.
   - Name it `e2e-prompt`.
   - Use `gpt-4o-mini` as the model.
   - Save and **publish**. Note the version (usually `1`).
   - The agent ID is `e2e-prompt:<version>` (e.g. `e2e-prompt:1`).
3. **Hosted agent** (covers the `foundry-hosted` scenario, optional)
   - Deploy any agent that exposes the **Responses** protocol.
   - Copy its endpoint URL (`https://...`).
   - This scenario is opt-in: leave the related Variable empty to skip it.

---

## Step 3 - Entra app + federated credential

The workflow authenticates to Azure with OIDC. No secrets, just a trust
relationship between the repo and an Entra app.

```bash
APP_NAME=agentops-e2e
SUBSCRIPTION_ID=<your-sub>
RG=<YOUR_RESOURCE_GROUP>

# 1. Create the app registration (no client secret).
APP_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)
SP_ID=$(az ad sp create --id "$APP_ID" --query id -o tsv)

# 2. Assign roles on the RG only (least privilege you can get away with).
#    `User Access Administrator` is needed because Foundry agent operations
#    can trigger role assignments to managed identities. If you want a
#    tighter role, swap UAA for a custom role with just
#    `Microsoft.Authorization/roleAssignments/*` over the AI Services scope.
az role assignment create \
  --assignee-object-id "$SP_ID" \
  --assignee-principal-type ServicePrincipal \
  --role Contributor \
  --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RG"

az role assignment create \
  --assignee-object-id "$SP_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "User Access Administrator" \
  --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RG"

# 3. Federated credential - bind the app to runs of e2e.yml on the
#    feature/revamp-1.0 branch. Add another credential for `main` later.
cat > /tmp/fic.json <<EOF
{
  "name": "agentops-e2e-feature-revamp-1.0",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:Azure/agentops:ref:refs/heads/feature/revamp-1.0",
  "audiences": ["api://AzureADTokenExchange"]
}
EOF
az ad app federated-credential create --id "$APP_ID" --parameters @/tmp/fic.json

echo "AZURE_CLIENT_ID=$APP_ID"
echo "AZURE_TENANT_ID=$(az account show --query tenantId -o tsv)"
echo "AZURE_SUBSCRIPTION_ID=$SUBSCRIPTION_ID"
```

Save the three values printed at the end - you'll add them as
**Variables** (not secrets) in Step 4.

> If you ever rename the branch or merge to `main`, add another federated
> credential entry with the new `subject:` value.

---

## Step 4 - Add Actions Variables

GitHub → **Settings → Secrets and variables → Actions → Variables**
(repo-level). Add the following keys.

**Identity:**

| Variable | Value |
|---|---|
| `AZURE_CLIENT_ID` | App ID from Step 3 |
| `AZURE_TENANT_ID` | Tenant ID from Step 3 |
| `AZURE_SUBSCRIPTION_ID` | Subscription ID |
| `AZURE_E2E_RESOURCE_GROUP` | name of the RG you deployed `bootstrap.bicep` into |

**Bootstrap outputs (Step 1):**

| Variable | Source |
|---|---|
| `AZURE_E2E_FOUNDRY_PROJECT_ENDPOINT` | `outputs.foundryProjectEndpoint.value` |
| `AZURE_E2E_MODEL_DEPLOYMENT` | `outputs.modelDeployment.value` |
| `AZURE_E2E_ACA_ENV_ID` | `outputs.acaEnvironmentId.value` |
| `AZURE_E2E_ACR_LOGIN_SERVER` | `outputs.acrLoginServer.value` |

**Foundry agents (Step 2):**

| Variable | Value |
|---|---|
| `AGENTOPS_E2E_FOUNDRY_PROMPT_AGENT` | `e2e-prompt:1` |
| `AGENTOPS_E2E_FOUNDRY_HOSTED_URL` | hosted agent URL, or leave unset to skip |

No GitHub Secrets are required.

---

## Step 5 - Trigger the workflow

The workflow is manual-only:

```bash
# Default: run only the offline demo + unit tests.
gh workflow run e2e.yml --ref feature/revamp-1.0

# All four live scenarios.
gh workflow run e2e.yml --ref feature/revamp-1.0 -f scenarios=all

# Just one scenario (foundry-prompt | foundry-hosted | http-aca | model-direct).
gh workflow run e2e.yml --ref feature/revamp-1.0 -f scenarios=http-aca

# Keep the per-run ACA app around for debugging.
gh workflow run e2e.yml --ref feature/revamp-1.0 \
  -f scenarios=http-aca -f keep_resources=true
```

> The `Run workflow` button only renders on the repository's **default
> branch**. While `e2e.yml` lives on `feature/revamp-1.0`, use the `gh`
> CLI as shown above. Once PR #108 merges to `main`, the button will
> appear in the Actions tab.

---

## Cost & lifecycle notes

- **Bootstrap (one-time):** ~5 minutes to deploy. Idle costs are minimal  - 
  AI Services and ACA are pay-per-request, ACR Basic is ~$5/mo, Log
  Analytics has a generous free tier for low ingestion.
- **Per run:** ~3–5 minutes total (ACA app comes up in ~30s, scenarios run
  in parallel, teardown is fast). Token cost is a few cents per run with
  the small datasets shipped in `scripts/e2e_data/`.
- **Teardown:** the workflow always deletes the per-run ACA app on exit
  unless `keep_resources=true`. A second pass sweeps any `aca-echo-run*`
  app older than one day to catch leftovers from runs that aborted before
  teardown could register.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `AADSTS70021: No matching federated identity record found` | Branch name in workflow run does not match the `subject:` of any federated credential. Add a credential for the new ref. |
| `AuthorizationFailed` on Bicep deployment | App registration is missing `Contributor` on the RG. |
| `RoleAssignmentRequiresElevation` during bootstrap | App registration is missing `User Access Administrator`. |
| `live-foundry-prompt` fails with 404 on agent | `AGENTOPS_E2E_FOUNDRY_PROMPT_AGENT` does not match a real agent in the project. Re-publish in the portal and update the Variable. |
| ACA echo URL returns HTML instead of JSON | The container failed to start. Check logs in `Microsoft.App/containerApps/<name>/logs`. |

---

## What's next

- A future iteration may declaratively create Foundry agents via the
  `azure-ai-projects` SDK from a one-time bootstrap script, removing
  Step 2.
- Hosted Foundry agent provisioning via container build + push to ACR is
  tracked under the v1.1 follow-ups (see `docs/concepts.md` deferred
  list).
