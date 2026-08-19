# Research: Azure Storage Evaluation Datasets

## Decision 1: Preserve the scalar dataset contract

**Decision**: Keep `dataset:` as a single scalar. Accept existing local paths
plus canonical HTTPS object URLs for Blob Storage and ADLS Gen2. Parse remote
references into a typed internal model without changing the YAML shape.

**Rationale**: This is additive, keeps existing workspaces valid, and avoids a
new command or nested configuration block. Canonical endpoints are documented
as `blob.core.windows.net` for Blob Storage and `dfs.core.windows.net` for ADLS
Gen2.

**Alternatives considered**:

- Add a nested `dataset_source:` object: rejected because it changes the simple
  flat configuration and is unnecessary for one source.
- Add CLI flags: rejected because the public CLI surface does not need to
  change.
- Accept arbitrary URLs or `abfss://`: rejected for the first release because
  strict HTTPS host validation is safer and directly supported by Azure SDK
  clients.

**References**:

- https://learn.microsoft.com/azure/storage/blobs/storage-blobs-introduction
- https://learn.microsoft.com/azure/storage/blobs/data-lake-storage-introduction

## Decision 2: Use service-specific SDK clients

**Decision**: Use `BlobClient` for `.blob.core.windows.net` objects and
`DataLakeFileClient` for `.dfs.core.windows.net` files. Add both storage SDKs to
the normal package dependencies and import them lazily in the resolver.

**Rationale**: ADLS Gen2 uses Blob Storage internally, but the DFS endpoint is
the native interface for hierarchical namespace paths. Supporting the URL users
already have avoids endpoint rewriting and HNS edge cases. Both SDKs support
Python 3.11 and use the same Azure Core pipeline and identity abstractions.

**Alternatives considered**:

- Rewrite DFS URLs to Blob endpoints and use only `azure-storage-blob`:
  rejected because it creates endpoint translation behavior and can encounter
  operation compatibility edge cases for externally managed ADLS files.
- Use direct HTTP: rejected because it would duplicate Azure SDK
  authentication, retries, range downloads, and error handling.
- Make ADLS an optional package extra: rejected because ADLS Gen2 is a required
  feature source, not an optional product capability.

**References**:

- https://learn.microsoft.com/python/api/overview/azure/storage-blob-readme
- https://learn.microsoft.com/python/api/overview/azure/storage-file-datalake-readme
- https://learn.microsoft.com/azure/storage/blobs/storage-feature-support-in-storage-accounts

## Decision 3: Authenticate only with the existing Azure identity

**Decision**: Use the project-standard shared Azure credential helper with
`process_timeout=30`. Extract the generic helper to
`utils/azure_credentials.py` and preserve the existing Doctor import as a
compatibility re-export. This covers Azure CLI developer sign-in,
environment/service-principal credentials, workload identity, and managed
identity. Require the least-privilege `Storage Blob Data Reader` role, scoped as
narrowly as practical. Do not add any dataset-specific authentication setting.

**Rationale**: Microsoft recommends passwordless authentication with
`DefaultAzureCredential`. The existing AgentOps project already uses the same
credential family and Windows timeout convention.

**Alternatives considered**:

- Account keys or connection strings: rejected because they grant broad access
  and conflict with passwordless, least-privilege guidance.
- SAS: rejected because it requires users or pipelines to acquire and manipulate
  a separate dataset token instead of reusing the Azure identity already
  running AgentOps.
- Separate explicit credential settings per environment: rejected because the
  standard chain already selects the appropriate identity without configuration
  changes.
- Import the Doctor-specific helper directly from the service layer: rejected
  because a shared utility avoids a services-to-agent architectural dependency.

**References**:

- https://learn.microsoft.com/azure/developer/python/sdk/authentication/credential-chains
- https://learn.microsoft.com/azure/storage/blobs/storage-quickstart-blobs-python
- https://learn.microsoft.com/azure/storage/blobs/assign-azure-role-data-access
- https://learn.microsoft.com/azure/storage/blobs/data-lake-storage-access-control-model

## Decision 4: Reject token-bearing dataset references

**Decision**: Reject remote dataset URLs containing any query string, user
information, or embedded authentication material. Do not introduce a
dataset-specific token environment variable.

**Rationale**: Users should grant the Azure identity already running AgentOps
read permission on the storage object. This keeps local usage aligned with
`az login`, lets CI reuse its managed/workload/service principal identity, and
avoids a second credential lifecycle.

**Alternatives considered**:

- Inline SAS: rejected because it embeds authentication into the dataset
  location and can leak through configuration or tooling.
- SAS environment variable: rejected because users would still need to obtain,
  rotate, and inject an additional token.
