# Contract: azd Evaluation Surfaces

This contract covers what AgentOps accepts from the user, what it invokes on the
Azure Developer CLI, and what it guarantees back. It does not restate the
`results.json` schema, which is unchanged; see [data-model.md](../data-model.md)
for the fields the current surface fills.

## Configuration

No new field is introduced. Both surfaces are driven by the existing keys.

### Auto-discovered current surface

```yaml
version: 1
agent: "travel-agent:1"
dataset: .agentops/data/smoke.jsonl
execution: azd
thresholds:
  task_adherence: ">=4"
  accuracy: ">=3"
```

With `evals/azure.eval.yaml` present, this resolves to the current surface. The
`dataset` field remains required by the AgentOps schema and continues to drive
readiness analysis; the evaluation itself uses the dataset the recipe declares,
which is existing `execution: azd` behavior.

### Explicit recipe

```yaml
version: 1
agent: "travel-agent:1"
dataset: .agentops/data/smoke.jsonl
execution: azd
eval_recipe: config/nightly/azure.eval.yaml
```

`eval_recipe` bypasses discovery for either surface. The surface is still
determined by the file's content.

### Unchanged legacy surface

```yaml
version: 1
agent: "reservation-agent:3"
dataset: .agentops/data/smoke.jsonl
execution: azd
```

With `eval.yaml` at the workspace root or under `src/<agent>/` and no
current-surface recipe, behavior is identical to previous releases.

## Recipe discovery

| Surface | Discovery locations |
|---|---|
| Current | `evals/azure.eval.yaml` |
| Legacy | `eval.yaml`, `eval.yml`, `src/*/eval.yaml`, `src/*/eval.yml` |

Precedence:

1. `eval_recipe`, when set, wins outright.
2. Candidates from both surfaces → the current surface is selected, and the run
   reports the selected and skipped paths.
3. More than one candidate within one surface → rejected as ambiguous, listing
   the candidates.
4. No candidate → configuration error naming both supported locations.

## Recipe schema classification

Classification is by content, not filename.

| Observed at the document root | Surface |
|---|---|
| `evals` is a sequence | Current |
| `agent` is a mapping, or `dataset_reference` is present, and no `evals` sequence | Legacy |
| Neither | Configuration error |

## Accepted current-surface recipe

```yaml
datasets:
  - name: support-agent-regression
    file: ./datasets/support-agent-regression.jsonl

evaluators:
  - name: support-agent-quality
    source: ./evaluators/support-agent-quality.json
  - name: brevity
    definition:
      type: rubric
      dimensions:
        - id: length
          weight: 1
          description: Answers the question without restating it.

evals:
  - name: support-agent-regression-eval
    dataset: support-agent-regression
    evaluation_level: turn
    max_samples: 50
    evaluators:
      - evaluator: builtin.task_adherence
        initialization_parameters:
          model: gpt-4o
      - evaluator: support-agent-quality
        version: "2"
        data_mapping:
          ground_truth: "{{item.expected}}"
    target:
      type: agent
      name: support-agent
```

A trace-sourced evaluation replaces `dataset` with `source`:

```yaml
evals:
  - name: support-agent-trace-eval
    source:
      type: traces
      agent_name: support-agent
      lookback_hours: 24
      max_traces: 20
    evaluation_level: turn
    evaluators:
      - evaluator: builtin.task_adherence
        initialization_parameters:
          model: gpt-4o
```

Notes that a parser must respect:

- The dataset key is `file`. `source` on a `datasets[]` entry is invalid.
- `dataset` and `source` on an `evals[]` entry are mutually exclusive.
- Unknown fields are preserved, not rejected.

## Threshold binding

Threshold keys in `agentops.yaml` bind to the metric names a recipe declares:

