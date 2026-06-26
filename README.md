<h1 align="center">AgentOps Accelerator</h1>

<p align="center">
<b>Evaluate. Ship. Observe. Own.</b>
<br/>
Continuous evaluation, safety testing, observability, and release readiness for Microsoft Foundry agents.
</p>

<p align="center">
<a href="https://aka.ms/agentops-accelerator"><b>Documentation</b></a> |
<a href="https://pypi.org/project/agentops-accelerator/">PyPI</a> |
<a href="https://marketplace.visualstudio.com/items?itemName=AgentOpsAccelerator.agentops-accelerator">VS Code Extension</a> |
<a href="https://github.com/Azure/agentops/releases/latest">Latest release</a>
</p>

<p align="center">
<a href="https://pypi.org/project/agentops-accelerator/"><img alt="PyPI" src="https://img.shields.io/pypi/v/agentops-accelerator.svg?label=PyPI&color=blue"/></a>
<a href="https://marketplace.visualstudio.com/items?itemName=AgentOpsAccelerator.agentops-accelerator"><img alt="VS Code Extension" src="https://img.shields.io/badge/VS%20Code-Extension-007ACC.svg?logo=visualstudiocode"/></a>
<a href="https://github.com/Azure/agentops/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Azure/agentops/actions/workflows/ci.yml/badge.svg?branch=develop"/></a>
<a href="https://github.com/Azure/agentops/actions/workflows/release.yml"><img alt="Release" src="https://github.com/Azure/agentops/actions/workflows/release.yml/badge.svg"/></a>
<a href="https://github.com/Azure/agentops/blob/main/LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-green.svg"/></a>
</p>

AgentOps Accelerator helps Microsoft Foundry agent teams evaluate quality, prepare releases, monitor behavior, and stay accountable after launch. It gives you a practical starting point for agent operations, with Foundry integration as the default path and deeper setup guidance in the full docs.

## Get started

```powershell
python -m pip install agentops-accelerator
agentops init
```

`agentops init` starts a guided setup that creates your `agentops.yaml` and
`.agentops/` workspace.

Next, follow the tutorial that matches your agent type:

- [Prompt Agent tutorial](https://azure.github.io/agentops/tutorial-prompt-agent/)
- [Hosted or HTTP Agent tutorial](https://azure.github.io/agentops/tutorial-hosted-agent/)

## What it helps you do

Use AgentOps Accelerator when you need to:

- Evaluate an agent before release
- Compare changes across versions
- Capture release evidence
- Monitor agent quality and regressions
- Give teams a repeatable way to own agent behavior in production

The accelerator keeps the local workflow simple, then points you to the full
docs when you are ready to configure pipelines, dashboards, and release
practices.

## Learn more

For setup guides, tutorials, architecture, CI/CD guidance, Doctor checks, and
evaluator reference, start with the documentation site:

<p align="center">
<a href="https://aka.ms/agentops-accelerator"><b>https://aka.ms/agentops-accelerator</b></a>
</p>

## Run a first evaluation

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

Install optional extras as needed: `[agent]` for Doctor/Cockpit and `[mcp]` for MCP.

- `agentops --version` - show installed version.
- `agentops init` - bootstrap config and seed data.
- `agentops eval analyze` - check eval readiness.
- `agentops eval init` - bootstrap an azd `eval.yaml` recipe and wire `execution: azd`.
- `agentops eval run [--baseline PATH]` - run an evaluation.
- `agentops eval promote-traces --source FILE [--apply]` - promote local trace export files.
- `agentops telemetry validate NAME` - validate an Azure Monitor or Application Insights import.
- `agentops telemetry preview NAME --rows N` - preview telemetry import rows.
- `agentops telemetry import NAME --apply` - write the imported telemetry dataset.
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

- [Foundry Prompt Agent tutorial](docs/tutorial-prompt-agent.md) - use this when the Foundry target is `agent: name:version`. Walks the sandbox to dev journey with a PR gate.
- [Hosted or HTTP Agent tutorial](docs/tutorial-hosted-agent-quickstart.md) - use this when the target is a Foundry hosted or HTTP endpoint URL. Same sandbox to dev journey for endpoint-based agents.
- [End-to-end tutorial](docs/tutorial-end-to-end.md) - extends either of the above with the full sandbox to dev to qa to prod promotion, Foundry red-team scans, and trace-to-regression promotion.
- [Evaluation paths](docs/evaluation.md) - choose static dataset, grey-box HTTP, or telemetry/trace import.
- [Core concepts](docs/concepts.md)
- [How it works](docs/how-it-works.md)
- [Doctor explained](docs/doctor-explained.md)
- [CI/CD with GitHub Actions](docs/ci-github-actions.md)
- [Built-in evaluator reference](docs/foundry-evaluation-sdk-built-in-evaluators.md)
- [Release process](docs/release-process.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development, testing, and contribution guidance.