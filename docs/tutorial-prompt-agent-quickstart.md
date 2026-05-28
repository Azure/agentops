# Quickstart: Foundry Prompt Agent (sandbox → dev with PR gate)

Use this quickstart when you want a Foundry-managed prompt agent referenced as
`name:version`. The example creates a small **Travel Agent** in Foundry and
then uses AgentOps to add repo-side readiness, a PR gate that catches
regressions before merge, a `dev` deploy workflow, Doctor evidence, and
Cockpit.

This path validates the Foundry-native multi-environment route:

- Foundry owns the prompt agent runtime, cloud evaluation execution, traces,
  and Operate dashboards in **each environment**.
- AgentOps owns repo-side readiness: source-controlled prompts, CI gates,
  Doctor blocking, release evidence, threshold enforcement, and Cockpit.

The toolkit benefit is the **release loop across environments**. You will
author the prompt in a **sandbox** Foundry project where saves are
experimentation only and never trigger CI, then let CI prove the prompt
is safe to merge by staging it as a candidate in the team's **dev**
Foundry project, evaluating that exact candidate, running Doctor against
the result, and — only when both pass — promoting the deploy.

Pay special attention to Doctor in this tutorial: it does not only report
whether thresholds passed, it also catches slow regressions (for example,
`groundedness` drifting from 5.0 to 4.0) that the threshold gate would
otherwise miss. When the PR workflow runs Doctor with
`--severity-fail critical`, those regression findings **block the PR**
the same way a failed threshold would.

## Repository set used in this tutorial

This tutorial intentionally shows the broader Foundry ecosystem, not only
AgentOps. The repository / skill set below keeps the CLI, workflow runner,
toolkit reference, and skill guidance aligned in one cohesive demo
environment.

| Repository / skill | Role in the journey |
|---|---|
| `Azure/agentops` | Provides the AgentOps CLI, workflow generation, Doctor, Cockpit, and release evidence flow. |
| `microsoft-foundry` skill (Copilot Chat) | External, not bundled with AgentOps. Demonstrates how a skill outside the AgentOps toolkit can guide Foundry project creation. The tutorial gives a portal-first fallback because the skill is optional. |
| `microsoft/ai-agent-evals` | Reference for the Foundry-native eval Action/task. AgentOps invokes Foundry cloud eval directly for the default PR gate so it can enforce thresholds and write normalized evidence. |
| `microsoft/foundry-toolkit` | Frames the VS Code create/debug experience and the Operate handoff after a prompt version is ready. |
| `microsoft/azure-skills` | Connects Copilot guidance to Foundry observe, CI/CD, regression, and trace follow-through. |
| `Azure-Samples/microsoft-foundry-e2e-agent-observability-workshop` | Reference for the Foundry Observe/Optimize/Protect loop: traces, App Insights, Operate Ask AI, evaluations, and red-team follow-through. |

## Before you run the tutorial

Do this once before a live walkthrough or guided session. The goal is to keep
the demo focused on the Foundry plus AgentOps flow, not on unexpected
permission prompts.

| Check | Why it matters |
|---|---|
| Azure CLI is installed and `az login` succeeds with the tenant that owns the Foundry projects. | AgentOps, Foundry SDK calls, and CI setup all need the same Azure identity context. |
| You can create **two** Foundry projects in the same Azure subscription (or have two existing projects you can use). | The tutorial uses a sandbox project for authoring and experimentation plus a shared dev project for the PR gate; the PR workflow stages candidates in dev. |
| You can publish a prompt agent in each Foundry project. | The tutorial seeds the same `travel-agent:1` baseline in both projects so the deploy workflow has a known template to look up. |
| You can create or attach Application Insights for at least the dev Foundry project. | Foundry Traces, the Operate dashboard, Doctor, and Cockpit need telemetry to tell the observability story. Sandbox observability is optional. |
| You can push to the tutorial GitHub repository and run GitHub Actions. | The PR gate only runs after the repo is pushed. |
| GitHub CLI is authenticated with `gh auth login` if you use the PR commands in this tutorial. | The regression step opens PRs and sends the reader directly to the workflow run. |
| You can create a GitHub environment named `dev` and add Actions variables/secrets. | The generated workflow uses that environment for Azure auth and the dev Foundry project endpoint. |
| You can create an Entra app registration with federated credentials, or an admin is ready to provide the client ID, tenant ID, and subscription ID. | The workflow skill can wire OIDC cleanly; without this, CI cannot authenticate to Azure. |
| Copilot or your coding-agent CLI is signed in before you ask it to run AgentOps skills. | The skill handoff assumes an authenticated coding-agent session that can read the repo and propose GitHub/Azure setup steps. |

## Mental model: sandbox, dev, and what crosses environments

