# Quickstart: Foundry Prompt Agent

Use this quickstart when you want a Foundry-managed prompt agent referenced as
`name:version`. The example creates a small **Travel Agent** in Foundry and then
uses AgentOps to add repo-side readiness, CI gates, Doctor, release evidence,
and Cockpit.

This path validates the Foundry-native route:

- Foundry owns the prompt agent runtime and Microsoft Foundry AI Agent Evaluation.
- AgentOps owns repo-side readiness: `agentops.yaml`, CI gates, Doctor,
  release evidence, and Cockpit.

## Repository set used in this tutorial

This tutorial intentionally shows the broader Foundry ecosystem, not only
AgentOps. For the recorded workshop path, use Paulo's fork set so the CLI,
workflow runner, Toolkit reference, and skill guidance stay aligned in one
cohesive demo environment.

| Repository | Workshop fork | Role in the journey |
|---|---|---|
| `Azure/agentops` | `placerda/agentops` | Provides the workshop CLI, workflow generation, Doctor, Cockpit, and release evidence flow. |
| `microsoft/ai-agent-evals` | `placerda/ai-agent-evals` | Provides the Foundry-native PR evaluation gate used by the generated workflow. |
| `microsoft/foundry-toolkit` | `placerda/foundry-toolkit` | Frames the VS Code create/debug experience and the Operate handoff after a prompt version is ready. |
| `microsoft/azure-skills` | `placerda/azure-skills` | Connects Copilot guidance to Foundry observe, CI/CD, regression, and trace follow-through. |

## Journey you will exercise

| Step | Main tool | What you do | AgentOps role |
|---|---|---|---|
| Create the agent | Foundry portal, Foundry SDK, Foundry Toolkit, or `microsoft-foundry` skill | Create and publish `travel-agent`. | No ownership; AgentOps consumes the published target. |
| Try and debug | Foundry playground, VS Code, Copilot Chat | Validate behavior before adding release gates. | Optional quick eval later. |
| Evaluate in CI | Official Microsoft AI Agent Evaluation | Run Foundry-native evaluation for `travel-agent:<version>`. | Generates routing and records evidence. |
| Review readiness | AgentOps Doctor and Cockpit | Check CI, eval, telemetry, evidence, and links. | Primary owner of repo-side release proof. |

## 1. Create the Travel Agent in Foundry

Create a prompt agent first. AgentOps starts after the agent exists.

