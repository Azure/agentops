# Quickstart: Validate Azure Storage Evaluation Datasets

## Prerequisites

- Python 3.11 or newer
- AgentOps installed from the feature branch
- A valid JSONL evaluation dataset smaller than or equal to 100 MiB
- An Azure identity with `Storage Blob Data Reader` on the target container,
  plus any required ADLS Gen2 path ACLs
- Network access from the current runner to the storage endpoint

Install the project and test dependencies:

```powershell
python -m pip install -e .
python -m pip install pytest
```

For identity-based local validation:

```powershell
az login
```

## Scenario 1: Preserve local dataset behavior

Configure the existing local form:

```yaml
version: 1
agent: "travel-agent:1"
dataset: .agentops/data/smoke.jsonl
execution: local
```

Run:

```powershell
agentops eval analyze
agentops eval run
```

Expected outcome:

- Analysis reports the dataset as ready.
- The run reads the same rows and selects the same evaluators as before the
  feature.
- `results.json` contains the resolved local path in `dataset_path`.

## Scenario 2: Load from Azure Blob Storage with identity

Configure:

```yaml
version: 1
agent: "travel-agent:1"
dataset: https://examplestorage.blob.core.windows.net/evals/smoke.jsonl
execution: local
```

Run:

```powershell
agentops eval analyze
agentops eval run
```

Expected outcome:

- No manual download is required.
- Analysis validates access and discovers the same columns as the local object.
- The run processes all rows from one snapshot.
- `results.json` and `report.md` show the validated Blob URL and no temporary
  path.

## Scenario 3: Load from ADLS Gen2 with identity

Change only the dataset reference:

```yaml
dataset: https://examplestorage.dfs.core.windows.net/evals/regression/smoke.jsonl
```

Run:

```powershell
agentops eval analyze
agentops eval run
```

Expected outcome:

- The DFS source resolves with the current identity.
- All valid JSONL rows are processed.
- Diagnostics mention ADLS ACL guidance if data-plane RBAC alone does not grant
  access.

## Scenario 4: Verify identity-only authentication

Run the Blob or ADLS scenario after `az login`, without setting any
storage-specific environment variable.

Expected outcome:

- The run uses the same Azure identity already available to AgentOps.
- No storage token, key, connection string, or SAS is requested.
- A URL containing a query string or embedded credential is rejected before a
  network request.

## Scenario 5: Verify actionable failures

Exercise each case independently:

1. Unsupported host or `http://` URL.
2. Missing object path.
3. Identity without read permission.
4. Runner outside a required private network.
5. Object larger than 100 MiB.
6. Invalid JSONL content.
7. Object modified while its snapshot is resolving.

Expected outcome:

- Every case stops before a completed evaluation is reported.
- The CLI returns exit code `1`.
- The message uses the categories and remediation defined in
  [contracts/dataset-source.md](contracts/dataset-source.md).
- No message contains authorization details or a credential-chain trace.

## Scenario 6: Verify cloud execution

Use a versioned Foundry agent and a remote dataset:

```yaml
version: 1
agent: "travel-agent:1"
dataset: https://examplestorage.blob.core.windows.net/evals/smoke.jsonl
execution: cloud
publish: true
```

Run:

```powershell
agentops eval run
```

Expected outcome:

- AgentOps resolves one snapshot and submits it through the existing cloud
  dataset flow.
- Cloud evaluation lineage remains intact.
- Local artifacts identify the validated Azure Storage source, not the
  temporary file.

## Focused automated coverage

Run the focused tests introduced or extended by this feature:

```powershell
python -m pytest tests/unit/test_dataset_source.py tests/unit/test_agentops_config.py tests/unit/test_eval_analysis.py tests/unit/test_cloud_runner.py tests/unit/test_official_eval.py tests/unit/test_pipeline_orchestrator.py tests/unit/test_pipeline_reporter.py tests/unit/test_prompt_deploy.py tests/unit/test_shared_credentials.py tests/integration/test_cli_flat_schema.py -q
```

Then run the full existing suite:

```powershell
python -m pytest tests/ -x -q
```
