# Running AgentOps Evaluations in GitHub Actions

This guide explains how to add AgentOps evaluation to your CI pipeline using GitHub Actions.

---

## Quick Start

1. **Initialise your workspace** (if you haven't already):

   ```bash
   agentops init
   ```

   This creates the `.agentops/` directory with starter configs, bundles, and datasets.

2. **Generate the workflow file**:

   ```bash
   agentops config cicd
   ```

   This creates `.github/workflows/agentops-eval.yml` in your repository.

3. **Configure GitHub Secrets** (see [Authentication](#authentication) below).

4. **Push a PR** — the evaluation runs automatically.

---

## Required Files

Your repository must contain these files for the workflow to succeed:

| File | Purpose |
| --- | --- |
| `.agentops/run.yaml` | Run specification — references the bundle, dataset, and backend |
| `.agentops/bundles/<name>.yaml` | Evaluation bundle — evaluators + thresholds |
| `.agentops/datasets/<name>.yaml` | Dataset metadata |
| `.agentops/datasets/<name>.jsonl` | Dataset rows (JSONL format) |

All paths in `run.yaml` are relative to the `.agentops/` directory.

### Example `run.yaml`

```yaml
version: 1
bundle:
  path: bundles/model_direct_baseline.yaml
dataset:
  path: datasets/smoke-model-direct.yaml
backend:
  type: foundry
  target: model
  model: gpt-5-mini
  project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
  timeout_seconds: 1800
output:
  write_report: true
```

---

## Authentication

The workflow uses **Workload Identity Federation (OIDC)** — no client secrets to manage or rotate. The GitHub Actions runner exchanges a short-lived OIDC token for an Azure access token at runtime.

#### Azure setup (one-time)

1. **Create or reuse an App Registration** in Azure AD (Microsoft Entra ID).
2. **Add a Federated Credential**:
   - Go to the App Registration → **Certificates & secrets** → **Federated credentials** → **Add credential**
   - Organization: your GitHub org/user
   - Repository: your repo name
   - Entity type: `Pull Request` (for PR triggers) and/or `Branch` (for workflow_dispatch)
   - Name: e.g. `github-agentops-eval`
3. **Grant the app** the required role on your Foundry project (e.g. `Cognitive Services User`).
4. Note the **Application (client) ID**, **Directory (tenant) ID**, and **Subscription ID**.

#### GitHub setup

Set these as **repository variables** (not secrets — they are not confidential):

| Variable | Value |
| --- | --- |
| `AZURE_CLIENT_ID` | Application (client) ID |
| `AZURE_TENANT_ID` | Directory (tenant) ID |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |

Set this as a **repository secret**:

| Secret | Value |
| --- | --- |
| `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` | Foundry project endpoint URL |

Go to **Settings** → **Secrets and variables** → **Actions** → **Variables** tab (for variables) or **Secrets** tab (for the endpoint).

---

## Workflow Triggers

The template workflow triggers on:

| Trigger | When |
| --- | --- |
| `pull_request` | Any PR targeting `main` or `develop` |
| `workflow_dispatch` | Manual run from the Actions tab (supports custom config path and output directory) |

To change which branches trigger evaluations, edit the `on.pull_request.branches` array in the workflow file.

---

## Exit Codes and CI Behaviour

AgentOps returns CI-friendly exit codes that GitHub Actions interprets directly:

| Exit Code | Meaning | CI Result |
| --- | --- | --- |
| `0` | Evaluation succeeded, all thresholds passed | ✅ Job passes |
| `2` | Evaluation succeeded, one or more thresholds failed | ❌ Job fails |
| `1` | Runtime or configuration error | ❌ Job fails |

No special handling is needed — GitHub Actions fails the job on any non-zero exit code.

---

## Artifacts

The workflow uploads the following files as a GitHub Actions artifact named `agentops-eval-results`:

| File | Description |
| --- | --- |
| `results.json` | Machine-readable evaluation results (versioned schema) |
| `report.md` | Human-readable Markdown summary |
| `backend_metrics.json` | Raw backend scores per row |
| `cloud_evaluation.json` | Cloud eval metadata with Foundry portal link (cloud mode only) |
| `backend.stdout.log` | Backend stdout capture |
| `backend.stderr.log` | Backend stderr capture |

Artifacts are uploaded even when the evaluation fails (`if: always()`), so you can always inspect results.

### Downloading artifacts

From the **Actions** tab → select the workflow run → scroll to **Artifacts** → click to download.

---

## PR Comments

When triggered by a pull request, the workflow automatically posts (or updates) a PR comment containing the full `report.md` content. This gives reviewers immediate visibility into evaluation results without downloading artifacts.

The comment is identified by a hidden HTML marker (`<!-- agentops-eval-report -->`) so subsequent pushes to the same PR update the existing comment rather than creating duplicates.

---

## Job Summary

The workflow writes a [GitHub Actions Job Summary](https://docs.github.com/en/actions/using-workflows/workflow-commands-for-github-actions#adding-a-job-summary) that includes:

- Pass/fail status banner
- Full `report.md` content (when available)

This is visible on the workflow run page without downloading artifacts.

---

## CLI Command Reference

### Generate the workflow

```bash
agentops config cicd
```

Options:

| Flag | Description | Default |
| --- | --- | --- |
| `--dir PATH` | Target repository root directory | `.` (current directory) |
| `--force` | Overwrite existing workflow file | `false` |

### Regenerate (overwrite)

```bash
agentops config cicd --force
```

---

## Customisation

### Using a different config path

With `workflow_dispatch`, you can specify a custom config path:

```bash
agentops eval run --config path/to/custom-run.yaml
```

Or modify the workflow's default:

```yaml
steps:
  - name: Run evaluation
    run: agentops eval run --config .agentops/my-custom-run.yaml
```

### Using a custom output directory

```yaml
steps:
  - name: Run evaluation
    run: agentops eval run --config .agentops/run.yaml --output ./eval-output
```

Update the artifact upload paths accordingly.

### Running multiple evaluations

To run several evaluation configs in a single workflow, use a matrix strategy:

```yaml
jobs:
  evaluate:
    strategy:
      fail-fast: false
      matrix:
        config:
          - .agentops/runs/model-direct.yaml
          - .agentops/runs/rag-retrieval.yaml
          - .agentops/runs/agent-tools.yaml
    steps:
      # ...
      - name: Run evaluation
        run: agentops eval run --config ${{ matrix.config }}
```

### Skipping the PR comment

Remove or comment out the "Post report as PR comment" step in the workflow.

---

## Troubleshooting

| Problem | Solution |
| --- | --- |
| `Error: evaluation failed: ...` (exit 1) | Check that `.agentops/run.yaml` exists, config is valid YAML, and secrets are set |
| `Threshold status: FAILED` (exit 2) | Review `report.md` — thresholds are too strict or model quality regressed |
| Missing artifacts | Ensure `.agentops/results/latest/` is not in `.gitignore` — the workflow reads this path |
| Authentication errors | Verify the federated credential entity matches your repo/branch; check that `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` are set as repository variables; confirm the app registration has access to the Foundry project |
| `agentops: command not found` | Ensure `pip install agentops-toolkit` runs before the eval step |

---

## Internal CI/CD Workflows (Contributors)

If you are contributing to the agentops-toolkit repository itself, the project has separate CI/CD workflows for building and releasing the package:

| Workflow | Trigger | Purpose |
| --- | --- | --- |
| `ci.yml` | Push to `develop`, PRs to `main`/`develop` | Lint (ruff) + test (matrix) + coverage |
| `_build.yml` | Called by staging/release | Reusable lint + test + build package |
| `staging.yml` | Push to `release/**` | Build → TestPyPI → verify install |
| `release.yml` | Push `v*` tag | TestPyPI → PyPI (with approval) → GitHub Release |
| `cut-release.yml` | Manual dispatch (Actions tab button) | Create release branch from `develop`, update CHANGELOG, open PR to `main` |

The **Cut Release** workflow provides a one-click way to start a release: enter a version number in the Actions UI, and it creates the release branch, updates the changelog, and opens the PR automatically.

For full details, see [release-process.md](release-process.md).
