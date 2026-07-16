---
hide:
  - toc
---

# Tutorials

Each tutorial is a hands-on, end-to-end walkthrough of the full AgentOps loop,
evaluate, ship, observe, and operate, on one kind of agent. Pick the one that
matches how your agent runs. They all teach the same sandbox to PR gate story,
so once you finish one, the others will feel familiar.

Not sure which fits? If your agent is authored and hosted in Foundry as a
prompt referenced by `name:version`, start with the prompt agent tutorial. If
Foundry runs your agent code as a hosted runtime behind an endpoint, use the
hosted agent tutorial. If your agent runs as an HTTP service you operate behind
a URL, use the HTTP agent tutorial.

<div class="agentops-cards" markdown>

<div class="agentops-card" markdown>
### :material-robot-happy: Prompt agent tutorial
For a **Foundry-managed prompt agent** referenced as `name:version`. Build a small
Travel Agent, add a PR gate, and read the evidence in the Cockpit.

[Start the prompt agent tutorial :material-arrow-right:](tutorial-prompt-agent.md){ .md-button--pill }
</div>

<div class="agentops-card" markdown>
### :material-cloud-check: Hosted agent tutorial
For a **Foundry Hosted Agent** that Foundry runs for you. Deploy it, read the
server-side `invoke_agent` traces, and gate the deployed endpoint.

[Start the hosted agent tutorial :material-arrow-right:](tutorial-hosted-agent.md){ .md-button--pill }
</div>

<div class="agentops-card" markdown>
### :material-web: HTTP agent tutorial
For an agent that runs as an **HTTP service behind a URL**. The example is a RAG
orchestrator (GPT-RAG). Deploy it, make the repo yours, and gate the live endpoint.

[Start the HTTP agent tutorial :material-arrow-right:](tutorial-http-agent.md){ .md-button--pill }
</div>

</div>

More tutorials are on the way. If there is a scenario you want covered, open an
issue in the [repository](https://github.com/Azure/agentops).
