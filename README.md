<h1 align="center">AgentOps Toolkit</h1>

<p align="center">
Answer the release question for Microsoft Foundry agents: can we ship it, and where is the proof?
</p>

<p align="center">
<a href="https://pypi.org/project/agentops-toolkit/"><img alt="PyPI" src="https://img.shields.io/pypi/v/agentops-toolkit.svg?label=PyPI&color=blue"/></a>
<a href="https://marketplace.visualstudio.com/items?itemName=AgentOpsToolkit.agentops-toolkit"><img alt="VS Code Extension" src="https://img.shields.io/badge/VS%20Code-Extension-007ACC.svg?logo=visualstudiocode"/></a>
<a href="https://github.com/Azure/agentops/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Azure/agentops/actions/workflows/ci.yml/badge.svg?branch=develop"/></a>
<a href="https://github.com/Azure/agentops/actions/workflows/release.yml"><img alt="Release" src="https://github.com/Azure/agentops/actions/workflows/release.yml/badge.svg"/></a>
<a href="https://github.com/Azure/agentops"><img alt="Status: Preview" src="https://img.shields.io/badge/Status-Preview-orange.svg"/></a>
<br/>
<a href="https://www.python.org/downloads/"><img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB.svg"/></a>
<a href="https://typer.tiangolo.com/"><img alt="CLI: Typer" src="https://img.shields.io/badge/CLI-Typer-5A67D8.svg"/></a>
<a href="https://learn.microsoft.com/azure/ai-foundry/"><img alt="Built on Microsoft Foundry" src="https://img.shields.io/badge/Built%20on-Microsoft%20Foundry-0078D4.svg"/></a>
<a href="https://github.com/Azure/agentops/blob/main/LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-green.svg"/></a>
</p>

## Overview

AgentOps Toolkit helps teams turn Foundry agent work into a clear release
decision. Foundry runs the agent. AgentOps proves the release is ready with
repeatable eval gates, Doctor readiness, release evidence, and trace-driven
regression loops.

The project enables:

- Local and CI execution for release gates
- Foundry prompt agent, Foundry hosted endpoint, HTTP/JSON agent, and raw model targets
- Auto-selected evaluators for RAG, tools, and model quality
- Stable `results.json` for automation
- PR-friendly `report.md`
- Baseline comparison for regression detection
- Doctor checks for repo, CI/CD, telemetry, landing zones, and Foundry setup
- Release evidence packs for promotion review
- Trace promotion into regression datasets
- Cockpit navigation for AgentOps, Foundry, and Azure Monitor

## AgentOps and Microsoft Foundry

Use Foundry to create, deploy, run, observe, and investigate agents. Use
AgentOps when the repo needs a source-controlled gate and evidence that the
candidate is ready to release.

| Surface | Official Foundry / Azure tooling | AgentOps role |
|---|---|---|
| Agent creation and deployment | Foundry portal, Foundry SDK/Toolkit, `microsoft-foundry` skill, azd | Reference the released candidate and gate it; do not replace lifecycle tooling |
| Evaluations | Foundry Evaluations and official CI actions/extensions | Repo config, thresholds, local/fallback runs, reports, baselines |
| Observability | Foundry Monitor, Traces, Azure Monitor, App Insights | Cockpit links, telemetry readiness, Doctor findings |
| Release decision | Branch protection, environments, approvals | `evidence.json` / `evidence.md` for promotion review |
| Improvement loop | Production traces and Foundry datasets | Review-first trace-to-dataset promotion |

The design goal is simple: **Foundry runs the agent. AgentOps proves the
release is ready.**

Core outputs:

- `results.json` (machine-readable)
- `report.md` (human-readable)
- `evidence.json` / `evidence.md` (from `agentops doctor --evidence-pack`)

Exit code contract:

- `0` execution succeeded and all thresholds passed
- `2` execution succeeded but one or more thresholds failed
- `1` runtime or configuration error

## Quickstart

