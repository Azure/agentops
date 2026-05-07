# Tutorial — End-to-end with AgentOps

This is the long-form, do-it-yourself tour of AgentOps. By the end you
will have a real Foundry hosted agent with **three function tools**
under evaluation, a baseline-vs-degraded comparison that demonstrates
tool-call regression detection, four GitFlow CI/CD workflows wired to
your own GitHub repo, and a watchdog report summarising your run
history.

It takes around 60–90 minutes the first time. Every step is concrete:
you copy a command, you see an artefact, you keep moving.

> **Why a tool-calling agent?** Production agents fail in interesting
> ways: they pick the wrong tool, fabricate arguments, or skip tool
> use entirely and answer from memory. AgentOps grades all of those
> behaviours — `tool_call_accuracy`, `intent_resolution`,
> `task_adherence` — alongside text quality. A trivia chatbot would
> only exercise the latter; this tutorial uses an agent where tool
> behaviour is the point.

## What you will build

- A Foundry hosted **support agent** with three function tools:
  `lookup_order`, `refund_order`, `escalate_to_human`.
- A flat `agentops.yaml` pointing at that agent with thresholds on
  both text-quality and tool-call metrics.
- A 5-row evaluation dataset of realistic support tickets, each
  carrying `tool_definitions` and the expected `tool_calls`.
- Two evaluation runs (a tool-using **v1** baseline and a degraded
  **v2** that answers from memory) compared side-by-side. The
  baseline-vs-degraded delta shows tool-call accuracy collapse —
  exactly the kind of regression CI is meant to catch.
- Four GitFlow workflows (`pr`, `dev`, `qa`, `prod`) wired to your
  own GitHub repository, gated on threshold pass/fail.
- A watchdog report combining your run history with optional
  Application Insights telemetry.

## Prerequisites

- Python 3.11 or later.
- Azure CLI (`az --version`) and `az login` working.
- An Azure AI Foundry project (`AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`).
- A model deployment in that project (`gpt-4o-mini` is enough).
- The **Azure AI User** RBAC role on the Foundry account
  (data-plane access required to create agents and call them).
- A GitHub account and the `gh` CLI (or use the web UI for pushes).
- An existing or new GitHub repo — empty is fine; we will populate it.

> **Verify your auth before running anything.** Most "this should
> have worked" failures in this tutorial come from a stale CLI token
> cache, being logged into the wrong tenant, or missing the role
> above. A 30-second sanity check:
>
> ```powershell
> az account show --query "{tenant:tenantId, user:user.name, sub:name}" -o table
> ```
>
> If the tenant or subscription is wrong, run `az login --tenant <tenant-id>`
> and `az account set --subscription <subscription-id>`. To grant the role
> to yourself (replace the placeholders with your account values):
>
> ```powershell
> az role assignment create `
>   --assignee "<your-upn-or-object-id>" `
>   --role "Azure AI User" `
>   --scope "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<foundry-account>"
> ```
>
> A 401 with `"Token not supported"` from
> `create_support_agent.py` almost always means one of:
>
> 1. **Stale CLI token cache** — most common when the script worked
>    earlier today and now suddenly fails. Fix:
>    ```powershell
>    az account clear
>    az login
>    ```
> 2. Wrong tenant (see above).
> 3. Missing **Azure AI User** role (see above).

Set the project endpoint up front so every command picks it up.

**PowerShell (Windows):**

```powershell
$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://<your-project>.services.ai.azure.com/api/projects/<project-name>"
$env:AZURE_OPENAI_ENDPOINT             = "https://<your-project>.services.ai.azure.com"
$env:AZURE_OPENAI_DEPLOYMENT           = "gpt-4o-mini"
```

**bash / zsh (Linux, macOS, WSL):**

```bash
export AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="https://<your-project>.services.ai.azure.com/api/projects/<project-name>"
export AZURE_OPENAI_ENDPOINT="https://<your-project>.services.ai.azure.com"
export AZURE_OPENAI_DEPLOYMENT="gpt-4o-mini"
```

> **Watch out for two endpoint shapes.** On a Foundry "AI Services"
> account, both env vars start with the same hostname but the
> project endpoint includes `/api/projects/<project-name>` while
> `AZURE_OPENAI_ENDPOINT` is **only** the hostname (no path). If you
> paste the project URL into `AZURE_OPENAI_ENDPOINT` the evaluators
> fail with `BadRequest: API version not supported`. AgentOps
> defaults the API version to a release that works against both
> New Foundry and classic Azure OpenAI; override with
> `AZURE_OPENAI_API_VERSION` only if your resource needs a specific
> version.

> The remaining shell snippets in this tutorial are written for
> **PowerShell** (the default on Windows). bash / zsh users can
> substitute `export VAR=value` for `$env:VAR = "value"`, `cat`
> for `Get-Content`, and `ls` for `Get-ChildItem`.

## 1. Install AgentOps

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1    # bash/zsh: source .venv/bin/activate
python -m pip install -U pip
python -m pip install agentops-toolkit
python -m pip install azure-ai-projects azure-identity azure-ai-evaluation
agentops --version
```

