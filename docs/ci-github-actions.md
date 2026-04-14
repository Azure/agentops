# Running AgentOps Evaluations in GitHub Actions

This guide explains how to add AgentOps evaluation to your CI/CD pipeline using GitHub Actions. Inspired by [GenAIOps Git Workflow](https://github.com/Azure/GenAIOps/blob/main/documentation/git_workflow.md) and [Foundry CI/CD patterns](https://github.com/balakreshnan/foundrycicdbasic), AgentOps generates up to three pipeline types tailored to your project.

## Pipeline Types

`agentops workflow generate` auto-detects which pipelines to create based on your `.agentops/` workspace:

| Pipeline | File | Trigger | Purpose |
| -------- | ---- | ------- | ------- |
| **PR Evaluation** | `agentops-eval.yml` | Pull requests to main/develop | Gate PRs on evaluation thresholds |
| **CI Evaluation** | `agentops-eval-ci.yml` | Push to develop/main | Post-merge comprehensive evaluation with optional matrix strategy |
| **CD Pipeline** | `agentops-eval-cd.yml` | Push to main | Safety QA evaluation gate + deploy placeholder |

### Auto-Detection Rules

- **PR pipeline** — always generated.
- **CI pipeline** — generated when multiple bundles or run configs exist in `.agentops/`.
- **CD pipeline** — generated alongside the CI pipeline (same detection rule).

To override auto-detection, simply delete any unwanted workflow file after generation.

### Branching Strategy

The pipeline suite maps to the Git Flow branching model:

```
feature/* → PR to develop   → agentops-eval.yml (PR gate)
             merge to develop → agentops-eval-ci.yml (CI evaluation)
             release/* → PR to main → agentops-eval.yml (PR gate)
             merge to main   → agentops-eval-cd.yml (safety QA → deploy)
```

## Quick Start

1. **Initialise your workspace** (if you haven't already):

   ```bash
   agentops init
   ```

   This creates the `.agentops/` directory with starter configs, bundles, and datasets.

2. **Generate the workflow files**:

   ```bash
   agentops workflow generate
   ```

   This creates one or more files in `.github/workflows/` based on your workspace content.

3. **Configure GitHub Secrets** (see [Authentication](#authentication) below).

4. **Push a PR** — the PR evaluation runs automatically. Merge to trigger the CI evaluation.

## Required Files

Your repository must contain these files for the workflow to succeed:

| File                              | Purpose                                                         |
| --------------------------------- | --------------------------------------------------------------- |
| `.agentops/run.yaml`              | Run specification — references the bundle, dataset, and backend |
| `.agentops/bundles/<name>.yaml`   | Evaluation bundle — evaluators + thresholds                     |
| `.agentops/datasets/<name>.yaml`  | Dataset metadata                                                |
| `.agentops/datasets/<name>.jsonl` | Dataset rows (JSONL format)                                     |

All paths in `run.yaml` are relative to the `.agentops/` directory.

### Example `run.yaml`

```yaml
version: 1
target:
  type: model
  hosting: foundry
  execution_mode: remote
  endpoint:
    kind: foundry_agent
    model: gpt-4o
    project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
bundle:
  name: model_quality_baseline
dataset:
  name: smoke-model-direct
execution:
  timeout_seconds: 1800
output:
  write_report: true
```

## Authentication

The workflow uses **Workload Identity Federation (OIDC)** — no client secrets to manage or rotate. The GitHub Actions runner exchanges a short-lived OIDC token for an Azure access token at runtime.

#### Azure setup (one-time)

1. **Create or reuse an App Registration** in Azure AD (Microsoft Entra ID).
2. **Add a Federated Credential**:
   - Go to the App Registration → **Certificates & secrets** → **Federated credentials** → **Add credential**
   - Organization: your GitHub org/user
   - Repository: your repo name
   - Entity type: `Pull Request` (for PR triggers) **and** `Branch` (for CI, CD, and workflow_dispatch triggers)
   - Name: e.g. `github-agentops-eval`
3. **Grant the app** the required roles on your Foundry project:
   - `Cognitive Services User` — invoke agents and evaluator models
   - `Azure AI Developer` — access evaluation APIs and Foundry features
4. Note the **Application (client) ID**, **Directory (tenant) ID**, and **Subscription ID**.

#### GitHub setup

Set these as **repository variables** (not secrets — they are not confidential):

| Variable                | Value                   |
| ----------------------- | ----------------------- |
| `AZURE_CLIENT_ID`       | Application (client) ID |
| `AZURE_TENANT_ID`       | Directory (tenant) ID   |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID   |

Set this as a **repository secret**:

| Secret                              | Value                        |
| ----------------------------------- | ---------------------------- |
| `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` | Foundry project endpoint URL |

Go to **Settings** → **Secrets and variables** → **Actions** → **Variables** tab (for variables) or **Secrets** tab (for the endpoint).

## Workflow Triggers

Each pipeline type has different triggers:

### PR Evaluation (`agentops-eval.yml`)

| Trigger             | When                                                                               |
| ------------------- | ---------------------------------------------------------------------------------- |
| `pull_request`      | Any PR targeting `main` or `develop`                                               |
| `workflow_dispatch` | Manual run from the Actions tab (supports custom config path and output directory) |

### CI Evaluation (`agentops-eval-ci.yml`)

| Trigger             | When                                                                               |
| ------------------- | ---------------------------------------------------------------------------------- |
| `push`              | Push to `develop` or `main` (path filter: `.agentops/**`, `src/**`, `pyproject.toml`) |
| `workflow_dispatch` | Manual run from the Actions tab                                                    |

### CD Pipeline (`agentops-eval-cd.yml`)

| Trigger             | When                                                                               |
| ------------------- | ---------------------------------------------------------------------------------- |
| `push`              | Push to `main`                                                                     |
| `workflow_dispatch` | Manual run from the Actions tab (supports `skip_safety` input)                     |

The CD pipeline has two jobs: **safety-qa** (runs evaluation as a quality gate) and **deploy** (placeholder for deployment commands). The deploy job only runs if the safety-qa job passes.

To change which branches trigger evaluations, edit the branch arrays in the workflow files.

## Exit Codes and CI Behaviour

AgentOps returns CI-friendly exit codes that GitHub Actions interprets directly:

| Exit Code | Meaning                                             | CI Result    |
| --------- | --------------------------------------------------- | ------------ |
| `0`       | Evaluation succeeded, all thresholds passed         | ✅ Job passes |
| `2`       | Evaluation succeeded, one or more thresholds failed | ❌ Job fails  |
| `1`       | Runtime or configuration error                      | ❌ Job fails  |

No special handling is needed — GitHub Actions fails the job on any non-zero exit code.

## Artifacts

Each pipeline uploads files as GitHub Actions artifacts:

| Pipeline | Artifact name | Contents |
| -------- | ------------- | -------- |
| PR Evaluation | `agentops-eval-results` | results.json, report.md, backend_metrics.json, cloud_evaluation.json, logs |
| CI Evaluation | `agentops-ci-eval-results` | Same as above |
| CD Pipeline | `agentops-cd-safety-results` | Same as above (from safety-qa job) |

Individual files in the artifact:

| File                    | Description                                                    |
| ----------------------- | -------------------------------------------------------------- |
| `results.json`          | Machine-readable evaluation results (versioned schema)         |
| `report.md`             | Human-readable Markdown summary                                |
| `backend_metrics.json`  | Raw backend scores per row                                     |
| `cloud_evaluation.json` | Cloud eval metadata with Foundry portal link (cloud mode only) |
| `backend.stdout.log`    | Backend stdout capture                                         |
| `backend.stderr.log`    | Backend stderr capture                                         |

Artifacts are uploaded even when the evaluation fails (`if: always()`), so you can always inspect results.

### Downloading artifacts

From the **Actions** tab → select the workflow run → scroll to **Artifacts** → click to download.

## PR Comments

When triggered by a pull request, the workflow automatically posts (or updates) a PR comment containing the full `report.md` content. This gives reviewers immediate visibility into evaluation results without downloading artifacts.

The comment is identified by a hidden HTML marker (`<!-- agentops-eval-report -->`) so subsequent pushes to the same PR update the existing comment rather than creating duplicates.

## Job Summary

The workflow writes a [GitHub Actions Job Summary](https://docs.github.com/en/actions/using-workflows/workflow-commands-for-github-actions#adding-a-job-summary) that includes:

- Pass/fail status banner
- Full `report.md` content (when available)

This is visible on the workflow run page without downloading artifacts.

## CLI Command Reference

### Generate the workflows

```bash
agentops workflow generate
```

This auto-detects which pipelines to generate based on your `.agentops/` workspace content.

Options:

| Flag         | Description                       | Default                 |
| ------------ | --------------------------------- | ----------------------- |
| `--dir PATH` | Target repository root directory  | `.` (current directory) |
| `--force`    | Overwrite existing workflow files | `false`                 |

### Regenerate (overwrite)

```bash
agentops workflow generate --force
```

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

## CD Pipeline

The CD pipeline (`agentops-eval-cd.yml`) is generated alongside the CI pipeline when multiple bundles or run configs exist in the workspace. It runs on pushes to `main` and acts as a deployment gate.

### How it works

1. The **safety-qa** job runs `agentops eval run` to evaluate the model/agent.
2. If evaluation passes (exit code 0), the **deploy** job runs.
3. If thresholds fail (exit code 2) or an error occurs (exit code 1), the deploy job is skipped.
4. The deploy job is a **placeholder** — fill it in with your deployment commands.

### Skipping safety checks

For emergency deployments, use `workflow_dispatch` with the `skip_safety` input set to `true`. This skips the safety-qa job and runs the deploy job directly.

### Adding deployment steps

Edit the `deploy` job in `agentops-eval-cd.yml` and replace the placeholder with your deployment commands:

```yaml
deploy:
  name: Deploy
  needs: safety-qa
  runs-on: ubuntu-latest
  # environment: production  # Uncomment for manual approval gate
  steps:
    - uses: actions/checkout@v4
    - name: Deploy to production
      run: |
        # Your deployment commands here, e.g.:
        # az webapp deploy ...
        # kubectl apply ...
        # azd deploy ...
```

### Adding environment approval

Uncomment `environment: production` in the deploy job to require manual approval before deployment. Configure the environment in GitHub Settings → Environments.

## CI Evaluation Pipeline

The CI pipeline (`agentops-eval-ci.yml`) is generated when multiple bundles or run configs exist. It runs after merges for comprehensive evaluation.

### Enabling matrix strategy

Uncomment the matrix block in the CI workflow and list your run configs:

```yaml
strategy:
  fail-fast: false
  matrix:
    config:
      - .agentops/run.yaml
      - .agentops/runs/rag-retrieval.yaml
      - .agentops/runs/agent-tools.yaml
```

### Enabling baseline comparison

Uncomment the comparison step in the CI workflow. Store a baseline run ID and compare automatically:

```yaml
- name: Compare against baseline
  run: |
    BASELINE=$(cat .agentops/results/baseline_id.txt)
    CURRENT=$(jq -r '.run_id' .agentops/results/latest/results.json)
    agentops eval compare --runs "$BASELINE,$CURRENT" -f md
```

## Troubleshooting

| Problem                                  | Solution                                                                                                                                                                                                                                  |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Error: evaluation failed: ...` (exit 1) | Check that `.agentops/run.yaml` exists, config is valid YAML, and secrets are set                                                                                                                                                         |
| `Threshold status: FAILED` (exit 2)      | Review `report.md` — thresholds are too strict or model quality regressed                                                                                                                                                                 |
| Missing artifacts                        | Ensure `.agentops/results/latest/` is not in `.gitignore` — the workflow reads this path                                                                                                                                                  |
| Authentication errors                    | Verify the federated credential entity matches your repo/branch; check that `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` are set as repository variables; confirm the app registration has access to the Foundry project |
| `agentops: command not found`            | Ensure `pip install agentops-toolkit` runs before the eval step                                                                                                                                                                           |
| Only PR workflow generated               | Auto-detection found a single bundle — this is expected; add bundles or run configs to trigger CI/CD pipelines                                                                                             |

## Internal CI/CD Workflows (Contributors)

If you are contributing to the agentops-toolkit repository itself, the project has separate CI/CD workflows for building and releasing the package:

| Workflow          | Trigger                                    | Purpose                                                                   |
| ----------------- | ------------------------------------------ | ------------------------------------------------------------------------- |
| `ci.yml`          | Push to `develop`, PRs to `main`/`develop` | Lint (ruff) + test (matrix) + coverage                                    |
| `_build.yml`      | Called by staging/release                  | Reusable lint + test + build package                                      |
| `staging.yml`     | Push to `release/**`                       | Build → TestPyPI → verify install                                         |
| `release.yml`     | Push `v*` tag                              | TestPyPI → PyPI (with approval) → GitHub Release                          |
| `cut-release.yml` | Manual dispatch (Actions tab button)       | Create release branch from `develop`, update CHANGELOG, open PR to `main` |

The **Cut Release** workflow provides a one-click way to start a release: enter a version number in the Actions UI, and it creates the release branch, updates the changelog, and opens the PR automatically.

For full details, see [release-process.md](release-process.md).
