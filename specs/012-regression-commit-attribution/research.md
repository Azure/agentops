# Phase 0 Research: Regression Commit Attribution

All items below were resolved by reading the existing implementation; no
`NEEDS CLARIFICATION` markers remain from the spec.

## 1. How to capture commit metadata across execution modes

**Decision**: Generalize the existing `_git_sha()` pattern from
`src/agentops/pipeline/prompt_deploy.py:649` (`GITHUB_SHA` →
`BUILD_SOURCEVERSION` → `Build.SourceVersion`) into a shared resolver in a new
`src/agentops/pipeline/commit_info.py`. The resolver: (1) tries the CI
environment variables in that order, falling back to `git rev-parse HEAD` when
none are set (covers local execution); (2) once a SHA is known, shells out to
`git show -s --format=...` for the subject line, author, and commit timestamp.
Steps 1 and 2 are identical for CI and local runs — only the SHA source
differs.

**Rationale**: The env-var lookup is already proven for CI (used today by
Foundry prompt-agent deploy gating); it just has never been wired into
`agentops eval run` itself. Reusing it keeps CI and local capture as one code
path with one fallback branch, rather than two parallel implementations.

**Alternatives considered**: Querying the CI provider's REST API (e.g. GitHub)
for commit details — rejected; adds a network dependency and auth surface for
data `git show` already provides locally, including in a shallow checkout
(the HEAD commit's own metadata is always present even at `fetch-depth: 1`).

## 2. Where to attach commit capture in the run lifecycle

**Decision**: Attach commit metadata once, inside
`orchestrator._persist()` (`src/agentops/pipeline/orchestrator.py:1143`),
which every execution path (local, cloud, azd) already calls before writing
`results.json` and rendering `report.md`.

**Rationale**: `RunResult` is currently constructed at three separate call
sites (local ~line 241, cloud ~line 500s, azd ~line 695/769), each already
funneling into the same `_persist()`. Attaching commit metadata there is a
single change that covers every execution mode — including the hosted-agent
cloud/azd path the product owner asked to prioritize — instead of three
call-site-specific changes that could drift out of sync.

**Alternatives considered**: Setting `result.commit` at each of the three
`RunResult(...)` construction sites — rejected as duplicative.

## 3. Which prior run to diff against for the causal explanation

**Decision**: Attribute a regression to the single most recent comparable run
(the immediately preceding entry sharing the same methodology fingerprint —
same agent target, dataset, evaluator set, per
`_methodology_fingerprint()` in `src/agentops/agent/sources/results_history.py:145`),
not the rolling mean that Doctor's existing regression check
(`src/agentops/agent/checks/regression.py`) uses for its drop-percentage math.

**Rationale**: The product owner's own example ("Corrida v3 → v4") compares
adjacent versions. A rolling mean of several prior runs has no single
corresponding commit to diff against; the immediately preceding run does.

**Alternatives considered**: Diffing against every run in the rolling-baseline
window and summarizing an aggregate — rejected as harder to state in one or
two sentences and not what a human would do manually first.

## 4. Handling unreachable git history (shallow clones, force-push, pruned commits)

**Decision**: When both compared commits are present in the git history
reachable from the current checkout, diff config/prompt-related fields via
direct comparison of the two runs' recorded `config`/`target` values (not
`git diff` of the whole tree — see #5). When one or both commits are not
resolvable at all in local git (e.g. GitHub Actions' default `fetch-depth: 1`
checkout doesn't contain the older commit), skip any git lookups and fall
back to comparing only the fields already recorded in each run's own stored
`results.json` (target, config, thresholds, dataset, evaluators).

**Rationale**: The generated PR workflow (`agentops-pr.yml`) uses a default,
shallow checkout. Requiring `fetch-depth: 0` to make this feature work at all
would be a breaking, latency-adding change to every existing generated
workflow. The fields already recorded per run are sufficient for the FR-006
comparison (prompt/model/config) without needing tree-level git access.

**Alternatives considered**: Generating workflows with full history fetch —
rejected as an unwanted default change with a real CI-time cost, for a
feature that must degrade gracefully anyway per FR-009.

## 5. How "what changed" is determined (no LLM, per FR-013)

**Decision**: Diff deterministically over fields already present in
`RunResult.target` (`kind`, `name`, `version`, `deployment` — Foundry
prompt/version or model identifier) and `RunResult.config` (dataset path,
evaluator list, thresholds), comparing the regressed run's values against the
prior comparable run's values field-by-field. "System prompt changed" is
inferred from a change in the Foundry prompt agent's `name:version` pair (a
new version implies new prompt content, matching how
`prompt_deploy.py` already tracks `prompt_sha256` per version) rather than by
fetching and diffing prompt text itself.

**Rationale**: `results.json` already records everything needed for this
comparison for every execution mode; no new capture step or Azure/Foundry
call is required, keeping the check fast, offline-capable, and deterministic
as FR-013 requires.

**Alternatives considered**: Calling Foundry to fetch and diff the full agent
definitions (instructions text) for both versions — rejected; adds a new
Azure SDK dependency and network round-trip into a check that must also work
for local execution mode and when regression is detected via Doctor's static
history scan.

## 6. Where the committed PR baseline fits in

**Finding (not a new decision)**: The generated PR workflow already supports
comparing against a **committed** baseline file at
`.agentops/baseline/results.json` (`src/agentops/services/cicd.py:311`,
auto-detected and passed as `--baseline` — see
`_github_baseline_autodetect_block`). Because that file is a normal
git-tracked file, once it carries a `commit` field (from whenever it was last
promoted), the explicit `--baseline` comparison path
(`pipeline/comparison.py:build_comparison`) already has both full `RunResult`
objects in memory — current and baseline — at the exact point `report.md` is
rendered. This is the natural, lowest-effort integration point for User Story
1's hosted-agent/CI scenario: no new file I/O is needed beyond what
`--baseline` already does.

## 7. Where Cockpit's history view plugs in

**Decision**: Extend `_project_run()`
(`src/agentops/agent/cockpit.py:856`) to include the run's `commit` field (if
present) and a computed `changed_inputs` list versus the previous entry
sharing the same methodology fingerprint, reusing the same field-diff helper
from #5. `_load_eval_runs()` (`cockpit.py:827`) already returns an ordered,
scanned list of runs from `.agentops/results/*/results.json` — the version
history view is a new rendering of that same list, not a new data source.

**Rationale**: This is the same scan Doctor's regression check and
`results_history.py` already perform; no new persisted index or storage is
needed once every run carries its own `commit` field.

**Alternatives considered**: A separate persisted history/index file —
rejected as redundant once per-run files carry everything needed.

## 8. Regression source: explicit `--baseline` vs. Doctor's rolling check

**Finding**: There are two independent regression signals in the codebase
today: `pipeline/comparison.py` (explicit, single-baseline, informational,
included in `report.md`) and `agent/checks/regression.py` (Doctor's rolling
mean vs. `min_runs`, surfaced as a `Finding`). Per spec edge cases, both must
produce the same attribution logic. The shared diff/explanation helper (a new
`pipeline/regression_insight.py`) is written to take two `RunResult`-shaped
inputs (or the fields needed from them) and is called from both
`pipeline/comparison.py`'s call site (for the report) and
`agent/checks/regression.py` (attached to `Finding.evidence`, which is
already a free-form `Dict[str, Any]` — no schema break there).

**Rationale**: One shared helper avoids re-implementing the same field-diff
and sentence-generation logic twice, and keeps the explanation consistent
regardless of which detector fired.
