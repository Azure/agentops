# Tutorial: minimal quickstart

This tutorial covers the simplest end-to-end AgentOps flow: create a
small Foundry prompt agent, bootstrap an AgentOps workspace, run a
baseline evaluation, intentionally change the agent prompt, and compare
the new run against the baseline.

> Looking for the long-form, do-it-yourself tour that also covers
> a real tool-calling support agent, baseline comparison, GitFlow
> CI/CD, and the AgentOps doctor? See
> [tutorial-end-to-end.md](tutorial-end-to-end.md).

## What you will build

- A Foundry prompt agent with deterministic smoke-test instructions.
- A flat `agentops.yaml` at your project root.
- A small JSONL dataset.
- An `agentops eval analyze` triage before the first run.
- A baseline `agentops eval run` producing `results.json` and `report.md`.
- A second run compared against the baseline so the report shows prompt
  quality deltas.
- A doctor analysis (`agentops doctor`) that surfaces
  regressions across the run history.
- A production-readiness evidence pack (`agentops doctor --evidence-pack`)
  that writes `evidence.json` and `evidence.md` for release review.
- A trace-to-dataset preview (`agentops eval promote-traces`) that shows how
  production conversations become reviewed regression rows.
- A local Cockpit (`agentops cockpit`) that brings eval history,
  Doctor findings, CI/CD status, telemetry readiness, and Foundry/Azure
  navigation into one workspace view.
- CI/CD workflows for GitHub Actions or Azure DevOps Pipelines
  (`agentops workflow generate`) including a daily Doctor cron.

The former bundle-based, multi-file workspace has been replaced by this flat `agentops.yaml` workflow for the common case.

## Prerequisites

- Python 3.11 or later.
- Permission to create or edit a **Foundry prompt agent** and publish a
  version identified by `name:version` (for example `quickstart-agent:1`).
- The Foundry project endpoint URL.
- For AI-assisted evaluators (Coherence, Groundedness, etc.): an Azure OpenAI endpoint and deployment to use as the judge model.
- `az login` working against the tenant that owns the Foundry project
  (the CLI uses it as a credential fallback when no env vars are set).

## 1. Install

The AgentOps stack pulls a large dependency tree (Azure SDKs,
FastAPI/uvicorn for Cockpit, and OpenTelemetry instrumentation for
Azure Monitor integration). Using **`uv`** instead of `pip` cuts the
cold install from ~2 minutes to ~15 seconds - same flags, drop-in
replacement.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U uv
uv pip install "agentops-toolkit[foundry,agent]"
agentops --version
```

If you'd rather stay on `pip`, swap the last two lines for:

```powershell
python -m pip install -U pip
python -m pip install --upgrade "agentops-toolkit[foundry,agent]"
```

The `[foundry]` extra brings the Azure SDKs the eval path needs;
`[agent]` adds the FastAPI/uvicorn runtime used by `agentops cockpit`
later in the tutorial. Installing both upfront avoids a second install
later.

## 2. Bootstrap the project

```powershell
agentops init --no-prompt
```

`agentops init` is the single onboarding command. It scaffolds the
workspace and (when run interactively) walks you through an azd-style
wizard that asks for the Foundry project endpoint, agent reference,
dataset path, and Application Insights connection string. Each answer is
persisted to disk immediately:

- `agentops.yaml` — your evaluation config (3 lines + comments).
- `.agentops/data/smoke.jsonl` — a 3-row seed dataset with short,
  deterministic factual answers.
- `.azure/dev/.env` — azd-compatible environment file containing
  `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`, `APPLICATIONINSIGHTS_CONNECTION_STRING`,
  and friends. Shared transparently with `azd` if you adopt it later.
- `.azure/config.json` + `.azure/.gitignore` — env folder metadata.
- Coding-agent skills under `.github/skills/` (or `.claude/commands/`
  if Claude Code is detected).

We pass `--no-prompt` here because you don't have a Foundry agent yet —
we will run `agentops init` again after step 3 to fill in the answers
interactively. Use `agentops init show` at any time to inspect the
current configuration.

## 3. Create the smoke-test Foundry agent

In Azure AI Foundry, create a prompt agent named `quickstart-agent` with
your preferred model deployment. Paste this exact instruction into the
agent's **Instructions** field, then save and publish it:

```text
You are the AgentOps quickstart smoke-test agent.

