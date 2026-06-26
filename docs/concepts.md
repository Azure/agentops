# Concepts

This page explains the core AgentOps building blocks. For the full schema
reference and architecture details, see [how-it-works.md](how-it-works.md).

## AgentOps + Microsoft Foundry

AgentOps helps teams answer two release questions for a Foundry agent:
**can we ship it, and where is the proof?**

Foundry is the agent control plane: create, deploy, run, trace, monitor,
evaluate, and investigate. AgentOps is the repo-side operating layer that turns
those signals into a repeatable release loop:

- Source-controlled eval config
- Local and CI quality gates
- Stable `results.json` and PR-friendly `report.md`
- Doctor diagnostics and release evidence
- Trace-to-dataset promotion
- Generated workflows and a local Cockpit

Use Foundry for runtime, evaluation, observability, and official drilldown. Use
AgentOps to keep the release contract close to the code: initialize, evaluate,
compare, run Doctor, produce evidence, and promote reviewed traces back into
regression data.

The short version is: **Foundry operates the agent; AgentOps turns that operating
signal into repo-side release proof.**

## How an Evaluation Works

```mermaid
graph TD
    A[agentops.yaml target dataset thresholds]
    B[JSONL dataset rows]
    C[AgentOps runner]
    D[Foundry target]
    E[HTTP target]
    F[Model target]
    G[Evaluators and thresholds]
    H[results.json]
    I[report.md]

    A --> C
    B --> C
    C --> D
    C --> E
    C --> F
    D --> G
    E --> G
    F --> G
    G --> H
    G --> I
```

> Exit code: `0` = pass, `2` = threshold fail, `1` = error

## Core Concepts

### Workspace

Created by `agentops init`. The release-gate config lives in the flat
`agentops.yaml` file at the project root. `.agentops/` is the
AgentOps-managed workspace for seed inputs, local run history, Doctor cache,
and generated release evidence. It is not a second `.foundry/` directory.

```text
agentops.yaml          # flat config: agent, dataset, thresholds
.agentops/
├── data/              # dataset rows (JSONL)
├── results/           # run outputs + latest/ pointer
├── agent/             # Doctor history and reports
└── release/           # evidence.json/evidence.md when generated
```

### AgentOps Config

A YAML file named `agentops.yaml` that connects **what** to evaluate,
**which dataset** to use, and **which thresholds** gate the run.

The minimum is:

```yaml
version: 1
agent: "my-agent:1"
dataset: .agentops/data/smoke.jsonl
```

Common `agent:` values:

| Agent value | Target kind |
|---|---|
| `"support-bot:1"` | Foundry prompt agent (`name:version`) |
| `"https://...services.ai.azure.com/.../agents/<id>"` | Foundry hosted agent endpoint |
| `"https://api.example.com/chat"` | HTTP/JSON agent |
| `"model:gpt-4o-mini"` | Direct model deployment |

HTTP targets can add top-level mapping fields such as `request_field`,
`response_fields`, `tool_calls_field`, `auth_header_env`, and `extra_fields`.
Use `response_fields.response` for the final answer and
`response_fields.context` for retrieved context. Use `response_source: dataset`
when each dataset row already contains the response to evaluate.

### Dataset

A JSONL file containing evaluation rows. Each row has an `input` prompt
and usually an `expected` reference answer. Some scenarios add extra
fields like `context` (RAG), `tool_definitions`, or `tool_calls` (agent
workflows).

```json
{"id": "1", "input": "What is Python?", "expected": "Python is a programming language."}
```

### Evaluator

A scoring function that measures one aspect of the target response.
Evaluators can be:

- **AI-assisted** (Foundry) - use a judge model to score responses on
  criteria like coherence, fluency, similarity, or groundedness.
- **Local metrics** - computed without a judge model, such as
  `avg_latency_seconds` or `F1ScoreEvaluator` for exact-reference model checks.

AgentOps auto-selects evaluators from the target kind and dataset shape.
Use `evaluators:` in `agentops.yaml` only when you need to override that
selection. See
[foundry-evaluation-sdk-built-in-evaluators.md](foundry-evaluation-sdk-built-in-evaluators.md)
for the complete evaluator reference.

### Target resolver

The execution engine sends dataset rows to the target and collects
responses. AgentOps automatically selects the target kind from `agent:`.

| `agent:` shape | Target kind | Use case |
|---|---|---|
| `name:version` | Foundry prompt agent | Foundry Agent Service agents |
| `https://...services.ai.azure.com/.../agents/...` | Foundry hosted agent endpoint | Hosted Agent URL target |
| `https://...` | HTTP/JSON endpoint | LangGraph, Agent Framework, ACA, AKS, custom REST |
| `model:<deployment>` | Model-direct | Raw model deployment checks |

