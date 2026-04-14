# Foundry Evaluation SDK Built-in Evaluators (AgentOps)

This guide maps Microsoft Foundry built-in evaluators to the configuration model used by AgentOps Toolkit.

## 1) AgentOps config shape (quick reference)

In AgentOps, each evaluator is configured under `bundle.evaluators[]`:

```yaml
evaluators:
  - name: SimilarityEvaluator
    source: foundry
    enabled: true
    config:
      kind: builtin                # builtin | custom
      class_name: SimilarityEvaluator
      init:                        # constructor kwargs
        model_config:
          azure_endpoint: ${env:AZURE_OPENAI_ENDPOINT}
          azure_deployment: ${env:AZURE_OPENAI_DEPLOYMENT}
      input_mapping:               # evaluator call kwargs
        query: $prompt
        response: $prediction
        ground_truth: $expected
      score_keys:                  # ordered keys to read numeric score
        - similarity
        - score
```

## 2) Global requirements by evaluator family

- AI-assisted quality evaluators use a judge model (`model_config`) in Azure OpenAI/OpenAI schema.
- Risk/safety evaluators and `GroundednessProEvaluator` use `azure_ai_project` instead of GPT deployment in `model_config`.
- Agent evaluators require agent-style payloads (`query/response` as messages, and often tool metadata).
- NLP evaluators (`F1`, `BLEU`, `GLEU`, `ROUGE`, `METEOR`) are non-LLM evaluators and usually need `response` + `ground_truth`.

## 3) Built-in evaluators and required AgentOps parameters

