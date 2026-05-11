# Tutorial: minimal quickstart

This tutorial covers the simplest end-to-end AgentOps flow: bootstrap a workspace, point it at any agent, and run an evaluation.

> Looking for the long-form, do-it-yourself tour that also covers
> a real tool-calling support agent, baseline comparison, GitFlow
> CI/CD, and the watchdog agent? See
> [tutorial-end-to-end.md](tutorial-end-to-end.md).

## What you will build

- A flat `agentops.yaml` at your project root.
- A small JSONL dataset.
- One `agentops eval run` execution producing `results.json` and `report.md`.

The former bundle-based, multi-file workspace has been replaced by this flat `agentops.yaml` workflow for the common case.

## Prerequisites

- Python 3.11 or later.
- Access to a target agent or model. Choose one:
  - A **Foundry prompt agent** identified by `name:version` (for example `customer-support:3`).
  - A **Foundry hosted endpoint** (`https://*.services.ai.azure.com/.../agents/<id>`).
  - A **generic HTTP/JSON agent** deployed anywhere (ACA, AKS, your own server).
  - A **raw Foundry model deployment** (e.g. `gpt-4o`).
- For Foundry targets: the Foundry project endpoint URL.
- For AI-assisted evaluators (Coherence, Groundedness, etc.): an Azure OpenAI endpoint and deployment to use as the judge model.

## 1. Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install --upgrade "agentops-toolkit[foundry] @ git+https://github.com/Azure/agentops.git@feature/test-tutorials"
agentops --version
```

## 2. Bootstrap the project

```powershell
agentops init
```

This creates two files:

- `agentops.yaml` — your evaluation config (3 lines + comments).
- `.agentops/data/smoke.jsonl` — a 3-row seed dataset with short,
  deterministic factual answers.

## 3. Configure your agent

Open `agentops.yaml` and set the `agent:` field. The classifier infers the target kind from the value:

| Value                                                    | Resolves to                          |
| -------------------------------------------------------- | ------------------------------------ |
| `"customer-support:3"`                                   | Foundry prompt agent (`name:version`) |
| `"https://<host>.services.ai.azure.com/.../agents/<id>"` | Foundry hosted endpoint              |
| `"https://api.example.com/chat"`                         | Generic HTTP/JSON agent              |
| `"model:gpt-4o"`                                         | Raw Foundry model deployment         |

For Foundry prompt agents, use the agent name plus the published version number shown in Foundry, without the `v` prefix. For example, an agent named `my-agent` with published version `v2` is referenced as `my-agent:2`.

The full minimal config is just:

```yaml
version: 1
agent: "customer-support:3"
dataset: .agentops/data/smoke.jsonl
```

If your target is a Foundry prompt agent (`name:version`) and you want the run to also appear in the New Foundry Evaluations panel, opt in to cloud publishing:

```yaml
version: 1
agent: "customer-support:3"
dataset: .agentops/data/smoke.jsonl
publish: foundry_cloud
```

## 4. Run the evaluation

Set credentials and run. These environment variables are required even if you also put `project_endpoint:` in `agentops.yaml`, because the local invocation and evaluator runtime read them from the process environment.

```powershell
az login
$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://<resource>.services.ai.azure.com/api/projects/<project>"
$env:AZURE_OPENAI_ENDPOINT = "https://<openai-resource>.openai.azure.com"
$env:AZURE_OPENAI_DEPLOYMENT = "gpt-4o-mini"
agentops eval run
```

Use the Azure OpenAI data-plane endpoint for `AZURE_OPENAI_ENDPOINT` (`*.openai.azure.com`, no `/api/projects/...` path), not the Foundry project endpoint.

Outputs:

```
.agentops/results/
├── 2026-05-06T14-30-22Z/   # Timestamped run (immutable history)
│   ├── results.json
│   ├── report.md
│   └── cloud_evaluation.json   # when publish: foundry_cloud is set
└── latest/                 # Mirror of the most recent run
    ├── results.json
    ├── report.md
    └── cloud_evaluation.json   # when publish: foundry_cloud is set
```

Without `publish`, AgentOps runs locally and only writes local artifacts. With `publish: foundry_cloud`, AgentOps still writes local artifacts first, then submits a server-side Foundry evaluation; `cloud_evaluation.json` includes the Foundry `eval_id`, `run_id`, status, and `report_url`.

To view the report rendered (tables, ✅/❌), open it in VS Code and press `Ctrl+Shift+V`:

```powershell
code .agentops/results/latest/report.md
```

The seed dataset asks the target to answer with exact short factual
sentences. That keeps the first run focused on proving the AgentOps loop
works instead of debugging subjective wording differences. After the
smoke test passes, replace the rows with domain-specific examples for
your agent.

The CLI prints `Threshold status: PASSED` (exit code `0`) or `FAILED` (exit code `2`) so you can wire it into CI directly.

## 5. Compare against a baseline

Each `agentops eval run` writes to a timestamped folder and refreshes
`.agentops/results/latest/`. To diff a new run against the previous
one, just point `--baseline` at it — no copy needed:

```powershell
# ... change your prompt, model, or dataset ...
agentops eval run --baseline .agentops/results/latest/results.json
```

AgentOps loads the baseline into memory before refreshing `latest/`,
so `latest/results.json` is shorthand for "the run before this one".
For a stable reference (e.g. a CI baseline), point at a specific
timestamp folder instead.

`report.md` now includes a `Comparison vs Baseline` section with per-metric deltas (🟢 improved / 🔴 regressed / ⚪ unchanged).

## Where evaluators come from

You did not pick evaluators — AgentOps inferred them:

- **Always:** Coherence, Fluency, Similarity, F1Score, average latency.
- **If your dataset rows include `context`:** Groundedness, Relevance, Retrieval, ResponseCompleteness.
- **If your dataset rows include `tool_calls` or `tool_definitions`:** TaskCompletion, ToolCallAccuracy, IntentResolution, TaskAdherence.

To override the auto-selection, list evaluator class names in `agentops.yaml`:

```yaml
evaluators:
  - GroundednessEvaluator
  - CoherenceEvaluator
```

## Where to go next

- [`docs/how-it-works.md`](how-it-works.md) — architecture and request flow.
- [`docs/ci-github-actions.md`](ci-github-actions.md) — wire AgentOps into PR checks with OIDC auth.
- The scenario tutorials use the same flat `agentops.yaml` workflow with more realistic datasets and targets.
