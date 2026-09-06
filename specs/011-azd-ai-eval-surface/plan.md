# Implementation Plan: Current azd AI Evaluation Surface Support

**Branch**: `placerda-azd-ai-eval-surface` | **Date**: 2026-09-06 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/011-azd-ai-eval-surface/spec.md`

## Summary

Teach `execution: azd` to speak two azd evaluation surfaces instead of one. A
pure core module gains a second recipe schema and a content-based classifier, so
a discovered recipe declares which surface it belongs to. A new pipeline adapter
drives the current surface — reconcile the evaluation, submit the run
asynchronously, poll to a terminal state, then read the run object and its
per-sample output — and normalizes that output into the existing `RunResult`
contract. The existing legacy adapter is left untouched and continues to serve
legacy recipes.

The current surface exposes no aggregate numeric metrics, only counts, so the new
adapter computes each metric as the mean of its per-sample scores and populates
per-sample rows. That is a richer result than the legacy adapter's
aggregate-only output, and it arrives through the same schema, so reporting,
threshold gating, baseline comparison, evidence, and exit codes need no
surface-specific branching.

Discovery adds `evals/azure.eval.yaml`. When both surfaces are discoverable and
no explicit `eval_recipe` is set, the current surface wins and the choice is
reported. No new command, flag, or configuration field is introduced.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: Pydantic v2 and `ruamel.yaml` for the second recipe
schema; the standard library `subprocess` for the azd boundary. No new
third-party dependency. No Azure SDK is involved — azd is the only integration
point and it is an external process.

**External tooling**: Azure Developer CLI `>= 1.27.1` plus the
`azure.ai.evaluations` extension for the current surface; the existing
`azure.ai.agents` extension for the legacy surface. Neither is installed by
AgentOps.

**Storage**: Recipe YAML read from the workspace; run artifacts written under the
existing timestamped results directory; a temporary file for the per-sample
output that azd writes directly.

**Testing**: pytest with the azd subprocess boundary mocked, matching the
existing azd adapter tests. No azd installation, no Azure credentials, and no
network access are required to run the suite.

**Target Platform**: Windows, Linux, and macOS workstations plus non-interactive
CI runners.

**Project Type**: Python CLI and library.

**Performance Goals**: Adapter overhead outside the cloud run itself stays
negligible relative to evaluation latency. Progress output during polling
appears at the existing heartbeat interval so a long run never looks hung.

**Constraints**: No new command, flag, or required configuration field; the flat
`agentops.yaml` schema evolves additively only; the `RunResult` schema evolves
additively only; exit codes keep their existing meanings; legacy recipe behavior
is unchanged; the evaluation executes exactly once per invocation; the gate stays
in AgentOps and is never delegated to azd; unbound thresholds and missing metrics
fail closed.

**Scale/Scope**: One recipe, one evaluation, and one run per `agentops eval run`
invocation. Per-sample output is retrieved in full rather than paged by the
caller.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate | Pre-design assessment | Post-design assessment |
|---|---|---|
| Preserve public contracts | PASS: `execution: azd` and `eval_recipe` keep their meaning and gain a second supported recipe schema. No new command or flag. Exit codes unchanged. | PASS: `RunResult` gains only additive content under the existing `config` mapping and populates the already-present `rows` list. Legacy recipes resolve, execute, and normalize exactly as before. Cross-surface discovery precedence is deterministic and reported, never silent. |
| Enforce architectural boundaries | PASS: recipe schema, classification, and threshold binding are pure and belong in `core/`; subprocess execution and normalization belong in `pipeline/`; readiness reporting belongs in `services/`; the CLI is unchanged. | PASS: the new adapter module holds every azd command construction and every JSON field name, so the volatile external surface is contained in one place. `core/` performs no I/O. `pathlib.Path` throughout. |
| Isolate Azure runtime integration | PASS: no Azure SDK is used. The integration is an external process, and it is invoked lazily from within the execution path. | PASS: tests mock the subprocess boundary, so the suite runs with no azd, no credentials, and no network. Missing tooling produces an explicit, actionable error naming the extension, never a silent fallback to another engine. |
| Keep release evidence trustworthy | PASS: Foundry still owns evaluation execution; AgentOps owns the gate and the normalized evidence. Doctor and Cockpit are untouched and stay read-only. | PASS: the gate is never delegated to azd; a null verdict is never read as a pass; missing metrics and zero-sample runs cannot report a pass; raw azd output is retained for audit inside the results directory the workspace already excludes from version control. |
| Verify every behavior change | PASS: schema classification, discovery precedence, command construction, polling and terminal-state handling, normalization, threshold binding, and readiness output each need focused coverage. | PASS: unit coverage is identified per module and integration coverage asserts exit codes for pass, gate failure, and error. Every existing azd test must pass unmodified. |
| Product and workflow constraints | PASS: schema evolution is additive; existing helpers and patterns are reused rather than replaced. | PASS: user-visible documentation, initialization guidance, and a changelog entry are included. No constitutional exception is required. |

**Notable risk carried forward, not a gate failure**: the `azure.ai.evaluations`
extension is not yet published (see [research.md](research.md), Decision 2). The
design treats absence as an ordinary handled outcome, so nothing regresses while
the extension is unavailable, and the surface details are pinned to a verified
schema rather than guessed.

## Project Structure

### Documentation (this feature)

```text
specs/011-azd-ai-eval-surface/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── azd-eval-surface.md
├── checklists/
│   └── requirements.md
└── tasks.md              # created by /speckit-tasks, not by this command
```

### Source Code (repository root)

```text
CHANGELOG.md
docs/
├── evaluation.md                       # both surfaces, versions, extension ids
├── how-it-works.md                     # azd execution path description
└── tutorial-hosted-agent.md            # current-surface walkthrough

