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

| File                              | Purpose                                                         |
| --------------------------------- | --------------------------------------------------------------- |
| `.agentops/run.yaml`              | Run specification — references the bundle, dataset, and backend |
| `.agentops/bundles/<name>.yaml`   | Evaluation bundle — evaluators + thresholds                     |
| `.agentops/datasets/<name>.yaml`  | Dataset metadata                                                |\n| `.agentops/data/<name>.jsonl`     | Dataset rows (JSONL format)                                     |", "oldString": "| `.agentops/datasets/<name>.yaml`  | Dataset metadata                                                |\n| `.agentops/datasets/<name>.jsonl` | Dataset rows (JSONL format)                                     |

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

---

## Workflow Triggers

The template workflow triggers on:

| Trigger             | When                                                                               |
| ------------------- | ---------------------------------------------------------------------------------- |
| `pull_request`      | Any PR targeting `main` or `develop`                                               |
| `workflow_dispatch` | Manual run from the Actions tab (supports custom config path and output directory) |

To change which branches trigger evaluations, edit the `on.pull_request.branches` array in the workflow file.

---

## Exit Codes and CI Behaviour

AgentOps returns CI-friendly exit codes that GitHub Actions interprets directly:

| Exit Code | Meaning                                             | CI Result    |
| --------- | --------------------------------------------------- | ------------ |
| `0`       | Evaluation succeeded, all thresholds passed         | ✅ Job passes |
| `2`       | Evaluation succeeded, one or more thresholds failed | ❌ Job fails  |
| `1`       | Runtime or configuration error                      | ❌ Job fails  |

No special handling is needed — GitHub Actions fails the job on any non-zero exit code.

---

## Artifacts

The workflow uploads the following files as a GitHub Actions artifact named `agentops-eval-results`:

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

| Flag         | Description                      | Default                 |
| ------------ | -------------------------------- | ----------------------- |
| `--dir PATH` | Target repository root directory | `.` (current directory) |
| `--force`    | Overwrite existing workflow file | `false`                 |

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

## CI/CD Integration Models

AgentOps supports several integration models depending on your team's workflow. Choose the one that fits your CI/CD strategy.

### PR Quality Gate (default)

Run evaluations on every pull request. The evaluation result gates whether the PR can merge.

```
PR opened → agentops eval run → exit code 0 → merge allowed
                                 exit code 2 → merge blocked (thresholds failed)
```

This is what the generated workflow template provides out of the box. Use this when evaluation quality should directly block code changes.

**When to use:** Teams that want to prevent quality regressions before merging.

### Scheduled Regression Detection

Run evaluations on a schedule (nightly, weekly) to detect model or agent degradation over time without blocking PRs.

Add a `schedule` trigger to the workflow:

```yaml
on:
  schedule:
    - cron: '0 2 * * 1'  # Every Monday at 2 AM UTC
  workflow_dispatch:
```

Combine with `agentops eval compare --runs latest,previous` to detect regressions across runs.

**When to use:** Teams that need ongoing quality monitoring independent of code changes (e.g. model deployment changes, data drift).

### Post-Deployment Validation

Run evaluations after deploying to an environment to verify the deployed agent or model meets quality standards.

```yaml
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy agent
        run: az ai agent deploy ...

  validate:
    needs: deploy
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: azure/login@v2
        with:
          client-id: ${{ vars.AZURE_CLIENT_ID }}
          tenant-id: ${{ vars.AZURE_TENANT_ID }}
          subscription-id: ${{ vars.AZURE_SUBSCRIPTION_ID }}
      - run: pip install agentops-toolkit
      - run: agentops eval run --config .agentops/run.yaml
```

**When to use:** Teams that deploy agents independently and want to verify quality post-deployment.

### Multi-Environment Promotion

Run evaluations across environments (dev → test → prod) using the same evaluation config but different Foundry project endpoints. Each environment uses GitHub Environment protection rules.

