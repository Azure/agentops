---
name: agentops-monitor
description: Guidance on monitoring evaluation quality over time. Trigger when users ask about tracking scores, setting up dashboards, or configuring quality alerts. Common phrases include "monitoring", "dashboards", "alerts", "monitor setup", "quality over time", "trending", "track scores", "evaluation health". Install agentops-toolkit via pip.
---

# AgentOps Monitor

**Not yet implemented.** The `agentops monitor` commands are planned but have no runtime behavior.

## Current Alternatives

| Approach | How |
|---|---|
| Run comparison | `agentops eval compare --runs <old>,<new>` |
| CI gating | Exit code `2` in GitHub Actions blocks PRs on regressions |
| Foundry portal | View evaluation history in the Foundry Experience dashboard |
| Manual trending | Compare `results.json` across timestamped runs in `.agentops/results/` |

## Planned Commands

- `agentops monitor show` — evaluation quality dashboard
- `agentops monitor configure` — alerts and quality thresholds

## Rules

- Do not pretend monitoring features exist — state they are planned.
- For quality tracking today, recommend `agentops eval compare` and CI exit codes.
- For production monitoring, recommend Azure Monitor and Foundry portal.
