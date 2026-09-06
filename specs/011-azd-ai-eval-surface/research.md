# Phase 0 Research: Current azd AI Evaluation Surface

**Feature**: `specs/011-azd-ai-eval-surface`
**Date**: 2026-09-06

All command names, flags, YAML keys, and JSON field names below are verified
against source in the `azure.ai.evaluations` extension implementation. Items
that could not be verified are listed under Open Risks and are designed around
defensively rather than assumed.

## Decision 1: Target the `azure.ai.evaluations` extension and the `ai.eval` namespace

**Decision**: The current surface is the azd extension `azure.ai.evaluations`,
which registers the command namespace `ai.eval`, producing `azd ai eval ...`.
It declares a minimum azd version of `1.27.1`. The legacy surface remains the
`azure.ai.agents` extension with the namespace `ai.agent`, producing
`azd ai agent eval ...`.

**Rationale**: The extension manifest states the id, namespace, and azd version
floor directly. AgentOps must name the correct extension in its availability
error message, otherwise the user installs the wrong thing.

**Alternatives considered**: Treating the two surfaces as one command family
with different subcommands was rejected. They are separate extensions with
separate install steps and separate version floors, so availability must be
checked per surface.

## Decision 2: The extension is not yet published, so absence is the normal state

**Decision**: Build the integration now, but treat "extension not installed" as
an ordinary, well-handled outcome rather than an exceptional one. Detection
failure must produce an actionable configuration error naming
`azure.ai.evaluations`, and must never degrade into another execution engine.

**Rationale**: The extension exists only as an unmerged pull request against
`Azure/azure-dev`. It is absent from both the public and dev extension
registries, and installing it from the default source fails today. It can only
be obtained by building and publishing it into a local extension source. This
means that for every user, on every machine, the unavailable path is the path
they will hit first.

**Consequences**: End-to-end validation against a live extension is not
possible in CI or on a clean workstation until the extension ships. All
automated coverage is therefore built on mocked subprocess boundaries, which the
project already requires for Azure interactions. The published documentation
must state the install path honestly, including that the extension is preview
and not yet in the default registry.

**Alternatives considered**: Deferring the feature until the extension ships was
rejected because the recipe schema and command surface are already stable enough
to code against, and the consuming lab work is blocked now. Vendoring or
auto-installing the extension was rejected: AgentOps does not install tooling.

## Decision 3: Detect availability with structured output, not substring scanning

**Decision**: Detect extension availability by parsing
`azd extension list --installed -o json`, which returns a bare JSON array whose
entries carry `id` and `installedVersion`. Match on `id` equality. Retain the
existing text scan only as a fallback when the JSON form is unavailable, so the
legacy detection path keeps its current observable behavior.

**Rationale**: The default table form of `azd extension list` lists every
registry extension, not just installed ones, and marks uninstalled entries with
a `Not installed` status. A substring scan for an extension id therefore reports
a false positive as soon as the extension appears in the registry but before the
user installs it. The table is also truncated to terminal width, so version
values are unreliable. azd itself directs users to `-o json` for full detail.

**Alternatives considered**: Parsing the table's `STATUS` column was rejected as
brittle across azd versions and terminal widths. Skipping detection and letting
the command fail was rejected because the resulting error would not tell the
user which extension to install.

## Decision 4: Distinguish recipe schemas structurally, not by filename

**Decision**: Classify a discovered recipe by inspecting its parsed content. A
document whose root `evals` key is a sequence is a current-surface recipe. A
document with a mapping-valued `agent` key, or a `dataset_reference` key, and no
`evals` sequence, is a legacy recipe. Filename and directory are discovery hints
only, never the classifier.

