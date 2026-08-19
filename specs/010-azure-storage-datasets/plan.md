# Implementation Plan: Azure Storage Evaluation Datasets

**Branch**: `placerda-azure-storage-datasets` | **Date**: 2026-08-18 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/010-azure-storage-datasets/spec.md`

## Summary

Extend the existing scalar `dataset:` setting so it accepts either the current
local JSONL path or an HTTPS object URL from Azure Blob Storage or ADLS Gen2.
A pure core parser classifies and validates the reference. A service-layer
resolver lazily loads the Azure SDK, reuses the project-standard Azure
credential context, and streams one size-bounded snapshot to a temporary local
JSONL file. Existing dataset validation and local/cloud/official evaluation
readers then consume that snapshot unchanged. The original source, never the
temporary path, is retained in progress output and `dataset_path` provenance.

The first implementation supports one JSONL object per evaluation, caps remote
inputs at 100 MiB, and leaves `execution: azd` recipe-owned dataset behavior
unchanged because azd does not consume the `agentops.yaml` dataset field during
execution.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: Pydantic v2, `azure-identity`; add
`azure-storage-blob>=12.20,<13` and
`azure-storage-file-datalake>=12.20,<13`, imported lazily inside runtime
functions


**Storage**: Existing local JSONL files; read-only Azure Blob Storage and ADLS
Gen2 objects; short-lived local temporary files for resolved snapshots


**Testing**: pytest with mocked Azure clients and credentials; existing CLI and
pipeline integration tests


**Target Platform**: Windows, Linux, and macOS developer workstations plus
non-interactive CI runners with Azure network access


**Project Type**: Python CLI and library


**Performance Goals**: In a healthy connected environment, 95% of remote
datasets up to 100 MiB begin row processing within 30 seconds


**Constraints**: Read-only access; HTTPS only; one object per run; JSONL content
contract unchanged; 100 MiB maximum; one immutable-for-the-run snapshot; no
dataset-specific tokens, keys, connection strings, SAS, query strings, or
embedded credentials; `DefaultAzureCredential` paths preserve
`process_timeout=30`; no new command or flag; exit codes remain 0/2/1


**Scale/Scope**: One dataset reference and one downloaded snapshot per analysis
or evaluation operation; Blob and DFS endpoints in public Azure; sovereign
cloud endpoint support is deferred until endpoint suffixes can be configured
without weakening URI validation

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate | Pre-design assessment | Post-design assessment |
|---|---|---|
| Preserve public contracts | PASS: `dataset:` remains one scalar value; local paths, output schemas, commands, and exit codes remain compatible. | PASS: the contract adds accepted URI forms without removing or reinterpreting existing values. `RunResult.dataset_path` remains a string and receives only validated provenance. |
| Enforce architectural boundaries | PASS: URI classification belongs in pure `core/`; Azure and filesystem I/O belong in `services/`; pipeline entry points only orchestrate resolved snapshots. | PASS: no network or filesystem I/O is assigned to `core/`, and no CLI business logic is introduced. |
| Isolate Azure runtime integration | PASS: storage SDK imports are lazy, credentials follow the Windows timeout convention, and errors are classified explicitly. | PASS: design adds mocked boundaries for both SDK clients and keeps core/config tests runnable without Azure credentials. |
| Keep release evidence trustworthy | PASS: remote storage is read-only and AgentOps does not provision or mutate Azure resources. | PASS: temporary paths and authentication details are excluded from normalized results, reports, diagnostics, and evidence. |
| Verify every behavior change | PASS: focused config, parser, resolver, analysis, pipeline, and compatibility tests are identified. | PASS: quickstart and contracts define observable outcomes for local, Blob, ADLS, identity, denied, missing, malformed, oversized, and mutable-source cases. |
| Product and workflow constraints | PASS: schema evolution is additive; no new command, flag, output version, or exit-code meaning is required. | PASS: user-visible documentation, template guidance, and changelog coverage are included; no constitutional exception is required. |

## Project Structure

### Documentation (this feature)

```text
specs/010-azure-storage-datasets/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── dataset-source.md
└── tasks.md
```

### Source Code (repository root)

```text
pyproject.toml
CHANGELOG.md
docs/
├── evaluation.md
└── how-it-works.md

