---
name: agentops-run-evals
description: Guide users through running AgentOps evaluations end to end. Trigger when users ask to initialize AgentOps, run an evaluation, regenerate a report, or summarize results. Common phrases include "run eval", "start agentops", "how do I use run.yaml", "regenerate report", "explain evaluation results". Install agentops-toolkit via pip to use the CLI. Relevant commands are agentops init, agentops eval run, agentops eval compare, and agentops report.
---

# AgentOps Run Evaluations

> **Prerequisite:** Install the AgentOps CLI with `pip install agentops-toolkit`.

## Purpose
Guide Copilot users through the AgentOps evaluation workflow — from workspace setup to report interpretation and run comparison.

## When to Use
- User wants to start using AgentOps in a project.
- User asks how to run an evaluation with `run.yaml`.
- User asks how to regenerate `report.md` from `results.json`.
- User asks where evaluation outputs are written.
- User asks for a quick summary of the latest run outcome.
- User wants to compare two evaluation runs.

## Available Commands

```bash
pip install agentops-toolkit        # Install the CLI
agentops init [--path <dir>]        # Scaffold workspace
agentops eval run                   # Run evaluation
agentops report                     # Regenerate report.md
agentops eval compare --runs <baseline>,<current>  # Compare two runs
```

## Recommended Workflow

1. **Initialize workspace:** `agentops init`
2. **Configure** `.agentops/run.yaml` with bundle, dataset, and backend settings.
3. **Run evaluation:** `agentops eval run`
4. **Inspect results:** `.agentops/results/latest/results.json` and `report.md`
5. **Regenerate report** (optional): `agentops report`
6. **Compare against baseline:** `agentops eval compare --runs <baseline>,latest`

## Expected Outputs
- `results.json` — machine-readable normalized results
- `report.md` — human-readable summary with metrics and threshold verdicts
- `cloud_evaluation.json` — present in cloud evaluation flows with portal URL
- `comparison.json` + `comparison.md` — produced by `eval compare`

## Exit Codes
- `0` — evaluation succeeded and all thresholds passed
- `2` — evaluation succeeded but one or more thresholds failed
- `1` — runtime or configuration error

## Interpretation Guidance
- Start with `report.md` for quick pass/fail narrative.
- Use `results.json` for metric-level details and row-level checks.
- Report concrete facts (metrics, thresholds) first, then brief interpretation.

## Guardrails
- Do not invent commands or flags beyond documented CLI behavior.
- Planned commands (`run list`, `bundle show`, `trace init`, `monitor`) are not yet implemented — state they are planned/stubbed.
- `agentops eval compare --runs` is available — use it for run comparison.

## Examples
- "How do I start using AgentOps?"
  → `pip install agentops-toolkit`, then `agentops init`, `agentops eval run`
- "Where are my results?"
  → `.agentops/results/latest/results.json` and `report.md`
- "Compare my last two runs"
  → `agentops eval compare --runs <previous_timestamp>,latest`

## Learn More
- Documentation: https://github.com/Azure/agentops
- PyPI: https://pypi.org/project/agentops-toolkit/