| Threshold key | Binds to |
|---|---|
| `task_adherence` | `builtin.task_adherence`, via the existing `builtin.` alias rule |
| `builtin.task_adherence` | itself, exactly |
| `support-agent-quality` | an evaluator reference name or label |
| `accuracy` | a rubric dimension id |

Binding uses the existing narrow alias rules. No fuzzy matching is added.

| Condition | Stage | Outcome | Exit code |
|---|---|---|---|
| Threshold matches no declared metric | Pre-flight, before any azd call | Configuration error | `1` |
| Threshold matches more than one declared metric | Pre-flight, before any azd call | Configuration error listing candidates | `1` |
| Declared metric produced no score in the run | Post-run | Threshold recorded as failed | `2` |
| All thresholds bound and satisfied | Post-run | Pass | `0` |
| Bound threshold not satisfied | Post-run | Gate failure | `2` |

Pre-flight binding applies to the current surface only. Legacy binding behavior
is unchanged.

## Commands AgentOps invokes

Every invocation is non-interactive and requests structured output. AgentOps
never passes a failure-gating flag; the release gate stays in AgentOps.

| Step | Command | Purpose |
|---|---|---|
| Probe | `azd version` | Confirm azd is present |
| Probe | `azd extension list --installed -o json` | Confirm the required extension is installed, matching on extension id |
| Reconcile | `azd ai eval create --path <recipe-dir> -o json` | Idempotently register the declared datasets, evaluators, and evaluation |
| Submit | `azd ai eval run start --eval <name> --path <recipe-dir> --no-wait -o json` | Start the run and capture identifiers |
| Poll | `azd ai eval run show <run-id> --path <recipe-dir> -o json` | Observe status until terminal or timeout |
| Retrieve run | `azd ai eval run show <run-id> --path <recipe-dir> -o json` | Final run object with counts and portal link |
| Retrieve samples | `azd ai eval run output list <run-id> --all --output-file <tmp> -o json` | Full per-sample output, unpaged |

Constraints this encodes:

- `--path` names the *directory* holding the recipe, not the file.
- `run start` takes no positional argument; the evaluation is named by `--eval`.
- `run output list` takes the run id positionally, which takes precedence over
  the `--run` flag.
- `--all` and `--output-file` together defeat the default page bound; the output
  file is read instead of stdout.
- The legacy surface continues to invoke `azd ai agent eval run` and
  `azd ai agent eval show` exactly as before. Note that the legacy surface spells
  its file flag `--out-file`, while the current surface spells it
  `--output-file`.

## Run status handling

| Status | Treatment |
|---|---|
| `completed` | Terminal, success |
| `failed`, `error`, `canceled`, `cancelled` | Terminal, runtime error |
| Anything else | Non-terminal; keep polling until the AgentOps timeout |

Comparison is case-insensitive. On timeout, AgentOps raises a runtime error that
includes the evaluation id, the run id, the last observed status, and the portal
link when known, so the operator can reattach in Foundry.

## Environment requirements

| Surface | azd | Extension |
|---|---|---|
| Current | `>= 1.27.1` | `azure.ai.evaluations` |
| Legacy | as previously documented | `azure.ai.agents` |

When azd or the required extension is missing, AgentOps exits `1` with a message
naming the missing component and the install command. AgentOps never installs
tooling and never falls back to another execution engine.

> The `azure.ai.evaluations` extension is preview and, as of this writing, is not
> yet present in the default azd extension registry. Until it ships, the current
> surface can only be exercised where the extension has been published into a
> local extension source. AgentOps treats its absence as a normal, clearly
> reported condition.

## Guarantees

- The evaluation executes exactly once per `agentops eval run`. Retrieving
  results never re-executes it.
- Normalized `results.json` and `report.md` are produced with the same structure
  for both surfaces.
- A run that produced zero samples, no decodable metrics, or a non-`completed`
  terminal status never reports a pass.
- Raw azd output is retained in the run's artifact directory for successful and
  failed runs alike.
- Existing legacy configurations continue to work with no edits.