1. Open the [Azure AI Foundry portal](https://ai.azure.com) and select your
   project.
2. Go to the agents area and create a new prompt-based agent.
3. Use these values:

   | Field | Value |
   |---|---|
   | Name | `travel-agent` |
   | Model deployment | `gpt-4o-mini` or another chat-capable deployment in the project |
   | Description | Helps plan short trips and explains tradeoffs. |

4. Paste these instructions:

   ```text
   You are Travel Agent, a concise travel planning assistant.

   Help users plan short leisure trips. Always include:
   - a short summary;
   - a day-by-day plan when the user asks for an itinerary;
   - practical notes about budget, transit, weather, or booking constraints;
   - a reminder that you cannot make live reservations or purchases.

   Ask one clarifying question only when the destination, duration, or traveler
   preference is missing. Do not invent booking confirmations, prices, or
   availability.
   ```

5. Save and publish the agent.
6. Copy the published reference. The rest of this tutorial assumes
   `travel-agent:1`; if Foundry shows a different version, use that exact
   `name:version` value instead.

Test it in the Foundry playground with:

```text
Plan a 3-day first-time trip to Lisbon for a couple who likes food and history.
```

## 2. Create a clean workspace and install AgentOps

```powershell
mkdir agentops-prompt-quickstart
cd agentops-prompt-quickstart
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install "agentops-toolkit[foundry,agent]"
agentops --version
```

For normal usage, prefer the published package above. For the recorded workshop
path, install from Paulo's fork so the CLI, generated workflows, and tutorial
steps stay in sync:

```powershell
python -m pip install "agentops-toolkit[foundry,agent] @ git+https://github.com/placerda/agentops.git@develop"
```

## 3. Create the travel eval dataset

Create a small JSONL dataset that matches the Travel Agent behavior.

```powershell
New-Item -ItemType Directory -Force .agentops\data | Out-Null
@'
{"input":"Plan a 3-day first-time trip to Lisbon for a couple who likes food and history.","expected":"A concise 3-day Lisbon itinerary with food, history, neighborhoods such as Baixa, Alfama, and Belem, practical notes, and no claim to make live bookings."}
{"input":"Suggest a low-budget weekend in Seattle for a solo traveler who likes coffee and museums.","expected":"A practical weekend Seattle plan with low-budget choices, coffee and museum suggestions, transit or weather notes, and no claim to make live bookings."}
{"input":"I want to visit Tokyo for 5 days with two kids. What should we do?","expected":"A family-friendly 5-day Tokyo itinerary with kid-appropriate activities, transit and pacing notes, and no claim to make live bookings."}
'@ | Set-Content -Encoding utf8 .agentops\data\travel-smoke.jsonl
```

## 4. Sign in and capture Foundry values

```powershell
az login
```

You need:

| Value | Example |
|---|---|
| Foundry project endpoint | `https://<resource>.services.ai.azure.com/api/projects/<project>` |
| Prompt agent reference | `travel-agent:1` or the version Foundry published |
| Application Insights connection string | optional later, for observability |

You do not need to set an evaluator deployment before initialization.
`agentops init` collects the workspace values. The evaluator deployment is a
CI/local-eval setting you add later when a runner needs a judge model.

## 5. Initialize AgentOps interactively

```powershell
agentops init
```

Answer the prompts as the wizard asks them:

| Prompt | Answer |
|---|---|
| Foundry project endpoint | `https://<resource>.services.ai.azure.com/api/projects/<project>` |
| Agent | `travel-agent:1`, or the exact published version from Foundry |
| Dataset path | `.agentops/data/travel-smoke.jsonl` |

The wizard does not ask for App Insights. Later runtime commands such as eval,
Doctor, and Cockpit use the Foundry project endpoint to ask the Azure AI
Projects SDK for the App Insights resource attached to that Foundry project. If
discovery is unavailable and you want to force a value, run
`agentops init --appinsights-connection-string "<connection-string>"` or set
`APPLICATIONINSIGHTS_CONNECTION_STRING` manually in `.azure/dev/.env`.

If the first run shows starter defaults such as `Agent [my-agent:1]` or
`Dataset path [.agentops/data/smoke.jsonl]`, replace them with the Travel Agent
values above. Those defaults only come from the scaffolded starter file.

The interactive path is intentional: you see what each value means, and each
answer is saved as soon as it validates. If you want an azd environment name
other than the default `dev`, run `agentops init --azd-env <name>`.

This creates:

```text
agentops.yaml
.agentops/
.azure/dev/.env
.github/skills/
```

`agentops.yaml` should stay small:

```yaml
version: 1
agent: travel-agent:1
dataset: .agentops/data/travel-smoke.jsonl
```

The Foundry project endpoint lives in `.azure/dev/.env`, not in source control.
If you force an App Insights connection string later, it is saved there too.

## 6. Check the selected eval runner

```powershell
agentops workflow analyze --format text
```

For `agent: name:version`, AgentOps should recommend the Foundry eval runner:

```text
Recommendation
  deploy          prompt-agent
  evaluate        Microsoft Foundry AI Agent Evaluation
  workflow edits  not needed - generated workflow should work as-is
  Copilot skills  not needed - no Copilot handoff for this project shape
```

That means generated CI uses the Microsoft Foundry AI Agent Evaluation
action/task for the eval step, then uses AgentOps to collect evidence and
readiness signals.

## 7. Generate the PR gate

```powershell
agentops workflow generate --kinds pr,watchdog --force
```

At this point the workflow files exist only on your machine. CI will not run
until the folder is a GitHub repository, pushed, and connected to Azure with
OIDC.

Recommended path: let Copilot use the installed AgentOps workflow skill as the
guide, because this step crosses repo, GitHub, and Azure permissions.

Refresh the Copilot skills with AgentOps instead of checking folders manually:

```powershell
agentops skills install --platform copilot
```

Then open Copilot in this repo and run:

```text
/skills
```

Confirm `agentops-workflow` is loaded before continuing.

When the skill is loaded, paste:

```text
Use the AgentOps workflow skill to get the generated PR gate and watchdog
workflows running on GitHub Actions for this Foundry prompt-agent project.

This may be a brand-new folder with no Git repo or GitHub remote yet. Keep the
scope to the PR gate and watchdog only: create or connect the GitHub repo if
needed, wire Azure OIDC and required Actions variables, create only the `dev`
environment, and do not set up `qa`, `production`, or deploy workflows yet.
Show me the plan before changing GitHub or Azure, and call out anything that
needs owner/admin permission.
```

For the `pr,watchdog` quickstart, the generated workflows use the `dev`
environment for OIDC and variables. You do **not** need `qa` or `production`
yet; add them when you generate deploy workflows later.

The workflow skill will copy the needed CI variables from your local
AgentOps/azd configuration into the GitHub `dev` environment. If a value such
as the evaluator model deployment is missing, it will ask you.

The PR workflow should contain the Microsoft Foundry eval action:

```text
microsoft/ai-agent-evals@v3-beta
```

For the recorded workshop branch, keep the PR gate aligned with the repository
set by pointing the generated workflow at Paulo's `ai-agent-evals` fork:

```powershell
(Get-Content .github\workflows\agentops-pr.yml) `
  -replace 'microsoft/ai-agent-evals@v3-beta', 'placerda/ai-agent-evals@main' |
  Set-Content -Encoding utf8 .github\workflows\agentops-pr.yml
```

Use this override only in the workshop branch. Product and release branches
should use the Microsoft Action reference unless your team intentionally pins a
controlled fork.

The workflow also records provenance and release-evidence files. The detailed
quality scores stay in Foundry Evaluations:

```text
.agentops/official-eval/metadata.json
.agentops/official-eval/result.json
.agentops/release/latest/evidence.md
```

## 8. Force a prompt regression, then fix it

This step makes the quickstart more than a happy path. You will intentionally
ship a worse prompt, watch the eval gate or metrics move, then recover.

This walkthrough assumes this concrete sequence:

- `travel-agent:2` is the last good version that already has a green run.
- `travel-agent:3` is the intentionally regressed version you are about to test.
- `travel-agent:4` is the restored version you publish after the regression
  test.

If your Foundry project publishes different numbers because you saved or
published extra times, use the exact `travel-agent:<version>` values shown in
your GitHub summary and Foundry run pages.

1. In Foundry, edit the `travel-agent` instructions to this intentionally bad
   version:

   ```text
   Answer travel questions in one vague sentence. Do not include day-by-day
   plans, practical notes, constraints, or booking caveats.
   ```

2. Publish it as the next version, for example `travel-agent:3`.
3. Re-run the wizard and update only the agent value:

   ```powershell
   agentops init --reconfigure
   ```

   Keep the same endpoint and dataset, but answer `Agent` with
   `travel-agent:3`.
4. Create a regression branch, push it, and open a PR to `main`:

   ```powershell
   git switch -c feature/regress-travel-agent
   git add agentops.yaml
   git commit -m "Evaluate regressed travel agent prompt"
   git push -u origin feature/regress-travel-agent
   gh pr create --base main --head feature/regress-travel-agent --title "Test AgentOps regression gate" --body "Evaluates the intentionally regressed travel-agent prompt."
   ```

   The PR gate reads `agent: travel-agent:3` from `agentops.yaml` in that
   branch and evaluates the regressed version. Open the PR in GitHub and watch
   **Checks** -> **AgentOps PR / Eval (PR gate)**:

   ```powershell
   gh pr view --web
   ```

   Use the GitHub Actions summary as the handoff to Foundry:

   - In GitHub, wait for **Checks** -> **AgentOps PR / Eval (PR gate)** to
     finish, then click **Details**.
   - Still in GitHub, on the workflow run **Summary**, find **Azure AI
     Evaluation**. The table shows the exact regressed Agent ID and its pass
     rates. Confirm it says `travel-agent:3`.
   - Still in GitHub, click **View run results** in that table. This opens
     Foundry in a new page for the regressed agent run. Keep this Foundry page
     open and use **Overall metric results** as the quality source of truth; the
     GitHub artifact is only provenance.
   - Now in Foundry, click the back arrow to return to **Evaluations**. Open the
     earlier green run for `travel-agent:2` in another browser tab.
   - Compare the two Foundry pages side by side: pass rate and average score in
     **Overall metric results**, then the same three rows in **Detailed metrics
     result**. If you need row-level explanations, click **Analyze Results** on
     each run.
     The regressed run should score lower because it no longer returns
     day-by-day plans, practical notes, constraints, or booking caveats.

5. Restore the original Travel Agent instructions from step 1, publish again
   as the next version, for example `travel-agent:4`.
6. Point the repo at the fixed version:

   ```powershell
   agentops init --reconfigure
   ```

   Keep the same endpoint and dataset, but answer `Agent` with
   `travel-agent:4`.
7. Create a fix branch, push it, and open a PR to `main`:

   ```powershell
   git switch main
   git pull
   git switch -c fix/restore-travel-agent
   git add agentops.yaml
   git commit -m "Restore travel agent prompt evaluation"
   git push -u origin fix/restore-travel-agent
   gh pr create --base main --head fix/restore-travel-agent --title "Restore Travel Agent eval target" --body "Points AgentOps at the restored travel-agent prompt version."
   ```

   The new PR gate should evaluate `travel-agent:4`. In the GitHub Actions
   summary, click **View run results** and confirm the Foundry metrics recover
   relative to the regressed `travel-agent:3` run.

The learning loop is the point: Foundry owns prompt versioning and the managed
evaluation run; AgentOps keeps the repo pointed at the exact version under
review and records the evidence for the release discussion.

## 9. Run Doctor and create release evidence locally

```powershell
agentops doctor --workspace . --evidence-pack
code .agentops\agent\report.md
code .agentops\release\latest\evidence.md
```

Open both files. The Doctor report explains what is ready and what is missing;
the evidence pack is the reviewer-friendly summary. In a fresh quickstart it is
normal to see warnings for production telemetry, scheduled CI, or trace
regression history. Those warnings are useful because they show the difference
between "the eval ran once" and "this agent has enough release evidence."

Doctor is read-only. It does not create Foundry resources or run red-team scans.
It checks whether the repo has the signals a release reviewer needs: eval gates,
telemetry wiring, CI, trace-regression readiness, and links back to Foundry
where Foundry owns the runtime view.

## 10. Open Cockpit

```powershell
agentops cockpit --workspace .
```

Open the local URL printed by the command. The Cockpit should show Foundry
connection, Microsoft Foundry eval readiness, Doctor findings, release
evidence, CI/CD, and next actions.

## Success criteria

You are done when:

- The Travel Agent exists in Foundry and has a published `travel-agent:<version>` reference.
- `agentops workflow analyze` selects Microsoft Foundry AI Agent Evaluation.
- `agentops workflow generate` creates a PR workflow with
  `microsoft/ai-agent-evals@v3-beta`, or `placerda/ai-agent-evals@main` only
  for the recorded workshop branch.
- You published a deliberately regressed prompt version, saw the eval/pipeline
  signal move, restored the prompt, and reran the gate.
- `agentops doctor --evidence-pack` writes
  `.agentops/release/latest/evidence.md`.
- Cockpit opens and links the repo-side readiness view back to Foundry.
