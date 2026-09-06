# Phase 1 Data Model: Current azd AI Evaluation Surface

**Feature**: `specs/011-azd-ai-eval-surface`
**Date**: 2026-09-06

Entities are grouped by layer. Pure recipe entities live in `core/`; adapter
entities live in `pipeline/`; the normalized output entity is the existing
`RunResult` contract, which is extended additively.

## Layer 1 — Recipe entities (`core/azd_eval.py`)

### EvalSurface

An enumeration identifying which azd command family and extension a recipe
requires.

| Value | Extension | Command family | Recipe schema |
|---|---|---|---|
| `legacy` | `azure.ai.agents` | `azd ai agent eval` | flat mapping with `agent` and `dataset_reference` |
| `current` | `azure.ai.evaluations` | `azd ai eval` | `datasets` / `evaluators` / `evals` arrays |

Every discovered recipe resolves to exactly one value. There is no `unknown`
value: a document that classifies as neither is rejected as a configuration
error, so downstream code never handles an indeterminate surface.

### RecipeClassification

Result of classifying a parsed YAML document.

| Field | Type | Notes |
|---|---|---|
| `surface` | `EvalSurface` | Resolved surface |
| `path` | `Path` | Absolute path to the recipe file |

**Classification rules**, applied in order against the parsed root mapping:

1. Root `evals` is a sequence → `current`.
2. Root `agent` is a mapping, or root has `dataset_reference` → `legacy`.
3. Otherwise → configuration error naming the path and stating that the document
   matches neither supported schema.

Rule 1 precedes rule 2 so that a current-surface document is never misread. The
two shapes cannot both match, because the current schema forbids top-level keys
outside its three arrays.

### RecipeResolution

Outcome of discovery, carrying both the choice and what was passed over so the
choice can be reported rather than made silently.

| Field | Type | Notes |
|---|---|---|
| `path` | `Path` | Selected recipe |
| `surface` | `EvalSurface` | Surface of the selected recipe |
| `skipped` | `tuple[Path, ...]` | Discoverable recipes not selected |
| `explicit` | `bool` | True when `eval_recipe` selected the path |

**Discovery locations**:

| Surface | Locations searched |
|---|---|
| `current` | `<workspace>/evals/azure.eval.yaml` |
| `legacy` | `<workspace>/eval.yaml`, `<workspace>/eval.yml`, `<workspace>/src/*/eval.yaml`, `<workspace>/src/*/eval.yml` |

**Resolution rules**:

- An explicit `eval_recipe` selects that path directly; classification still
  applies, and `skipped` is empty.
- Candidates from more than one surface → select the `current` candidate and
  record the others in `skipped`.
- More than one candidate within a single surface → ambiguity error listing the
  candidates and directing the user to set `eval_recipe`. This preserves existing
  legacy behavior.
- No candidates → configuration error naming both supported locations.

### CurrentEvalRecipe

Tolerant model of `evals/azure.eval.yaml`. Unknown fields are preserved for
forward compatibility with the preview schema, mirroring how the existing legacy
recipe model is built.

| Field | Type | Notes |
|---|---|---|
| `datasets` | `list[CurrentDatasetDecl]` | Optional |
| `evaluators` | `list[CurrentEvaluatorDecl]` | Optional; locally declared rubric evaluators |
| `evals` | `list[CurrentEval]` | Optional in schema; required in practice for a run |

### CurrentDatasetDecl

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | Required |
| `file` | `Optional[str]` | Local `.jsonl` path. **Key is `file`, not `source`** |
| `version` | `Optional[str]` | Coerced to string, matching existing version handling |

### CurrentEvaluatorDecl

A locally declared evaluator, typically a weighted rubric.

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | Required; referenced by `CurrentEvaluatorRef.evaluator` |
| `source` | `Optional[str]` | Path to a rubric definition file |
| `definition` | `Optional[dict]` | Inline rubric, or a nested file reference |
| `version` | `Optional[str]` | Coerced to string |