Before the hands-on steps, hold this picture in your head:

```
sandbox Foundry project              dev Foundry project
(authoring + experimentation;        (shared environment, PR gate target,
 used by you or the team)             where merge deploys land)
    │                                          │
    │  travel-agent:1 (seed)                   │  travel-agent:1 (seed, same instructions)
    │  travel-agent:2,3,4,... (free saves)     │  travel-agent:2,3,... (created by CI per PR / deploy)
    │                                          │
    └──── git is the source of truth ─────────►│
          .agentops/prompts/travel-agent.md
          prompt_sha256 + git_sha
```

Two ideas to internalize:

1. **The prompt in `git` is the source of truth.** The file
   `.agentops/prompts/travel-agent.md` is what CI reads and what reviewers
   diff. Each Foundry project's version numbers count its own saves and
   are environment-local.
2. **Cross-environment identity is the SHA, not the number.** AgentOps
   embeds `agentops.prompt_sha256` and `agentops.git_sha` into every
   Foundry version it creates, and writes the same identifiers into the
   per-environment deploy artifact `foundry-agent.json`. When you ask
   "is the same prompt running in sandbox, dev, and prod?", you compare
   SHAs, not version numbers. The version numbers will differ.

The longer walkthrough of that identity story is in step 13, when you
have a real `foundry-agent.json` artifact to open.

## Journey you will exercise

| Step | Main tool | What you do | AgentOps role |
|---|---|---|---|
| Create two Foundry projects | Foundry portal (or `microsoft-foundry` skill) | Create `travel-agent-sandbox` and `travel-agent-dev`; seed `travel-agent:1` in both. | No ownership; AgentOps consumes the published baselines. |
| Author in sandbox | Foundry playground | Iterate on the prompt safely in sandbox Foundry. | Optional spot-check via local `agentops eval run`. |
| Promote the prompt to git | Editor | Copy validated instructions into `.agentops/prompts/travel-agent.md`. | The CI gate reads this file. |
| First green PR + dev deploy | GitHub Actions + Foundry dev project | Push prompt, open PR, watch CI stage a candidate in dev, evaluate it, run Doctor; merge; deploy lands in dev. | Owns the gate, the threshold decision, the Doctor blocking step, the deploy artifact, and the release evidence. |
| Force a regression | Editor + GitHub Actions | Edit the prompt to a worse version, push, observe BOTH eval threshold failure AND Doctor regression CRITICAL. | Catches the regression at PR time, not after merge. |
| Fix and redeploy | Editor + GitHub Actions | Restore prompt, push, PR green, merge, deploy. | Records the recovery. |
| Review readiness | AgentOps Doctor + Cockpit | Check CI, eval, telemetry, evidence, and links. | Turns scattered signals into release blockers, warnings, evidence files, and next actions. |

## 1. Create a clean workspace and install AgentOps

Create a workspace folder and install the toolkit before any other tool
runs. The skills and CLI commands later in the tutorial all depend on this.

```powershell
mkdir agentops-prompt-quickstart
cd agentops-prompt-quickstart
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install "agentops-accelerator[foundry,agent]"
agentops --version
```

For normal usage, prefer the published package above. For this tutorial
path, install the aligned reference branch so the CLI, generated
workflows, and tutorial steps stay in sync:

```powershell
python -m pip install "agentops-accelerator[foundry,agent] @ git+https://github.com/placerda/agentops.git@develop"
```

## 2. Install the AgentOps Copilot skills

AgentOps ships a set of Copilot skills that guide eval, dataset, workflow,
and Doctor flows. Install them now so they are available when you hand off
to Copilot Chat later.

```powershell
agentops skills install --platform copilot --force
```

That command installs the AgentOps skills (`agentops-eval`,
`agentops-workflow`, `agentops-config`, `agentops-dataset`, and so on)
into `.github/skills/` so Copilot can pick them up when you say `/skills`
in chat.

The `microsoft-foundry` skill used in step 3 is **separate and external**
to AgentOps. If it is not already available in your Copilot Chat session,
the tutorial falls back to the Foundry portal for the project creation
step. The intent is intentional: this is where AgentOps and other skills
meet, not a place where AgentOps imposes a particular skill stack.

## 3. Create the two Foundry projects

You need two Foundry projects in the same Azure subscription. Use these
names so the rest of the tutorial reads naturally:

- `travel-agent-sandbox` — the authoring and experimentation space. Saves
  here never trigger CI. One project is fine whether you are solo or
  working with a small team; everyone with access can iterate here.
- `travel-agent-dev` — the first shared environment. The PR gate stages
  candidates here, and the dev deploy workflow lands here.