| Evaluator | Category | Typical required inputs | Backend init requirements | AgentOps `config` minimum |
|---|---|---|---|---|
| `CoherenceEvaluator` | General purpose | `query`, `response` | `model_config` (AI-assisted) | `kind: builtin`, `class_name`, `input_mapping(query,response)`, `score_keys` |
| `FluencyEvaluator` | General purpose | `query`, `response` | `model_config` (AI-assisted) | same as above |
| `SimilarityEvaluator` | Textual similarity | `query`, `response`, `ground_truth` | `model_config` (AI-assisted) | `input_mapping(query,response,ground_truth)` |
| `F1ScoreEvaluator` | Textual similarity (NLP) | `response`, `ground_truth` | none beyond class init defaults | `input_mapping(response,ground_truth)` |
| `BleuScoreEvaluator` | Textual similarity (NLP) | `response`, `ground_truth` | none beyond class init defaults | `input_mapping(response,ground_truth)` |
| `GleuScoreEvaluator` | Textual similarity (NLP) | `response`, `ground_truth` | none beyond class init defaults | `input_mapping(response,ground_truth)` |
| `RougeScoreEvaluator` | Textual similarity (NLP) | `response`, `ground_truth` | optional `rouge_type` in `init` | `input_mapping(response,ground_truth)` |
| `MeteorScoreEvaluator` | Textual similarity (NLP) | `response`, `ground_truth` | optional `alpha/beta/gamma/threshold` in `init` | `input_mapping(response,ground_truth)` |
| `RetrievalEvaluator` | RAG | usually `query`, `response`, `context` | `model_config` (AI-assisted) | `input_mapping(query,response,context)` |
| `DocumentRetrievalEvaluator` | RAG | retrieval outputs + `ground_truth` | check SDK class contract | explicit `input_mapping` recommended |
| `GroundednessEvaluator` | RAG | `response`, `context` (optional `query`) | `model_config` (AI-assisted) | `input_mapping(response,context[,query])` |
| `GroundednessProEvaluator` *(preview)* | RAG | `query/response/context` or conversation | `azure_ai_project` (not GPT model_config) | `init.azure_ai_project`, explicit `input_mapping` |
| `RelevanceEvaluator` | RAG | `query`, `response` (optionally `context`) | `model_config` (AI-assisted) | `input_mapping(query,response[,context])` |
| `ResponseCompletenessEvaluator` | RAG | `response`, `ground_truth` (often query optional) | AI-assisted quality path | explicit `input_mapping`, `score_keys` |
| `HateUnfairnessEvaluator` | Safety | `query`, `response`/conversation | `azure_ai_project` | `init.azure_ai_project`, `input_mapping` |
| `SexualEvaluator` | Safety | `query`, `response`/conversation | `azure_ai_project` | same |
| `ViolenceEvaluator` | Safety | `query`, `response`/conversation | `azure_ai_project` | same |
| `SelfHarmEvaluator` | Safety | `query`, `response`/conversation | `azure_ai_project` | same |
| `ContentSafetyEvaluator` | Safety composite | query/response or conversation | `azure_ai_project` | `init.azure_ai_project`, `input_mapping` |
| `ProtectedMaterialEvaluator` | Safety | query/response or multimodal | `azure_ai_project` | `init.azure_ai_project`, `input_mapping` |
| `CodeVulnerabilityEvaluator` | Safety/risk | text/code response | `azure_ai_project` | `init.azure_ai_project`, `input_mapping(response[,query])` |
| `UngroundedAttributesEvaluator` | Safety/risk | text response | `azure_ai_project` | `init.azure_ai_project`, `input_mapping(response[,query])` |
| `IndirectAttackEvaluator` | Safety/risk | conversation-oriented input | `azure_ai_project` | `init.azure_ai_project`, `input_mapping(conversation/query,response)` |
| `IntentResolutionEvaluator` *(preview)* | Agent | `query`, `response` (string or message list) | agent evaluator path | `input_mapping(query,response[,tool_definitions])` |
| `TaskAdherenceEvaluator` *(preview)* | Agent | `query`, `response` (string or message list) | agent evaluator path | `input_mapping(query,response[,tool_calls])` |
| `ToolCallAccuracyEvaluator` *(preview)* | Agent | `query`; plus `response` or `tool_calls`; `tool_definitions` required | agent evaluator path | `input_mapping(query,response,tool_calls,tool_definitions)` |
| `TaskCompletionEvaluator` *(preview)* | Agent | agent run/conversation payload | preview; use latest SDK docs | explicit `input_mapping`, explicit `score_keys` |
| `TaskNavigationEfficiencyEvaluator` *(preview)* | Agent | tool/call sequence + expected path context | preview; evolving | explicit `input_mapping`, explicit `score_keys` |
| `ToolSelectionEvaluator` *(preview)* | Agent | query/response + selected tools + tool defs | preview; evolving | explicit `input_mapping`, explicit `score_keys` |
| `ToolInputAccuracyEvaluator` *(preview)* | Agent | tool args + tool defs + context | preview; evolving | explicit `input_mapping`, explicit `score_keys` |
| `ToolOutputUtilizationEvaluator` *(preview)* | Agent | tool outputs + final response | preview; evolving | explicit `input_mapping`, explicit `score_keys` |
| `ToolCallSuccessEvaluator` *(preview)* | Agent | tool execution results/status | preview; evolving | explicit `input_mapping`, explicit `score_keys` |
| `QAEvaluator` | Composite quality | `query`, `response`, `ground_truth`, `context` | `model_config` (AI-assisted composite) | `input_mapping(query,response,ground_truth,context)` |
| `AzureOpenAILabelGrader` | Azure OpenAI grader | template-driven (often conversation/query/response) | grader init requires template/model config | explicit `init` + explicit `input_mapping` |
| `AzureOpenAIStringCheckGrader` | Azure OpenAI grader | template-driven text fields | grader init requires template | explicit `init` + explicit `input_mapping` |
| `AzureOpenAITextSimilarityGrader` | Azure OpenAI grader | text + `ground_truth` equivalent | grader init requires template/model config | explicit `init` + explicit `input_mapping` |
| `AzureOpenAIGrader` | Azure OpenAI grader | template-defined | grader init requires rubric/template | explicit `init` + explicit `input_mapping` |

## 4) Practical rules for AgentOps bundles