> `azure-ai-projects` and `azure-identity` are only needed by the
> helper script that creates the agent. `azure-ai-evaluation` is the
> SDK that runs the local evaluators (`ToolCallAccuracyEvaluator`,
> `IntentResolutionEvaluator`, `CoherenceEvaluator`, …) — without it
> `agentops eval run` exits with
> `Evaluators require the 'azure-ai-evaluation' package`.

## 2. Create the Foundry hosted support agent

The tutorial uses **three function tools** that a real support agent
would expose:

| Tool | Purpose | Required arguments |
|---|---|---|
| `lookup_order` | Look up an order's status. | `order_id` |
| `refund_order` | Refund an order. | `order_id`, `reason` |
| `escalate_to_human` | Hand the conversation to a human agent. | `category` |

Registering three tools through the portal is fiddly, so this
repository ships a small helper script,
[`scripts/create_support_agent.py`](../scripts/create_support_agent.py),
that does it in one command. **Just download the single file into the
root of your tutorial project** — there's no need to create a
`scripts/` folder, and the script has no AgentOps dependency (only
`azure-ai-projects` and `azure-identity`). Then run it from the same
folder:

```powershell
python create_support_agent.py create --name support-bot
# stdout: support-bot:1
```

The first line of stdout is the `name:version` identifier you paste
into `agentops.yaml` next. The script:

- Creates a hosted prompt agent named `support-bot`.
- Registers the three function tools above with strict JSON Schema
  parameters.
- Pins the system prompt to require tool use whenever the user asks
  about an order, a refund, or talking to a human.
- Prints `support-bot:<version>` on stdout and a friendly summary on
  stderr (including a `Registered tools:` line so you can confirm
  the attachment).

> **Why don't I see the tools in the Playground?** The Foundry
> portal's Playground tab only lists tools you added through the
> portal's **Add** button. Tools registered through the SDK (like
> these) show up under the agent's **Code** / **YAML** tab and are
> invoked at runtime — `agentops eval run` exercises them either
> way.