For every user message:
- If the message starts with "Answer with exactly this sentence:", copy
  only the sentence after that prefix.
- Do not add greetings, markdown, citations, caveats, or explanations.
- Preserve punctuation, capitalization, and wording exactly.
- If the message does not use that prefix, answer briefly and factually
  in one sentence.
```

Copy the published `name:version` value from Foundry, for example
`quickstart-agent:2`. Foundry's portal saves the first published version
as **v2** (v1 is reserved for the empty draft), so your baseline will
normally be `:2`, not `:1`. This prompt is intentionally strict because
the seed dataset checks whether the agent can follow exact-output
instructions.

## 4. Connect Application Insights for tracing

AgentOps can resolve telemetry readiness and Azure Monitor links from
the Application Insights resource your Foundry project is wired to. Wire
it once and Cockpit, Doctor, and report tooling all share the same
project context.

In the Foundry portal:

1. Open the `quickstart-agent` agent.
2. Click the **Traces** tab.
3. Click **Connect** on the banner *Create or connect an App Insights
   resource to enable tracing*.
4. Pick an existing Application Insights resource (or create a new one),
   then **Connect**.

You can also wire it at project scope from the project name dropdown
(top-left) → **Project details** → **Connected resources** → **Add
connection** → **Application Insights**. Both paths write the same
project-level setting, so you only need to do it once.

After this step `agentops cockpit` can show telemetry readiness and link
you to the matching Foundry traces and Azure Monitor resources without
extra environment variables.

## 5. Configure AgentOps

Now that you have the agent published and Application Insights connected,
run the init wizard again — this time interactively — so AgentOps writes
every value for you:

```powershell
agentops init
```

Answer the prompts:

- **Foundry project endpoint** — paste your project URL
  (`https://<resource>.services.ai.azure.com/api/projects/<project>`).
- **Agent reference** — `quickstart-agent:2` (the `name:version` you
  copied from Foundry).
- **Dataset path** — accept the default `.agentops/data/smoke.jsonl`.
- **App Insights connection string** — paste from the Application Insights
  resource's *Overview* blade.

The wizard writes `agent` and `dataset` to `agentops.yaml`, and the
Azure values to `.azure/dev/.env`. You can also set the values via flags
for scripting:

```powershell
agentops init --no-prompt `
  --project-endpoint "https://<resource>.services.ai.azure.com/api/projects/<project>" `
  --agent "quickstart-agent:2" `
  --dataset ".agentops/data/smoke.jsonl"
