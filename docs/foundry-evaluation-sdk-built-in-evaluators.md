# Foundry Evaluators

This page explains how AgentOps maps Microsoft Foundry Evaluation SDK
evaluators to the data in `agentops.yaml`, dataset rows, HTTP responses, and
trace imports.

Most users do not need to configure evaluator internals. AgentOps selects common
evaluators from the target type and dataset shape. Use this page when you need
to understand what each evaluator receives.

## Config shape

The normal config stays small:

```yaml
version: 1
agent: "https://support-dev.example.com/chat"
dataset: .agentops/data/rag-smoke.jsonl
response_source: agent

protocol: http-json
request_field: message
response_fields:
  response: answer
  context: context

thresholds:
  groundedness: ">=3"
  retrieval: ">=3"
  coherence: ">=3"
```

Use `evaluators:` only when you want to override the automatic choice:

```yaml
evaluators:
  - GroundednessEvaluator
  - RetrievalEvaluator
  - RelevanceEvaluator
```

## Evaluator families

| Family | What it checks | Common inputs |
|---|---|---|
| Quality judges | The answer is coherent, fluent, similar, complete, or relevant. | prompt, response, expected answer |
| RAG judges | The answer uses retrieved context and the retrieval is useful. | prompt, response, context |
| Safety judges | The answer avoids harmful or protected content. | prompt, response |
| Agent judges | Tool use and agent workflow behavior are correct. | prompt, response, tool calls, tool definitions |
| Local metrics | Scores that do not need a judge model. | response, expected answer, latency |

## Evaluator inputs

AgentOps uses a small set of logical inputs. The same logical input can come from
a static dataset, a live HTTP response, or imported telemetry.

| Logical input | Meaning | Common source |
|---|---|---|
| `query` | The user prompt. | `row.input` |
| `response` | The target's final answer. | extracted response text |
| `ground_truth` | The expected answer or acceptance criteria. | `row.expected` |
| `response field` | Any value extracted through `response_fields`. | `$response.<field>` |
| `context` | Retrieved chunks, citations, or grounding text. | `row.context`, `$response.context`, `$retrieved_context`, or `$retrieved_context_items` |
| `tool_calls` | Tools called by the agent. | endpoint response or dataset row |
| `tool_definitions` | Tool schemas available to the agent. | dataset row |
| `trace_id` | Trace lineage for review and troubleshooting. | `$telemetry.trace_id` |

## Mapping rules

The mapping rules are intentionally boring:

1. `input` in the dataset becomes the evaluator `query`.
2. The extracted target answer becomes `response`.
3. `expected` in the dataset becomes `ground_truth`.
4. `context` in the dataset becomes evaluator `context`.
5. For grey-box HTTP, `response_fields.response` supplies the final answer.
6. For grey-box HTTP, `response_fields.context` can supply `$response.context`.
7. `$retrieved_context` and `$retrieved_context_items` expose retrieval context
   in the evaluator placeholder format.
8. `$telemetry.trace_id` exposes imported telemetry lineage when it exists.
9. Tool fields are used only when the dataset or response includes tool data.

For RAG, prefer a live context from the response when the endpoint can return it.
That gives the judge the same evidence the agent used for the answer. Use static
`row.context` when you want a fixed, hand-authored reference context.

## Examples

Static dataset row:

```json
{"input":"What is the refund window?","expected":"Customers can request a refund within 30 days.","context":"Refunds are available for 30 days after purchase."}
```

Static dataset config:

```yaml
response_source: dataset
```

Use `response_source: dataset` when each row already has a `response`,
`prediction`, `output`, or `answer` value and AgentOps should evaluate that value
instead of calling the target.

Grey-box HTTP config:

```yaml
protocol: http-json
request_field: message
response_fields:
  response: output.answer
  context: output.retrieval.chunks
```

Telemetry import:

```powershell
agentops telemetry validate prod-rag
agentops telemetry preview prod-rag --rows 10
agentops telemetry import prod-rag --apply
```

## Quality judges

