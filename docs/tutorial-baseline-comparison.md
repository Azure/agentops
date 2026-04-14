# Tutorial: Baseline Comparison

This tutorial walks through comparing evaluation runs to catch regressions before they reach production. It covers the mechanics of the compare command, but also explores how comparisons behave differently depending on whether you are evaluating a model deployment directly or an agent — and when each approach makes sense.

## Why compare runs?

Every time you change something — a model deployment, an agent's instructions, a retrieval pipeline, or even the evaluation dataset itself — you risk degrading quality without realizing it. A single evaluation run tells you where you stand *now*. Comparing two runs tells you *what changed* and *whether it got worse*.

This matters most in two situations:
- **Before merging a PR**: did the change improve the agent, or break it?
- **After deploying a new model version**: did quality hold, or did it regress?

Without comparison, you're looking at absolute scores and hoping you remember what they were last time. With comparison, you get a structured diff that tells you exactly which metrics moved, which thresholds flipped, and which specific rows started failing.

## Prerequisites

- Python 3.11+
- `pip install agentops-toolkit`
- A Foundry project with at least one model deployment (for model-direct) or a deployed agent (for agent evaluation)
- `az login` or equivalent Azure credentials
- Two completed evaluation runs, or the willingness to run two evaluations now

## Part 1: Choosing your evaluation target

Before you compare, you need to decide what you're evaluating. AgentOps supports two targets, and they produce meaningfully different results.

### Model-direct (`target.type: model`)

Sends your dataset prompts straight to a model deployment and evaluates the raw completions. There is no agent layer — no system instructions, no tools, no retrieval. The model sees each prompt in isolation and responds.

This is useful when you want to:
- Benchmark a model deployment before building an agent on top of it
- Detect model-level regressions when Azure deploys a new model version
- Measure raw language capabilities (similarity, coherence, fluency) without agent complexity
- Establish a quality floor that your agent should at least match

In practice, model-direct evaluations tend to produce **higher similarity scores** because the model responds concisely and closely to the expected answer. There is no agent personality reshaping the response.

Run configuration:
```yaml
target:
  type: model
  hosting: foundry
  execution_mode: remote
  endpoint:
    kind: foundry_agent
    model: gpt-5.1
```

### Agent (`target.type: agent`)

Routes each prompt through a deployed Foundry agent. The agent applies its system instructions, may call tools, may consult a knowledge base, and produces a response shaped by its configuration.

This is useful when you want to:
- Evaluate the full end-to-end behavior your users actually experience
- Test whether agent instructions and tool configurations work correctly together
- Catch regressions caused by changes to agent settings, not just the underlying model
- Measure real latency including agent orchestration overhead

Agent evaluations typically produce **lower similarity scores** than model-direct, even on the same questions. This is expected — the agent adds context, rephrases answers in its own style, and may include extra information from tools. A SimilarityEvaluator score of 5.0 on model-direct might become 3.4 on an agent for the same prompt. That does not necessarily mean the agent is worse; it means the agent is doing its job differently.

Run configuration:
```yaml
target:
  type: agent
  hosting: foundry
  execution_mode: remote
  endpoint:
    kind: foundry_agent
    agent_id: my-agent:1
    model: gpt-5.1
```

### When to compare model-direct vs agent

Comparing a model-direct run against an agent run is valid and sometimes valuable. It answers the question: *how much does the agent layer change the output quality?*

Expect to see:
- **Similarity drops** — the agent rephrases, which lowers textual similarity even when answers are correct
- **Latency increases** — agent orchestration adds overhead (thread creation, polling, tool calls)
- **Threshold flips** — thresholds set for model-direct may be too strict for agent responses

If you see a large similarity drop (say, from 5.0 to 1.0), that is worth investigating — the agent may be hallucinating, ignoring the question, or hitting an error in its tool chain. But a moderate drop (5.0 to 3.5) is usually the agent adding its own framing, which is fine.

For ongoing regression detection, compare **like against like**: model-direct against model-direct, or agent against agent. Cross-target comparisons are more diagnostic than gating.

## Part 2: Running two evaluations

### Step 1: Run the baseline

Pick your target and run:

```bash
# Model-direct baseline
agentops eval run -c .agentops/run.yaml

# Or agent baseline
agentops eval run -c .agentops/run-agent.yaml
```

This creates a timestamped directory:
```
.agentops/results/2026-03-19_100000/
├── results.json
├── report.md
└── backend_metrics.json
```

The run is also copied to `.agentops/results/latest/`.

### Step 2: Make a change

Now change something you want to evaluate:
- Update the model deployment version
- Modify the agent's system instructions
- Add or remove a tool from the agent
- Update the evaluation dataset with new test cases
- Adjust a retrieval pipeline or knowledge base

### Step 3: Run again

```bash
agentops eval run -c .agentops/run.yaml
```

You now have two runs under `.agentops/results/`.

## Part 3: Comparing runs

The compare command takes two run identifiers separated by a comma. The first is the baseline, the second is the current run.

```bash
# By timestamped folder name
agentops eval compare --runs 2026-03-19_100000,2026-03-19_140000

# Using 'latest' for the current run
agentops eval compare --runs 2026-03-19_100000,latest

# Write output to a specific directory
agentops eval compare --runs 2026-03-19_100000,latest -o .agentops/results/my-comparison
```

