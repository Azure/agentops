<h1 align="center">AgentOps Accelerator</h1>

<p align="center">
<b>Open-source framework and CLI for continuous evaluation, safety testing, and release readiness of Microsoft Foundry agents.</b>
<br/>
Can we ship it, and where is the proof?
</p>

<p align="center">
<a href="https://pypi.org/project/agentops-accelerator/"><img alt="PyPI" src="https://img.shields.io/pypi/v/agentops-accelerator.svg?label=PyPI&color=blue"/></a>
<a href="https://marketplace.visualstudio.com/items?itemName=AgentOpsAccelerator.agentops-accelerator"><img alt="VS Code Extension" src="https://img.shields.io/badge/VS%20Code-Extension-007ACC.svg?logo=visualstudiocode"/></a>
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

**AgentOps Accelerator is an open-source framework and CLI that standardizes
continuous evaluation, safety testing, and release readiness for enterprise AI
agents — with Microsoft Foundry as the agent runtime.**

It is an *orchestrator*, not a reimplementation. AgentOps wires together the
tools you already use — Foundry Evaluations, `azd ai agent eval`, the
open-source ASSERT framework, the PyRIT-backed AI Red Teaming agent, Azure
Monitor / Application Insights, and your CI/CD platform — into a single
repeatable release loop:

1. **Evaluate** the agent against datasets, rubrics, and policies — locally or
   in the cloud — using auto-selected evaluators for RAG, tool use, model
   quality, and safety.
2. **Probe** the agent with adversarial inputs by orchestrating ASSERT
   (`agentops assert run`) and the Foundry/PyRIT Red Teaming agent
   (`agentops redteam run`) as active CI steps.
3. **Diagnose** repo, telemetry, landing zone, and Foundry readiness with
   `agentops doctor`.
4. **Gate** the release with a deterministic exit-code contract that PRs and
   pipelines can rely on.
5. **Prove** the release with a stable evidence pack (`evidence.json` +
   `evidence.md`) that bundles eval results, ASSERT verdicts, red-team
   findings, telemetry readiness, and Doctor findings for promotion review.
6. **Learn from production** by promoting reviewed traces into regression
   datasets that feed the next eval cycle.

The output is a clear answer to two questions reviewers actually ask:
**can we ship it, and where is the proof?**

### Core outputs

| Artifact | Produced by | Audience |
|---|---|---|
| `results.json` | `agentops eval run` | CI / automation |
| `report.md` | `agentops eval run` | PR reviewers |
| `.agentops/assert/latest.json` | `agentops assert run` | Evidence pack, CI gate |
| `.agentops/redteam/latest.json` | `agentops redteam run` | Evidence pack, CI gate |
| `evidence.json` / `evidence.md` | `agentops doctor --evidence-pack` | Release approver |
| Cockpit (localhost) | `agentops cockpit` | Engineer reviewing readiness |

### Exit-code contract

- `0` — execution succeeded and all gates passed
- `2` — execution succeeded but a threshold, ASSERT violation, red-team rate,
  or Doctor severity gate failed
- `1` — runtime or configuration error

## AgentOps and Microsoft Foundry

Foundry and AgentOps are designed to meet at the release boundary. Foundry is
where teams create, deploy, run, observe, and investigate agents. AgentOps is
the repo-side operating layer that turns those signals into a repeatable
ship/no-ship workflow.

| Moment | Foundry / Azure does | AgentOps adds |
|---|---|---|
| Build and version | Foundry portal, Foundry SDK/Toolkit, `microsoft-foundry` skill, azd | Pins the exact candidate in `agentops.yaml` and generates the PR/release gate around it |
| Evaluate and compare | Foundry Evaluations, `azd ai agent eval`, Rubric evaluator, and official CI actions/extensions | Keeps datasets and thresholds in the repo, records evidence, normalizes azd/Rubric outputs, and provides local/fallback runs for non-prompt targets |
| Probe safety | ASSERT framework, PyRIT-backed AI Red Teaming agent | Runs both as active CI steps via `agentops assert run` and `agentops redteam run`, normalizes verdicts, and gates the pipeline |
| Observe and investigate | Foundry Monitor, Traces, Azure Monitor, App Insights | Surfaces deep links, telemetry readiness, Doctor findings, and Cockpit navigation |
| Decide release | Branch protection, environments, approvals | Packages `evidence.json` / `evidence.md` for promotion review |
| Govern controls | ACS, Foundry Guardrails | References reviewed artifacts by path/hash/status without executing or applying the external controls |
| Improve from production | Production traces and Foundry datasets | Promotes reviewed trace learnings into regression candidates |

The rhythm is simple: build and operate the agent in Foundry, keep the release
contract in the repo, and let AgentOps connect the two into a clean review loop.

## Quickstart

### 1) Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install --upgrade "agentops-accelerator[foundry] @ git+https://github.com/Azure/agentops.git@main"
```

This installs the current AgentOps source from GitHub. After the next package
release, you can switch the install line back to `agentops-accelerator[foundry]`
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
- `agentops eval init` - bootstrap an azd `eval.yaml` recipe and wire `execution: azd`.
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

- [Foundry Prompt Agent tutorial](docs/tutorial-prompt-agent-quickstart.md) - use this when the Foundry target is `agent: name:version`. Walks the sandbox → dev journey with a PR gate.
- [Hosted or HTTP Agent tutorial](docs/tutorial-hosted-agent-quickstart.md) - use this when the target is a Foundry hosted or HTTP endpoint URL. Same sandbox → dev journey for endpoint-based agents.
- [End-to-end tutorial](docs/tutorial-end-to-end.md) - extends either of the above with the full sandbox → dev → qa → prod promotion, Foundry red-team scans, and trace-to-regression promotion.
- [Core concepts](docs/concepts.md)
- [How it works](docs/how-it-works.md)
- [Doctor explained](docs/doctor-explained.md)
- [CI/CD with GitHub Actions](docs/ci-github-actions.md)
- [Built-in evaluator reference](docs/foundry-evaluation-sdk-built-in-evaluators.md)
- [Release process](docs/release-process.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for architecture rules, testing, and contribution flow.