```

Open `agentops.yaml` to confirm — the minimal Foundry-cloud config is:

```yaml
version: 1                       # schema version of agentops.yaml itself
agent: "quickstart-agent:2"        # ':2' is the Foundry agent version
dataset: .agentops/data/smoke.jsonl
execution: cloud                 # Foundry runs the agent + evaluators server-side
```

Add `execution: cloud` manually for now — it tells AgentOps to submit the
run to the New Foundry Evaluations panel via the OpenAI Evals API.

Field reference:

| Field | Values | Effect |
|---|---|---|
| `execution` | `local` (default) / `cloud` | `local`: AgentOps invokes the agent row-by-row. `cloud`: Foundry runs the agent + evaluators server-side via the OpenAI Evals API. |
| `publish` | `false` (default for local) / `true` | When `true`, results are published to Foundry. Destination is derived from `execution`: local + publish → **Classic Foundry**, cloud → **New Foundry**. |

> **`execution: cloud` always publishes.** A cloud run is hosted by
> Foundry by definition, so `publish` defaults to `true` automatically.
> Setting `publish: false` together with `execution: cloud` is rejected
> as a contradiction. If you want local-only results, use
> `execution: local` (the default).

The top-level `version: 1` is the schema version of `agentops.yaml`
(always `1` today). The trailing `:2` in `agent:` is the Foundry agent's
published version - they are independent.

> AgentOps also supports hosted Foundry endpoints, generic HTTP/JSON
> endpoints, and raw model deployments. Those are covered in the scenario
> tutorials; this quickstart keeps the path focused on a Foundry prompt
> agent.

## 6. Run the baseline evaluation

The init wizard already wrote `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` (and the
matching `AZURE_OPENAI_*` values it can derive) to `.azure/dev/.env`.
AgentOps auto-loads that file on every CLI run — you do not have to
re-export the variables.

You still need to sign in once so the SDKs have a credential:

```powershell
az login
agentops eval analyze
agentops eval run
```

If you prefer to set values in the current shell instead of `.env`, the
classic environment-variable path still works:

```powershell
$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://<resource>.services.ai.azure.com/api/projects/<project>"
$env:AZURE_OPENAI_ENDPOINT = "https://<openai-resource>.openai.azure.com"
$env:AZURE_OPENAI_DEPLOYMENT = "gpt-4o-mini"
agentops eval analyze
agentops eval run
```

> **Important:** run this command **without** `--baseline`. The baseline
> file does not exist yet - you are creating it in this step.

Use the Azure OpenAI data-plane endpoint for `AZURE_OPENAI_ENDPOINT` (`*.openai.azure.com`, no `/api/projects/...` path), not the Foundry project endpoint.

If Azure returns `Tenant provided in token does not match resource tenant`, sign in with the tenant that owns the Foundry project:

```powershell
az login --tenant <tenant-id>
```

Outputs:

```
.agentops/results/
├── 2026-05-06T14-30-22Z/   # Timestamped run (immutable history)
│   ├── results.json
│   ├── report.md
│   └── cloud_evaluation.json   # when publish: true (Classic or New mode)
└── latest/                 # Mirror of the most recent run
    ├── results.json
    ├── report.md
    └── cloud_evaluation.json   # when publish: true (Classic or New mode)
```

With `publish: false`, AgentOps writes only `results.json` and `report.md`. With `publish: true`, AgentOps also publishes to Foundry: `execution: local` uploads metrics to Classic Foundry; `execution: cloud` triggers a server-side run on New Foundry. Either way, `cloud_evaluation.json` records the portal URL.

For `execution: cloud`, the local JSONL referenced by `dataset:` remains the
source of truth. AgentOps syncs it to a stable Foundry dataset version by
default, then uses that Foundry dataset in the Evals run. If you force
`dataset_sync.mode: inline`, Foundry may display generated `eval-data-*`
backing assets in **Data > Datasets**. The `cloud_evaluation.json` file includes
a `dataset` block that explains this lineage.

To view the report rendered (tables, ✅/❌), open it in VS Code and press `Ctrl+Shift+V`:

```powershell
code .agentops/results/latest/report.md
```

The seed dataset asks the target to answer with exact short factual
sentences. The prompt from step 3 is designed to pass this smoke test, so
this first successful run is your baseline.

**Capture the baseline now** - the comparison run in step 7 requires
this file to exist:

```powershell
New-Item -ItemType Directory -Force .agentops\baseline | Out-Null
Copy-Item .agentops\results\latest\results.json .agentops\baseline\results.json
```

The CLI prints `Threshold status: PASSED` (exit code `0`) or `FAILED` (exit code `2`) so you can wire it into CI directly.

## 7. Change the prompt and compare against the baseline

Before running the comparison, make a real prompt change so the report
has something visible to measure. In Foundry, replace the agent
instructions with this intentionally degraded prompt, then save and
publish a new version:

```text
You are a friendly educational assistant.

