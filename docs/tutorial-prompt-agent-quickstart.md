# Tutorial: Foundry Prompt Agent (sandbox → dev with PR gate)

Use this tutorial when you want a Foundry-managed prompt agent referenced as
`name:version`. The example creates a small **Travel Agent** in Foundry and
then uses AgentOps to add repo-side readiness, a PR gate that catches
regressions before merge, a `dev` deploy workflow, Doctor evidence, and
Cockpit.

This path validates the Foundry-native multi-environment route:

- Foundry owns the prompt agent runtime, cloud evaluation execution, traces,
  Rubric evaluator definitions, traces, Guardrails, red-team scans, and
  Operate dashboards in **each environment**.
- AgentOps owns repo-side readiness: source-controlled prompts, CI gates,
  Doctor blocking, release evidence, threshold enforcement, ASSERT/ACS evidence
  references, and Cockpit.

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
| `azd ai agent eval` / `microsoft/ai-agent-evals` | Foundry-native eval paths. AgentOps can wrap azd `eval.yaml` recipes (`execution: azd`) or invoke Foundry cloud eval directly; in both cases AgentOps normalizes threshold evidence and release artifacts. |
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
| You can create **two** Foundry projects in the same Azure subscription (or have two existing projects you can use). | The tutorial uses a sandbox project for authoring and experimentation plus a shared dev project for the PR gate. You only need to publish the agent in sandbox — CI auto-bootstraps it in dev (and later qa / prod). |
| You can publish a prompt agent in the **sandbox** Foundry project. | The tutorial seeds `travel-agent:2` only in sandbox (Foundry portal typically numbers the first published version `:2`, not `:1`). Dev / qa / prod start empty; the prompt-agent deploy workflow creates the first version in those projects automatically using `prompt_agent_bootstrap` defaults plus `prompt_file`. |
| The **same model deployment name** (for example `gpt-4o-mini`) exists in every Foundry project you plan to deploy to. | `prompt_agent_bootstrap.model` is a single value reused for every environment. If dev does not have that deployment, the first auto-bootstrap fails. |
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
    │  travel-agent:2 (your first publish      │  (empty — no agent here yet;
    │   in sandbox; Foundry portal numbers     │   CI auto-creates the agent
    │   it starting from :2)                   │   on the first deploy via
    │  travel-agent:3,4,5,... (free saves)     │   prompt_agent_bootstrap; the
    │                                          │   number Foundry assigns there
    │                                          │   is environment-local)
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
2. **You only author the agent in sandbox.** Dev, qa, and prod start
   empty. When the prompt-agent deploy workflow runs against an empty
   environment, it reads `prompt_agent_bootstrap` from `agentops.yaml`
   plus `prompt_file`, then creates the first version of the agent
   automatically in that environment. You never seed dev / qa / prod by
   hand.
3. **Cross-environment identity is the SHA, not the number.** AgentOps
   embeds `agentops.prompt_sha256` and `agentops.git_sha` into every
   Foundry version it creates, and writes the same identifiers into the
   per-environment deploy artifact `foundry-agent.json`. When you ask
   "is the same prompt running in sandbox, dev, and prod?", you compare
   SHAs, not version numbers. The version numbers will differ.

The longer walkthrough of that identity story is in step 15, when you
have a real `foundry-agent.json` artifact to open.

## Journey you will exercise

| Step | Main tool | What you do | AgentOps role |
|---|---|---|---|
| Create two Foundry projects | Foundry portal (or `microsoft-foundry` skill) | Create `travel-agent-sandbox` (where you author) and `travel-agent-dev` (left empty — CI seeds it). | No ownership; AgentOps consumes the published baseline from sandbox and bootstraps dev. |
| Author in sandbox | Foundry playground | Iterate on the prompt safely in sandbox Foundry. | Optional spot-check via local `agentops eval run`. |
| Promote the prompt to git | Editor | Copy validated instructions into `.agentops/prompts/travel-agent.md`. | The CI gate reads this file. |
| First green PR + dev deploy | GitHub Actions + Foundry dev project | Push prompt, open PR, watch CI auto-bootstrap the first version of `travel-agent` in dev from `prompt_agent_bootstrap` (the dev project is still empty at this point), evaluate it, run Doctor; merge; deploy lands in dev. | Owns the gate, the bootstrap-on-first-deploy, the threshold decision, the Doctor blocking step, the deploy artifact, and the release evidence. |
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
python -m pip install "agentops-accelerator[foundry,agent] @ git+https://github.com/Azure/agentops.git@develop"
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

