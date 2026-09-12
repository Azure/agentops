# Contract: `report.md` section and Cockpit history payload

## `report.md`: new "Regression Insight" section

Rendered by `pipeline/reporter.py` immediately after the existing "Comparison
vs Baseline" section (`_render_comparison`), and only when
`result.comparison.insight` is present:

```markdown
## Comparison vs Baseline
...existing table...

## Regression Insight

Run v3 → v4: **accuracy** dropped from 0.91 to 0.79.

**Likely cause:** the system prompt changed and the model changed from
`gpt-4o` to `gpt-4o-mini`.

**Suggested action:** review the prompt change and the model swap; consider
reverting one at a time to isolate the cause.
```

This section is purely additive to the report: it never replaces the
existing Metrics/Thresholds/Comparison/Rows sections, and its absence (no
regression, or missing commit metadata on either side) leaves `report.md`
byte-for-byte identical to today's output. Because this is the same
`report.md` already uploaded as the `agentops-pr-results` CI artifact and
posted as the PR comment by the generated `agentops-pr.yml` workflow, no
workflow template changes are required to deliver it (FR-010).

Doctor's rolling-baseline `Finding` (`agent/checks/regression.py`) gets the
same explanation text via `Finding.evidence["insight"]`, and Doctor's own
Markdown rendering already surfaces `Finding.summary`/`recommendation` — this
plan extends that finding's `recommendation` text with the same
deterministic explanation when an insight is available, in place of today's
generic "inspect prompt/model/dataset changes" instruction.

## Cockpit: version history

No new HTTP route. The existing partial-load endpoint
(`GET /?_partial=1`, `cockpit.py:5405`) response gains a new section
alongside the existing eval "cards" built by `_build_eval_section`
(`cockpit.py:180`):

```jsonc
{
  // ...existing cockpit payload sections unchanged...
  "eval_history": {
    "has_runs": true,
    "entries": [
      {
        "run_id": "20260901-101500",
        "timestamp": "2026-09-01T10:15:00Z",
        "commit": { "short_sha": "a1b2c3d", "subject": "..." },
        "metrics": { "accuracy": 0.91 },
        "methodology_fingerprint": "9f2a...",
        "changed_inputs": [],
        "regressed": false
      },
      {
        "run_id": "20260910-140300",
        "timestamp": "2026-09-10T14:03:00Z",
        "commit": { "short_sha": "b7e91aa", "subject": "..." },
        "metrics": { "accuracy": 0.79 },
        "methodology_fingerprint": "9f2a...",
        "changed_inputs": [
          { "field": "model", "description": "model changed from gpt-4o to gpt-4o-mini" }
        ],
        "regressed": true
      }
    ]
  }
}
```

Entries are grouped/ordered by `methodology_fingerprint` (same grouping key
already used by `results_history.py` and Doctor's regression check), oldest
first. A run with no resolvable `commit` still appears, with `commit: null`
and an empty `changed_inputs` list rather than being omitted (per User Story
2's acceptance scenario 3). This view is read-only and introduces no writes
to any monitored resource, consistent with the constitution's Cockpit
read-only requirement.
