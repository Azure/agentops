# `waf-checklist.csv` — workspace WAF checklist

This CSV is a **workspace override** for the AgentOps Doctor's WAF
knowledge base. The Doctor reads `.agentops/waf-checklist.csv` on
every run; rows here either:

* **Override** rows shipped in the packaged checklist
  (`agentops/agent/knowledge/waf-checklist.csv`) by matching the
  `doctor_check_id` column, **or**
* **Extend** the checklist with new rows that map your own custom
  finding ids onto WAF pillars.

The seed file copied into your workspace by `agentops init` ships a
**curated shortlist** drawn from the public **Microsoft Azure AI
Landing Zones Checklist** (177 items), filtered to *only* items the
Doctor verifies automatically today.

## Curation policy

* **Automation-only.** The Doctor is an *agent*; its purpose is to
  remove manual toil. The seed checklist therefore ships only items
  where the Doctor already has a working, deterministic rule (or an
  opt-in LLM-judged rule) that surfaces the signal without human
  intervention.
* No `manual` rows, no `planned` rows. New items earn their spot
  here when their rule actually runs — not as a "we will do this
  later" promise that is easy to forget.
* Sourced from the public AI Landing Zones Checklist
  (https://learn.microsoft.com/azure/cloud-adoption-framework/scenarios/ai/),
  prioritized by severity / leverage.

## Column reference

| Column | Meaning |
|---|---|
| `pillar` | WAF pillar — `Security`, `Reliability`, `Performance`, or `OperationalExcellence`. |
| `area` | Free-text sub-area (e.g. `Identity`, `CI-CD`, `Telemetry`). |
| `item_id` | Stable short id. Items sourced from the AI Landing Zones Checklist use `ai_lz.AI.<n>`. |
| `title` | User-facing short label. Includes the original `[AI.<n>]` reference for traceability. |
| `detection_source` | Doctor source(s) that produce this signal — `results_history`, `azure_monitor`, `foundry_control`, `azure_resources`, `workspace_files`. |
| `detection_signal` | Short description of how the rule checks the signal (programmatic predicate or LLM judge). |
| `doctor_check_id` | The finding id Doctor emits. **A row with this column empty is ignored by the loader.** |
| `status` | Always `implemented` in this file — the policy is to ship only working checks. |
| `reference_url` | Public Microsoft Learn URL for the WAF pillar. |

## How to extend

Add a new row at the bottom of the CSV when you implement a new
deterministic check or LLM-judged rule. Example:

```csv
OperationalExcellence,Custom,my.team.review_cadence,Monthly review cadence enforced,workspace_files,review-log file modified within last 35 days,opex.review_cadence,implemented,https://your-wiki/policies/review
```

Two practical rules to keep in mind:

1. **Stay strict on `doctor_check_id`** — the loader skips rows
   whose `doctor_check_id` is empty. The id you put here must match
   the id the rule actually emits at runtime, otherwise the WAF
   citation won't show up next to the finding.
2. **No comment lines.** CSV has no portable comment syntax; this
   `README.md` is the canonical place for documentation. The Doctor's
   loader currently tolerates `#`-prefixed lines (they're skipped at
   the `_row_to_item` filter), but Excel / pandas / any third-party
   parser will treat them as data and clutter the file.

## Seed shortlist

19 items, all with a working rule. Per-pillar coverage is uneven by
design — we ship what verifies automatically, not a forced 10-per-
pillar quota.

| Pillar | Implemented |
|---|---:|
| Security | 6 |
| OperationalExcellence | 7 |
| Reliability | 3 |
| Performance | 3 |
| **Total** | **19** |

The Cost pillar is **not represented** in the seed file: the Doctor
has no automated cost check today. When a cost rule lands (e.g.,
`max_tokens` enforcement parsed from `run.yaml`, or budget-alert
audit via the Cost Management API), it will be added here.
