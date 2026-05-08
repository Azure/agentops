# Tutorial — Copilot-assisted AgentOps workflow

This tutorial shows how to use the AgentOps coding-agent skills as a
guided development workflow. Instead of memorizing the AgentOps schema,
you let Copilot inspect the project, generate the config and dataset, run
the eval, explain the report, and create the CI/CD workflow.

The tutorial is still fully executable without guessing: each Copilot
prompt is followed by the concrete file or command you should expect.

## What you will build

- A small HTTP support agent that answers three customer-service
  questions.
- Installed AgentOps skills under `.github/skills/`.
- A flat `agentops.yaml` generated from project context.
- A JSONL dataset generated for the agent's behavior.
- One passing local evaluation and a readable `report.md`.
- GitHub Actions workflow files generated from the skill-guided flow.

## Prerequisites

- Python 3.11 or later.
- GitHub Copilot Chat or Copilot CLI with repository context.
- Azure CLI login and a judge-model deployment for AI-assisted evaluators.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install "agentops-toolkit @ git+https://github.com/Azure/agentops.git@develop"

az login
$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://<resource>.services.ai.azure.com/api/projects/<project>"
$env:AZURE_OPENAI_ENDPOINT             = "https://<resource>.openai.azure.com"
$env:AZURE_OPENAI_DEPLOYMENT           = "gpt-4o-mini"
```

> If you are testing unreleased AgentOps changes locally, install from
> your checkout instead:
>
> ```powershell
> python -m pip install -e "C:\path\to\agentops[foundry,agent]"
> ```

## 1. Create the sample agent

Create `support_agent.py`:

```python
from http.server import BaseHTTPRequestHandler, HTTPServer
import json


RESPONSES = {
    "Where is my order ORD-12345?": "Order ORD-12345 is in transit and expected to arrive tomorrow.",
    "Can I return a damaged headset from ORD-77821?": "Yes. Start a return for ORD-77821 and choose damaged item as the reason.",
    "How do I contact a human support agent?": "I can connect you to a human support agent for account or order issues.",
}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length))
        message = body.get("message", "")
        text = RESPONSES.get(message, "I can help with order status, returns, and support escalation.")

        payload = json.dumps({"text": text}).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


HTTPServer(("127.0.0.1", 8790), Handler).serve_forever()
```

Start it in a second terminal:

```powershell
.\.venv\Scripts\Activate.ps1
python support_agent.py
```

## 2. Initialize AgentOps and install skills

```powershell
agentops init
agentops skills install --platform copilot --force
```

You should now have:

```text
agentops.yaml
.agentops/data/smoke.jsonl
.github/skills/
  agentops-config/SKILL.md
  agentops-dataset/SKILL.md
  agentops-eval/SKILL.md
  agentops-report/SKILL.md
  agentops-workflow/SKILL.md
```

The skills are workflow instructions for Copilot. They tell Copilot how
to inspect the workspace, which AgentOps files to create, which commands
are valid, and when to ask for missing values instead of inventing them.

## 3. Ask Copilot to configure AgentOps

In Copilot Chat, ask:

```text
Use the agentops-config skill. Inspect this project and create an
AgentOps config for the local HTTP support agent on port 8790.
```

Expected `agentops.yaml`:

```yaml
version: 1
agent: "http://127.0.0.1:8790/"
dataset: .agentops/data/support-agent.jsonl

request_field: message
response_field: text

thresholds:
  coherence: ">=3"
  fluency: ">=3"
  similarity: ">=3"
  avg_latency_seconds: "<=2"