src/agentops/
├── core/
│   └── azd_eval.py                     # + current recipe model, classifier,
│                                       #   evals/ discovery, declared-metric names
├── pipeline/
│   ├── azd_runner.py                   # legacy adapter: unchanged behavior;
│   │                                   #   shared availability + command helpers
│   ├── azd_eval_runner.py              # NEW current-surface adapter
│   └── orchestrator.py                 # dispatch by resolved surface
├── services/
│   ├── eval_analysis.py                # resolved recipe + surface readiness
│   └── azd_eval_init.py                # initialization guidance per surface
└── templates/
    └── agentops.yaml                   # comment guidance for both surfaces

tests/
├── unit/
│   ├── test_azd_eval.py                # classifier, discovery precedence, parsing
│   ├── test_azd_eval_runner.py         # NEW adapter, polling, normalization
│   ├── test_azd_runner.py              # legacy regression, unchanged
│   ├── test_eval_analysis.py           # recipe/surface readiness reporting
│   └── test_azd_eval_init.py           # initialization guidance
└── integration/
    └── test_cli_flat_schema.py         # end-to-end exit codes per outcome
```

**Structure Decision**: Add a second adapter module rather than branching inside
the existing one. The legacy adapter carries behavior that must not change, while
the current surface needs a different command sequence, a different result
retrieval model, and its own aggregation step. Keeping them separate makes the
backward-compatibility requirement verifiable by inspection and confines the
volatile preview surface to one file. Shared, surface-agnostic concerns —
extension availability probing, subprocess execution with progress heartbeat,
failure formatting, and raw artifact writing — stay in the existing module and
are reused by both.

## Design Decisions

1. **Recipe classification is content-based.** A parsed document whose root
   `evals` key is a sequence is a current-surface recipe; a document with a
   mapping-valued `agent` key or a `dataset_reference` key and no `evals`
   sequence is a legacy recipe. Filenames and directories are discovery hints
   only. A file matching neither shape is a configuration error naming the path
   and the reason, never a best-effort run.

2. **Discovery precedence is fixed and reported.** Auto-discovery collects
   candidates from the current-surface location and the legacy locations. Across
   surfaces, the current surface wins and the run reports which recipe was chosen
   and which was skipped. Within a surface, more than one candidate is rejected
   as ambiguous with the candidates listed, preserving today's behavior. An
   explicit `eval_recipe` bypasses all of this.

3. **Threshold binding is split into pre-flight and post-run stages.** Before any
   azd command runs, configured thresholds are bound against the metric names the
   recipe declares; unmatched or ambiguous keys are a configuration error with
   exit code `1`, raised before a run is billed. After the run, a declared metric
   the run did not emit is recorded as a failed threshold, producing a gate
   failure with exit code `2`. Both fail closed. The existing narrow alias rules
   are reused; no broader matching is introduced. This staging applies to the
   current surface only, leaving legacy binding behavior untouched.

4. **Submission is asynchronous and AgentOps owns the wait.** The run is started
   in no-wait mode to capture the evaluation and run identifiers deterministically
   in a single predictable payload shape, then polled to a terminal state under
   the AgentOps timeout, emitting the existing progress heartbeat. A timeout
   raises a runtime error that carries both identifiers and the last observed
   status, so the operator can reattach in Foundry. This avoids the surface's
   blocking mode, which can exit successfully with an unfinished run when its
   internal wait budget expires.

5. **The gate is never delegated.** AgentOps does not pass a failure-gating flag
   to azd. A completed run with failing samples exits zero from azd, and AgentOps
   decides the release outcome from the normalized results. This keeps a gate
   breach distinguishable from an operational failure, which azd's collapsed exit
   codes cannot express.

6. **Aggregate metrics are computed, not read.** The run object carries only
   counts. Each aggregate metric is the mean of the per-sample scores recorded
   under that metric name. A sample that produced no score for a metric is
   excluded from that metric's mean rather than counted as zero. A metric with no
   scores at all is absent from the aggregate map, which makes any threshold
   bound to it fail closed by construction.

7. **Per-sample results are first-class.** The current surface populates the
   existing `rows` list with one entry per sample, carrying its metric scores,
   reasons, and outcome, and marks the result granularity accordingly. Failed and
   errored samples appear as rows with their outcome recorded; they are never
   dropped from counts nor averaged away.

8. **Verdicts use three-valued logic.** A sample verdict is a pass, a judged
   failure, or absent. Absent means the evaluator did not judge the sample and is
   never collapsed into a failure. Sample totals and pass counts come from the
   run object's counts, cross-checked against the retrieved rows, so a truncated
   retrieval cannot inflate the pass rate.

9. **Availability detection prefers structured output.** Extension presence is
   determined from the structured installed-extension listing, matching on
   extension id, because the human-readable listing also includes uninstalled
   registry entries and is truncated to terminal width. The existing text scan
   remains as a fallback so legacy detection behavior is preserved.

10. **Value parsing is deliberately tolerant.** Scores accept both numeric and
    string encodings; unknown recipe and payload fields are preserved rather than
    rejected; the run's always-present error object is tested by message content
    rather than existence. The preview surface is expected to drift, and rejecting
    unknown fields would turn additive upstream changes into outages.

11. **Evaluation definitions are reconciled before each run.** The create step
    runs first and idempotently registers what the recipe declares, so a run
    started from a clean CI checkout resolves the evaluation by name without
    depending on local state from a prior interactive session.

12. **Raw output retention is unchanged in shape and location.** The current
    surface writes its native run payload, per-sample output, and command streams
    into the same run artifact directory the legacy adapter already uses, for both
    successful and failed runs. Per-sample content includes prompts and model
    responses, and the workspace already excludes that directory from version
    control.

13. **The new surface is strictly opt-in, and initialization never opts a user
    in to something they cannot install.** The current-surface code path is
    reachable only when a current-surface recipe exists — through discovery at
    `evals/azure.eval.yaml` or through an explicit `eval_recipe`. A workspace
    without such a file behaves exactly as it does today, so no existing user and
    no fresh clone can encounter a new failure.

    This constrains initialization directly: `agentops eval init` must keep
    generating a legacy recipe while the current-surface extension is
    unavailable, because generating a current-surface recipe would produce a
    workspace whose very next command fails on a dependency the user cannot
    install. Initialization probes for the current-surface extension and targets
    it only when it is present; otherwise it targets the legacy surface, which is
    published and installable. Documentation describes both surfaces regardless,
    so a reader can choose deliberately.

## Complexity Tracking

No constitution violations and no justified complexity exceptions are required.

The one design choice worth naming is the second adapter module. It is not an
unnecessary abstraction: the alternative — branching inside the existing adapter
— would interleave a frozen legacy code path with a volatile preview one in the
same functions, making the "legacy behavior is unchanged" requirement
unverifiable by inspection and making every future upstream surface change a
regression risk for existing adopters.
