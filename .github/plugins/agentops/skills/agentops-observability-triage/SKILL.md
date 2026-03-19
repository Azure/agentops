---
name: agentops-observability-triage
description: Guide users on observability and triage workflows for AgentOps evaluations. Trigger when users ask about tracing, monitoring, dashboards, alerts, run health, or production triage. Common phrases include "set up tracing", "monitor evals", "create alerts", "triage failed evaluations", "observability for agentops". Install agentops-toolkit via pip. Currently available commands are agentops eval run and agentops report. Tracing and monitoring commands are planned for a future release.
---

# AgentOps Observability Triage

> **Prerequisite:** Install the AgentOps CLI with `pip install agentops-toolkit`.

## Purpose
Provide honest, practical observability guidance for AgentOps evaluations — use current reporting artifacts today, and frame tracing/monitoring commands as planned future workflow.

## When to Use
- User asks how to monitor ongoing evaluation quality.
- User asks for tracing setup in AgentOps.
- User asks for dashboards or alerts around evaluation regressions.
- User needs triage steps after an unexpected evaluation outcome.

## Available Commands (Current)

```bash
agentops eval run                                   # Generate fresh results
agentops report                                     # Regenerate report
agentops eval compare --runs <baseline>,<current>   # Compare runs
```

## Planned Commands (Not Yet Available)

```bash
agentops trace init             # Initialize tracing
agentops monitor setup          # Set up monitoring
agentops monitor dashboard      # Configure dashboards
agentops monitor alert          # Configure alerts
```

## Triage Workflow (Current)

1. **Generate artifacts:** `agentops eval run`
2. **Quick triage:** Read `report.md` — what failed? How severe?
3. **Detailed inspection:** Read `results.json` — metric values, row-level checks.
4. **Compare with baseline:** `agentops eval compare --runs <baseline>,latest`
5. **Review cloud portal:** Check `cloud_evaluation.json` for Foundry portal URL.

## Interpretation Guidance
- Use `report.md` for quick operational triage.
- Use `results.json` for detailed metric and threshold inspection.
- When users ask for observability features not yet implemented:
  - State explicitly: planned/stubbed, not available yet.
  - Provide immediate fallback: artifact-based troubleshooting.
  - Suggest preparation: keep run artifacts organized for future automation.

## Guardrails
- Do not present tracing or monitoring commands as available today.
- Do not imply real-time dashboards or alerts currently exist in the CLI.
- Always pivot unsupported requests to concrete available outputs (`results.json`, `report.md`, `comparison.md`).
- Be explicit about current capability vs roadmap.

## Examples
- "How do I set up tracing for AgentOps?"
  → Tracing (`agentops trace init`) is planned but not implemented. For now, use `agentops eval run` and inspect `results.json`.
- "Can AgentOps create monitoring alerts?"
  → Monitoring commands are planned/stubbed. Use `agentops eval compare` to detect regressions today.
- "What should I do after a sudden quality drop?"
  → `agentops eval run`, then `agentops eval compare --runs <baseline>,latest`, review `comparison.md`.

## Learn More
- Documentation: https://github.com/Azure/agentops
- PyPI: https://pypi.org/project/agentops-toolkit/