> **Enterprise provisioning option.** This quickstart creates only the Foundry
> resources needed for the video path. For a fuller Azure baseline with
> networking, identity, security, and operations patterns, see
> [Azure AI Landing Zone](https://aka.ms/ailz).

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

#### Path A follow-up — grant agent-build and data-plane access manually

Creating a project through the portal only assigns you `Foundry User` **at
the project scope**. In the Foundry UI, creating/building agents can also
require `Foundry User` on the parent Foundry / AI Services resource. Some
portal screens still use the previous role name, `Azure AI User`, while the
Azure RBAC role name is now `Foundry User`. If that role is missing, the portal
blocks step 4 with:

```text
You don't have permission to build agents in this project.
To get access, please ask your administrator to assign you the Azure AI User role.
```

You also need `Cognitive Services OpenAI User` for the OpenAI data-plane actions
that live on the parent AI Services *account* — the chat-completions call that
backs every AI-assisted evaluator and every cloud-eval grader. Even `Owner` on
the subscription is not enough: the built-in `Owner` role definition has
`actions: ["*"]` but `dataActions: []`, so it grants full control plane and zero
data plane on Cognitive Services accounts.

Skipping the OpenAI role is what causes the eval grader to fail later with::

    PermissionDenied: The principal `<your-objectId>` lacks the required
    data action `Microsoft.CognitiveServices/accounts/OpenAI/deployments/
    chat/completions/action` to perform `POST /openai/deployments/...`

Run these assignments once per AI Services account that hosts a Foundry project you
will build in or evaluate against. Cloud evaluations run server-side: the agent
call and graders may authenticate as Foundry/Azure AI managed identities, not
only as your signed-in user. Assigning the OpenAI role only to your user can
still leave some graders failing with `AuthenticationError`. Replace
`<resource-group>` with the resource group you chose above, for example
`rg-agentops-travel-<your-alias>`, and `<account-name>` with the parent Foundry /
AI Services account name.

```powershell
$subscriptionId = az account show --query id -o tsv
$resourceGroup = "<resource-group>"
$accountName = "<account-name>"
$accountScope = az cognitiveservices account show `
  --resource-group $resourceGroup `
  --name $accountName `
  --query id -o tsv
$userObjectId = az ad signed-in-user show --query id -o tsv

# User building agents in Foundry and running local commands / cloud evals.
az role assignment create `
  --assignee $userObjectId `
  --role "53ca6127-db72-4b80-b1b0-d745d6d5456d" `
  --scope $accountScope

az role assignment create `
  --assignee $userObjectId `
  --role "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd" `
  --scope $accountScope

# Foundry/Azure AI managed identities used by server-side agent/evaluator calls.
az resource list -g $resourceGroup `
  --query "[?identity.principalId!=null].identity.principalId" -o tsv |
  ForEach-Object {
    az role assignment create `
      --assignee-object-id $_ `
      --assignee-principal-type ServicePrincipal `
      --role "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd" `
      --scope $accountScope
  }
```

Repeat the command with the `travel-agent-dev` resource group if the dev
project lives in a different RG.

> **Give the assignment a few minutes to propagate.** Data-plane role
> assignments on the AI Services account do **not** take effect
> instantly — propagation to the Foundry evaluator workers can take
> several minutes (occasionally up to ~15). The cloud eval runs each
> grader as an independent worker that authenticates separately, so the
> **first run right after granting the role may show intermittent
> `AuthenticationError` on a subset of graders and report
> `Threshold status: FAILED` even when every threshold is green** (no
> single row had all graders succeed). This is a grader execution
> failure, not a quality regression. Wait a few minutes and re-run
> `agentops eval run` — once propagation finishes, every grader scores
> and the gate passes.

AgentOps Doctor will detect the missing assignment in a future release,
but until then this is a manual one-time setup step per new environment.

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

Use these Azure container/resource names unless I say otherwise:
- Resource group: rg-agentops-travel-<your-alias>
- Azure AI Foundry resource / AI Services account: foundry-agentops-travel-<your-alias>
- Region: East US 2
- Model deployment name in both projects: gpt-4o-mini

1. travel-agent-sandbox - the authoring and experimentation space
   (used by me, or shared with my team for iteration). I will publish
   the seed prompt agent here manually in the next step (Foundry will
   typically assign it version :2, since the unpublished draft counts
   as :1).
2. travel-agent-dev - shared dev environment used by CI as the PR gate
   target and the dev deploy target. Leave this project EMPTY. CI will
   auto-create the first agent version here on the first deploy using
   AgentOps' prompt_agent_bootstrap defaults.

For each project, please:
- Create the project under the resource group and Foundry resource named above.
- Make sure the SAME chat-capable model deployment name is available in
  both projects (gpt-4o-mini works). Same name is important: AgentOps
  uses a single bootstrap model value for every environment.
- Attach or create an Application Insights resource for telemetry,
  starting with the dev project.
- Grant or verify `Foundry User` access for my signed-in user on the parent
  Foundry / AI Services account so I can build agents in the
  Foundry UI. Some portal screens still call this role `Azure AI User`.
- Grant or verify `Cognitive Services OpenAI User` data-plane access for my
  signed-in user and for the Foundry/Azure AI managed identities that will call
  the model deployment during server-side evaluations.

Show me the planned changes and the resulting endpoints before applying.
```

Replace `<your-alias>` with a short unique suffix such as your initials,
GitHub handle, or a date (`pl`, `contoso-dev1`, `video-0604`). This matters
when multiple people run the tutorial in the same subscription: resource group
names must be unique within that subscription, Foundry / AI Services resource
names should be unique enough to avoid Azure naming conflicts, and project names
must be unique inside the Foundry resource. The model deployment name
`gpt-4o-mini` does **not** need to be globally unique, but it must be the same
in both tutorial projects. For a recorded tutorial, one shared resource group is
easiest because RBAC and cleanup happen in one place; production teams may split
resource groups by environment.

Before continuing, check that the skill's plan/output explicitly lists
`Foundry User` (or the previous portal label, `Azure AI User`) for your signed-in
user and `Cognitive Services OpenAI User` for your signed-in user plus the
Foundry/Azure AI managed identities. If it only created the projects and model
deployments, ask the skill to add or verify those role assignments before you
move to step 4.

## 4. Seed `travel-agent` in the sandbox project

You only author the agent in **one place**: your sandbox Foundry
project. Dev (and later qa / prod) start empty. The first time the
prompt-agent deploy workflow runs against an empty environment, it reads
`prompt_agent_bootstrap` from `agentops.yaml` plus `prompt_file` and
creates the first version automatically. You do **not** repeat this
manual step for every environment.

In the **sandbox** project only:

1. Open the [Azure AI Foundry portal](https://ai.azure.com) and select
   the `travel-agent-sandbox` project.
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

5. Save and publish the agent. Foundry typically assigns version `2`
   on first publish (`travel-agent:2`) because the unpublished draft
   counts as `:1`. **Note the exact version Foundry assigned** — you
   will paste this number into `agentops.yaml` in section 9. The dev
   project still has no agent at this point — that is expected.

> **Why not seed dev too?** Forcing the operator to recreate the same
> prompt agent in every environment is exactly the manual drift problem
> AgentOps is here to eliminate. Section 9 adds a `prompt_agent_bootstrap`
> block to `agentops.yaml`; the first PR / deploy run against dev reads
> those defaults plus `prompt_file` and creates the first version of
> the agent in dev (the version number Foundry assigns there is
> environment-local, typically `:1` for an SDK-created first version)
> with the same metadata trail (`agentops.prompt_sha256`,
> `agentops.git_sha`). Subsequent runs follow the normal reuse /
> next-version flow.

> **Prompt-as-code captures only the instructions.** Later in the
> tutorial you will commit `.agentops/prompts/travel-agent.md` to git
> and let CI use it as the prompt source. That file does not capture
> the model deployment, parameters (temperature, top-p), tools, or
> other agent settings — those come from `prompt_agent_bootstrap` on
> the first deploy and stay on the Foundry agent definition afterwards.
> Use the same model deployment name in every Foundry project so the
> single `prompt_agent_bootstrap.model` value works everywhere without
> per-environment tweaks. AgentOps will not detect drift in non-prompt
> fields between environments.

## 5. Try the agent in the sandbox playground

Open `travel-agent-sandbox` in the Foundry portal, open `travel-agent:2`
(the version Foundry assigned on first publish), and run a sample in the
playground:

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
in step 18; for now, just confirm there is at least one trace to look at
later.

## 6. Create the travel eval dataset

Create the small JSONL dataset that matches the Travel Agent behavior:

> **Copilot assist:** If you want help expanding or reviewing these rows, ask
> Copilot to use `/skills agentops-dataset`. The skill can propose additional
> edge cases, check that each row has `input` and `expected`, and keep the
> criteria written as reviewable behavior instead of exact answer strings.

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
| Agent | `travel-agent:2` (use the exact version Foundry assigned in section 4) |
| Dataset path | `.agentops/data/travel-smoke.jsonl` |

If the wizard offers starter defaults such as `Agent [my-agent:1]` or
`Dataset path [.agentops/data/smoke.jsonl]`, replace them with the
Travel Agent values above.

Before continuing, verify the saved dataset path. This must point to the
Travel Agent dataset you created in step 6, not the starter
`.agentops/data/smoke.jsonl` file:

```powershell
Select-String -Path agentops.yaml -Pattern '^dataset:'
```

Expected output:

```text
dataset: .agentops/data/travel-smoke.jsonl
```

If it still says `.agentops/data/smoke.jsonl`, fix it now:

```powershell
(Get-Content agentops.yaml) `
  -replace '^dataset:.*$', 'dataset: .agentops/data/travel-smoke.jsonl' |
  Set-Content -Encoding utf8 agentops.yaml
```

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
agent: travel-agent:2
dataset: .agentops/data/travel-smoke.jsonl
```

> **Why `version: 1`?** This is the AgentOps configuration schema version, not
> the Foundry agent version. Keep it as `1`; the agent version is the suffix in
> `agent: travel-agent:2`.
>
> **App Insights — should already be wired from step 3.** Step 3
> (both Path A and Path B) instructs you to attach an Application
> Insights resource to the **dev** Foundry project when you create it,
> so by default this is already done and no manual env variable is
> needed. AgentOps auto-discovers the connection string through the
> Azure AI Projects SDK at runtime.
>
> Verify in 10 seconds: open <https://ai.azure.com> → **`travel-agent-dev`**
> project → left rail **Tracing** (sometimes under "Observability" /
> "Monitoring"). If you see a linked Application Insights resource with
> a "Copy connection string" button, you are done — skip the optional
> subsection in section 8.
>
> Only set `APPLICATIONINSIGHTS_CONNECTION_STRING` manually if the
> Tracing tab shows "Connect Application Insights" (the resource was
> not created in step 3), if your identity cannot read the linked
> resource at runtime, or if you intentionally want telemetry to go to
> a different resource. Section 8 covers all three cases.

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

Replace the endpoint with your real dev project endpoint from step 3.

### Optional: also set the dev project's App Insights connection string

In most walkthroughs you can **skip this subsection**. Step 3 already
attached an Application Insights resource to the **`travel-agent-dev`**
Foundry project (either you did it manually in Path A or the
`microsoft-foundry` skill did it in Path B, following the explicit
"Attach or create an Application Insights resource for telemetry,
starting with the dev project" instruction in the step 3 prompt), and
AgentOps auto-discovers that connection string at runtime through the
Azure AI Projects SDK. No env variable required.

**Quick verification (10 seconds):**

Open <https://ai.azure.com> → left rail **Admin** → select the
**`travel-agent-dev`** project → **Connected resources**. Make sure you
are checking the **dev** project, not the sandbox project you used to
build the prompt agent. One of two things will be true:

| What you see | What it means | What to do |
|---|---|---|
| An `appinsights` row with category `AppInsights` | The resource exists and is connected to the dev project. Auto-discovery will pick it up. | **You are done.** Skip the rest of this subsection and continue to section 9. |
| No App Insights row in **Connected resources** | The resource was not connected in step 3. | Click **Add connection**, connect or create an Application Insights resource for the dev project, or paste a connection string manually. |

**If Connected resources does not show App Insights**, the fastest fix is
to connect one through the Foundry portal itself: click **Add connection**
and either pick an existing Application Insights resource or create one
in the same resource group as the dev project. Once an `appinsights` row
appears under **Connected resources**, you can again skip the manual env
variable — auto-discovery will pick it up.

**Only if you specifically want to override which resource telemetry
goes to** (advanced case, e.g. you have a dedicated observability
resource group), grab the connection string and paste it into
`.azure\dev\.env`. Pick whichever path is easiest:

**Path A — Azure AI Foundry portal (recommended, no Azure Portal
hopping):**

1. On the **Tracing** tab of `travel-agent-dev`, click the "Copy
   connection string" button next to the linked Application Insights
   resource.

**Path B — Azure Portal:**

1. Open <https://portal.azure.com> and search for the Application
   Insights resource attached to your dev Foundry project (it is
   typically created alongside the project and shares its name prefix).
2. On the **Overview** blade, the right-hand "Essentials" panel shows a
   **Connection String** field. Click the copy icon next to it.

**Path C — Azure CLI (one command):**

```powershell
az monitor app-insights component show `
  --app <appinsights-name> `
  --resource-group <resource-group> `
  --query connectionString -o tsv
```

Once you have the value, append it to `.azure\dev\.env`:

```text
APPLICATIONINSIGHTS_CONNECTION_STRING=<paste-the-string-here>
```

The full string starts with `InstrumentationKey=...` and includes
`IngestionEndpoint=...`; paste the whole thing on one line.

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
`agentops eval run` use the sandbox project. CI workflows in step 13
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

Then tell `agentops.yaml` where to find the file and add
`prompt_agent_bootstrap` so CI can auto-create the agent in dev (and
later qa / prod) on the first deploy:

```yaml
version: 1
agent: travel-agent:2
dataset: .agentops/data/travel-smoke.jsonl
prompt_file: .agentops/prompts/travel-agent.md
prompt_agent_bootstrap:
  model: gpt-4o-mini
  description: "Helps plan short trips and explains tradeoffs."
```

The `agent: travel-agent:2` value is now a **seed pointer**. CI uses it
to look up the existing agent in the current environment's Foundry
project:

- If the agent exists at that exact version (the sandbox case, and
  every environment after it has caught up), CI copies the looked-up
  definition (model deployment, name, kind), replaces the instructions
  with the contents of `prompt_file`, and either re-uses the same
  Foundry version (when the prompt is byte-identical) or lets Foundry
  auto-create the next number in that project (when it differs).
- If the agent does **not** exist at that version (the empty dev / qa /
  prod case on the first deploy, or when the env's version numbering
  has not yet caught up to the seed), CI reads `prompt_agent_bootstrap`
  for the model deployment (and optional `description`,
  `model_parameters`, `tools`) and creates a new version of the agent
  from those defaults plus `prompt_file`. The deploy artifact for that
  run records `action: "bootstrapped"`. Because the SDK auto-increments
  version numbers per project, the bootstrap may fire on the first one
  or two deploys per environment before the env catches up to the seed;
  that is expected. Subsequent deploys follow the reuse-or-create flow
  above and ignore the bootstrap block.

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

> **Keep `project_endpoint` out of `agentops.yaml` for multi-env work.**
> When `project_endpoint` is set in `agentops.yaml`, it wins over the
> `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` environment variable that azd
> environments rely on. That makes every command target the same
> Foundry project regardless of which env is active, which defeats the
> sandbox / dev / qa / prod split. The wizard does the right thing by
> default (it writes the endpoint to `.azure/<env>/.env`, not to
> `agentops.yaml`). If you ever copied the endpoint into `agentops.yaml`
> manually, delete it now.

## 10. Initialize the azd eval recipe and run the smoke gate

Confirm the eval runner the workflow generator will use:

```powershell
agentops workflow analyze --format text
```

For `agent: name:version` plus `prompt_file`, AgentOps detects the
prompt-agent deploy mode. The recommendation may still show AgentOps
cloud eval in Foundry before you initialize the azd recipe:

```text
Recommendation
  deploy          prompt-agent
  evaluate        AgentOps cloud eval in Foundry
  workflow edits  not needed - generated workflow should work as-is
  Copilot skills  installed - available for workflow adaptation handoff
```

That confirms the deployment side is wired correctly. Now let AgentOps
prepare the native azd eval recipe:

```powershell
agentops eval init
```

This creates `azure.yaml` and `src/travel-agent/agent.yaml` if they are
missing, enriches the active `.azure/sandbox/.env` with the Foundry
metadata azd expects, writes an azd-friendly dataset copy with the
`query` field derived from your AgentOps `input` values, asks azd to
generate the eval recipe, and records it in `agentops.yaml`:

```yaml
execution: azd
eval_recipe: src/travel-agent/eval.yaml
```

Use `--force` only when you intentionally want to regenerate an existing
`eval.yaml`. For the normal flow, run it without `--force`.

Run the gate locally:

```powershell
agentops eval run
```

You should see `execution: azd` and `Threshold status: PASSED`. The raw
azd run details are kept under `.agentops/results/latest/` alongside
AgentOps' normalized `results.json` and `report.md`.

## 11. Harden the gate: conversation-aware dataset and rubric

The smoke gate proves the workspace works. Before generating CI, harden
the same gate with multi-turn rows that line up with future trace replay
and a rubric that scores the Travel Agent's product behavior.

### Create a synthetic multi-turn dataset

Define a small set of synthetic multi-turn rows. They are not claiming
the agent already produced the assistant turns verbatim — they define
controlled conversation scenarios the next response must handle.

> **Copilot assist:** `/skills agentops-dataset` can draft these
> conversation scenarios. Ask for synthetic multi-turn rows that keep the
> conversation summary in `input`, preserve the structured turns in
> `messages`, and write `expected` as acceptance criteria.

Keep the important context inside `input` (the field AgentOps maps to the
azd `query`) and keep `messages` alongside it so the dataset matches the
shape of future trace-derived rows.

```powershell
@'
{"input":"Conversation so far: the user wants to visit Rome with two kids. The assistant asked how many days and what pace they prefer. The user answered: three days, moderate pace, museums and food. Now plan the trip.","expected":"The agent should preserve the family-with-kids constraint, propose a practical three-day Rome itinerary, include transit/rest pacing, and avoid claiming it can book live reservations.","messages":[{"role":"user","content":"We want to visit Rome with two kids."},{"role":"assistant","content":"How many days do you have and what pace do you prefer?"},{"role":"user","content":"Three days, moderate pace, museums and food."}]}
{"input":"Conversation so far: the user needs a low-budget food weekend. The assistant asked whether they are choosing between specific cities. The user answered: Lisbon or Seattle. Now compare those options.","expected":"The agent should compare both destinations, mention budget tradeoffs, food activities, transit/weather notes, and avoid unsupported price or booking claims.","messages":[{"role":"user","content":"I need a low-budget food weekend."},{"role":"assistant","content":"Are you choosing between specific cities?"},{"role":"user","content":"Lisbon or Seattle."}]}
'@ | Set-Content -Encoding utf8 .agentops\data\travel-conversations.jsonl
```

Point `agentops.yaml` at it:

```yaml
dataset: .agentops/data/travel-conversations.jsonl
dataset_kind: multi-turn
```

Re-init the recipe and run the gate again:

```powershell
agentops eval init --force
agentops eval run
```

When it passes, `results.json` records `execution: azd`, the evaluator
list, the multi-turn dataset kind, and the threshold results.

> **What did this gate test?** Individual synthetic conversation-context
> turns, not the Foundry portal **Full conversations** preview. AgentOps
> uses `messages` to preserve the conversation shape and
> `dataset_kind: multi-turn` to make the release evidence
> conversation-aware. For end-to-end full-conversation evaluation, use
> the optional Foundry path below.

### Optional: Full conversations evaluation in the Foundry portal

This is a Foundry-native deeper review path, not a required step in the
automated release gate. The automated gate for this tutorial stays
AgentOps + azd + Doctor evidence.

| If you have... | Use this dataset source |
|---|---|
| No production conversations yet | Start with the synthetic rows from `.agentops/data/travel-conversations.jsonl`. |
| A deployed agent with traffic | Use Foundry traces or exported conversation logs, then convert/select those conversations as the Foundry evaluation dataset. |
| A curated review set from your team | Upload that approved conversation dataset in the format the portal asks for. |

For this tutorial, start with the synthetic file you just created. Later,
replace that with real Foundry traces or approved conversation logs.

1. Open your Foundry project in <https://ai.azure.com>.
2. Go to **Evaluation** and create a new evaluation.
3. Choose the **Full conversations (preview)** scope.
4. Select or upload the conversation dataset you want Foundry to evaluate.
5. Run the evaluation and review the result in Foundry.

Reference: [Run evaluations from the Microsoft Foundry portal](https://learn.microsoft.com/azure/foundry/how-to/evaluate-generative-ai-app#create-an-evaluation).

### Add the Travel Agent rubric to the release gate

A normal evaluator checks a general quality signal (coherence, fluency).
A rubric evaluator is still usually an LLM-as-a-judge evaluation, but the
judge is guided by product-specific criteria you define for this agent.

For the Travel Agent, the rubric asks:

| Rubric dimension | What the judge checks |
|---|---|
| Task success | Did the answer complete the user's travel-planning goal? |
| Constraint following | Did it preserve constraints such as kids, budget, trip length, and pace? |
| Safe booking behavior | Did it avoid claiming live bookings, confirmations, or prices it cannot verify? |

The `eval_model` in the generated azd recipe is the judge. The rubric
file tells it which dimensions to score, and the thresholds in
`agentops.yaml` decide whether the gate passes.

Fill in two kinds of real names: the rubric evaluator name and the rubric
dimension names. Do not invent values — both must come from files
`agentops eval init` already generated on disk.

> **About the auto-generated evaluator.** When you ran `agentops eval
> init`, azd seeded `src/travel-agent/eval.yaml` with two kinds of
> evaluators: built-ins like `builtin.coherence` and `builtin.fluency`
> (general response-quality checks) plus a local rubric evaluator —
> typically `name: smoke-core` — whose `local_uri` points at a JSON file
> with rubric dimensions specific to this Travel Agent. That local
> evaluator is the hook AgentOps `rubrics:` bind to. You will reference
> its `name:` and its dimension `id`s in the next two steps.

**1. Find the evaluator name.** Open `src/travel-agent/eval.yaml` and
look under `evaluators:` for the entry with a `local_uri`:

```yaml
evaluators:
    - builtin.coherence
    - builtin.fluency
    - name: smoke-core
      version: "9"
      local_uri: evaluators\smoke-core\rubric_dimensions.json
```

The value you need is the `name:` of that entry. In this example,
`smoke-core`.

**2. Find the dimension names.** Open the file the `local_uri` points to
(e.g. `src/travel-agent/evaluators/smoke-core/rubric_dimensions.json`).
Each object's `id` is a metric name azd will emit:

```json
[
  { "id": "correct_itinerary", "description": "...", "weight": 9 },
  { "id": "clear_practical_notes", "description": "...", "weight": 5 },
  { "id": "user_satisfaction", "description": "...", "weight": 4 },
  { "id": "adherence_to_constraints", "description": "...", "weight": 3 },
  { "id": "itinerary_clarity", "description": "...", "weight": 2 },
  { "id": "general_quality", "description": "...", "weight": 5,
    "always_applicable": true }
]
```

For this quickstart the three dimensions that map to Task success /
Constraint following / Safe booking are:

| Dimension intent | Dimension `id` to use |
|---|---|
| Task success | `correct_itinerary` |
| Constraint following | `adherence_to_constraints` |
| Safe booking behavior | `clear_practical_notes` |

**3. Add `rubrics:` and `thresholds:` to `agentops.yaml`:**

```yaml
rubrics:
  - name: travel-concierge-quality
    evaluator: smoke-core
    description: Scores the Travel Agent against the intended product behavior.
    dimensions:
      - name: correct_itinerary
        description: Completes the user's travel-planning goal across the conversation.
        weight: 0.5
      - name: adherence_to_constraints
        description: Carries user constraints such as kids, budget, duration, and pace.
        weight: 0.3
      - name: clear_practical_notes
        description: Avoids claiming live bookings, confirmations, or prices it cannot verify.
        weight: 0.2

thresholds:
  smoke-core: ">=0.6"
  coherence: ">=0.6"
  fluency: ">=0.6"
```

> **Why threshold the evaluator, not the dimensions?** `azd ai agent
> eval` emits one aggregate pass-rate metric per evaluator
> (`coherence`, `fluency`, `smoke-core`), not one metric per rubric
> dimension. The dimension `id`s live inside the local rubric file and
> guide the judge's prompt, but azd does not surface them as separate
> metrics today, so thresholds bind to the evaluator names azd actually
> reports. The `rubrics:` block above is still recorded in
> `results.json` and the release evidence pack as documentation of what
> the judge was asked to score. Values are pass rates in `0..1` (e.g.
> `">=0.6"` means at least 60% of rows passed the evaluator).

**4. Regenerate the recipe and re-run the gate:**

```powershell
agentops eval init --force
agentops eval run
```

When this passes, the gate enforces both the conversation-context dataset
and the Travel Agent rubric pass-rate threshold. If a threshold key is
wrong, AgentOps cannot bind it to an emitted metric — open
`.agentops/results/latest/results.json` and look at
`aggregate_metrics` to see exactly which evaluator names azd produced
for this recipe.

## 12. Add ASSERT and Red Team to the release gate

The eval gate proves quality. Two additional release-readiness signals
deserve to run inside the same loop:

- **ASSERT** (open-source `assert-ai`) — turns natural-language policies
  into executable behavior tests (prompt injection, jailbreak,
  hallucination, PII leak, unauthorized tool use). Repo:
  <https://github.com/responsibleai/ASSERT>.
- **AI Red Teaming** (Foundry agent, PyRIT-backed) — generates
  adversarial prompts across risk categories (violence, hate, self-harm,
  sexual) and applies attack strategies (base64, rot13, morse) to surface
  safety regressions. Docs:
  <https://learn.microsoft.com/azure/ai-foundry/concepts/ai-red-teaming-agent>.

AgentOps does not reimplement either. It orchestrates them as active CI
steps, gates the pipeline on their results, and writes normalized JSON
summaries that the evidence pack ingests automatically.

### Run ASSERT against the Travel Agent

Install ASSERT and scaffold a minimal eval config:

```powershell
pip install assert-ai

New-Item -ItemType Directory -Force .\assert | Out-Null
@'
suite_id: travel-agent-v1
run_id: ci-tutorial
target:
  type: azure_openai
  deployment: gpt-4o-mini
dimensions:
  - prompt_injection
  - pii_leak
  - jailbreak
num_cases_per_dimension: 5
'@ | Set-Content -Encoding utf8 .\assert\eval_config.yaml
```

Add the `assert:` block to `agentops.yaml`:

```yaml
assert:
  config: ./assert/eval_config.yaml
  fail_on_violations: true
```

Run it through AgentOps:

```powershell
agentops assert run
```

What AgentOps does for you:

1. Verifies `assert-ai` is installed.
2. Invokes `assert-ai run --config ./assert/eval_config.yaml`.
3. Locates the run output under `artifacts/results/<suite>/<run>/`.
4. Parses `metrics.json` and `scores.jsonl` for per-dimension verdicts.
5. Writes a normalized summary at `.agentops/assert/latest.json`.
6. Exits non-zero (code 2) when ASSERT reports any policy violation,
   unless you pass `--no-gate` or set `assert.fail_on_violations: false`.

### Run the AI Red Teaming agent

Install Foundry's Red Team SDK (it ships under an extra of
`azure-ai-evaluation`):

```powershell
pip install "azure-ai-evaluation[redteam]"
```

Add the `redteam:` block to `agentops.yaml`:

```yaml
redteam:
  target:
    model_deployment: gpt-4o-mini
  risk_categories: [violence, hate_unfairness, self_harm, sexual]
  attack_strategies: [base64, rot13, morse]
  num_objectives: 5
  fail_on_attack_success_rate: 0.2  # fail if >20% of attacks succeed
```

Run it:

```powershell
agentops redteam run
```

What AgentOps does for you:

1. Verifies the `RedTeam` Python API is importable.
2. Resolves the target (deployment / agent / endpoint) from the YAML.
3. Calls `RedTeam.scan(...)` with the configured risk categories,
   strategies, and objective count.
4. Aggregates per-category and per-strategy attack-success-rate.
5. Writes a normalized summary at `.agentops/redteam/latest.json` plus
   the raw SDK payload at `.agentops/redteam/raw_summary.json`.
6. Exits non-zero (code 2) when overall attack-success-rate exceeds
   `fail_on_attack_success_rate`, unless you pass `--no-gate`.

> **Heads-up.** Both commands hit live Azure services. Run them against a
> non-production deployment and budget for the cost of the configured
> objective count.

### Pull both into the release evidence pack

Both runners write to well-known paths the evidence pack auto-discovers
(via `assert_path` and `redteam_path` resolution). When you produce the
evidence pack:

```powershell
agentops doctor --workspace . --evidence-pack
```

`evidence.json` and `evidence.md` now include the suite/run id, total
cases, violation counts, attack-success-rate, and SHA-256 hashes for both
artifacts — without claiming AgentOps invented the verdicts. The verdicts
come from ASSERT and PyRIT; AgentOps owns orchestration, normalization,
and gating.

## 13. Generate the PR + dev deploy workflows

> **Pipeline ownership.** This tutorial uses `agentops workflow generate`
> because the workflow is the release-readiness contract: it stages the prompt
> agent, runs eval thresholds, Doctor checks, and writes release evidence. For a
> full `azd` / AI Landing Zone app, you can also use `azd pipeline config` to
> bootstrap the app / infra deployment pipeline, then add AgentOps checks where
> you need release readiness proof.

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
   candidate in the **dev** Foundry project (not sandbox).
   - On the **very first PR**, dev is still empty. The stage step looks
     up `travel-agent:2` and gets a 404. It then reads
     `prompt_agent_bootstrap` from `agentops.yaml` plus `prompt_file`
     and creates a new version of the agent in dev via the Foundry SDK.
     The SDK assigns the version number per-project — typically `:1` in
     an empty project — so the bootstrapped candidate is normally
     `travel-agent:1`. The stage step reports `action: bootstrapped`.
   - On every subsequent PR, dev's version count gradually catches up to
     the sandbox seed (`:2`). Until it does, the stage step keeps
     bootstrapping. Once dev has `travel-agent:2`, the stage step
     switches to the normal lookup path: it reads `travel-agent:2`'s
     definition, replaces the instructions with `prompt_file`, and
     either re-uses the same version (when the prompt is byte-identical
     to the seed) or lets Foundry auto-create the next number. The
     stage step then reports `reused` or `created`.
   In all cases, the workflow writes
   `.agentops/deployments/agentops.candidate.yaml` pointing at the
   staged candidate.
2. **`eval`** — runs `agentops eval run` against the candidate, then
   runs Doctor with `--severity-fail critical`. Because the previous step
   moved the gate to a conversation dataset, the workflow is not just checking a
   single smoke response: it runs the Foundry / azd evaluation recipe against the
   multi-turn Travel Agent rows and writes normalized evidence to
   `.agentops/results/latest/results.json`.

> **Why does the PR workflow stage in dev, not sandbox?** The PR gate
> must evaluate the same target the deploy workflow will use. Sandbox
> is the author's playground and never receives CI traffic. PR
> candidates accumulate in dev over time and may need periodic
> cleanup according to your team's Foundry retention policy; AgentOps
> uses prompt SHAs and git SHAs as the durable identity, not old
> candidate version numbers.

The dev deploy workflow stages a candidate (same logic), evaluates it,
summarizes the deployment via `prompt_deploy summarize`, and uploads
`.agentops/deployments/foundry-agent.json` as a workflow artifact.
The deploy gate uses the same conversation-aware `agentops eval run`, so the
candidate that lands in dev has already passed the gate reviewers saw on the PR.

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

## 14. Wire CI: GitHub repository + Azure OIDC + dev environment

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
principal has **both** Foundry User access on the **dev** Foundry project
**and** Cognitive Services OpenAI User on the underlying Azure AI Services
account that hosts the evaluator model (both roles are required — without
the OpenAI User role, the Foundry cloud graders fail with a 401 and every
metric comes back null), and do not set up `qa`, `production`, scheduled
Doctor, or hosted deployment workflows yet.

I am using trunk-based development with `main` as both my trunk and dev
branch. The generator's stock dev-deploy trigger is `push: branches:
[develop]`. Rewrite the `agentops-deploy-dev.yml` (and the matching
`agentops-pr.yml` `pull_request: branches:` list, if it references
`develop`) so they fire on `main` instead. The PR gate must run on PRs
targeting `main`, and the dev deploy must auto-run on push to `main`
after a merge.

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
- **Rewrite the dev deploy trigger to `main`.** The generator emits the
  stock GitFlow defaults (`pull_request: branches: [develop, "release/**",
  main]` on `agentops-pr.yml`, `push: branches: [develop]` on
  `agentops-deploy-dev.yml`). For this trunk-on-`main` tutorial the
  skill should rewrite both so the PR gate fires on PRs into `main` and
  the deploy fires on push to `main`. If the skill skips this rewrite,
  open the two YAML files in `.github/workflows/` and edit the
  `branches:` lists by hand before opening the first PR.
- Verify the OIDC principal has **two** Azure RBAC roles before the first
  run. Both are required and the eval step fails silently (every metric
  returns `null`) if only one is in place:
  - **Foundry User** on the dev Foundry project — Reader alone is not
    enough for the data-plane calls the prompt-agent staging and eval steps
    make.
  - **Cognitive Services OpenAI User** on the underlying Azure AI Services
    account that hosts the evaluator model deployment. Foundry
    `azure_ai_evaluator` graders impersonate the OIDC principal to call
    OpenAI; without this role they fail with a 401 `PermissionDenied`. The
    AgentOps cloud-results parser lifts that error into `results.json` so
    you can see the cause in the artifact, but the workflow still fails
    the gate.

## 15. First green PR → merge → dev deploy

This is the happy path. Before the regression step, you need a clean
green baseline so the rolling-history Doctor checks (regression, drift)
have something to compare against.

The workflow skill in step 14 already committed your local changes,
pushed `main` to the GitHub remote, and dispatched first verification
runs of **both** `agentops-pr.yml` and `agentops-deploy-dev.yml` (via
`workflow_dispatch`, after asking you to approve) so the CI wiring is
verified end-to-end. Open the repo's **Actions** tab and confirm both
runs reached the eval stage:

- `agentops-pr.yml` — `Stage Foundry prompt candidate (PR)` and
  `AgentOps eval (PR gate)` jobs both ran.
- `agentops-deploy-dev.yml` — `stage-candidate`, `eval`, and the
  `Mark candidate as deployed` step all ran (the deploy job uses
  `prompt_deploy summarize`, not a real Foundry promotion — it writes
  the deployment record artifact + workflow summary).

It is **expected** for one or both of these first runs to exit
`threshold_failed` (`exit 2`) when the dev Foundry project starts
empty: the bootstrap path creates a fresh `travel-agent:1` (and, on
the next run, `:2`) in dev and evaluates it against the seed
`agentops.yaml` thresholds, which can miss on first contact. That is
by design, not a CI wiring failure. What you are really verifying at
this point is the plumbing — OIDC, Foundry RBAC, the evaluator
deployment, the staging step, the deploy summary writer — and that
dev now contains a bootstrapped version of the agent.

`agentops-deploy-dev.yml` will fire **again** automatically when you
merge the baseline PR at the end of this section, because the skill
rewrote its trigger from `develop` to `main` in step 14.

If you want to wait on the first PR-workflow verification run from the
terminal instead of the Actions UI:

```powershell
$runId = gh run list --workflow agentops-pr.yml --branch main --limit 1 --json databaseId --jq '.[0].databaseId'
gh run view $runId --web
gh run watch $runId --exit-status
```

What you should see in the **first** PR workflow run, after the
skill's verification dispatches have already touched dev:

1. **Stage Foundry prompt candidate (PR)** job runs first. The
   `prompt_deploy stage` step looks up `travel-agent:2` in the dev
   project. Three outcomes are possible depending on what the skill's
   verification dispatches produced:
   - `action: reused` — dev already has `travel-agent:2` with the
     same instructions as the seed (no new version created).
   - `action: created` — dev has the seed version but with different
     instructions, so Foundry auto-creates the next number (likely
     `travel-agent:3`).
   - `action: bootstrapped` — dev still does not have `travel-agent:2`
     (only `:1`, because the bootstrap can fire `:1` and `:2`
     back-to-back over two runs). The step reads
     `prompt_agent_bootstrap` plus `prompt_file` and creates the next
     SDK-assigned version, then uses it as the candidate.
2. **AgentOps eval (PR gate)** job runs second. It evaluates the
   candidate using cloud eval. Doctor runs with
   `--severity-fail critical`; advisory findings are listed but do not
   fail the job. The first one or two PR runs against a fresh dev
   project can still fail thresholds while bootstrap catches up. After
   that, normal reuse / create flow takes over and the baseline PR
   should go green.

Successive PR runs walk the same three branches above until dev's
version count catches up to the seed (`travel-agent:2`). Once it does,
every PR run hits the normal lookup path:

- If `prompt_file` is byte-identical to the seed's instructions: the
  stage step reports `reused` and uses `travel-agent:2` as the
  candidate (no new version created).
- If `prompt_file` differs: Foundry auto-creates the next number
  (likely `travel-agent:3`) and the stage step reports `created`.

> **Why the bootstrap can fire one or two times per environment.**
> Foundry portal saves and SDK creates can start numbering at different
> values. The portal counts unpublished drafts (so `:1` is consumed
> before you publish), while the SDK starts at `:1` in an empty
> project. As long as you have not yet introduced a hand-authored seed
> into a new environment, the first one or two CI runs there will keep
> bootstrapping until the environment's version count reaches the seed
> value. After that, normal reuse / create flow takes over. This is
> fine — `prompt_sha256` + `git_sha` are the durable identity, not the
> per-project version numbers.

Now open a feature branch, modify a non-functional file (or just rerun
the workflow), open a PR, and merge it once green:

```powershell
git switch -c chore/agentops-baseline
git commit --allow-empty -m "Baseline AgentOps run"
git push -u origin chore/agentops-baseline
gh pr create --base main --head chore/agentops-baseline --title "Baseline AgentOps run" --body "First green PR to establish history."
```

Open the PR in GitHub. The PR check runs the same staging + eval flow.
Whether this baseline PR goes green on the first try depends on how
many bootstrap rounds the dev project has already absorbed (from the
skill's verification dispatches plus any failed PRs). Once bootstrap
catches up to the seed and the prompt is stable, the PR goes green —
re-run the workflow on the PR if needed. Then merge.

After the merge, the **AgentOps deploy (dev)** workflow runs
automatically on `main` (the skill rewrote its trigger from `develop`
to `main` in step 14 because this tutorial uses trunk-based flow).
This is the **second** deploy-dev run for this repo — the first was
the skill's verification dispatch in step 14. It stages the candidate
(by this point most likely `action: reused` or `created`), evaluates
it, runs `prompt_deploy summarize` to write the dev deployment summary,
and uploads the deployment artifact.

Open the deploy run and download the `foundry-agent-dev-deployment`
artifact. Inside, open `foundry-agent.json`. In the **steady-state**
case (the most common — the seed `travel-agent:2` already exists in
dev and matches the prompt the PR shipped), the file looks like
this — note the actual field names AgentOps writes:

```json
{
  "version": 1,
  "type": "foundry_prompt_agent_deployment",
  "environment": "dev",
  "action": "reused",
  "agent_name": "travel-agent",
  "source_agent": "travel-agent:2",
  "candidate_agent": "travel-agent:2",
  "source_version": "2",
  "candidate_version": "2",
  "project_endpoint": "https://<your-resource>.services.ai.azure.com/api/projects/travel-agent-dev",
  "prompt_file": "/home/runner/work/<your-repo>/<your-repo>/.agentops/prompts/travel-agent.md",
  "prompt_sha256": "9727437db863b00d52bc8ef1f314b70ed22e3e562f5a3a1f9dd68e26f7ea0975",
  "eval_config": "/home/runner/work/<your-repo>/<your-repo>/.agentops/deployments/agentops.candidate.yaml",
  "created_at": "2026-05-30T17:57:53.135435+00:00",
  "git_sha": "3078df74c3b18625553dec8ecd4ed4282f1ca1ca",
  "workflow_url": "https://github.com/<owner>/<your-repo>/actions/runs/26690922142",
  "foundry_agent_version_id": "travel-agent:2"
}
```

In the steady-state, `source_agent` and `candidate_agent` are
**identical** (`travel-agent:2`) because the dev project already had
`travel-agent:2` with the same instructions as the PR's `prompt_file`,
so `prompt_deploy stage` reported `action: reused` and nothing new
was created. The `prompt_file` and `eval_config` paths are absolute
because they are resolved inside the GitHub Actions runner workspace
(`/home/runner/work/<your-repo>/<your-repo>/...`).

`action` will be one of:

- **`reused`** — dev already had `travel-agent:2` with byte-identical
  instructions. No new Foundry version was created. (Steady-state and
  most-common case.)
- **`created`** — dev had `travel-agent:2` but with **different**
  instructions, so Foundry auto-created the next number (e.g.
  `travel-agent:3`). `candidate_agent` would then be `travel-agent:3`.
- **`bootstrapped`** — dev did not yet have `travel-agent:2` at all,
  so the stage step fell back to `prompt_agent_bootstrap` defaults
  plus `prompt_file` and asked the SDK to create the first version.
  In a fresh, empty dev project the SDK starts at `:1`, so you would
  see `candidate_agent: "travel-agent:1"` and `candidate_version: "1"`
  while `source_agent` still reports the seed (`travel-agent:2`). The
  two numbers stay different until subsequent runs catch dev up to
  the seed.

That `prompt_sha256` + `git_sha` pair is what the mental-model diagram
at the start of the tutorial referred to as **cross-environment
identity**. When you later add qa and prod deploys, each environment
will have its own `foundry-agent.json` with possibly different
`candidate_agent` version numbers but the **same** `prompt_sha256` and
`git_sha` whenever they are running the same release.

> **Foundry version numbers may differ between the PR and the deploy.**
> The PR workflow and the deploy workflow each stage independently
> against whatever the current seed (`travel-agent:2`) looks like at the
> moment they run. If the seed's instructions did not change between PR
> and merge, both runs typically reuse or create the same version. If
> another PR was staged in between, the version numbers may interleave.
> AgentOps deduplicates against the seed, not against all prior
> candidate versions, so two distinct PRs with the same prompt content
> can each create their own version. The durable identifier is
> `prompt_sha256`, not the integer suffix.

## 16. Regression PR — eval gate AND Doctor blocking

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
> runs to compute the baseline. The baseline run in step 15 plus this
> regression run should be enough, but if you skipped the baseline,
> Doctor may only emit lower-severity findings. Re-run the green
> workflow once on `main` to seed history, then push the regression
> branch again.

The lesson: this PR is blocked at PR time, before any reviewer touches
it, and the reason is in the GitHub run summary — not buried in a
post-deploy production alert.

## 17. Fix and redeploy

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

## 18. Brief observability checkout (Foundry side)

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

## 19. Sync local evidence and create the release evidence pack

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

In a fresh tutorial workspace it is normal to see warnings for scheduled CI
(you only generated `pr` and `dev`), continuous evaluation, qa/prod
deploys, explicit thresholds, or red-team/governance evidence. Treat those as the
hardening backlog. The eval gates and the dev deploy loop are
production-ready.

If you want to show the governance evidence path in the video, keep it as a
short optional callout:

```powershell
/skills agentops-governance
```

Use that skill to draft or review pointers to ASSERT policies, ACS contracts,
Guided Guardrail review notes, and red-team evidence indexes. AgentOps records
the paths, SHA-256 hashes, and ACS checkpoint coverage in
`.agentops\release\latest\evidence.json`; ASSERT execution, ACS enforcement,
Guardrail setup, and red-team scans still happen in their owning tools.

## 20. Open Cockpit

```powershell
agentops cockpit --workspace .
```

Open the local URL printed by the command. The Cockpit should show
Foundry connection (sandbox by default; you can switch in the URL),
AgentOps cloud-eval readiness, Doctor findings, release evidence, the
PR and dev deploy CI pipelines, and next actions.

## Success criteria

You are done when:

- Two Foundry projects exist (`travel-agent-sandbox`, `travel-agent-dev`).
  Sandbox has a hand-published `travel-agent` seed (normally `:2` after
  first publish in the portal). Dev started empty and was bootstrapped
  by CI on the first one or two deploys; the version number in dev is
  environment-local.
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
- Optional safety runners are either skipped (no Doctor noise) or wired in:
  `assert:` to run `agentops assert run`, and `redteam:` to run
  `agentops redteam run`. Both write normalized JSON under `.agentops/` that
  the evidence pack ingests automatically. Pre-existing `assert_path`,
  `acs_path`, `redteam_path` references for evidence-only hash/status are
  still honored.
- Cockpit opens and links the repo-side readiness view back to Foundry
  for both sandbox and dev.

Where to go next:

- Add `qa` and `prod` deploy workflows with
  `agentops workflow generate --kinds qa,prod --deploy-mode prompt-agent --force`.
  Each environment needs its own Foundry project; the first one or two
  CI runs there will bootstrap the agent via `prompt_agent_bootstrap`
  just as dev did.
- Add the scheduled Doctor workflow with
  `agentops workflow generate --kinds doctor --force`.
- Promote vetted production traces into the regression dataset with
  `agentops eval promote-traces` to grow the gate over time.
- Use `/skills agentops-governance` to add ASSERT, ACS, Guardrail review, and
  red-team evidence artifacts when your release process is ready for those
  controls.
