# Evaluation Bundles

An **evaluation bundle** defines which evaluators run against your target and what quality thresholds must be met. Each bundle is a standalone YAML file stored in `.agentops/bundles/`.

## Predefined Bundles

AgentOps ships five predefined bundles covering the most common evaluation scenarios:

| Bundle | Category | Evaluators | Typical Use |
|---|---|---|---|
| `model_quality_baseline` | Model quality | SimilarityEvaluator, CoherenceEvaluator, FluencyEvaluator, F1ScoreEvaluator, avg\_latency\_seconds | Benchmark raw model output quality |
| `rag_quality_baseline` | RAG quality | GroundednessEvaluator, RelevanceEvaluator, RetrievalEvaluator, ResponseCompletenessEvaluator, CoherenceEvaluator, avg\_latency\_seconds | Evaluate grounding and retrieval quality |
| `conversational_agent_baseline` | Conversational | CoherenceEvaluator, FluencyEvaluator, RelevanceEvaluator, SimilarityEvaluator, avg\_latency\_seconds | Chatbots, assistants, and Q\&A agents |
| `agent_workflow_baseline` | Agent workflow | TaskCompletionEvaluator, ToolCallAccuracyEvaluator, IntentResolutionEvaluator, TaskAdherenceEvaluator, ToolSelectionEvaluator, ToolInputAccuracyEvaluator, avg\_latency\_seconds | Agents with tool calling |
| `safe_agent_baseline` | Safety | ViolenceEvaluator, SexualEvaluator, SelfHarmEvaluator, HateUnfairnessEvaluator, ProtectedMaterialEvaluator, avg\_latency\_seconds | Content safety and responsible AI |

## Bundle YAML Structure

```yaml
version: 1
name: model_quality_baseline
description: >
  Baseline evaluation bundle for model quality assessment.
evaluators:
  - name: SimilarityEvaluator
    source: foundry
    enabled: true
    config:
      kind: builtin
      class_name: SimilarityEvaluator
      input_mapping:
        query: "$prompt"
        response: "$prediction"
        ground_truth: "$expected"
      score_keys: ["similarity"]
  - name: avg_latency_seconds
    source: local
    enabled: true
thresholds:
  - evaluator: SimilarityEvaluator
    criteria: ">="
    value: 3
  - evaluator: avg_latency_seconds
    criteria: "<="
    value: 10.0
metadata:
  category: model-quality
  tags:
    - baseline
```

### Top-Level Fields

| Field | Required | Description |
|---|---|---|
| `version` | Yes | Always `1` |
| `name` | Yes | Unique bundle identifier |
| `description` | No | Human-readable description |
| `evaluators` | Yes | List of evaluator definitions |
| `thresholds` | Yes | Pass/fail criteria per evaluator |
| `metadata` | No | Arbitrary metadata (category, tags) |

### Evaluator Fields

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Evaluator class name or local metric name |
| `source` | Yes | `foundry` (AI-assisted) or `local` (computed locally) |
| `enabled` | Yes | Whether this evaluator runs |
| `config` | No | Explicit evaluator configuration (recommended for Foundry evaluators) |

### Evaluator Config Fields

| Field | Description |
|---|---|
| `kind` | `builtin` for Foundry SDK evaluators |
| `class_name` | Python class name in `azure.ai.evaluation` |
| `input_mapping` | Maps evaluator input parameters to dataset/response variables |
| `score_keys` | Metric names produced by this evaluator |

### Input Mapping Variables

| Variable | Source |
|---|---|
| `$prompt` | User input / query from the dataset row |
| `$prediction` | Model or agent response |
| `$expected` | Ground truth / expected answer from the dataset row |
| `$context` | Retrieved context documents from the dataset row |
| `$tool_calls` | Tool calls returned by the agent |
| `$tool_definitions` | Tool definitions from the dataset row |

### Threshold Criteria

| Criteria | Description | Requires `value` |
|---|---|---|
| `>=`, `>`, `<=`, `<`, `==` | Numeric comparison | Yes |
| `true`, `false` | Boolean pass/fail | No |

## model\_quality\_baseline

**Category:** Model quality
**When to use:** Evaluating raw model output quality for any model deployment — Foundry models, HTTP endpoints, or local adapters. No retrieval context or tool calling involved.

**Evaluators:**
- `SimilarityEvaluator` — AI-assisted semantic similarity between response and expected answer (score 1–5, threshold ≥ 3)
- `CoherenceEvaluator` — Logical structure and flow of the response (score 1–5, threshold ≥ 3)
- `FluencyEvaluator` — Language quality and readability (score 1–5, threshold ≥ 3)
- `F1ScoreEvaluator` — Token overlap between response and expected answer (0–1, threshold ≥ 0.4)
- `avg_latency_seconds` — Average response time (threshold ≤ 10s)

