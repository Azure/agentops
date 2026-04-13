# Issue #51 — Two-Track Analysis

**Date:** 2026-04-03

---

## Track 1: How to Fully Support Foundry Default Evaluators

### Current Architecture

The cloud evaluation path in `foundry_backend.py` builds evaluators like this:

```python
builtin_name = _to_builtin_evaluator_name(evaluator.name)  # "SimilarityEvaluator" → "similarity"
criterion = {
    "type": "azure_ai_evaluator",
    "name": evaluator.name,
    "evaluator_name": f"builtin.{builtin_name}",
    "data_mapping": _cloud_evaluator_data_mapping(builtin_name, input_field, expected_field, context_field),
}
if _cloud_evaluator_needs_model(builtin_name):
    criterion["initialization_parameters"] = {"deployment_name": settings.model}
```

The `_cloud_evaluator_data_mapping` function routes evaluators to the correct
`data_mapping` based on frozenset membership:

```
default path            → {"query": "{{item.X}}", "response": "{{sample.output_text}}"}
_NLP_ONLY_EVALUATORS    → no "query", just "response"
_GROUND_TRUTH           → adds "ground_truth": "{{item.Y}}"
_CONTEXT                → adds "context": "{{item.Z}}"
_TOOL_CALLS             → adds "tool_calls": "{{sample.tool_calls}}", "tool_definitions": "{{item.tool_definitions}}"
```

### Problem: Only 8 of ~35 evaluators are routed correctly

Any evaluator NOT in any frozenset falls to the default path (`query` + `response`).
This accidentally works for some evaluators (like `coherence`) but silently sends
wrong data_mappings for many others.

### What Each Evaluator Actually Needs

Based on Foundry cloud evaluation docs (2026-04-02), here are the correct
`data_mapping` patterns for every built-in evaluator:

#### Pattern 1: query + response (simplest — default path)

Works with current default path. No code change needed.

| Evaluator | builtin name | Needs model | Status |
|---|---|---|---|
| CoherenceEvaluator | `coherence` | Yes | ✅ Works today (falls to default) |
| FluencyEvaluator | `fluency` | Yes | ✅ Works today |
| RelevanceEvaluator | `relevance` | Yes | ✅ Works today |
| IntentResolutionEvaluator | `intent_resolution` | Yes | ✅ Works today |
| TaskCompletionEvaluator | `task_completion` | Yes | ✅ Works today |
| ViolenceEvaluator | `violence` | Yes | ✅ Works today |
| SexualEvaluator | `sexual` | Yes | ✅ Works today |
| SelfHarmEvaluator | `self_harm` | Yes | ✅ Works today |
| HateUnfairnessEvaluator | `hate_unfairness` | Yes | ✅ Works today |
| ContentSafetyEvaluator | `content_safety` | Yes | ✅ Works today |
| ProtectedMaterialEvaluator | `protected_material` | Yes | ✅ Works today |
| CodeVulnerabilityEvaluator | `code_vulnerability` | Yes | ✅ Works today |
| UngroundedAttributesEvaluator | `ungrounded_attributes` | Yes | ✅ Works today |
| IndirectAttackEvaluator | `indirect_attack` | Yes | ✅ Works today |

**Verdict:** These 14 evaluators already work with the current code — users
just don't know they can use them because they're not documented/tested.

#### Pattern 2: query + response (output_items) — agent structured output

`task_adherence` needs `{{sample.output_items}}` instead of
`{{sample.output_text}}` for the response field, because it needs to see the
full structured agent output (tool calls, intermediate steps).

| Evaluator | builtin name | response field | Status |
|---|---|---|---|
| TaskAdherenceEvaluator | `task_adherence` | `{{sample.output_items}}` | ❌ **Broken** — sends `output_text` |

**Fix required:** Add `task_adherence` to a new set
`_EVALUATORS_NEEDING_OUTPUT_ITEMS` and map `response` to
`{{sample.output_items}}` instead of `{{sample.output_text}}`.

