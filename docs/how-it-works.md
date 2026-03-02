# How it works

## Workspace structure

- **Workspace (`.agentops/`)**
  - Local folder that stores your evaluation configuration.
  - Typical structure:
    - `.agentops/config.yaml`
    - `.agentops/bundles/*.yaml`
    - `.agentops/datasets/*.yaml`
    - `.agentops/run.yaml`
    - `.agentops/results/` (generated artifacts)

## Bundle (`.agentops/bundles/*.yaml`)

- Defines *what quality means* for a scenario.
- Contains evaluators and threshold rules.
- Evaluators are explicit score producers:
  - `source: local` for AgentOps-native evaluators (for example `exact_match`, `avg_latency_seconds`)
  - `source: foundry` for Foundry SDK evaluators (name must match evaluator class name, for example `GroundednessEvaluator`)
- Supported local evaluators are explicit: `exact_match`, `pass_at_1`, `latency_seconds`, `avg_latency_seconds`.
- AgentOps does not emulate Foundry evaluators locally; if you configure `SimilarityEvaluator`/`GroundednessEvaluator`, use `source: foundry`.
- Foundry evaluators support generic configuration via `evaluators[].config`:
  - `kind`: `builtin` (default) or `custom`
  - `class_name`: built-in class name from `azure.ai.evaluation` (optional; defaults to evaluator `name`)
  - `callable_path`: required when `kind: custom`, format `<module>:<symbol>`
  - `init`: constructor kwargs (supports `${env:VAR}` placeholders)
  - `input_mapping`: maps evaluator args to runtime values (for example `$prompt`, `$prediction`, `$expected`, `$row.<field>`, `${env:VAR}`)
  - `score_keys`: ordered list of candidate keys used to extract numeric score from evaluator output
- Create a new bundle when you need a different quality policy (for example: stricter production gate vs. smoke gate).
- Minimal shape:

```yaml
version: 1
name: rag_strict
evaluators:
  - name: GroundednessEvaluator
    source: foundry
    enabled: true
  - name: avg_latency_seconds
    source: local
    enabled: true
thresholds:
  - evaluator: GroundednessEvaluator
    criteria: ">="
    value: 3
  - evaluator: avg_latency_seconds
    criteria: "<="
    value: 10.0
```

Example with explicit Foundry evaluator config:

```yaml
version: 1
name: qa_similarity
evaluators:
  - name: SimilarityEvaluator
    source: foundry
    enabled: true
    config:
      kind: builtin
      class_name: SimilarityEvaluator
      init:
        model_config:
          azure_endpoint: ${env:AZURE_OPENAI_ENDPOINT}
          azure_deployment: ${env:AZURE_OPENAI_DEPLOYMENT}
      input_mapping:
        query: $prompt
        response: $prediction
        ground_truth: $expected
      score_keys:
        - similarity
        - score
  - name: avg_latency_seconds
    source: local
    enabled: true
thresholds:
  - evaluator: SimilarityEvaluator
    criteria: ">="
    value: 3
  - evaluator: avg_latency_seconds
    criteria: "<="
    value: 10.0
```

For built-in Foundry evaluators, AgentOps uses `DefaultAzureCredential` by default (passwordless). Prefer managed identity in Azure environments and avoid API keys.

- Recommended baseline split:
  - `rag_baseline`: groundedness
  - `qa_similarity_baseline`: similarity
  - `classifier_baseline`: exact-match + latency

- Threshold criteria:
  - Numeric: `>=`, `>`, `<=`, `<`, `==` (requires `value`)
  - Boolean: `true`, `false` (do not set `value`)

## Dataset (`.agentops/datasets/*.yaml`)

- Describes the dataset source and format metadata used in evaluation.
- Create a new dataset config when you want to evaluate another file/source (for example: regression set, domain-specific set).
- Minimal shape:

```yaml
version: 1
name: regression_set
source:
  type: file
  path: ../../eval/datasets/regression.jsonl
format:
  type: jsonl
  input_field: input
  expected_field: expected
```

- `path` is resolved relative to the dataset config file location.
- For files under `.agentops/datasets/`, use `../../eval/datasets/<file>.jsonl`.

## Run config (`.agentops/run.yaml`)

- Connects one bundle + one dataset + backend execution details.
- This is the default run file loaded by `agentops eval run`.
- This is the file you change most often to point to your target (Foundry agent service, or subprocess app).
- Create additional run files when you need different execution modes (for example: local vs CI backend args).
- Minimal shape:

```yaml
version: 1
bundle:
  path: bundles/rag_strict.yaml
dataset:
  path: datasets/regression_set.yaml
backend:
  type: foundry
  target: agent
  agent_id: asst_abc123
  project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
  api_version: "2025-05-01"
  poll_interval_seconds: 2
  max_poll_attempts: 120
output:
  write_report: true
```

### Output options

| Option | Default | Description |
|---|---|---|
| `write_report` | `true` | Generate `report.md` alongside `results.json` |
| `publish_foundry_evaluation` | `false` | Publish results to Foundry v2 Evaluations panel (Classic Experience) |
| `fail_on_foundry_publish_error` | `false` | Return exit code `1` if Foundry publish fails |

## Backend behavior

- AgentOps Toolkit provides backend orchestration with native `foundry` support.
- In `foundry` mode, AgentOps uses **Foundry Cloud Evaluation** (project-native eval/run lifecycle).
- Cloud runs are persisted in the Foundry project and visible in **Build > Evaluations** (New Foundry Experience).
- AgentOps writes `backend_metrics.json` automatically.
- AgentOps then writes normalized `results.json` (stable contract for CI/reporting).
- `subprocess` is still supported if you want to use a custom evaluator pipeline.