| Evaluator | Typical inputs | Notes |
|---|---|---|
| `CoherenceEvaluator` | `query`, `response` | Checks whether the answer is logically consistent. |
| `FluencyEvaluator` | `response` | Checks language quality. |
| `SimilarityEvaluator` | `query`, `response`, `ground_truth` | Compares the answer with the expected answer. |
| `ResponseCompletenessEvaluator` | `query`, `response`, `ground_truth` | Checks whether the answer covers what was expected. |
| `RelevanceEvaluator` | `query`, `response`, optional `context` | Useful for both chat and RAG quality. |

Quality judges need a judge model deployment. Set
`AZURE_OPENAI_DEPLOYMENT` or `AZURE_AI_MODEL_DEPLOYMENT_NAME` when local or
cloud evaluation needs one.

## Safety judges

| Evaluator | Typical inputs | Notes |
|---|---|---|
| `ViolenceEvaluator` | `query`, `response` | Scores violent content risk. |
| `SexualEvaluator` | `query`, `response` | Scores sexual content risk. |
| `SelfHarmEvaluator` | `query`, `response` | Scores self-harm content risk. |
| `HateUnfairnessEvaluator` | `query`, `response` | Scores hate and unfairness risk. |
| `ProtectedMaterialEvaluator` | `query`, `response` | Checks protected material risk when supported by the SDK. |
| `ContentSafetyEvaluator` | `query`, `response` | Composite safety path when supported by the SDK. |

Safety judges require a Foundry project connection. Use
`AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` or `project_endpoint:` in `agentops.yaml`.

## Agent judges

| Evaluator | Typical inputs | Notes |
|---|---|---|
| `ToolCallAccuracyEvaluator` | `query`, `tool_calls`, `tool_definitions` | Checks whether the expected tools were called. |
| `IntentResolutionEvaluator` | `query`, `response`, `tool_definitions` | Checks whether the agent resolved the user's intent. |
| `TaskAdherenceEvaluator` | `query`, `response`, `tool_definitions` | Checks whether the agent stayed on task. |
| `TaskCompletionEvaluator` | conversation payload | Preview in some SDK versions. |
| `ToolSelectionEvaluator` | tool selection plus tool definitions | Preview in some SDK versions. |
| `ToolInputAccuracyEvaluator` | tool arguments plus tool definitions | Preview in some SDK versions. |

Agent judges work best when the target returns tool telemetry or the dataset row
contains expected tool calls. If the endpoint cannot expose tool calls, start
with answer quality and RAG judges instead.

## Local metrics

| Evaluator | Typical inputs | Notes |
|---|---|---|
| `F1ScoreEvaluator` | `response`, `ground_truth` | Good for exact reference checks. |
| `BleuScoreEvaluator` | `response`, `ground_truth` | Optional text similarity metric. |
| `GleuScoreEvaluator` | `response`, `ground_truth` | Optional text similarity metric. |
| `RougeScoreEvaluator` | `response`, `ground_truth` | Optional summary similarity metric. |
| `MeteorScoreEvaluator` | `response`, `ground_truth` | Optional text similarity metric. |
| `avg_latency_seconds` | elapsed time | AgentOps computes this locally. |

Local metrics are useful when you want a cheap deterministic signal. They are not
a replacement for human review or RAG-specific judges.

## Cloud defaults

AgentOps keeps cloud evaluation setup minimal:

| Setting | Default | Override |
|---|---|---|
| Authentication | `DefaultAzureCredential` | `az login` locally, managed identity in Azure, or federated identity in CI. |
| Foundry project | `project_endpoint` or `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` | Set either value before running. |
| Judge model | Project deployment selected by environment | `AZURE_OPENAI_DEPLOYMENT` or `AZURE_AI_MODEL_DEPLOYMENT_NAME`. |
| Publishing | Implicit for `execution: cloud` | `publish: true` for local runs that should upload metrics. |

## Caveats

- Foundry Evaluation SDK preview evaluators can change names or call signatures.
- If the SDK changes an evaluator, keep the docs, catalog, and tests in sync.
- `response_fields.response` is the final answer path for HTTP JSON responses.
- `response_fields.context` is the retrieved context path for RAG evaluation.
- Production trace imports need review before they become blocking release gates.

**Last updated:** 2026-06-26 (UTC)
