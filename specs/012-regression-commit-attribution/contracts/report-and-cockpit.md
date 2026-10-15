# Contract: `report.md` section and Cockpit history payload

## `report.md`: new "Regression Insight" section

Rendered by `pipeline/reporter.py` immediately after the existing "Comparison
vs Baseline" section (`_render_comparison`), and only when
`result.comparison.insight` is present:

```markdown
## Comparison vs Baseline
...existing table...

## Regression Insight

**Regressed metrics:**
- `similarity`: 4.000 → 3.000
- `accuracy`: 0.910 → 0.790

Run v3 → v4: similarity dropped from 4.00 to 3.00 and accuracy dropped from
0.91 to 0.79. Likely cause: the system prompt changed and the model changed
from `gpt-4o` to `gpt-4o-mini`.

**Suggested action:** review the prompt change and the model swap; consider
reverting one at a time to isolate the cause.

- [Baseline run in Foundry](https://ai.azure.com/...)
- [Regressed run in Foundry](https://ai.azure.com/...)
```

Every metric that regressed is listed - not just the single worst one - in
`RegressionInsight.regressed_metrics`, ordered worst-first
(direction-aware, so a lower-is-better metric like `avg_latency_seconds`
getting worse still sorts as a regression). The "Regressed metrics" bullet
list only renders when there's more than one; a single-metric regression
keeps the original one-line prose shape. The two Foundry links
(`from_report_url`/`to_report_url`) render only when that side was
published (`execution: cloud`, or a completed local `publish: true`) -
omitted silently otherwise, never fabricated. For the live `--baseline`
comparison specifically, the *current* run's own link is initially `None`
when the run will go on to `publish: true`, because that publish step
happens after this comparison is built and persisted - but
`orchestrator._publish_to_foundry_safely` patches the value in and
re-persists `results.json`/`report.md` once that publish step actually
completes, so the already-written files don't permanently miss their own
link. Doctor's rolling check, which compares two already-completed
historical runs, never has this gap in the first place.

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
generic "inspect prompt/model/dataset changes" instruction. When no insight
could be built at all (e.g. Doctor fell back to a Foundry cloud-fetched run
with no local `results.json`, so no commit), the recommendation instead
names why attribution is unavailable
(`Finding.evidence["attribution_unavailable"]`) rather than silently
omitting it.

## Cockpit: version history

No new HTTP route. The existing partial-load endpoint
(`GET /?_partial=1`) response gains a new `eval_history` section
(`_build_eval_history_section`), alongside the existing eval "cards"
section. This is the actual payload shape (`cockpit.py`'s
`_build_eval_history_section`/`_attach_version_history`), **newest run
first**:

```jsonc
{
  // ...existing cockpit payload sections unchanged...
  "eval_history": {
    "has_runs": true,
    "entries": [
      {
        "run_id": "20260910-140300",
        "timestamp": "2026-09-10T14:03:00Z",
        "target": "greeter:4",
        "metrics": { "accuracy": 0.79 },
        "commit_short_sha": "b7e91aa",
        "commit_subject": "Swap eval agent to gpt-4o-mini",
        "changed_inputs": [
          { "field": "model", "description": "model changed from gpt-4o to gpt-4o-mini", "from_value": "gpt-4o", "to_value": "gpt-4o-mini" }
        ],
        "regressed": true,
        "regressed_metrics": ["accuracy"],
        "report_link": "/api/runs/20260910-140300/report",
        "cloud_report_url": null,
        "previous_cloud_report_url": "https://ai.azure.com/foundry/baseline"
      },
      {
        "run_id": "20260901-101500",
        "timestamp": "2026-09-01T10:15:00Z",
        "target": "greeter:3",
        "metrics": { "accuracy": 0.91 },
        "commit_short_sha": "a1b2c3d",
        "commit_subject": "Initial smoke eval",
        "changed_inputs": [],
        "regressed": false,
        "regressed_metrics": [],
        "report_link": "https://ai.azure.com/foundry/baseline",
        "cloud_report_url": "https://ai.azure.com/foundry/baseline",
        "previous_cloud_report_url": null
      }
    ]
  }
}
```

Entries are grouped/compared using `version_lineage_key` - a coarser,
version/deployment-blind key (same agent identity, dataset, evaluators)
than Doctor's `methodology_fingerprint`, deliberately: this view exists to
show what changed *across* version bumps, where Doctor's rolling check
deliberately excludes a version bump from its own baseline. `commit` is
flattened to `commit_short_sha`/`commit_subject` (not a nested object); a
run with no resolvable commit still appears, with both `null` and an
empty `changed_inputs` list rather than being omitted (per User Story 2's
acceptance scenario 3). `regressed_metrics` names every metric that
regressed relative to the previous entry in its lineage, not just a
boolean - a lower-is-better metric (e.g. `avg_latency_seconds`) getting
*better* is never counted as a regression. `cloud_report_url` (this run's
own Foundry Evaluations link, `null` if never published) and
`previous_cloud_report_url` (the previous lineage-comparable run's, same
rule) let a regressed row link to both sides of the comparison; `report_link`
is unrelated pre-existing per-row routing (Foundry when published, else
this run's own local report page) and always non-null. This view is
read-only and introduces no writes to any monitored resource, consistent
with the constitution's Cockpit read-only requirement.
