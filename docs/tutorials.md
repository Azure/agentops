---
hide:
  - toc
---

# Tutorials

Each tutorial is a hands-on, end-to-end walkthrough of the full AgentOps loop,
evaluate, ship, observe, and operate, on one kind of agent. Pick the one that
matches how your agent runs. They all teach the same sandbox to PR gate story,
so once you finish one, the others will feel familiar.

Not sure which fits? If your agent is authored and hosted in Foundry, start with
the prompt agent tutorial. If your agent runs as an HTTP service behind a URL,
start with the HTTP agent tutorial.

<div class="agentops-cards" markdown>

<div class="agentops-card" markdown>
### :material-robot-happy: Prompt agent tutorial
For a **Foundry-managed prompt agent** referenced as `name:version`. Build a
small Travel Agent in Foundry, add a PR gate that catches regressions before
merge, deploy to dev, collect Doctor evidence, and read it in the Cockpit.

[Start the prompt agent tutorial :material-arrow-right:](tutorial-prompt-agent.md){ .md-button--pill }
</div>

<div class="agentops-card" markdown>
### :material-web: HTTP agent tutorial
For an agent that runs as an **HTTP service behind a URL**. The worked example is
a RAG orchestrator (a FastAPI service in an Azure Container App). Deploy it, take
ownership of the repo, and add a PR gate that evaluates the live endpoint.

[Start the HTTP agent tutorial :material-arrow-right:](tutorial-http-agent.md){ .md-button--pill }
</div>

</div>

More tutorials are on the way. If there is a scenario you want covered, open an
issue in the [repository](https://github.com/Azure/agentops).