#### Pattern 3: response + ground_truth (existing)

Already implemented via `_EVALUATORS_NEEDING_GROUND_TRUTH`.

| Evaluator | builtin name | Status |
|---|---|---|
| SimilarityEvaluator | `similarity` | ✅ Supported |
| ResponseCompletenessEvaluator | `response_completeness` | ❌ Missing from frozenset |

**Fix required:** Add `response_completeness` to `_EVALUATORS_NEEDING_GROUND_TRUTH`.

#### Pattern 4: NLP only — no query, no model (existing)

Already implemented via `_NLP_ONLY_EVALUATORS`.

| Evaluator | builtin name | Status |
|---|---|---|
| F1ScoreEvaluator | `f1_score` | ✅ Supported |
| BleuScoreEvaluator | `bleu` | ✅ Supported |
| GleuScoreEvaluator | `gleu` | ✅ Supported |
| RougeScoreEvaluator | `rouge` | ✅ Supported |
| MeteorScoreEvaluator | `meteor` | ✅ Supported |

#### Pattern 5: response + context (existing)

Already implemented via `_EVALUATORS_NEEDING_CONTEXT`.

| Evaluator | builtin name | Status |
|---|---|---|
| GroundednessEvaluator | `groundedness` | ✅ Supported |
| GroundednessProEvaluator | `groundedness_pro` | ❌ Missing from frozenset |
| RetrievalEvaluator | `retrieval` | ❌ Missing from frozenset |

**Fix required:** Add `groundedness_pro` and `retrieval` to
`_EVALUATORS_NEEDING_CONTEXT`.

#### Pattern 6: tool evaluators (existing)

Already implemented via `_EVALUATORS_NEEDING_TOOL_CALLS`.

| Evaluator | builtin name | data_mapping | Status |
|---|---|---|---|
| ToolCallAccuracyEvaluator | `tool_call_accuracy` | query, response, tool_calls, tool_definitions | ✅ Supported |
| ToolSelectionEvaluator | `tool_selection` | query, response, tool_calls, tool_definitions | ❌ Missing from frozenset |
| ToolInputAccuracyEvaluator | `tool_input_accuracy` | query, response, tool_definitions | ❌ Missing (needs tool_definitions but not tool_calls) |
| ToolOutputUtilizationEvaluator | `tool_output_utilization` | query, response, tool_definitions | ❌ Missing |
| ToolCallSuccessEvaluator | `tool_call_success` | response, tool_definitions | ❌ Missing |

**Fix required:**
- Add `tool_selection` to `_EVALUATORS_NEEDING_TOOL_CALLS`
- For `tool_input_accuracy` and `tool_output_utilization`: need
  `tool_definitions` but NOT `tool_calls` — need a new set
  `_EVALUATORS_NEEDING_TOOL_DEFINITIONS_ONLY`
- For `tool_call_success`: needs `response` + `tool_definitions` only

#### Pattern 7: Special — Graders

Azure OpenAI graders use `type: "azure_openai_grader"` instead of
`type: "azure_ai_evaluator"`. These are a different testing criteria type.

| Evaluator | Status |
|---|---|
| AzureOpenAILabelGrader | ❌ Not supported — different type |
| AzureOpenAIStringCheckGrader | ❌ Not supported — different type |
| AzureOpenAITextSimilarityGrader | ❌ Not supported — different type |
| AzureOpenAIGrader | ❌ Not supported — different type |

**Out of scope for now.** Graders require a fundamentally different config
model (rubric templates, scoring criteria). Can be tracked separately.

#### Pattern 8: Special — Red team

Red team evaluators use a different data source type
(`azure_ai_red_team`) with attack strategies and taxonomy generation.

| Evaluator | Status |
|---|---|
| ProhibitedActionsEvaluator | ❌ Different flow |
| SensitiveDataLeakageEvaluator | ❌ Different flow |

**Out of scope for now.** Red team requires a separate execution flow.

### Summary: What Needs to Change in `foundry_backend.py`

