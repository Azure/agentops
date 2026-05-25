# Quickstart: Foundry Prompt Agent

Use this quickstart when your agent already exists in Microsoft Foundry as a
prompt agent version, for example `travel-agent:1`.

This path validates the Foundry-native route:

- Foundry owns the prompt agent runtime and official AI Agent Evaluation.
- AgentOps owns repo-side readiness: `agentops.yaml`, CI gates, Doctor,
  release evidence, and Cockpit.

## Journey you will exercise

| Step | Main tool | What you do | AgentOps role |
|---|---|---|---|
| Create or select the agent | Foundry portal, Foundry SDK, Foundry Toolkit, or `microsoft-foundry` skill | Create or choose a prompt agent version. | No ownership; AgentOps consumes the released target. |
| Try and debug | Foundry playground, VS Code, Copilot Chat | Validate behavior before adding release gates. | Optional quick eval later. |
| Evaluate in CI | Official Microsoft AI Agent Evaluation | Run Foundry-native evaluation for `name:version`. | Generates routing and records evidence. |
| Review readiness | AgentOps Doctor and Cockpit | Check CI, eval, telemetry, evidence, and links. | Primary owner of repo-side release proof. |

## 1. Create or select a Prompt Agent in Foundry

Before running AgentOps, create or select the prompt agent with the official
Foundry tools:

1. In the Foundry portal, create or open the prompt agent.
2. Test it in the playground or from VS Code.
3. Confirm the agent has a version such as `travel-agent:1`.
4. If you use Copilot with Foundry Skills, ask the `microsoft-foundry` skill to
   inspect or evaluate the agent, but keep AgentOps for repo-side release proof.

The output of this step is the agent reference you will put in `agentops.yaml`.

## 2. Create a clean workspace

```powershell
mkdir C:\Users\paulolacerda\workspace\test-agentops-prompt
cd C:\Users\paulolacerda\workspace\test-agentops-prompt
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e "C:\Users\paulolacerda\workspace\agentops[foundry,agent]"
agentops --version
```

For a public install before the next package release, use:

```powershell
python -m pip install "agentops-toolkit[foundry,agent] @ git+https://github.com/Azure/agentops.git@main"
```

## 3. Sign in and capture Foundry values

```powershell
az login
```

You need:

| Value | Example |
|---|---|
| Foundry project endpoint | `https://<resource>.services.ai.azure.com/api/projects/<project>` |
| Prompt agent reference | `travel-agent:1` |
| Evaluator model deployment | `gpt-4o-mini` |
| Application Insights connection string | optional, but recommended |

Set the deployment used by the official evaluator:

```powershell
$env:AZURE_OPENAI_DEPLOYMENT = "gpt-4o-mini"
```

## 4. Initialize AgentOps

```powershell
agentops init `
  --dir . `
  --azd-env dev `
  --project-endpoint "https://<resource>.services.ai.azure.com/api/projects/<project>" `
  --agent "travel-agent:1" `
  --dataset ".agentops/data/smoke.jsonl" `
  --no-prompt
```

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
dataset: .agentops/data/smoke.jsonl
project_endpoint: https://<resource>.services.ai.azure.com/api/projects/<project>
```

## 5. Check the selected eval runner

```powershell
agentops workflow analyze --format text
```

For `agent: name:version`, AgentOps should recommend:

```text
recommended_eval_runner: official-ai-agent-evaluation
```

That means generated CI uses the official Microsoft AI Agent Evaluation runner
for the eval step, then uses AgentOps to collect evidence and readiness signals.

## 6. Generate the PR gate

```powershell
agentops workflow generate --kinds pr,watchdog --force
```

The PR workflow should contain the official eval action:

```text
microsoft/ai-agent-evals@v3-beta
```

It also records:

```text
.agentops/official-eval/metadata.json
.agentops/official-eval/result.json
.agentops/release/latest/evidence.md
```

## 7. Run Doctor and create release evidence locally

```powershell
agentops doctor --workspace . --evidence-pack
code .agentops/release/latest/evidence.md
```

Doctor is read-only. It checks whether the repo has the signals a release
reviewer needs: eval gates, telemetry wiring, CI, trace-regression readiness,
and links back to Foundry where Foundry owns the runtime view.

## 8. Open Cockpit

```powershell
agentops cockpit --workspace .
```

Open the local URL printed by the command. The Cockpit should show Foundry
connection, official eval readiness, Doctor findings, release evidence, CI/CD,
and next actions.

## Success criteria

You are done when:

- `agentops workflow analyze` selects `official-ai-agent-evaluation`.
- `agentops workflow generate` creates a PR workflow with
  `microsoft/ai-agent-evals@v3-beta`.
- `agentops doctor --evidence-pack` writes
  `.agentops/release/latest/evidence.md`.
- Cockpit opens and links the repo-side readiness view back to Foundry.
