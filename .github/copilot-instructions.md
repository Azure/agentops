# Copilot Instructions for AgentOps

AgentOps Toolkit is a Python 3.11+ CLI, local Cockpit, and coding-agent skill
set that helps teams answer two release questions for Microsoft Foundry agents:
can we ship it, and where is the proof? Foundry runs the agent. AgentOps proves
the release is ready with eval gates, Doctor readiness, CI/CD, release evidence,
and trace-driven regression loops.

Read these first for detailed architecture and product context:

- `AGENTS.md`
- `docs/how-it-works.md`
- `CONTRIBUTING.md`
- `README.md`

## Current public CLI surface

- `agentops --version`
- `agentops explain [COMMAND...] [--no-pager] [--format text|markdown|html] [--out PATH] [--open]`
- `agentops init [--force] [--dir PATH] [--no-prompt] [--no-appinsights] [--azd-env NAME] [--project-endpoint URL] [--agent REF] [--dataset PATH] [--appinsights-connection-string STR]`
- `agentops init show [--dir PATH] [--reveal-secrets]`
- `agentops eval analyze [--dir PATH] [--format text|markdown|json] [--out PATH]`
- `agentops eval run [--config PATH] [--baseline PATH] [--output DIR]`
- `agentops eval promote-traces --source PATH [--out PATH] [--max-rows N] [--label-mode self-similarity|pending] [--apply]`
- `agentops report generate [--in PATH] [--out PATH]`
- `agentops workflow analyze [--dir PATH] [--format text|markdown|json] [--out PATH]`
- `agentops workflow generate [--force] [--dir PATH] [--kinds pr,dev,qa,prod,watchdog] [--platform github|azure-devops] [--deploy-mode auto|placeholder|azd|prompt-agent] [--doctor-gate critical|warning|none]`
- `agentops skills install [--platform copilot|claude] [--from SOURCE] [--prompt] [--force] [--dir PATH]`
- `agentops mcp serve`
- `agentops doctor [--workspace PATH] [--config PATH] [--out PATH] [--lookback-days N] [--severity-fail SEVERITY] [--evidence-pack] [--evidence-out PATH]`
- `agentops doctor explain [--no-pager] [--format text|markdown|html] [--out PATH] [--open]`
- `agentops cockpit [--host HOST] [--port PORT] [--workspace PATH] [--no-preflight]`
- `agentops agent serve [--host HOST] [--port PORT] [--config PATH] [--no-verify] [--workers N]`

Do not add commands or flags unless the product discussion explicitly calls for
them. Keep `--help` terse and put long-form manual content under `explain`.

## Architecture rules

- Keep `src/agentops/cli/app.py` thin: parse arguments, print concise output,
  and delegate to `pipeline/` or `services/`.
- Keep `src/agentops/core/` pure: no Azure SDK imports, no network calls.
- Use `pathlib.Path` for paths.
- Prefer small focused functions and explicit user-friendly errors.
- Azure SDK imports must be lazy inside runtime functions.
- No import-time side effects beyond the existing dotenv loader behavior in the
  CLI entry point.

Where to put common changes:

| Change | Location |
|---|---|
| `agentops.yaml` schema | `src/agentops/core/agentops_config.py` |
| `results.json` schema | `src/agentops/core/results.py` |
| Release evidence schema | `src/agentops/core/release_evidence.py` |
| Eval execution | `src/agentops/pipeline/` |
| Doctor checks | `src/agentops/agent/checks/` |
| Release evidence writer | `src/agentops/services/evidence_pack.py` |
| Trace export to dataset candidates | `src/agentops/services/trace_promotion.py` |
| Workflow generation | `src/agentops/services/cicd.py` and `src/agentops/templates/` |

## Product principles

- Use Foundry for hosted agents, cloud evaluations, traces, monitoring,
  red teaming, datasets, and operations.
- Use AgentOps for repo-controlled release readiness: eval config, CI gates,
  normalized artifacts, Doctor diagnostics, release evidence,
  trace-to-dataset promotion, and Cockpit links to Foundry/Azure Monitor.
- `agentops doctor --evidence-pack` is a readiness projection over existing
  signals; do not create a second exit-code contract.
- Trace promotion is review-first. `self-similarity` labels support drift
  detection, not human-verified correctness.
- Doctor and Cockpit stay read-only; data promotion belongs in services/CLI.

## Exit codes

- `0` = execution succeeded and all thresholds passed
- `2` = execution succeeded but one or more thresholds or configured finding
  gates failed
- `1` = runtime or configuration error

Do not reinterpret these codes.

## Testing

Use existing tests only. The standard full-suite command is:

```bash
python -m pytest tests/ -x -q
```

Add focused unit tests for new schema, services, Doctor checks, CLI flags, and
workflow template behavior. Azure SDK calls must be mocked or avoided in tests.