```yaml
jobs:
  eval-dev:
    environment: dev
    env:
      AZURE_AI_FOUNDRY_PROJECT_ENDPOINT: ${{ secrets.AZURE_AI_FOUNDRY_PROJECT_ENDPOINT }}
    steps:
      - run: agentops eval run

  eval-test:
    needs: eval-dev
    environment: test
    env:
      AZURE_AI_FOUNDRY_PROJECT_ENDPOINT: ${{ secrets.AZURE_AI_FOUNDRY_PROJECT_ENDPOINT }}
    steps:
      - run: agentops eval run

  eval-prod:
    needs: eval-test
    environment: production  # requires approval
    env:
      AZURE_AI_FOUNDRY_PROJECT_ENDPOINT: ${{ secrets.AZURE_AI_FOUNDRY_PROJECT_ENDPOINT }}
    steps:
      - run: agentops eval run
```

The key principle: **the evaluation policy is environment-invariant**. The same `run.yaml`, bundle, and thresholds evaluate the same agent across environments. Only `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` changes — set as a per-environment secret via GitHub Environments.

The `needs:` dependency ensures each stage only runs if the previous one passes (exit code 0). GitHub Environment protection rules can require manual approval for production.

**When to use:** Enterprise teams with dev/test/prod environments that need sequential validation before production.

### Multi-Config Matrix

