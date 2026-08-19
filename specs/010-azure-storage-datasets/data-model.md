# Data Model: Azure Storage Evaluation Datasets

## Dataset Reference

Represents the scalar value supplied through `agentops.yaml`. Classification
occurs on the untouched YAML string before any `Path` coercion so HTTPS URLs are
not rewritten on Windows.

| Field | Type | Rules |
|---|---|---|
| `kind` | `local`, `blob`, or `adls` | Derived from value shape and endpoint |
| `original` | path or URI string | Untouched scalar used for classification; never logged before validation |
| `source_uri` | optional URI string | Validated remote HTTPS URI; absent for local references |
| `account` | optional string | Required for remote references; valid Azure Storage account label |
| `container_or_filesystem` | optional string | Required and non-empty for remote references |
| `object_path` | optional string | Required and non-empty; identifies exactly one object |

### Validation rules

- Local references retain existing relative and absolute path behavior.
- Remote references must use HTTPS.
- Blob references must target `<account>.blob.core.windows.net`.
- ADLS references must target `<account>.dfs.core.windows.net`.
- Remote references must identify both a container/file system and an object
  path.
- Fragments, user information, wildcard paths, directories, arbitrary hosts,
  query strings, and unsupported schemes are rejected.

## Access Context

Represents how the resolver authorizes a read.

| Field | Type | Rules |
|---|---|---|
| `credential` | runtime-only credential | Never serialized, logged, or placed in result models |
| `source` | enum | `azure_identity` |

### Validation rules

- Access uses the standard AgentOps Azure credential settings, including
  `process_timeout=30`.
- Dataset-specific tokens, SAS, account keys, and connection strings are
  unsupported.
- The access context is discarded after snapshot resolution.

## Dataset Snapshot

Represents the stable local file consumed by existing dataset readers.

| Field | Type | Rules |
|---|---|---|
| `local_path` | absolute path | Existing local source or private temporary JSONL file |
| `source` | Dataset Reference | Used for validated display and provenance |
| `display_name` | string | Object filename or local filename; no query values |
| `size_bytes` | integer | Non-negative and no greater than 100 MiB |
| `etag` | optional string | Captured for remote source consistency diagnostics |
| `last_modified` | optional timestamp | Captured from remote metadata when available |
| `temporary` | boolean | Controls cleanup responsibility |

### Relationships

- One Dataset Reference resolves to one Dataset Snapshot per operation.
- One Dataset Snapshot supplies every row to one analysis or evaluation
  operation.
- One Access Context may be used only during resolution and is not retained by
  the snapshot.
- A remote Run Result records `DatasetReference.source_uri`, never
  `DatasetSnapshot.local_path` when the snapshot is temporary.
- Cloud lineage, official evaluation names, progress, telemetry, reports, and
  generated candidate configuration use `source_uri` or `display_name`, never a
  temporary path.

### State transitions

```text
unparsed
  -> validated
  -> metadata_checked
  -> downloading
  -> resolved
  -> consumed
  -> cleaned
```

Terminal failure transitions:

```text
unparsed -> malformed
validated -> authentication_failed | authorization_failed | not_found
metadata_checked -> oversized
downloading -> connectivity_failed | service_unavailable | source_changed
resolved -> invalid_content
```

No failed state may transition to a completed evaluation result.

## Dataset Source Diagnosis

Represents the bounded result used by `agentops eval analyze`.

| Field | Type | Meaning |
|---|---|---|
| `status` | stable category | `ready`, `malformed`, `not_found`, `authentication_failed`, `authorization_failed`, `connectivity_failed`, `service_unavailable`, `oversized`, `invalid_content`, or `unknown` |
| `source` | validated string | Safe local path or remote URI |
| `columns` | set of strings | Columns discovered from the resolved JSONL sample |
| `message` | string | Concise remediation with no secret-bearing values |
| `access_checked` | boolean | Whether the current environment completed a remote access probe |

Analysis diagnostics and runtime resolution use the same classification rules,
but analysis returns a readiness result while runtime raises an explicit error.
