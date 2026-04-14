# Tutorial: Evaluating an Agent Workflow with Tools (Agent Framework)

This tutorial shows how to evaluate an **agent with tool calling** built with Microsoft Agent Framework using AgentOps.

Workflow agents orchestrate multi-step tasks: they interpret user intent, select the right tools, call them with correct arguments, and synthesize a final response. The evaluation measures **task completion, tool selection accuracy, and intent resolution**.

## When to Use This Scenario

Use the **agent workflow** evaluation when:

- Your agent calls external tools or functions (APIs, databases, search, calculations)
- You want to verify the agent selects the correct tool for each task
- You want to check that tool call arguments are accurate
- Your agent is built with Microsoft Agent Framework and runs as local Python code
- You need CI-friendly quality gates for tool-calling agents

This tutorial uses the **callable adapter** to invoke the agent directly as a Python function.

## Prerequisites

- Python 3.11+
- AgentOps installed: `pip install agentops-toolkit`
- Microsoft Agent Framework SDK installed (for your agent code)
- An Azure OpenAI deployment for AI-assisted evaluators
- `az login` completed

## Part 1: Initialize the Workspace

```bash
cd your-project-root
agentops init
```

Confirm the agent workflow bundle and dataset exist:

```
.agentops/
├── bundles/
│   └── agent_workflow_baseline.yaml
├── datasets/
│   └── smoke-agent-tools.yaml
├── data/
│   └── smoke-agent-tools.jsonl
└── callable_adapter.py
```

## Part 2: Understand the Dataset Format

Agent workflow evaluation requires richer dataset rows. Review `.agentops/data/smoke-agent-tools.jsonl`:

```json
{
  "id": "1",
  "input": "What is the weather in Seattle today?",
  "expected": "I'll check the weather for Seattle. The current temperature is 55°F with partly cloudy skies.",
  "tool_definitions": [
    {
      "name": "get_weather",
      "description": "Get current weather for a city",
      "parameters": {
        "type": "object",
        "properties": { "city": { "type": "string" } },
        "required": ["city"]
      }
    }
  ],
  "tool_calls": [
    { "name": "get_weather", "arguments": { "city": "Seattle" } }
  ]
}
```

Each row contains:
- `input` — The user request
- `expected` — The expected final response
- `tool_definitions` — Available tools the agent can choose from
- `tool_calls` — The expected tool calls (name + arguments)

The evaluators compare what tools the agent **should have called** vs. what it **actually called**.

## Part 3: Point to Your Agent Function

The callable adapter lets you point AgentOps directly to a Python function in your project — no wrapper code needed. Your function just needs to follow this contract:

```
(input_text: str, context: dict) -> dict
```

Where the returned dict has at least `{"response": "..."}`, and optionally `{"tool_calls": [...]}`.

### Option A: Point directly to your existing function

If your project already has a function with the right signature (or close to it), just reference it in `run.yaml`:

```yaml
local:
  callable: my_agent.workflow:run_evaluation
```

For example, if your Agent Framework code lives in `my_agent/workflow.py`:

```python
# my_agent/workflow.py

def run_evaluation(input_text: str, context: dict) -> dict:
    """Entry point called by AgentOps for each dataset row."""
    result = my_workflow.invoke(
        user_message=input_text,
        available_tools=context.get("tool_definitions", []),
    )
    return {
        "response": result.final_answer,
        "tool_calls": [
            {"name": tc.name, "arguments": tc.arguments}
            for tc in result.tool_calls
        ],
    }
```

### Option B: Use the starter template

`agentops init` already creates `.agentops/callable_adapter.py` with the correct signature and placeholder code. Open it and replace the body with your agent invocation — typically 2-3 lines:

```python
# .agentops/callable_adapter.py  (created by agentops init)

def run_evaluation(input_text: str, context: dict) -> dict:
    from my_agent.workflow import run_workflow

    result = run_workflow(
        user_message=input_text,
        available_tools=context.get("tool_definitions", []),
    )
    return {
        "response": result.final_answer,
        "tool_calls": [
            {"name": tc.name, "arguments": tc.arguments}
            for tc in result.tool_calls
        ],
    }
```

### Return contract

For the `agent_workflow_baseline` evaluators to work, the return dict should include:
- `"response"` — The agent's final text response (required)
- `"tool_calls"` — A list of tool calls the agent made (optional but recommended for tool accuracy evaluators)

## Part 4: Configure the Run

Edit `.agentops/run.yaml` to point to your function and select the workflow bundle:

```yaml
version: 1

target:
  type: agent
  hosting: local
  execution_mode: local
  local:
    # Point to your function: module.path:function_name
    callable: my_agent.workflow:run_evaluation

bundle:
  name: agent_workflow_baseline

dataset:
  name: smoke-agent-tools

execution:
  timeout_seconds: 300

output:
  write_report: true
```

Key fields:
- `local.callable` — The `module:function` path to your agent function. Use your project's module path (e.g. `my_agent.workflow:run_evaluation`) or point to the starter template (`callable_adapter:run_evaluation`).
- `target.type: agent` — Identifies this as an agent (not a model)
- `bundle.name: agent_workflow_baseline` — Uses tool-calling evaluators
- `dataset.name: smoke-agent-tools` — Dataset with `tool_definitions` and `tool_calls` fields