### 1) Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install --upgrade "agentops-toolkit[foundry] @ git+https://github.com/Azure/agentops.git@main"
```

This installs the current AgentOps source from GitHub. After the next package
release, you can switch the install line back to `agentops-toolkit[foundry]`
from PyPI.

### 2) Bootstrap

```powershell
agentops init
```

This writes a single `agentops.yaml` at the project root and an
AgentOps-managed workspace under `.agentops/` for seed data, run history, and
generated evidence. It is not a second `.foundry/` project directory.

### 3) Configure your agent

Pick one of these forms for the `agent:` field - AgentOps classifies the target automatically:

```yaml
agent: "my-rag:3"                          # Foundry prompt agent (name:version)
agent: "https://...services.ai.azure.com/.../agents/<id>"  # Foundry hosted endpoint
agent: "https://api.example.com/chat"      # any HTTP/JSON agent (ACA, AKS, custom)
agent: "model:gpt-4o"                       # raw Foundry model deployment
```

AgentOps supports both Foundry Prompt Agents and Hosted Agents as evaluation
and readiness targets. Create and deploy them with Foundry tools, then reference
the published candidate in `agentops.yaml`.

For the smoke dataset, create a Foundry prompt agent such as
`agentops-smoke` and publish it with instructions that copy exact-answer
requests verbatim:

```text
If the user message starts with "Answer with exactly this sentence:",
copy only the sentence after that prefix. Do not add greetings,
markdown, citations, caveats, or explanations.
```

Evaluators come from dataset shape: `context` triggers RAG checks;
`tool_calls` / `tool_definitions` trigger tool-use checks. Minimal config:

```yaml
version: 1
agent: "agentops-smoke:2"  # Foundry saves the first published version as v2
dataset: .agentops/data/smoke.jsonl
```

### 4) Run

```powershell
az login
$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://<resource>.services.ai.azure.com/api/projects/<project>"
$env:AZURE_OPENAI_ENDPOINT = "https://<openai-resource>.openai.azure.com"
$env:AZURE_OPENAI_DEPLOYMENT = "gpt-4o-mini"
agentops eval analyze
agentops eval run
agentops doctor --evidence-pack
```

For Foundry targets, use either `project_endpoint:` in `agentops.yaml` or
`AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`. Config wins when both are set.

Outputs land in `.agentops/results/latest/`:

- `results.json` - machine-readable (versioned, stable schema)
- `report.md` - human-readable, PR-friendly

Release evidence lands in `.agentops/release/latest/`:

- `evidence.json` - machine-readable production-readiness projection
- `evidence.md` - PR/release summary

Capture the first successful run as a baseline:

```powershell
New-Item -ItemType Directory -Force .agentops\baseline | Out-Null
Copy-Item .agentops\results\latest\results.json .agentops\baseline\results.json
```

To see a visible comparison, publish a new agent version with a prompt
that paraphrases instead of copying exact-answer requests, update
`agentops.yaml` to that new `name:version`, and compare against the
baseline:

```powershell
agentops eval run --baseline .agentops/baseline/results.json
```

The report grows a `Comparison vs Baseline` section with per-metric deltas.

---

## Commands

Install optional extras as needed: `[foundry]` for eval runtime, `[agent]` for
Doctor/Cockpit, and `[mcp]` for MCP.

- `agentops --version` - show installed version.
- `agentops init` - bootstrap config and seed data.
- `agentops eval analyze` - check eval readiness.
- `agentops eval run [--baseline PATH]` - run an evaluation.
- `agentops eval promote-traces --source FILE [--apply]` - promote traces.
- `agentops report generate` - regenerate `report.md`.
- `agentops workflow analyze` - recommend CI/CD shape.
- `agentops workflow generate` - generate CI/CD workflows.
- `agentops skills install` - install Copilot or Claude skills.
- `agentops mcp serve` - start the MCP server.
- `agentops doctor [--evidence-pack]` - run readiness checks.
- `agentops cockpit` - open the local Cockpit.
- `agentops agent serve` - serve Doctor as a Copilot Extension.

## AgentOps Cockpit

`agentops cockpit` opens a localhost command center for the current workspace.
It combines eval history, Doctor findings, workflow status, and links to the
matching Foundry and Azure Monitor views.

Cockpit sections, in display order:

- **Foundry connection** - project, tenant, agent, App Insights.
- **Foundry launchpad** - links for the agent, project, and telemetry.
- **Observability readiness** - tracing, evals, red team, alerts.
- **AgentOps Doctor** - latest Doctor findings.
- **Eval gate summary** - local and CI gate history.
- **Quality gate summary** - score trends and regressions.
- **Production signal** - App Insights health snapshot.
- **CI/CD Pipelines** - GitHub Actions status.
- **Next actions** - contextual recommendations.

## Documentation

- [Prompt Agent quickstart](docs/tutorial-prompt-agent-quickstart.md) - use this when the Foundry target is `agent: name:version`.
- [Hosted Agent quickstart](docs/tutorial-hosted-agent-quickstart.md) - use this when the target is a Foundry hosted or HTTP endpoint URL.
- [End-to-end workshop](docs/tutorial-end-to-end.md) - complete Foundry + AgentOps journey: create, debug, evaluate, release, observe, red-team follow-through, and trace regression.
- [Core concepts](docs/concepts.md)
- [How it works](docs/how-it-works.md)
- [Doctor explained](docs/doctor-explained.md)
- [CI/CD with GitHub Actions](docs/ci-github-actions.md)
- [Built-in evaluator reference](docs/foundry-evaluation-sdk-built-in-evaluators.md)
- [Release process](docs/release-process.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for architecture rules, testing, and contribution flow.
