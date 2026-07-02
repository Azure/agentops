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

<p class="agentops-banner-question">Evaluate. Ship. Observe. Operate.</p>

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
repeatable ship or no-ship workflow. It connects Foundry Evaluations, the ASSERT
safety framework, the PyRIT-backed AI Red Teaming agent, Azure Monitor, and your
CI/CD platform into one release loop. Every result is packaged into a stable
evidence pack that proves a release is ready for production.

!!! tip "Install in 60 seconds"
    Install the package, bootstrap a workspace, and drop the skills into your
    coding agent.

    ```bash
    pip install agentops-accelerator
    agentops init
    agentops skills install --platform copilot
    ```

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
### :material-clipboard-check: Evaluate
Read [Evaluation](evaluation.md) to learn how datasets, evaluators, thresholds,
and rubrics turn an agent into a pass or fail gate. Start with `agentops eval run`
and the `agentops-eval` skill.
</div>

<div class="agentops-card" markdown>
### :material-source-branch: Ship
[Ship](ship.md) explains the generated PR gate and dev deploy workflows, and how
candidate versions become a release. Start with
`agentops workflow generate --kinds pr` and the `agentops-workflow` skill.
</div>

<div class="agentops-card" markdown>
### :material-radar: Observe
[Observe](observe.md) covers Foundry traces and Azure Monitor, and how
production signals feed continuous evaluation. Start with
`agentops telemetry validate` and the `agentops-agent` skill.
</div>

<div class="agentops-card" markdown>
### :material-stethoscope: Operate
[Operate](operate.md) shows how Doctor scores readiness and packages an evidence
pack so you can make the ship or no-ship call. Start with `agentops doctor` and
the `agentops-governance` skill.
</div>

</div>

## Reference architecture

Use this as the mental model for the AgentOps loop: build in a sandbox, commit
the release contract to source control, promote through environments with
evidence, then feed production learning back into the next evaluation set.

![AgentOps Accelerator reference architecture](media/agentops-architecture.png){ .agentops-reference-architecture }

## Where to go next

<div class="agentops-cta" markdown>
Pick a tutorial to learn the sandbox to PR gate flow end to end, or jump straight
to the evaluation reference.

[Prompt Agent tutorial :material-rocket-launch:](tutorial-prompt-agent.md){ .md-button--pill }
[HTTP Agent tutorial :material-rocket-launch:](tutorial-http-agent.md){ .md-button--pill }
[Evaluation reference :material-book-open-variant:](evaluation.md){ .md-button--pill }
</div>

Contributions are welcome. See the
[repository](https://github.com/Azure/agentops) for guidelines, issues, and the
contribution process.