| Change | Affected evaluators | Effort |
|---|---|---|
| Add to `_EVALUATORS_NEEDING_GROUND_TRUTH` | `response_completeness` | 1 line |
| Add to `_EVALUATORS_NEEDING_CONTEXT` | `groundedness_pro`, `retrieval` | 1 line |
| Add to `_EVALUATORS_NEEDING_TOOL_CALLS` | `tool_selection` | 1 line |
| New set: `_EVALUATORS_NEEDING_TOOL_DEFS_ONLY` | `tool_input_accuracy`, `tool_output_utilization`, `tool_call_success` | ~10 lines |
| New set: `_EVALUATORS_NEEDING_OUTPUT_ITEMS` | `task_adherence` | ~5 lines |
| Document that default path works | `coherence`, `fluency`, `relevance`, `intent_resolution`, `task_completion`, all safety evaluators | 0 lines (docs only) |

### Data Model Gap: item_schema

The current code builds `item_schema` with only two string fields:

```python
item_schema = {
    "type": "object",
    "properties": {
        input_field: {"type": "string"},
        expected_field: {"type": "string"},
    },
    "required": [input_field, expected_field],
}
```

For tool evaluators to work, the schema must also declare `tool_definitions`
(and `tool_calls` if present in the dataset). The schema needs to be
dynamically built based on which evaluators are enabled.

**Fix required:** When any evaluator in `_EVALUATORS_NEEDING_TOOL_CALLS` or
`_EVALUATORS_NEEDING_TOOL_DEFS_ONLY` is enabled, add `tool_definitions` to
`item_schema.properties`. Similarly, add `context_field` when context
evaluators are used.

### Data Model Gap: DatasetFormat

`DatasetFormat` currently has `input_field`, `expected_field`, and
`context_field`. It does NOT have:
- `tool_definitions_field` — needed for tool evaluators
- `tool_calls_field` — needed for `tool_call_accuracy`, `tool_selection`

**Fix required:** Add optional fields to `DatasetFormat` model:

```python
class DatasetFormat(BaseModel):
    type: str
    input_field: str
    expected_field: str
    context_field: Optional[str] = None
    tool_definitions_field: Optional[str] = None   # NEW
    tool_calls_field: Optional[str] = None          # NEW
```

### Revised Evaluator Support Count

After the fixes above:

| Category | Before | After |
|---|---|---|
| Works correctly today | 8 (NLP + similarity + groundedness + tool_call_accuracy) | 8 |
| Accidentally works (default path) | 0 recognized | 14 newly recognized |
| Fixed by adding to frozensets | 0 | 5 (response_completeness, groundedness_pro, retrieval, tool_selection, task_adherence) |
| Fixed by new sets | 0 | 3 (tool_input_accuracy, tool_output_utilization, tool_call_success) |
| **Total supported** | **8** | **30** |
| Remaining unsupported | | 5 (4 graders + documentation_retrieval) |

---

## Track 2: Evaluation Patterns from Real Scenarios (Harpreet)

### Pattern A: Cloud Agent Evaluation with Inline Data

**Source:** `agenteval.py`

**Flow:**
1. Connect to Foundry project via `AIProjectClient`
2. Get OpenAI client via `project_client.get_openai_client()`
3. Define `data_source_config` with `type: custom` and item_schema
4. Define `testing_criteria` — array of `azure_ai_evaluator` entries
5. Call `client.evals.create()` with testing_criteria
6. Call `client.evals.runs.create()` with inline JSONL data
7. Poll `client.evals.runs.retrieve()` until completed/failed
8. Retrieve output items via `client.evals.runs.output_items.list()`

**Data format used:**

```python
data_source_config = {
    "type": "custom",
    "item_schema": {
        "type": "object",
        "properties": {
            "query": {"anyOf": [{"type": "string"}, {"type": "array"}]},
            "tool_definitions": {"anyOf": [{"type": "object"}, {"type": "array"}]},
            "tool_calls": {"anyOf": [{"type": "object"}, {"type": "array"}]},
            "response": {"anyOf": [{"type": "string"}, {"type": "array"}]},
        },
        "required": ["query", "response", "tool_definitions"],
    },
    "include_sample_schema": True,
}
```