Rubric dimensions are read from `definition.dimensions` when the rubric is
inline, and from the referenced file when `source` is set. Each dimension
contributes a declared metric name from its `id`, falling back to its `name`,
reusing the existing dimension metric-name rule.

### CurrentEval

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | Required; the value passed to `--eval` |
| `id` | `Optional[str]` | Pre-existing evaluation created outside this recipe |
| `description` | `Optional[str]` | |
| `dataset` | `Optional[str]` | Name of a `datasets[]` entry; mutually exclusive with `source` |
| `source` | `Optional[CurrentSourceDecl]` | Trace or response source; mutually exclusive with `dataset` |
| `evaluation_level` | `Optional[str]` | `turn` or `conversation` |
| `max_samples` | `Optional[int]` | Recipe-owned; AgentOps does not override it |
| `evaluators` | `list[CurrentEvaluatorRef]` | |
| `target` | `Optional[CurrentTarget]` | Omitted when grading responses already present in rows |

### CurrentSourceDecl

| Field | Type | Notes |
|---|---|---|
| `type` | `str` | `traces` or `responses` |
| `agent_name`, `agent_version` | `Optional[str]` | Trace sources |
| `lookback_hours`, `max_traces`, `max_turns` | `Optional[int]` | |
| `response_ids` | `list[str]` | Response sources |
| `start_time`, `end_time` | `Optional[str]` | Absolute window |

Used for provenance in the normalized result's dataset description. A
trace-sourced evaluation has no local dataset file, so the recorded dataset path
describes the source instead.

### CurrentEvaluatorRef

| Field | Type | Notes |
|---|---|---|
| `evaluator` | `str` | Required. `builtin.<name>` or a name from `evaluators[]` |
| `name` | `Optional[str]` | Label used when one evaluator is referenced twice |
| `version` | `Optional[str]` | Coerced to string |
| `initialization_parameters` | `dict` | Carries the judge `model` |
| `data_mapping` | `dict` | Binds evaluator inputs to dataset columns |

### CurrentTarget

| Field | Type | Notes |
|---|---|---|
| `type` | `str` | `agent` or `model` |
| `name` | `str` | Agent or deployment name |

### Declared metric names

The set of metric names a current-surface recipe can produce, used for
pre-flight threshold binding:

- For each `CurrentEvaluatorRef`: its `name` when set, otherwise its `evaluator`.
- For each rubric dimension of each referenced local evaluator: the dimension's
  metric name.

These feed the existing threshold binding helper unchanged, so the established
narrow alias rules (including the `builtin.` prefix rule) apply identically to
both surfaces.

## Layer 2 — Adapter entities (`pipeline/azd_eval_runner.py`)

### CurrentEvalRun

Everything captured from one delegated evaluation.

| Field | Type | Notes |
|---|---|---|
| `recipe_path` | `Path` | Selected recipe |
| `eval_id` | `Optional[str]` | Evaluation identifier |
| `eval_name` | `Optional[str]` | Evaluation name from the recipe |
| `run_id` | `str` | Run identifier; required to retrieve output |
| `status` | `str` | Last observed run status |
| `run_payload` | `dict` | Raw run object |
| `output_items` | `list[dict]` | Raw per-sample items |
| `report_url` | `Optional[str]` | Portal link |
| `error_message` | `Optional[str]` | Non-empty run-level error message, if any |
| `stdout`, `stderr` | `str` | Concatenated command streams |
| `duration_seconds` | `float` | Wall time for the delegated sequence |

### RunCounts

Sample tallies read from the run object.

| Field | Type |
|---|---|
| `total`, `passed`, `failed`, `errored`, `skipped` | `int` |

Authoritative for sample totals and pass counts. Cross-checked against the number
of retrieved output items; a mismatch is recorded as a retrieval warning and the
smaller pass count is used, so an incomplete retrieval can never inflate the pass
rate.

### SampleOutcome

Per-sample status vocabulary: `passed`, `failed`, `errored`, `skipped`.

### SampleScore

One metric score for one sample.

