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
AgentOps. The repository set below keeps the CLI, workflow runner, Toolkit
reference, and skill guidance aligned in one cohesive demo environment.

| Repository | Role in the journey |
|---|---|
| `Azure/agentops` | Provides the AgentOps CLI, workflow generation, Doctor, Cockpit, and release evidence flow. |
| `microsoft/ai-agent-evals` | Provides the Foundry-native PR evaluation gate used by the generated workflow. |
| `microsoft/foundry-toolkit` | Frames the VS Code create/debug experience and the Operate handoff after a prompt version is ready. |
| `microsoft/azure-skills` | Connects Copilot guidance to Foundry observe, CI/CD, regression, and trace follow-through. |
| `Azure-Samples/microsoft-foundry-e2e-agent-observability-workshop` | Reference for the Foundry Observe/Optimize/Protect loop: traces, App Insights, Operate Ask AI, evaluations, and red-team follow-through. |

## Before you run the tutorial

Do this once before a live walkthrough or guided session. The goal is to keep the
demo focused on the Foundry plus AgentOps flow, not on unexpected permission
prompts.

| Check | Why it matters |
|---|---|
| Azure CLI is installed and `az login` succeeds with the tenant that owns the Foundry project. | AgentOps, Foundry SDK calls, and CI setup all need the same Azure identity context. |
| You can create or publish a prompt agent in the Foundry project. | The tutorial starts from a real `travel-agent:<version>` target. |
| You can create or attach Application Insights for the Foundry project, or you already have one connected. | Foundry Traces, the Operate dashboard, Doctor, and Cockpit need telemetry to tell the observability story. |
| You can push to the tutorial GitHub repository and run GitHub Actions. | The PR gate only runs after the repo is pushed. |
| GitHub CLI is authenticated with `gh auth login` if you use the PR commands in this tutorial. | The regression step opens a PR and sends the reader directly to the workflow run. |
| You can create a GitHub environment named `dev` and add Actions variables/secrets. | The generated workflow uses that environment for Azure auth and evaluator settings. |
| You can create an Entra app registration with federated credentials, or an admin is ready to provide the client ID, tenant ID, and subscription ID. | The workflow skill can wire OIDC cleanly; without this, CI cannot authenticate to Azure. |
| Copilot or your coding-agent CLI is signed in before you ask it to run AgentOps skills. | The skill handoff assumes an authenticated coding-agent session that can read the repo and propose GitHub/Azure setup steps. |

## Journey you will exercise

| Step | Main tool | What you do | AgentOps role |
|---|---|---|---|
| Create the agent | Foundry portal, Foundry SDK, Foundry Toolkit, or `microsoft-foundry` skill | Create and publish `travel-agent`. | No ownership; AgentOps consumes the published target. |
| Try and debug | Foundry playground, VS Code, Copilot Chat | Validate behavior before adding release gates. | Optional quick eval later. |
| Observe the run | Foundry Traces, Application Insights, Foundry Operate | Inspect the first trace, quality signals, and conversation context. | Later checks telemetry wiring and links evidence back to Foundry. |
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
6. Copy the published reference. Foundry commonly shows `travel-agent:2` after
   this first publish, so the rest of this tutorial uses that as the baseline.
   If Foundry shows a different version, use that exact `name:version` value
   instead and shift the later regression/fix numbers accordingly.

Test it in the Foundry playground with:

```text
Plan a 3-day first-time trip to Lisbon for a couple who likes food and history.
```

Before leaving Foundry, turn that playground call into an observability check.
There are two separate Foundry surfaces here: the agent **Traces** tab for the
single run, and **Operate** for aggregate metrics and Ask AI.

1. In **Build** -> **Agents**, open `travel-agent`, then open the **Traces**
   tab. If Foundry asks you to connect
   Application Insights, connect an existing resource or create one from the
   portal flow. You need permission to create or attach that resource.
2. Confirm the App Insights resource appears under the Foundry project connected
   resources.
