# Azure Storage Datasets

Most AgentOps projects should keep their first evaluation dataset as a local
JSONL file, such as `.agentops/data/smoke.jsonl`. Use Azure Storage when the
dataset already belongs in centrally managed storage, must be shared across
runners, or cannot live in the repository workspace.

AgentOps supports one Azure Blob Storage or Azure Data Lake Storage Gen2 object
as the `dataset` value:

```yaml
# Azure Blob Storage
dataset: https://examplestorage.blob.core.windows.net/evals/smoke.jsonl

# Azure Data Lake Storage Gen2
dataset: https://examplestorage.dfs.core.windows.net/evals/regression/smoke.jsonl
```

## How access works

Remote reads reuse the Azure identity already available to AgentOps:

- Local development uses the identity established by `az login`.
- CI uses its existing workload or service-principal identity.
- Azure-hosted runners use managed identity when configured.

AgentOps does not accept dataset-specific tokens, SAS URLs, storage account
keys, or connection strings. Grant the running identity `Storage Blob Data
Reader`, preferably at container scope. ADLS Gen2 path ACLs may also be
required.

For each analysis or evaluation operation, AgentOps downloads one read-only
snapshot and applies the same JSONL validation and evaluator selection used for
local files. Remote objects are limited to 100 MiB. The snapshot is removed
after the operation, while results, reports, telemetry, and cloud lineage retain
the original Azure Storage URL.

## Configure a private Blob dataset

1. Sign in and select the subscription:

    ```powershell
    az login
    az account set --subscription <subscription-id>
    ```

2. Have an Azure administrator grant read access at the narrowest practical
   scope:

    ```powershell
    $scope = "/subscriptions/<subscription-id>/resourceGroups/<resource-group>/providers/Microsoft.Storage/storageAccounts/<account>/blobServices/default/containers/<container>"
    az role assignment create `
      --assignee-object-id <user-or-workload-object-id> `
      --assignee-principal-type User `
      --role "Storage Blob Data Reader" `
      --scope $scope
    ```

    Use the actual principal type for automation identities. Role propagation
    can take several minutes.

3. Set the object URL in `agentops.yaml`:

    ```yaml
    version: 1
    agent: "my-agent:1"
    dataset: "https://<account>.blob.core.windows.net/<container>/smoke.jsonl"
    project_endpoint: "https://<resource>.services.ai.azure.com/api/projects/<project>"
    execution: local
    ```

4. Validate access and run the evaluation:

    ```powershell
    agentops eval analyze
    agentops eval run
    ```

`eval analyze` reports `dataset_status: ready` when the identity can read and
validate the remote JSONL. A successful run preserves the Blob URL in
`results.json.dataset_path`.

## Run the evaluation in Foundry

Local execution does not create an item on the Foundry **Evaluations** page.
For a supported Foundry agent, change the execution mode when you want Foundry
to invoke the agent and evaluators server-side:

```yaml
version: 1
agent: "my-agent:1"
dataset: "https://<account>.blob.core.windows.net/<container>/smoke.jsonl"
project_endpoint: "https://<resource>.services.ai.azure.com/api/projects/<project>"
execution: cloud
```

Then run `agentops eval run`. On completion AgentOps writes:

- `results.json` and `report.md`;
- `cloud_evaluation.json` with the Foundry evaluation and run IDs;
- `cloud_output_items.json` with the downloaded per-row cloud results;
- a direct link to the run in the New Foundry Evaluations experience.

Cloud mode first reads and validates the private object through the runner
identity, then synchronizes the content to the selected Foundry project.
Foundry runs the agent and supported evaluators. Runtime-only metrics such as
client-observed latency are skipped because the cloud runner cannot measure
them.

## Use Azure Storage from CI

Use workload identity federation or managed identity rather than credentials in
repository secrets. Grant that principal `Storage Blob Data Reader` on the
container and access to the Foundry project. The same `agentops.yaml` works
locally and in CI; authentication changes with the hosting environment, not with
the dataset setting.

## Security and URL restrictions

The dataset URL must identify one object on a canonical Blob or DFS HTTPS host.
AgentOps rejects query strings, fragments, embedded user information, SAS
tokens, noncanonical hosts, and unsupported schemes before making a network
request. A private storage endpoint must be reachable from the runner; AgentOps
does not provision networking or role assignments.

## Troubleshooting

| Symptom | Likely cause | Resolution |
|---|---|---|
| `AuthorizationPermissionMismatch` or HTTP 403 | The active identity lacks data-plane access, or RBAC has not propagated. | Confirm the object ID used by `az login`, grant `Storage Blob Data Reader`, and retry after propagation. Generic Azure `Reader` or `Contributor` is insufficient. |
| Message says the request may be blocked by network rules | The runner cannot reach the Storage data-plane endpoint. | Allow the runner through the Storage firewall, run inside the private network, or use an approved Network Security Perimeter rule. |
| DNS or connection timeout with a private endpoint | Private DNS or network routing does not resolve or reach the private endpoint from the runner. | Run AgentOps from the connected VNet or fix private DNS and routing. |
| ADLS Gen2 returns 403 despite Blob Reader | Hierarchical namespace ACL traversal is missing. | Add the required execute and read ACLs on the filesystem and parent paths. |
| URL is rejected before download | The URL contains a query string, fragment, embedded credentials, unsupported host, or does not identify one object. | Use the Blob or DFS HTTPS object URL without SAS or other query parameters. |
| Object exceeds the limit | The remote JSONL is larger than 100 MiB. | Split it into smaller evaluation datasets. |
| Evaluation passes locally but is absent from the portal | `execution` is local or omitted. | Set `execution: cloud` for a New Foundry Evaluation, or `publish: true` for the Classic upload path. |
| Cloud mode cannot resolve the target | The agent is not a Foundry `name:version` target, or the project endpoint points elsewhere. | Use an existing prompt-agent version and its owning Foundry project endpoint. |

## Next

Return to the [Evaluation overview](evaluation.md), review the
[Built-in Evaluators](foundry-evaluation-sdk-built-in-evaluators.md), or wire
the evaluation into CI from the [Ship](ship.md) guide.