### Prompt Agent vs Hosted Agent

| Term | What it means | Who creates/deploys it | How AgentOps uses it |
|---|---|---|---|
| **Foundry Prompt Agent** | A Foundry-managed agent version referenced as `name:version`. | Foundry portal, Foundry SDK/Toolkit, or `microsoft-foundry` skill. | Can run locally through AgentOps or server-side with `execution: cloud`. |
| **Foundry Hosted Agent** | A deployed agent endpoint on a Foundry domain. | Foundry Agent Service / Hosted Agent tooling. | Evaluated as a URL target by the AgentOps local runner. |
| **HTTP/JSON agent** | A custom endpoint outside the Foundry agent URL shape. | The app's own deployment path, often azd, ACA, AKS, or another host. | Evaluated as a URL target with request/response mapping fields. |

AgentOps does not replace the tools that create or deploy agents. It references
the candidate that those tools produced and turns the evaluation, Doctor, and
evidence outputs into a release gate.

### Evaluation path

| Target | Foundry server-side eval through AgentOps | AgentOps local runner | Recommended default |
|---|---|---|---|
| Foundry Prompt Agent (`name:version`) | Yes, with `execution: cloud` | Yes | Use cloud for official Foundry-hosted runs; use local for fast feedback or fallback. |
| Foundry Hosted Agent URL | No | Yes | Use local runner; optionally publish local metrics to Foundry with `publish: true`. |
| Generic HTTP/JSON endpoint | No | Yes | Use local runner. |
| Raw model deployment (`model:<name>`) | No | Yes | Use local runner. |

For prompt-agent CI pipelines, prefer AgentOps cloud eval when the repo needs a
merge/deploy gate: Foundry executes the managed eval, and AgentOps enforces
thresholds, baselines, Doctor readiness, release evidence, and
trace-to-regression review. The standalone Microsoft Foundry AI Agent Evaluation
Action/task remains useful for platform-native validation outside the AgentOps
release-readiness flow.

If a Foundry project already uses the public-preview `azd ai agent eval` recipe
flow, set `execution: azd` and `eval_recipe: eval.yaml`. AgentOps delegates eval
execution to azd, then normalizes metrics, binds thresholds, writes
`results.json`, and fails closed for thresholds that have no emitted metric.
Rubric evaluator dimensions are treated as first-class metric names.

Governance artifacts are optional evidence inputs:

```yaml
assert_path: .assert/evaluation-policy.yaml
acs_path: acs.yaml
redteam_path: .agentops/governance/redteam-plan.md
```

AgentOps records path, hash, status, and ACS checkpoint coverage in Doctor,
Cockpit, and release evidence. ASSERT execution, ACS enforcement, Guided
Guardrail setup, and red-team scans remain in their owning tools.

## Evaluation Scenarios

AgentOps auto-selects common evaluation patterns from the dataset:

| Scenario | Signal | Purpose |
|---|---|---|
| **Model quality** | `model:<deployment>` + `expected` | Direct model checks |
| **RAG** | `context` | Grounding and retrieval checks |
| **Conversational** | `input` + `expected` | Chatbot and Q&A quality |
| **Agent workflow** | `tool_calls` + `tool_definitions` | Tool-use quality |
| **Content safety** | Safety evaluators | Responsible AI checks |

Use one of the three hands-on tutorials for scenario coverage:

- [Evaluation paths](evaluation.md) explains when to use a static dataset,
  grey-box HTTP response mapping, or telemetry/trace import.
- [Foundry Prompt Agent tutorial](tutorial-prompt-agent-quickstart.md) for Foundry
  prompt agents referenced as `name:version`.
- [Hosted or HTTP Agent tutorial](tutorial-hosted-agent-quickstart.md) for Foundry
  hosted endpoints, generic HTTP agents, RAG services, and code-based workflows.
- [End-to-end tutorial](tutorial-end-to-end.md) for the complete Foundry +
  AgentOps loop, including CI/CD, observability, red-team follow-through,
  Doctor, release evidence, and trace regression.

## Configuration Model

`agentops.yaml` is the single source of truth. Keep it small and add only
the fields your target needs:

```yaml
version: 1
agent: "https://api.example.com/chat"
dataset: .agentops/data/support.jsonl
response_source: agent

protocol: http-json
request_field: message
response_fields:
  response: text
  context: retrieved_context

thresholds:
  coherence: ">=3"
  avg_latency_seconds: "<=2"
```

See [how-it-works.md](how-it-works.md) for the full schema, endpoint
fields, validation rules, and more examples.
