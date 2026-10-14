# Phase 1 Data Model: Regression Commit Attribution

All new shapes are additive Pydantic v2 models in `src/agentops/core/results.py`
(per the constitution's "Preserve Public Contracts" and "pure `core/`" rules —
no I/O in these models). Existing fields are unchanged; nothing is removed.

## CommitInfo

Represents the git commit a single evaluation run was produced from.

| Field | Type | Notes |
|---|---|---|
| `sha` | `str` | Full commit SHA. Required when the object exists at all. |
| `short_sha` | `str` | First 7-12 chars, for display. |
| `subject` | `str` | First line of the commit message. |
| `author` | `str` | Commit author name (not committer). |
| `authored_at` | `str` | ISO 8601 commit timestamp. |
| `source` | `Literal["ci", "local"]` | How the SHA was resolved (CI env var vs. `git rev-parse HEAD`). |

**Validation rule**: `RunResult.commit` is either a fully-populated
`CommitInfo` or `None` — never a partial object. If any required piece
(subject/author/timestamp via `git show`) cannot be resolved after a SHA was
found, `commit` is left `None` rather than persisting a half-filled record.

## ChangedInput

One concrete difference detected between two runs' recorded fields.

| Field | Type | Notes |
|---|---|---|
| `field` | `str` | One of `"system_prompt"`, `"model"`, `"dataset"`, `"evaluators"`, `"thresholds"`, or another tracked config key. |
| `description` | `str` | Human-readable one-liner, e.g. `"model changed from gpt-4o to gpt-4o-mini"`. |
| `from_value` | `Optional[str]` | Prior value (truncated/hashed for large values such as prompt version identifiers). |
| `to_value` | `Optional[str]` | New value. |

## RegressionInsight

The causal explanation for a detected regression between two comparable runs.

| Field | Type | Notes |
|---|---|---|
| `from_run_id` | `str` | Identifier of the prior comparable run. |
| `to_run_id` | `str` | Identifier of the regressed run. |
| `from_commit` | `Optional[CommitInfo]` | Prior run's commit, if known. |
| `to_commit` | `Optional[CommitInfo]` | Regressed run's commit, if known. |
| `regressed_metrics` | `List[RegressedMetric]` | Every metric that regressed between the two runs, not just the worst one, ordered worst-first (direction-aware). Each entry: `{metric: str, from_value: float, to_value: float}`. |
| `changed_inputs` | `List[ChangedInput]` | All detected changes, not just the first found (per spec edge case). |
| `explanation` | `str` | The rendered plain-language summary covering every entry in `regressed_metrics` (FR-007). |
| `suggested_action` | `Optional[str]` | Brief, rule-based corrective suggestion (FR-008). |
| `commits_available_locally` | `bool` | `True` when both commits were reachable in local git history, `False` otherwise (e.g. a shallow CI checkout per research.md #4). Informational only - the comparison itself is always the field-based diff described in research.md #4/#5, never a tree-level `git diff`, regardless of this value. |
| `from_report_url` | `Optional[str]` | Foundry Evaluations deep-link for the prior run, when it was published (`execution: cloud`, or local `publish: true` after its publish step completed). `None` when not published - never fabricated. |
| `to_report_url` | `Optional[str]` | Same, for the regressed run. |

A `RegressedMetric` has `metric: str`, `from_value: float`, `to_value: float`.

**Validation rule**: A `RegressionInsight` is only ever constructed when both
`from_commit` and `to_commit` are non-`None` (FR-006, FR-009). If either run
lacks commit metadata, no `RegressionInsight` is produced at all — the plain
metric comparison (already existing `ComparisonInfo`/`Finding`) is left
untouched.

## Extensions to existing models

- **`RunResult`** (`src/agentops/core/results.py:100`): add
  `commit: Optional[CommitInfo] = None`. Fully additive; `model_config =
  ConfigDict(extra="forbid")` is preserved by declaring the field rather than
  stuffing it into the free-form `config: Dict[str, Any]`.
- **`ComparisonInfo`** (`results.py:90`): add
  `insight: Optional[RegressionInsight] = None`, populated by
  `pipeline/comparison.py` (or the caller in `orchestrator.py`) when a
  regressed metric and both commits are available.
- **Doctor `Finding.evidence`** (`agent/findings.py`, already
  `Dict[str, Any]`): gains an optional `"insight"` key holding
  `RegressionInsight.model_dump()` when `agent/checks/regression.py` produces
  one — no schema change needed since `evidence` is already free-form.

## Cockpit projection (not a persisted model — computed per request)

**VersionHistoryEntry** (dict shape returned by `_build_eval_history_section()`
/ consumed by the cockpit UI; see `contracts/report-and-cockpit.md` for the
full example payload):

| Key | Type | Notes |
|---|---|---|
| `run_id` | `str` | Existing field. |
| `timestamp` | `Optional[str]` | Existing field. |
| `commit_short_sha` / `commit_subject` | `Optional[str]` | New: flattened from `CommitInfo` when known (not a nested `commit` object). |
| `metrics` | `Dict[str, float]` | Existing field. |
| `changed_inputs` | `List[dict]` | New: `ChangedInput` list vs. the previous entry sharing the same `version_lineage_key` (**not** `methodology_fingerprint` - a deliberately coarser, version/deployment-blind key per FR-005/research.md #3, so a version bump is detected as a change within the same lineage rather than excluded from it); empty for the first run of a lineage. |
| `regressed` | `bool` | New: whether any metric regressed vs. the previous entry (drives a visual marker; the list itself is not regression-gated per FR-012/User Story 2). |
| `regressed_metrics` | `List[str]` | New: names every metric that regressed vs. the previous entry, direction-aware (a lower-is-better metric improving is never counted) - not just the boolean. |
| `cloud_report_url` / `previous_cloud_report_url` | `Optional[str]` | New: this entry's and the previous lineage-comparable entry's Foundry Evaluations link, when published; `None` otherwise. |

## State / lifecycle notes

These are all point-in-time computed values attached to an immutable run
result at write time (`commit`) or derived at read time (`insight`,
`changed_inputs`) — there are no state transitions to model. A `RunResult`,
once persisted, is never mutated in place.
