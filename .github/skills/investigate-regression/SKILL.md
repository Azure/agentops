---
name: investigate-regression
description: Help users investigate potential evaluation regressions in AgentOps outputs using implemented tooling and artifact review. Trigger when users say "regression", "score dropped", "threshold started failing", "compare runs", "why did this eval get worse", or "debug evaluation drift". Relevant current commands: `agentops eval run`, `agentops report`. Planned but stubbed command: `agentops eval compare --runs ID1,ID2`.
---

# Investigate Regression

## Purpose
Help Copilot guide users through regression investigation using currently available AgentOps outputs while clearly marking compare automation as planned/stubbed.

## When to Use
- User reports lower scores versus previous runs.
- User reports new threshold failures.
- User asks to compare current and prior evaluation outcomes.
- CI gating changed from pass to fail and root cause is unclear.

## Required Inputs
- At least one recent run artifact set:
  - `results.json`
  - `report.md`
- Preferably a baseline run artifact set for side-by-side checks.
- Context about what changed (prompt, model/deployment, dataset, bundle, backend mode, environment).

## Recommended Command Patterns
Use available commands to generate fresh artifacts:

```bash
agentops eval run
agentops report
```

For historical comparison, use manual artifact analysis until compare tooling is implemented.

Planned/stubbed (not available yet):

```bash
agentops eval compare --runs ID1,ID2
```

## Expected Outputs
- Fresh run outputs in the results directory, including `results.json` and `report.md`.
- Manual comparison notes covering:
  - Metric deltas
  - Threshold pass/fail changes
  - Any row-level concentration of failures

## Interpretation Guidance
- Separate statements into two buckets:
  - Observations: directly supported by artifacts.
  - Hypotheses: plausible causes requiring validation.
- Compare baseline vs current using concrete fields from `results.json` and summary sections from `report.md`.
- Prioritize impact-first interpretation:
  - Which thresholds flipped
  - Which metrics degraded most
  - Whether degradation is broad or concentrated in specific rows/use-cases
- End with actionable next checks (rerun with controlled changes, validate dataset consistency, verify configuration inputs).

## Guardrails
- Do not claim `agentops eval compare` is implemented.
- Do not infer causality from correlation alone.
- Do not mix speculation into factual summary sections.
- Keep remediation advice tied to reproducible checks using generated artifacts.

## Examples
- "My pass rate dropped after changing model deployment."
  - Re-run with `agentops eval run`, regenerate with `agentops report`, compare current artifacts to baseline, report factual deltas, then propose controlled rollback/retest.
- "Can you compare run 42 and 43?"
  - Explain `agentops eval compare --runs ...` is planned/stubbed; perform manual comparison of each run's `results.json` and `report.md`.
- "Why is CI failing now?"
  - Identify which thresholds now fail in current artifacts, then list likely causes as hypotheses and propose targeted confirmation steps.