## Part 5: Set Up AI-Assisted Evaluator Credentials

The workflow evaluators (TaskCompletionEvaluator, IntentResolutionEvaluator, etc.) are **AI-assisted**.

```bash
export AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="https://your-project.services.ai.azure.com"
export AZURE_OPENAI_ENDPOINT="https://your-openai.openai.azure.com/"
export AZURE_AI_MODEL_DEPLOYMENT_NAME="gpt-4o"
```

Or on Windows (PowerShell):

```powershell
$env:AZURE_AI_FOUNDRY_PROJECT_ENDPOINT = "https://your-project.services.ai.azure.com"
$env:AZURE_OPENAI_ENDPOINT = "https://your-openai.openai.azure.com/"
$env:AZURE_AI_MODEL_DEPLOYMENT_NAME = "gpt-4o"
```

## Part 6: Run the Evaluation

```bash
agentops eval run --config .agentops/run.yaml
```

### Output

```
AgentOps evaluation run
  Config: .agentops/run.yaml
  Bundle: agent_workflow_baseline
  Dataset: smoke-agent-tools (5 rows)
  Backend: local_adapter (callable)

Processing row 1/5
Processing row 2/5
...

Results: .agentops/results/latest/results.json
Report:  .agentops/results/latest/report.md

Summary:
  Overall: PASSED
  Thresholds: 6/6 passed
  TaskCompletionEvaluator avg: 4.0
  ToolCallAccuracyEvaluator avg: 4.5
  IntentResolutionEvaluator avg: 4.2
  TaskAdherenceEvaluator avg: 3.8
  ToolSelectionEvaluator avg: 4.1
  ToolInputAccuracyEvaluator avg: 4.3
```

### Exit Codes

- `0` — All thresholds passed
- `2` — One or more thresholds failed
- `1` — Runtime or configuration error

## Thresholds

The `agent_workflow_baseline` bundle enforces:

| Evaluator | Criteria | Threshold |
|---|---|---|
| TaskCompletionEvaluator | ≥ | 3.0 |
| ToolCallAccuracyEvaluator | ≥ | 3.0 |
| IntentResolutionEvaluator | ≥ | 3.0 |
| TaskAdherenceEvaluator | ≥ | 3.0 |
| ToolSelectionEvaluator | ≥ | 3.0 |
| ToolInputAccuracyEvaluator | ≥ | 3.0 |
| avg_latency_seconds | ≤ | 15.0 |

Scores range from 1 to 5. Adjust thresholds in `.agentops/bundles/agent_workflow_baseline.yaml`.

## Building Your Dataset

When creating your own dataset for agent workflow evaluation:

1. **Identify representative tasks** — Cover the main use cases your agent handles
2. **Define tool definitions** — List all tools the agent has access to for each row
3. **Specify expected tool calls** — What tools should be called and with what arguments
4. **Write expected responses** — The ideal final response after tool execution
5. **Include edge cases** — Tasks where no tool should be called, or multiple tools are needed

Example with multiple tools:

```json
{
  "id": "multi-tool-1",
  "input": "Book a flight from NYC to London and check the weather there",
  "expected": "I've found flights from NYC to London and the weather in London is 12°C with rain.",
  "tool_definitions": [
    {"name": "search_flights", "description": "Search flights", "parameters": {"type": "object", "properties": {"origin": {"type": "string"}, "destination": {"type": "string"}}, "required": ["origin", "destination"]}},
    {"name": "get_weather", "description": "Get weather", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}
  ],
  "tool_calls": [
    {"name": "search_flights", "arguments": {"origin": "NYC", "destination": "London"}},
    {"name": "get_weather", "arguments": {"city": "London"}}
  ]
}
```

## Comparing with Foundry Agent Evaluation

If your agent is also deployed to Foundry, you can run the **same bundle** against different targets:

| Target | Run Config | Execution |
|---|---|---|
| Local Agent Framework | `local.callable: my_adapter:run_eval` | In-process, fast |
| Foundry Agent | `endpoint.kind: foundry_agent` | Cloud, production-like |

Use `agentops eval compare` to compare results across targets:

```bash
agentops eval compare --runs .agentops/results/local-run,.agentops/results/foundry-run
```

## CI/CD Integration

```yaml
- name: Run agent workflow evaluation
  run: |
    pip install agentops-toolkit
    agentops eval run --config .agentops/run.yaml
```

## Notes

- **Callable vs HTTP**: Use callable for Agent Framework code that runs in-process. Use HTTP backend (`endpoint.kind: http`) if your agent is deployed as a REST service (LangGraph, ACA, etc.).
- **Tool calls in response**: If your agent framework provides tool call metadata, include it in the callable return dict. The `ToolCallAccuracyEvaluator` and `ToolSelectionEvaluator` use this data.
- **Timeout**: The default timeout is 15 seconds per row for agent workflows. Increase `execution.timeout_seconds` if your agent makes slow external calls.
- **Safety evaluation**: Add the `safe_agent_baseline` bundle as a second evaluation pass to check for content safety issues.