**Key observation:** The field types use `anyOf` with string OR array. This
allows both simple string queries AND structured conversation-format arrays.
AgentOps hardcodes `{"type": "string"}` — this works for simple eval but
blocks conversation-format data.

**Evaluators used (9 total):**

| # | Name | Category | data_mapping |
|---|---|---|---|
| 1 | task_completion | System | query, response, tool_definitions |
| 2 | task_adherence | System | query, response, tool_definitions |
| 3 | intent_resolution | System | query, response, tool_definitions |
| 4 | groundedness | RAG | query, tool_definitions, response |
| 5 | relevance | RAG | query, response |
| 6 | tool_call_accuracy | Process | query, tool_definitions, tool_calls, response |
| 7 | tool_selection | Process | query, response, tool_calls, tool_definitions |
| 8 | tool_input_accuracy | Process | query, response, tool_definitions |
| 9 | tool_output_utilization | Process | query, response, tool_definitions |

**AgentOps compatibility after Track 1 fixes:** 9/9 evaluators would be
supported. The remaining gap is the `item_schema` format — Harpreet uses
`anyOf` types while AgentOps hardcodes `string`.

### Pattern B: Red Team Safety Evaluation

**Source:** `redteam.py`

**Flow:**
1. Connect to Foundry project client
2. Create an agent version via `project_client.agents.create_version()`
3. Define safety testing criteria (7 evaluators)
4. Create evaluation taxonomy via `project_client.evaluation_taxonomies.create()`
5. Create eval run with `data_source.type: azure_ai_red_team`
6. Uses generated adversarial inputs with attack strategies `["Flip", "Base64"]`
7. Poll until completion, save results to JSON

**Data source:** `azure_ai_red_team` — fundamentally different from the
`custom`/`completions`/`azure_ai_target_completions` data sources that
AgentOps supports.

**Safety evaluators used (7 total):**

| # | Name | builtin name |
|---|---|---|
| 1 | Prohibited Actions | `builtin.prohibited_actions` |
| 2 | Task Adherence | `builtin.task_adherence` |
| 3 | Sensitive Data Leakage | `builtin.sensitive_data_leakage` |
| 4 | Self Harm | `builtin.self_harm` |
| 5 | Violence | `builtin.violence` |
| 6 | Sexual | `builtin.sexual` |
| 7 | Hate Unfairness | `builtin.hate_unfairness` |

**Key observations:**
- Safety evaluators like `violence`, `self_harm`, `sexual`, `hate_unfairness`
  CAN be used in normal cloud evaluation (Pattern A) with `query + response`
  data mapping — they don't REQUIRE the red team data source.
- `prohibited_actions` and `sensitive_data_leakage` are red-team-specific.
- `task_adherence` is reused across both patterns.

**AgentOps compatibility:** The safety evaluators (items 4-7) would work in
normal eval after Track 1 (they use the default `query + response` pattern).
The red-team flow itself (attack strategies, taxonomy generation) is a
separate feature.

### Pattern C: Agent Smoke Test

**Source:** `exagent.py`

**Flow:**
1. Connect to Foundry project client
2. Get existing agent by name via `project_client.agents.get()`
3. Get OpenAI client via `project_client.get_openai_client()`
4. Send a query via `openai_client.responses.create()` with agent reference
5. Handle MCP approval requests (auto-approve)
6. Poll for response completion
7. Display response text and citations

**AgentOps compatibility:** Not relevant to evaluation. This is a
pre-evaluation health check. Users can add this as a custom pipeline step
before `agentops eval run`. No tool change needed.

### Pattern D: Data Format — Conversation vs. String

**The critical data model difference:**

Harpreet's `agenteval.py` provides data in **conversation format**:

```python
query = [
    {"role": "system", "content": "You are a weather report agent."},
    {"role": "user", "content": [{"type": "text", "text": "Can you send me..."}]},
]

response = [
    {"role": "assistant", "content": [{"type": "tool_call", "name": "fetch_weather", ...}]},
    {"role": "tool", "content": [{"type": "tool_result", ...}]},
    {"role": "assistant", "content": [{"type": "text", "text": "I have successfully..."}]},
]

tool_definitions = [
    {"name": "fetch_weather", "description": "...", "parameters": {...}},
    {"name": "send_email", "description": "...", "parameters": {...}},
]
```

AgentOps datasets use **simple string format**:

```jsonl
{"input": "What is the weather?", "expected": "Sunny, 25°C"}
```

**When does this matter?**

- **For model-direct evaluation:** Simple strings work fine. The model receives
  the query and generates a response — evaluators compare output_text.
- **For agent evaluation with tool calls:** The conversation format is needed
  when evaluating tool-using agents on pre-computed responses. But when using
  `azure_ai_target_completions` with a live agent target, the agent generates
  structured responses at runtime — so simple string queries work.
- **For dataset (offline) evaluation:** If users want to evaluate
  pre-computed agent conversations (not calling the agent at runtime),
  they need conversation-format JSONL rows.

**Impact on AgentOps:**

The current `item_schema` hardcodes `{"type": "string"}`. This blocks:
1. Dataset evaluation with pre-computed structured responses
2. Tool evaluators that need `tool_definitions` in the dataset rows

It does NOT block:
1. Live agent evaluation (agent generates structured output at runtime)
2. Live model evaluation (model generates text at runtime)

**Fix:** Make `item_schema.properties` type flexible — use `anyOf` when the
evaluator requires structured data, or infer from JSONL row content.

---

## Synthesis: Combined Gap Map

| # | Gap | Track | Severity | Fix |
|---|---|---|---|---|
| 1 | 14 evaluators work but aren't documented | Track 1 | Low | Document and add tests |
| 2 | `response_completeness` missing from ground_truth set | Track 1 | Low | 1 line |
| 3 | `groundedness_pro`, `retrieval` missing from context set | Track 1 | Low | 1 line |
| 4 | `tool_selection` missing from tool_calls set | Track 1 | Low | 1 line |
| 5 | `tool_input_accuracy`, `tool_output_utilization`, `tool_call_success` need new set | Track 1 | Medium | ~10 lines |
| 6 | `task_adherence` needs `{{sample.output_items}}` response mapping | Track 1 | Medium | ~5 lines |
| 7 | `item_schema` hardcodes `{"type": "string"}` | Track 1+2 | High | Dynamic schema building |
| 8 | `DatasetFormat` lacks `tool_definitions_field` | Track 1+2 | High | Model change + wire through |
| 9 | `item_schema` doesn't include context_field | Track 1 | Medium | Dynamic schema building |
| 10 | Red team flow not supported | Track 2 | Future | Separate feature |
| 11 | Graders not supported | Track 1 | Future | Different testing_criteria type |

### Recommended Implementation Order

**Phase 1 — Quick wins (unblock 14 more evaluators):**
- Add evaluators to existing frozensets (#2, #3, #4)
- Create new frozensets (#5, #6)
- Update `_cloud_evaluator_data_mapping` for new patterns
- Add unit tests
- Update evaluator reference doc

**Phase 2 — Schema flexibility (unblock tool evaluators with dataset data):**
- Add `tool_definitions_field` and `tool_calls_field` to `DatasetFormat`
- Build `item_schema` dynamically based on enabled evaluators
- Add `context_field` to `item_schema` when context evaluators are used
- Use `anyOf` types when field content may be structured

**Phase 3 — Documentation (confirm patterns work end-to-end):**
- Document which evaluators work for each scenario
- Add bundle examples for agent evaluation with tool evaluators
- Document conversation-format dataset rows

**Phase 4 — Future:**
- Red team data source support
- Azure OpenAI grader support