3. Run the Lisbon prompt again in the playground.
4. Open **Traces**, wait 2-5 minutes if needed, and find the newest row in
   **Conversations** or **Responses**. Click the **Trace ID** link.
5. In the trace details modal, inspect the left-side span list, such as
   `invoke_agent` and the chat/model span, then use the right-side
   **Input + Output** and **Metadata** tabs to review latency, model call,
   prompt, response, tokens, and IDs.
6. If Foundry shows a **Conversation ID** for the same interaction, open it to
   see the broader multi-turn context. If it is not shown, keep the Trace ID;
   that is enough for the rest of this tutorial.
7. If you want an AI-assisted operations summary, switch to **Operate** ->
   **Overview**, select the same subscription/project, wait for metrics to sync,
   and use **Ask AI** there. Treat it as a dashboard-level helper, not a
   trace-modal button. Example:

   ```text
   Help me identify any issues or anomalies in my agent metrics.
   ```

This is the Foundry side of Operate. AgentOps does not replace it; later Doctor,
Cockpit, and release evidence check whether the repo can point reviewers back to
these official runtime signals.

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

For normal usage, prefer the published package above. For this tutorial path,
install the aligned reference branch so the CLI, generated workflows, and
tutorial steps stay in sync:

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
| Prompt agent reference | `travel-agent:2` or the version Foundry published |
| Application Insights connection string | recommended for observability and Doctor links |

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
| Agent | `travel-agent:2`, or the exact published version from Foundry |
| Dataset path | `.agentops/data/travel-smoke.jsonl` |

The wizard does not ask for App Insights. Later runtime commands try to discover
the connected App Insights resource through the Azure AI Projects SDK. If the
project has no resource attached, or your identity cannot read it, run
`agentops init --appinsights-connection-string "<connection-string>"` or set
`APPLICATIONINSIGHTS_CONNECTION_STRING` manually in `.agentops/.env`.

If the first run shows starter defaults such as `Agent [my-agent:1]` or
`Dataset path [.agentops/data/smoke.jsonl]`, replace them with the Travel Agent
values above. Those defaults only come from the scaffolded starter file.

The interactive path is intentional: you see what each value means, and each
answer is saved as soon as it validates. By default, local Azure values go to
`.agentops/.env`. If this repo already uses `azd`, or you want AgentOps to write
to an azd env, run `agentops init --azd-env <name>`.

This creates:

```text
agentops.yaml
.agentops/
.agentops/.env
```

`agentops.yaml` should stay small:

```yaml
version: 1
agent: travel-agent:2
dataset: .agentops/data/travel-smoke.jsonl
```

The `.agentops/.env` file is intentional: AgentOps keeps local Azure values out
of source control while eval, Doctor, and Cockpit commands resolve the same
workspace environment. The Foundry project endpoint lives there instead of in
`agentops.yaml`; if you force an App Insights connection string later, it is
saved there too. Existing azd workspaces keep using `.azure/<env>/.env`.
The Copilot skills are installed later, in step 7, with
`agentops skills install --platform copilot`.

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
  Copilot skills  installed - available for workflow adaptation handoff