> **Prefer the portal?** Open
> [Azure AI Foundry](https://ai.azure.com) → your project → **Build →
> Agents → New agent**, register the three function tools manually
> (the script's source is the canonical schema), paste the system
> prompt from `INSTRUCTIONS_GOOD` in the script, save, and copy the
> resulting `name:version` string.

## 3. Initialize the workspace

In an empty folder (or the GitHub repo you want to use):

```powershell
agentops init
```

You get:

```
.agentops/
├── agentops.yaml
├── data/
│   └── smoke.jsonl
├── datasets/
│   └── smoke.yaml
└── results/
.github/
└── skills/
    └── agentops-*/SKILL.md
```

Open `.agentops/agentops.yaml` and configure it for the support
agent:

```yaml
version: 1
agent: "support-bot:1"

dataset: ./data/tickets.jsonl

thresholds:
  # Tool-calling metrics (auto-inferred from tool_definitions /
  # tool_calls in the dataset).
  tool_call_accuracy: ">=0.8"
  intent_resolution: ">=4"
  task_adherence: ">=0.8"
  # Text quality metrics.
  coherence: ">=3"
  fluency: ">=3"
  similarity: ">=3"
  # Latency budget.
  avg_latency_seconds: "<=10"
```

The `agent: "name:version"` shape is recognised as a **Foundry hosted
agent**. AgentOps invokes it through the Foundry project endpoint
using your `az login` credentials.

## 4. Author the support-ticket dataset

Replace `.agentops/data/smoke.jsonl` with a new
`.agentops/data/tickets.jsonl` carrying five realistic support
tickets. Each row includes:

- `input` — the customer message,
- `expected` — the expected outcome in plain prose,
- `tool_definitions` — every tool the agent has access to,
- `tool_calls` — the tool the agent **should** call (or an empty
  list when the right behaviour is to answer with no tool).

The variety of intents — order lookup, refund, escalation, an
ambiguous query that should resolve to a lookup, and a casual
greeting that should *not* trigger any tool — is what gives the
evaluators something interesting to grade.

```jsonl
{"input": "Where is my order ORD-12345?", "expected": "Calls lookup_order with order_id='ORD-12345'.", "tool_definitions": [{"type": "function", "name": "lookup_order", "description": "Look up an order.", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}}, {"type": "function", "name": "refund_order", "description": "Refund an order.", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["order_id", "reason"]}}, {"type": "function", "name": "escalate_to_human", "description": "Hand the conversation to a human.", "parameters": {"type": "object", "properties": {"category": {"type": "string"}}, "required": ["category"]}}], "tool_calls": [{"type": "tool_call", "tool_call_id": "c1", "name": "lookup_order", "arguments": {"order_id": "ORD-12345"}}]}
{"input": "I want a refund for ORD-77821, it arrived broken.", "expected": "Calls refund_order with order_id='ORD-77821' and reason mentioning broken.", "tool_definitions": [{"type": "function", "name": "lookup_order", "description": "Look up an order.", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}}, {"type": "function", "name": "refund_order", "description": "Refund an order.", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["order_id", "reason"]}}, {"type": "function", "name": "escalate_to_human", "description": "Hand the conversation to a human.", "parameters": {"type": "object", "properties": {"category": {"type": "string"}}, "required": ["category"]}}], "tool_calls": [{"type": "tool_call", "tool_call_id": "c2", "name": "refund_order", "arguments": {"order_id": "ORD-77821", "reason": "arrived broken"}}]}
{"input": "Please connect me to a human about my refund — this has dragged on too long.", "expected": "Calls escalate_to_human with category='refund'.", "tool_definitions": [{"type": "function", "name": "lookup_order", "description": "Look up an order.", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}}, {"type": "function", "name": "refund_order", "description": "Refund an order.", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["order_id", "reason"]}}, {"type": "function", "name": "escalate_to_human", "description": "Hand the conversation to a human.", "parameters": {"type": "object", "properties": {"category": {"type": "string"}}, "required": ["category"]}}], "tool_calls": [{"type": "tool_call", "tool_call_id": "c3", "name": "escalate_to_human", "arguments": {"category": "refund"}}]}
{"input": "Did ORD-99001 ship yet?", "expected": "Calls lookup_order with order_id='ORD-99001'.", "tool_definitions": [{"type": "function", "name": "lookup_order", "description": "Look up an order.", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}}, {"type": "function", "name": "refund_order", "description": "Refund an order.", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["order_id", "reason"]}}, {"type": "function", "name": "escalate_to_human", "description": "Hand the conversation to a human.", "parameters": {"type": "object", "properties": {"category": {"type": "string"}}, "required": ["category"]}}], "tool_calls": [{"type": "tool_call", "tool_call_id": "c4", "name": "lookup_order", "arguments": {"order_id": "ORD-99001"}}]}
{"input": "Hi there!", "expected": "Replies with a brief greeting and does NOT call any tool.", "tool_definitions": [{"type": "function", "name": "lookup_order", "description": "Look up an order.", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}}, {"type": "function", "name": "refund_order", "description": "Refund an order.", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["order_id", "reason"]}}, {"type": "function", "name": "escalate_to_human", "description": "Hand the conversation to a human.", "parameters": {"type": "object", "properties": {"category": {"type": "string"}}, "required": ["category"]}}], "tool_calls": []}
```

> **Why each row repeats the full `tool_definitions`?** Each dataset
> row is evaluated independently and the evaluators that check tool
> selection / argument accuracy need the **complete** tool catalogue
> per row. Repetition is the cost of row-level isolation; in real
> projects a small Python script can stamp the same definitions into
> every row at dataset-build time.

The presence of `tool_definitions` and `tool_calls` is what auto-
selects the tool-calling evaluators on top of the standard text-
quality stack. When AgentOps loads the dataset it picks:

| Evaluator | What it grades |
|---|---|
| `ToolCallAccuracyEvaluator` | Did the agent emit the expected tool calls (name + arguments)? |
| `IntentResolutionEvaluator` | Did the agent resolve the user's intent? |
| `TaskAdherenceEvaluator` | Did the agent stick to the system prompt's tool-use rules? |
| `CoherenceEvaluator` / `FluencyEvaluator` / `SimilarityEvaluator` / `F1ScoreEvaluator` | Standard text quality. |
| `avg_latency_seconds` | End-to-end latency budget. |

## 5. Run your first evaluation

```powershell
agentops eval run
```

The CLI:

1. Resolves the target from `agentops.yaml`.
2. Calls the Foundry hosted agent once per row, capturing both the
   final text response and the structured tool calls.
3. Runs evaluators using `AZURE_OPENAI_DEPLOYMENT`.
4. Writes a timestamped run under `.agentops/results/<timestamp>/` and refreshes
   `.agentops/results/latest/` with a copy of it. Pass `--output <dir>` to write
   the run only to that path instead.

Open the report in VS Code (any OS, no extra tooling required) and press `Ctrl+Shift+V` to render the Markdown — tables and ✅/❌ display the same way they do on GitHub:

```powershell
code .agentops/results/latest/report.md
```

> Tip: `Ctrl+K V` opens the rendered preview side-by-side with the source.

The report has four sections you will revisit often:

- **Verdict** — one line: pass or fail.
- **Per-row transcript** — input, expected, agent response, the
  `tool_calls` the agent emitted, and every metric. The greeting
  row's transcript shows an empty `tool_calls` block — useful when
  debugging false-positive tool calls.
- **Aggregate metrics** — averages across rows.
- **Thresholds** — every rule from `agentops.yaml` with measured
  value. With v1 you should see all the tool-calling thresholds in
  the green.

The exit code is `0` (all thresholds passed) or `2` (one or more
failed). `1` means a runtime error.

## 6. Compare against a degraded baseline

This is where the tutorial earns its keep. AgentOps writes every run to a
timestamped folder under `.agentops/results/` and refreshes
`.agentops/results/latest/` with a copy. The v1 run you just executed
is still on disk — you don't need to copy or re-run anything to use it
as the baseline. Just point `--baseline` at the previous run when you
execute v2:

- `.agentops/results/latest/results.json` works as a shorthand for
  "the run before this one" (AgentOps loads it into memory before
  refreshing `latest/`).
- For a stable, named reference you can also point at a specific
  timestamp folder, e.g.
  `.agentops/results/2026-05-06T20-13-21Z/results.json`.

Now create a **degraded** version of the agent — same model, no
tools, plain-text-only instructions — so the regression demo has
something to detect:

```powershell
python create_support_agent.py create `
  --name support-bot `
  --variant v2-degraded
# stdout: support-bot:2
```

Update `agentops.yaml`:

```yaml
agent: "support-bot:2"
```

Re-run with the v1 result as the baseline:

**PowerShell:**

```powershell
agentops eval run --baseline .agentops/results/latest/results.json
```

**bash / zsh:**

```bash
agentops eval run --baseline .agentops/results/latest/results.json
```

Then open the new report:

```powershell
code .agentops/results/latest/report.md
```

Press `Ctrl+Shift+V` to render the Markdown.

The new `report.md` adds a **Comparison vs Baseline** section with
per-metric deltas. Because v2 has **no tools attached at all**, the
agent literally cannot call `lookup_order`, `refund_order`, or
`escalate_to_human` — every order-specific row degrades to a
plain-text apology. You should see roughly:

| Metric | Baseline (v1) | Current (v2) | Direction |
|---|---|---|---|
| `tool_call_accuracy` | high (≈ 5) | **collapses to `n/a` / floor** | 🔴 regressed |
| `intent_resolution` | high (≈ 4–5) | **drops noticeably** | 🔴 regressed |
| `task_adherence` | mid–high | **drops to floor (1.0)** | 🔴 regressed |
| `coherence` | ≈ 4 | ≈ 4 | ⚪ unchanged |
| `fluency` | ≈ 4 | ≈ 4 | ⚪ unchanged |
| `similarity` | ≈ 3 | ≈ 3 | ⚪ unchanged |

Text quality barely moves — the degraded agent is still articulate
and on-topic — but the tool-related metrics collapse, the verdict
flips to fail, and the run exits `2`. **This is the regression-detection
loop you will wire into CI next.**

> Exact numbers will jitter run-to-run because the evaluators
> themselves are model-graded, and metrics like `task_adherence` use
> an ordinal 1–5 scale (1.0 is the floor, not 0). What matters is the
> *shape* of the delta: tool/task metrics down, text-quality metrics
> flat.

## 7. Generate the GitFlow workflows

```powershell
agentops workflow generate
```

Four files appear under `.github/workflows/`:

| Workflow | Trigger | Purpose |
|---|---|---|
| `agentops-pr.yml` | Pull request opened against `develop` or `main` | Runs `agentops eval run` against the baseline; comments the report on the PR; gates merge on threshold pass/fail. |
| `agentops-deploy-dev.yml` | Push to `develop` | Deploys to the **dev** environment after a passing eval. |
| `agentops-deploy-qa.yml` | Push to a `release/*` branch | Deploys to **qa**. |
| `agentops-deploy-prod.yml` | Push to `main` | Deploys to **prod** after a passing eval. |

Read [`ci-github-actions.md`](ci-github-actions.md) for the full
reference. The defaults are sane: you do not need to edit them yet.

## 8. Push to GitHub and watch it run

Initialize the repo and push. Pick a unique suffix (your initials, a
date, anything) so the repo and the app registration you create later
don't collide with someone else running this same tutorial:

```powershell
$suffix = "<your-initials-or-date>"   # e.g. "pl-20260507"
git init -b main
git add .
git commit -m "feat: bootstrap AgentOps eval and CI/CD"
gh repo create "support-bot-$suffix" --public --source=. --push
git checkout -b develop
git push -u origin develop
```

> **Prefer the portal?** Create the repo at
> [github.com/new](https://github.com/new) named `support-bot-<suffix>`,
> then push from your terminal:
> `git remote add origin https://github.com/<owner>/support-bot-<suffix>.git && git push -u origin main && git push -u origin develop`.

### Wire the GitHub Environments

The three workflows (`pr`, `deploy-dev`, `deploy-qa`, `deploy-prod`)
expect a GitHub **environment** per stage, each populated with the same
six variables and a federated credential so Azure trusts GitHub OIDC.

The next four snippets create everything end-to-end. Run them in order
from the same PowerShell session you used above (so `$suffix` is still
in scope).

#### 1. Create the app registration GitHub will impersonate

```powershell
$app    = az ad app create --display-name "support-bot-ci-$suffix" | ConvertFrom-Json
az ad sp create --id $app.appId | Out-Null
$client = $app.appId
$tenant = az account show --query tenantId -o tsv
$sub    = az account show --query id -o tsv
Write-Host "AZURE_CLIENT_ID       = $client"
Write-Host "AZURE_TENANT_ID       = $tenant"
Write-Host "AZURE_SUBSCRIPTION_ID = $sub"
```

> **Notes**
> - **One app registration vs many.** This tutorial uses a single app
>   registration shared across `dev`, `qa`, and `prod` to keep the
>   walkthrough short. In production you typically create **one app
>   registration per environment** so you can grant least-privilege
>   roles per stage and rotate them independently.
> - **No CLI? Use the portal.** Create the app under **Microsoft Entra
>   ID → App registrations → New registration**, then set
>   `$client = "<application-client-id>"` manually before running the
>   next snippet.

#### 2. Create the three environments and push the variables

```powershell
$foundry = $env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
$aoai    = $env:AZURE_OPENAI_ENDPOINT
$deploy  = "gpt-4o-mini"
$repo    = gh repo view --json nameWithOwner -q .nameWithOwner

foreach ($envName in @("dev","qa","prod")) {
  gh api -X PUT "repos/$repo/environments/$envName" | Out-Null
  gh variable set AZURE_TENANT_ID                    --env $envName --body $tenant
  gh variable set AZURE_SUBSCRIPTION_ID              --env $envName --body $sub
  gh variable set AZURE_CLIENT_ID                    --env $envName --body $client
  gh variable set AZURE_AI_FOUNDRY_PROJECT_ENDPOINT  --env $envName --body $foundry
  gh variable set AZURE_OPENAI_ENDPOINT              --env $envName --body $aoai
  gh variable set AZURE_OPENAI_DEPLOYMENT            --env $envName --body $deploy
  Write-Host "Configured environment: $envName"
}
```

> **Prefer the portal?** Open your repo on github.com → **Settings →
> Environments → New environment** and create `dev`, `qa`, and `prod`.
> For each one, click **Add variable** and add the six rows from the
> table at the top of this section.

#### 3. Add federated credentials so Azure trusts GitHub OIDC

One credential per environment. The PR gate workflow runs **inside the
`dev` environment** (so it inherits the same `dev` variables and OIDC
subject) — no separate `pull_request` credential is needed. The JSON is
written to a temp file because `az` does not parse inline JSON reliably
under PowerShell:

```powershell
$subjects = @{
  "dev"  = "repo:${repo}:environment:dev"
  "qa"   = "repo:${repo}:environment:qa"
  "prod" = "repo:${repo}:environment:prod"
}

foreach ($name in $subjects.Keys) {
  $payload = [ordered]@{
    name      = "github-$name"
    issuer    = "https://token.actions.githubusercontent.com"
    subject   = $subjects[$name]
    audiences = @("api://AzureADTokenExchange")
  }
  $tmp = New-TemporaryFile
  $payload | ConvertTo-Json | Set-Content -Path $tmp -Encoding utf8

  az ad app federated-credential create --id $client --parameters "@$tmp" | Out-Null
  Remove-Item $tmp
  Write-Host "Added federated credential: $name"
}
```

> **Prefer the portal?** Open **Microsoft Entra ID → App registrations
> → support-bot-ci-$suffix → Certificates & secrets → Federated
> credentials → Add credential**. Pick **GitHub Actions deploying Azure
> resources** as the scenario, then create one credential per subject
> in the table above (`environment:dev`, `environment:qa`,
> `environment:prod`).

#### 4. Grant the app the roles it needs

```powershell
$spId = az ad sp show --id $client --query id -o tsv

# Resolve resource IDs from the endpoint URLs (no need to know the RG).
$foundryName = (($env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT -split "//")[1] -split "\.")[0]
$aoaiName    = (($env:AZURE_OPENAI_ENDPOINT -split "//")[1] -split "\.")[0]

$foundryId = az resource list --name $foundryName `
  --resource-type "Microsoft.CognitiveServices/accounts" --query "[0].id" -o tsv
$aoaiId    = az resource list --name $aoaiName `
  --resource-type "Microsoft.CognitiveServices/accounts" --query "[0].id" -o tsv

if (-not $foundryId) { throw "Could not resolve Foundry resource id for '$foundryName'" }
if (-not $aoaiId)    { throw "Could not resolve Azure OpenAI resource id for '$aoaiName'" }

# Foundry project — read agents and runs
az role assignment create --assignee-object-id $spId `
  --assignee-principal-type ServicePrincipal `
  --role "Azure AI User" --scope $foundryId | Out-Null

# Azure OpenAI — call the judge model
az role assignment create --assignee-object-id $spId `
  --assignee-principal-type ServicePrincipal `
  --role "Cognitive Services OpenAI User" --scope $aoaiId | Out-Null

Write-Host "Roles granted on Foundry project and Azure OpenAI."
```

> **Prefer the portal?** Open your Foundry project resource → **Access
> control (IAM) → Add role assignment**, pick **Azure AI User**, and
> assign it to the `support-bot-ci-$suffix` app. Repeat on the Azure
> OpenAI resource with the **Cognitive Services OpenAI User** role.

### Open a PR

```powershell
git checkout -b feature/tweak-prompt
# make any small change, e.g. edit tickets.jsonl
git commit -am "test: refine ticket dataset"
git push -u origin feature/tweak-prompt
gh pr create --base develop --fill
```

The `agentops-pr.yml` workflow runs. When it finishes you will see:

- A green or red check on the PR.
- A bot comment with the verdict, threshold table (including the
  tool-call metrics), and a link to the full `report.md` artifact.

Merge the PR. `agentops-deploy-dev.yml` triggers, runs an eval against
the dev environment, and deploys if it passes.

## 9. Run the Watchdog

The watchdog reads your accumulated run history and (optionally)
queries Application Insights and the Foundry control plane to flag
drifts that a single eval cannot see — repeated regressions, latency
trends, error spikes, safety findings.

```powershell
pip install "agentops-toolkit[agent]"
agentops agent analyze
```

This produces `.agentops/agent/report.md`. With no `agent.yaml`
present, only the local results-history source is active and Azure
Monitor / Foundry control plane appear as `skipped` in the
diagnostics block. That is enough for the basic regression and
latency checks across all your previous runs.

To pull production telemetry, drop a starter `agent.yaml` into the
workspace and edit it:

```powershell
$tpl = python -c "import agentops, pathlib; print(pathlib.Path(agentops.__file__).parent / 'templates' / 'agent.yaml')"
Copy-Item $tpl .agentops/agent.yaml
```

```yaml
sources:
  results_history:
    enabled: true
  azure_monitor:
    enabled: true
    app_insights_resource_id: /subscriptions/<sub>/resourceGroups/<rg>/providers/microsoft.insights/components/<ai>
  foundry_control:
    enabled: true
    project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
```

Re-run `agentops agent analyze`. The findings table now mixes signals
from your eval history (including the v1 → v2 tool-call regression)
with live telemetry from the deployed agent.

> **Optional — WAF-AI security audit.** The watchdog can also run a
> read-only audit of your Foundry resource group against the
> [Well-Architected Framework for AI workloads — Security pillar][waf-ai].
> Enable the `azure_resources` source and the `posture` check in
> `agent.yaml` (commented stanzas are included), grant your identity
> `Reader` on the resource group, and re-run with
> `agentops agent analyze --categories security`. Full walkthrough:
> [`tutorial-agent-watchdog.md`](tutorial-agent-watchdog.md#2b-security-posture-audit-waf-ai).

For deeper integration (Copilot Chat extension, ACA deploy), see
[`tutorial-agent-watchdog.md`](tutorial-agent-watchdog.md).

[waf-ai]: https://learn.microsoft.com/azure/well-architected/ai/security

## 10. Clean up

The two agent versions live in your Foundry project until you delete
them. The helper script handles cleanup:

```powershell
python create_support_agent.py delete --name support-bot
```

This removes every version (idempotent — ignores 404s).

## 11. Where to go next

You now have the full AgentOps loop running end-to-end with a real
tool-calling agent. From here:

- **Per-scenario tutorials** — adapt the dataset shape to your own
  agent:
  - [`tutorial-rag.md`](tutorial-rag.md) — retrieval-augmented agents.
  - [`tutorial-agent-workflow.md`](tutorial-agent-workflow.md) —
    focused tool-calling reference (single-tool variants, HTTP-hosted
    agents, dataset shape details).
  - [`tutorial-conversational-agent.md`](tutorial-conversational-agent.md)
    — multi-turn assistants.
  - [`tutorial-http-agent.md`](tutorial-http-agent.md) — agents
    deployed outside Foundry (ACA, AKS, custom).
  - [`tutorial-model-direct.md`](tutorial-model-direct.md) — raw
    model deployments without an agent layer.
- **Deeper baseline workflows** —
  [`tutorial-baseline-comparison.md`](tutorial-baseline-comparison.md).
- **Watchdog as a Copilot extension** —
  [`tutorial-agent-watchdog.md`](tutorial-agent-watchdog.md).
- **CI/CD reference** —
  [`ci-github-actions.md`](ci-github-actions.md).
- **Architecture and concepts** —
  [`how-it-works.md`](how-it-works.md),
  [`concepts.md`](concepts.md).