- Always set `source: foundry` for Foundry SDK evaluators.
- For preview evaluators, always provide explicit:
  - `config.class_name`
  - `config.input_mapping`
  - `config.score_keys`
- Prefer explicit `input_mapping` even when defaults might work.
- Keep `thresholds[].evaluator` exactly equal to `evaluators[].name`.
- For agent evaluators, use structured fields in dataset rows (messages, tool calls, tool definitions) and map with `$row.<field>`.

## 5) Examples by evaluator type

The following examples show one practical bundle snippet for each evaluator family used in AgentOps:

- `5.1` AI-assisted quality evaluators (`model_config`)
- `5.2` Risk/safety evaluators (`azure_ai_project`)
- `5.3` Agent evaluators (message/tool payloads)
- `5.4` NLP evaluators (non-LLM)

## 5.1) Example for AI-assisted quality evaluator (`model_config`)

```yaml
evaluators:
  - name: RelevanceEvaluator
    source: foundry
    enabled: true
    config:
      kind: builtin
      class_name: RelevanceEvaluator
      init:
        model_config:
          azure_endpoint: ${env:AZURE_OPENAI_ENDPOINT}
          azure_deployment: ${env:AZURE_OPENAI_DEPLOYMENT}
      input_mapping:
        query: $prompt
        response: $prediction
      score_keys:
        - relevance
        - score

thresholds:
  - evaluator: RelevanceEvaluator
    criteria: ">="
    value: 3
```

## 5.2) Example for risk/safety evaluator (`azure_ai_project`)

```yaml
evaluators:
  - name: ViolenceEvaluator
    source: foundry
    enabled: true
    config:
      kind: builtin
      class_name: ViolenceEvaluator
      init:
        azure_ai_project: ${env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT}
      input_mapping:
        query: $prompt
        response: $prediction
      score_keys:
        - violence
        - score

thresholds:
  - evaluator: ViolenceEvaluator
    criteria: "<="
    value: 2
```

`GroundednessProEvaluator` follows the same pattern (`azure_ai_project` in `init`).

## 5.3) Example for agent evaluator (agent payload + tools)

```yaml
evaluators:
  - name: ToolCallAccuracyEvaluator
    source: foundry
    enabled: true
    config:
      kind: builtin
      class_name: ToolCallAccuracyEvaluator
      input_mapping:
        query: $row.query_messages
        response: $row.response_messages
        tool_calls: $row.tool_calls
        tool_definitions: $row.tool_definitions
      score_keys:
        - tool_call_accuracy
        - score

thresholds:
  - evaluator: ToolCallAccuracyEvaluator
    criteria: ">="
    value: 3
```

## 5.4) Example for NLP evaluator (non-LLM)

```yaml
evaluators:
  - name: F1ScoreEvaluator
    source: foundry
    enabled: true
    config:
      kind: builtin
      class_name: F1ScoreEvaluator
      input_mapping:
        response: $prediction
        ground_truth: $expected
      score_keys:
        - f1_score
        - score

thresholds:
  - evaluator: F1ScoreEvaluator
    criteria: ">="
    value: 0.7
```

## 6) Cloud Evaluation defaults

AgentOps provides sensible defaults so you don't need to configure extra environment variables:

| Setting | Default | Override |
|---|---|---|
| Judge model (AI-assisted evaluators) | A deployment you configure in your project | `backend.model` in `run.yaml` or `AZURE_AI_MODEL_DEPLOYMENT_NAME` env var |
| Authentication | `DefaultAzureCredential` (passwordless) | `az login` locally, Managed Identity in Azure |

## 7) Known caveats

- Some agent evaluators listed in the latest Foundry docs are preview and can change name/signature.
- Not all preview evaluators have stable Python API docs with full constructor/call signatures at any given time.
- When a signature changes, update only evaluator `config` in bundle (no code change needed in AgentOps core, due to generic runtime).

**Last updated:** 2026-03-02 (UTC)

Because Foundry Evaluation SDK and evaluator signatures evolve (especially preview features), review official docs before production rollout.