```

That means generated CI uses the Microsoft Foundry AI Agent Evaluation
action/task for the eval step, then uses AgentOps to collect evidence and
readiness signals.

## 7. Generate the PR gate and Doctor evidence

```powershell
agentops workflow generate --kinds pr --force
agentops doctor --workspace . --evidence-pack
```

`agentops doctor` can take a few minutes in a fresh workspace because it checks
Azure auth, Foundry discovery, Azure Monitor/App Insights, local eval history,
and repo workflow evidence. Watch the terminal progress line; the command is
still running while it reports elapsed time.

Read the output in this order: `AgentOps pre-flight` shows which local auth and
discovery checks passed, `Release readiness` gives the shippability verdict,
`Findings` / `Finding summary` names the blocking or warning items, and
`Evidence pack` / `Evidence report` show the files to open. Warnings are
advisory unless you run with strict pre-flight; `blocked` means the report has
findings to review, not that the command crashed. If App Insights is connected
in Foundry but AgentOps warns about discovery, run `az login`, confirm Reader on
the Foundry project resource group, or set
`APPLICATIONINSIGHTS_CONNECTION_STRING` explicitly.

Use this quick readout while presenting the terminal output:

| Output | How to explain it |
|---|---|
| `AgentOps pre-flight   4 ok` | The workspace, Azure auth, Foundry project, and App Insights discovery checks are all usable. |
| `Wrote` | The local Doctor diagnostic report was generated. |
| `Release readiness: blocked` | The command succeeded, but the current evidence has findings that block release readiness. |
| `Evidence pack` / `Evidence report` | These are the release-review artifacts to open or attach to the PR/release discussion. |
| `Findings: 13 (3 critical ...)` | This is the severity rollup; critical items are what you discuss first. |
| `Finding summary` | This is the terminal triage list. In a demo output like latency plus `regression.coherence` / `regression.f1_score`, explain that production performance and eval regressions block release, while workflow, threshold, RAI, and trace-regression warnings are hardening follow-ups. |

The useful story is the insight list, not the fact that a file was written.
In the sample output, Doctor is telling you that this agent is not release-ready
for three concrete reasons: production telemetry shows latency/error risk, eval
history shows quality regression on metrics such as `coherence` and `f1_score`,
and the repo still has operational hardening gaps such as missing deploy
workflow, explicit thresholds, continuous eval, action SHA pinning, and
trace-to-regression feedback. Use the critical findings as release blockers and
the warning/info findings as the backlog for making the agent production-ready.

In the generated PR workflow, the eval step is the merge gate and Doctor is
evidence-only. That means the Actions run can be green while the evidence report
still says `Release readiness: blocked`; use that as the didactic point that PR
quality passed, but production readiness still has work to do. Later production
deploy workflows run Doctor as a critical release gate.

At this point the workflow files exist only on your machine. CI will not run
until the folder is a GitHub repository, pushed, and connected to Azure with
OIDC.

Recommended path: let Copilot use the installed AgentOps workflow skill as the
guide, because this step crosses repo, GitHub, and Azure permissions.
Have the Entra app-registration permission or the admin-provided OIDC values
ready before you start this handoff.

Refresh the Copilot skills with AgentOps instead of checking folders manually:

```powershell
agentops skills install --platform copilot --force
```

Then open Copilot in this repo and run:

```text
/skills
```

Confirm `agentops-workflow` is loaded before continuing.

When the skill is loaded, paste:

```text
Use the AgentOps workflow skill to get the generated PR gate running on GitHub
Actions for this Foundry prompt-agent project.

This may be a brand-new folder with no Git repo or GitHub remote yet. Keep the
scope to the PR gate only: create or connect the GitHub repo if needed, wire
Azure OIDC and required Actions variables, create only the `dev` environment,
verify the OIDC principal has Foundry User access before running the workflow,
and do not set up `qa`, `production`, scheduled Doctor, or deploy workflows yet.
Show me the plan before changing GitHub or Azure, and call out anything that
needs owner/admin permission.
```

For this PR-gate quickstart, the generated workflow uses the `dev` environment
for OIDC and variables. You do **not** need `qa` or `production` yet; add them
when you generate deploy workflows later.

The workflow skill may move ahead using reasonable defaults, such as the current
folder or remote name for the GitHub repository and `dev` for the environment.
If you want a different repo, environment name, or secret/variable source, say
that in the prompt before it starts. If a required value such as the evaluator
model deployment is missing, it will ask you.

The PR workflow should contain the Microsoft Foundry eval action:

```text
microsoft/ai-agent-evals@v3-beta
```

The generated workflow uses the official Microsoft Action by default. Keep that
default for product and release branches. In this tutorial branch only, switch
the Action reference so the CLI, tutorial steps, and eval Action all come from
the same tutorial-aligned repository set while you are walking through the demo.
The evaluation still runs and is reviewed in Foundry; this change only controls
which GitHub Action implementation the PR gate calls.

For this tutorial branch, point the generated workflow at the tutorial reference
action:

```powershell
(Get-Content .github\workflows\agentops-pr.yml) `
  -replace 'microsoft/ai-agent-evals@v3-beta', 'placerda/ai-agent-evals@main' |
  Set-Content -Encoding utf8 .github\workflows\agentops-pr.yml
```

