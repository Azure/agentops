# Quickstart: Validating Current azd Evaluation Surface Support

**Feature**: `specs/011-azd-ai-eval-surface`
**Date**: 2026-09-06

Validation is split into two tiers. **Tier 1 runs anywhere** — no azd, no Azure
credentials, no network — and covers every guarantee in
[contracts/azd-eval-surface.md](contracts/azd-eval-surface.md) through the mocked
subprocess boundary. **Tier 2 requires a live environment** and is only possible
where the `azure.ai.evaluations` extension has been published into an azd
extension source.

Tier 1 is the acceptance bar for this feature. Tier 2 is confirmation, and until
the extension ships it cannot run on a clean machine.

## Prerequisites

```bash
python -m pip install -e .
python -m pip install pytest
```

Tier 2 additionally needs azd `>= 1.27.1`, the `azure.ai.evaluations` extension,
an authenticated Azure session, and a Foundry project with a deployed agent.

## Tier 1 — Offline validation

### T1.1 Full suite, no regressions

```bash
python -m pytest tests/ -x -q
```

**Expected**: all tests pass. Every pre-existing azd test must pass **without
modification** — that is the mechanical proof of the backward-compatibility
requirement. A test that had to be edited to accommodate this feature is a
finding, not a fix.

### T1.2 Recipe classification and discovery precedence

```bash
python -m pytest tests/unit/test_azd_eval.py -q
```

**Expected coverage**:

| Workspace state | Expected resolution |
|---|---|
| `evals/azure.eval.yaml` only | current surface, no skipped entries |
| `eval.yaml` only | legacy surface, unchanged from today |
| `src/<agent>/eval.yaml` only | legacy surface, unchanged from today |
| both surfaces present | current selected, legacy recorded as skipped |
| two legacy candidates | ambiguity error listing both |
| `eval_recipe` set | that path used, classified by content |
| document matching neither schema | configuration error naming the path |

Also assert that a current-surface recipe using `datasets[].file` parses, that an
unknown top-level key is preserved rather than rejected, and that declared metric
names include builtin evaluator references, evaluator labels, and rubric
dimension ids.

### T1.3 Adapter behavior against a mocked azd

```bash
python -m pytest tests/unit/test_azd_eval_runner.py -q
```

**Expected coverage**, each with the subprocess boundary faked:

- **Command sequence**: probe, create, `run start --no-wait`, poll, final
  `run show`, `run output list --all --output-file`. Assert the evaluation is
  started exactly once and that no failure-gating flag is passed.
- **Flag correctness**: `--path` receives the recipe's *directory*; `run start`
  receives no positional argument; `run output list` receives the run id
  positionally; the current surface uses `--output-file`, never `--out-file`.
- **Polling**: a sequence of non-terminal statuses followed by `completed`
  resolves; an unknown status keeps polling; timeout raises a runtime error that
  contains the evaluation id, run id, and last observed status.
- **Aggregation**: metric means exclude samples with no score rather than
  treating them as zero; a metric with no scores anywhere is absent from the
  aggregate map.
- **Tolerant decoding**: a score arriving as the string `"4"` decodes to `4.0`; a
  score that is neither number nor numeric string is recorded as absent.
- **Three-valued verdicts**: a sample with `passed: null` is not counted as a
  judged failure and is not counted as a pass; a sample with an empty result list
  is a failure.
- **Run-level error**: an error object whose members are null is not treated as a
  failure; a non-empty error message is.
- **Portal link**: `report_url` wins over `portal_url`.
- **Raw artifacts**: the run payload, the per-sample items, and both command
  streams are written for a successful run *and* for a failed run.

### T1.4 Availability detection

Still in `tests/unit/test_azd_eval_runner.py`:

- A structured installed-extension listing that includes the extension id →
  available.
- A listing that omits it → unavailable, with an error naming
  `azure.ai.evaluations` and the install command.
- A **human-readable** listing containing the extension id with a not-installed
  status → **unavailable**. This is the false-positive guard; a substring scan
  would wrongly report available.
- The legacy detection path continues to resolve through its existing fallback,
  proven by the unmodified legacy tests.

### T1.5 Exit codes end to end

```bash
python -m pytest tests/integration/test_cli_flat_schema.py -q
```

**Expected**, with azd faked at the subprocess boundary:

| Scenario | Exit code |
|---|---|
| Completed run, all thresholds satisfied | `0` |
| Completed run, a bound threshold not satisfied | `2` |
| Completed run, a declared metric produced no score | `2` |
| Threshold naming a metric no evaluator declares | `1`, **before any azd command runs** |
| Threshold ambiguous across declared metrics | `1`, before any azd command runs |
| Terminal status of `failed`, `error`, or either cancelled spelling | `1` |
| Poll timeout | `1`, with identifiers in the message |
| Run completed with zero samples | not `0` |
| azd or extension missing | `1`, naming the missing component |

For the two pre-flight cases, assert that the fake azd was **never invoked**.
That is the observable proof that a configuration typo does not bill a cloud run.

### T1.6 Readiness analysis

```bash
python -m pytest tests/unit/test_eval_analysis.py -q
agentops eval analyze --format json
```

**Expected**: for an `execution: azd` workspace, the report names the resolved
recipe path and its surface. With no discoverable recipe, it reports the gap and
the action required. With both surfaces present, it reports the selection and
what was skipped.

### T1.7 Nobody is opted in by accident

The current surface must be unreachable unless a current-surface recipe exists.

```bash
# Workspace with a legacy eval.yaml and no evals/ directory
agentops eval run
agentops eval analyze
```

**Expected**: identical output to the previous release. No mention of the current
surface, no new warning, no new error. This is the regression guard for existing
users and fresh clones.

```bash
# Fresh workspace, current-surface extension not installed
agentops eval init
```

**Expected**: a **legacy** recipe is generated, and the workspace is immediately
runnable. Initialization must not generate `evals/azure.eval.yaml` while the
current-surface extension is unavailable, because that would produce a workspace
whose next command fails on a dependency the user cannot install. Assert that
initialization probes for the extension and that the probe result selects the
target surface.

### T1.8 Unavailable-extension experience

In a workspace where someone deliberately added `evals/azure.eval.yaml`, on a
machine without the extension:

```bash
agentops eval run
echo "exit=$?"
```

**Expected**: exit `1`. The message names `azure.ai.evaluations`, states the azd
version floor, and gives the install command. It must not suggest that AgentOps
fell back to another engine, and it must not mention the legacy extension.

Because the extension is not yet in the default registry, this is what every
opt-in user sees today, so its wording carries more weight than usual.

## Tier 2 — Live validation

Only runnable where the extension is installed.

### T2.1 Happy path

Given a Foundry project, a deployed agent, `evals/azure.eval.yaml` declaring a
dataset and at least one builtin evaluator, and matching thresholds:

```bash
agentops eval run
echo "exit=$?"
```

**Expected**:

- Progress output names the resolved recipe and the current surface.
- A heartbeat appears while the run is in flight; the command never looks hung.
- Exit `0` when thresholds pass.
- `.agentops/results/latest/results.json` contains computed `aggregate_metrics`,
  one row per sample, and a `config.azd_evaluation` block whose `surface` is
  `current`.
- `.agentops/results/latest/report.md` renders thresholds, rubric dimensions, and
  any failed samples.
- The raw run payload and per-sample items are present in the same directory.
- **The Foundry portal shows exactly one run** for this invocation. This is the
  observable check that results retrieval did not re-execute the evaluation.

### T2.2 Gate failure

Tighten one threshold above the observed score and rerun.

**Expected**: exit `2`. The report names the failing threshold. The run itself is
still recorded as completed — a gate failure is not an execution failure.

### T2.3 Rubric dimensions

With a recipe declaring a local rubric evaluator and thresholds naming its
dimension ids:

**Expected**: each dimension appears in `aggregate_metrics` under its id and is
gated exactly like a builtin metric.

### T2.4 Legacy regression

In a workspace with only a legacy `eval.yaml`:

```bash
agentops eval run
```

**Expected**: identical behavior to the previous release — same commands, same
normalization, same `result_granularity` of `aggregate`, same exit code.

### T2.5 Baseline comparison

```bash
agentops eval run --baseline .agentops/results/<previous>/results.json
```

**Expected**: the comparison renders against the current-surface run exactly as
it does for any other execution mode.

## Documentation check

Confirm before closing the feature:

- `docs/evaluation.md` documents both surfaces, their extension ids, their azd
  version floors, and the discovery precedence rule.
- The preview status of `azure.ai.evaluations`, including that it is not yet in
  the default extension registry, is stated plainly rather than implied.
- `CHANGELOG.md` records the added support under the unreleased section.
