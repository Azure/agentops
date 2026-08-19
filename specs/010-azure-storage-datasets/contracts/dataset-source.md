# Contract: Evaluation Dataset Source

## Configuration

The existing `dataset` field remains required and scalar.

### Local source

```yaml
version: 1
agent: "travel-agent:1"
dataset: .agentops/data/smoke.jsonl
```

Relative paths resolve from the directory containing `agentops.yaml`. Absolute
paths retain their existing behavior.

### Azure Blob Storage source

```yaml
version: 1
agent: "travel-agent:1"
dataset: https://examplestorage.blob.core.windows.net/evals/smoke.jsonl
```

Accepted grammar:

```text
https://<account>.blob.core.windows.net/<container>/<object-path>.jsonl
```

### Azure Data Lake Storage Gen2 source

```yaml
version: 1
agent: "travel-agent:1"
dataset: https://examplestorage.dfs.core.windows.net/evals/regression/smoke.jsonl
```

Accepted grammar:

```text
https://<account>.dfs.core.windows.net/<filesystem>/<file-path>.jsonl
```

The first release does not accept `http://`, `abfs://`, `abfss://`, arbitrary
hosts, account/container roots, directories, wildcards, or multiple objects.

## Authentication

AgentOps uses the standard Azure identity context already available to the
process:

- Local development: the current `az login` identity.
- Azure-hosted execution: managed identity or workload identity.
- CI/CD: the pipeline's existing federated identity or service principal.

No dataset-specific token or credential setting is introduced. URLs containing
query strings or embedded credentials are rejected. SAS, account keys, and
connection strings are not accepted.

Identity-based access requires data-plane read permission. The recommended
least-privilege assignment is `Storage Blob Data Reader`, scoped to the
container when practical. ADLS Gen2 path ACLs may impose additional access
requirements.

## Content contract

- The remote object must satisfy the same UTF-8 JSONL row contract as a local
  AgentOps dataset.
- Blank lines retain existing handling.
- Each non-blank line must be a JSON object.
- Existing dataset-shape and evaluator-selection validation remains
  authoritative.
- One object is resolved once and used for the entire operation.
- Objects larger than 100 MiB are rejected before download when size metadata is
  available and during download if the byte count crosses the limit.

## Provenance and redaction

`results.json` retains the existing `dataset_path` string.

- Local dataset: absolute resolved path, as today.
- Remote dataset: validated HTTPS source.

Progress messages, reports, analysis signals, telemetry attributes, errors, and
release evidence must use the same validated source. Temporary paths,
authorization headers, and credential-chain details must not appear.

Cloud dataset lineage must identify the validated remote source and must not
serialize the temporary materialization path as a local source. Prompt-agent
candidate configuration must preserve the validated URI and continue using the
Azure identity available in its execution environment.

## Error contract

All resolution failures are runtime or configuration errors and therefore
produce existing exit code `1`. Exit code `2` remains reserved for completed
evaluations whose gates fail.

| Category | Trigger | Required guidance |
|---|---|---|
| `malformed` | Unsupported scheme, host, or missing object path | Show accepted Blob and DFS forms |
| `not_found` | Account endpoint resolves but container/file system/object is absent | Identify validated source and verify path |
| `authentication_failed` | No usable Azure identity | Recommend `az login` locally or verify the workload identity configuration |
| `authorization_failed` | Identity authenticates but lacks read access | Recommend `Storage Blob Data Reader` and ADLS ACL review |
| `connectivity_failed` | DNS, firewall, private endpoint, or transport failure | Check runner network and private connectivity |
| `service_unavailable` | Azure Storage transient failures exhausted retries | Retry later and inspect Azure service health |
| `source_changed` | Source consistency condition fails during resolution | Retry after source writes complete |
| `oversized` | Object exceeds 100 MiB | Reduce or partition the dataset |
| `invalid_content` | Download succeeds but JSONL validation fails | Apply existing local dataset row guidance |

Error text may include the validated source and stable service error category,
but not an authorization header or full credential-chain trace.

## Execution boundaries

- `execution: local`: supported through a resolved snapshot.
- `execution: cloud`: supported; the snapshot is supplied to the existing cloud
  dataset submission flow.
- Official Foundry evaluation entry point: supported through a resolved
  snapshot.
- `agentops eval analyze`: supported with bounded access and content inspection.
- Prompt-agent candidate configuration generation: supported with validated URI
  preservation and the environment's existing Azure identity.
- `execution: azd`: unchanged; azd uses the dataset declared in `eval.yaml`.
