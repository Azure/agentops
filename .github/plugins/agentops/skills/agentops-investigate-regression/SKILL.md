---
name: agentops-investigate-regression
description: Help users investigate evaluation regressions in AgentOps outputs by comparing runs and analyzing metric deltas. Trigger when users say "regression", "score dropped", "threshold started failing", "compare runs", "why did this eval get worse", or "debug evaluation drift". Install agentops-toolkit via pip to use the CLI. Relevant commands are agentops eval run, agentops eval compare, and agentops report.
---

# AgentOps Investigate Regression

> **Prerequisite:** Install the AgentOps CLI with `pip install agentops-toolkit`.

## Purpose
Guide Copilot users through regression investigation using AgentOps comparison tooling and artifact review.

## When to Use
- User reports lower scores versus previous runs.
- User reports new threshold failures.
- User asks to compare current and prior evaluation outcomes.
- CI gating changed from pass to fail and root cause is unclear.

## Available Commands

```bash
agentops eval run                                   # Generate fresh results
agentops report                                     # Regenerate report
agentops eval compare --runs <baseline>,<current>   # Compare two runs
```

Run identifiers for `--runs` can be:
- Timestamped folder names (e.g. `2026-03-01_100000`)
- The keyword `latest`
- Absolute or relative paths to a `results.json` or a run directory

## Investigation Workflow

1. **Reproduce:** `agentops eval run` to get fresh results.
2. **Compare:** `agentops eval compare --runs <baseline>,latest` to compute deltas.
3. **Read:** `comparison.md` for human-readable diff summary.
4. **Analyze:** `comparison.json` for structured metric deltas and threshold flips.
5. **Act:** Follow actionable checks based on findings.

## Comparison Outputs
- `comparison.json` — metric deltas, threshold flips, item-level changes
- `comparison.md` — human-readable comparison report

Exit codes for compare:
- `0` = no regressions detected
- `2` = regressions detected
- `1` = error

## Interpretation Guidance
- Use `comparison.md` for a quick summary of what changed between runs.
- Separate findings into:
  - **Observations:** directly supported by artifacts (metric deltas, threshold flips, item changes).
  - **Hypotheses:** plausible causes requiring validation (dataset change, model drift, config change).
- Prioritize impact-first:
  - Which thresholds flipped (pass→fail = regression)?
  - Which metrics degraded most (check `metric_deltas`)?
  - Is degradation broad or concentrated in specific rows (check `item_deltas`)?
- End with actionable next checks (rerun with controlled changes, diff datasets, verify configuration).

## Guardrails
- Do not infer causality from correlation alone.
- Do not mix speculation into factual summary sections.
- Keep remediation advice tied to reproducible checks using generated artifacts.

## Examples
- "My pass rate dropped after changing model deployment."
  → `agentops eval run`, then `agentops eval compare --runs <baseline>,latest`. Review metric deltas and propose controlled rollback.
- "Can you compare run 42 and 43?"
  → `agentops eval compare --runs 2026-03-01_100000,2026-03-02_140000`. Review `comparison.md`.
- "Why is CI failing now?"
  → `agentops eval compare --runs latest,<previous>` to identify flipped thresholds.

## Learn More
- Documentation: https://github.com/Azure/agentops
- PyPI: https://pypi.org/project/agentops-toolkit/
