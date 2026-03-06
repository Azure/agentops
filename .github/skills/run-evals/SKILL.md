---
name: run-evals
description: Guide users through running AgentOps evaluations end to end using implemented CLI commands. Trigger when users ask to initialize AgentOps, run an evaluation, regenerate a report, locate run.yaml, or summarize results.json/report.md. Common phrases: "run eval", "start agentops", "how do I use run.yaml", "regenerate report", "explain evaluation results". Relevant commands: `agentops init [--path DIR]`, `agentops eval run`, `agentops report`.
---

# Run Evaluations

## Purpose
Help Copilot guide users through the currently implemented AgentOps evaluation workflow from workspace setup to report interpretation.

## When to Use
- User wants to start using AgentOps in a repo.
- User asks how to run an evaluation with `run.yaml`.
- User asks how to regenerate `report.md` from `results.json`.
- User asks where evaluation outputs are written.
- User asks for a quick summary of the latest run outcome.

## Required Inputs
- Project root path where `agentops` commands should run.
- Evaluation run config path when not using default (commonly `.agentops/run.yaml`).
- Optional output directory if the user wants non-default output placement.
- Access to generated artifacts for interpretation:
  - `results.json`
  - `report.md`

## Recommended Command Patterns
Use only implemented commands.

```bash
agentops init [--path <dir>]
agentops eval run
agentops report
```

Common operational sequence:
1. Initialize workspace: `agentops init`
2. Confirm run config exists (typically `.agentops/run.yaml`).
3. Execute evaluation: `agentops eval run`
4. Regenerate markdown report when needed: `agentops report`
5. Inspect outputs under `.agentops/results/latest/`.

## Expected Outputs
- `results.json` (machine-readable normalized results)
- `report.md` (human-readable summary)
- In cloud evaluation flows, `cloud_evaluation.json` may also be present.

Typical latest pointers:
- `.agentops/results/latest/results.json`
- `.agentops/results/latest/report.md`

## Interpretation Guidance
- Start with `report.md` for a quick pass/fail narrative and threshold view.
- Use `results.json` for metric-level details, row-level checks, and automation.
- Distinguish execution states clearly:
  - Run completed with thresholds passing.
  - Run completed with threshold failures.
  - Run/config/runtime error.
- When summarizing, report concrete facts first (metrics and threshold outcomes), then brief interpretation.

## Guardrails
- Do not invent commands or flags beyond documented CLI behavior.
- Do not present planned commands as available.
- If a user asks for compare or run-history commands, state they are planned/stubbed and pivot to artifact inspection using `results.json` and `report.md`.
- Keep guidance operational; avoid architecture duplication from `.github/copilot-instructions.md`.

## Examples
- "Initialize and run an eval in this repo."
  - Use: `agentops init`, then `agentops eval run`, then `agentops report`.
- "Where is my run config?"
  - Check `.agentops/run.yaml` first; if custom path is used, run with that config.
- "Summarize my last evaluation."
  - Read `.agentops/results/latest/report.md` and confirm details with `.agentops/results/latest/results.json`.