Use this override only for the tutorial walkthrough. In real product or release
branches, keep `microsoft/ai-agent-evals@v3-beta` unless your team intentionally
pins a different controlled reference.

After the replacement, the workflow contract stays the same: it prepares the
Foundry eval input, records provenance for review, and lets AgentOps attach
release evidence. The GitHub run summary shows the Doctor finding summary from
`evidence.md`, so a blocked readiness result names the critical items to fix
without hunting through logs. The detailed quality scores stay in Foundry
Evaluations:

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

Before you create the regression, make sure the baseline state is already on the
remote default branch and has run once:

```powershell
git status
git add agentops.yaml .agentops\data\travel-smoke.jsonl .github\workflows
git commit -m "Add AgentOps prompt agent gate"
git push -u origin main
gh workflow run agentops-pr.yml --ref main
Start-Sleep -Seconds 10
$runId = gh run list --workflow agentops-pr.yml --branch main --limit 1 --json databaseId --jq '.[0].databaseId'
gh run view $runId --web
gh run watch $runId --exit-status
```

If the commit already exists, skip the `git commit` line and just push/run the
workflow. This manual `workflow_dispatch` run is the green baseline you will use
for comparison.

Use the browser view as part of the lesson, not only the terminal. In GitHub,
open the repository, go to **Actions** -> **AgentOps PR**, open the latest run,
and click **AgentOps eval (PR gate)**. The terminal tells you pass/fail; the
Actions page shows which step failed, the summary table, and the link back to
Foundry.

Do not continue to the intentional regression until this baseline run is green.
It is okay if the run summary includes `Release readiness: blocked` in the
Doctor evidence section; for this PR-gate tutorial, that section is advisory.
If the failed step is **Run official AI Agent Evaluation** and the log says the
principal `lacks the required data action`
`Microsoft.CognitiveServices/accounts/AIServices/agents/read`, the workflow
authenticated to Azure but the GitHub OIDC app/service principal cannot read
Foundry agents yet. Ask the Azure/Foundry admin to grant that principal
**Foundry User** access at the Foundry project scope, or at the Foundry resource
scope if that is how your environment is managed, then rerun the same workflow.
Reader alone is not enough for this data-plane call.

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

Use the same Doctor readout rules from step 7: a multi-minute run is normal,
pre-flight warnings explain missing local access or telemetry discovery, and
`Release readiness: blocked` means review the findings rather than retrying the
command.

Open both files. The Doctor report explains what is ready and what is missing;
the evidence pack is the reviewer-friendly summary. If the Foundry trace from
step 1 appeared, telemetry readiness should have a concrete resource to link to.
In a fresh quickstart it is normal to see warnings for production telemetry,
scheduled CI, or trace regression history. Those warnings are useful because
they show the difference between "the eval ran once" and "this agent has enough
release evidence."

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
- At least one playground interaction appears in Foundry Traces, and you can
  open the Trace ID to inspect the spans and input/output details.
- `agentops workflow analyze` selects Microsoft Foundry AI Agent Evaluation.
- `agentops workflow generate` creates a PR workflow with the Microsoft Action
  reference for product/release branches, and the tutorial reference action only
  for the tutorial branch.
- You published a deliberately regressed prompt version, saw the eval/pipeline
  signal move, restored the prompt, and reran the gate.
- `agentops doctor --evidence-pack` writes
  `.agentops/release/latest/evidence.md`, and the GitHub run summary shows its
  Doctor finding summary.
- Cockpit opens and links the repo-side readiness view back to Foundry.
