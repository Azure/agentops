---
name: observability-triage
description: Guide users on observability and triage workflows for AgentOps while accurately reflecting current CLI maturity. Trigger when users ask about tracing, monitoring, dashboards, alerts, run health, or production triage for evaluations. Common phrases: "set up tracing", "monitor evals", "create alerts", "triage failed evaluations", "observability for agentops". Current commands to anchor troubleshooting: `agentops eval run`, `agentops report`. Planned/stubbed commands: `agentops trace init`, `agentops monitor setup|dashboard|alert`.
---

# Observability Triage

## Purpose
Help Copilot provide honest, practical observability guidance: use current reporting artifacts today and frame tracing/monitoring commands as planned future workflow.

## When to Use
- User asks how to monitor ongoing evaluation quality.
- User asks for tracing setup in AgentOps.
- User asks for dashboards/alerts around regressions.
- User needs triage steps after an unexpected evaluation outcome.

## Required Inputs
- Current run artifacts (`results.json`, `report.md`) from the run under investigation.
- Optional prior run artifacts for trend context.
- Deployment/runtime context (backend target, environment, notable recent changes).

## Recommended Command Patterns
Use currently implemented commands for triage artifact generation:

```bash
agentops eval run
agentops report
```

Planned/stubbed observability commands (not implemented yet):

```bash
agentops trace init
agentops monitor setup
agentops monitor dashboard
agentops monitor alert
```

## Expected Outputs
Current state:
- `results.json` and `report.md` as primary triage artifacts.
- Optional `cloud_evaluation.json` in cloud evaluation mode.

Future direction (planned):
- Tracing initialization workflow.
- Monitoring setup, dashboard views, and alert configuration.

## Interpretation Guidance
- Use `report.md` for quick operational triage (what failed, how severe).
- Use `results.json` for detailed metric and threshold inspection.
- When users ask for observability features not yet implemented, provide:
  - Explicit status: planned/stubbed.
  - Immediate fallback: artifact-based troubleshooting.
  - Suggested preparation: keep run artifacts organized for future compare/monitor automation.

## Guardrails
- Do not present tracing or monitoring commands as available today.
- Do not imply real-time dashboards/alerts currently exist in CLI.
- Always pivot unsupported requests to concrete, available outputs (`results.json`, `report.md`).
- Keep language explicit about current capability vs roadmap.

## Examples
- "How do I set up tracing for AgentOps?"
  - Explain `agentops trace init` is planned/stubbed; use current eval/report outputs for troubleshooting today.
- "Can AgentOps create monitoring alerts right now?"
  - State `agentops monitor setup|dashboard|alert` are planned/stubbed; recommend run/report artifact checks for current triage.
- "What should I do after a sudden quality drop?"
  - Run `agentops eval run`, regenerate with `agentops report`, inspect threshold and metric changes, then define follow-up checks.
