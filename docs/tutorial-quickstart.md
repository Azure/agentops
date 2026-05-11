# Tutorial: minimal quickstart

This tutorial covers the simplest end-to-end AgentOps flow: create a
small Foundry prompt agent, bootstrap an AgentOps workspace, run a
baseline evaluation, intentionally change the agent prompt, and compare
the new run against the baseline.

> Looking for the long-form, do-it-yourself tour that also covers
> a real tool-calling support agent, baseline comparison, GitFlow
> CI/CD, and the watchdog agent? See
> [tutorial-end-to-end.md](tutorial-end-to-end.md).

## What you will build

- A Foundry prompt agent with deterministic smoke-test instructions.
- A flat `agentops.yaml` at your project root.
- A small JSONL dataset.
- A baseline `agentops eval run` producing `results.json` and `report.md`.
- A second run compared against the baseline so the report shows prompt
  quality deltas.

The former bundle-based, multi-file workspace has been replaced by this flat `agentops.yaml` workflow for the common case.

## Prerequisites

- Python 3.11 or later.
- Permission to create or edit a **Foundry prompt agent** and publish a
  version identified by `name:version` (for example `agentops-smoke:1`).
- The Foundry project endpoint URL.
- For AI-assisted evaluators (Coherence, Groundedness, etc.): an Azure OpenAI endpoint and deployment to use as the judge model.

## 1. Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install --upgrade "agentops-toolkit[foundry]"
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

## 3. Create the smoke-test Foundry agent

In Azure AI Foundry, create a prompt agent named `agentops-smoke` with
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
`agentops-smoke:2`. Foundry's portal saves the first published version
as **v2** (v1 is reserved for the empty draft), so your baseline will
normally be `:2`, not `:1`. This prompt is intentionally strict because
the seed dataset checks whether the agent can follow exact-output
instructions.

## 4. Configure AgentOps

Open `agentops.yaml` and set `agent:` to your Foundry prompt agent using
the `name:version` format. Use the agent name plus the published version
number shown in Foundry, without the `v` prefix. For example, an agent
named `my-agent` with published version `v2` is referenced as
`my-agent:2`.

The full minimal config is just:

```yaml
version: 1                       # schema version of agentops.yaml itself
agent: "agentops-smoke:2"        # ':2' is the Foundry agent version
dataset: .agentops/data/smoke.jsonl
execution: cloud                 # Foundry runs the agent + evaluators server-side
```

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
published version — they are independent.

> AgentOps also supports hosted Foundry endpoints, generic HTTP/JSON
> endpoints, and raw model deployments. Those are covered in the scenario
> tutorials; this quickstart keeps the path focused on a Foundry prompt
> agent.

## 5. Run the baseline evaluation

Set credentials and run. For Foundry targets, provide the project endpoint either in `agentops.yaml` as `project_endpoint:` or in `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`; if both are set, `agentops.yaml` wins for target invocation and publishing.

```powershell
az login
$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://<resource>.services.ai.azure.com/api/projects/<project>"
$env:AZURE_OPENAI_ENDPOINT = "https://<openai-resource>.openai.azure.com"
$env:AZURE_OPENAI_DEPLOYMENT = "gpt-4o-mini"
agentops eval run
```

> **Important:** run this command **without** `--baseline`. The baseline
> file does not exist yet — you are creating it in this step.

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

To view the report rendered (tables, ✅/❌), open it in VS Code and press `Ctrl+Shift+V`:

```powershell
code .agentops/results/latest/report.md
```

The seed dataset asks the target to answer with exact short factual
sentences. The prompt from step 3 is designed to pass this smoke test, so
this first successful run is your baseline.

**Capture the baseline now** — the comparison run in step 6 requires
this file to exist:

```powershell
New-Item -ItemType Directory -Force .agentops\baseline | Out-Null
Copy-Item .agentops\results\latest\results.json .agentops\baseline\results.json
```

The CLI prints `Threshold status: PASSED` (exit code `0`) or `FAILED` (exit code `2`) so you can wire it into CI directly.

## 6. Change the prompt and compare against the baseline

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
after your baseline — typically `:3`):

```yaml
version: 1
agent: "agentops-smoke:3"
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
