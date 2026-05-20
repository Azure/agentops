# Tutorial: Copilot skills for AgentOps

AgentOps skills are instructions for a coding agent. They help GitHub
Copilot, Copilot CLI, Cursor, or Claude Code inspect your repository and
produce the right AgentOps files and commands.

They are not the same thing as the AgentOps Watchdog runtime:

| Concept | Where it lives | What it does |
|---|---|---|
| Coding-agent skills | `.github/skills/` or `.claude/commands/` | Guide Copilot to create config, datasets, workflows, evals, reports, and Watchdog commands. |
| Watchdog runtime | `agentops doctor` / `agentops agent serve` | Reads real eval history, Azure Monitor telemetry, and Foundry metadata to produce findings. |
| `agentops-agent` skill | Installed skill file | The Copilot-facing workflow for invoking Watchdog. It does not invent findings. |

## Implemented skills

The current CLI installs these skills:

| Skill | Responsibility |
|---|---|
| `agentops-config` | Generate or update flat `agentops.yaml` from project context. |
| `agentops-dataset` | Create realistic JSONL evaluation rows. |
| `agentops-eval` | Run evals, handle exit codes, and compare against baselines. |
| `agentops-report` | Explain `results.json` and `report.md`. |
| `agentops-workflow` | Generate supported GitHub Actions workflows and explain required GitHub/Azure wiring. |
| `agentops-agent` | Run and interpret Watchdog (`agentops doctor` / `serve`). |

There are no shipped `agentops-monitor`, `agentops-trace`, or
`agentops-regression` skills in this implementation. Monitoring,
tracing, and regression analysis belong to the Watchdog runtime and
reports until dedicated skills are implemented.

## Installation options

### Option 1: VS Code extension

Install **AgentOps Skills** from the VS Code Marketplace. Use this when
you want Copilot in VS Code to discover the packaged skills through the
extension/plugin experience.

### Option 2: CLI install into a repository

Use this when you want the skills checked into a repo:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install "agentops-toolkit[foundry,agent]"

