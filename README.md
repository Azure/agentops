<h1 align="center">AgentOps Accelerator</h1>

<p align="center">
<b>Evaluate. Ship. Observe. Operate.</b>
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

AgentOps Accelerator helps Microsoft Foundry agent teams evaluate quality, prepare releases, monitor behavior, and operate reliably after launch. It gives you a practical starting point for agent operations, with Foundry integration as the default path and deeper setup guidance in the full docs.

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
- Give teams a repeatable way to operate agents responsibly in production

The accelerator keeps the local workflow simple, then points you to the full
docs when you are ready to configure pipelines, dashboards, and release
practices.

## Learn more

For setup guides, tutorials, architecture, CI/CD guidance, Doctor checks, and
evaluator reference, see:

<p align="center">
<a href="https://aka.ms/agentops-accelerator"><b>https://aka.ms/agentops-accelerator</b></a>
</p>

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development, testing, and contribution guidance.