- Storage account keys or connection strings: rejected because they are broader
  credentials than required for read-only dataset access.

## Decision 5: Materialize one bounded snapshot

**Decision**: Resolve the object exactly once per operation into a private
temporary JSONL file. Inspect metadata before download, reject content over
100 MiB, stream chunks to disk, and retain the validated source URI, ETag, size, and
last-modified metadata in memory for the operation.

**Rationale**: Existing shape detection, local execution, cloud submission, and
official evaluation all consume a `Path`. A temporary snapshot reuses those
proven readers, avoids holding up to 100 MiB in memory, and guarantees every
row in a run comes from the same completed download.

**Alternatives considered**:

- Teach every reader to consume remote streams: rejected because it duplicates
  network and lifecycle handling across multiple execution paths.
- Download into the repository: rejected because it creates persistent dataset
  duplication and cleanup risk.
- Read the whole object into memory: rejected because a 100 MiB supported
  object would create avoidable memory pressure.
- Cache between runs: deferred because cache invalidation and retention are
  outside the requested scope.

**References**:

- https://learn.microsoft.com/azure/storage/blobs/storage-blob-download-python
- https://learn.microsoft.com/python/api/azure-storage-blob/azure.storage.blob.storagestreamdownloader
- https://learn.microsoft.com/azure/storage/blobs/storage-blob-properties-metadata-python

## Decision 6: Use bounded SDK retries and explicit error categories

**Decision**: Rely on Azure Storage's exponential retry pipeline for transient
network and service failures, with bounded attempts suitable for an interactive
CLI. Do not retry terminal 401/403/404 or malformed-input failures. Map final
Azure Core exceptions and service error codes to stable, actionable AgentOps
categories.

**Rationale**: Azure SDK policies already understand retryable storage
responses. Explicit post-retry mapping keeps CLI messages concise and preserves
exit code `1` for any source resolution failure.

**Alternatives considered**:

- Broad catch with a generic failure: rejected because it obscures remediation.
- Custom retry loops around SDK calls: rejected because they could multiply SDK
  retries and delay CI failures.
- New exit codes per storage error: rejected because the 0/2/1 contract is
  stable.

**References**:

- https://learn.microsoft.com/azure/storage/blobs/storage-retry-policy-python
- https://learn.microsoft.com/python/api/azure-core/azure.core.exceptions
- https://learn.microsoft.com/rest/api/storageservices/blob-service-error-codes

## Decision 7: Keep azd recipe ownership explicit

**Decision**: Do not rewrite `eval.yaml` or inject the AgentOps remote dataset
into `execution: azd`. That backend continues to execute the dataset declared by
the azd recipe. Remote resolution covers AgentOps-owned local/cloud execution,
official Foundry evaluation, and readiness analysis.

**Rationale**: The current azd runner does not consume `AgentOpsConfig.dataset`
during execution. Silent recipe mutation would blur ownership and could make
reported lineage differ from what azd actually evaluated.

**Alternatives considered**:

- Generate a temporary azd recipe pointing to the snapshot: rejected because it
  changes an external tool's source-of-truth and requires separate product
  design.
- Claim support while leaving azd unchanged: rejected because documentation
  must state the boundary explicitly.

## Decision 8: Separate materialization paths from provenance

**Decision**: Resolver output carries distinct `local_path`, `source_uri`, and
`display_name` values. Only `local_path` reaches file readers. Progress,
telemetry, reports, `RunResult.dataset_path`, official evaluation naming, and
cloud dataset lineage use the validated source URI or display name.

**Rationale**: Existing pipeline code derives all of those values from one
`dataset_path`. Reusing a temporary path without an explicit split would leak
implementation details, corrupt lineage, and make remote results irreproducible.

**Alternatives considered**:

- Reuse the temporary Path everywhere: rejected because it violates provenance
  and redaction requirements.
- Rename the temporary file to the object name and treat it as provenance:
  rejected because the local directory remains transient and unrelated to the
  real source.

## Decision 9: Preserve prompt-deploy configuration safely

**Decision**: Candidate prompt-agent configuration keeps the validated remote
URI unchanged instead of resolving it as a local path. The evaluation
environment uses its existing Azure identity.

**Rationale**: `prompt_deploy` is an existing direct `config.dataset` consumer.
Treating a URL as `Path` corrupts it on Windows.

**Alternatives considered**:

- Exclude prompt deploy from the feature: rejected because FR-001 requires all
  AgentOps-owned consumers of the existing field to handle the additive value.
- Add a separate deploy-time storage token: rejected because it creates a second
  authentication mechanism.

## Resolution Status

All technical-context unknowns are resolved. No `NEEDS CLARIFICATION` markers
remain.