src/agentops/
├── core/
│   ├── agentops_config.py       # additive dataset scalar typing/validation
│   └── dataset_source.py        # pure URI parsing, classification, validation
├── services/
│   ├── dataset_source.py        # Azure download, snapshot lifecycle, error mapping
│   └── eval_analysis.py         # remote readiness and safe source signals
├── pipeline/
│   ├── orchestrator.py          # resolve once; separate reader path from provenance
│   ├── cloud_runner.py          # source-aware cloud lineage and dataset naming
│   ├── official_eval.py         # resolve once for official Foundry eval
│   └── prompt_deploy.py         # preserve validated remote source in candidate config
├── utils/
│   └── azure_credentials.py     # shared Azure CLI/DAC credential helper
├── agent/sources/
│   └── _credentials.py          # compatibility re-export of shared helper
└── templates/
    └── agentops.yaml            # local, Blob, and DFS examples

tests/
├── unit/
│   ├── test_agentops_config.py
│   ├── test_dataset_source.py
│   ├── test_eval_analysis.py
│   ├── test_cloud_runner.py
│   ├── test_official_eval.py
│   ├── test_pipeline_orchestrator.py
│   ├── test_pipeline_reporter.py
│   ├── test_prompt_deploy.py
│   └── test_shared_credentials.py
└── integration/
    └── test_cli_flat_schema.py
```

**Structure Decision**: Keep dataset source parsing in a new pure core module
and all Azure SDK/network/temp-file behavior in a new service module. Existing
pipeline readers continue to receive a `Path`, which minimizes risk across
shape detection, local execution, cloud submission, and official evaluation.
`eval analyze` uses the same resolver in bounded inspection mode so readiness
and runtime classify failures consistently.

## Design Decisions

1. `dataset:` continues to be a YAML scalar. A pre-validation classifier must
   inspect the untouched YAML string before Pydantic can coerce it to `Path`;
   local values become `Path`, while valid Azure Storage HTTPS values remain
   strings represented by a pure `DatasetReference`. Every direct
   `config.dataset` consumer is updated to use the classifier rather than Path
   methods.
2. Accepted remote endpoints are
   `https://<account>.blob.core.windows.net/<container>/<path>` and
   `https://<account>.dfs.core.windows.net/<filesystem>/<path>`. `abfs(s)://`,
   account URLs without an object path, arbitrary hosts, HTTP, directories, and
   wildcard references are rejected.
3. Azure identity is the only supported authentication model and uses a shared
   credential helper
   extracted to `utils/azure_credentials.py`; the existing Doctor helper
   re-exports it so Azure CLI preference, caching, and `process_timeout=30`
   remain consistent. Remote references containing query strings, user
   information, SAS, keys, or connection strings are rejected before any
   network request.
4. The resolver checks object metadata, rejects objects over 100 MiB, streams
   to a private temporary file, captures ETag/size/last-modified metadata, and
   uses the completed file for the entire operation.
5. Azure SDK retry behavior handles transient requests with bounded exponential
   retry. Final exceptions are mapped to malformed, not found, authentication,
   authorization, connectivity, service unavailable, source changed, oversized,
   and invalid-content diagnoses.
6. Resolver output deliberately separates `local_path` (reader-only) from
   `source_uri`, `display_name`, and remote metadata. Local/cloud orchestrator
   progress, telemetry, `RunResult.dataset_path`, reporter output, official
   evaluation names, and cloud dataset lineage must use the source/display
   fields. `cloud_runner` may receive the temporary file for upload but must not
   serialize it as `local_path` lineage for a remote source.
7. Prompt-agent candidate config generation preserves the validated remote URI
   instead of applying Path operations. Authentication continues to come from
   the Azure identity available in the evaluation environment.
8. `execution: azd` remains based on the dataset declared by `eval.yaml`.
   AgentOps does not silently rewrite that external recipe. Remote
   `agentops.yaml` dataset resolution applies to AgentOps-owned local, cloud,
   official-eval, and readiness-analysis paths.

## Complexity Tracking

No constitution violations or justified complexity exceptions are required.