**Rationale**: The two schemas differ at the root in ways that cannot collide.
The current schema has exactly three optional top-level array keys — `datasets`,
`evaluators`, `evals` — and forbids additional properties. The legacy schema is
a flat mapping with `name`, `agent`, `dataset_reference`, `evaluators`, and
`options`, and has no `evals` key at all. Classifying by content also handles the
case where the current-surface configuration is embedded in `azure.yaml` under a
service with host `azure.ai.eval`, and the case where a recipe is referenced from
an arbitrary path.

**Alternatives considered**: Keying off the `evals/` directory or the
`azure.eval.yaml` basename alone was rejected because the directory is only a
default, the path is overridable by flag, and content classification is needed
anyway to parse the file.

## Decision 5: Canonical discovery location is `evals/azure.eval.yaml`

**Decision**: Add `evals/azure.eval.yaml` to auto-discovery, alongside the
existing legacy locations. The azd commands accept a `--path` flag that names
the *directory* holding the file, not the file itself, so AgentOps passes the
recipe's parent directory when invoking azd.

**Rationale**: The extension defines the default evaluation directory as `evals`
and the configuration basename as `azure.eval.yaml`. The basename is a constant
with no flag to override it; only the containing directory is configurable.

**Alternatives considered**: Recursively scanning the workspace for any
`*.eval.yaml` was rejected as both slow and ambiguous. Users with a non-default
location already have the explicit `eval_recipe` setting.

## Decision 6: Compute aggregate metrics from per-sample scores

**Decision**: AgentOps computes each aggregate metric as the mean of the
per-sample scores for that metric, read from the per-sample output. The run
object's counts are used for sample totals and pass/fail/error/skip tallies, not
for metric values.

**Rationale**: This is the single most consequential finding. The run object
exposes `result_counts` and `per_testing_criteria_results`, but both are
**counts**, not scores. There is no aggregate numeric metric anywhere on the run
object. The only numeric scores in the surface are per-sample. The legacy
adapter's assumption that the payload carries aggregate numeric metrics does not
hold for the current surface, so aggregation moves into AgentOps.

**Consequences**: Per-sample output retrieval is mandatory, not optional — the
gate cannot be evaluated without it. It also means AgentOps now populates
per-sample rows for this surface, where the legacy adapter emits an empty row
list and marks the result aggregate-only.

**Alternatives considered**: Deriving a pass rate from `result_counts` and gating
only on that was rejected: it would silently discard every threshold that names a
specific evaluator or rubric dimension, which is the primary use case.

## Decision 7: Submit asynchronously and poll, rather than relying on `--wait`

**Decision**: Invoke `run start` with `--no-wait` to obtain the evaluation and
run identifiers deterministically, then poll `run show` at a fixed interval until
the run reaches a terminal state or the AgentOps timeout expires.

**Rationale**: `run start` blocks by default, but it has an internal wait budget.
When that budget expires it prints reattachment instructions and **exits 0**,
emitting a small handoff object instead of the full run object. A caller that
trusts the exit code would treat an unfinished run as a success. Worse, the same
command emits two structurally different JSON shapes depending on which path it
took. Forcing `--no-wait` collapses this into one predictable shape, puts the
timeout under AgentOps control, and lets AgentOps emit its own progress
heartbeat during long runs, consistent with the existing legacy adapter.

It also directly satisfies the specification's requirement that a run which never
reaches a terminal state fails with the identifiers preserved, because those
identifiers are captured before polling begins.

**Terminal states**: `completed`, `failed`, `canceled`, `cancelled`, `error`.
Both spellings of cancelled occur, and `error` is distinct from `failed`. Only
`completed` is treated as success. Status comparison is case-insensitive.

**Alternatives considered**: Using the default blocking `--wait` and detecting
the handoff shape post hoc was rejected: it is a second code path guarding a
silent-success bug, and it forfeits control of the timeout.

## Decision 8: Never pass `--fail-on`; AgentOps owns the gate

**Decision**: AgentOps does not pass `--fail-on` to `run start`. Thresholds
configured in `agentops.yaml` remain the only gate, and are applied to the
normalized results.

