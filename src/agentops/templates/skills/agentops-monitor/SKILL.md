---
name: agentops-monitor
description: Guidance on monitoring evaluation quality over time. Trigger when users ask about tracking scores, setting up dashboards, or configuring quality alerts. Common phrases include "monitoring", "dashboards", "alerts", "monitor setup", "quality over time", "trending", "track scores", "evaluation health". Install agentops-toolkit via pip.
---

# AgentOps Monitor

## Purpose

Provide guidance on monitoring evaluation quality over time. The `agentops monitor` commands are **planned but not yet implemented**.

## Before You Start

1. **AgentOps installed?** Check if `agentops` CLI is available. If not: `pip install agentops-toolkit`.
2. **Workspace exists?** Check for `.agentops/`. If missing: `agentops init`.
3. **Foundry endpoint configured?** Search for `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` in environment variables, `.env`, `.env.local`. If not found, ask the user for the endpoint URL and instruct them to set it.

## Status

🚧 **Not yet implemented.** The CLI stubs exist but have no runtime behavior.

## Current Alternatives

Until `agentops monitor` is available:

| Approach | How |
|---|---|
| Manual trending | Compare `results.json` across timestamped runs in `.agentops/results/` |
| CI gating | Use exit code `2` in GitHub Actions to block PRs on quality regressions |
| Foundry portal | View evaluation history in the Foundry Experience dashboard |
| Run comparison | `agentops eval compare --runs <old>,<new>` for side-by-side delta |

## What Will Be Available

When implemented:
- `agentops monitor show` — Display evaluation quality dashboard
- `agentops monitor configure` — Set up alerts and quality thresholds

## Guardrails

- Do not pretend monitoring features exist — clearly state they are planned.
- For quality tracking today, recommend `agentops eval compare` and CI exit codes.
- For production monitoring, recommend Azure Monitor and Foundry portal.