Run identifiers can be:
- **Timestamped folder names** like `2026-03-19_100000` — resolved under `.agentops/results/`
- **`latest`** — points to the most recent run
- **Paths** — relative or absolute path to a `results.json` file or a directory containing one

The command produces two files in the current run's output directory (or the `-o` directory):
- `comparison.json` — structured data for automation
- `comparison.md` — readable report for humans and PR reviews

### Exit codes

| Code | Meaning |
|---|---|
| `0` | No regressions detected — safe to proceed |
| `2` | Regressions detected — investigate before merging |
| `1` | Error — bad run ID, missing file, or other problem |

These are the same exit codes used by `agentops eval run`, so CI pipelines handle them consistently.

## Part 4: Reading the comparison report

### How metric direction works

AgentOps figures out whether "up" or "down" is good for each metric by looking at the threshold criteria in your results:

- Metrics with `>=` or `>` thresholds are **higher-is-better** (e.g., SimilarityEvaluator). A decrease is flagged as a regression.
- Metrics with `<=` or `<` thresholds are **lower-is-better** (e.g., avg_latency_seconds). An increase is flagged as a regression.

This means if your latency drops from 6s to 4s, the comparison correctly reports it as an **improvement**, not a regression.

### The summary section

The summary gives you the quick picture:

```
Metrics improved: 1
Metrics regressed: 1
Thresholds flipped pass→fail: 1
Items newly failing: 3
```

If `has_regressions` is true (and exit code is 2), at least one of these is nonzero: metrics regressed, thresholds flipped to fail, or items started failing.

### Metric deltas table

Shows every metric that exists in both runs, with the delta and direction:

```
| SimilarityEvaluator | 5.00 | 1.80 | -3.20 | -64% | regressed |
| avg_latency_seconds | 5.69 | 4.59 | -1.10 | -19% | improved  |
```

### Threshold changes table

Only shows thresholds that **flipped** between runs. A stable threshold (pass→pass or fail→fail) is omitted for clarity.

### Item changes table

Only shows rows that changed pass/fail status. If row 3 was passing in both runs, it is not listed.

## Part 5: Using comparison in CI

A typical GitHub Actions pattern:

```yaml
- name: Run evaluation
  run: agentops eval run -o .agentops/results/current

- name: Compare with baseline
  run: agentops eval compare --runs baseline,current
  # Exit code 2 fails the job if regressions are detected
```

### Choosing a baseline strategy

There is no single right way to manage baselines. Pick the one that fits your workflow:

**Committed baseline** — check a `results.json` into your repo under a stable name (e.g., `.agentops/results/baseline/`). Every PR compares against it. Update the baseline when you intentionally accept a quality change. This is simple and predictable, but requires manual baseline updates.

**Artifact-based baseline** — download the baseline `results.json` from a previous CI run's artifacts. Each merge to `main` uploads the current results as the new baseline. This automates baseline drift but depends on your CI artifact retention.

**Rolling latest** — always compare against the previous run. This catches run-over-run regressions but can miss gradual degradation over many runs.

For most teams, the committed baseline approach works well. It acts as a quality contract: merge only if you match or exceed the baseline.

## Part 6: Investigating regressions

When the comparison says regressions were detected, work through these steps:

1. **Read `comparison.md`** — start with the summary. How many metrics regressed? How many thresholds flipped? How many items are newly failing?

2. **Check concentration** — if 1 out of 50 items regressed, that might be a dataset edge case. If 40 out of 50 regressed, something fundamental changed.

3. **Identify the variable** — what changed between the two runs? Only one thing should change at a time. If you changed the model *and* the dataset *and* the agent instructions simultaneously, you cannot attribute the regression to any single cause.

4. **Look at the actual responses** — read `backend.stdout.log` in the run output directory. It shows the expected and predicted text for each row. Often the root cause is obvious when you see the actual model/agent output.

5. **Rerun with the previous configuration** — if you suspect the model deployment changed, rerun the baseline dataset against the current deployment. If scores still drop, the model is the cause. If scores hold, something else changed.

### Typical regression patterns

**Across-the-board similarity drop** — usually means the model deployment was updated or the agent's system instructions changed in a way that alters response style. Check whether the answers are still *correct* even if they are less *similar* to the expected text.

**A few rows regressed, most are fine** — likely dataset-specific. Check whether the failing rows have unusual inputs, edge cases, or ambiguous expected answers.

**Latency increased but quality held** — infrastructure issue, throttling, or the agent is now calling more tools. Check whether new tool calls were added to the agent configuration.

**Threshold was borderline and flipped** — the metric is near the threshold value and normal variance pushed it over. Consider whether the threshold is set too tightly, or whether the metric genuinely degraded.

## Next steps

- [Model-Direct Evaluation Tutorial](tutorial-model-direct.md) — evaluate a model deployment without agents
- [RAG Evaluation Tutorial](tutorial-rag.md) — evaluate retrieval-augmented responses
- [Foundry Agent Evaluation Tutorial](tutorial-basic-foundry-agent.md) — evaluate an agent end-to-end
- [CI/CD Integration Guide](ci-github-actions.md) — set up automated evaluation in pipelines
- [CI/CD Integration Guide](ci-github-actions.md)
