# Tutorial — conversational agent

Evaluate a multi-turn assistant or chatbot. The shape of a
*conversational* agent is identical to any other agent for AgentOps —
what makes it conversational is the **dataset**: the rows can include
prior turns the agent should consider.

## When to use this

You have an assistant deployed as either:

- A **Foundry prompt agent** (`name:version`)
- A **Foundry hosted endpoint** (`https://*.services.ai.azure.com/.../agents/<id>`)
- A **plain HTTP service** (Container Apps, AKS, your own server)

…and you want to measure response **coherence**, **fluency**,
**similarity to a reference answer**, and **latency** across a curated
script of questions.

## 1. Bootstrap

```powershell
pip install "agentops-toolkit[foundry] @ git+https://github.com/Azure/agentops.git@develop"
agentops init
$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://<resource>.services.ai.azure.com/api/projects/<project>"
```

## 2. Edit `agentops.yaml`

Pick the form that matches your agent:

```yaml
version: 1
agent: "customer-support:3"          # Foundry prompt agent (name:version)
dataset: .agentops/data/chat.jsonl
```

```yaml
version: 1
agent: "https://api.example.com/chat"   # any HTTP/JSON service
dataset: .agentops/data/chat.jsonl
```

For HTTP targets, AgentOps POSTs `{"message": "<input>"}` and reads
the response from the `text` field by default. If your service uses
different field names, override them:

```yaml
version: 1
agent: "https://api.example.com/chat"
dataset: .agentops/data/chat.jsonl

request_field: prompt
response_field: choices.0.message.content
```

## 3. Dataset shape (`chat.jsonl`)

```jsonl
{"id":"1","input":"Hi, can you help me reset my password?","expected":"Sure — could you share the email on your account?"}
{"id":"2","input":"What's the SLA on a refund request?","expected":"Refunds are processed within 5 business days."}
{"id":"3","input":"My order #1234 hasn't arrived. Can you track it?","expected":"I can look that up. One moment, please."}
```

Rows have `input` and `expected`. With this shape AgentOps
auto-selects the **conversational baseline** evaluators: Coherence,
Fluency, Similarity, F1Score, average latency.

> Want to test multi-turn behaviour explicitly? Have your service
> accept a `history` field, then add `extra_fields: [history]` to
> `agentops.yaml` and include a `history` array in each JSONL row.

## 4. Run

```powershell
agentops eval run
```

Open the report with `code .agentops/results/latest/report.md` and press `Ctrl+Shift+V` to render the Markdown — verdict, per-row
transcript, and aggregate scores.

## 5. Lock in a baseline

No extra step needed — `latest/results.json` is your previous run.
Diff your next run against it:

```powershell
# … change a prompt / model / config …
agentops eval run --baseline .agentops/results/latest/results.json
```

The next report adds *Comparison vs Baseline* with per-metric deltas.

## See also

- [tutorial-http-agent.md](tutorial-http-agent.md) — full HTTP-target walkthrough including auth headers
- [tutorial-agent-workflow.md](tutorial-agent-workflow.md) — same shape, plus tool calling
- [tutorial-baseline-comparison.md](tutorial-baseline-comparison.md) — regression detection
