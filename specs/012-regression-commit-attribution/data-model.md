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
| `metric` | `str` | The regressed metric's name. |
| `from_value` | `float` | Metric value on the prior run. |
| `to_value` | `float` | Metric value on the regressed run. |
| `changed_inputs` | `List[ChangedInput]` | All detected changes, not just the first found (per spec edge case). |
| `explanation` | `str` | The rendered one-to-two-sentence plain-language summary (FR-007). |
| `suggested_action` | `Optional[str]` | Brief, rule-based corrective suggestion (FR-008). |
| `used_git_diff` | `bool` | `True` when both commits were reachable in local git history for a fuller diff; `False` when this fell back to comparing only the fields already recorded in each run's stored result (research.md #4). |

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

**VersionHistoryEntry** (dict shape returned by `_project_run()` /
consumed by the cockpit UI):

| Key | Type | Notes |
|---|---|---|
| `run_id` | `str` | Existing field. |
| `timestamp` | `Optional[str]` | Existing field. |
| `commit` | `Optional[dict]` | New: `CommitInfo.model_dump()` when known. |
| `metrics` | `Dict[str, float]` | Existing field. |
| `methodology_fingerprint` | `Optional[str]` | New: reused from `results_history._methodology_fingerprint()` so entries can be grouped/ordered per lineage. |
| `changed_inputs` | `List[dict]` | New: `ChangedInput` list vs. the previous entry sharing the same fingerprint; empty for the first run of a fingerprint. |
| `regressed` | `bool` | New: whether any metric regressed vs. the previous entry (drives a visual marker; the list itself is not regression-gated per FR-012/User Story 2). |

## State / lifecycle notes

These are all point-in-time computed values attached to an immutable run
result at write time (`commit`) or derived at read time (`insight`,
`changed_inputs`) — there are no state transitions to model. A `RunResult`,
once persisted, is never mutated in place.