agentops skills install --platform copilot
```

Expected files:

```text
.github/copilot-instructions.md
.github/skills/agentops-config/SKILL.md
.github/skills/agentops-dataset/SKILL.md
.github/skills/agentops-eval/SKILL.md
.github/skills/agentops-report/SKILL.md
.github/skills/agentops-workflow/SKILL.md
.github/skills/agentops-agent/SKILL.md
```

Use `--platform claude` for `.claude/commands/*.md`, `--platform cursor`
for Cursor rules, or omit `--platform` and let AgentOps auto-detect the
repo.

## Scenario: use Copilot to set up AgentOps for a real HTTP agent

This scenario assumes you already built and deployed the Azure Container
Apps support agent from [tutorial-http-agent.md](tutorial-http-agent.md).
That tutorial gives you a URL like:

```text
https://<container-app>.<region>.azurecontainerapps.io/chat
```

Set local evaluator variables:

```powershell
$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://<resource>.services.ai.azure.com/api/projects/<project>"
$env:AZURE_OPENAI_ENDPOINT             = "https://<resource>.openai.azure.com"
$env:AZURE_OPENAI_DEPLOYMENT           = "gpt-4o-mini"
```

`AZURE_AI_MODEL_DEPLOYMENT_NAME` is accepted as a fallback name for the
judge deployment if you prefer the Foundry-style variable. Set only one
of the two — `AZURE_OPENAI_DEPLOYMENT` wins when both are set.

## 1. Ask Copilot to configure AgentOps

Prompt Copilot:

```text
Use the agentops-config skill. Inspect this repository and create an
AgentOps config for the deployed HTTP support agent. The agent URL is
https://<container-app>.<region>.azurecontainerapps.io/chat. The request
field is message, the final answer is in text, and returned tool calls
are in tool_calls.
```

Expected `agentops.yaml`:

```yaml
version: 1
agent: "https://<container-app>.<region>.azurecontainerapps.io/chat"
dataset: .agentops/data/support-tools.jsonl

request_field: message
response_field: text
tool_calls_field: tool_calls

thresholds:
  coherence: ">=3"
  fluency: ">=3"
  tool_call_accuracy: ">=0.8"
  intent_resolution: ">=3"
  task_adherence: ">=0.6"
  avg_latency_seconds: "<=30"
```

What Copilot should explain:

- `agent` is the deployed URL, not a local loopback address.
- `request_field` matches the HTTP request body the agent expects.
- `response_field` and `tool_calls_field` match the JSON response.
- Tool thresholds are included because the endpoint returns tool traces.

## 2. Ask Copilot to create the dataset

Prompt Copilot:

```text
Use the agentops-dataset skill. Generate a deterministic JSONL dataset
for this support agent. Include order lookup, refund, and no-tool greeting
rows. Include tool_definitions and expected tool_calls on every row.
```

Expected file: `.agentops/data/support-tools.jsonl`

```jsonl
{"input":"Where is my order ORD-12345?","expected":"Order ORD-12345 is in transit and expected to arrive tomorrow.","tool_definitions":[{"type":"function","name":"lookup_order","description":"Look up an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"}},"required":["order_id"]}},{"type":"function","name":"refund_order","description":"Refund an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"},"reason":{"type":"string"}},"required":["order_id","reason"]}}],"tool_calls":[{"type":"tool_call","tool_call_id":"lookup_1","name":"lookup_order","arguments":{"order_id":"ORD-12345"}}]}
{"input":"I want a refund for ORD-77821, it arrived broken.","expected":"A refund is started for ORD-77821 because it arrived broken.","tool_definitions":[{"type":"function","name":"lookup_order","description":"Look up an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"}},"required":["order_id"]}},{"type":"function","name":"refund_order","description":"Refund an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"},"reason":{"type":"string"}},"required":["order_id","reason"]}}],"tool_calls":[{"type":"tool_call","tool_call_id":"refund_1","name":"refund_order","arguments":{"order_id":"ORD-77821","reason":"arrived broken"}}]}
{"input":"Hi there!","expected":"The assistant replies with a clear greeting and offers support options without calling a tool.","tool_definitions":[{"type":"function","name":"lookup_order","description":"Look up an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"}},"required":["order_id"]}},{"type":"function","name":"refund_order","description":"Refund an order.","parameters":{"type":"object","properties":{"order_id":{"type":"string"},"reason":{"type":"string"}},"required":["order_id","reason"]}}],"tool_calls":[]}
```

The no-tool greeting row prevents a common regression: agents that call a
business action even when the user only greets them.

## 3. Ask Copilot to run the eval

Prompt Copilot:

```text
Use the agentops-eval skill. Run the AgentOps evaluation. If it fails,
explain whether the failure is config, endpoint reachability, auth, tool
trace mismatch, or threshold quality.
```

Expected command:

```powershell
agentops eval run
```

Expected outputs:

```text
.agentops/results/latest/results.json
.agentops/results/latest/report.md
```

Exit codes:

| Code | Meaning |
|---|---|
| `0` | Eval succeeded and thresholds passed. |
| `2` | Eval succeeded but one or more thresholds failed. |
| `1` | Runtime or configuration error. |

## 4. Ask Copilot to explain the report

Prompt Copilot:

```text
Use the agentops-report skill. Read .agentops/results/latest/report.md
and summarize the verdict, weakest metric, weakest row, and next code or
dataset change.
```

A useful answer should cite concrete report evidence. It should not just
say "passed". For tool agents, it should mention:

- whether text-quality thresholds passed;
- whether `tool_call_accuracy` passed;
- which row had the weakest intent or adherence score;
- whether latency looks like an endpoint problem.

## 5. Ask Copilot to add CI

Prompt Copilot:

```text
Use the agentops-workflow skill. Add a GitHub Actions PR gate only, then
tell me which GitHub environment variables and Azure federated credential
are required before I push it.
```

Expected command:

```powershell
agentops workflow generate --kinds pr --force
```

Why PR-only? The full `agentops workflow generate` scaffold includes
DEV/QA/PROD deploy workflows. Those are correct for a real release
pipeline, but they must not be pushed until GitHub Environments, Azure
OIDC, and either `azure.yaml` or real placeholder replacements are
configured. Otherwise the first push to `main` will create a red deploy
workflow before the tutorial teaches anything useful.

Configure the `dev` environment variables:

```powershell
$repo = "<owner>/<repo>"

gh api -X PUT "repos/$repo/environments/dev" | Out-Null
gh variable set AZURE_CLIENT_ID --repo $repo --env dev --body "<app-registration-client-id>"
gh variable set AZURE_TENANT_ID --repo $repo --env dev --body "<tenant-id>"
gh variable set AZURE_SUBSCRIPTION_ID --repo $repo --env dev --body "<subscription-id>"
gh variable set AZURE_AI_FOUNDRY_PROJECT_ENDPOINT --repo $repo --env dev --body $env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
gh variable set AZURE_OPENAI_ENDPOINT --repo $repo --env dev --body $env:AZURE_OPENAI_ENDPOINT
gh variable set AZURE_OPENAI_DEPLOYMENT --repo $repo --env dev --body $env:AZURE_OPENAI_DEPLOYMENT
```

Add an Azure federated credential with subject:

```text
repo:<owner>/<repo>:environment:dev
```

Open a PR and wait for `AgentOps PR` to pass before merging.

## 6. Ask Copilot to run Watchdog

Prompt Copilot:

```text
Use the agentops-agent skill. Run Watchdog against this repository's
AgentOps results and summarize the top findings. Do not invent telemetry;
if a source is skipped, say why.
```

Expected command:

```powershell
agentops doctor --severity-fail critical
```

Expected output:

```text
.agentops/agent/report.md
```

The Watchdog report should list:

- sources that ran, such as results history;
- sources that skipped, such as Azure Monitor if `.agentops/agent.yaml`
  has no Application Insights resource id;
- findings sorted by severity;
- recommendations generated from the analyzer, not from Copilot guesses.

## 7. When to generate the full CI/CD scaffold

After the PR gate is green and you have an azd deployment or real
deployment commands, ask:

```text
Use the agentops-workflow skill. Generate the full dev/qa/prod workflow
scaffold. If this repo has azure.yaml, use azd provision/deploy; otherwise
wire placeholders to this repository's actual build and deploy commands.
```

Expected command:

```powershell
agentops workflow generate --kinds pr,dev,qa,prod --deploy-mode auto --force
```

Before pushing those files, verify:

- GitHub Environments `dev`, `qa`, and `production` exist.
- Production has required reviewers.
- Azure federated credentials exist for every workflow subject.
- If using azd, `azure.yaml`, `infra/`, and azd hooks are committed.
- If using placeholder mode, build and deploy placeholders are replaced
  with real commands.

That is the difference between a useful CI/CD tutorial and a red Action
that only proves the repo was not configured.