```

Why this is the right config:

- `agent` is the local HTTP endpoint.
- `request_field` matches `body.get("message")` in `support_agent.py`.
- `response_field` matches the returned JSON key `{ "text": ... }`.
- The thresholds are intentionally simple for the first smoke gate.

## 4. Ask Copilot to generate the dataset

In Copilot Chat, ask:

```text
Use the agentops-dataset skill. Generate a small deterministic JSONL
dataset for the support agent behavior in support_agent.py.
```

Expected `.agentops/data/support-agent.jsonl`:

```jsonl
{"input":"Where is my order ORD-12345?","expected":"Order ORD-12345 is in transit and expected to arrive tomorrow."}
{"input":"Can I return a damaged headset from ORD-77821?","expected":"The customer can start a return for ORD-77821 and choose damaged item as the reason."}
{"input":"How do I contact a human support agent?","expected":"The assistant can connect the customer to a human support agent for account or order issues."}
```

The dataset uses exact intents that the sample app implements. That makes
the first run a configuration smoke test: if it fails, you likely have a
field mapping, endpoint, auth, or environment problem rather than a
prompt-quality problem.

## 5. Ask Copilot to run the eval

In Copilot Chat, ask:

```text
Use the agentops-eval skill. Run the evaluation and explain any failure.
```

Expected command:

```powershell
agentops eval run
```

Expected outputs:

```text
.agentops/results/<timestamp>/results.json
.agentops/results/<timestamp>/report.md
.agentops/results/latest/results.json
.agentops/results/latest/report.md
```

Exit code `0` means the config, dataset, HTTP agent, and thresholds all
worked. Exit code `2` means the run completed but one or more thresholds
failed. Exit code `1` means a runtime/configuration error.

## 6. Ask Copilot to interpret the report

In Copilot Chat, ask:

```text
Use the agentops-report skill. Read the latest report and summarize the
strongest rows, weakest rows, and next improvement.
```

A useful answer should not just say "pass" or "fail". It should point to:

- the threshold table in `.agentops/results/latest/report.md`;
- the lowest-scoring row or metric;
- whether latency is agent runtime or evaluator overhead;
- a concrete next change, such as improving an answer or tightening a
  threshold after repeated passing runs.

## 7. Ask Copilot to add the PR gate

In Copilot Chat, ask:

```text
Use the agentops-workflow skill. Generate the GitHub Actions workflow
files and tell me which GitHub environment variables are required.
```

Expected command:

```powershell
agentops workflow generate
```

Expected workflow files:

```text
.github/workflows/agentops-pr.yml
.github/workflows/agentops-deploy-dev.yml
.github/workflows/agentops-deploy-qa.yml
.github/workflows/agentops-deploy-prod.yml
```

For this HTTP tutorial, the PR gate needs the same evaluator-model values
you used locally:

| GitHub variable | Purpose |
|---|---|
| `AZURE_CLIENT_ID` | OIDC identity used by `azure/login`. |
| `AZURE_TENANT_ID` | Tenant for the OIDC login. |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription for the login. |
| `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` | Foundry project used by AI-assisted evaluators. |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint for the judge model. |
| `AZURE_OPENAI_DEPLOYMENT` | Judge model deployment, for example `gpt-4o-mini`. |

If your HTTP agent is remote and protected, also add the token variable
referenced by `auth_header_env`.

Because this tutorial starts the sample agent on `127.0.0.1`, GitHub
Actions must start that process before `agentops eval run`. For this
sample repo, add this step between **Install AgentOps Toolkit** and
**Run AgentOps eval** in `agentops-pr.yml`:

```yaml
      - name: Start local tutorial agent
        run: |
          python support_agent.py &
          sleep 2
```

For a deployed ACA/AKS/App Service endpoint, skip that step and point
`agent:` at the public or private URL your runner can reach.

## 8. Push the tutorial repo

```powershell
git init -b main
git add .
git commit -m "feat: add Copilot-assisted AgentOps eval"
gh repo create "agentops-copilot-skills-<suffix>" --public --source=. --push
```

The first PR against `main` or `develop` will run `agentops-pr.yml`.
When it finishes, open the workflow artifact or PR comment to view the
same `report.md` you inspected locally.

## What Copilot should have learned

The skills keep Copilot inside the AgentOps contract:

- `agentops-config` creates a flat `agentops.yaml`, not legacy
  `run.yaml` / bundle / dataset config files.
- `agentops-dataset` creates rows tailored to the app instead of generic
  trivia.
- `agentops-eval` runs `agentops eval run` and respects exit codes.
- `agentops-report` turns metrics into actionable insights.
- `agentops-workflow` generates the standard GitFlow workflow scaffold
  without inventing unsupported flags or commands.

That is the intended AgentOps development loop: Copilot accelerates the
file creation and interpretation, while AgentOps supplies the repeatable
evaluation contract.
