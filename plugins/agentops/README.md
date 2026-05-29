# AgentOps Skills for Coding Agents

This extension packages the same AgentOps skills that the CLI installs
with `agentops skills install`. Skills are **instructions for a coding
agent** such as GitHub Copilot, Copilot CLI, Cursor, or Claude Code. They
help the coding agent create config, datasets, reports, and workflows in
your repository.

They are different from the **AgentOps Watchdog runtime agent**:

- **Skills** live in `.github/skills/` or `.claude/commands/` and guide a
  coding assistant.
- **Watchdog** is the runtime CLI/server behind `agentops doctor`
  and `agentops agent serve`; it reads real eval history, Azure Monitor
  telemetry, and Foundry metadata.
- The `agentops-agent` skill is only the coding-agent front door to that
  Watchdog runtime. It does not fabricate findings.

## Implemented skills

| Skill | What it does |
|---|---|
| **agentops-config** | Inspect the workspace and generate or update flat `agentops.yaml`. |
| **agentops-dataset** | Generate realistic JSONL evaluation rows grounded in the app. |
| **agentops-eval** | Run `agentops eval run`, handle exit codes, and compare with `--baseline`. |
| **agentops-report** | Explain `results.json` / `report.md` and suggest concrete next actions. |
| **agentops-workflow** | Generate the supported GitHub Actions CI/CD scaffold and explain required GitHub/Azure wiring. |
| **agentops-agent** | Run and interpret the Watchdog runtime (`agentops doctor` / `serve`). |

There are no shipped `agentops-monitor`, `agentops-trace`, or
`agentops-regression` skills in the current implementation.

## Installation options

### Option 1: VS Code extension

Install from the
[VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=AgentOpsAccelerator.agentops-accelerator)
or search **AgentOps Skills** in the VS Code Extensions view.

Use this when you want Copilot in VS Code to discover the packaged
skills from the extension/plugin.

### Option 2: CLI install into a repository

Run this from the repository where you want skills checked in:

```bash
python -m pip install "agentops-accelerator[foundry] @ git+https://github.com/Azure/agentops.git@main"
agentops skills install --platform copilot --force
```

This writes:

```text
.github/copilot-instructions.md
.github/skills/agentops-config/SKILL.md
.github/skills/agentops-dataset/SKILL.md
.github/skills/agentops-eval/SKILL.md
.github/skills/agentops-report/SKILL.md
.github/skills/agentops-workflow/SKILL.md
.github/skills/agentops-agent/SKILL.md
```

Use `--platform claude` for `.claude/commands/*.md`, or omit
`--platform` and let AgentOps auto-detect the coding agent setup.

## Usage

Open Copilot Chat or your coding-agent CLI in the project and ask for the
workflow you need:

```text
Set up AgentOps evaluation for this app.
Generate an evaluation dataset for the support-agent tools.
Run the eval and explain the failing rows.
Generate the GitHub Actions AgentOps workflow and tell me what Azure/GitHub variables it needs.
Run the AgentOps watchdog and summarize production latency findings.
```

## Links

- [AgentOps Accelerator](https://github.com/Azure/agentops)
- [Foundry Prompt Agent tutorial](https://github.com/Azure/agentops/blob/main/docs/tutorial-prompt-agent-quickstart.md)
- [Hosted or HTTP Agent tutorial](https://github.com/Azure/agentops/blob/main/docs/tutorial-hosted-agent-quickstart.md)
- [End-to-end tutorial](https://github.com/Azure/agentops/blob/main/docs/tutorial-end-to-end.md)
- [How it works](https://github.com/Azure/agentops/blob/main/docs/how-it-works.md)
