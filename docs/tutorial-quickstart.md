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

The rest of the toolkit (legacy bundles, multi-file workspaces, custom adapters) still works, but is not required for the common case.

## Prerequisites

- Python 3.11 or later.
- Access to a target agent or model. Choose one:
  - A **Foundry prompt agent** identified by `name:version` (for example `customer-support:3`).
  - A **Foundry hosted endpoint** (`https://*.services.ai.azure.com/.../agents/<id>`).
  - A **generic HTTP/JSON agent** deployed anywhere (ACA, AKS, your own server).
  - A **raw Foundry model deployment** (e.g. `gpt-4o`).
- For Foundry targets: `az login` (or a service principal) and `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` set.
- For AI-assisted evaluators (Coherence, Groundedness, etc.): `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_DEPLOYMENT` set.

## 1. Install

```bash
python -m venv .venv
python -m pip install -U pip
python -m pip install agentops-toolkit
```

## 2. Bootstrap the project

```bash
agentops init
```

This creates two files:

- `agentops.yaml` — your evaluation config (3 lines + comments).
- `.agentops/data/smoke.jsonl` — a 3-row seed dataset.

## 3. Configure your agent

Open `agentops.yaml` and set the `agent:` field. The classifier infers the target kind from the value:

| Value                                                    | Resolves to                          |
| -------------------------------------------------------- | ------------------------------------ |
| `"customer-support:3"`                                   | Foundry prompt agent (`name:version`) |
| `"https://<host>.services.ai.azure.com/.../agents/<id>"` | Foundry hosted endpoint              |
| `"https://api.example.com/chat"`                         | Generic HTTP/JSON agent              |
| `"model:gpt-4o"`                                         | Raw Foundry model deployment         |

The full minimal config is just:

```yaml
version: 1
agent: "customer-support:3"
dataset: .agentops/data/smoke.jsonl
```

## 4. Run the evaluation

Set credentials and run:

```bash
export AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="https://<resource>.services.ai.azure.com/api/projects/<project>"
agentops eval run
```

Outputs:

```
.agentops/results/
├── 2026-05-06T14-30-22Z/   # Timestamped run (immutable history)
│   ├── results.json
│   └── report.md
└── latest/                 # Mirror of the most recent run
    ├── results.json
    └── report.md
```

To view the report rendered (tables, ✅/❌), open it in VS Code and press `Ctrl+Shift+V`:

```bash
code .agentops/results/latest/report.md
```

The CLI prints `Threshold status: PASSED` (exit code `0`) or `FAILED` (exit code `2`) so you can wire it into CI directly.

## 5. Compare against a baseline

Each `agentops eval run` writes to a timestamped folder and refreshes
`.agentops/results/latest/`. To diff a new run against the previous
one, just point `--baseline` at it — no copy needed:

```bash
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
- The existing tutorials still apply if you stay on the legacy multi-file layout.