For every answer:
- Do not copy the user's requested sentence verbatim.
- Paraphrase the answer in your own words.
- Add one extra sentence of helpful context.
- Use a warm, conversational tone.
```

Update `agentops.yaml` to the new published version (the next number
after your baseline - typically `:3`):

```yaml
version: 1
agent: "quickstart-agent:3"
dataset: .agentops/data/smoke.jsonl
execution: cloud
```

Now compare the changed prompt against the captured baseline:

```powershell
agentops eval run --baseline .agentops/baseline/results.json
```

The comparison should show visible deltas because the baseline prompt
copied the exact expected sentences while the new prompt deliberately
paraphrases and adds extra text. Expect `similarity` and `f1_score` to
drop, while latency may vary normally.

`report.md` now includes a `Comparison vs Baseline` section with per-metric deltas (🟢 improved / 🔴 regressed / ⚪ unchanged).

For normal local iteration you can also use
`.agentops/results/latest/results.json` as the baseline path. AgentOps
loads the baseline before refreshing `latest/`, so that path means "the
run before this one".

## 8. Run the AgentOps doctor

So far the loop is reactive: someone ran an eval and decided whether the
delta was acceptable. The **AgentOps doctor** is the AgentOps service
that turns the same run history (plus the workspace, eval bundle, and  -
when configured - production telemetry) into a written report:
severity-ranked findings with categories, summaries, and suggested fixes.
It is **complementary** to Foundry **Operate → Compliance**, which
already covers runtime guardrails, security posture, and data
governance at the resource level. The doctor focuses on what Foundry
doesn't surface: pipeline hygiene (MLOps), repo / identity security
beyond posture, and Responsible-AI heuristics on the prompt and eval
bundle.

You already have at least two runs in `.agentops/results/` (the baseline
and the comparison). Point the doctor at the workspace:

```powershell
agentops doctor --workspace . --out .agentops/agent/report.md --evidence-pack
```

It reads every run under `.agentops/results/`, applies the rules defined
in the workspace `agent.yaml` (regression detection, threshold drift,
latency spikes, MLOps hygiene, …) and emits a single Markdown report.
Open it the same way as the eval report:

```powershell
code .agentops/agent/report.md
code .agentops/release/latest/evidence.md
```

The CLI returns:

- `0` - no findings at or above the `--severity-fail` threshold (default `critical`).
- `2` - at least one finding at that severity (use this in CI to fail
  noisily on regressions).
- `1` - runtime/config error.

The doctor is the bridge between "one eval ran" and "the project's
quality is healthy". Wire `agentops doctor` into the CI workflow
generated below to get the same view automatically on every PR.

With `--evidence-pack`, Doctor also writes:

```text
.agentops/release/latest/
├── evidence.json   # versioned release-readiness contract
└── evidence.md     # reviewer-friendly release summary
```

The evidence status is a projection of existing signals, not a new gate:
`ready`, `ready_with_warnings`, or `blocked`. Eval and Doctor exit codes remain
unchanged, so CI behavior stays predictable.

### Preview trace-to-dataset promotion

Production traces become more valuable when high-signal conversations are
reviewed and added back to your regression suite. If you export Foundry/App
Insights traces as JSONL, preview candidate rows locally:

```powershell
agentops eval promote-traces --source .agentops/traces/sample-traces.jsonl
```

Use `--apply` only after reviewing the candidate rows:

```powershell
agentops eval promote-traces --source .agentops/traces/sample-traces.jsonl --apply
```

This writes `.agentops/data/trace-regression.jsonl` and
`.agentops/data/trace-regression-manifest.json`. The default
`self-similarity` mode stores the production response as `expected`, which is
useful for drift detection but not human-verified truth. Use
`--label-mode pending` when a reviewer should fill expected answers before the
dataset gates releases.

> **Tip - local Cockpit.** Every Doctor run appends a record to
> `.agentops/agent/history.jsonl`. Run `agentops cockpit` in a separate
> terminal to open http://127.0.0.1:8090 with eval history, Doctor
> findings, CI/CD status, telemetry readiness, and Foundry/Azure links in
> one read-only workspace view.

### Auditing the agent in App Insights

Whenever `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` is set (it already is, for
cloud execution), AgentOps **auto-discovers** the Application Insights
resource attached to the Foundry project and emits OpenTelemetry traces
there - no extra environment variable required. Cockpit uses the same
project context to surface telemetry readiness and route you to the
matching Foundry and Azure Monitor views.

Once you've run an `agentops eval run` or `agentops doctor`, you
can inspect the spans directly in the Foundry project's App Insights:

```kusto
union dependencies, requests, traces
| where timestamp > ago(1h)
| where name has "ANALYZE" or name has "RUN " or name has "eval_item"
   or name has "invoke_agent" or name has "evaluator" or name has "chat"
