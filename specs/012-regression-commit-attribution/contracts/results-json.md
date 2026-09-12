# Contract: `results.json` additions

`results.json` is a documented public contract (constitution, Principle I).
This feature only adds optional, additive fields — no existing field
changes meaning or type, and no consumer that ignores unknown-to-it new
top-level keys breaks.

## New top-level field: `commit`

```jsonc
{
  // ...existing RunResult fields unchanged...
  "commit": {
    "sha": "a1b2c3d4e5f6...",
    "short_sha": "a1b2c3d",
    "subject": "Swap eval agent to gpt-4o-mini",
    "author": "Jane Doe",
    "authored_at": "2026-09-10T14:03:00Z",
    "source": "ci"
  }
  // or "commit": null when it could not be determined
}
```

Absent/`null` on any run produced before this feature shipped, or when commit
metadata could not be resolved (not a git repo, git unavailable). Consumers
MUST treat a missing/`null` `commit` the same as before this feature existed.

## New field on `comparison`: `insight`

Only present when `comparison` (the existing `--baseline` block) is present
**and** a regression was detected **and** both the current and baseline runs
have non-null `commit`:

```jsonc
{
  "comparison": {
    // ...existing ComparisonInfo fields unchanged...
    "insight": {
      "from_run_id": "20260901-101500",
      "to_run_id": "20260910-140300",
      "from_commit": { "sha": "...", "short_sha": "...", "subject": "...", "author": "...", "authored_at": "...", "source": "ci" },
      "to_commit": { "sha": "...", "short_sha": "...", "subject": "...", "author": "...", "authored_at": "...", "source": "ci" },
      "metric": "accuracy",
      "from_value": 0.91,
      "to_value": 0.79,
      "changed_inputs": [
        { "field": "system_prompt", "description": "the system prompt changed", "from_value": "greeter:v3", "to_value": "greeter:v4" },
        { "field": "model", "description": "model changed from gpt-4o to gpt-4o-mini", "from_value": "gpt-4o", "to_value": "gpt-4o-mini" }
      ],
      "explanation": "Run v3 → v4: accuracy dropped from 0.91 to 0.79. Likely cause: the system prompt changed and the model changed from gpt-4o to gpt-4o-mini.",
      "suggested_action": "Review the prompt change and the model swap; consider reverting one at a time to isolate the cause.",
      "used_git_diff": false
    }
  }
}
```

Absent (`comparison.insight` key not present) whenever no regression was
detected, either compared run lacks commit metadata, or `comparison` itself
is absent (no `--baseline` was used). This preserves every existing
`comparison`-shaped consumer.

## No changes to exit codes or CLI flags

This feature introduces no new `agentops eval run` flags and does not change
the exit-code contract (`0`/`1`/`2` meanings are unchanged, per FR-011). The
committed-baseline auto-detection already used by generated PR workflows
(`.agentops/baseline/results.json` → `--baseline`) is unchanged; this feature
only adds richer content to what that path already produces.
