---
hide:
  - navigation
  - toc
---

<div class="agentops-banner" markdown>
<div class="agentops-banner-inner" markdown>

<div class="agentops-banner-head" markdown>
<img class="agentops-banner-logo" src="media/logo.png" alt="AgentOps" />
# AgentOps Accelerator
</div>

<p class="agentops-banner-tagline">The open-source AgentOps jumpstart for continuous evaluation, safety testing, observability, and release readiness of Microsoft Foundry agents.</p>

<p class="agentops-banner-question">Evaluate. Ship. Observe. Own.</p>

<div class="agentops-banner-actions" markdown>
[Latest Release {{ latest_release("Azure/agentops") }} :material-tag:](https://github.com/Azure/agentops/releases/latest){ .md-button--pill }
[PyPI :material-language-python:](https://pypi.org/project/agentops-accelerator/){ .md-button--pill }
[VS Code Extension :material-microsoft-visual-studio-code:](https://marketplace.visualstudio.com/items?itemName=AgentOpsAccelerator.agentops-accelerator){ .md-button--pill }
{% set rc_tag = latest_release_candidate("Azure/agentops") %}{% if rc_tag %}
[Pre-release {{ rc_tag }} :material-tag:]({{ latest_release_candidate_url("Azure/agentops") }}){ data-md-color-accent="orange" .md-button--pill .md-button--pill--rc }
{% endif %}
</div>

</div>
</div>

## What AgentOps does

AgentOps turns Foundry evaluation, safety, and observability signals into a
repeatable ship/no-ship workflow. It connects Foundry Evaluations, the ASSERT
safety framework, the PyRIT-backed AI Red Teaming agent, Azure Monitor, and your
CI/CD platform into one release loop, packaging every result into a stable
evidence pack that proves a release is ready for production.

Foundry runs the agent. AgentOps proves readiness with eval gates, Doctor checks,
generated CI/CD, release evidence, and trace-driven regression loops.

<div class="agentops-video-embed">
  <iframe
    src="https://www.youtube-nocookie.com/embed/-uYMYzdKCZ4?vq=hd1080&hd=1"
    title="AgentOps Accelerator overview"
    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
    allowfullscreen>
  </iframe>
</div>

<div class="agentops-cards" markdown>

<div class="agentops-card" markdown>
### :material-rocket-launch: Get started
Start with the [Prompt Agent tutorial](tutorial-prompt-agent.md) to learn the
sandbox -> dev PR gate flow.
</div>

<div class="agentops-card" markdown>
### :material-cog: How it works
Read [How It Works](how-it-works.md) for the architecture and the principles
behind the CLI, Cockpit, and skills.
</div>

<div class="agentops-card" markdown>
### :material-stethoscope: Doctor
See the [Doctor checks](doctor-checks.md) that score release readiness and
produce an evidence pack.
</div>

<div class="agentops-card" markdown>
### :material-source-branch: CI/CD
Generate gated [GitHub Actions](ci-github-actions.md) workflows that run evals,
safety, and red teaming on every PR.
</div>

</div>

## Contributing

Contributions are welcome. See the project
[repository](https://github.com/Azure/agentops) for guidelines, issues, and the
contribution process.