| project timestamp, itemType, name, success, duration, customDimensions
| order by timestamp desc
| take 100
```

Useful slices once data is flowing:

```kusto
// Pass rate by agent version over the last 7 days.
requests
| where timestamp > ago(7d)
| where name startswith "RUN "
| extend agent = tostring(customDimensions["agentops.eval.target"])
| summarize passed = countif(success == true), total = count() by agent
| extend pass_rate = round(100.0 * passed / total, 1)
| order by total desc
```

```kusto
// Per-evaluator score distribution for the latest run.
dependencies
| where timestamp > ago(2h)
| where name startswith "evaluator"
| extend score = todouble(customDimensions["agentops.eval.evaluator.score"])
| extend evaluator = tostring(customDimensions["agentops.eval.evaluator.builtin"])
| summarize avg_score = avg(score), runs = count() by evaluator
| order by avg_score asc
```

### Scheduling the doctor

Running `agentops doctor` manually is fine for the smoke test,
but the doctor earns its keep when it runs on a schedule. Two options:

**(a) CI cron (recommended for shared repos):** generate the doctor
workflow that ships with AgentOps:

```powershell
agentops workflow generate --kinds watchdog
# or for Azure DevOps Pipelines:
agentops workflow generate --platform azure-devops --kinds watchdog
```

This writes a daily-cron workflow (`agentops-watchdog.yml`) that
checks out the repo, restores the previous run's `history.jsonl` from
the pipeline artifact, runs `agentops doctor`, and re-uploads
the updated history. Trend data persists across runners.

**(b) Local Task Scheduler / cron (single developer machine):** drop
into Windows Task Scheduler or a Linux `crontab -e` and add
`agentops doctor --workspace <path>` on the cadence you want
(hourly, daily). Combine with `agentops cockpit` left running and the
cockpit refreshes itself.

### Security posture (WAF AI Security pillar)

The watchdog's posture check can audit your Foundry project resources
against the Well-Architected Framework's AI Security pillar - managed
identity instead of API keys, customer-managed encryption, private
networking, content safety enabled, etc. It is enabled by default and
tries to discover your deployed resource automatically from AZD
`.azure/<env>/.env` metadata, then from the Foundry project endpoint.

If discovery is ambiguous, pin the Azure resource explicitly in
`.agentops/agent.yaml`:

```yaml
sources:
  results_history:
    enabled: true
  azure_resources:
    enabled: true
    subscription_id: "<your-sub-id>"
    resource_group: "<your-rg>"