| Field | Type | Notes |
|---|---|---|
| `metric` | `str` | `metric` when present, otherwise `name` |
| `score` | `Optional[float]` | Tolerant decode; `None` when absent or undecodable |
| `passed` | `Optional[bool]` | Three-valued: pass, judged failure, or unjudged |
| `label` | `Optional[str]` | |
| `reason` | `Optional[str]` | Carried into the normalized row metric |

**Invariants**:

- `passed = None` is never collapsed to `False`.
- `score = None` is never coerced to `0.0`; the sample is excluded from that
  metric's mean.
- A sample with an empty score list is an outcome of `failed`, not `passed`.

### Aggregation rule

For each distinct `metric` across all samples:

```text
aggregate_metrics[metric] = mean(score for score in samples if score is not None)
```

A metric with no decodable scores is omitted from `aggregate_metrics`. Any
threshold bound to an omitted metric therefore fails closed at evaluation time.

## Layer 3 — Normalized output (`core/results.py`)

`RunResult` is unchanged structurally. The current-surface adapter fills existing
fields that the legacy adapter leaves empty, and adds one nested mapping inside
the existing free-form `config` field.

| `RunResult` field | Current-surface content |
|---|---|
| `target` | Derived from the configured `agent`, as today |
| `dataset_path` | Recipe dataset file when present, otherwise a description of the declared source |
| `evaluators` | Declared evaluator reference names |
| `rows` | One `RowResult` per retrieved sample |
| `aggregate_metrics` | Computed means, per the aggregation rule |
| `thresholds` | Evaluated against `aggregate_metrics`, with declared-but-missing metrics recorded as failed |
| `summary` | Counts from `RunCounts`; `overall_passed` requires a `completed` status and a full threshold pass rate |
| `config.azd_evaluation` | Provenance block, extended for this surface |

### RowResult mapping

| `RowResult` field | Source |
|---|---|
| `row_index` | Position in the retrieved output |
| `input` | Best-effort extraction from the sample's data item |
| `expected` | Best-effort extraction from the sample's data item |
| `response` | Best-effort extraction from the sample's data item |
| `metrics` | One `RowMetric` per `SampleScore`, carrying `name`, `value`, `reason` |
| `error` | Set for `errored` samples, from the run-level error when no per-sample message exists |

The surface has no per-sample error field; an execution failure appears as an
`errored` status, an empty score list, or an unjudged verdict. Text extraction is
best-effort and never affects the gate: metric scores are read from the score
list, not from the data item, so an extraction miss degrades readability only.
The raw item is always retained in the raw artifact.

### `config.azd_evaluation` provenance block

Existing keys are preserved. Keys marked new are additive **and are emitted by
the current surface only** — the legacy adapter's provenance block is left
byte-for-byte as it was, because FR-013 requires the legacy artifact contract to
be unchanged. A downstream consumer therefore reads an absent `surface` as
`legacy`.

| Key | Notes |
|---|---|
| `recipe_path` | Existing |
| `run_id`, `eval_id`, `status`, `report_url` | Existing |
| `metric_binding`, `unused_metrics` | Existing |
| `surface` | **New, current surface only**: `current` |
| `extension` | **New, current surface only**: extension id used |
| `eval_name` | **New, current surface only** |
| `skipped_recipes` | **New, current surface only**: discoverable recipes not selected |
| `result_counts` | **New, current surface only**: total, passed, failed, errored, skipped |
| `missing_metrics` | **New, current surface only**: declared metrics the run did not emit |
| `retrieval_warnings` | **New, current surface only**: count mismatches or truncation notices |
| `error_message` | **New, current surface only**: run-level error message when non-empty |

`config.result_granularity` becomes `row` for the current surface; it remains
`aggregate` for the legacy surface.

## Raw artifacts

Written to the run's output directory for successful and failed runs alike,
alongside the existing legacy artifact names.

| File | Content |
|---|---|
| `azd_evaluation.json` | Raw run object |
| `azd_eval_output_items.json` | Raw per-sample items |
| `azd_stdout.log`, `azd_stderr.log` | Concatenated command streams |

Per-sample items contain prompts and model responses. They stay inside the
results directory, which the generated workspace `.gitignore` already excludes
from version control.