Run several evaluation configs in parallel (already documented above in [Running multiple evaluations](#running-multiple-evaluations)).

**When to use:** Teams that run different bundles (model-direct, RAG, agent tools) in a single pipeline.

### Azure DevOps Pipelines

AgentOps works in Azure DevOps pipelines the same way — the CLI exit codes and artifacts are CI-system-agnostic. Here is a minimal Azure DevOps pipeline:

```yaml
trigger:
  branches:
    include:
      - main
      - develop

pool:
  vmImage: 'ubuntu-latest'

variables:
  - group: agentops-vars  # contains AZURE_AI_FOUNDRY_PROJECT_ENDPOINT

steps:
  - task: UsePythonVersion@0
    inputs:
      versionSpec: '3.11'

  - task: AzureCLI@2
    displayName: 'Run AgentOps Evaluation'
    inputs:
      azureSubscription: 'your-service-connection'
      scriptType: 'bash'
      scriptLocation: 'inlineScript'
      inlineScript: |
        pip install agentops-toolkit
        agentops eval run --config .agentops/run.yaml
        EXIT_CODE=$?
        if [ $EXIT_CODE -eq 0 ]; then
          echo "##[section]Evaluation Passed"
        elif [ $EXIT_CODE -eq 2 ]; then
          echo "##[error]Evaluation Failed — Threshold(s) Not Met"
        else
          echo "##[error]Evaluation Error (exit code $EXIT_CODE)"
        fi
        exit $EXIT_CODE

  - task: PublishBuildArtifacts@1
    displayName: 'Upload evaluation results'
    condition: always()
    inputs:
      PathtoPublish: '.agentops/results/latest'
      ArtifactName: 'agentops-eval-results'
```

Key differences from GitHub Actions:
- Use `AzureCLI@2` task for authentication (service connection).
- Use `PublishBuildArtifacts@1` for artifact upload.
- Use ADO variable groups for secrets.
- Exit codes are interpreted the same way — ADO fails the task on non-zero.

---

## Best Practices for Gating Deployments

### Design thresholds for your scenario

Set thresholds based on your evaluation scenario and risk tolerance:

```yaml
# Model-direct: text quality matters
thresholds:
  - evaluator: CoherenceEvaluator
    criteria: ">="
    value: 4            # High bar for coherence
  - evaluator: SimilarityEvaluator
    criteria: ">="
    value: 3            # Moderate similarity to expected answers

# Agent with tools: functional correctness matters
thresholds:
  - evaluator: TaskCompletionEvaluator
    criteria: ">="
    value: 3
  - evaluator: ToolCallAccuracyEvaluator
    criteria: ">="
    value: 3
  - evaluator: IntentResolutionEvaluator
    criteria: ">="
    value: 4

# Safety-critical: zero tolerance
thresholds:
  - evaluator: ViolenceEvaluator
    criteria: "<="
    value: 0            # Must be zero
  - evaluator: SelfHarmEvaluator
    criteria: "<="
    value: 0
```

### Use per-row thresholds for consistency

AgentOps evaluates thresholds per-row, not just on averages. A single failing row fails the evaluation — this catches outlier regressions that averages would hide.

### Start lenient, tighten over time

Begin with low thresholds to establish a baseline, then raise them as your agent improves:

1. First run: set thresholds low (`>= 1`) to establish passing baseline
2. Review `report.md` scores to understand typical ranges
3. Raise thresholds to just below the current average
4. Iterate as the agent improves

### Combine quality and safety evaluators

Run both in a single bundle so a single pipeline stage covers all dimensions:

```yaml
evaluators:
  # Quality
  - name: CoherenceEvaluator
    source: foundry
    enabled: true
  - name: RelevanceEvaluator
    source: foundry
    enabled: true
  # Safety
  - name: ViolenceEvaluator
    source: foundry
    enabled: true
  - name: HateUnfairnessEvaluator
    source: foundry
    enabled: true
```

### Use comparison for regression detection

After each evaluation, compare against a known-good baseline:

```bash
agentops eval run --config .agentops/run.yaml
agentops eval compare --runs latest,2026-03-15_120000
```

Exit code `2` from compare means regressions were detected.

### Choose the right evaluators for your scenario

AgentOps supports all Foundry built-in evaluators. Select the ones that match your scenario:

| Scenario | Recommended evaluators |
| --- | --- |
| Model-direct (text generation) | CoherenceEvaluator, FluencyEvaluator, SimilarityEvaluator, RelevanceEvaluator |
| RAG (retrieval-augmented) | GroundednessEvaluator, RelevanceEvaluator, ResponseCompletenessEvaluator |
| Agent with tools | TaskCompletionEvaluator, TaskAdherenceEvaluator, IntentResolutionEvaluator, ToolCallAccuracyEvaluator, ToolSelectionEvaluator |
| Safety-critical | ViolenceEvaluator, SexualEvaluator, SelfHarmEvaluator, HateUnfairnessEvaluator |
| Text similarity (NLP) | F1ScoreEvaluator, BleuScoreEvaluator, RougeScoreEvaluator, MeteorScoreEvaluator |

### Keep evaluation config in Git

All evaluation policy — bundles, datasets, thresholds — should be committed to the repository. This ensures:

- Evaluation changes are PR-reviewable YAML diffs
- Every evaluation is reproducible from a git commit
- No configuration drift between environments

---

## Supported Evaluators

AgentOps supports the following Foundry built-in evaluators in cloud evaluation mode. All evaluators use the `azure_ai_evaluator` testing criteria type with `builtin.<name>` designator.

### Quality Evaluators

| Evaluator | `builtin.` name | Inputs | Needs model |
| --- | --- | --- | --- |
| CoherenceEvaluator | `coherence` | query, response | Yes |
| FluencyEvaluator | `fluency` | query, response | Yes |
| RelevanceEvaluator | `relevance` | query, response | Yes |

### Agent Evaluators

| Evaluator | `builtin.` name | Inputs | Needs model |
| --- | --- | --- | --- |
| IntentResolutionEvaluator | `intent_resolution` | query, response | Yes |
| TaskCompletionEvaluator | `task_completion` | query, response | Yes |
| TaskAdherenceEvaluator | `task_adherence` | query, response (output_items) | Yes |

### Similarity / Ground Truth Evaluators

| Evaluator | `builtin.` name | Inputs | Needs model |
| --- | --- | --- | --- |
| SimilarityEvaluator | `similarity` | query, response, ground_truth | Yes |
| ResponseCompletenessEvaluator | `response_completeness` | query, response, ground_truth | Yes |

### RAG / Context Evaluators

| Evaluator | `builtin.` name | Inputs | Needs model |
| --- | --- | --- | --- |
| GroundednessEvaluator | `groundedness` | query, response, context | Yes |
| GroundednessProEvaluator | `groundedness_pro` | query, response, context | Yes |
| RetrievalEvaluator | `retrieval` | query, response, context | Yes |

RAG evaluators use the `context_field` from your dataset format config. If not set, they fall back to `expected_field`.

### Safety Evaluators

| Evaluator | `builtin.` name | Inputs | Needs model |
| --- | --- | --- | --- |
| ViolenceEvaluator | `violence` | query, response | Yes |
| SexualEvaluator | `sexual` | query, response | Yes |
| SelfHarmEvaluator | `self_harm` | query, response | Yes |
| HateUnfairnessEvaluator | `hate_unfairness` | query, response | Yes |

Safety evaluators require a Foundry project in a [region that supports content safety](https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/evaluation-evaluators/risk-safety-evaluators#foundry-project-configuration-and-region-support). If your region does not support them, the evaluators will return errors — run with `--verbose` to see details.

### Tool Evaluators

| Evaluator | `builtin.` name | Inputs | Needs model |
| --- | --- | --- | --- |
| ToolCallAccuracyEvaluator | `tool_call_accuracy` | query, response, tool_calls, tool_definitions | Yes |
| ToolSelectionEvaluator | `tool_selection` | query, response, tool_calls, tool_definitions | Yes |
| ToolInputAccuracyEvaluator | `tool_input_accuracy` | query, response, tool_definitions | Yes |
| ToolOutputUtilizationEvaluator | `tool_output_utilization` | query, response, tool_definitions | Yes |
| ToolCallSuccessEvaluator | `tool_call_success` | response, tool_definitions | Yes |

Tool evaluators require `tool_definitions` in your JSONL dataset rows. For evaluators that also need `tool_calls`, the agent's runtime tool call output is used automatically via `{{sample.tool_calls}}`.

### NLP Evaluators (Non-LLM)

| Evaluator | `builtin.` name | Inputs | Needs model |
| --- | --- | --- | --- |
| F1ScoreEvaluator | `f1_score` | response, ground_truth | No |
| BleuScoreEvaluator | `bleu_score` | response, ground_truth | No |
| GleuScoreEvaluator | `gleu_score` | response, ground_truth | No |
| RougeScoreEvaluator | `rouge_score` | response, ground_truth | No |
| MeteorScoreEvaluator | `meteor_score` | response, ground_truth | No |

NLP evaluators compare the generated response against `ground_truth` (the `expected_field` in your dataset) using text-matching algorithms. They do not require a model deployment.

---

## Troubleshooting

| Problem                                  | Solution                                                                                                                                                                                                                                  |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Error: evaluation failed: ...` (exit 1) | Check that `.agentops/run.yaml` exists, config is valid YAML, and secrets are set                                                                                                                                                         |
| `Threshold status: FAILED` (exit 2)      | Review `report.md` — thresholds are too strict or model quality regressed                                                                                                                                                                 |
| Missing artifacts                        | Ensure `.agentops/results/latest/` is not in `.gitignore` — the workflow reads this path                                                                                                                                                  |
| Authentication errors                    | Verify the federated credential entity matches your repo/branch; check that `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` are set as repository variables; confirm the app registration has access to the Foundry project |
| `agentops: command not found`            | Ensure `pip install agentops-toolkit` runs before the eval step                                                                                                                                                                           |
| Safety evaluators return no scores       | Your Foundry project must be in a [region that supports content safety](https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/evaluation-evaluators/risk-safety-evaluators#foundry-project-configuration-and-region-support). Run with `--verbose` to see the specific error from the service. |
| `Missing scores for enabled evaluators`  | One or more evaluators returned no score. Run with `--verbose` to see per-evaluator error messages. Common causes: region restrictions (safety), missing `tool_definitions` in dataset (tool evaluators), or unsupported evaluator name.   |

---

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