```

Then re-run `agentops doctor`. Posture findings appear under the
`security` category with WAF rule ids you can grep for, and the
cockpit's *Security* card lights up if anything regressed since the
last analysis.

## 9. Generate the CI/CD workflows

The eval loop is most useful when it runs automatically on every pull
request and deploy. `agentops workflow generate` writes a complete
GitFlow scaffold - a PR gate plus three deploy stages
(dev / qa / production) plus a daily watchdog - that you can commit to
your repo once Azure auth and deployment wiring are ready.

You can target **GitHub Actions** (default) or **Azure DevOps
Pipelines**. The conceptual workflows are identical across platforms;
the only difference is the YAML dialect and where the files land.

**GitHub Actions:**

```powershell
agentops workflow analyze
agentops workflow generate
```

`workflow analyze` is the CI/CD triage step. It explains the recommended
deploy mode before `workflow generate` writes files.

writes:

```
.github/workflows/
├── agentops-pr.yml            # PR gate (PRs to develop, release/**, main)
├── agentops-deploy-dev.yml    # push to develop  → environment: dev
├── agentops-deploy-qa.yml     # push to release/** → environment: qa
├── agentops-deploy-prod.yml   # push to main      → environment: production
└── agentops-watchdog.yml      # daily cron → runs `agentops doctor`
```

**Azure DevOps Pipelines:**

```powershell
agentops workflow analyze
agentops workflow generate --platform azure-devops
```

writes:

```
.azuredevops/pipelines/
├── agentops-pr.yml
├── agentops-deploy-dev.yml
├── agentops-deploy-qa.yml
├── agentops-deploy-prod.yml
└── agentops-watchdog.yml
```

Each workflow installs AgentOps, runs `agentops eval run`, uploads the
results as a pipeline artifact, and (for the PR gate) posts the
rendered `report.md` plus release evidence as an idempotent PR comment.
The PR, production deploy, and watchdog templates also run
`agentops doctor --evidence-pack` so `.agentops/release/latest/evidence.*`
is attached to the workflow artifacts.

Deployment is azd-first. If you omit `--deploy-mode`, the default is `auto`.
When `azure.yaml` exists, `auto` writes deploy stages that call
`azd provision` / `azd deploy`; when `agentops.yaml` targets a Foundry prompt
agent, it can use prompt-agent deployment; otherwise it writes stack-agnostic
placeholders. The command output prints the effective mode, such as
`Deploy mode: azd (auto default)`.

If the repo is based on Azure AI Landing Zone and includes
`scripts/Invoke-PreflightChecks.ps1`, azd deploy workflows run that official
preflight with `-Strict` before provisioning. `agentops doctor` reports the same
path as AI Landing Zone deployment readiness under Operational Excellence.

For the quickstart you don't have to commit these files yet - opening
them locally shows what AgentOps will run in CI:

```powershell
code .github/workflows/agentops-pr.yml          # GitHub
code .azuredevops/pipelines/agentops-pr.yml     # Azure DevOps
```

Useful flags:

| Flag | Default | Effect |
|---|---|---|
| `--platform` | `github` | `github` or `azure-devops`. |
| `--kinds` | all five | Comma-separated subset, e.g. `--kinds pr,dev`. Use `--kinds pr` for the safest first commit. |
| `--deploy-mode` | `auto` | `auto`, `azd`, `prompt-agent`, or `placeholder`. Omit it for the safe default. |
| `--force` | off | Overwrite existing workflow files. |
| `--dir` | `.` | Repo root directory. |

The `agentops-workflow` Copilot skill walks you through the rest
(environments, Azure auth, branch protection / approvals) when you're
ready to push the workflows to your repo.

## 10. Wire the workflows to Azure via OIDC

The workflows expect six environment variables (`AZURE_CLIENT_ID`,
`AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`,
`AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`, `AZURE_OPENAI_ENDPOINT`,
`AZURE_OPENAI_DEPLOYMENT`) per GitHub Environment, and they
authenticate to Azure via **OpenID Connect** - no long-lived secret
sits in the repo. This section walks the full setup end-to-end.

Substitute your own values for `<repo>` (e.g.
`agentops-quickstart-MMDDYYHHMM`) and `<owner>` (your GitHub login)
below.

### 10.1 Push the repo to GitHub

```powershell
git init -b main
git add .
git commit -m "initial: AgentOps quickstart workspace + CI workflows"
gh repo create "<owner>/<repo>" --private --source=.
git push -u origin main
git checkout -b develop && git push -u origin develop && git checkout main
```

`develop` is the integration branch the PR gate runs against. `main`
is the prod-deploy trigger.

### 10.2 Create the Azure AD app registration

The CI service principal needs to exist in the same tenant `azd`/`az`
is logged into. Confirm with `az account show --query tenantId`.

```powershell
$APP_NAME = "<repo>"        # reuse the GitHub repo name for clarity
az ad app create --display-name $APP_NAME --sign-in-audience AzureADMyOrg
az ad sp create --id (az ad app list --display-name $APP_NAME --query "[0].appId" -o tsv)
```

Capture the app's `appId` (client ID) and the service principal's
`objectId`; the next steps reference them.

### 10.3 Configure federated credentials (one per environment)

This is the OIDC trust: GitHub Actions tokens issued for these
subjects can request Azure tokens for this app, with no shared secret.

Repeat the block below for each environment (`dev`, `qa`,
`production`) and once more for `pull_request`:

```powershell
$APP_OBJ_ID = (az ad app list --display-name $APP_NAME --query "[0].id" -o tsv)
$BODY = @{
  name      = "github-<repo>-env-dev"
  issuer    = "https://token.actions.githubusercontent.com"
  subject   = "repo:<owner>/<repo>:environment:dev"
  audiences = @("api://AzureADTokenExchange")
} | ConvertTo-Json -Depth 5
$BODY | az ad app federated-credential create --id $APP_OBJ_ID --parameters "@-"
```

For PR-only triggers (no environment binding) use
`subject = "repo:<owner>/<repo>:pull_request"`.

### 10.4 Grant the service principal Azure roles

Three role assignments cover the cloud-eval path. Adjust scopes if you
target a different Foundry account.

```powershell
$SP_ID    = (az ad sp list --display-name $APP_NAME --query "[0].id" -o tsv)
$SCOPE_FN = "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<foundry-account>"
$SCOPE_SUB = "/subscriptions/<sub>"