> **Team scaling.** A single sandbox project works fine for a solo
> walkthrough and for small teams. If you grow to the point that
> simultaneous saves collide, or different feature streams need to
> experiment in isolation, you can split into per-stream sandboxes
> (`travel-agent-checkout-sandbox`, `travel-agent-search-sandbox`, etc.)
> or per-developer sandboxes. AgentOps does not care how many sandbox
> projects exist; only the dev / qa / prod chain is what CI promotes
> through.

### Path A — Foundry portal (always available)

1. Open the [Azure AI Foundry portal](https://ai.azure.com).
2. Create the first project. Use the same Azure subscription you will
   target with CI.
   - **Project name:** `travel-agent-sandbox`
   - **Region/resource:** any region with the model deployment you plan
     to use.
3. Repeat for the second project named `travel-agent-dev`. Use the same
   subscription. The two projects can share a resource group or be in
   separate ones, depending on your team's policy.
4. For each project, copy the project endpoint URL from the project
   overview page. It looks like:

   ```text
   https://<resource>.services.ai.azure.com/api/projects/travel-agent-sandbox
   https://<resource>.services.ai.azure.com/api/projects/travel-agent-dev
   ```

   Save both endpoints. You will paste them in step 7 and step 8.

### Path B — `microsoft-foundry` skill (if available)

If your Copilot session already has the external `microsoft-foundry`
skill, you can drive the same outcome from chat. In Copilot, run:

```text
/skills
```

If you see `microsoft-foundry` listed, paste the following and let the
skill propose the changes before applying them:

```text
I want to set up two Azure AI Foundry projects in the same subscription
for an AgentOps tutorial:

1. travel-agent-sandbox - the authoring and experimentation space
   (used by me, or shared with my team for iteration).
2. travel-agent-dev - shared dev environment used by CI as the PR gate
   target and the dev deploy target.

For each project, please:
- Create the project (any region with a chat-capable deployment is fine).
- Make sure a chat-capable model deployment (gpt-4o-mini works) is
  available in the project.
- Attach or create an Application Insights resource for telemetry,
  starting with the dev project.

Show me the planned changes and the resulting endpoints before applying.
```

If the skill is not available, use Path A.

## 4. Seed `travel-agent:1` in both Foundry projects

The deploy workflow looks up the agent reference from `agentops.yaml`
inside each environment's Foundry project as a **template**. It copies
that template's model deployment, kind, name, and other settings, then
replaces the instructions with whatever is in `prompt_file`. That means
`travel-agent:1` must already exist in **both** projects when CI runs,
with identical settings.

In **each** project (sandbox first, then dev), do the same thing:

1. Open the [Azure AI Foundry portal](https://ai.azure.com) and select
   the project.
2. Go to the agents area and create a new prompt-based agent.
3. Use these values:

   | Field | Value |
   |---|---|
   | Name | `travel-agent` |
   | Model deployment | `gpt-4o-mini` or another chat-capable deployment available in this project |
   | Description | Helps plan short trips and explains tradeoffs. |

4. Paste these baseline instructions:

   ```text
   You are Travel Agent, a concise travel planning assistant.

   Help users plan short leisure trips. Always include:
   - a short summary;
   - a day-by-day plan when the user asks for an itinerary;
   - practical notes about budget, transit, weather, or booking constraints;
   - a reminder that you cannot make live reservations or purchases.

   Ask one clarifying question only when the destination, duration, or
   traveler preference is missing. Do not invent booking confirmations,
   prices, or availability.
   ```

5. Save and publish the agent. Foundry assigns version `1` (`travel-agent:1`).
6. Confirm both projects now show `travel-agent:1` with the same
   instructions and the same model deployment.

> **Prompt-as-code captures only the instructions.** Later in the
> tutorial you will commit `.agentops/prompts/travel-agent.md` to git and
> let CI use it as the prompt source. That file does not capture the
> model deployment, parameters (temperature, top-p), tools, or other
> agent settings — those stay on the Foundry agent definition. If you
> ever need to change one of those, change it on the seed agent in
> **every** environment manually, or treat that change as a new release
> with its own review process. AgentOps will not detect drift in
> non-prompt fields.

## 5. Try the agent in the sandbox playground

Open `travel-agent-sandbox` in the Foundry portal, open `travel-agent:1`,
and run a sample in the playground:

```text
Plan a 3-day first-time trip to Lisbon for a couple who likes food and history.
```

This is the sandbox role: you confirm the prompt actually does what you
want before promoting it to git. Sandbox saves stay local to this project
and do not affect CI.

A short observability cross-reference: in the same project's
**Traces** tab you can find this run. If Foundry asks to attach
Application Insights and you have not connected it yet, you can do that
now or wait until the closeout step. The detailed observability tour is
in step 16; for now, just confirm there is at least one trace to look at
later.

## 6. Create the travel eval dataset

Create the small JSONL dataset that matches the Travel Agent behavior:

```powershell
New-Item -ItemType Directory -Force .agentops\data | Out-Null
@'
{"input":"Plan a 3-day first-time trip to Lisbon for a couple who likes food and history.","expected":"A concise 3-day Lisbon itinerary with food, history, neighborhoods such as Baixa, Alfama, and Belem, practical notes, and no claim to make live bookings."}
{"input":"Suggest a low-budget weekend in Seattle for a solo traveler who likes coffee and museums.","expected":"A practical weekend Seattle plan with low-budget choices, coffee and museum suggestions, transit or weather notes, and no claim to make live bookings."}
{"input":"I want to visit Tokyo for 5 days with two kids. What should we do?","expected":"A family-friendly 5-day Tokyo itinerary with kid-appropriate activities, transit and pacing notes, and no claim to make live bookings."}
'@ | Set-Content -Encoding utf8 .agentops\data\travel-smoke.jsonl
```

The `expected` values here are acceptance criteria, not exact answer
strings. For prompt agents, AgentOps uses judge-based quality and
completeness metrics on this shape; token-overlap F1 is better reserved
for exact-reference model tests.

## 7. Initialize AgentOps against the sandbox project

Sign in to Azure with the same identity that has access to both Foundry
projects:

```powershell
az login
```

Then run the wizard against the sandbox environment. AgentOps creates an
azd-compatible environment directory so the same workspace cleanly
supports multiple environments later.

```powershell
agentops init --azd-env sandbox
```

Answer the prompts:

| Prompt | Answer |
|---|---|
| Foundry project endpoint | The **sandbox** project endpoint from step 3 |
| Agent | `travel-agent:1` |
| Dataset path | `.agentops/data/travel-smoke.jsonl` |

If the wizard offers starter defaults such as `Agent [my-agent:1]` or
`Dataset path [.agentops/data/smoke.jsonl]`, replace them with the
Travel Agent values above.

The interactive path is intentional: you see what each value means, and
each answer is saved as soon as it validates. Because you passed
`--azd-env sandbox`, the wizard writes the local Azure values to
`.azure/sandbox/.env` and sets `defaultEnvironment: sandbox` in
`.azure/config.json`.

After the command finishes, your workspace looks like this:

```text
agentops.yaml
.agentops/
.agentops/data/travel-smoke.jsonl
.azure/
.azure/config.json
.azure/.gitignore
.azure/sandbox/.env
```

`agentops.yaml` should stay small:

```yaml
version: 1
agent: travel-agent:1
dataset: .agentops/data/travel-smoke.jsonl
```

> **App Insights.** The wizard does not ask for App Insights. Later
> runtime commands try to discover the connected App Insights resource
> through the Azure AI Projects SDK. If your dev project has no resource
> attached, or your identity cannot read it, set
> `APPLICATIONINSIGHTS_CONNECTION_STRING` manually inside
> `.azure/dev/.env` in step 8 (and inside `.azure/sandbox/.env` if you
> want sandbox traces too).

## 8. Add the dev azd environment by hand

The dev project endpoint goes into a second azd environment, but **do
not** re-run `agentops init --azd-env dev` — that would flip
`defaultEnvironment` in `.azure/config.json` to `dev` and change which
project local commands hit by default. Add the dev env manually instead:

```powershell
New-Item -ItemType Directory -Force .azure\dev | Out-Null
@'
AZURE_AI_FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/travel-agent-dev
'@ | Set-Content -Encoding utf8 .azure\dev\.env
```

Replace the endpoint with your real dev project endpoint from step 3. If
you have the dev project's App Insights connection string, add it here
too:

```text
APPLICATIONINSIGHTS_CONNECTION_STRING=<your-connection-string>
```

Confirm the final topology:

```text
.azure/
├── config.json              # defaultEnvironment: sandbox
├── .gitignore               # excludes <env>/.env
├── sandbox/
│   └── .env                 # sandbox project endpoint
└── dev/
    └── .env                 # dev project endpoint
```

`defaultEnvironment: sandbox` means local commands like
`agentops eval run` use the sandbox project. CI workflows in step 11
read from `.azure/dev/.env` explicitly so they always target dev.

## 9. Promote the prompt to a source-controlled file

This step turns the prompt into code. From here on, the prompt that CI
evaluates and deploys comes from this file in git, not from manual edits
in the Foundry portal.

```powershell
New-Item -ItemType Directory -Force .agentops\prompts | Out-Null
@'
You are Travel Agent, a concise travel planning assistant.

Help users plan short leisure trips. Always include:
- a short summary;
- a day-by-day plan when the user asks for an itinerary;
- practical notes about budget, transit, weather, or booking constraints;
- a reminder that you cannot make live reservations or purchases.

Ask one clarifying question only when the destination, duration, or
traveler preference is missing. Do not invent booking confirmations,
prices, or availability.
'@ | Set-Content -Encoding utf8 .agentops\prompts\travel-agent.md
```

Then tell `agentops.yaml` where to find the file:

```yaml
version: 1
agent: travel-agent:1
dataset: .agentops/data/travel-smoke.jsonl
prompt_file: .agentops/prompts/travel-agent.md
```

The `agent: travel-agent:1` value is now a **seed pointer**. CI uses it to
look up the existing agent in the current environment's Foundry project,
copies its definition (model deployment, name, kind), and replaces the
instructions with the contents of `prompt_file`. If the prompt is
byte-identical to the looked-up seed's instructions, CI re-uses the same
Foundry version. If it differs, Foundry auto-creates the next version
number in that project.

> **Versioning, in one paragraph.** You are not pinning Foundry's
> version number — you are pinning the prompt. The number that gets
> created in each Foundry project depends on how many saves that
> project has accumulated; sandbox, dev, qa, and prod will diverge.
> What stays identical across environments — and what you cite when
> traceability matters — is the prompt SHA-256 + the git SHA, both
> embedded into the Foundry version metadata and into
> `foundry-agent.json`. You only update `agent:` in `agentops.yaml`
> when you want to repoint at a different stable seed version in
> Foundry — not on every prompt change.

## 10. Check the selected eval runner

```powershell
agentops workflow analyze --format text
```

For `agent: name:version` plus `prompt_file`, AgentOps should recommend
AgentOps cloud eval in Foundry with the prompt-agent deploy mode:

```text
Recommendation
  deploy          prompt-agent
  evaluate        AgentOps cloud eval in Foundry
  workflow edits  not needed - generated workflow should work as-is
  Copilot skills  installed - available for workflow adaptation handoff
```

This is the combination the rest of the tutorial assumes: prompt-agent
mode means the PR workflow stages candidates from `prompt_file` and the
deploy workflow records candidates as deployed. Cloud eval in Foundry
means the actual evaluator runs server-side; AgentOps still owns the
threshold gate and the Doctor step.

## 11. Generate the PR + dev deploy workflows

```powershell
agentops workflow generate --kinds pr,dev --deploy-mode prompt-agent --doctor-gate critical --force
```

This creates two workflow files:

```text
.github/workflows/agentops-pr.yml
.github/workflows/agentops-deploy-dev.yml
```

The PR workflow now has two jobs:

1. **`stage-candidate`** — stages an ephemeral Foundry prompt-agent
   candidate in the **dev** Foundry project (not sandbox) by copying
   `travel-agent:1`'s definition and replacing instructions with
   `prompt_file`. Writes `.agentops/deployments/agentops.candidate.yaml`
   pointing at the staged candidate.
2. **`eval`** — runs `agentops eval run` against the candidate, then
   runs Doctor with `--severity-fail critical`.

> **Why does the PR workflow stage in dev, not sandbox?** The PR gate
> must evaluate the same target the deploy workflow will use. Sandbox
> is the author's playground and never receives CI traffic. PR
> candidates accumulate in dev over time and may need periodic
> cleanup according to your team's Foundry retention policy; AgentOps
> uses prompt SHAs and git SHAs as the durable identity, not old
> candidate version numbers.

The dev deploy workflow stages a candidate (same logic), evaluates it,
records the deployment via `prompt_deploy record`, and uploads
`.agentops/deployments/foundry-agent.json` as a workflow artifact.

The `--doctor-gate critical` flag controls the Doctor severity floor in
the PR template. The table below summarizes the three values:

| `--doctor-gate` value | PR Doctor behavior |
|---|---|
| `critical` (default) | The PR step fails if Doctor reports any critical findings. Use this to catch regressions that pass thresholds but still drift meaningfully (for example, `groundedness` 5.0 → 4.0). |
| `warning` | The PR step fails on warnings or critical findings. Tighter; useful for late-stage hardening. |
| `none` | Doctor runs advisory only. The PR step never fails because of Doctor. Use this only if you have a separate scheduled Doctor pipeline that owns the readiness call. |

Deploy templates always run with `--severity-fail critical` regardless of
`--doctor-gate`. The gate flag affects the PR template only; deploys are
the last-mile production gate and should always block on critical
findings.

## 12. Wire CI: GitHub repository + Azure OIDC + dev environment

The workflows live only on your machine right now. CI will not run until
the folder is a GitHub repository, pushed to a remote, and connected to
Azure with OIDC. Use the `agentops-workflow` Copilot skill so the GitHub
and Azure work happens in chat with explicit prompts and review.

Refresh the skills first (already done in step 2; this re-run ensures
they are up to date):

```powershell
agentops skills install --platform copilot --force
```

Open Copilot in this repo and run:

```text
/skills
```

Confirm `agentops-workflow` is loaded, then paste:

```text
Use the AgentOps workflow skill to get the generated PR gate plus dev
deploy workflows running on GitHub Actions for this Foundry prompt-agent
project.

This may be a brand-new folder with no Git repo or GitHub remote yet.
Keep the scope to the PR gate and dev deploy only: create or connect the
GitHub repo if needed, wire Azure OIDC and required Actions
variables/secrets, create only the `dev` environment, verify the OIDC
principal has Foundry User access on the **dev** Foundry project, and
do not set up `qa`, `production`, scheduled Doctor, or hosted
deployment workflows yet.

The dev Foundry project endpoint is in `.azure/dev/.env`; the sandbox
endpoint is local-only and must not be added to CI.

Show me the plan before changing GitHub or Azure, and call out anything
that needs owner/admin permission.
```

The workflow skill will normally do the following, but call out anything
it skips:

- Create/connect the GitHub remote.
- Create the `dev` GitHub environment.
- Configure OIDC federated credentials between GitHub and Entra ID.
- Set Actions variables `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`,
  `AZURE_CLIENT_ID`, `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` (the dev
  endpoint), and `APPLICATIONINSIGHTS_CONNECTION_STRING` if available.
- Verify the OIDC principal has **Foundry User** access on the dev
  Foundry project. Reader alone is not enough for the data-plane calls
  the prompt-agent staging and eval steps make.

## 13. First green PR → merge → dev deploy

This is the happy path. Before the regression step, you need a clean
green baseline so the rolling-history Doctor checks (regression, drift)
have something to compare against.

```powershell
git add agentops.yaml .agentops .github\workflows
git commit -m "Add AgentOps prompt agent gate + dev deploy"
git push -u origin main
```

Open the repository in GitHub and confirm both workflows appear under
**Actions**. Trigger the PR workflow once on `main` so the dev project
has a known-good candidate run:

```powershell
gh workflow run agentops-pr.yml --ref main
Start-Sleep -Seconds 10
$runId = gh run list --workflow agentops-pr.yml --branch main --limit 1 --json databaseId --jq '.[0].databaseId'
gh run view $runId --web
gh run watch $runId --exit-status
```

What you should see in the PR workflow run:

1. **Stage Foundry prompt candidate (PR)** job runs first. The
   `prompt_deploy stage` step looks up `travel-agent:1` in the dev
   project and compares the instructions in `prompt_file` against that
   seed.
   - If they are byte-identical: the stage step reports `reused` and
     uses `travel-agent:1` as the candidate (no new version created).
   - If they differ: Foundry auto-creates the next number (likely
     `travel-agent:2`) and the stage step reports `created`.
2. **AgentOps eval (PR gate)** job runs second. It evaluates the
   candidate (re-used or created) using cloud eval. Thresholds pass.
   Doctor runs with `--severity-fail critical`; advisory findings are
   listed but do not fail the job.

Now open a feature branch, modify a non-functional file (or just rerun
the workflow), open a PR, and merge it once green:

```powershell
git switch -c chore/agentops-baseline
git commit --allow-empty -m "Baseline AgentOps run"
git push -u origin chore/agentops-baseline
gh pr create --base main --head chore/agentops-baseline --title "Baseline AgentOps run" --body "First green PR to establish history."
```

Open the PR in GitHub. The PR check runs the same staging + eval flow,
green again because the prompt is unchanged. Merge.

After the merge, the **AgentOps deploy (dev)** workflow runs
automatically on `main`. It stages the candidate (likely re-using the
same version as the PR run), evaluates it, runs `prompt_deploy record`
to mark it as the dev deployment, and uploads the deployment artifact.

Open the deploy run and download the `foundry-agent-dev-deployment`
artifact. Inside, open `foundry-agent.json`:

```json
{
  "environment": "dev",
  "agent_source": "travel-agent:1",
  "agent_candidate": "travel-agent:2",
  "action": "created",
  "agentops": {
    "prompt_sha256": "9c3a...e0b1",
    "git_sha": "5f1a2c...",
    "workflow_url": "https://github.com/.../actions/runs/..."
  }
}
```

That `prompt_sha256` + `git_sha` pair is what the mental-model diagram
at the start of the tutorial referred to as **cross-environment
identity**. When you later add qa and prod deploys, each environment
will have its own `foundry-agent.json` with possibly different
`agent_candidate` version numbers but the **same** `prompt_sha256` and
`git_sha` whenever they are running the same release.

> **Foundry version numbers may differ between the PR and the deploy.**
> The PR workflow and the deploy workflow each stage independently
> against whatever the current seed (`travel-agent:1`) looks like at the
> moment they run. If the seed's instructions did not change between PR
> and merge, both runs typically reuse or create the same version. If
> another PR was staged in between, the version numbers may interleave.
> AgentOps deduplicates against the seed, not against all prior
> candidate versions, so two distinct PRs with the same prompt content
> can each create their own version. The durable identifier is
> `prompt_sha256`, not the integer suffix.

## 14. Regression PR — eval gate AND Doctor blocking

Now exercise the value of running Doctor as a critical PR gate. You will
intentionally ship a worse prompt and observe **two independent failure
modes** in the same PR:

- The eval thresholds may fail because `response_completeness` drops
  below the configured floor.
- Doctor's `regression.<metric>` checks fire because the relevant
  metric (commonly `coherence`, `response_completeness`, or
  `groundedness`) drops meaningfully from the rolling baseline. Because
  the PR workflow runs Doctor with `--severity-fail critical`, those
  findings fail the Doctor step on their own.

The two gates are independent; either is sufficient to block the PR.
This is why `--doctor-gate critical` matters: in cases where the eval
thresholds are loose enough that a regression slips through, Doctor
still catches it.

```powershell
git switch main
git pull
git switch -c feature/regress-travel-agent
```

Edit `.agentops/prompts/travel-agent.md` to this intentionally vague
version:

```text
Answer travel questions in one vague sentence. Do not include day-by-day
plans, practical notes, constraints, or booking caveats.
```

Commit and push:

```powershell
git add .agentops\prompts\travel-agent.md
git commit -m "Intentional regression: vague travel prompt"
git push -u origin feature/regress-travel-agent
gh pr create --base main --head feature/regress-travel-agent --title "Test AgentOps regression gate" --body "Evaluates an intentionally regressed travel-agent prompt."
```

Watch the PR check:

```powershell
gh pr view --web
```

In the GitHub run summary, you should see:

- **Stage Foundry prompt candidate (PR)** succeeds. The vague prompt
  differs from the seed, so Foundry creates a new version (the number
  depends on how many candidates have been staged in dev so far —
  do not depend on a specific number).
- **AgentOps eval (PR gate)** likely fails. The summary table shows
  failed thresholds, typically on `response_completeness` — the bad
  prompt still produces fluent travel text, but it stops satisfying
  the day-by-day plan / practical notes / booking caveat criteria.
- The **Run AgentOps Doctor** step runs with `--severity-fail critical`
  and reports `regression.<metric>` as critical. Even if the eval
  thresholds had marginally passed, this step would still fail the job.

In Foundry, navigate to the dev project, open **Evaluations**, and
compare the regressed run side-by-side with the baseline run from step
13. The pass rate and overall metric scores should be visibly lower on
the regressed run.

> **What if Doctor does not flag regression yet?** The
> `regression.<metric>` checks need at least a small history of prior
> runs to compute the baseline. The baseline run in step 13 plus this
> regression run should be enough, but if you skipped the baseline,
> Doctor may only emit lower-severity findings. Re-run the green
> workflow once on `main` to seed history, then push the regression
> branch again.

The lesson: this PR is blocked at PR time, before any reviewer touches
it, and the reason is in the GitHub run summary — not buried in a
post-deploy production alert.

## 15. Fix and redeploy

Restore the prompt to the good version:

```powershell
@'
You are Travel Agent, a concise travel planning assistant.

Help users plan short leisure trips. Always include:
- a short summary;
- a day-by-day plan when the user asks for an itinerary;
- practical notes about budget, transit, weather, or booking constraints;
- a reminder that you cannot make live reservations or purchases.

Ask one clarifying question only when the destination, duration, or
traveler preference is missing. Do not invent booking confirmations,
prices, or availability.
'@ | Set-Content -Encoding utf8 .agentops\prompts\travel-agent.md
```

Commit and push:

```powershell
git add .agentops\prompts\travel-agent.md
git commit -m "Restore travel agent prompt"
git push
```

The same PR re-runs (no new PR needed). The eval should pass again and
Doctor's regression findings should clear because the candidate's
metrics return to the rolling baseline. Merge. The dev deploy workflow
records the restored prompt as the dev deployment with a new
`foundry-agent.json` artifact that has the SHA of the recovered prompt.

The learning loop is the point: the prompt source of truth is in git,
the PR workflow exercises it as a candidate in dev, Doctor catches
regressions that thresholds alone miss, and the merge promotes through
the deploy workflow. None of those gates require the developer to
remember to look at a dashboard.

## 16. Brief observability checkout (Foundry side)

The Foundry side of the loop is worth a short tour, even though it is
not what AgentOps owns. This is the "Foundry tells you what happened"
side of the conversation.

1. Open the `travel-agent-dev` project in the Foundry portal.
2. Open the `travel-agent` agent and switch to the **Traces** tab. If
   Application Insights is not yet connected, connect or create the
   resource now.
3. Find the most recent eval run in **Conversations** or
   **Responses** and click the **Trace ID**. Inspect spans, latency,
   model call, and the input/output panes.
4. Switch to **Operate → Overview** and use **Ask AI** for a
   dashboard-level summary. Example:

   ```text
   Help me identify any issues or anomalies in my agent metrics for
   the last 24 hours.
   ```

5. Optionally, sample the same operation through Application Insights
   Logs (KQL) for the engineer-level view.

This is the observability surface AgentOps does **not** replace. Doctor
will check whether this telemetry is wired (App Insights connection
string, recent traces, etc.) and include it in the readiness call, but
the runtime view itself lives in Foundry.

## 17. Sync local evidence and create the release evidence pack

```powershell
agentops eval run
agentops doctor --workspace . --evidence-pack
code .agentops\results\latest\report.md
code .agentops\agent\report.md
code .agentops\release\latest\evidence.md
```

`agentops eval run` runs against the **sandbox** project by default
(because `defaultEnvironment` in `.azure/config.json` is `sandbox`).
That gives Doctor a current local snapshot to layer on top of the
CI-side results.

`agentops doctor --workspace . --evidence-pack` can take a few minutes
in a fresh workspace because it checks Azure auth, Foundry discovery,
Azure Monitor / App Insights, local eval history, and repo workflow
evidence. Read the output in this order:

| Output | How to explain it |
|---|---|
| `AgentOps pre-flight   4 ok` | The workspace, Azure auth, Foundry project, and App Insights discovery checks are all usable. |
| `Wrote` | The local Doctor diagnostic report was generated. |
| `Release readiness: blocked` | The command succeeded, but the current evidence has findings that block release readiness. |
| `Evidence pack` / `Evidence report` | These are the release-review artifacts to open or attach to the PR / release discussion. |
| `Findings: N (M critical ...)` | The severity rollup; critical items are what you discuss first. |
| `Finding summary` | The terminal triage list. |

In a fresh quickstart it is normal to see warnings for scheduled CI
(you only generated `pr` and `dev`), continuous evaluation, qa/prod
deploys, explicit thresholds, or red-team scans. Treat those as the
hardening backlog. The eval gates and the dev deploy loop are
production-ready.

## 18. Open Cockpit

```powershell
agentops cockpit --workspace .
```

Open the local URL printed by the command. The Cockpit should show
Foundry connection (sandbox by default; you can switch in the URL),
AgentOps cloud-eval readiness, Doctor findings, release evidence, the
PR and dev deploy CI pipelines, and next actions.

## Success criteria

You are done when:

- Two Foundry projects exist (`travel-agent-sandbox`, `travel-agent-dev`),
  each with a published `travel-agent:1` seed.
- `.azure/` has both `sandbox` and `dev` environment directories, with
  `defaultEnvironment: sandbox` for local commands.
- The prompt lives in `.agentops/prompts/travel-agent.md` and
  `agentops.yaml` references it via `prompt_file`.
- `agentops workflow analyze` selects AgentOps cloud eval in Foundry
  with `deploy: prompt-agent`.
- `agentops workflow generate --kinds pr,dev --deploy-mode prompt-agent
  --doctor-gate critical --force` produced a PR workflow that stages a
  candidate in the dev project and a dev deploy workflow that records
  the deployment.
- You ran a green PR + dev deploy at least once. The deploy artifact
  `foundry-agent.json` exists with a `prompt_sha256` and `git_sha`.
- You pushed an intentional regression. The PR was blocked twice — once
  by the eval threshold gate and once by Doctor's
  `--severity-fail critical` step. You can explain that either gate is
  sufficient on its own.
- You restored the prompt, the PR returned to green, the merge ran the
  dev deploy again, and the new `foundry-agent.json` shows the recovered
  prompt's SHA.
- `agentops doctor --evidence-pack` writes
  `.agentops/release/latest/evidence.md`, and the GitHub run summary
  shows its Doctor finding summary.
- Cockpit opens and links the repo-side readiness view back to Foundry
  for both sandbox and dev.

Where to go next:

- Add `qa` and `prod` deploy workflows with
  `agentops workflow generate --kinds qa,prod --deploy-mode prompt-agent --force`.
  Each environment needs its own Foundry project and its own seeded
  `travel-agent:1`.
- Add the scheduled Doctor workflow with
  `agentops workflow generate --kinds doctor --force`.
- Promote vetted production traces into the regression dataset with
  `agentops eval promote-traces` to grow the gate over time.