**Dataset fields:** `input`, `expected`

## rag\_quality\_baseline

**Category:** RAG quality
**When to use:** Evaluating retrieval-augmented generation pipelines — agents or models that retrieve context documents before generating a response.

**Evaluators:**
- `GroundednessEvaluator` — Whether the response is grounded in the retrieved context (score 1–5, threshold ≥ 3)
- `RelevanceEvaluator` — Whether the response is relevant to the query given the context (score 1–5, threshold ≥ 3)
- `RetrievalEvaluator` — Quality of the retrieved context for the query (score 1–5, threshold ≥ 3)
- `ResponseCompletenessEvaluator` — Whether the response fully addresses the query (score 1–5, threshold ≥ 3)
- `CoherenceEvaluator` — Logical structure and flow (score 1–5, threshold ≥ 3)
- `avg_latency_seconds` — Average response time (threshold ≤ 10s)

**Dataset fields:** `input`, `expected`, `context`

## conversational\_agent\_baseline

**Category:** Conversational
**When to use:** Evaluating conversational agents — chatbots, virtual assistants, Q&A bots — where the focus is on response quality without tool calling or retrieval context.

**Evaluators:**
- `CoherenceEvaluator` — Logical structure and flow (score 1–5, threshold ≥ 3)
- `FluencyEvaluator` — Language quality and readability (score 1–5, threshold ≥ 3)
- `RelevanceEvaluator` — Whether the response is relevant to the query (score 1–5, threshold ≥ 3)
- `SimilarityEvaluator` — Semantic similarity to expected answer (score 1–5, threshold ≥ 3)
- `avg_latency_seconds` — Average response time (threshold ≤ 10s)

**Dataset fields:** `input`, `expected`

## agent\_workflow\_baseline

**Category:** Agent workflow
**When to use:** Evaluating agents that use tool calling (function calling) to complete tasks. Covers task completion, tool accuracy, intent resolution, and adherence.

**Evaluators:**
- `TaskCompletionEvaluator` — Whether the agent completed the requested task (score 1–5, threshold ≥ 3)
- `ToolCallAccuracyEvaluator` — Correctness of tool call arguments and sequencing (score 1–5, threshold ≥ 3)
- `IntentResolutionEvaluator` — Whether the agent correctly identified the user's intent (score 1–5, threshold ≥ 3)
- `TaskAdherenceEvaluator` — Whether the agent stayed on task (score 1–5, threshold ≥ 3)
- `ToolSelectionEvaluator` — Whether the agent chose the right tools (score 1–5, threshold ≥ 3)
- `ToolInputAccuracyEvaluator` — Correctness of inputs passed to tools (score 1–5, threshold ≥ 3)
- `avg_latency_seconds` — Average response time (threshold ≤ 15s)

**Dataset fields:** `input`, `expected`, `tool_definitions`, `tool_calls`

## safe\_agent\_baseline

**Category:** Safety
**When to use:** Evaluating content safety and responsible AI compliance for any agent or model deployment. Detects violence, sexual content, self-harm, hate/unfairness, and protected material risks in model responses.

**Evaluators:**
- `ViolenceEvaluator` — Violence risk level (score 0–7, threshold ≤ 2)
- `SexualEvaluator` — Sexual content risk level (score 0–7, threshold ≤ 2)
- `SelfHarmEvaluator` — Self-harm risk level (score 0–7, threshold ≤ 2)
- `HateUnfairnessEvaluator` — Hate and unfairness risk level (score 0–7, threshold ≤ 2)
- `ProtectedMaterialEvaluator` — Protected material risk level (score 0–7, threshold ≤ 2)
- `avg_latency_seconds` — Average response time (threshold ≤ 10s)

**Dataset fields:** `input`, `expected`
**Requirements:** `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` — safety evaluators use `azure_ai_project` (auto-injected) instead of `model_config`

## Creating Custom Bundles

Copy any predefined bundle and modify it:

1. Copy a bundle: `cp .agentops/bundles/model_quality_baseline.yaml .agentops/bundles/my_custom.yaml`
2. Edit evaluators — add, remove, or disable individual evaluators
3. Adjust thresholds to match your quality bar
4. Reference it in your run config: `bundle: { name: my_custom }`

See [foundry-evaluation-sdk-built-in-evaluators.md](foundry-evaluation-sdk-built-in-evaluators.md) for the full list of available Foundry evaluators.