az role assignment create --assignee-object-id $SP_ID --assignee-principal-type ServicePrincipal `
  --role "Cognitive Services User" --scope $SCOPE_FN
az role assignment create --assignee-object-id $SP_ID --assignee-principal-type ServicePrincipal `
  --role "Azure AI Developer"      --scope $SCOPE_FN
az role assignment create --assignee-object-id $SP_ID --assignee-principal-type ServicePrincipal `
  --role "Reader"                  --scope $SCOPE_SUB
```

Why these three:

| Role | Scope | Why |
|---|---|---|
| `Cognitive Services User` | Foundry account | Invoke the agent + call the Evals API |
| `Azure AI Developer` | Foundry account | Read/write agents and evaluation runs in the project |
| `Reader` | Subscription | Read resource metadata for auto-discovery (App Insights, deployments) |

### 10.5 Create the GitHub environments + variables

The workflows read `vars.*` from the GitHub Environment that the job
is bound to. Create the three environments and seed the variables on
each:

```powershell
foreach ($env in @("dev","qa","production")) {
  gh api -X PUT "/repos/<owner>/<repo>/environments/$env" --silent
}

$vars = @{
  AZURE_CLIENT_ID                   = "<app-id>"
  AZURE_TENANT_ID                   = "<tenant-id>"
  AZURE_SUBSCRIPTION_ID             = "<sub-id>"
  AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
  AZURE_OPENAI_ENDPOINT             = "https://<account>.openai.azure.com/"
  AZURE_OPENAI_DEPLOYMENT           = "gpt-4o-mini"
}
foreach ($env in @("dev","qa","production")) {
  foreach ($k in $vars.Keys) {
    $body = @{ name=$k; value=$vars[$k] } | ConvertTo-Json -Compress
    $body | gh api -X POST "/repos/<owner>/<repo>/environments/$env/variables" --input - --silent
  }
}
```

Production should also have **required reviewers**: open
`Settings → Environments → production` in the GitHub UI and add at
least one human approver.

### 10.6 Validate end-to-end

Trigger the PR gate manually to confirm the OIDC chain works:

```powershell
gh workflow run "agentops-pr.yml" --ref main
gh run watch
```

The expected output is `Threshold status: PASSED` followed by
`exit code 0`. If you see `failed to load agentops.yaml` validation
errors, the CI installed an older AgentOps build than your local one
 -  pin the install to a specific tag in the workflow's
`pip install` step.

## Where evaluators come from

You did not pick evaluators - AgentOps inferred them:

- **Always:** Coherence, Fluency, Similarity, F1Score.
- **`execution: local` only:** `avg_latency_seconds` (client-perceived
  latency, measured during the row-by-row local invocation). In
  `execution: cloud`, runtime evaluators are skipped because the agent
  never runs on your machine, so the latency the CLI prints is
  Foundry-side instead.
- **If your dataset rows include `context`:** Groundedness, Relevance, Retrieval, ResponseCompleteness.
- **If your dataset rows include `tool_calls` or `tool_definitions`:** TaskCompletion, ToolCallAccuracy, IntentResolution, TaskAdherence.

To override the auto-selection, list evaluator class names in `agentops.yaml`
under `name:` keys:

```yaml
evaluators:
  - name: GroundednessEvaluator
  - name: CoherenceEvaluator
```

## Where to go next

- [`docs/how-it-works.md`](how-it-works.md) - architecture and request flow.
- [`docs/ci-github-actions.md`](ci-github-actions.md) - wire AgentOps into PR checks with OIDC auth.
- [`docs/tutorial-production-readiness.md`](tutorial-production-readiness.md) - the POC-to-production journey with release evidence and trace regression.
- The scenario tutorials use the same flat `agentops.yaml` workflow with more realistic datasets and targets.