**Rationale**: Without `--fail-on`, a completed run with failing samples exits 0,
which is exactly what AgentOps needs: azd reports execution outcome, AgentOps
decides release outcome. Passing `--fail-on` would fold a gate breach into the
same non-zero exit code as an operational failure, and azd collapses extension
exit codes so the two would be indistinguishable without parsing message text.
Keeping the gate in AgentOps preserves the project's exit-code contract.

**Alternatives considered**: Translating configured thresholds into `--fail-on`
expressions was rejected. `--fail-on` supports only a pass-rate or
any-failure form, which cannot express per-metric thresholds, and it would move
the gate outside the artifact that CI reviews.

## Decision 9: Retrieve results with two verified calls

**Decision**: After the run reaches a terminal state, call `run show` for the run
object and `run output list` with `--all` and `--output-file` for the per-sample
items. Read the per-sample items from the written file rather than from stdout.

**Rationale**: `run output list` emits a bare JSON array by design, which means
the paging fields carried by the underlying list envelope are stripped from JSON
output. A default call returns only a bounded first page with no indication that
more exists. Passing `--all` or `--output-file` switches the command to an
unbounded page size, so both are passed. The file is written atomically with
owner-only permissions because rows contain prompts and model responses.

The positional run identifier takes precedence over the `--run` flag, so the
positional form is used.

**Alternatives considered**: `run output export`, which writes the run and its
items as a single document, is appealing as a one-call retrieval, but its exact
document shape was not verified against source. Two individually verified calls
were preferred over one partially verified call. Export can replace both later
without changing the normalized contract.

## Decision 10: Bind thresholds before submitting, and fail closed after

**Decision**: Split threshold validation into two stages.

1. **Pre-flight, before any azd command runs**: bind configured threshold keys
   against the metric names the *recipe declares* — builtin evaluator names,
   evaluator labels, and rubric dimension ids. An unmatched or ambiguous
   threshold is a configuration error, exit code `1`, raised before an evaluation
   is created or a run is billed.
2. **Post-run**: a metric that the recipe declared but the completed run did not
   emit is recorded as a failed threshold, producing a gate failure with exit
   code `2`.

**Rationale**: These are genuinely different failures and deserve different exit
codes under the project's contract. A threshold naming a metric that no evaluator
in the recipe can ever produce is a misconfiguration, and catching it before
submission saves a cloud run. A threshold naming a declared metric that the run
failed to produce means execution succeeded but the evidence is incomplete, which
is a gate outcome, not a configuration error. Both fail closed; neither can
report a pass.

The existing narrow alias rules are reused unchanged. Broad fuzzy matching is not
introduced, because a wrong match here creates a false-green release gate.

**Scope**: Pre-flight binding applies to the current surface only. The legacy
adapter's post-run binding behavior is left exactly as it is, so existing
behavior and existing tests are unaffected.

**Alternatives considered**: Treating every binding failure as a runtime error,
matching the legacy adapter, was rejected because it makes a missing score
indistinguishable from a typo in the configuration. Treating every binding
failure as a gate failure was rejected because it would bill a cloud run to
discover a typo.

## Decision 11: Parse defensively at every value boundary

**Decision**: The per-sample parser treats the following as expected variation
rather than corruption:

- `score` may arrive as a JSON number or a string, and is decoded through a
  tolerant numeric conversion. A value that is neither is recorded as a missing
  score for that metric on that sample, not as a zero.
- `passed` is nullable and carries three-valued meaning. `true` is a pass,
  `false` is a judged failure, and `null` means the evaluator did not judge the
  sample. `null` must never be collapsed to `false`.
- A sample with an empty result list is a failure, not a pass.
- The run's `error` object is always present with null members on success, so
  failure is detected by testing for a non-empty `error.message`, never by
  testing whether `error` exists.
- `report_url` is preferred for the portal link, with `portal_url` as fallback.