## Foundry target mode

- `target: agent` (required for `backend.type: foundry`)
  - Required in backend config: `agent_id`
  - Required env: `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`
  - Authentication: automatic via `DefaultAzureCredential` (supports `az login`, managed identity, service principal)
  - Optional tuning: `poll_interval_seconds`, `max_poll_attempts`

## Main Foundry testing flow

- Authenticate (pick one):
  - Local dev: `az login`
  - CI/CD: set `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET`
  - Azure hosted: managed identity (no config needed)
- Set project endpoint:
  - `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>`
- Configure `.agentops/run.yaml`:

```yaml
version: 1
bundle:
  path: bundles/rag_strict.yaml
dataset:
  path: datasets/regression_set.yaml
backend:
  type: foundry
  target: agent
  agent_id: my-agent:1
  project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
  api_version: "2025-05-01"
  poll_interval_seconds: 2
  max_poll_attempts: 120
output:
  write_report: true
```

- Run `agentops eval run`.
- AgentOps creates one thread/run per dataset row, fetches the assistant response, computes metrics, and writes artifacts.

## Foundry backend inputs

- Dataset config must point to a JSONL file.
- Each row must include the fields configured in dataset format (`input_field`, `expected_field`).
- For `target: agent`, each row input is sent as a user message to the configured Foundry agent.
- The backend computes only the metrics configured in the bundle:
  - **Foundry evaluators** (`source: foundry`) are executed by the cloud evaluation API.
  - **Local evaluators** (`source: local`) such as `exact_match`, `latency_seconds`, `avg_latency_seconds`, and `pass_at_1` are computed by AgentOps only when explicitly enabled in the bundle.
  - `samples_evaluated` is always emitted.

## Backend metrics contract (`backend_metrics.json`)

- This is the file consumed by AgentOps to build `results.json`.
- In `foundry` mode AgentOps generates it automatically.
- In `subprocess` mode your custom backend must generate it with this shape:

```json
{
  "metrics": [
    { "name": "exact_match", "value": 0.84 },
    { "name": "avg_latency_seconds", "value": 1.21 }
  ],
  "row_metrics": [
    {
      "row_index": 1,
      "metrics": [
        { "name": "pass_at_1", "value": 1.0 },
        { "name": "pass_at_3", "value": 1.0 }
      ]
    },
    {
      "row_index": 2,
      "metrics": [
        { "name": "pass_at_1", "value": 0.0 },
        { "name": "pass_at_3", "value": 1.0 }
      ]
    }
  ]
}
```

- Required rules:
  - root JSON object
  - `metrics` must be a list
  - each metric entry must include `name` (string) and `value` (number)
- `row_metrics` is optional, but recommended for dataset-native consolidation.
- when present, each row entry must include:
  - `row_index` (1-based)
  - `metrics` list with `{name, value}` entries
- Each metric `name` must match the evaluator `name` referenced in bundle thresholds.
- AgentOps applies thresholds per item and then consolidates item verdicts into run-level outputs.
- AgentOps validates that every enabled evaluator in the bundle has produced scores in `row_metrics`.

## How evaluators and metrics work

- Evaluator execution is row-first:
  - each dataset row is evaluated and can produce one or more row scores.
- Threshold evaluation is bundle-driven:
  - each threshold references one evaluator score (`thresholds[].evaluator`)
  - each row receives a threshold verdict per evaluator
  - if a row passes all threshold rules, the row verdict is PASS
  - run-level threshold status is consolidated from item verdicts.
- Metrics have three levels in `results.json`:
  - `metrics`: backend/global metrics (already aggregated by backend)
  - `row_metrics`: per-row evaluator outputs (`row_index` + metric list)
  - `item_evaluations`: per-row threshold verdicts (per evaluator + final row PASS/FAIL)
  - `run_metrics`: consolidated execution metrics derived by AgentOps

In short:
- evaluator computes score per item
- threshold validates expected quality policy per item and per run
- AgentOps consolidates visibility for CI and reporting

## Consolidated run metrics

- AgentOps derives consolidated run metrics for each execution in `results.json` under `run_metrics`.
- Derived by default:
  - `run_pass` (`1.0` pass, `0.0` fail)
  - `threshold_pass_rate` (`thresholds_passed / thresholds_count`)
  - `items_total`
  - `items_passed_all`
  - `items_failed_any`
  - `items_pass_rate`
  - per-metric aggregates from row data, for example:
    - `groundedness_avg`
    - `groundedness_stddev`
    - `latency_seconds_avg`
    - `latency_seconds_stddev`
  - `accuracy` (from row-level `exact_match` average when available)

### About pass@1 / pass@k

- `pass@1` and `pass@k` can represent different concepts depending on your evaluation design.
- For multi-attempt or multi-run scenarios (for example "passed at least once across N runs"), compute them in a separate analysis layer.
- AgentOps keeps single-run consolidation simple and focused on row verdicts + run aggregates.

## Outputs and history

- Every run stores artifacts in `.agentops/results/<timestamp>/`.
- AgentOps also refreshes `.agentops/results/latest/` with a copy of the most recent run.
- `results.json`: normalized, machine-readable result for CI/automation.
- `report.md`: human-readable summary for review.

When you run:

```bash
agentops eval run
```

AgentOps writes to both:

- `.agentops/results/YYYY-MM-DD_HHMMSS/` (immutable history of that run)
- `.agentops/results/latest/` (convenient pointer to last run content)

If you pass `--output`, AgentOps writes to that directory and still updates `.agentops/results/latest/` with the newest run content.