**Rationale**: Each of these is a documented property of the surface, and each
has a plausible naive reading that produces a false pass or a false failure.
Collapsing a null verdict to `false` would fail runs that merely lacked a
judgement; treating a missing score as `0.0` would fail a threshold that was
never actually evaluated.

**Alternatives considered**: Strict schema validation that rejects any deviation
was rejected. The surface is preview and explicitly volatile; unknown fields must
be tolerated and preserved, exactly as the legacy recipe model already does.

## Decision 12: Reconcile the evaluation definition before each run

**Decision**: Invoke `azd ai eval create` against the recipe directory before
starting a run, treating it as idempotent reconciliation of the declared
datasets, evaluators, and evaluation.

**Rationale**: The create step registers what the recipe declares and resolves
the evaluation by name. A CI runner is a clean environment with no cached local
state from a prior interactive session, so an evaluation referenced only by name
would not resolve without it. Reconciling first makes the run reproducible from a
fresh clone.

This step registers definitions in Foundry. That is consistent with an
evaluation run, which already publishes results, and is unrelated to the
read-only guarantee that constrains Doctor and Cockpit.

**Alternatives considered**: Calling `create` only when `run start` fails to
resolve the evaluation was rejected as a fragile error-string dependency.

## Decision 13: `-o json` implies non-interactive

**Decision**: Pass `-o json` on every invocation and rely on it to suppress
prompts, while continuing to pass the explicit non-interactive flag for clarity
and for commands where output format is not requested.

**Rationale**: The extension makes JSON output imply non-interactive mode, so
JSON invocations never block waiting for input. This matters because the
evaluation runs from CI with no attached terminal.

## Open Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The extension is unshipped and its command surface is explicitly described as subject to change before merge. | Flags or field names could shift, breaking the adapter. | Isolate every azd command construction and every JSON field name in one adapter module with focused unit tests, so a surface change is a localized edit. Pin the documented supported version range. |
| The exact key names inside a sample's data item, which carry the input, expected value, and response, were not verified. | Per-sample rows could be populated with empty or wrong text. | Extract by documented key preference with a safe fallback, always retain the raw item in the raw artifact, and never let extraction failure fail the run or the gate. Metric scores, which the gate depends on, do not come from this structure. |
| Whether `score` is emitted as a number or a string was not verified. | A strict numeric parse would fail every sample. | Accept both, per Decision 11. |
| The service could report a run status outside the client's known terminal set. | The poller could wait until timeout on a finished run. | Treat unknown statuses as non-terminal and let the AgentOps timeout bound the wait, surfacing the last observed status in the error. |
| `run output export` shape unverified. | None today. | Not used; noted as a future simplification. |
| Per-sample output contains prompts and model responses. | Sensitive content could reach a shared location. | Raw artifacts stay in the run's results directory, which the workspace already excludes from version control, matching how existing raw azd artifacts are handled. |
| Learn documentation for this surface is not published; only the legacy surface is documented. | Users cannot self-serve setup. | AgentOps documentation states the extension id, the azd version floor, and the install path explicitly rather than linking to a page that does not exist. |

## Sources

- `azure.ai.evaluations` extension manifest, command implementations, and JSON
  models, from the open pull request `Azure/azure-dev#9500`.
- `schemas/azure.ai.eval.json` — the formal recipe schema, including the
  `additionalProperties: false` constraints relied on for schema discrimination.
- `schemas/examples/inline.azure.yaml` and `schemas/examples/ref.azure.yaml` —
  official example recipes.
- `Azure/azure-dev` `cli/azd/extensions/registry.json` and `registry.dev.json` —
  confirmation that the extension is absent from both registries.
- Microsoft Learn, "Evaluate with the Azure Developer CLI" — the legacy
  `azd ai agent eval` surface and `eval.yaml` schema, used here as the
  backward-compatibility reference.
